"""Independent custody of trust anchors (V4, round 3: authenticated identities).

Policy
  * Extraction creates UNTRUSTED datasets only. The extraction OPERATOR signs an attestation over
    the dataset (`make_candidate`); that candidate grants nothing on its own.
  * An authorized CUSTODIAN signs an APPROVAL of exactly that candidate and appends it to an
    append-only, hash-chained evidence store (`EvidenceStore`).
  * Identities are AUTHENTICATED, not declared: every statement that can grant trust carries an
    Ed25519 signature by a key listed in the principal registry that the VERIFIER supplies from
    outside the dataset (`PrincipalRegistry`). Independence is decided by comparing the principals
    that own the verified keys, never display names. A raw store append by someone without a
    registered custodian key cannot produce an accepted approval.
  * TRUSTED needs: a verifying chain, a valid operator attestation by an `operator` principal whose
    id is the operator recorded in the manifest, a valid approval signed by a different principal
    holding the `custodian` role, both keys valid at signing time AND at verification time and not
    revoked, bindings to this dataset's identity, content, manifest, symbol and window, and no
    later revocation.

Storage integrity (hash chain) and approval authenticity (signatures) are separate properties and
are checked separately. The trust roots are (a) the registry file the verifier obtains outside the
dataset and (b) the holders' custody of their private keys; both are process dependencies that this
code cannot prove (see THREAT-MODEL-V4.md).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from nexora.market_history import signing
from nexora.market_history.fsutil import TMP_MARKER, Dir
from nexora.market_history.models import HistoryError, canonical_json, sha256_hex
from nexora.market_history.signing import Signer

CANDIDATE_SCHEMA = "nexora.market-history-anchor-candidate/2"
APPROVAL_SCHEMA = "nexora.market-history-approval/2"
REGISTRY_SCHEMA = "nexora.market-history-principals/2"
ROLES = frozenset({"operator", "custodian", "pin_authority"})
GENESIS = "0" * 64
MAX_SKEW = timedelta(minutes=5)
_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_ENTRY_NAME = re.compile(r"entry-(\d{8})\.json")
# Principal ids are plain lower-case ASCII. Anything else (upper case, whitespace, Unicode
# confusables, compatibility forms, control characters) is REFUSED, never normalised: two encodings
# that a human might read as one name must not silently compare equal or unequal.
_PRINCIPAL_ID = re.compile(r"[a-z0-9][a-z0-9._@-]{0,127}")
_MAX_FILE = 1 << 20
_SEAL_KEYS = ("seq", "prev_sha256", "entry_sha256")
BINDING_FIELDS = ("dataset_id", "content_sha256", "manifest_sha256", "symbol", "window_raw_msc")


# ---- identities ------------------------------------------------------------------------------
def normalize_identity(identity: Any) -> str:
    """Return `identity` if it is a canonical principal id, else raise `invalid_identity`.

    Round 3 (X5): ambiguous encodings are rejected rather than normalised. The only accepted form
    is `[a-z0-9][a-z0-9._@-]{0,127}` (ASCII). Identity is proved by keys; this check only stops a
    display string from passing for a principal id."""
    if type(identity) is not str or not _PRINCIPAL_ID.fullmatch(identity):
        raise HistoryError("invalid_identity", "not a canonical principal id")
    return identity


def _aware(value: Any, what: str) -> datetime:
    try:
        when = datetime.fromisoformat(str(value))
    except ValueError:
        raise HistoryError("invalid_timestamp", what) from None
    if when.tzinfo is None:
        raise HistoryError("invalid_timestamp", what)
    return when.astimezone(UTC)


def _hash_of(body: Mapping[str, Any], *drop: str) -> str:
    return sha256_hex(canonical_json({k: v for k, v in body.items() if k not in drop}))


def _without(body: Mapping[str, Any], *drop: str) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in drop}


def _bindings(manifest: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("dataset_id", "content_sha256", "manifest_sha256", "symbol")
    return {k: manifest[k] for k in keys} | {"window_raw_msc": list(manifest["window_raw_msc"])}


def manifest_operator(manifest: Mapping[str, Any]) -> str | None:
    prov = manifest.get("provenance")
    op = prov.get("operator") if isinstance(prov, Mapping) else None
    return op if type(op) is str and op.strip() else None


def _nonce() -> str:
    return os.urandom(16).hex()


# ---- principal registry (supplied by the VERIFIER, outside the dataset) -----------------------
@dataclass(frozen=True, slots=True)
class PrincipalKey:
    key_id: str
    public_hex: str
    valid_from: datetime | None
    valid_until: datetime | None
    revoked: bool


@dataclass(frozen=True, slots=True)
class Principal:
    id: str
    roles: frozenset[str]
    keys: tuple[PrincipalKey, ...]


@dataclass(frozen=True, slots=True)
class PrincipalRegistry:
    """Who may sign what. Format (JSON, public keys only, no secrets):

        {"schema": "nexora.market-history-principals/2",
         "principals": [
           {"id": "carol", "roles": ["custodian"],
            "keys": [{"ed25519": "<64 hex>",                       # raw public key
                      "valid_from": "2026-01-01T00:00:00+00:00",   # optional
                      "valid_until": null,                         # optional
                      "revoked": false}]}]}                        # optional

    Every principal id and every public key must be unique across the whole registry; an
    ambiguous registry is refused. A key flagged `revoked` invalidates every statement it ever
    signed (there is no trusted timestamp that could tell pre-compromise signatures apart)."""

    principals: tuple[Principal, ...] = field(default_factory=tuple)

    @classmethod
    def from_json(cls, data: Any) -> PrincipalRegistry:
        if not isinstance(data, dict) or data.get("schema") != REGISTRY_SCHEMA:
            raise HistoryError("registry_malformed", "schema")
        if set(data) - {"schema", "principals"}:
            raise HistoryError("registry_malformed", "unknown_field")
        raw = data.get("principals")
        if not isinstance(raw, list) or not raw:
            raise HistoryError("registry_malformed", "principals")
        ids: set[str] = set()
        key_ids: set[str] = set()
        out: list[Principal] = []
        for entry in raw:
            if not isinstance(entry, dict) or set(entry) - {"id", "roles", "keys"}:
                raise HistoryError("registry_malformed", "entry")
            try:
                pid = normalize_identity(entry.get("id"))
            except HistoryError:
                raise HistoryError("registry_malformed", "principal_id") from None
            roles = entry.get("roles")
            if (
                not isinstance(roles, list)
                or not roles
                or not all(type(r) is str and r in ROLES for r in roles)
                or len(set(roles)) != len(roles)
            ):
                raise HistoryError("registry_malformed", "roles")
            keys = entry.get("keys")
            if not isinstance(keys, list) or not keys:
                raise HistoryError("registry_malformed", "keys")
            parsed = tuple(_parse_key(k) for k in keys)
            if pid in ids or any(k.key_id in key_ids for k in parsed):
                raise HistoryError("registry_malformed", "duplicate_principal_or_key")
            if len({k.key_id for k in parsed}) != len(parsed):
                raise HistoryError("registry_malformed", "duplicate_principal_or_key")
            ids.add(pid)
            key_ids |= {k.key_id for k in parsed}
            out.append(Principal(pid, frozenset(roles), parsed))
        return cls(tuple(out))

    def find(self, identity: str) -> Principal | None:
        return next((p for p in self.principals if p.id == identity), None)

    def key(self, key_id: Any) -> tuple[Principal, PrincipalKey] | None:
        for p in self.principals:
            for k in p.keys:
                if k.key_id == key_id:
                    return p, k
        return None

    def authorize(
        self, key_id: Any, role: str, claimed_principal: Any, at: Sequence[datetime]
    ) -> tuple[Principal | None, PrincipalKey | None, str]:
        """(principal, key, '') when `key_id` is registered to `claimed_principal`, that principal
        holds `role`, and the key is unrevoked and inside its validity window at every instant of
        `at` (signing time AND verification time); else (None, None, reason)."""
        found = self.key(key_id)
        if found is None:
            return None, None, "key_not_registered"
        principal, key = found
        if principal.id != claimed_principal:
            return None, None, "key_belongs_to_another_principal"
        if role not in principal.roles:
            return None, None, f"principal_lacks_role:{role}"
        if key.revoked:
            return None, None, "key_revoked"
        for t in at:
            if (key.valid_from and t < key.valid_from) or (key.valid_until and t > key.valid_until):
                return None, None, "key_not_valid_at_time"
        return principal, key, ""


# Round-2 name kept for callers; the registry now holds principals with roles and keys.
CustodianRegistry = PrincipalRegistry


def _parse_key(k: Any) -> PrincipalKey:
    if not isinstance(k, dict) or set(k) - {"ed25519", "valid_from", "valid_until", "revoked"}:
        raise HistoryError("registry_malformed", "key")
    pub = k.get("ed25519")
    if not signing.is_public_key_hex(pub):
        raise HistoryError("registry_malformed", "public_key")
    revoked = k.get("revoked", False)
    if type(revoked) is not bool:
        raise HistoryError("registry_malformed", "revoked")
    try:
        vf, vu = k.get("valid_from"), k.get("valid_until")
        lo = _aware(vf, "valid_from") if vf is not None else None
        hi = _aware(vu, "valid_until") if vu is not None else None
    except HistoryError:
        raise HistoryError("registry_malformed", "timestamp") from None
    assert isinstance(pub, str)
    return PrincipalKey(signing.key_id_of(pub), pub, lo, hi, revoked)


def load_registry(path: Path, *, dataset_dir: Path | None = None) -> PrincipalRegistry:
    """Read a registry file (final component not a symlink, bounded size). It must live outside
    the dataset folder: a registry shipped with the data would let the data vouch for itself."""
    if dataset_dir is not None and _inside(path, dataset_dir):
        raise HistoryError("registry_inside_dataset")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as fh:
            data = json.loads(fh.read(_MAX_FILE))
    except (OSError, ValueError, UnicodeDecodeError):
        raise HistoryError("registry_unreadable") from None
    return PrincipalRegistry.from_json(data)


def _inside(child: Path, parent: Path) -> bool:
    child, parent = Path(os.path.realpath(child)), Path(os.path.realpath(parent))
    return child == parent or parent in child.parents


# ---- candidate = operator attestation ---------------------------------------------------------
def make_candidate(
    manifest: Mapping[str, Any], *, signer: Signer, created_at: datetime
) -> dict[str, Any]:
    """The extraction operator's signed attestation of this dataset, handed to a custodian. It
    grants nothing on its own. The operator id is the one recorded in the manifest; the verifier
    checks that `signer`'s key is registered to exactly that principal with the `operator` role."""
    if created_at.tzinfo is None:
        raise HistoryError("invalid_timestamp", "created_at")
    operator = manifest_operator(manifest)
    if operator is None:
        raise HistoryError("operator_not_recorded")
    normalize_identity(operator)
    body: dict[str, Any] = {
        "schema": CANDIDATE_SCHEMA,
        **_bindings(manifest),
        "operator": operator,
        "operator_key_id": signer.key_id,
        "created_at": created_at.isoformat(),
        "nonce": _nonce(),
    }
    body["candidate_sha256"] = _hash_of(body)
    body["operator_signature"] = signer.sign("operator-attestation", body)
    return body


def candidate_problem(c: Any) -> str:
    """'' when structurally well formed with a correct self hash, else the first offending field.
    (Authenticity is `operator_problem`.)"""
    if not isinstance(c, dict) or c.get("schema") != CANDIDATE_SCHEMA:
        return "schema"
    for key in ("dataset_id", "content_sha256", "manifest_sha256", "candidate_sha256"):
        if type(c.get(key)) is not str or not _HEX64.fullmatch(c[key]):
            return key
    if type(c.get("operator_key_id")) is not str or not _HEX64.fullmatch(c["operator_key_id"]):
        return "operator_key_id"
    if type(c.get("symbol")) is not str or not c["symbol"].strip():
        return "symbol"
    w = c.get("window_raw_msc")
    if not (isinstance(w, list) and len(w) == 2 and all(type(x) is int for x in w)):
        return "window_raw_msc"
    if type(c.get("nonce")) is not str or not _HEX32.fullmatch(c["nonce"]):
        return "nonce"
    if not signing.is_signature_hex(c.get("operator_signature")):
        return "operator_signature"
    try:
        normalize_identity(c.get("operator"))
        _aware(c.get("created_at"), "created_at")
    except HistoryError:
        return "operator_or_created_at"
    if c["candidate_sha256"] != _hash_of(c, "candidate_sha256", "operator_signature"):
        return "candidate_sha256"
    return ""


def operator_problem(
    c: Mapping[str, Any], manifest: Mapping[str, Any], registry: PrincipalRegistry, *, now: datetime
) -> str:
    """'' when `c` is an authentic attestation by the operator recorded in `manifest`."""
    recorded = manifest_operator(manifest)
    if recorded is None:
        return "operator_not_recorded"
    if c.get("operator") != recorded:
        return "candidate_operator_mismatch"
    created_at = _aware(c["created_at"], "created_at")
    _p, key, why = registry.authorize(
        c.get("operator_key_id"), "operator", recorded, (created_at, now)
    )
    if key is None:
        return f"operator_{why}"
    body = _without(c, "operator_signature")
    if not signing.verify(key.public_hex, "operator-attestation", body, c["operator_signature"]):
        return "operator_signature_invalid"
    return ""


# ---- evidence store --------------------------------------------------------------------------
@runtime_checkable
class EvidenceStore(Protocol):
    """Provider-neutral, append-only evidence log. An implementation may be a WORM bucket, a
    ledger, a git-notes ref on another host, a ticketing system export: anything that can

      * `append(body)`: atomically add one entry and return it with the store-assigned `seq`,
        `prev_sha256` and `entry_sha256` (see `seal_entry`); MUST refuse to overwrite;
      * `entries()`: return every entry in order.

    The verifier never trusts the store's own claims: `verify_chain` recomputes everything, and
    authorship comes from signatures inside the entries, never from the store."""

    def append(self, body: Mapping[str, Any]) -> dict[str, Any]: ...

    def entries(self) -> list[dict[str, Any]]: ...


def seal_entry(body: Mapping[str, Any], seq: int, prev_sha256: str) -> dict[str, Any]:
    entry = dict(body)
    entry["seq"] = seq
    entry["prev_sha256"] = prev_sha256
    entry["entry_sha256"] = _hash_of(entry, "entry_sha256")
    return entry


@dataclass(frozen=True, slots=True)
class ChainHead:
    """An UNSIGNED head reference. It can detect accidental truncation when it comes from an
    independent channel, but it is never sufficient for TRUSTED on its own."""

    seq: int
    entry_sha256: str

    def __post_init__(self) -> None:
        if type(self.seq) is not int or self.seq < 1:
            raise HistoryError("invalid_store_head", "seq must be >= 1")
        if type(self.entry_sha256) is not str or not _HEX64.fullmatch(self.entry_sha256):
            raise HistoryError("invalid_store_head", "entry_sha256")


def verify_chain(entries: Any, expected_head: ChainHead | None = None) -> str:
    """'' when the chain verifies, else a short code. Fail closed on anything unexpected."""
    if not isinstance(entries, list):
        return "not_a_list"
    prev = GENESIS
    for i, e in enumerate(entries, 1):
        if not isinstance(e, dict) or e.get("schema") != APPROVAL_SCHEMA:
            return f"entry_{i}_schema"
        if e.get("seq") != i or type(e.get("seq")) is not int:
            return f"entry_{i}_seq"
        if e.get("prev_sha256") != prev:
            return f"entry_{i}_prev"
        if type(e.get("entry_sha256")) is not str or e["entry_sha256"] != _hash_of(
            e, "entry_sha256"
        ):
            return f"entry_{i}_hash"
        prev = e["entry_sha256"]
    if expected_head is not None:
        if len(entries) < expected_head.seq:
            return "truncated_before_pinned_head"
        if entries[expected_head.seq - 1]["entry_sha256"] != expected_head.entry_sha256:
            return "pinned_head_mismatch"
    return ""


def head_of(entries: Sequence[Mapping[str, Any]]) -> ChainHead | None:
    return ChainHead(len(entries), str(entries[-1]["entry_sha256"])) if entries else None


def _single_link(d: Dir, name: str) -> bool:
    """True when `name` has exactly one link. An appender that is between its link() and unlink()
    of the temp name makes a second link visible for a moment: wait that out, but only while a
    sibling temp of that name exists; a persistent extra link is a second name for the evidence."""
    import time

    for _ in range(100):
        if d.stat(name).st_nlink == 1:
            return True
        if not any(n.startswith(f"{name}{TMP_MARKER}") for n in d.listdir()):
            return False
        time.sleep(0.002)
    return False


class LocalAppendOnlyStore:
    """Reference implementation for tests and single-host use: one read-only file per entry,
    `entry-<seq>.json`, created with temp+fsync+no-overwrite link, so a concurrent appender or a
    replacement attempt at an existing sequence number fails instead of overwriting. The directory
    is pinned by descriptor; symlinked ancestors are refused.

    It is NOT tamper-proof against a user who can write to the directory (they can delete the tail
    or replace the whole chain). Production custody needs a store with real immutability (object
    lock, ledger) plus an independently signed head pin."""

    def __init__(self, directory: Path) -> None:
        self.location = directory

    def entries(self) -> list[dict[str, Any]]:
        try:
            d = Dir.open(self.location)
        except (HistoryError, OSError):
            raise HistoryError("store_unreadable", "cannot open store directory") from None
        with d:
            # in-flight temp files of a concurrent appender are not entries (and a crashed
            # appender's leftover is harmless: it never carried an entry name)
            names = sorted(n for n in d.listdir() if TMP_MARKER not in n)
            out: list[dict[str, Any]] = []
            for i, name in enumerate(names, 1):
                m = _ENTRY_NAME.fullmatch(name)
                if not m or int(m.group(1)) != i:
                    raise HistoryError("store_unexpected_entry", name[:60])
                try:
                    if not _single_link(d, name):
                        raise HistoryError("store_unexpected_entry", "multiple links")
                    with d.open_regular(name) as fh:
                        data = json.loads(fh.read(_MAX_FILE))
                except HistoryError as exc:
                    if exc.code == "store_unexpected_entry":
                        raise
                    raise HistoryError("store_unreadable", name[:60]) from None
                except (OSError, ValueError, UnicodeDecodeError):
                    raise HistoryError("store_unreadable", name[:60]) from None
                out.append(data)
            return out

    def append(self, body: Mapping[str, Any]) -> dict[str, Any]:
        from nexora.market_history.extract import write_once_in

        with Dir.open(self.location, create=True) as d:
            for _ in range(5):
                entries = self.entries()
                problem = verify_chain(entries)
                if problem:
                    raise HistoryError("store_chain_invalid", problem)
                head = head_of(entries)
                entry = seal_entry(
                    body, (head.seq if head else 0) + 1, head.entry_sha256 if head else GENESIS
                )
                try:
                    write_once_in(
                        d, f"entry-{entry['seq']:08d}.json",
                        json.dumps(entry, sort_keys=True, indent=2).encode() + b"\n",
                    )  # fmt: skip
                except FileExistsError:
                    continue  # another appender took this sequence number: re-read and retry
                return entry
        raise HistoryError("store_conflict", "could not append after retries")


# ---- approval and revocation -----------------------------------------------------------------
def _signed_body(entry: Mapping[str, Any]) -> dict[str, Any]:
    """What a custodian signed: the entry without its signature and without the store's seal."""
    return _without(entry, "signature", *_SEAL_KEYS)


def check_approval_entry(
    entry: Mapping[str, Any],
    manifest: Mapping[str, Any],
    registry: PrincipalRegistry,
    *,
    now: datetime,
) -> str:
    """'' when `entry` is an AUTHENTIC approval of exactly this dataset, else a reason code.
    Lineage (replay, revocation) is decided by `evaluate_custody` over the whole chain."""
    if entry.get("decision") != "approve":
        return "not_an_approval"
    for key, value in _bindings(manifest).items():
        if entry.get(key) != value:
            return f"binding_mismatch:{key}"
    cand = entry.get("candidate")
    bad = candidate_problem(cand)
    if bad:
        return f"candidate_malformed:{bad}"
    assert isinstance(cand, dict)
    for key, value in _bindings(manifest).items():
        if cand.get(key) != value:
            return f"candidate_binding_mismatch:{key}"
    if entry.get("candidate_sha256") != cand["candidate_sha256"]:
        return "candidate_hash_not_bound"
    ref = entry.get("evidence_ref")
    if type(ref) is not str or not ref.strip():
        return "evidence_ref_missing"
    if type(entry.get("approval_id")) is not str or not _HEX32.fullmatch(entry["approval_id"]):
        return "approval_id_malformed"
    try:
        approver_id = normalize_identity(entry.get("approver"))
        approved_at = _aware(entry.get("approved_at"), "approved_at")
        created_at = _aware(cand["created_at"], "created_at")
    except HistoryError as exc:
        return f"invalid:{exc.code}"
    why = operator_problem(cand, manifest, registry, now=now)
    if why:
        return why
    approver, akey, why = registry.authorize(
        entry.get("approver_key_id"), "custodian", approver_id, (approved_at, now)
    )
    if approver is None or akey is None:
        return f"approver_{why}"
    if not signing.verify(
        akey.public_hex, "custody-approval", _signed_body(entry), entry.get("signature")
    ):
        return "approval_signature_invalid"
    # independence over AUTHENTICATED principals (the operator principal is the one whose key
    # verified the attestation above; the approver is the one whose key verified this entry)
    if approver.id == cand["operator"]:
        return "self_approval_by_operator"
    if approved_at < created_at:
        return "approved_before_candidate_created"
    try:
        retrieved = _aware(manifest["provenance"]["retrieved_at"], "retrieved_at")
    except (HistoryError, KeyError, TypeError):
        return "dataset_retrieval_time_unreadable"
    if approved_at < retrieved:
        return "approved_before_dataset_retrieved"
    if approved_at > now + MAX_SKEW:
        return "approved_in_the_future"
    return ""


@dataclass(frozen=True, slots=True)
class CustodyVerdict:
    approved: bool
    reasons: tuple[str, ...]
    approval_seq: int | None = None
    head: ChainHead | None = None


def evaluate_custody(
    manifest: Mapping[str, Any],
    entries: Any,
    registry: PrincipalRegistry,
    *,
    expected_head: ChainHead | None = None,
    now: datetime | None = None,
) -> CustodyVerdict:
    """Decide whether the store holds a valid, un-revoked approval for exactly this dataset."""
    now = now or datetime.now(UTC)
    problem = verify_chain(entries, expected_head)
    if problem:
        return CustodyVerdict(False, (f"custody:chain_invalid:{problem}",))
    head = head_of(entries)
    same_id = [e for e in entries if e.get("dataset_id") == manifest.get("dataset_id")]
    bound = _bindings(manifest)
    exact = [e for e in same_id if all(e.get(k) == v for k, v in bound.items())]
    reasons: list[str] = []
    for e in same_id:
        if e not in exact:
            mism = [k for k, v in bound.items() if e.get(k) != v]
            reasons.append(f"custody:binding_mismatch:{','.join(mism)}")
    valid: list[Mapping[str, Any]] = []
    for e in exact:
        if e.get("decision") != "approve":
            continue
        why = check_approval_entry(e, manifest, registry, now=now)
        if why:
            reasons.append(f"custody:approval_{e['seq']}_rejected:{why}")
        else:
            valid.append(e)
    if not valid:
        return CustodyVerdict(False, (*reasons, "custody:no_valid_approval"), None, head)
    latest = valid[-1]
    # Any later revocation wins, whoever wrote it: an unauthorized revoker can only deny trust,
    # never grant it, so ignoring it would be the unsafe direction.
    revoked = [e for e in exact if e.get("decision") == "revoke" and e["seq"] > latest["seq"]]
    if revoked:
        return CustodyVerdict(False, (f"custody:revoked_at_seq_{revoked[-1]['seq']}",), None, head)
    return CustodyVerdict(True, (), int(latest["seq"]), head)


def build_approval_body(
    manifest: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    signer: Signer,
    approver: str,
    evidence_ref: str,
    approved_at: datetime,
) -> dict[str, Any]:
    """A signed approval body ready to append. `approver` is the principal id `signer`'s key is
    registered to (the verifier re-derives it from the key)."""
    body: dict[str, Any] = {
        "schema": APPROVAL_SCHEMA,
        "decision": "approve",
        **_bindings(manifest),
        "candidate": dict(candidate),
        "candidate_sha256": candidate.get("candidate_sha256"),
        "approval_id": _nonce(),
        "approver": approver,
        "approver_key_id": signer.key_id,
        "approved_at": approved_at.isoformat(),
        "evidence_ref": evidence_ref.strip() if type(evidence_ref) is str else evidence_ref,
    }
    body["signature"] = signer.sign("custody-approval", body)
    return body


def _principal_for(signer: Signer, registry: PrincipalRegistry, role: str) -> str:
    found = registry.key(signer.key_id)
    if found is None:
        raise HistoryError("approval_refused", "key_not_registered")
    if role not in found[0].roles:
        raise HistoryError("approval_refused", f"principal_lacks_role:{role}")
    return found[0].id


def approve_dataset(
    directory: Path,
    candidate: Mapping[str, Any],
    *,
    store: EvidenceStore,
    registry: PrincipalRegistry,
    signer: Signer,
    evidence_ref: str,
    approved_at: datetime,
) -> dict[str, Any]:
    """Custodian action. Independently re-verifies the dataset, then runs the SAME checks the
    verifier will run before appending, so an invalid or self-approval is refused at the door."""
    from nexora.market_history.extract import _read_manifest, usability_blockers

    if approved_at.tzinfo is None:
        raise HistoryError("invalid_timestamp", "approved_at")
    approver = _principal_for(signer, registry, "custodian")
    blockers = usability_blockers(directory)
    if blockers:
        raise HistoryError("dataset_not_approvable", ",".join(blockers)[:300])
    with Dir.open(directory) as d:
        manifest = _read_manifest(d)
    if isinstance(manifest, list):
        raise HistoryError("dataset_not_approvable", str(manifest[0]))
    location = getattr(store, "location", None)
    if isinstance(location, Path) and _inside(location, directory):
        raise HistoryError("store_inside_dataset")
    body = build_approval_body(
        manifest, candidate, signer=signer, approver=approver, evidence_ref=evidence_ref,
        approved_at=approved_at,
    )  # fmt: skip
    why = check_approval_entry(body, manifest, registry, now=datetime.now(UTC))
    if why:
        raise HistoryError("approval_refused", why)
    return store.append(body)


def revoke_dataset(
    directory: Path,
    *,
    store: EvidenceStore,
    registry: PrincipalRegistry,
    signer: Signer,
    evidence_ref: str,
    revoked_at: datetime,
) -> dict[str, Any]:
    """Custodian action: withdraw trust for this exact dataset (custodian key required here; the
    verifier honours any revocation, since a revocation can only deny trust)."""
    from nexora.market_history.extract import _read_manifest

    with Dir.open(directory) as d:
        manifest = _read_manifest(d)
    if isinstance(manifest, list):
        raise HistoryError("dataset_not_revocable", str(manifest[0]))
    revoker = _principal_for(signer, registry, "custodian")
    if type(evidence_ref) is not str or not evidence_ref.strip():
        raise HistoryError("approval_refused", "evidence_ref_missing")
    body: dict[str, Any] = {
        "schema": APPROVAL_SCHEMA,
        "decision": "revoke",
        **_bindings(manifest),
        "revocation_id": _nonce(),
        "revoker": revoker,
        "revoker_key_id": signer.key_id,
        "revoked_at": revoked_at.isoformat(),
        "evidence_ref": evidence_ref.strip(),
    }
    body["signature"] = signer.sign("custody-revocation", body)
    return store.append(body)
