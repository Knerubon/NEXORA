"""Chronological train / validation / holdout splits and walk-forward windows.

Splits are defined by absolute UTC boundaries, never by shuffling. An optional purge gap
keeps a label horizon from straddling a boundary. The holdout is locked: reading it needs
an explicit unlock bound to the exact plan hash.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from nexora.artifacts import canonical_hash

Segment = Literal["train", "validation", "holdout"]


class SplitError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class HoldoutLocked(SplitError):
    def __init__(self) -> None:
        super().__init__("holdout_locked")


def _utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SplitError("timezone_required")


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
            _utc(value)
        if self.purge < timedelta(0):
            raise SplitError("negative_purge")
        if not (
            self.start < self.train_end
            and self.train_end + self.purge < self.validation_end
            and self.validation_end + self.purge < self.end
        ):
            raise SplitError("split_boundaries_not_increasing")

    @property
    def plan_hash(self) -> str:
        # timedelta is not canonically serializable; hash the purge as whole seconds.
        return canonical_hash(
            (
                self.start,
                self.train_end,
                self.validation_end,
                self.end,
                int(self.purge.total_seconds()),
            )
        )

    def bounds(self, segment: Segment) -> tuple[datetime, datetime]:
        if segment == "train":
            return self.start, self.train_end
        if segment == "validation":
            return self.train_end + self.purge, self.validation_end
        return self.validation_end + self.purge, self.end


@dataclass(frozen=True, slots=True)
class HoldoutUnlock:
    """Explicit, auditable permission to read the holdout for the plan with this hash."""

    plan_hash: str
    authorized_by: str
    reason: str


def select_segment[T](
    plan: SplitPlan,
    items: Sequence[T],
    times: Sequence[datetime],
    segment: Segment,
    *,
    unlock: HoldoutUnlock | None = None,
) -> tuple[T, ...]:
    """Items whose timestamp lies in the segment. `times` aligns with `items`."""
    if len(items) != len(times):
        raise SplitError("length_mismatch")
    if segment == "holdout" and (
        unlock is None
        or unlock.plan_hash != plan.plan_hash
        or not unlock.authorized_by.strip()
        or not unlock.reason.strip()
    ):
        raise HoldoutLocked
    low, high = plan.bounds(segment)
    for moment in times:
        _utc(moment)
    return tuple(item for item, moment in zip(items, times, strict=True) if low <= moment < high)


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def walk_forward_windows(
    start: datetime,
    end: datetime,
    *,
    train: timedelta,
    test: timedelta,
    step: timedelta | None = None,
    purge: timedelta = timedelta(0),
    anchored: bool = False,
) -> tuple[WalkForwardWindow, ...]:
    """Chronological windows. Each test window starts after its train window plus purge,
    and test windows never overlap one another. `end` must be the end of the *non-holdout*
    range; pass `plan.validation_end` so walk-forward cannot reach the holdout."""
    _utc(start)
    _utc(end)
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
