"""Wave V9 / R-1: how long can a tail-truncated REVOCATION go undetected?

A hash chain proves that what is present is consistent; it cannot prove that nothing newer was
removed. Only an independently signed head pin proves "the store had at least N entries when the pin
authority looked". So a revocation appended AFTER the latest pin, then deleted from the tail, leaves
a chain that still verifies and a pin that still matches: TRUSTED until the pin becomes stale
(`custody:pin_stale`, bounded by the verifier's `max_pin_age`) or the pin authority pins a head that
includes the revocation (`custody:pin_ahead_of_store_truncated`).

These tests CHARACTERISE that window with exact instants. They do not claim, and the code does not
provide, immediate revocation detection. Mitigations that need operational policy (how fresh a pin
must be, who re-pins and when) are NOT invented here: THREAT-MODEL-V4.md records them as process
controls and an escalation. Synthetic fixtures and per-run keys only.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from nexora.market_history.custody import make_pin

from tests.test_market_history_r3_adversarial import _revoke
from tests.test_market_history_v4_custody import (
    CHECK,
    KEYS,
    MAX_PIN_AGE,
    Env,
    approve,
    fresh_pin,
    make_env,
    status,
)


def _drop_tail(e: Env, seq: int) -> None:
    """An actor with write access to the store directory deletes the newest entry (the chain
    remains a valid prefix, so only an independent head can notice)."""
    path = e.store.location / f"entry-{seq:08d}.json"
    path.chmod(0o644)
    path.unlink()


def _pin_over(e: Env, at: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    return make_pin(
        e.store.entries(), signer=KEYS["pia"], pinned_by="pia", captured_at=at,
        previous=previous, initial=previous is None,
    )  # fmt: skip


def _scenario(tmp: Path) -> tuple[Env, dict[str, Any]]:
    """approval (seq 1), pin P1 over seq 1 captured at CHECK-30min, THEN a revocation (seq 2)."""
    e = make_env(tmp)
    approve(e)
    p1 = fresh_pin(e.store, CHECK - timedelta(minutes=30))
    assert p1 is not None and p1["seq"] == 1
    _revoke(e)
    return e, p1


def test_R1_intact_store_the_revocation_is_honoured_even_with_an_older_pin(tmp_path: Path) -> None:
    e, p1 = _scenario(tmp_path)
    s = status(e, pin=p1)
    assert not s.trusted and "custody:revoked_at_seq_2" in s.reasons


def test_R1_truncated_revocation_newer_than_the_pin_is_NOT_detected_inside_the_window(
    tmp_path: Path,
) -> None:
    """THE RESIDUAL: store [approval, revocation], pin at seq 1, delete entry 2 -> TRUSTED."""
    e, p1 = _scenario(tmp_path)
    _drop_tail(e, 2)
    s = status(e, pin=p1)
    assert s.trusted, s.reasons  # characterisation of the residual risk, not an endorsement
    captured = CHECK - timedelta(minutes=30)
    # exact edge of the window: still trusted at captured + max_pin_age, stale one microsecond later
    assert status(e, pin=p1, now=captured + MAX_PIN_AGE).trusted
    late = status(e, pin=p1, now=captured + MAX_PIN_AGE + timedelta(microseconds=1))
    assert not late.trusted and "custody:pin_stale" in late.reasons


def test_R1_window_ends_when_the_pin_authority_pins_a_head_that_includes_the_revocation(
    tmp_path: Path,
) -> None:
    e, p1 = _scenario(tmp_path)
    p2 = _pin_over(e, CHECK - timedelta(minutes=5), previous=p1)  # pin authority reads seq 2
    assert p2["seq"] == 2
    _drop_tail(e, 2)
    s = status(e, pin=p2)
    assert not s.trusted and "custody:pin_ahead_of_store_truncated" in s.reasons
    # the older pin still passes, which is why verifiers must use the NEWEST pin they can get
    assert status(e, pin=p1).trusted


def test_R1_pin_created_after_the_revocation_closes_the_window_immediately(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    _revoke(e)
    p = fresh_pin(e.store, CHECK - timedelta(minutes=1))
    assert p is not None and p["seq"] == 2
    assert "custody:revoked_at_seq_2" in status(e, pin=p).reasons
    _drop_tail(e, 2)
    assert "custody:pin_ahead_of_store_truncated" in status(e, pin=p).reasons


def test_R1_latency_is_bounded_by_max_pin_age_for_every_choice_of_that_parameter(
    tmp_path: Path,
) -> None:
    """For ANY max_pin_age the truncated revocation is undetected for at most that long after the
    pin was captured: the latency is the verifier's own parameter, not a property of the code."""
    e, p1 = _scenario(tmp_path)
    _drop_tail(e, 2)
    captured = CHECK - timedelta(minutes=30)
    for age in (timedelta(minutes=31), timedelta(hours=1), timedelta(days=1), timedelta(days=30)):
        last_trusted = captured + age
        assert status(e, pin=p1, now=last_trusted, max_pin_age=age).trusted
        after = status(e, pin=p1, now=last_trusted + timedelta(seconds=1), max_pin_age=age)
        assert not after.trusted and "custody:pin_stale" in after.reasons


def test_R1_before_the_revocation_exists_there_is_nothing_to_detect(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    assert status(e).trusted  # positive control: approval + fresh pin is TRUSTED
