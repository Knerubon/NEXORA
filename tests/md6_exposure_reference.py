"""MD-6 evaluation exposure lineage: reference model.

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING). Test-only reference
model; not runtime enforcement.

Specifies the rule set in
``docs/research/edge-validation/md6-data-gap-policy/EVALUATION-EXPOSURE-LINEAGE.md``:

* an append-only, hash-chained exposure ledger (each entry cites the previous entry hash);
* exposure keyed by (instrument, half-open UTC interval) and by ``content_identity``, never by
  evaluation family id, dataset id or preregistration id;
* exposure inherited by overlapping windows and through dataset lineage (parents and children);
* gate eligibility outcomes INDEPENDENT / EXPLORATORY / UNVERIFIED / REJECTED_RETROSPECTIVE.

``ContentIdentity`` and ``DatasetLineage`` are stand-ins for the definitions owned by
QUARANTINE-IDENTITY.md (content hash plus canonicalization version, and lineage parents). This
module does not define any canonicalization; it treats both fields as opaque strings.

No rule here reads an outcome value (P/L, win rate, trades, signals): records carry no such field.
Ordering is by ledger position. ``recorded_at`` values are synthetic integers and only have to be
non-decreasing; they never override position.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Literal

GENESIS_PREV_HASH = "0" * 64
_MAX_LINEAGE_DEPTH = 64


class Outcome(Enum):
    """Gate-eligibility outcome of one evaluation (or one result family)."""

    INDEPENDENT = "INDEPENDENT"
    EXPLORATORY = "EXPLORATORY"
    UNVERIFIED = "UNVERIFIED"
    REJECTED_RETROSPECTIVE = "REJECTED_RETROSPECTIVE"


# Higher rank = worse. Used only to combine outcomes of a result family.
_RANK: dict[Outcome, int] = {
    Outcome.INDEPENDENT: 0,
    Outcome.EXPLORATORY: 1,
    Outcome.REJECTED_RETROSPECTIVE: 2,
    Outcome.UNVERIFIED: 3,
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


Record = Registration | Evaluation | ProtectedStatisticsView | Abandonment


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
    """Exposed part of ``a`` caused by ``b``: same instrument OR same content, and time overlap."""
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
    ledger: Ledger, before_position: int, pieces: Sequence[Piece], lineage: LineageRegistry
) -> tuple[Piece, ...]:
    """Parts of ``pieces`` observed by any exposure entry strictly before ``before_position``."""
    found: list[Piece] = []
    for entry in ledger[:before_position]:
        targets = _exposing(entry.record)
        if targets is None:
            continue
        for seen in footprint(targets, lineage):
            for piece in pieces:
                hit = collide(piece, seen)
                if hit is not None and hit not in found:
                    found.append(hit)
    return tuple(found)


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
    for entry in ledger:
        record = entry.record
        if isinstance(record, Abandonment) and record.preregistration_id == preregistration_id:
            return "ABANDONED"
        if isinstance(record, Registration) and preregistration_id in record.supersedes:
            return "SUPERSEDED"
    return "ACTIVE"


def _unverified(*reasons: str) -> Assessment:
    return Assessment(Outcome.UNVERIFIED, tuple(reasons))


def classify_evaluation(
    replicas: Sequence[Ledger],
    anchors: Sequence[LedgerAnchor],
    lineage: LineageRegistry,
    evaluation_id: str,
) -> Assessment:
    """Gate-eligibility outcome of one evaluation, computed from the ledger alone.

    Precedence: UNVERIFIED, then REJECTED_RETROSPECTIVE, then EXPLORATORY, else INDEPENDENT.
    """
    ledger, problems = reconcile(replicas)
    if ledger is None:
        return _unverified(*problems)
    hits = _find(ledger, Evaluation, "evaluation_id", evaluation_id)
    if len(hits) != 1:
        return _unverified(f"evaluation {evaluation_id} not found exactly once in the ledger")
    e_entry = hits[0]
    assert isinstance(e_entry.record, Evaluation)
    evaluation = e_entry.record
    anchor_problems = check_anchors(ledger, anchors, e_entry.position)
    if anchor_problems:
        return _unverified(*anchor_problems)
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
    try:
        reg_pieces = footprint(registration.targets, lineage)
        footprint(evaluation.targets, lineage)
        exposed = exposed_parts(ledger, r_entry.position, reg_pieces, lineage)
    except LineageUnresolvable as exc:
        return _unverified(f"exposure history or lineage unverifiable: {exc}")

    reasons: list[str] = []
    if exposed:
        reasons.append("registered windows/content already exposed before registration")
    computed: ExposureStatus = "EXPOSED" if exposed else "UNEXPOSED"
    for label, declared in (
        ("registration", registration.prior_exposure_status),
        ("evaluation", evaluation.prior_exposure_status),
    ):
        if declared != computed:
            reasons.append(f"{label} declares {declared} but ledger shows {computed}")
    for name in ("strategy_version", "policy_version", "evaluation_family_id", "targets"):
        if getattr(evaluation, name) != getattr(registration, name):
            reasons.append(f"evaluation deviates from registration in {name}")
    status = registration_status(ledger, registration.preregistration_id)
    if status != "ACTIVE":
        reasons.append(f"registration is {status}")
    earlier = [
        e
        for e in ledger[: e_entry.position]
        if isinstance(e.record, Evaluation)
        and e.record.preregistration_id == registration.preregistration_id
    ]
    if earlier:
        reasons.append("registration already consumed by an earlier evaluation")
    if reasons:
        return Assessment(Outcome.EXPLORATORY, tuple(reasons), exposed)
    return Assessment(Outcome.INDEPENDENT, ("preregistered before any exposure",))


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
