"""ADR-026 M30 Bias contracts: record models, identities and canonical serialization.

Pure: no I/O, no wall clock, no environment. Identity serialization is defined here
explicitly and does not depend on ``nexora.artifacts.canonical_serialize``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

POLICY_VERSION = "m30-bias-v1"
BUCKET_CONTRACT = "m30-bucket-v1"
CANDLE_ID_CONTRACT = "m30-candle-id-v1"
ALGORITHM_ID_CONTRACT = "m30-algorithm-id-v1"
PREDICTION_ID_CONTRACT = "m30-prediction-id-v1"
OUTCOME_ID_CONTRACT = "m30-outcome-id-v1"
TIMEFRAME = "M30"
DURATION_SECONDS = 1800
BUCKET = timedelta(seconds=DURATION_SECONDS)

TIME_CONTRACT_LEGACY_ADR019 = "legacy-adr019"
TIME_CONTRACT_RECORDED_UTC = "recorded-utc-v1"
# ADR-025 binding time contracts are defined by ADR-025, which is not available yet.
SUPPORTED_TIME_CONTRACTS = frozenset({TIME_CONTRACT_LEGACY_ADR019, TIME_CONTRACT_RECORDED_UTC})
LEGACY_MT5_SOURCE_PREFIX = "MT5-quote-observation:time-offset="

ELIGIBILITY_STRUCTURAL = "structural-v1"
THRESHOLD_POLICY_NONE = "none"

M30BiasValue = Literal["UP", "DOWN", "NO_EDGE", "UNAVAILABLE"]
M30BiasRecordStatus = Literal["FROZEN", "SKIPPED"]
M30BiasOutcomeStatus = Literal["EVALUATED", "NO_DATA"]
ThresholdLabel = Literal["UP", "DOWN", "FLAT"]
FirstTouch = Literal["UP", "DOWN", "NONE", "AMBIGUOUS"]

BIAS_VALUES: frozenset[str] = frozenset({"UP", "DOWN", "NO_EDGE", "UNAVAILABLE"})


class M30IdentityError(ValueError):
    """A candle/algorithm identity cannot be built; no record may be keyed."""

    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}:{detail}")


class M30ConflictError(ValueError):
    """Recomputed content differs from stored content under the same key (N5)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# --- canonical serialization -------------------------------------------------------


def format_utc(value: datetime) -> str:
    """Exact ADR-026 timestamp format: ``YYYY-MM-DDTHH:MM:SS.ffffffZ``."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_required")
    moment = value.astimezone(UTC)
    return (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}T"
        f"{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}."
        f"{moment.microsecond:06d}Z"
    )


def format_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("non_finite_decimal")
    if value == 0:
        return "0"
    encoded = format(value, "f")
    if "." in encoded:
        encoded = encoded.rstrip("0").rstrip(".")
    return encoded


def canonical_json_bytes(value: Any) -> bytes:
    """Sorted keys, compact separators, ASCII-escaped, UTF-8, no trailing newline."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity_value(value: Any) -> Any:
    """Identity inputs: strings or nested string-keyed objects only (no numbers/lists)."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise M30IdentityError("invalid_identity_value", "non_string_key")
            result[key] = _identity_value(item)
        return result
    raise M30IdentityError("invalid_identity_value", type(value).__name__)


def content_value(value: Any) -> Any:
    """JSON-ready content encoding for hashed record content."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: content_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): content_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [content_value(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, Decimal):
        return format_decimal(value)
    if isinstance(value, datetime):
        return format_utc(value)
    if isinstance(value, float):
        # Floats may only arrive inside committed JSON payloads; json repr is deterministic.
        return value
    raise TypeError(f"unsupported_content_type:{type(value).__name__}")


def record_bytes(record: M30BiasPrediction | M30BiasOutcome) -> bytes:
    """Deterministic hashed-content bytes of a prediction or outcome record."""
    return canonical_json_bytes(content_value(record))


def resolve_write(existing: bytes | None, recomputed: bytes, conflict_code: str) -> str:
    """Write-once rule (N5): absent -> "write"; identical -> "noop"; different -> conflict."""
    if existing is None:
        return "write"
    if existing == recomputed:
        return "noop"
    raise M30ConflictError(conflict_code)


# --- identities --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeedIdentity:
    time_contract: str
    feed_key: str
    instrument_key: str
    price_source: str
    units: str


def feed_identity(time_contract: str, event: Any) -> FeedIdentity:
    """ADR-026 5A feed identity from recorded provenance only; never computes offsets."""
    if time_contract not in SUPPORTED_TIME_CONTRACTS:
        raise M30IdentityError("candle_identity_unavailable", "unsupported_time_contract")
    source, symbol = event.source, event.symbol
    price_source, units = event.price_source, event.units
    for name, item in (
        ("source", source),
        ("symbol", symbol),
        ("price_source", price_source),
        ("units", units),
    ):
        if not isinstance(item, str) or not item:
            raise M30IdentityError("candle_identity_unavailable", f"missing_{name}")
    is_mt5_quote = source.startswith(LEGACY_MT5_SOURCE_PREFIX)
    if time_contract == TIME_CONTRACT_LEGACY_ADR019:
        offset = source[len(LEGACY_MT5_SOURCE_PREFIX) :]
        digits = offset[1:] if offset.startswith("-") else offset
        if not is_mt5_quote or not digits.isascii() or not digits.isdigit():
            raise M30IdentityError("candle_identity_unavailable", "time_contract_mismatch")
    elif is_mt5_quote:
        raise M30IdentityError("candle_identity_unavailable", "time_contract_mismatch")
    return FeedIdentity(
        time_contract=time_contract,
        feed_key=f"legacy:{source}:{symbol}",
        instrument_key=symbol,
        price_source=price_source,
        units=units,
    )


def candle_identity_fields(feed: FeedIdentity, bucket_start: datetime) -> dict[str, str]:
    return {
        "id_contract": CANDLE_ID_CONTRACT,
        "bucket_contract": BUCKET_CONTRACT,
        "timeframe": TIMEFRAME,
        "duration_seconds": str(DURATION_SECONDS),
        "time_contract": feed.time_contract,
        "feed_key": feed.feed_key,
        "instrument_key": feed.instrument_key,
        "price_source": feed.price_source,
        "units": feed.units,
        "bucket_start": format_utc(bucket_start),
    }


def candle_id_bytes(feed: FeedIdentity, bucket_start: datetime) -> bytes:
    return canonical_json_bytes(candle_identity_fields(feed, bucket_start))


def candle_id(feed: FeedIdentity, bucket_start: datetime) -> str:
    return "m30c1-" + sha256_hex(candle_id_bytes(feed, bucket_start))


@dataclass(frozen=True, slots=True)
class PolicySpec:
    """An explicitly supplied policy id with fully resolved string parameters."""

    policy_id: str
    params: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AlgorithmIdentity:
    algorithm_id: str
    algorithm_version: str
    algorithm_params: Mapping[str, Any]
    freeze_lead_seconds: int
    threshold_policy: PolicySpec | None  # None = Q-M3 undecided -> "none"
    eligibility_policy: str
    evidence_source: str
    policy_version: str = POLICY_VERSION

    def fields(self) -> dict[str, Any]:
        for name in ("algorithm_id", "algorithm_version", "evidence_source", "policy_version"):
            if not getattr(self, name):
                raise M30IdentityError("algorithm_identity_invalid", f"missing_{name}")
        if self.eligibility_policy != ELIGIBILITY_STRUCTURAL:
            # Q-M8 thresholds are undecided; only the frozen structural rule exists.
            raise M30IdentityError("algorithm_identity_invalid", "unsupported_eligibility_policy")
        validate_freeze_lead(self.freeze_lead_seconds)
        threshold: Any = THRESHOLD_POLICY_NONE
        if self.threshold_policy is not None:
            if not self.threshold_policy.policy_id:
                raise M30IdentityError("algorithm_identity_invalid", "missing_threshold_policy_id")
            threshold = {
                "policy_id": self.threshold_policy.policy_id,
                "params": _identity_value(self.threshold_policy.params),
            }
        return {
            "id_contract": ALGORITHM_ID_CONTRACT,
            "policy_version": self.policy_version,
            "algorithm_id": self.algorithm_id,
            "algorithm_version": self.algorithm_version,
            "algorithm_params": _identity_value(self.algorithm_params),
            "freeze_lead_seconds": str(self.freeze_lead_seconds),
            "threshold_policy": threshold,
            "eligibility_policy": self.eligibility_policy,
            "evidence_source": self.evidence_source,
        }

    def key_bytes(self) -> bytes:
        return canonical_json_bytes(self.fields())

    def key(self) -> str:
        return "m30a1-" + sha256_hex(self.key_bytes())


def validate_freeze_lead(value: Any) -> int:
    """Δ is a Quant decision (Q-M4): it must be supplied explicitly; no default exists."""
    if type(value) is not int or not 0 <= value < DURATION_SECONDS:
        raise M30IdentityError("algorithm_identity_invalid", "invalid_freeze_lead_seconds")
    return value


def _pair_id(prefix: str, contract: str, algorithm_key: str, candle: str) -> str:
    payload = {"id_contract": contract, "algorithm_key": algorithm_key, "candle_id": candle}
    return prefix + sha256_hex(canonical_json_bytes(payload))


def prediction_id(algorithm_key: str, candle: str) -> str:
    return _pair_id("m30p1-", PREDICTION_ID_CONTRACT, algorithm_key, candle)


def outcome_id(algorithm_key: str, candle: str) -> str:
    return _pair_id("m30o1-", OUTCOME_ID_CONTRACT, algorithm_key, candle)


# --- records -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class M30BiasEvidence:
    component: str
    code: str
    polarity: int
    value: str | None
    as_of_event_time: datetime
    as_of_received_at: datetime
    source_version: str


@dataclass(frozen=True, slots=True)
class M30BiasPrediction:
    """Hashed content: deterministic fields only (no wall clock, mode or lifecycle)."""

    schema_version: Literal[1]
    policy_version: str
    prediction_id: str
    candle_id: str
    algorithm_key: str
    source: str
    symbol: str
    price_source: str
    units: str
    target_start: datetime
    target_end: datetime
    freeze_lead_seconds: int
    cutoff_time: datetime
    prediction_time: datetime
    snapshot_event_time: datetime
    snapshot_received_at: datetime
    snapshot_event_identity: str
    freeze_event_identity: str
    freeze_after_target_open: bool
    freeze_lag_seconds: Decimal
    status: M30BiasRecordStatus
    bias: M30BiasValue
    reason_codes: tuple[str, ...]
    reference_price: Decimal | None
    threshold: Decimal | None
    evidence: tuple[M30BiasEvidence, ...]
    input_provenance: Mapping[str, str | None]


@dataclass(frozen=True, slots=True)
class M30BiasOutcome:
    """Hashed content: deterministic fields only."""

    schema_version: Literal[1]
    policy_version: str
    outcome_id: str
    prediction_id: str
    candle_id: str
    algorithm_key: str
    status: M30BiasOutcomeStatus
    evaluation_time: datetime
    closing_event_identity: str
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    sample_count: int
    first_sample_time: datetime | None
    last_sample_time: datetime | None
    max_sample_gap_seconds: Decimal | None
    gap_or_incomplete_samples: int
    close_return: Decimal | None
    body: Decimal | None
    pre_target_drift: Decimal | None
    label_threshold: ThresholdLabel | None
    first_touch: FirstTouch | None
    mfe: Decimal | None
    mae: Decimal | None
    mfe_time: datetime | None
    mae_time: datetime | None
    up_excursion: Decimal | None
    down_excursion: Decimal | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class M30IdentityUnavailable:
    """No record can be keyed; the feature reports health ``unavailable`` (5A)."""

    reason_codes: tuple[str, ...]
    event_identity: str
