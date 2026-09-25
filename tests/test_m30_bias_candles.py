"""ADR-026 Phase 2A: UTC M30 bucketing and sampled candle construction."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from nexora.m30_bias import Sample, bucket_end, bucket_start, build_candle

from tests.test_m30_bias_core import ev

B_K = datetime(2026, 1, 5, 10, 30, tzinfo=UTC)


def test_event_at_b_k_belongs_to_bucket_k() -> None:
    assert bucket_start(B_K) == B_K
    assert bucket_end(B_K) == B_K + timedelta(minutes=30)


def test_event_one_microsecond_before_b_k_belongs_to_previous_bucket() -> None:
    assert bucket_start(B_K - timedelta(microseconds=1)) == B_K - timedelta(minutes=30)
    assert bucket_start(B_K + timedelta(minutes=30) - timedelta(microseconds=1)) == B_K


def test_buckets_are_utc_aligned_regardless_of_input_offset() -> None:
    bangkok = datetime(2026, 1, 5, 17, 45, tzinfo=ZoneInfo("Asia/Bangkok"))
    assert bucket_start(bangkok) == B_K
    assert bucket_start(bangkok).tzinfo == UTC
    india = datetime(2026, 1, 5, 16, 5, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert bucket_start(india) == B_K  # 10:35Z
    # DST transition day in New York: still plain UTC arithmetic.
    dst = datetime(2026, 3, 8, 3, 15, tzinfo=ZoneInfo("America/New_York"))
    assert bucket_start(dst) == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone_required"):
        bucket_start(datetime(2026, 1, 5, 10, 30))


def test_sampled_candle_ohlc_and_quality() -> None:
    samples = (
        Sample.from_event(ev(B_K, "100.50", "a"), "unknown"),
        Sample.from_event(ev(B_K + timedelta(minutes=5), "99.20", "b", is_gap=True), "complete"),
        Sample.from_event(ev(B_K + timedelta(minutes=20), "101.30", "c"), "partial"),
        Sample.from_event(ev(B_K + timedelta(minutes=29), "100.80", "d"), "complete"),
    )
    candle = build_candle(samples)
    assert (candle.open, candle.high, candle.low, candle.close) == (
        Decimal("100.50"),
        Decimal("101.30"),
        Decimal("99.20"),
        Decimal("100.80"),
    )
    assert candle.sample_count == 4
    assert candle.max_sample_gap_seconds == Decimal(15 * 60)
    assert candle.gap_or_incomplete_samples == 2
    assert (candle.first_event_identity, candle.last_event_identity) == ("a", "d")
    single = build_candle(samples[:1])
    assert single.max_sample_gap_seconds is None


def test_candle_rejects_mixed_buckets() -> None:
    with pytest.raises(ValueError, match="mixed_bucket_samples"):
        build_candle(
            (
                Sample.from_event(ev(B_K - timedelta(microseconds=1), "1", "a"), "unknown"),
                Sample.from_event(ev(B_K, "1", "b"), "unknown"),
            )
        )


_HOST_TZ_PROBE = """
from tests.test_m30_bias_core import as_bytes, run, scenario
import hashlib, time
print(time.strftime('%z'), hashlib.sha256(b''.join(as_bytes(run(scenario())))).hexdigest())
"""


def test_host_timezone_does_not_change_records() -> None:
    root = Path(__file__).resolve().parent.parent
    digests = set()
    offsets = set()
    for zone in ("UTC", "Asia/Bangkok", "EST5EDT", "PST8PDT"):
        env = {**os.environ, "TZ": zone, "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(
            [sys.executable, "-c", _HOST_TZ_PROBE],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        offset, digest = result.stdout.split()
        offsets.add(offset)
        digests.add(digest)
    assert len(offsets) > 1, "host timezone override had no effect; test would be vacuous"
    assert len(digests) == 1
