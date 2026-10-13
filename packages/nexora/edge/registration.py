"""Split pre-registration record, independent of any one dataset manifest.

`build_edge_manifest(..., split_plan=...)` binds a plan to ONE manifest. That alone cannot stop
someone from building a second manifest over the same (or overlapping) bars with no plan, or
with a different plan, and reading the original holdout as `unsplit` or `validation`. The
registry closes that gap: it records, per market stream (`source`, `symbol`), which plan governs
which time range, and `run_exit_mode` checks every run against it.

Rules (enforced in `run_exit_mode`, see `governing_registration`):
* Streams are compared by `stream_key`: NFKC, case-folded, with every non-alphanumeric
  character removed, so "Synthetic-Test", "synthetic-test " and "synthetic_test" are one
  stream. A name that is not ASCII after NFKC (look-alike dashes or letters) is refused.
* A dataset whose time range overlaps a registered plan's `[start, end)` for the same stream
  must be run with exactly that plan; it can never be run unsplit or under another plan.
* Once a stream has any registration, a dataset of that stream outside every registered range
  is refused (`stream_registered_dataset_outside_registered_ranges`): the same bars with their
  timestamps moved cannot escape the registry by no longer overlapping it.
* A stream with no registration at all can only be run unsplit (and is labelled so).
* Registrations for the same stream may not overlap in time, and a dataset may not span two.

Limits (not hidden): the registry is still caller-supplied data. It protects the holdout only
when it is loaded from an independently reviewed record (a committed JSON file whose
`registry_hash` is pinned next to the expected manifest hash), when a range is registered
before any run touches it, and when the labels are honest: a genuinely different `source` or
`symbol` (e.g. "feed2") is a different stream. See ADR-037 (Proposed).
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from nexora.artifacts import canonical_hash
from nexora.edge.splits import SplitError, SplitPlan

_MICROSECOND = timedelta(microseconds=1)
REGISTRY_SCHEMA = "nexora.edge.split-registry/v1"


class RegistrationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _name_key(name: object) -> str:
    if type(name) is not str:
        raise RegistrationError("invalid_stream_name")
    folded = unicodedata.normalize("NFKC", name).casefold()
    if not folded.isascii():
        # Look-alike characters (U+2010 hyphen, Cyrillic letters) would otherwise make a
        # second, unregistered stream that reads the same as the registered one.
        raise RegistrationError("non_ascii_stream_name")
    key = "".join(ch for ch in folded if ch.isalnum())
    if not key:
        raise RegistrationError("invalid_stream_name")
    return key


def stream_key(source: object, symbol: object) -> tuple[str, str]:
    """Normalised identity of a market stream; near-identical spellings collapse to one key."""
    return (_name_key(source), _name_key(symbol))


@dataclass(frozen=True, slots=True)
class SplitRegistration:
    """A plan fixed for one market stream before any evaluation. `registered_by` and `reason`
    are declarations for the reviewer, not credentials."""

    source: str
    symbol: str
    plan: SplitPlan
    registered_by: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.plan, SplitPlan):
            raise RegistrationError("invalid_split_plan")
        for value in (self.source, self.symbol, self.registered_by, self.reason):
            if type(value) is not str or not value.strip():
                raise RegistrationError("invalid_registration_field")
        stream_key(self.source, self.symbol)  # refuse names that cannot be normalised

    @property
    def stream(self) -> tuple[str, str]:
        return stream_key(self.source, self.symbol)

    def overlaps(self, low: datetime, high: datetime) -> bool:
        """True when the closed range [low, high] intersects the plan's [start, end)."""
        return low < self.plan.end and self.plan.start <= high


@dataclass(frozen=True, slots=True)
class SplitRegistry:
    entries: tuple[SplitRegistration, ...] = ()

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple:
            raise RegistrationError("invalid_registry")
        for index, entry in enumerate(self.entries):
            if not isinstance(entry, SplitRegistration):
                raise RegistrationError("invalid_registry")
            for other in self.entries[:index]:
                if other.stream == entry.stream and other.overlaps(
                    entry.plan.start, entry.plan.end - _MICROSECOND
                ):
                    raise RegistrationError("overlapping_registrations")

    @property
    def registry_hash(self) -> str:
        """Order-independent identity of the registry; pin it in the independent record."""
        return canonical_hash(sorted(_hash_payload(e) for e in self.entries))

    def register(self, entry: SplitRegistration) -> SplitRegistry:
        """Return a new registry with `entry` added. An overlapping registration for the same
        stream is refused, so a plan can never be replaced after the fact."""
        return SplitRegistry((*self.entries, entry))

    def governing_registration(
        self, source: str, symbol: str, event_times: Sequence[datetime]
    ) -> SplitRegistration | None:
        """The registration that governs a dataset with these event times, or None when the
        stream has no registration at all. Raises when the stream is registered but the
        dataset lies outside every registered range, or spans more than one."""
        if not event_times:
            raise RegistrationError("empty_dataset")
        key = stream_key(source, symbol)
        low, high = min(event_times), max(event_times)
        same_stream = [e for e in self.entries if e.stream == key]
        found = [e for e in same_stream if e.overlaps(low, high)]
        if len(found) > 1:
            raise RegistrationError("dataset_spans_multiple_registrations")
        if not found and same_stream:
            raise RegistrationError("stream_registered_dataset_outside_registered_ranges")
        return found[0] if found else None

    def to_json(self) -> str:
        """Canonical JSON for a committed, reviewable record."""
        payload = {
            "schema": REGISTRY_SCHEMA,
            "entries": sorted((_json_entry(e) for e in self.entries), key=_entry_sort_key),
        }
        return json.dumps(payload, sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> SplitRegistry:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RegistrationError("invalid_registry_json") from exc
        if not isinstance(payload, dict) or payload.get("schema") != REGISTRY_SCHEMA:
            raise RegistrationError("unsupported_registry_schema")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise RegistrationError("invalid_registry_json")
        return cls(tuple(_from_json_entry(item) for item in entries))


def _hash_payload(entry: SplitRegistration) -> tuple[str, str, str, str, str]:
    return (entry.source, entry.symbol, entry.plan.plan_hash, entry.registered_by, entry.reason)


def _json_entry(entry: SplitRegistration) -> dict[str, Any]:
    plan = entry.plan
    return {
        "source": entry.source,
        "symbol": entry.symbol,
        "registered_by": entry.registered_by,
        "reason": entry.reason,
        "plan_hash": plan.plan_hash,
        "plan": {
            "start": plan.start.isoformat(),
            "train_end": plan.train_end.isoformat(),
            "validation_end": plan.validation_end.isoformat(),
            "end": plan.end.isoformat(),
            "purge_us": plan.purge // _MICROSECOND,
        },
    }


def _entry_sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (item["source"], item["symbol"], item["plan"]["start"])


def _from_json_entry(item: object) -> SplitRegistration:
    try:
        assert isinstance(item, dict)
        raw = item["plan"]
        purge_us = raw["purge_us"]
        if type(purge_us) is not int:
            raise RegistrationError("invalid_registry_json")
        plan = SplitPlan(
            datetime.fromisoformat(raw["start"]),
            datetime.fromisoformat(raw["train_end"]),
            datetime.fromisoformat(raw["validation_end"]),
            datetime.fromisoformat(raw["end"]),
            timedelta(microseconds=purge_us),
        )
        entry = SplitRegistration(
            item["source"], item["symbol"], plan, item["registered_by"], item["reason"]
        )
    except (AssertionError, KeyError, TypeError, ValueError, SplitError) as exc:
        if isinstance(exc, RegistrationError):
            raise
        raise RegistrationError("invalid_registry_json") from exc
    # The stored hash is a cross-check: an edited boundary cannot keep the old hash.
    if item.get("plan_hash") != plan.plan_hash:
        raise RegistrationError("registry_plan_hash_mismatch")
    return entry
