"""MD-6 content-addressed quarantine and requalification register (reference model).

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING).
Test-only reference model; not runtime enforcement.

Companion document:
``docs/research/edge-validation/md6-data-gap-policy/QUARANTINE-IDENTITY.md``.

What this module specifies:

* ``content_identity``: a canonical, content-only identity of a tick dataset
  (canonicalization version, hash algorithm, ordered content hash, multiset
  fingerprint, record count). Metadata (file names, ``dataset_id``, manifest
  labels, conversion declaration, instrument label) is never content.
* Per-record hashes, kept in the register so a derived subset, superset or
  partial overlap of quarantined content is detected.
* An append-only, hash-chained quarantine register with an independently stored
  anchor (entry count plus head hash). Any missing, corrupted, truncated or
  unanchored register makes every check ``UNVERIFIED`` (fail closed).
* The re-entry check (``check_reentry``) and the requalification procedure
  (``requalify``). Neither ever grants TRUSTED or PASS: at best a dataset may
  proceed to the normal Part A acceptance criteria.

No numeric MD-6 threshold is used or implied anywhere in this module.
Integers only, no wall clock, no randomness, standard library only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

CANONICALIZATION_VERSION: Final = "nexora.md6-tick-content/1"
HASH_ALGORITHM: Final = "sha256"
REGISTER_FORMAT: Final = "nexora.md6-quarantine-register/1"
GENESIS_HASH: Final = "0" * 64

ReentryOutcome = Literal[
    "NOT_KNOWN",
    "QUARANTINED_MATCH",
    "DERIVED_FROM_QUARANTINED",
    "CANONICALIZATION_CONFLICT",
    "UNVERIFIED",
    "REQUALIFIED",
]
Eligibility = Literal["UNVERIFIED", "BLOCKED", "PART_A_REQUIRED"]
Relation = Literal[
    "NONE",
    "EXACT_ORDERED",
    "MULTISET",
    "DECLARED_LINEAGE",
    "SUBSET",
    "SUPERSET",
    "PARTIAL_OVERLAP",
    "REPRESENTATION_DRIFT",
    "VERSION_MISMATCH",
    "REGISTER_INVALID",
    "DECLARED_IDENTITY_MISMATCH",
    "NON_CANONICAL_CONTENT",
]
Role = Literal[
    "INDEPENDENT_DATA_REVIEWER",
    "QUANT_APPROVER",
    "STRATEGY_AUTHOR",
    "SUBMITTER",
    "OTHER",
]
Scope = Literal["DATASET", "WINDOW"]
EntryKind = Literal["QUARANTINE", "REQUALIFICATION"]
EntryStatus = Literal["QUARANTINED", "REQUALIFIED"]

DECIDING_ROLES: Final[frozenset[str]] = frozenset({"INDEPENDENT_DATA_REVIEWER", "QUANT_APPROVER"})

# Canonical lexical grammar of a stored price token. The stored string IS the content:
# "1.10" and "1.1" are different content and are never normalized into each other.
_FINITE_DECIMAL: Final = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")
# Closed set of non-finite spellings that are canonical content (so a dataset rejected for
# a non-finite hard defect can still be registered). Any other spelling is non-canonical.
NONFINITE_TOKENS: Final[frozenset[str]] = frozenset({"NaN", "Infinity", "-Infinity"})
_FLAGS: Final = re.compile(r"0|[1-9][0-9]*")


class CanonicalizationError(ValueError):
    """Content cannot be canonically serialized under CANONICALIZATION_VERSION."""


class RegisterInvalid(ValueError):
    """Register missing, unanchored, corrupted, truncated or violating an invariant."""


# --------------------------------------------------------------------------------------
# Content identity
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TickRecord:
    """One stored tick. Content fields only: raw_msc, bid, ask, flags.

    ``bid``/``ask`` are the exact stored decimal strings; ``flags`` is the exact stored
    unsigned decimal string, or None when the source supplies no flags.
    """

    raw_msc: int
    bid: str
    ask: str
    flags: str | None


@dataclass(frozen=True)
class ContentIdentity:
    """``content_identity``: what Track B and the register cite for a dataset's content."""

    canonicalization_version: str
    hash_algorithm: str
    ordered_content_hash: str
    multiset_fingerprint: str
    record_count: int


@dataclass(frozen=True)
class ContentProfile:
    identity: ContentIdentity
    record_hashes: tuple[str, ...]  # sorted multiset of per-record canonical hashes
    shadow_record_hashes: tuple[str, ...]  # sorted; representation-drift detector only


def _sha256(*parts: bytes) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part)
    return h.hexdigest()


def _price_token(token: object, name: str) -> str:
    if not isinstance(token, str):
        raise CanonicalizationError(f"{name} is not a stored decimal string")
    if token in NONFINITE_TOKENS or _FINITE_DECIMAL.fullmatch(token):
        return token
    raise CanonicalizationError(f"{name} token {token!r} is not canonical")


def _flags_token(token: object) -> str | None:
    if token is None:
        return None
    if isinstance(token, str) and _FLAGS.fullmatch(token):
        return token
    raise CanonicalizationError(f"flags token {token!r} is not canonical")


def _raw_msc_token(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CanonicalizationError("raw_msc is not an integer")
    return str(value)


def canonical_record_bytes(record: TickRecord) -> bytes:
    """Canonical serialization of one record: JSON array, no whitespace, ASCII only."""
    fields: list[str | None] = [
        _raw_msc_token(record.raw_msc),
        _price_token(record.bid, "bid"),
        _price_token(record.ask, "ask"),
        _flags_token(record.flags),
    ]
    return json.dumps(fields, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def record_hash(record: TickRecord) -> str:
    version = CANONICALIZATION_VERSION.encode("ascii")
    return _sha256(b"md6-record\x00", version, b"\x00", canonical_record_bytes(record))


def _shadow_price(token: str) -> str:
    """Value key that ignores representation (trailing zeros, sign of zero).

    Used ONLY to detect that a candidate re-expresses quarantined values in another
    representation. It never establishes a match and never normalizes content.
    """
    if token in NONFINITE_TOKENS:
        return token
    negative = token.startswith("-")
    body = token[1:] if negative else token
    integer, _, fraction = body.partition(".")
    fraction = fraction.rstrip("0")
    value = integer + ("." + fraction if fraction else "")
    if value == "0":
        return "0"
    return ("-" if negative else "") + value


def _shadow_record_hash(record: TickRecord) -> str:
    """Flags-insensitive value key (N6): a re-extraction that drops, adds or rewrites ``flags``
    while raw time and prices are unchanged is representation drift, never an unrelated record."""
    canonical_record_bytes(record)  # validates the grammar first
    fields = [
        str(record.raw_msc),
        _shadow_price(record.bid),
        _shadow_price(record.ask),
    ]
    payload = json.dumps(fields, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    version = CANONICALIZATION_VERSION.encode("ascii")
    return _sha256(b"md6-record-shadow\x00", version, b"\x00", payload)


def content_profile(records: Sequence[TickRecord]) -> ContentProfile:
    """Compute the content identity and per-record hashes. Raises CanonicalizationError."""
    hashes = [record_hash(r) for r in records]
    version = CANONICALIZATION_VERSION.encode("ascii")
    ordered = hashlib.sha256(b"md6-ordered\x00" + version + b"\x00")
    for rh in hashes:  # stored order is significant (D10)
        ordered.update(rh.encode("ascii") + b"\n")
    sorted_hashes = tuple(sorted(hashes))
    multiset = _sha256(
        b"md6-multiset\x00", version, b"\x00", "\n".join(sorted_hashes).encode("ascii")
    )
    identity = ContentIdentity(
        canonicalization_version=CANONICALIZATION_VERSION,
        hash_algorithm=HASH_ALGORITHM,
        ordered_content_hash=ordered.hexdigest(),
        multiset_fingerprint=multiset,
        record_count=len(hashes),
    )
    shadows = tuple(sorted(_shadow_record_hash(r) for r in records))
    return ContentProfile(identity, sorted_hashes, shadows)


def content_identity(records: Sequence[TickRecord]) -> ContentIdentity:
    return content_profile(records).identity


# --------------------------------------------------------------------------------------
# Datasets, lineage, authority
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LineageRef:
    """A declared parent: its dataset_id and its ordered content hash (both cited)."""

    dataset_id: str
    ordered_content_hash: str


@dataclass(frozen=True)
class CandidateDataset:
    dataset_id: str
    records: tuple[TickRecord, ...]
    metadata: tuple[tuple[str, str], ...] = ()  # filename, manifest label, conversion, ...
    declared_parents: tuple[LineageRef, ...] = ()
    declared_identity: ContentIdentity | None = None


@dataclass(frozen=True)
class Authority:
    person_id: str
    role: Role


# --------------------------------------------------------------------------------------
# Register entries
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class QuarantineDecision:
    scope: Scope
    content_identity: ContentIdentity
    record_hashes: tuple[str, ...]
    shadow_record_hashes: tuple[str, ...]
    dataset_id: str
    previous_aliases: tuple[str, ...]
    lineage_parents: tuple[LineageRef, ...]
    window_id: str | None
    instrument_id: str
    defect_class: str
    criteria_id: str
    evidence_refs: tuple[str, ...]
    submitter_id: str
    strategy_author_ids: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RequalificationDecision:
    target_entry_hash: str
    requalified_identity: ContentIdentity
    new_evidence_refs: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class RegisterEntry:
    seq: int
    prev_entry_hash: str
    kind: EntryKind
    status: EntryStatus
    decided_at: int  # synthetic integer; ordering authority is append position (seq)
    post_evaluation: bool
    authority: Authority
    quarantine: QuarantineDecision | None
    requalification: RequalificationDecision | None
    entry_hash: str


@dataclass(frozen=True)
class RegisterAnchor:
    """Stored independently of the register (e.g. in the preregistration commit)."""

    entry_count: int
    head_hash: str


def _identity_obj(i: ContentIdentity) -> dict[str, object]:
    return {
        "canonicalization_version": i.canonicalization_version,
        "hash_algorithm": i.hash_algorithm,
        "ordered_content_hash": i.ordered_content_hash,
        "multiset_fingerprint": i.multiset_fingerprint,
        "record_count": i.record_count,
    }


def _lineage_obj(refs: tuple[LineageRef, ...]) -> list[object]:
    return [
        {"dataset_id": r.dataset_id, "ordered_content_hash": r.ordered_content_hash} for r in refs
    ]


def _body_obj(
    seq: int,
    prev: str,
    kind: EntryKind,
    status: EntryStatus,
    decided_at: int,
    post_evaluation: bool,
    authority: Authority,
    q: QuarantineDecision | None,
    r: RequalificationDecision | None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "register_format": REGISTER_FORMAT,
        "seq": seq,
        "prev_entry_hash": prev,
        "kind": kind,
        "status": status,
        "decided_at": decided_at,
        "post_evaluation": post_evaluation,
        "authority": {"person_id": authority.person_id, "role": authority.role},
        "quarantine": None,
        "requalification": None,
    }
    if q is not None:
        body["quarantine"] = {
            "scope": q.scope,
            "content_identity": _identity_obj(q.content_identity),
            "record_hashes": list(q.record_hashes),
            "shadow_record_hashes": list(q.shadow_record_hashes),
            "dataset_id": q.dataset_id,
            "previous_aliases": list(q.previous_aliases),
            "lineage_parents": _lineage_obj(q.lineage_parents),
            "window_id": q.window_id,
            "instrument_id": q.instrument_id,
            "defect_class": q.defect_class,
            "criteria_id": q.criteria_id,
            "evidence_refs": list(q.evidence_refs),
            "submitter_id": q.submitter_id,
            "strategy_author_ids": list(q.strategy_author_ids),
            "metadata": [[k, v] for k, v in q.metadata],
        }
    if r is not None:
        body["requalification"] = {
            "target_entry_hash": r.target_entry_hash,
            "requalified_identity": _identity_obj(r.requalified_identity),
            "new_evidence_refs": list(r.new_evidence_refs),
            "rationale": r.rationale,
        }
    return body


def _canonical_json(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _entry_hash(body: Mapping[str, object]) -> str:
    return _sha256(b"md6-register-entry\x00", _canonical_json(body))


def _entry_body(e: RegisterEntry) -> dict[str, object]:
    return _body_obj(
        e.seq,
        e.prev_entry_hash,
        e.kind,
        e.status,
        e.decided_at,
        e.post_evaluation,
        e.authority,
        e.quarantine,
        e.requalification,
    )


def _entry_line(e: RegisterEntry) -> bytes:
    obj = _entry_body(e)
    obj["entry_hash"] = e.entry_hash
    return _canonical_json(obj)


def register_bytes(entries: Sequence[RegisterEntry]) -> bytes:
    """Serialize as canonical JSON lines (one entry per line, trailing newline)."""
    return b"".join(_entry_line(e) + b"\n" for e in entries)


def anchor_for(entries: Sequence[RegisterEntry]) -> RegisterAnchor:
    head = entries[-1].entry_hash if entries else GENESIS_HASH
    return RegisterAnchor(entry_count=len(entries), head_hash=head)


# ---- strict parsing (any deviation is corruption) ------------------------------------


def _obj(v: object, what: str) -> Mapping[str, object]:
    if not isinstance(v, dict):
        raise RegisterInvalid(f"{what} is not an object")
    out: dict[str, object] = {}
    for k, val in v.items():
        if not isinstance(k, str):
            raise RegisterInvalid(f"{what} has a non-string key")
        out[k] = val
    return out


def _str(o: Mapping[str, object], k: str) -> str:
    v = o.get(k)
    if not isinstance(v, str) or not v:
        raise RegisterInvalid(f"field {k} missing or not a non-empty string")
    return v


def _opt_str(o: Mapping[str, object], k: str) -> str | None:
    if k not in o:
        raise RegisterInvalid(f"field {k} missing")
    v = o[k]
    if v is None:
        return None
    if not isinstance(v, str) or not v:
        raise RegisterInvalid(f"field {k} not a string or null")
    return v


def _int(o: Mapping[str, object], k: str) -> int:
    v = o.get(k)
    if isinstance(v, bool) or not isinstance(v, int):
        raise RegisterInvalid(f"field {k} missing or not an integer")
    return v


def _bool(o: Mapping[str, object], k: str) -> bool:
    v = o.get(k)
    if not isinstance(v, bool):
        raise RegisterInvalid(f"field {k} missing or not a boolean")
    return v


def _strs(o: Mapping[str, object], k: str) -> tuple[str, ...]:
    v = o.get(k)
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise RegisterInvalid(f"field {k} missing or not a list of strings")
    return tuple(str(x) for x in v)


def _identity_from(v: object) -> ContentIdentity:
    o = _obj(v, "content identity")
    return ContentIdentity(
        canonicalization_version=_str(o, "canonicalization_version"),
        hash_algorithm=_str(o, "hash_algorithm"),
        ordered_content_hash=_str(o, "ordered_content_hash"),
        multiset_fingerprint=_str(o, "multiset_fingerprint"),
        record_count=_int(o, "record_count"),
    )


def _lineage_from(v: object) -> tuple[LineageRef, ...]:
    if not isinstance(v, list):
        raise RegisterInvalid("lineage_parents is not a list")
    out = []
    for item in v:
        o = _obj(item, "lineage ref")
        out.append(LineageRef(_str(o, "dataset_id"), _str(o, "ordered_content_hash")))
    return tuple(out)


def _metadata_from(v: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(v, list):
        raise RegisterInvalid("metadata is not a list")
    out: list[tuple[str, str]] = []
    for item in v:
        if not (isinstance(item, list) and len(item) == 2):
            raise RegisterInvalid("metadata item is not a pair")
        k, val = item[0], item[1]
        if not isinstance(k, str) or not isinstance(val, str):
            raise RegisterInvalid("metadata pair is not strings")
        out.append((k, val))
    return tuple(out)


def _role(v: str) -> Role:
    if v == "INDEPENDENT_DATA_REVIEWER":
        return "INDEPENDENT_DATA_REVIEWER"
    if v == "QUANT_APPROVER":
        return "QUANT_APPROVER"
    if v == "STRATEGY_AUTHOR":
        return "STRATEGY_AUTHOR"
    if v == "SUBMITTER":
        return "SUBMITTER"
    if v == "OTHER":
        return "OTHER"
    raise RegisterInvalid(f"unknown role {v!r}")


def _scope(v: str) -> Scope:
    if v == "DATASET":
        return "DATASET"
    if v == "WINDOW":
        return "WINDOW"
    raise RegisterInvalid(f"unknown scope {v!r}")


def _parse_entry(line: bytes) -> RegisterEntry:
    try:
        raw: object = json.loads(line.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegisterInvalid("register line is not valid JSON") from exc
    o = _obj(raw, "entry")
    if o.get("register_format") != REGISTER_FORMAT:
        raise RegisterInvalid("unknown register format")
    auth_o = _obj(o.get("authority"), "authority")
    authority = Authority(_str(auth_o, "person_id"), _role(_str(auth_o, "role")))
    kind_s = _str(o, "kind")
    status_s = _str(o, "status")
    q: QuarantineDecision | None = None
    r: RequalificationDecision | None = None
    kind: EntryKind
    status: EntryStatus
    if kind_s == "QUARANTINE" and status_s == "QUARANTINED":
        kind, status = "QUARANTINE", "QUARANTINED"
        if o.get("requalification") is not None:
            raise RegisterInvalid("quarantine entry carries a requalification payload")
        qo = _obj(o.get("quarantine"), "quarantine")
        q = QuarantineDecision(
            scope=_scope(_str(qo, "scope")),
            content_identity=_identity_from(qo.get("content_identity")),
            record_hashes=_strs(qo, "record_hashes"),
            shadow_record_hashes=_strs(qo, "shadow_record_hashes"),
            dataset_id=_str(qo, "dataset_id"),
            previous_aliases=_strs(qo, "previous_aliases"),
            lineage_parents=_lineage_from(qo.get("lineage_parents")),
            window_id=_opt_str(qo, "window_id"),
            instrument_id=_str(qo, "instrument_id"),
            defect_class=_str(qo, "defect_class"),
            criteria_id=_str(qo, "criteria_id"),
            evidence_refs=_strs(qo, "evidence_refs"),
            submitter_id=_str(qo, "submitter_id"),
            strategy_author_ids=_strs(qo, "strategy_author_ids"),
            metadata=_metadata_from(qo.get("metadata")),
        )
    elif kind_s == "REQUALIFICATION" and status_s == "REQUALIFIED":
        kind, status = "REQUALIFICATION", "REQUALIFIED"
        if o.get("quarantine") is not None:
            raise RegisterInvalid("requalification entry carries a quarantine payload")
        ro = _obj(o.get("requalification"), "requalification")
        r = RequalificationDecision(
            target_entry_hash=_str(ro, "target_entry_hash"),
            requalified_identity=_identity_from(ro.get("requalified_identity")),
            new_evidence_refs=_strs(ro, "new_evidence_refs"),
            rationale=_str(ro, "rationale"),
        )
    else:
        raise RegisterInvalid("kind/status combination is not valid")
    entry = RegisterEntry(
        seq=_int(o, "seq"),
        prev_entry_hash=_str(o, "prev_entry_hash"),
        kind=kind,
        status=status,
        decided_at=_int(o, "decided_at"),
        post_evaluation=_bool(o, "post_evaluation"),
        authority=authority,
        quarantine=q,
        requalification=r,
        entry_hash=_str(o, "entry_hash"),
    )
    if _entry_line(entry) != line:
        raise RegisterInvalid("register line is not in canonical form")
    return entry


def _independence_violation(
    authority: Authority, submitter_id: str, strategy_author_ids: Sequence[str]
) -> str | None:
    if authority.role not in DECIDING_ROLES:
        return f"role {authority.role} may not decide"
    if authority.person_id == submitter_id:
        return "decider is the original submitter"
    if authority.person_id in strategy_author_ids:
        return "decider is a strategy author"
    return None


def verify_register(blob: bytes | None, anchor: RegisterAnchor | None) -> tuple[RegisterEntry, ...]:
    """Parse and verify the whole register against its anchor. Raises RegisterInvalid."""
    if blob is None:
        raise RegisterInvalid("register missing")
    if anchor is None:
        raise RegisterInvalid("register anchor missing")
    if blob and not blob.endswith(b"\n"):
        raise RegisterInvalid("register truncated mid-line")
    lines = blob.split(b"\n")[:-1] if blob else []
    entries: list[RegisterEntry] = []
    by_hash: dict[str, RegisterEntry] = {}
    prev = GENESIS_HASH
    last_decided: int | None = None
    for index, line in enumerate(lines):
        e = _parse_entry(line)
        if e.seq != index:
            raise RegisterInvalid("sequence gap or reorder")
        if e.prev_entry_hash != prev:
            raise RegisterInvalid("hash chain broken")
        if _entry_hash(_entry_body(e)) != e.entry_hash:
            raise RegisterInvalid("entry hash mismatch")
        if last_decided is not None and e.decided_at < last_decided:
            raise RegisterInvalid("decided_at decreases in append order")
        if e.quarantine is not None:
            q = e.quarantine
            reason = _independence_violation(e.authority, q.submitter_id, q.strategy_author_ids)
            if reason is not None:
                raise RegisterInvalid(f"quarantine entry {index}: {reason}")
            if not q.evidence_refs:
                raise RegisterInvalid("quarantine entry without evidence")
            if (q.scope == "WINDOW") != (q.window_id is not None):
                raise RegisterInvalid("scope and window_id disagree")
        if e.requalification is not None:
            target = by_hash.get(e.requalification.target_entry_hash)
            if target is None or target.quarantine is None:
                raise RegisterInvalid("requalification target is not an earlier quarantine")
            tq = target.quarantine
            reason = _independence_violation(e.authority, tq.submitter_id, tq.strategy_author_ids)
            if reason is not None:
                raise RegisterInvalid(f"requalification entry {index}: {reason}")
            if not e.requalification.new_evidence_refs:
                raise RegisterInvalid("requalification without new evidence")
        entries.append(e)
        by_hash[e.entry_hash] = e
        prev = e.entry_hash
        last_decided = e.decided_at
    if anchor.entry_count != len(entries) or anchor.head_hash != prev:
        raise RegisterInvalid("register does not match its anchor (truncated or replaced)")
    return tuple(entries)


def _append(
    entries: Sequence[RegisterEntry],
    kind: EntryKind,
    status: EntryStatus,
    decided_at: int,
    post_evaluation: bool,
    authority: Authority,
    q: QuarantineDecision | None,
    r: RequalificationDecision | None,
) -> RegisterEntry:
    seq = len(entries)
    prev = entries[-1].entry_hash if entries else GENESIS_HASH
    body = _body_obj(seq, prev, kind, status, decided_at, post_evaluation, authority, q, r)
    return RegisterEntry(
        seq, prev, kind, status, decided_at, post_evaluation, authority, q, r, _entry_hash(body)
    )


# --------------------------------------------------------------------------------------
# Recording a quarantine (dataset-level or window-level)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class QuarantineRequest:
    candidate: CandidateDataset
    scope: Scope
    window_id: str | None
    instrument_id: str
    defect_class: str
    criteria_id: str
    evidence_refs: tuple[str, ...]
    submitter_id: str
    strategy_author_ids: tuple[str, ...]
    previous_aliases: tuple[str, ...]
    authority: Authority
    decided_at: int
    post_evaluation: bool


@dataclass(frozen=True)
class RegisterUpdated:
    register: bytes
    anchor: RegisterAnchor
    entry: RegisterEntry


@dataclass(frozen=True)
class Refused:
    reason: str


def record_quarantine(
    register: bytes | None, anchor: RegisterAnchor | None, request: QuarantineRequest
) -> RegisterUpdated | Refused:
    try:
        entries = verify_register(register, anchor)
    except RegisterInvalid as exc:
        return Refused(f"UNVERIFIED: {exc}")
    reason = _independence_violation(
        request.authority, request.submitter_id, request.strategy_author_ids
    )
    if reason is not None:
        return Refused(reason)
    if not request.evidence_refs or not request.criteria_id or not request.defect_class:
        return Refused("criteria id, defect class and evidence are required")
    if (request.scope == "WINDOW") != (request.window_id is not None):
        return Refused("scope and window_id disagree")
    if entries and request.decided_at < entries[-1].decided_at:
        return Refused("decided_at earlier than the register head")
    try:
        profile = content_profile(request.candidate.records)
    except CanonicalizationError as exc:
        return Refused(f"CANONICALIZATION_CONFLICT: {exc}")
    q = QuarantineDecision(
        scope=request.scope,
        content_identity=profile.identity,
        record_hashes=profile.record_hashes,
        shadow_record_hashes=profile.shadow_record_hashes,
        dataset_id=request.candidate.dataset_id,
        previous_aliases=request.previous_aliases,
        lineage_parents=request.candidate.declared_parents,
        window_id=request.window_id,
        instrument_id=request.instrument_id,
        defect_class=request.defect_class,
        criteria_id=request.criteria_id,
        evidence_refs=request.evidence_refs,
        submitter_id=request.submitter_id,
        strategy_author_ids=request.strategy_author_ids,
        metadata=request.candidate.metadata,
    )
    entry = _append(
        entries,
        "QUARANTINE",
        "QUARANTINED",
        request.decided_at,
        request.post_evaluation,
        request.authority,
        q,
        None,
    )
    new_entries = (*entries, entry)
    return RegisterUpdated(register_bytes(new_entries), anchor_for(new_entries), entry)


# --------------------------------------------------------------------------------------
# Requalification
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RequalificationRequest:
    target_entry_hash: str
    requalified_identity: ContentIdentity
    new_evidence_refs: tuple[str, ...]
    authority: Authority
    context_strategy_author_ids: tuple[str, ...]
    rationale: str
    decided_at: int
    post_evaluation: bool
    # Required only when requalifying a subset of the quarantined content (N5): the records are
    # re-hashed and must reproduce ``requalified_identity`` and be a sub-multiset of the target.
    requalified_records: tuple[TickRecord, ...] = ()


def _requalified_ordered_hashes(
    entries: Sequence[RegisterEntry], target_hash: str
) -> frozenset[str]:
    """Ordered content hashes requalified under a quarantine entry, by append order.

    A later QUARANTINE entry for the same content re-quarantines it (see check_reentry).
    """
    out: set[str] = set()
    for e in entries:
        r = e.requalification
        if r is not None and r.target_entry_hash == target_hash:
            out.add(r.requalified_identity.ordered_content_hash)
    return frozenset(out)


def requalify(
    register: bytes | None, anchor: RegisterAnchor | None, request: RequalificationRequest
) -> RegisterUpdated | Refused:
    try:
        entries = verify_register(register, anchor)
    except RegisterInvalid as exc:
        return Refused(f"UNVERIFIED: {exc}")
    target = next((e for e in entries if e.entry_hash == request.target_entry_hash), None)
    if target is None or target.quarantine is None:
        return Refused("target is not a quarantine entry in this register")
    tq = target.quarantine
    reason = _independence_violation(
        request.authority,
        tq.submitter_id,
        (*tq.strategy_author_ids, *request.context_strategy_author_ids),
    )
    if reason is not None:
        return Refused(reason)
    if not request.new_evidence_refs:
        return Refused("new evidence is required")
    used: set[str] = set(tq.evidence_refs)
    for e in entries:
        if (
            e.requalification is not None
            and e.requalification.target_entry_hash == target.entry_hash
        ):
            used.update(e.requalification.new_evidence_refs)
    if used.intersection(request.new_evidence_refs):
        return Refused("evidence is not new (already cited for this quarantine)")
    if not request.rationale:
        return Refused("rationale is required")
    ident = request.requalified_identity
    tid = tq.content_identity
    if (
        ident.canonicalization_version != CANONICALIZATION_VERSION
        or ident.hash_algorithm != HASH_ALGORITHM
        or tid.canonicalization_version != CANONICALIZATION_VERSION
        or tid.hash_algorithm != HASH_ALGORITHM
    ):
        return Refused("CANONICALIZATION_CONFLICT: identity not comparable")
    if ident.multiset_fingerprint != tid.multiset_fingerprint:
        if not request.requalified_records:
            return Refused("requalified identity is not the quarantined content")
        try:
            sub = content_profile(request.requalified_records)
        except CanonicalizationError as exc:
            return Refused(f"CANONICALIZATION_CONFLICT: {exc}")
        if sub.identity != ident:
            return Refused("requalified records do not reproduce the requalified identity")
        if Counter(sub.record_hashes) - Counter(tq.record_hashes):
            return Refused("requalified identity is not the quarantined content or a subset of it")
    if ident.ordered_content_hash in _requalified_ordered_hashes(entries, target.entry_hash):
        return Refused("this content identity is already requalified")
    if entries and request.decided_at < entries[-1].decided_at:
        return Refused("decided_at earlier than the register head")
    r = RequalificationDecision(
        target_entry_hash=target.entry_hash,
        requalified_identity=ident,
        new_evidence_refs=request.new_evidence_refs,
        rationale=request.rationale,
    )
    entry = _append(
        entries,
        "REQUALIFICATION",
        "REQUALIFIED",
        request.decided_at,
        request.post_evaluation,
        request.authority,
        None,
        r,
    )
    new_entries = (*entries, entry)
    return RegisterUpdated(register_bytes(new_entries), anchor_for(new_entries), entry)


def requalification_history(
    blob: bytes, anchor: RegisterAnchor, target_hash: str
) -> tuple[RegisterEntry, ...]:
    """The quarantine entry followed by every requalification of it, in append order."""
    entries = verify_register(blob, anchor)
    return tuple(
        e
        for e in entries
        if e.entry_hash == target_hash
        or (e.requalification is not None and e.requalification.target_entry_hash == target_hash)
    )


# --------------------------------------------------------------------------------------
# Re-entry check
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReentryResult:
    outcome: ReentryOutcome
    relation: Relation
    matched_entry_hashes: tuple[str, ...]
    detail: str

    @property
    def eligibility(self) -> Eligibility:
        if self.outcome in ("UNVERIFIED", "CANONICALIZATION_CONFLICT"):
            return "UNVERIFIED"
        if self.outcome in ("QUARANTINED_MATCH", "DERIVED_FROM_QUARANTINED"):
            return "BLOCKED"
        return "PART_A_REQUIRED"

    @property
    def may_proceed_to_part_a(self) -> bool:
        return self.eligibility == "PART_A_REQUIRED"

    @property
    def trust_granted(self) -> bool:
        """Always False: this check never grants TRUSTED or PASS."""
        return False


def _result(
    outcome: ReentryOutcome, relation: Relation, hashes: Sequence[str], detail: str
) -> ReentryResult:
    return ReentryResult(outcome, relation, tuple(sorted(set(hashes))), detail)


def _comparable(i: ContentIdentity) -> bool:
    return i.canonicalization_version == CANONICALIZATION_VERSION and (
        i.hash_algorithm == HASH_ALGORITHM
    )


def check_reentry(
    candidate: CandidateDataset, register: bytes | None, anchor: RegisterAnchor | None
) -> ReentryResult:
    """Decide the re-entry status of a candidate. Precedence (first that applies):

    UNVERIFIED (register) > CANONICALIZATION_CONFLICT (version/alg/non-canonical) >
    QUARANTINED_MATCH > DERIVED_FROM_QUARANTINED > CANONICALIZATION_CONFLICT
    (representation drift) > REQUALIFIED > NOT_KNOWN.
    """
    try:
        entries = verify_register(register, anchor)
    except RegisterInvalid as exc:
        return _result("UNVERIFIED", "REGISTER_INVALID", (), str(exc))

    for e in entries:
        idents = []
        if e.quarantine is not None:
            idents.append(e.quarantine.content_identity)
        if e.requalification is not None:
            idents.append(e.requalification.requalified_identity)
        if not all(_comparable(i) for i in idents):
            return _result(
                "CANONICALIZATION_CONFLICT",
                "VERSION_MISMATCH",
                (e.entry_hash,),
                "register entry uses another canonicalization version or hash algorithm",
            )
    declared = candidate.declared_identity
    if declared is not None and not _comparable(declared):
        return _result(
            "CANONICALIZATION_CONFLICT",
            "VERSION_MISMATCH",
            (),
            "candidate identity declared under another canonicalization version or algorithm",
        )
    try:
        profile = content_profile(candidate.records)
    except CanonicalizationError as exc:
        return _result("CANONICALIZATION_CONFLICT", "NON_CANONICAL_CONTENT", (), str(exc))
    if declared is not None and declared != profile.identity:
        return _result(
            "UNVERIFIED",
            "DECLARED_IDENTITY_MISMATCH",
            (),
            "declared content identity does not match the records",
        )

    cand = profile.identity
    cand_records = set(profile.record_hashes)
    cand_shadows = set(profile.shadow_record_hashes)

    quarantined: list[tuple[str, Relation]] = []
    derived: list[tuple[str, Relation]] = []
    drift: list[str] = []
    requalified: list[str] = []

    for e in entries:
        q = e.quarantine
        if q is None:
            continue
        # A later quarantine entry for identical content supersedes earlier requalifications
        # because it is itself an active entry that matches; requalifications are per entry.
        requal = _requalified_ordered_hashes(entries, e.entry_hash)
        qi = q.content_identity
        exact: Relation | None = None
        if cand.ordered_content_hash == qi.ordered_content_hash:
            exact = "EXACT_ORDERED"
        elif cand.multiset_fingerprint == qi.multiset_fingerprint:
            exact = "MULTISET"
        if exact is not None:
            if cand.ordered_content_hash in requal:
                requalified.append(e.entry_hash)
            else:
                quarantined.append((e.entry_hash, exact))
            continue
        if cand.ordered_content_hash in requal:
            # N5: this exact subset of the quarantined content was itself requalified.
            requalified.append(e.entry_hash)
            continue
        names = {q.dataset_id, *q.previous_aliases}
        # A declared parent that is itself a requalified identity is not a quarantined parent.
        lineage_hit = any(
            p.ordered_content_hash not in requal
            and (p.dataset_id in names or p.ordered_content_hash == qi.ordered_content_hash)
            for p in candidate.declared_parents
        )
        if lineage_hit:
            derived.append((e.entry_hash, "DECLARED_LINEAGE"))
            continue
        q_records = set(q.record_hashes)
        common = cand_records & q_records
        if common:
            if cand_records <= q_records:
                derived.append((e.entry_hash, "SUBSET"))
            elif q_records <= cand_records:
                derived.append((e.entry_hash, "SUPERSET"))
            else:
                derived.append((e.entry_hash, "PARTIAL_OVERLAP"))
            continue
        if cand_shadows & set(q.shadow_record_hashes):
            drift.append(e.entry_hash)

    if quarantined:
        return _result(
            "QUARANTINED_MATCH",
            quarantined[0][1],
            [h for h, _ in quarantined],
            "candidate content equals quarantined content (ordered or multiset)",
        )
    if derived:
        return _result(
            "DERIVED_FROM_QUARANTINED",
            derived[0][1],
            [h for h, _ in derived],
            "candidate declares lineage from, or shares records with, quarantined content",
        )
    if drift:
        return _result(
            "CANONICALIZATION_CONFLICT",
            "REPRESENTATION_DRIFT",
            drift,
            "candidate re-expresses quarantined values in another stored representation",
        )
    if requalified:
        return _result(
            "REQUALIFIED",
            "EXACT_ORDERED",
            requalified,
            "content identity requalified; normal Part A criteria still apply",
        )
    return _result("NOT_KNOWN", "NONE", (), "no register relation; normal Part A criteria apply")


# --------------------------------------------------------------------------------------
# OLD rule as written at PR head 3eaf57c (D13): for the old-policy failure tests only
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OldRegisterEntry:
    """D13 as written: window id, instrument, defect class, evidence ids, date, decider."""

    window_id: str
    instrument: str
    defect_class: str
    evidence_ids: tuple[str, ...]
    decision_date: int
    decider: str


def old_rule_is_quarantined(
    register: Sequence[OldRegisterEntry], window_id: str, instrument: str
) -> bool:
    """The only lookup the old fields allow: by labels. Content cannot be consulted."""
    return any(e.window_id == window_id and e.instrument == instrument for e in register)


__all__ = [
    "CANONICALIZATION_VERSION",
    "DECIDING_ROLES",
    "GENESIS_HASH",
    "HASH_ALGORITHM",
    "NONFINITE_TOKENS",
    "REGISTER_FORMAT",
    "Authority",
    "CandidateDataset",
    "CanonicalizationError",
    "ContentIdentity",
    "ContentProfile",
    "LineageRef",
    "OldRegisterEntry",
    "QuarantineDecision",
    "QuarantineRequest",
    "Refused",
    "RegisterAnchor",
    "RegisterEntry",
    "RegisterInvalid",
    "RegisterUpdated",
    "ReentryResult",
    "RequalificationDecision",
    "RequalificationRequest",
    "anchor_for",
    "canonical_record_bytes",
    "check_reentry",
    "content_identity",
    "content_profile",
    "old_rule_is_quarantined",
    "record_hash",
    "record_quarantine",
    "register_bytes",
    "requalification_history",
    "requalify",
    "verify_register",
]
