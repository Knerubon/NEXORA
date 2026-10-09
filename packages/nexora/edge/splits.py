"""Chronological train / validation / holdout splits and walk-forward windows.

Splits are defined by absolute UTC boundaries, never by shuffling. An optional purge gap
keeps a label horizon from straddling a boundary. The holdout is locked: reading it needs
an unlock bound to the exact plan hash AND an access log, and every read is recorded.

Neither the unlock nor the log is proof of authority: both are caller-supplied objects. They
make a holdout read deliberate and visible; they do not authorize a statistical claim (see
`nexora.edge.evidence`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, cast

from nexora.artifacts import canonical_hash

Segment = Literal["train", "validation", "holdout"]
# Closed vocabulary. Anything else is rejected, never mapped to a segment by fall-through.
SEGMENTS: tuple[str, ...] = ("train", "validation", "holdout")
_MICROSECOND = timedelta(microseconds=1)


class SplitError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class HoldoutLocked(SplitError):
    def __init__(self, code: str = "holdout_locked") -> None:
        super().__init__(code)


def validate_segment(segment: object) -> Segment:
    """Exact, case-sensitive match against the closed vocabulary. A `str` subclass (which
    could override `==`) and every other type are rejected."""
    if type(segment) is not str or segment not in SEGMENTS:
        raise SplitError("invalid_segment")
    return cast(Segment, segment)


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise SplitError("invalid_split_input")
    if value.tzinfo is None or value.utcoffset() is None:
        raise SplitError("timezone_required")
    return value


def _duration(value: object) -> timedelta:
    if not isinstance(value, timedelta):
        raise SplitError("invalid_split_input")
    return value


@dataclass(frozen=True, slots=True)
class SplitPlan:
    """Half-open segments: train [start, train_end), validation [train_end + purge,
    validation_end), holdout [validation_end + purge, end)."""

    start: datetime
    train_end: datetime
    validation_end: datetime
    end: datetime
    purge: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        for value in (self.start, self.train_end, self.validation_end, self.end):
            _aware(value)
        if _duration(self.purge) < timedelta(0):
            raise SplitError("negative_purge")
        if not (
            self.start < self.train_end
            and self.train_end + self.purge < self.validation_end
            and self.validation_end + self.purge < self.end
        ):
            raise SplitError("split_boundaries_not_increasing")

    @property
    def plan_hash(self) -> str:
        # timedelta is not canonically serializable; hash the purge in whole microseconds so
        # two different purges can never collide.
        return canonical_hash(
            (
                self.start,
                self.train_end,
                self.validation_end,
                self.end,
                self.purge // _MICROSECOND,
            )
        )

    def bounds(self, segment: str) -> tuple[datetime, datetime]:
        name = validate_segment(segment)
        if name == "train":
            return self.start, self.train_end
        if name == "validation":
            return self.train_end + self.purge, self.validation_end
        return self.validation_end + self.purge, self.end


@dataclass(frozen=True, slots=True)
class HoldoutUnlock:
    """A caller's DECLARATION that the holdout of the plan with this hash may be read.

    It is not a credential: the library cannot prove who wrote it. Its only effect is that a
    holdout read must be explicit and must be recorded in a `HoldoutAccessLog`."""

    plan_hash: str
    authorized_by: str
    reason: str


@dataclass(frozen=True, slots=True)
class HoldoutAccess:
    sequence: int
    plan_hash: str
    authorized_by: str
    reason: str
    selected: int
    # True when this plan's holdout had already been read once before.
    repeat: bool
    # Always False: the authorization came from a caller-supplied object and was never
    # verified by anything independent of the caller.
    authorization_verified: bool = False


class HoldoutAccessLog:
    """In-memory record of holdout reads. Not tamper-evident and not authoritative; persist
    `entries` with the report and let a reviewer judge them."""

    def __init__(self) -> None:
        self._entries: list[HoldoutAccess] = []

    @property
    def entries(self) -> tuple[HoldoutAccess, ...]:
        return tuple(self._entries)

    def record(self, unlock: HoldoutUnlock, selected: int) -> HoldoutAccess:
        entry = HoldoutAccess(
            sequence=len(self._entries) + 1,
            plan_hash=unlock.plan_hash,
            authorized_by=unlock.authorized_by,
            reason=unlock.reason,
            selected=selected,
            repeat=any(e.plan_hash == unlock.plan_hash for e in self._entries),
            authorization_verified=False,
        )
        self._entries.append(entry)
        return entry


def _check_access(
    plan: SplitPlan,
    segment: Segment,
    unlock: HoldoutUnlock | None,
    access_log: HoldoutAccessLog | None,
) -> None:
    """The single gate for every code path that can read holdout data."""
    if segment != "holdout":
        return
    if (
        unlock is None
        or unlock.plan_hash != plan.plan_hash
        or not unlock.authorized_by.strip()
        or not unlock.reason.strip()
    ):
        raise HoldoutLocked
    if access_log is None:
        raise HoldoutLocked("holdout_access_log_required")


def select_segment[T](
    plan: SplitPlan,
    items: Sequence[T],
    times: Sequence[datetime],
    segment: str,
    *,
    unlock: HoldoutUnlock | None = None,
    access_log: HoldoutAccessLog | None = None,
) -> tuple[T, ...]:
    """Point-in-time items whose timestamp lies in the segment. `times` aligns with `items`.

    For items that span an interval (bars) use `select_completed_segment`: an item selected
    here by its start alone may finish after the segment ends."""
    name = validate_segment(segment)
    _check_access(plan, name, unlock, access_log)
    stored = tuple(items)
    stamps = tuple(times)
    if len(stored) != len(stamps):
        raise SplitError("length_mismatch")
    low, high = plan.bounds(name)
    for moment in stamps:
        _aware(moment)
    selected = tuple(
        item for item, moment in zip(stored, stamps, strict=True) if low <= moment < high
    )
    if name == "holdout":
        assert unlock is not None and access_log is not None
        access_log.record(unlock, len(selected))
    return selected


def select_completed_segment[T](
    plan: SplitPlan,
    items: Sequence[T],
    starts: Sequence[datetime],
    ends: Sequence[datetime],
    segment: str,
    *,
    unlock: HoldoutUnlock | None = None,
    access_log: HoldoutAccessLog | None = None,
) -> tuple[T, ...]:
    """Items whose whole interval [start, end] lies inside the segment.

    An item that starts in one segment but is only complete (or only available) after the
    segment ends belongs to NO segment, exactly like a purged item. This keeps information
    that arrived after a boundary out of the earlier segment."""
    name = validate_segment(segment)
    _check_access(plan, name, unlock, access_log)
    stored = tuple(items)
    opens = tuple(starts)
    closes = tuple(ends)
    if not len(stored) == len(opens) == len(closes):
        raise SplitError("length_mismatch")
    low, high = plan.bounds(name)
    for first, last in zip(opens, closes, strict=True):
        if _aware(first) > _aware(last):
            raise SplitError("invalid_interval")
    selected = tuple(
        item
        for item, first, last in zip(stored, opens, closes, strict=True)
        if low <= first and last <= high
    )
    if name == "holdout":
        assert unlock is not None and access_log is not None
        access_log.record(unlock, len(selected))
    return selected


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def walk_forward_windows(
    plan: SplitPlan,
    *,
    train: timedelta,
    test: timedelta,
    step: timedelta | None = None,
    purge: timedelta = timedelta(0),
    anchored: bool = False,
) -> tuple[WalkForwardWindow, ...]:
    """Chronological windows over the plan's NON-holdout range [plan.start, validation_end).

    The range comes from the plan, never from the caller, so walk-forward cannot reach the
    holdout. Each test window starts after its train window plus purge, and test windows
    never overlap one another."""
    if not isinstance(plan, SplitPlan):
        raise SplitError("invalid_split_input")
    start, end = plan.start, plan.validation_end
    _duration(train)
    _duration(test)
    _duration(purge)
    if step is not None:
        _duration(step)
    if train <= timedelta(0) or test <= timedelta(0) or purge < timedelta(0):
        raise SplitError("invalid_window_lengths")
    stride = step if step is not None else test
    if stride < test:
        raise SplitError("test_windows_would_overlap")
    windows: list[WalkForwardWindow] = []
    cursor = start
    while True:
        train_start = start if anchored else cursor
        train_end = cursor + train
        test_start = train_end + purge
        test_end = test_start + test
        if test_end > end:
            break
        windows.append(
            WalkForwardWindow(len(windows), train_start, train_end, test_start, test_end)
        )
        cursor += stride
    if not windows:
        raise SplitError("range_too_short_for_walk_forward")
    return tuple(windows)
