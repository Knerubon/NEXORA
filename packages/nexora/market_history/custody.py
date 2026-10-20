"""Independent custody of trust anchors (V4).

Policy
  * Extraction creates UNTRUSTED datasets only. The extractor may produce an anchor CANDIDATE
    (`make_candidate`); it cannot approve it.
  * An authorized custodian, a different identity from the extractor/candidate creator AND from the
    operator recorded in the dataset provenance, appends an APPROVAL to an append-only external
    evidence store (`EvidenceStore`). Entries are hash-chained, so an entry cannot be silently
    replaced, reordered or removed from the middle of the chain.
  * TRUSTED needs: a verifying chain, an approval bound to this dataset's identity, content,
    manifest, symbol and window, an approver in the custodian registry supplied by the VERIFIER from
    outside the dataset, independence from the extractor and operator, and no later revocation.
    A hash that merely matches is never enough.

Machine-enforced: chain integrity, binding to the exact dataset, registry membership (with
validity window), identity independence by normalized declared identity, timestamp ordering,
fail-closed on any malformed or unverifiable evidence.
Process control (NOT provable by this code): that the approver's declared identity is truthful, that
the registry is maintained by the right people, that the store really is append-only/immutable, and
that the head pin is obtained from an independent channel. There is no signature scheme and no
secret: the trust root is the store's access control plus the registry. See THREAT-MODEL-V4.md.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from nexora.market_history.fsutil import TMP_MARKER, Dir
from nexora.market_history.models import HistoryError, canonical_json, sha256_hex

CANDIDATE_SCHEMA = "nexora.market-history-anchor-candidate/1"
APPROVAL_SCHEMA = "nexora.market-history-approval/1"
REGISTRY_SCHEMA = "nexora.market-history-custodians/1"
GENESIS = "0" * 64
MAX_SKEW = timedelta(minutes=5)
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ENTRY_NAME = re.compile(r"entry-(\d{8})\.json")
_MAX_FILE = 1 << 20
BINDING_FIELDS = ("dataset_id", "content_sha256", "manifest_sha256", "symbol", "window_raw_msc")


# ---- identities ------------------------------------------------------------------------------
def normalize_identity(identity: Any) -> str:
    """Canonical form used for EVERY identity comparison: NFKC, trimmed, case-folded, inner
    whitespace collapsed. Control characters and empty strings are refused. This catches casing,
    spacing and compatibility-character disguises of the same declared identity; it cannot know
    that two different strings denote one human (that is what registry `aliases` are for)."""
    if type(identity) is not str:
        raise HistoryError("invalid_identity", "not a string")
    text = unicodedata.normalize("NFKC", identity)
    if any(unicodedata.category(c).startswith("C") for c in text):
        raise HistoryError("invalid_identity", "control character")
    text = " ".join(text.split()).casefold()
    if not text or len(text) > 200:
        raise HistoryError("invalid_identity", "empty or too long")
    return text


def _aware(value: Any, what: str) -> datetime:
    try:
        when = datetime.fromisoformat(str(value))
    except ValueError:
        raise HistoryError("invalid_timestamp", what) from None
    if when.tzinfo is None:
        raise HistoryError("invalid_timestamp", what)
    return when.astimezone(UTC)


def _hash_of(body: Mapping[str, Any], key: str) -> str:
    return sha256_hex(canonical_json({k: v for k, v in body.items() if k != key}))


def _bindings(manifest: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("dataset_id", "content_sha256", "manifest_sha256", "symbol")
    return {k: manifest[k] for k in keys} | {"window_raw_msc": list(manifest["window_raw_msc"])}


def manifest_operator(manifest: Mapping[str, Any]) -> str | None:
    prov = manifest.get("provenance")
    op = prov.get("operator") if isinstance(prov, Mapping) else None
    return op if type(op) is str and op.strip() else None


# ---- candidate -------------------------------------------------------------------------------
def make_candidate(
    manifest: Mapping[str, Any], *, created_by: str, created_at: datetime
) -> dict[str, Any]:
    """What the extractor hands to a custodian. It grants nothing: `dataset_status` ignores a
    candidate that has no approval in the store."""
    if created_at.tzinfo is None:
        raise HistoryError("invalid_timestamp", "created_at")
    normalize_identity(created_by)
    body: dict[str, Any] = {
        "schema": CANDIDATE_SCHEMA,
        **_bindings(manifest),
        "operator": manifest_operator(manifest),
        "created_by": created_by.strip(),
        "created_at": created_at.isoformat(),
        "nonce": os.urandom(16).hex(),
    }
    body["candidate_sha256"] = _hash_of(body, "candidate_sha256")
    return body


def candidate_problem(c: Any) -> str:
    """'' when well formed with a correct self hash, else the first offending field."""
    if not isinstance(c, dict) or c.get("schema") != CANDIDATE_SCHEMA:
        return "schema"
    for key in ("dataset_id", "content_sha256", "manifest_sha256", "candidate_sha256"):
        if type(c.get(key)) is not str or not _HEX64.fullmatch(c[key]):
            return key
    if type(c.get("symbol")) is not str or not c["symbol"].strip():
        return "symbol"
    w = c.get("window_raw_msc")
    if not (isinstance(w, list) and len(w) == 2 and all(type(x) is int for x in w)):
        return "window_raw_msc"
    if type(c.get("nonce")) is not str or len(c["nonce"]) < 16:
        return "nonce"
    try:
        normalize_identity(c.get("created_by"))
        _aware(c.get("created_at"), "created_at")
    except HistoryError:
        return "created_by_or_created_at"
    if c["candidate_sha256"] != _hash_of(c, "candidate_sha256"):
        return "candidate_sha256"
    return ""


# ---- custodian registry (supplied by the VERIFIER, outside the dataset) -----------------------
@dataclass(frozen=True, slots=True)
class Custodian:
    id: str
    identities: frozenset[str]  # normalized id + aliases
    valid_from: datetime | None
    valid_until: datetime | None


@dataclass(frozen=True, slots=True)
class CustodianRegistry:
    """Allowlist of identities that may approve. Format (JSON, no secrets):

        {"schema": "nexora.market-history-custodians/1",
         "custodians": [{"id": "rin", "aliases": ["rin@example.org"],
                         "valid_from": "2026-01-01T00:00:00+00:00",   # optional
                         "valid_until": null}]}                      # optional

    Every id/alias must be unique across the whole registry (an ambiguous registry is refused)."""

    custodians: tuple[Custodian, ...] = field(default_factory=tuple)

    @classmethod
    def from_json(cls, data: Any) -> CustodianRegistry:
        if not isinstance(data, dict) or data.get("schema") != REGISTRY_SCHEMA:
            raise HistoryError("registry_malformed", "schema")
        raw = data.get("custodians")
        if not isinstance(raw, list) or not raw:
            raise HistoryError("registry_malformed", "custodians")
        seen: set[str] = set()
        out: list[Custodian] = []
        for entry in raw:
            if not isinstance(entry, dict) or set(entry) - {
                "id", "aliases", "valid_from", "valid_until"
            }:  # fmt: skip
                raise HistoryError("registry_malformed", "entry")
            aliases = entry.get("aliases", [])
            if not isinstance(aliases, list):
                raise HistoryError("registry_malformed", "aliases")
            try:
                ids = frozenset(normalize_identity(x) for x in [entry.get("id"), *aliases])
                vf, vu = entry.get("valid_from"), entry.get("valid_until")
                lo = _aware(vf, "valid_from") if vf else None
                hi = _aware(vu, "valid_until") if vu else None
            except HistoryError:
                raise HistoryError("registry_malformed", "identity_or_timestamp") from None
            if ids & seen:
                raise HistoryError("registry_malformed", "duplicate_identity")
            seen |= ids
            out.append(Custodian(normalize_identity(entry["id"]), ids, lo, hi))
        return cls(tuple(out))

    def find(self, identity: str) -> Custodian | None:
        try:
            wanted = normalize_identity(identity)
        except HistoryError:
            return None
        return next((c for c in self.custodians if wanted in c.identities), None)


def load_registry(path: Path, *, dataset_dir: Path | None = None) -> CustodianRegistry:
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
    return CustodianRegistry.from_json(data)


def _inside(child: Path, parent: Path) -> bool:
    child, parent = Path(os.path.realpath(child)), Path(os.path.realpath(parent))
    return child == parent or parent in child.parents


# ---- evidence store --------------------------------------------------------------------------
@runtime_checkable
class EvidenceStore(Protocol):
    """Provider-neutral, append-only evidence log. An implementation may be a WORM bucket, a
    ledger, a git-notes ref on another host, a ticketing system export: anything that can

      * `append(body)`: atomically add one entry and return it with the store-assigned `seq`,
        `prev_sha256` and `entry_sha256` (see `seal_entry`); MUST refuse to overwrite;
      * `entries()`: return every entry in order.

    The verifier never trusts the store's own claims: `verify_chain` recomputes everything."""

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
    seq: int
    entry_sha256: str


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
    lock, ledger) plus a pinned head: see `ChainHead`."""

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


# ---- approval --------------------------------------------------------------------------------
def _independence_problem(
    approver: Custodian | None, approver_id: str, others: Mapping[str, str | None]
) -> str:
    """Self-approval check over normalized declared identities (approver id and every alias)."""
    mine = approver.identities if approver else {normalize_identity(approver_id)}
    for role, ident in others.items():
        if ident is None:
            return f"{role}_not_recorded"
        if normalize_identity(ident) in mine:
            return f"self_approval_by_{role}"
    return ""


def check_approval_entry(
    entry: Mapping[str, Any],
    manifest: Mapping[str, Any],
    registry: CustodianRegistry,
    *,
    now: datetime,
) -> str:
    """'' when `entry` is a valid APPROVAL of exactly this dataset, else a short reason code."""
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
    try:
        approver_id = entry.get("approver")
        normalize_identity(approver_id)
        approved_at = _aware(entry.get("approved_at"), "approved_at")
        created_at = _aware(cand["created_at"], "created_at")
    except HistoryError as exc:
        return f"invalid:{exc.code}"
    custodian = registry.find(str(approver_id))
    if custodian is None:
        return "approver_not_authorized"
    if (custodian.valid_from and approved_at < custodian.valid_from) or (
        custodian.valid_until and approved_at > custodian.valid_until
    ):
        return "approver_not_valid_at_approval_time"
    independence = _independence_problem(
        custodian, str(approver_id),
        {"operator": manifest_operator(manifest), "candidate_creator": cand["created_by"]},
    )  # fmt: skip
    if independence:
        return independence
    if cand.get("operator") != manifest_operator(manifest):
        return "candidate_operator_mismatch"
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
    registry: CustodianRegistry,
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
    approver: str,
    evidence_ref: str,
    approved_at: datetime,
) -> dict[str, Any]:
    return {
        "schema": APPROVAL_SCHEMA,
        "decision": "approve",
        **_bindings(manifest),
        "candidate": dict(candidate),
        "candidate_sha256": candidate.get("candidate_sha256"),
        "approver": approver.strip() if type(approver) is str else approver,
        "approved_at": approved_at.isoformat(),
        "evidence_ref": evidence_ref.strip() if type(evidence_ref) is str else evidence_ref,
    }


def approve_dataset(
    directory: Path,
    candidate: Mapping[str, Any],
    *,
    store: EvidenceStore,
    registry: CustodianRegistry,
    approver: str,
    evidence_ref: str,
    approved_at: datetime,
) -> dict[str, Any]:
    """Custodian action. Independently re-verifies the dataset, then runs the SAME checks the
    verifier will run before appending, so an invalid or self-approval is refused at the door."""
    from nexora.market_history.extract import _read_manifest, usability_blockers

    if approved_at.tzinfo is None:
        raise HistoryError("invalid_timestamp", "approved_at")
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
        manifest, candidate, approver=approver, evidence_ref=evidence_ref, approved_at=approved_at
    )
    why = check_approval_entry(body, manifest, registry, now=datetime.now(UTC))
    if why:
        raise HistoryError("approval_refused", why)
    return store.append(body)


def revoke_dataset(
    directory: Path,
    *,
    store: EvidenceStore,
    registry: CustodianRegistry,
    revoker: str,
    evidence_ref: str,
    revoked_at: datetime,
) -> dict[str, Any]:
    """Custodian action: withdraw trust for this exact dataset (authorized custodians only)."""
    from nexora.market_history.extract import _read_manifest

    with Dir.open(directory) as d:
        manifest = _read_manifest(d)
    if isinstance(manifest, list):
        raise HistoryError("dataset_not_revocable", str(manifest[0]))
    if registry.find(revoker) is None:
        raise HistoryError("approval_refused", "approver_not_authorized")
    if type(evidence_ref) is not str or not evidence_ref.strip():
        raise HistoryError("approval_refused", "evidence_ref_missing")
    return store.append(
        {
            "schema": APPROVAL_SCHEMA,
            "decision": "revoke",
            **_bindings(manifest),
            "candidate": None,
            "candidate_sha256": None,
            "approver": revoker.strip(),
            "approved_at": revoked_at.isoformat(),
            "evidence_ref": evidence_ref.strip(),
        }
    )
