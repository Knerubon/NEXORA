"""ADR-026 Phase 2A: candle/algorithm/prediction identity contracts and golden vectors."""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from nexora.m30_bias import (
    AlgorithmIdentity,
    FeedIdentity,
    M30ConflictError,
    M30IdentityError,
    PolicySpec,
    candle_id,
    candle_id_bytes,
    feed_identity,
    outcome_id,
    prediction_id,
    resolve_write,
)

from tests.test_m30_bias_core import core, predictions, run, scenario

FEED = FeedIdentity(
    time_contract="legacy-adr019",
    feed_key="legacy:MT5-quote-observation:time-offset=10800:XAUUSD",
    instrument_key="XAUUSD",
    price_source="bid",
    units="USD",
)
BUCKET_START = datetime(2026, 1, 5, 10, 30, tzinfo=UTC)

GOLDEN_CANDLE_BYTES = (
    b'{"bucket_contract":"m30-bucket-v1","bucket_start":"2026-01-05T10:30:00.000000Z",'
    b'"duration_seconds":"1800","feed_key":"legacy:MT5-quote-observation:time-offset=10800:'
    b'XAUUSD","id_contract":"m30-candle-id-v1","instrument_key":"XAUUSD","price_source":"bid",'
    b'"time_contract":"legacy-adr019","timeframe":"M30","units":"USD"}'
)
GOLDEN_CANDLE_ID = "m30c1-dccf5ef7100711524ed6fedcdfdf40fd50c9cd9820d7769e51247e71f6e07097"

ALGORITHM = AlgorithmIdentity(
    algorithm_id="fixture-alg",
    algorithm_version="1",
    algorithm_params={"lookback": "3", "mode": {"a": "x"}},
    freeze_lead_seconds=0,
    threshold_policy=None,
    eligibility_policy="structural-v1",
    evidence_source="research:abc123",
)
GOLDEN_ALGORITHM_BYTES = (
    b'{"algorithm_id":"fixture-alg","algorithm_params":{"lookback":"3","mode":{"a":"x"}},'
    b'"algorithm_version":"1","eligibility_policy":"structural-v1","evidence_source":'
    b'"research:abc123","freeze_lead_seconds":"0","id_contract":"m30-algorithm-id-v1",'
    b'"policy_version":"m30-bias-v1","threshold_policy":"none"}'
)
GOLDEN_ALGORITHM_KEY = "m30a1-c8aa920f2d0f125c2812f9cacc2b22432b0028802b368e3331b8c2d6745e43a5"
GOLDEN_PREDICTION_ID = "m30p1-d4d350a9f6b2abc91d8f1af309d1dfa52bdfe1ee01161f08e4e50adb7c03b339"
GOLDEN_OUTCOME_ID = "m30o1-3e781adb9cf5fc1e42e6d9fec44f1c1229ee0ae20d2a37b505fbd0d9ee3430e6"


def test_candle_id_golden_vector() -> None:
    assert candle_id_bytes(FEED, BUCKET_START) == GOLDEN_CANDLE_BYTES
    # The digest is checked against an independent hash of the frozen literal bytes.
    assert GOLDEN_CANDLE_ID == "m30c1-" + hashlib.sha256(GOLDEN_CANDLE_BYTES).hexdigest()
    assert candle_id(FEED, BUCKET_START) == GOLDEN_CANDLE_ID


def test_algorithm_key_golden_vector() -> None:
    assert ALGORITHM.key_bytes() == GOLDEN_ALGORITHM_BYTES
    assert GOLDEN_ALGORITHM_KEY == "m30a1-" + hashlib.sha256(GOLDEN_ALGORITHM_BYTES).hexdigest()
    assert ALGORITHM.key() == GOLDEN_ALGORITHM_KEY


def test_prediction_and_outcome_id_golden_vectors() -> None:
    payload = (
        b'{"algorithm_key":"'
        + GOLDEN_ALGORITHM_KEY.encode()
        + b'","candle_id":"'
        + GOLDEN_CANDLE_ID.encode()
        + b'","id_contract":"m30-prediction-id-v1"}'
    )
    assert GOLDEN_PREDICTION_ID == "m30p1-" + hashlib.sha256(payload).hexdigest()
    assert prediction_id(GOLDEN_ALGORITHM_KEY, GOLDEN_CANDLE_ID) == GOLDEN_PREDICTION_ID
    assert outcome_id(GOLDEN_ALGORITHM_KEY, GOLDEN_CANDLE_ID) == GOLDEN_OUTCOME_ID
    assert GOLDEN_OUTCOME_ID.removeprefix("m30o1-") != GOLDEN_PREDICTION_ID.removeprefix("m30p1-")


def test_equal_instants_in_other_zones_give_same_candle_id() -> None:
    from zoneinfo import ZoneInfo

    for zone in ("Asia/Bangkok", "America/New_York", "Europe/London"):
        local = BUCKET_START.astimezone(ZoneInfo(zone))
        assert candle_id(FEED, local) == GOLDEN_CANDLE_ID


@pytest.mark.parametrize(
    "change",
    [
        {"time_contract": "recorded-utc-v1"},
        {"feed_key": "legacy:MT5-quote-observation:time-offset=7200:XAUUSD"},
        {"instrument_key": "XAUUSD.m"},
        {"price_source": "mid"},
        {"units": "USC"},
    ],
)
def test_candle_id_changes_with_any_identity_field(change: dict[str, str]) -> None:
    assert candle_id(dataclasses.replace(FEED, **change), BUCKET_START) != GOLDEN_CANDLE_ID


def test_candle_id_is_not_keyed_by_timestamp_alone() -> None:
    other_feed = dataclasses.replace(FEED, instrument_key="EURUSD", feed_key="legacy:x:EURUSD")
    assert candle_id(other_feed, BUCKET_START) != candle_id(FEED, BUCKET_START)
    later = datetime(2026, 1, 5, 11, 0, tzinfo=UTC)
    assert candle_id(FEED, later) != candle_id(FEED, BUCKET_START)


@pytest.mark.parametrize(
    "change",
    [
        {"algorithm_version": "2"},
        {"algorithm_id": "other-alg"},
        {"algorithm_params": {"lookback": "4", "mode": {"a": "x"}}},
        {"freeze_lead_seconds": 60},
        {"threshold_policy": PolicySpec("box-multiple", {"k": "1.5"})},
        {"evidence_source": "research:other"},
    ],
)
def test_same_candle_different_algorithm_or_config_never_collides(change: dict[str, Any]) -> None:
    other = dataclasses.replace(ALGORITHM, **change)
    assert other.key() != GOLDEN_ALGORITHM_KEY
    assert prediction_id(other.key(), GOLDEN_CANDLE_ID) != GOLDEN_PREDICTION_ID
    assert outcome_id(other.key(), GOLDEN_CANDLE_ID) != GOLDEN_OUTCOME_ID


def test_journal_uniqueness_rule() -> None:
    assert resolve_write(None, b"a", "m30_prediction_conflict") == "write"
    assert resolve_write(b"a", b"a", "m30_prediction_conflict") == "noop"
    with pytest.raises(M30ConflictError, match="m30_outcome_conflict"):
        resolve_write(b"a", b"b", "m30_outcome_conflict")


def test_core_records_carry_their_algorithm_key() -> None:
    first = predictions(run(scenario()))[0]
    assert first.algorithm_key == core().algorithm_key
    assert first.prediction_id == prediction_id(first.algorithm_key, first.candle_id)
    other = predictions(run(scenario(), delta=0, threshold=None))[0]
    assert other.prediction_id == first.prediction_id


@pytest.mark.parametrize("value", [True, -1, 1800, 1.0, "0", None])
def test_freeze_lead_must_be_explicit_valid_int(value: Any) -> None:
    with pytest.raises(M30IdentityError, match="invalid_freeze_lead_seconds"):
        dataclasses.replace(ALGORITHM, freeze_lead_seconds=value).key()


@pytest.mark.parametrize(
    "params", [{"k": 1}, {"k": 1.5}, {"k": ["a"]}, {1: "a"}, {"k": None}, {"k": True}]
)
def test_identity_params_accept_strings_and_objects_only(params: Any) -> None:
    with pytest.raises(M30IdentityError, match="invalid_identity_value"):
        dataclasses.replace(ALGORITHM, algorithm_params=params).key()


def test_only_structural_eligibility_is_supported() -> None:
    with pytest.raises(M30IdentityError, match="unsupported_eligibility_policy"):
        dataclasses.replace(ALGORITHM, eligibility_policy="min-samples-v1").key()


def _event(source: str, symbol: str = "XAUUSD") -> Any:
    return SimpleNamespace(source=source, symbol=symbol, price_source="bid", units="USD")


@pytest.mark.parametrize(
    ("contract", "source", "detail"),
    [
        ("legacy-adr019", "MT5-quote", "time_contract_mismatch"),
        ("legacy-adr019", "MT5-quote-observation:time-offset=", "time_contract_mismatch"),
        ("legacy-adr019", "MT5-quote-observation:time-offset=3h", "time_contract_mismatch"),
        ("recorded-utc-v1", "MT5-quote-observation:time-offset=0", "time_contract_mismatch"),
        ("adr025-binding-v1", "MT5-quote-observation:time-offset=0", "unsupported_time_contract"),
    ],
)
def test_identity_unavailable_without_matching_provenance(
    contract: str, source: str, detail: str
) -> None:
    with pytest.raises(M30IdentityError) as caught:
        feed_identity(contract, _event(source))
    assert (caught.value.code, caught.value.detail) == ("candle_identity_unavailable", detail)


def test_identity_unavailable_for_missing_symbol() -> None:
    with pytest.raises(M30IdentityError, match="missing_symbol"):
        feed_identity("legacy-adr019", _event("MT5-quote-observation:time-offset=0", ""))


def test_legacy_feed_key_uses_recorded_source_verbatim() -> None:
    feed = feed_identity("legacy-adr019", _event("MT5-quote-observation:time-offset=-3600"))
    assert feed.feed_key == "legacy:MT5-quote-observation:time-offset=-3600:XAUUSD"
    assert feed.instrument_key == "XAUUSD"
