"""MD-6 evaluation exposure lineage: reference model.

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING). Test-only reference
model; not runtime enforcement.

Specifies the rule set in
``docs/research/edge-validation/md6-data-gap-policy/EVALUATION-EXPOSURE-LINEAGE.md``:

* an append-only, hash-chained exposure ledger (each entry cites the previous entry hash);
* exposure keyed by (instrument, half-open UTC interval) and by ``content_identity``, never by
  evaluation family id, dataset id or preregistration id;
* exposure inherited by overlapping windows and through dataset lineage (parents and children);
* gate eligibility outcomes INDEPENDENT / EXPLORATORY / UNVERIFIED / REJECTED_RETROSPECTIVE;
* one exposure unit, one qualification (N1): exposure is checked at the registration's AND at the
  evaluation's ledger position, and two ACTIVE registrations whose footprints overlap contend, so
  neither is INDEPENDENT on its own, whatever order they are evaluated in;
* outcomes are computed from the ledger prefix ending at the evaluation (N2), PLUS two ledger-wide,
  fail-closed inputs: whole-ledger validity and the lineage registry. The DEFAULT semantics are
  therefore "monotone non-improving" (V9 F-1): a later append that keeps the ledger valid never
  changes an earlier outcome, except that a later multiplicity declaration naming the evaluation's
  registration downgrades it (M5); a later append that makes the ledger invalid voids every
  outcome (UNVERIFIED); no append ever makes an earlier non-INDEPENDENT outcome INDEPENDENT.
  Whether an already issued outcome may instead be FROZEN against such later events is a Quant /
  governance decision (QE-9); the frozen alternative is isolated in
  ``tests/md6_exposure_frozen_prefix_proposal.py`` and is NOT the default;
* a ``MultiplicityDeclaration`` as the only way several pre-declared variants can yield gate
  evidence, and then only one qualification for the whole set (M5: no member is INDEPENDENT on its
  own, in any order). The correction itself is OPEN (QE-7). The model checks the STRUCTURE of the
  two references only (``reference_problem``); it never treats free text as verified authority: a
  well-formed declaration yields PENDING_QUANT_AUTHORITY (not gate eligible) unless an injected
  ``QuantAuthorityVerifier`` approves it. No real verifier or registry is defined (QE-7, OPEN).

``ContentIdentity`` and ``DatasetLineage`` are stand-ins for the definitions owned by
QUARANTINE-IDENTITY.md (content hash plus canonicalization version, and lineage parents). This
module does not define any canonicalization. ``content_hash`` is compared as an opaque string;
two pieces under different canonicalization versions are not comparable and fail closed (N3).
``tests/md6_cross_track_reference.py`` derives both from Track C's content profile.

No rule here reads an outcome value (P/L, win rate, trades, signals): records carry no such field.
Ordering is by ledger position. ``recorded_at`` values are synthetic integers and only have to be
non-decreasing; they never override position.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Literal

GENESIS_PREV_HASH = "0" * 64
_MAX_LINEAGE_DEPTH = 64


class Outcome(Enum):
    """Gate-eligibility outcome of one evaluation (or one result family).

    Only INDEPENDENT is gate eligible. PENDING_QUANT_AUTHORITY is a distinct NON-GATING outcome: a
    declared multiplicity set that is structurally sound but whose Quant authority is NOT VERIFIED.
    """

    INDEPENDENT = "INDEPENDENT"
    PENDING_QUANT_AUTHORITY = "PENDING_QUANT_AUTHORITY"
    EXPLORATORY = "EXPLORATORY"
    UNVERIFIED = "UNVERIFIED"
    REJECTED_RETROSPECTIVE = "REJECTED_RETROSPECTIVE"

    @property
    def gate_eligible(self) -> bool:
        return self is Outcome.INDEPENDENT


# Higher rank = worse. Used only to combine outcomes of a result family.
_RANK: dict[Outcome, int] = {
    Outcome.INDEPENDENT: 0,
    Outcome.PENDING_QUANT_AUTHORITY: 1,
    Outcome.EXPLORATORY: 2,
    Outcome.REJECTED_RETROSPECTIVE: 3,
    Outcome.UNVERIFIED: 4,
}

ExposureStatus = Literal["UNEXPOSED", "EXPOSED"]
LineageStatus = Literal["ROOT", "DERIVED", "UNKNOWN"]
RegistrationStatus = Literal["ACTIVE", "SUPERSEDED", "ABANDONED"]


# --------------------------------------------------------------------------------------------
# Identities and records
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ContentIdentity:
    """Opaque `content_identity` (definition owned by QUARANTINE-IDENTITY.md)."""

    content_hash: str
    canonicalization_version: str


@dataclass(frozen=True)
class Target:
    """One (instrument, window, dataset) named by a record. Window is half-open [start, end)."""

    instrument_id: str
    start_ms: int
    end_ms: int
    dataset_id: str
    content_identity: ContentIdentity


@dataclass(frozen=True)
class Registration:
    preregistration_id: str
    strategy_version: str
    policy_version: str
    evaluation_family_id: str
    targets: tuple[Target, ...]
    prior_exposure_status: ExposureStatus
    registration_timestamp: int
    decision_ref: str
    approval_ref: str
    supersedes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Evaluation:
    """Any run that observed strategy outcomes. ``preregistration_id`` None = unregistered run."""

    evaluation_id: str
    preregistration_id: str | None
    strategy_version: str
    policy_version: str
    evaluation_family_id: str
    targets: tuple[Target, ...]
    prior_exposure_status: ExposureStatus
    registration_timestamp: int | None
    decision_ref: str
    approval_ref: str
    evaluation_timestamp: int


@dataclass(frozen=True)
class ProtectedStatisticsView:
    """Any observation of any statistic (including counts/coverage) on a protected range."""

    view_id: str
    targets: tuple[Target, ...]
    viewer_ref: str
    view_timestamp: int


@dataclass(frozen=True)
class Abandonment:
    preregistration_id: str
    reason_ref: str
    abandonment_timestamp: int


@dataclass(frozen=True)
class MultiplicityDeclaration:
    """Statement that several registrations form one confirmatory set.

    ``quant_authority_ref`` and ``correction_ref`` are checked for STRUCTURE only
    (``reference_problem``); nothing here verifies that they point to a real Quant approval. The
    correction method and any number in it are OPEN (QE-7). The set yields at most one gate
    qualification, and none until a verifier confirms the authority.
    """

    declaration_id: str
    member_preregistration_ids: tuple[str, ...]
    quant_authority_ref: str
    correction_ref: str
    declaration_timestamp: int


Record = Registration | Evaluation | ProtectedStatisticsView | Abandonment | MultiplicityDeclaration


@dataclass(frozen=True)
class LedgerEntry:
    position: int
    prev_hash: str
    recorded_at: int
    kind: str
    record: Record
    entry_hash: str


Ledger = tuple[LedgerEntry, ...]


@dataclass(frozen=True)
class LedgerAnchor:
    """Independently stored (position, entry hash) pair, e.g. in a signed commit."""

    position: int
    entry_hash: str


@dataclass(frozen=True)
class LineageEdge:
    """Child covers [start, end) of ``parent`` (UTC time preserved by derivation)."""

    parent: ContentIdentity
    parent_instrument_id: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class DatasetLineage:
    content_identity: ContentIdentity
    instrument_id: str
    start_ms: int
    end_ms: int
    status: LineageStatus
    parents: tuple[LineageEdge, ...] = ()


LineageRegistry = Mapping[ContentIdentity, DatasetLineage]


@dataclass(frozen=True)
class Piece:
    """Resolved exposure footprint element: (instrument, [start, end), content_identity)."""

    instrument_id: str
    start_ms: int
    end_ms: int
    content_identity: ContentIdentity


@dataclass(frozen=True)
class Assessment:
    outcome: Outcome
    reasons: tuple[str, ...]
    exposed_parts: tuple[Piece, ...] = ()
    contending_preregistrations: tuple[str, ...] = ()
    declaration_id: str | None = None


# --------------------------------------------------------------------------------------------
# Ledger construction and verification
# --------------------------------------------------------------------------------------------


def _kind(record: Record) -> str:
    return type(record).__name__


def _record_time(record: Record) -> int:
    if isinstance(record, Registration):
        return record.registration_timestamp
    if isinstance(record, Evaluation):
        return record.evaluation_timestamp
    if isinstance(record, ProtectedStatisticsView):
        return record.view_timestamp
    if isinstance(record, MultiplicityDeclaration):
        return record.declaration_timestamp
    return record.abandonment_timestamp


def entry_hash(position: int, prev_hash: str, recorded_at: int, record: Record) -> str:
    """SHA-256 over a canonical JSON of the entry (ledger-local format; final format OPEN)."""
    body = {
        "position": position,
        "prev_hash": prev_hash,
        "recorded_at": recorded_at,
        "kind": _kind(record),
        "record": asdict(record),
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def append(ledger: Ledger, record: Record, recorded_at: int) -> Ledger:
    """Return a new ledger with ``record`` appended (the only permitted write)."""
    position = len(ledger)
    prev = ledger[-1].entry_hash if ledger else GENESIS_PREV_HASH
    entry = LedgerEntry(
        position=position,
        prev_hash=prev,
        recorded_at=recorded_at,
        kind=_kind(record),
        record=record,
        entry_hash=entry_hash(position, prev, recorded_at, record),
    )
    return (*ledger, entry)


def verify_chain(ledger: Ledger) -> tuple[str, ...]:
    """Structural problems of one ledger copy. Empty tuple = chain verifies."""
    problems: list[str] = []
    if not ledger:
        return ("ledger empty: exposure history missing",)
    prev = GENESIS_PREV_HASH
    last_time: int | None = None
    prereg_ids: set[str] = set()
    eval_ids: set[str] = set()
    view_ids: set[str] = set()
    declaration_ids: set[str] = set()
    declared_members: set[str] = set()
    for index, entry in enumerate(ledger):
        if entry.position != index:
            problems.append(f"position gap or reorder at index {index} (found {entry.position})")
        if entry.prev_hash != prev:
            problems.append(f"broken chain at position {entry.position}: prev_hash mismatch")
        expected = entry_hash(entry.position, entry.prev_hash, entry.recorded_at, entry.record)
        if entry.entry_hash != expected:
            problems.append(f"entry hash mismatch at position {entry.position}")
        if entry.kind != _kind(entry.record):
            problems.append(f"kind mismatch at position {entry.position}")
        if last_time is not None and entry.recorded_at < last_time:
            problems.append(f"recorded_at decreases at position {entry.position}")
        if _record_time(entry.record) != entry.recorded_at:
            problems.append(f"record timestamp differs from recorded_at at {entry.position}")
        record = entry.record
        if isinstance(record, Registration):
            if record.preregistration_id in prereg_ids:
                problems.append(f"duplicate preregistration id {record.preregistration_id}")
            for old in record.supersedes:
                if old not in prereg_ids:
                    problems.append(f"supersedes unknown or later preregistration {old}")
            prereg_ids.add(record.preregistration_id)
        elif isinstance(record, Evaluation):
            if record.evaluation_id in eval_ids:
                problems.append(f"duplicate evaluation id {record.evaluation_id}")
            eval_ids.add(record.evaluation_id)
        elif isinstance(record, ProtectedStatisticsView):
            if record.view_id in view_ids:
                problems.append(f"duplicate view id {record.view_id}")
            view_ids.add(record.view_id)
        elif isinstance(record, MultiplicityDeclaration):
            if record.declaration_id in declaration_ids:
                problems.append(f"duplicate declaration id {record.declaration_id}")
            declaration_ids.add(record.declaration_id)
            members = record.member_preregistration_ids
            if len(set(members)) != len(members) or len(members) < 2:
                problems.append(f"declaration {record.declaration_id} needs 2+ distinct members")
            for member in members:
                if member not in prereg_ids:
                    problems.append(f"declares unknown or later preregistration {member}")
                if member in declared_members:
                    problems.append(f"preregistration {member} declared in more than one set")
                declared_members.add(member)
        elif record.preregistration_id not in prereg_ids:
            problems.append(
                f"abandons unknown or later preregistration {record.preregistration_id}"
            )
        prev = entry.entry_hash
        last_time = entry.recorded_at
    return tuple(problems)


def reconcile(replicas: Sequence[Ledger]) -> tuple[Ledger | None, tuple[str, ...]]:
    """Verify every replica; a shorter replica must be a hash prefix of the longest one."""
    if not replicas:
        return None, ("no ledger replica supplied: exposure history missing",)
    problems: list[str] = []
    for number, replica in enumerate(replicas):
        problems.extend(f"replica {number}: {p}" for p in verify_chain(replica))
    if problems:
        return None, tuple(problems)
    longest = max(replicas, key=len)
    for number, replica in enumerate(replicas):
        for entry in replica:
            if longest[entry.position].entry_hash != entry.entry_hash:
                problems.append(
                    f"replica {number} disagrees with longest replica at position {entry.position}"
                )
                break
    if problems:
        return None, tuple(problems)
    return longest, ()


def check_anchors(
    ledger: Ledger, anchors: Sequence[LedgerAnchor], must_cover_position: int
) -> tuple[str, ...]:
    """Every anchor must be present; at least one anchor must be at or after the position."""
    problems: list[str] = []
    for anchor in anchors:
        if anchor.position >= len(ledger):
            problems.append(f"anchor at position {anchor.position} missing: ledger truncated")
        elif ledger[anchor.position].entry_hash != anchor.entry_hash:
            problems.append(f"anchor at position {anchor.position} disagrees with ledger")
    if not any(a.position >= must_cover_position for a in anchors):
        problems.append(f"no independent anchor covers position {must_cover_position}")
    return tuple(problems)


# --------------------------------------------------------------------------------------------
# Lineage resolution and overlap
# --------------------------------------------------------------------------------------------


class LineageUnresolvable(Exception):
    """Lineage or identity cannot be verified: callers fail closed (UNVERIFIED)."""


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> tuple[int, int] | None:
    start, end = max(a_start, b_start), min(a_end, b_end)
    return (start, end) if start < end else None


def _covered(start: int, end: int, intervals: Sequence[tuple[int, int]]) -> bool:
    cursor = start
    for s, e in sorted(intervals):
        if s > cursor:
            return False
        cursor = max(cursor, e)
        if cursor >= end:
            return True
    return cursor >= end


def resolve(
    instrument_id: str,
    start_ms: int,
    end_ms: int,
    content: ContentIdentity,
    lineage: LineageRegistry,
    depth: int = 0,
) -> tuple[Piece, ...]:
    """Footprint of [start, end) of ``content``: itself plus every ancestor interval it uses."""
    if depth > _MAX_LINEAGE_DEPTH:
        raise LineageUnresolvable("lineage too deep or cyclic")
    if start_ms >= end_ms:
        raise LineageUnresolvable("empty or inverted window")
    node = lineage.get(content)
    if node is None:
        raise LineageUnresolvable(f"no lineage record for {content.content_hash}")
    if node.status == "UNKNOWN":
        raise LineageUnresolvable(f"lineage UNKNOWN for {content.content_hash}")
    if node.instrument_id != instrument_id:
        raise LineageUnresolvable(
            f"instrument {instrument_id} conflicts with lineage instrument {node.instrument_id}"
        )
    if start_ms < node.start_ms or end_ms > node.end_ms:
        raise LineageUnresolvable("window outside the dataset's recorded coverage")
    pieces = [Piece(instrument_id, start_ms, end_ms, content)]
    if node.status == "DERIVED":
        if not node.parents:
            raise LineageUnresolvable("DERIVED dataset without parents")
        clipped: list[tuple[int, int]] = []
        for edge in node.parents:
            part = _overlap(start_ms, end_ms, edge.start_ms, edge.end_ms)
            if part is None:
                continue
            clipped.append(part)
            pieces.extend(
                resolve(
                    edge.parent_instrument_id, part[0], part[1], edge.parent, lineage, depth + 1
                )
            )
        if not _covered(start_ms, end_ms, clipped):
            raise LineageUnresolvable("part of derived window has no recorded parent")
    return tuple(pieces)


def footprint(targets: Sequence[Target], lineage: LineageRegistry) -> tuple[Piece, ...]:
    pieces: list[Piece] = []
    for t in targets:
        pieces.extend(resolve(t.instrument_id, t.start_ms, t.end_ms, t.content_identity, lineage))
    return tuple(pieces)


def collide(a: Piece, b: Piece) -> Piece | None:
    """Exposed part of ``a`` caused by ``b``: same instrument OR same content, and time overlap.

    Raises LineageUnresolvable when the two content identities are under different
    canonicalization versions: content equality cannot be decided (CANONICALIZATION_CONFLICT).
    """
    ca, cb = a.content_identity, b.content_identity
    if ca.canonicalization_version != cb.canonicalization_version:
        raise LineageUnresolvable(
            "CANONICALIZATION_CONFLICT: content identities under different canonicalization "
            f"versions ({ca.canonicalization_version} vs {cb.canonicalization_version})"
        )
    if a.instrument_id != b.instrument_id and a.content_identity != b.content_identity:
        return None
    part = _overlap(a.start_ms, a.end_ms, b.start_ms, b.end_ms)
    if part is None:
        return None
    return Piece(a.instrument_id, part[0], part[1], a.content_identity)


def _exposing(record: Record) -> tuple[Target, ...] | None:
    if isinstance(record, Evaluation | ProtectedStatisticsView):
        return record.targets
    return None


def exposed_parts(
    ledger: Ledger,
    before_position: int,
    pieces: Sequence[Piece],
    lineage: LineageRegistry,
    ignore_preregistrations: frozenset[str] = frozenset(),
) -> tuple[Piece, ...]:
    """Parts of ``pieces`` observed by any exposure entry strictly before ``before_position``.

    ``ignore_preregistrations`` skips evaluations run under those registrations; it is used only
    for the members of one Quant-declared multiplicity set (never for views or unregistered runs).
    """
    found: list[Piece] = []
    for entry in ledger[:before_position]:
        targets = _exposing(entry.record)
        if targets is None:
            continue
        record = entry.record
        if (
            isinstance(record, Evaluation)
            and record.preregistration_id is not None
            and record.preregistration_id in ignore_preregistrations
        ):
            continue
        for seen in footprint(targets, lineage):
            for piece in pieces:
                hit = collide(piece, seen)
                if hit is not None and hit not in found:
                    found.append(hit)
    return tuple(found)


# --------------------------------------------------------------------------------------------
# Structural validation of authority references (V9 F-2)
# --------------------------------------------------------------------------------------------

QuantAuthorityVerifier = Callable[[str, str], bool]
"""(quant_authority_ref, correction_ref) -> approved. Its real definition is OPEN (QE-7): no
registry of Quant authority exists. The default is NO verifier, which means NOT VERIFIED."""

_REF_ALPHABET = re.compile(r"[\x21-\x7e]+")  # printable ASCII, no whitespace or control chars
# Literals that assert the absence of a reference cannot be a reference. Structural only.
_NO_REFERENCE_LITERALS = frozenset(
    {
        "none",
        "null",
        "nil",
        "n/a",
        "na",
        "tbd",
        "todo",
        "pending",
        "unknown",
        "undefined",
        "missing",
    }
)


def reference_problem(ref: object) -> str | None:
    """Structural problem of a Quant authority / correction reference, or None if well formed.

    STRUCTURE ONLY (OPEN: the real reference format and registry, QE-1 / QE-7). A well-formed
    string is still unverified free text; this function never says a reference is genuine.
    """
    if not isinstance(ref, str):
        return "reference is not text"
    if not ref.strip():
        return "reference is blank"
    if ref != ref.strip():
        return "reference has leading or trailing whitespace"
    if not _REF_ALPHABET.fullmatch(ref):
        return "reference contains whitespace, control or non-ASCII characters"
    if not any(c.isalnum() for c in ref):
        return "reference has no alphanumeric character"
    if ref.lower() in _NO_REFERENCE_LITERALS:
        return "reference is a no-reference literal"
    return None


# --------------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------------


def _missing_fields(record: Registration | Evaluation) -> tuple[str, ...]:
    missing: list[str] = []
    for name in ("strategy_version", "policy_version", "evaluation_family_id"):
        if not getattr(record, name):
            missing.append(name)
    for name in ("decision_ref", "approval_ref"):
        if not getattr(record, name):
            missing.append(name)
    if record.prior_exposure_status not in ("UNEXPOSED", "EXPOSED"):
        missing.append("prior_exposure_status")
    if not record.targets:
        missing.append("targets")
    for t in record.targets:
        if not (t.instrument_id and t.dataset_id):
            missing.append("target instrument_id/dataset_id")
        if not (t.content_identity.content_hash and t.content_identity.canonicalization_version):
            missing.append("target content_identity")
        if t.start_ms >= t.end_ms:
            missing.append("target window [from, to)")
    if isinstance(record, Evaluation) and record.preregistration_id is not None:
        if record.registration_timestamp is None:
            missing.append("registration_timestamp")
    return tuple(missing)


def _find(ledger: Ledger, kind: type[object], key: str, value: str) -> list[LedgerEntry]:
    return [e for e in ledger if isinstance(e.record, kind) and getattr(e.record, key) == value]


def registration_status(ledger: Ledger, preregistration_id: str) -> RegistrationStatus:
    """Status from the entries of ``ledger``. Pass a prefix to get the status at a position."""
    for entry in ledger:
        record = entry.record
        if isinstance(record, Abandonment) and record.preregistration_id == preregistration_id:
            return "ABANDONED"
        if isinstance(record, Registration) and preregistration_id in record.supersedes:
            return "SUPERSEDED"
    return "ACTIVE"


def declaration_of(ledger: Ledger, preregistration_id: str) -> str | None:
    """Id of the multiplicity declaration naming ``preregistration_id``, anywhere in ``ledger``."""
    for entry in ledger:
        record = entry.record
        if (
            isinstance(record, MultiplicityDeclaration)
            and preregistration_id in record.member_preregistration_ids
        ):
            return record.declaration_id
    return None


def contending_registrations(
    prefix: Ledger,
    own_preregistration_id: str,
    pieces: Sequence[Piece],
    lineage: LineageRegistry,
) -> tuple[str, ...]:
    """ACTIVE registrations in ``prefix``, other than the own one, whose footprint overlaps.

    A registration withdrawn (superseded or abandoned) inside ``prefix`` does not contend.
    Raises LineageUnresolvable if any registration's footprint cannot be resolved: contention
    that cannot be decided fails closed.
    """
    found: list[str] = []
    for entry in prefix:
        record = entry.record
        if not isinstance(record, Registration):
            continue
        if record.preregistration_id == own_preregistration_id:
            continue
        if registration_status(prefix, record.preregistration_id) != "ACTIVE":
            continue
        theirs = footprint(record.targets, lineage)
        if any(collide(a, b) is not None for a in pieces for b in theirs):
            found.append(record.preregistration_id)
    return tuple(found)


def _unverified(*reasons: str) -> Assessment:
    return Assessment(Outcome.UNVERIFIED, tuple(reasons))


def _assess(
    ledger: Ledger,
    lineage: LineageRegistry,
    e_entry: LedgerEntry,
    declared_set: frozenset[str] = frozenset(),
) -> Assessment:
    """Outcome of the evaluation at ``e_entry``, from the ledger prefix ending at it.

    ``declared_set`` holds the members of one Quant-declared multiplicity set; their mutual
    contention and their evaluations' mutual exposure are ignored. Only
    ``classify_multiplicity_set`` passes it.
    """
    assert isinstance(e_entry.record, Evaluation)
    evaluation = e_entry.record
    missing = _missing_fields(evaluation)
    if missing:
        return _unverified(*(f"evaluation missing {m}" for m in missing))
    if evaluation.preregistration_id is None:
        return Assessment(Outcome.EXPLORATORY, ("evaluation cites no preregistration",))
    reg_hits = _find(ledger, Registration, "preregistration_id", evaluation.preregistration_id)
    if len(reg_hits) != 1:
        return _unverified(f"preregistration {evaluation.preregistration_id} not in the ledger")
    r_entry = reg_hits[0]
    assert isinstance(r_entry.record, Registration)
    registration = r_entry.record
    missing = _missing_fields(registration)
    if missing:
        return _unverified(*(f"registration missing {m}" for m in missing))
    if r_entry.position > e_entry.position:
        return Assessment(
            Outcome.REJECTED_RETROSPECTIVE,
            (
                f"registration at position {r_entry.position} is after the evaluation it covers "
                f"at position {e_entry.position}",
            ),
        )
    if evaluation.registration_timestamp != registration.registration_timestamp:
        return _unverified("evaluation cites a registration timestamp the ledger contradicts")
    # R2/N2: nothing appended after the evaluation is consulted from here on.
    prefix = ledger[: e_entry.position + 1]
    try:
        reg_pieces = footprint(registration.targets, lineage)
        footprint(evaluation.targets, lineage)
        at_registration = exposed_parts(prefix, r_entry.position, reg_pieces, lineage)
        before_evaluation = exposed_parts(
            prefix, e_entry.position, reg_pieces, lineage, declared_set
        )
        contenders = tuple(
            c
            for c in contending_registrations(
                prefix, registration.preregistration_id, reg_pieces, lineage
            )
            if c not in declared_set
        )
    except LineageUnresolvable as exc:
        return _unverified(f"exposure history or lineage unverifiable: {exc}")

    reasons: list[str] = []
    if at_registration:
        reasons.append("registered windows/content already exposed before registration")
    elif before_evaluation:
        reasons.append("footprint exposed after registration and before this evaluation")
    at_reg_status: ExposureStatus = "EXPOSED" if at_registration else "UNEXPOSED"
    at_eval_status: ExposureStatus = "EXPOSED" if before_evaluation else "UNEXPOSED"
    for label, declared, computed in (
        ("registration", registration.prior_exposure_status, at_reg_status),
        ("evaluation", evaluation.prior_exposure_status, at_eval_status),
    ):
        if declared != computed:
            reasons.append(f"{label} declares {declared} but ledger shows {computed}")
    if contenders:
        reasons.append(
            "exposure unit contended by other ACTIVE registrations "
            f"{', '.join(contenders)}: one exposure unit yields at most one qualification, and "
            "several variants need a Quant multiplicity declaration (QE-7)"
        )
    for name in ("strategy_version", "policy_version", "evaluation_family_id", "targets"):
        if getattr(evaluation, name) != getattr(registration, name):
            reasons.append(f"evaluation deviates from registration in {name}")
    status = registration_status(prefix, registration.preregistration_id)
    if status != "ACTIVE":
        reasons.append(f"registration is {status}")
    declaration_id: str | None = None
    if not declared_set:
        # M5 (V9 F-3): a declared member is never INDEPENDENT on its own, even when its footprint is
        # disjoint from every other member. Only classify_multiplicity_set may qualify the set.
        declaration_id = declaration_of(ledger, registration.preregistration_id)
        if declaration_id is not None:
            reasons.append(
                f"registration is a member of declared multiplicity set {declaration_id}: "
                "not INDEPENDENT on its own (M5); cite the set outcome instead"
            )
    earlier = [
        e
        for e in prefix[:-1]
        if isinstance(e.record, Evaluation)
        and e.record.preregistration_id == registration.preregistration_id
    ]
    if earlier:
        reasons.append("registration already consumed by an earlier evaluation")
    if reasons:
        return Assessment(
            Outcome.EXPLORATORY, tuple(reasons), before_evaluation, contenders, declaration_id
        )
    return Assessment(Outcome.INDEPENDENT, ("preregistered before any exposure, uncontended",))


def classify_evaluation(
    replicas: Sequence[Ledger],
    anchors: Sequence[LedgerAnchor],
    lineage: LineageRegistry,
    evaluation_id: str,
) -> Assessment:
    """Gate-eligibility outcome of one evaluation, computed from the ledger alone.

    Precedence: UNVERIFIED, then REJECTED_RETROSPECTIVE, then EXPLORATORY, else INDEPENDENT.
    DEFAULT semantics (V9 F-1): the outcome is computed from the ledger prefix ending at the
    evaluation plus whole-ledger validity and the supplied lineage registry. It is monotone
    non-improving under appends; it is NOT frozen against a later invalid append or a later
    declaration. A member of a multiplicity set is never INDEPENDENT on its own (M5).
    """
    ledger, problems = reconcile(replicas)
    if ledger is None:
        return _unverified(*problems)
    hits = _find(ledger, Evaluation, "evaluation_id", evaluation_id)
    if len(hits) != 1:
        return _unverified(f"evaluation {evaluation_id} not found exactly once in the ledger")
    anchor_problems = check_anchors(ledger, anchors, hits[0].position)
    if anchor_problems:
        return _unverified(*anchor_problems)
    return _assess(ledger, lineage, hits[0])


def classify_multiplicity_set(
    replicas: Sequence[Ledger],
    anchors: Sequence[LedgerAnchor],
    lineage: LineageRegistry,
    declaration_id: str,
    authority_verifier: QuantAuthorityVerifier | None = None,
) -> Assessment:
    """The single gate qualification a declared multiplicity set may yield.

    INDEPENDENT only if: the declaration's two references are structurally well formed AND the
    injected ``authority_verifier`` approves them (default: none, so never); it is recorded after
    every member registration and before every member evaluation; every member has exactly one
    evaluation; and every member evaluation would be INDEPENDENT once contention and exposure among
    the declared members themselves are set aside. Any other registration, view or unregistered run
    on the footprint still disqualifies. The correction is not applied here.

    A structurally sound set whose authority is not verified is PENDING_QUANT_AUTHORITY (not gate
    eligible). Free text is never treated as an approval.
    """
    ledger, problems = reconcile(replicas)
    if ledger is None:
        return _unverified(*problems)
    hits = _find(ledger, MultiplicityDeclaration, "declaration_id", declaration_id)
    if len(hits) != 1:
        return _unverified(f"declaration {declaration_id} not found exactly once in the ledger")
    d_entry = hits[0]
    assert isinstance(d_entry.record, MultiplicityDeclaration)
    declaration = d_entry.record
    for label, ref in (
        ("quant_authority_ref", declaration.quant_authority_ref),
        ("correction_ref", declaration.correction_ref),
    ):
        problem = reference_problem(ref)
        if problem is not None:
            return _unverified(f"multiplicity declaration {label} is malformed: {problem} (QE-7)")
    members = frozenset(declaration.member_preregistration_ids)
    evaluations: list[LedgerEntry] = []
    for member in declaration.member_preregistration_ids:
        runs = [
            e
            for e in ledger
            if isinstance(e.record, Evaluation) and e.record.preregistration_id == member
        ]
        if len(runs) != 1:
            return _unverified(f"member {member} has {len(runs)} evaluations; exactly one required")
        evaluations.append(runs[0])
    anchor_problems = check_anchors(ledger, anchors, max(e.position for e in evaluations))
    if anchor_problems:
        return _unverified(*anchor_problems)
    worst = Outcome.INDEPENDENT
    reasons: list[str] = []
    for e_entry in evaluations:
        assert isinstance(e_entry.record, Evaluation)
        if e_entry.position < d_entry.position:
            assessment = Assessment(
                Outcome.EXPLORATORY,
                ("declaration recorded after a member evaluation",),
            )
        else:
            assessment = _assess(ledger, lineage, e_entry, members)
        if _RANK[assessment.outcome] > _RANK[worst]:
            worst = assessment.outcome
        reasons.extend(f"{e_entry.record.evaluation_id}: {r}" for r in assessment.reasons)
    if worst is not Outcome.INDEPENDENT:
        return Assessment(worst, tuple(reasons))
    verified = False
    if authority_verifier is not None:
        try:
            verified = bool(
                authority_verifier(declaration.quant_authority_ref, declaration.correction_ref)
            )
        except Exception as exc:  # a verifier that cannot decide fails closed
            return _unverified(f"authority verifier failed: {type(exc).__name__}")
    if not verified:
        return Assessment(
            Outcome.PENDING_QUANT_AUTHORITY,
            (
                f"declared set {declaration_id}: structure and exposure checks passed, but the "
                "Quant authority and correction references are free text and NOT VERIFIED "
                "(QE-7 OPEN); not gate eligible",
            ),
        )
    return Assessment(
        Outcome.INDEPENDENT,
        (
            f"one qualification for declared set {declaration_id} "
            f"({len(members)} members); correction {declaration.correction_ref} must be applied "
            "(method OPEN, QE-7); no member is INDEPENDENT on its own",
        ),
    )


# --------------------------------------------------------------------------------------------
# Relabelling, disclosure, result families
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RelabelDecision:
    accepted: bool
    outcome: Outcome
    reason: str


def request_relabel(current: Assessment, requested: Outcome) -> RelabelDecision:
    """Labels come only from recomputation. Manual change is allowed only as a voluntary
    downgrade INDEPENDENT -> EXPLORATORY; every other request is refused."""
    if current.outcome == requested:
        return RelabelDecision(True, current.outcome, "no change")
    if current.outcome is Outcome.INDEPENDENT and requested is Outcome.EXPLORATORY:
        return RelabelDecision(True, Outcome.EXPLORATORY, "voluntary downgrade")
    return RelabelDecision(False, current.outcome, "relabel refused: outcome is ledger-derived")


@dataclass(frozen=True)
class RegistrationDisclosure:
    preregistration_id: str
    status: RegistrationStatus
    position: int


def registrations_touching(
    ledger: Ledger, targets: Sequence[Target], lineage: LineageRegistry
) -> tuple[RegistrationDisclosure, ...]:
    """Every registration (any status) whose footprint overlaps ``targets``' footprint.

    Raises LineageUnresolvable if any footprint cannot be resolved (callers fail closed).
    """
    wanted = footprint(targets, lineage)
    found: list[RegistrationDisclosure] = []
    for entry in ledger:
        record = entry.record
        if not isinstance(record, Registration):
            continue
        theirs = footprint(record.targets, lineage)
        if any(collide(a, b) is not None for a in wanted for b in theirs):
            found.append(
                RegistrationDisclosure(
                    record.preregistration_id,
                    registration_status(ledger, record.preregistration_id),
                    entry.position,
                )
            )
    return tuple(found)


def check_family_disclosure(
    ledger: Ledger,
    targets: Sequence[Target],
    lineage: LineageRegistry,
    listed_ids: Sequence[str],
    listed_count: int,
) -> tuple[str, ...]:
    """Problems with a result family's list of all preregistrations touching its data."""
    try:
        required = registrations_touching(ledger, targets, lineage)
    except LineageUnresolvable as exc:
        return (f"disclosure unverifiable: {exc}",)
    required_ids = {r.preregistration_id for r in required}
    problems: list[str] = []
    problems.extend(
        f"undisclosed preregistration {m}" for m in sorted(required_ids - set(listed_ids))
    )
    problems.extend(
        f"listed id not touching data {x}" for x in sorted(set(listed_ids) - required_ids)
    )
    if listed_count != len(required_ids) or len(set(listed_ids)) != len(listed_ids):
        problems.append(f"disclosed count {listed_count} != required count {len(required_ids)}")
    return tuple(problems)


def evaluations_touching(
    ledger: Ledger, targets: Sequence[Target], lineage: LineageRegistry
) -> tuple[str, ...]:
    """Every evaluation (registered or not, any outcome, downgraded or not) on the footprint.

    Raises LineageUnresolvable if any footprint cannot be resolved (callers fail closed).
    """
    wanted = footprint(targets, lineage)
    found: list[str] = []
    for entry in ledger:
        record = entry.record
        if not isinstance(record, Evaluation):
            continue
        theirs = footprint(record.targets, lineage)
        if any(collide(a, b) is not None for a in wanted for b in theirs):
            found.append(record.evaluation_id)
    return tuple(found)


def check_evaluation_disclosure(
    ledger: Ledger,
    targets: Sequence[Target],
    lineage: LineageRegistry,
    listed_evaluation_ids: Sequence[str],
) -> tuple[str, ...]:
    """D-4: a result family lists every evaluation on its footprint, including any INDEPENDENT
    result voluntarily downgraded or under a later superseded registration (no file drawer)."""
    try:
        required = set(evaluations_touching(ledger, targets, lineage))
    except LineageUnresolvable as exc:
        return (f"disclosure unverifiable: {exc}",)
    problems = [
        f"undisclosed evaluation {m}" for m in sorted(required - set(listed_evaluation_ids))
    ]
    problems.extend(
        f"listed evaluation not touching data {x}"
        for x in sorted(set(listed_evaluation_ids) - required)
    )
    if len(set(listed_evaluation_ids)) != len(listed_evaluation_ids):
        problems.append("duplicate evaluation id in disclosure")
    return tuple(problems)


def family_outcome(
    assessments: Sequence[Assessment], disclosure_problems: Sequence[str]
) -> Outcome:
    """A family is INDEPENDENT only if every member is and disclosure is complete."""
    if not assessments or disclosure_problems:
        return Outcome.UNVERIFIED
    return max((a.outcome for a in assessments), key=lambda o: _RANK[o])


# --------------------------------------------------------------------------------------------
# OLD rules as written at PR head 3eaf57cb (encoded only to demonstrate the defect)
# --------------------------------------------------------------------------------------------


def old_rule_d15_gate_eligible(ledger: Ledger, evaluation_id: str) -> bool:
    """D15 / B-04 at PR head: eligible if the cited preregistration was committed before the
    evaluation, is not superseded, and is a 'new preregistration id' with a 'new dataset identity
    or evaluation family' relative to earlier registrations. Exposure is never consulted."""
    hits = _find(ledger, Evaluation, "evaluation_id", evaluation_id)
    if len(hits) != 1:
        return False
    assert isinstance(hits[0].record, Evaluation)
    evaluation = hits[0].record
    if evaluation.preregistration_id is None:
        return False
    regs = _find(ledger, Registration, "preregistration_id", evaluation.preregistration_id)
    if len(regs) != 1 or regs[0].position > hits[0].position:
        return False
    assert isinstance(regs[0].record, Registration)
    reg = regs[0].record
    if registration_status(ledger, reg.preregistration_id) == "SUPERSEDED":
        return False
    earlier = [e.record for e in ledger[: regs[0].position] if isinstance(e.record, Registration)]
    if not earlier:
        return True
    used_families = {r.evaluation_family_id for r in earlier}
    used_datasets = {t.dataset_id for r in earlier for t in r.targets}
    new_family = reg.evaluation_family_id not in used_families
    new_dataset = all(t.dataset_id not in used_datasets for t in reg.targets)
    return new_family or new_dataset


def old_rule_d16_view_permitted(ledger: Ledger, view_id: str) -> bool:
    """D16 at PR head: protected-range gap statistics are only forbidden 'before preregistration
    of thresholds'. A view after any preregistration on the range is permitted, and nothing
    stops it feeding a later preregistration."""
    views = _find(ledger, ProtectedStatisticsView, "view_id", view_id)
    if len(views) != 1:
        return False
    assert isinstance(views[0].record, ProtectedStatisticsView)
    view_keys = {(t.instrument_id, t.start_ms, t.end_ms) for t in views[0].record.targets}
    for entry in ledger[: views[0].position]:
        if isinstance(entry.record, Registration):
            for t in entry.record.targets:
                for inst, s, e in view_keys:
                    if t.instrument_id == inst and _overlap(t.start_ms, t.end_ms, s, e):
                        return True
    return False
