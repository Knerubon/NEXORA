"""Wave V9 / N-04: focused regressions that kill the valid mutants the V8 review found surviving
against the PR #96 suite at 1c536d5 (the author's own 33-mutant script reported 33/33 killed; these
mutants lay outside what it exercised, or were masked by an either-or assertion).

    D3   gap `>` vs `>=` at equality        quality.py  QualityAccumulator.add
    C25  key `valid_from` ignored           custody.py  PrincipalRegistry.authorize
    T8   chunk contiguity unchecked         extract.py  _verify_in: chunk_not_contiguous
    C21  approved before dataset retrieved  custody.py  check_approval_entry
    T9   `orders_submitted` unchecked       extract.py  _verify_in: provenance_safety_claims
    C12  reapproval need not acknowledge    custody.py  _lineage_problem
    OPM  candidate/operator mismatch        custody.py  operator_problem
    X2-X5 (extra, found by the independent mutator): pin age equality, approval/pin clock skew
          bounds, approval dated after the pin capture

Each test pins the CURRENT, documented behaviour (a boundary is stated explicitly where the code
chose one) and asserts the specific reason code, so an either-or assertion cannot mask a regression.
No threshold or semantics is changed here (gap size remains Quant decision MD-6).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history.custody import (
    MAX_SKEW,
    CustodianRegistry,
    check_approval_entry,
    make_candidate,
    make_pin,
    pin_reasons,
)
from nexora.market_history.extract import verify_dataset
from nexora.market_history.models import HistoryError, NormalizedTick
from nexora.market_history.quality import QualityAccumulator, QualityConfig

from tests.test_market_history import FakeSource, mk
from tests.test_market_history_corrective import REQ, go, reseal
from tests.test_market_history_r3_adversarial import _reasons, _revoke
from tests.test_market_history_v3 import NOW
from tests.test_market_history_v4_custody import (
    CHECK,
    KEYS,
    LATER,
    approve,
    body_for,
    fresh_candidate,
    make_env,
    principal,
    registry_json,
    status,
)


def _tick(utc_msc: int) -> NormalizedTick:
    return NormalizedTick(utc_msc, utc_msc, Decimal("1.00"), Decimal("1.10"), Decimal("0.10"))


# ------------------------------------------------------------------------------ D3: gap bound ---
@pytest.mark.parametrize(
    ("delta", "gaps"),
    [(999, 0), (1000, 0), (1001, 1)],  # a gap is STRICTLY greater than max_gap_ms
)
def test_D3_gap_is_strictly_greater_than_max_gap_ms(delta: int, gaps: int) -> None:
    acc = QualityAccumulator(QualityConfig(max_gap_ms=1000))
    for t in (10_000, 10_000 + delta):
        acc.add(_tick(t))
    report = acc.report()
    assert report["gap_count"] == gaps
    assert report["gaps_utc_msc"] == ([[10_000, 10_000 + delta]] if gaps else [])


def test_D3_the_same_bound_holds_for_the_legacy_quality_version() -> None:
    from nexora.market_history.quality import LEGACY_QUALITY_VERSION

    acc = QualityAccumulator(QualityConfig(1000), version=LEGACY_QUALITY_VERSION)
    for t in (0, 1000, 2001):
        acc.add(_tick(t))
    assert acc.report()["gap_count"] == 1  # only 1000 -> 2001


# ---------------------------------------------------------------------- C25: key validity window
def _registry_with_window(**window: str) -> CustodianRegistry:
    base = registry_json()
    base["principals"][1] = principal("carol", ["custodian"], **window)
    return CustodianRegistry.from_json(base)


def test_C25_key_not_yet_valid_at_signing_time_is_rejected() -> None:
    reg = _registry_with_window(valid_from=(LATER + timedelta(hours=1)).isoformat())
    carol = KEYS["carol"].key_id
    principal_, key, why = reg.authorize(carol, "custodian", "carol", (LATER, CHECK))
    assert (principal_, key, why) == (None, None, "key_not_valid_at_time")


def test_C25_key_not_yet_valid_at_verification_time_is_rejected() -> None:
    reg = _registry_with_window(valid_from=(CHECK + timedelta(hours=1)).isoformat())
    _p, key, why = reg.authorize(KEYS["carol"].key_id, "custodian", "carol", (LATER, CHECK))
    assert key is None and why == "key_not_valid_at_time"


def test_C25_window_bounds_are_inclusive() -> None:
    carol = KEYS["carol"].key_id
    reg = _registry_with_window(valid_from=LATER.isoformat(), valid_until=CHECK.isoformat())
    for instants in ((LATER, CHECK), (LATER, LATER), (CHECK, CHECK)):
        assert reg.authorize(carol, "custodian", "carol", instants)[2] == "", instants
    before, after = LATER - timedelta(microseconds=1), CHECK + timedelta(microseconds=1)
    assert reg.authorize(carol, "custodian", "carol", (before, CHECK))[2] == "key_not_valid_at_time"
    assert reg.authorize(carol, "custodian", "carol", (LATER, after))[2] == "key_not_valid_at_time"


def test_C25_end_to_end_approval_signed_before_the_key_became_valid_is_rejected(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    e.registry = _registry_with_window(valid_from=(LATER + timedelta(hours=1)).isoformat())
    with pytest.raises(HistoryError, match="approver_key_not_valid_at_time"):
        approve(e)  # LATER is before valid_from
    e.store.append(body_for(e, "carol"))  # a raw append bypasses the tool; the verifier decides
    assert "custody:approval_1_rejected:approver_key_not_valid_at_time" in _reasons(e)
    # positive control: the same registry accepts an approval made once the key is valid
    approve(e, at=LATER + timedelta(hours=2))
    assert status(e).trusted


# ----------------------------------------------------------------------- T8: chunk contiguity ---
def _two_gap_free_chunks(tmp: Path) -> Path:
    """Three chunks [1000,3000) [3000,5000) [5000,6000) with NO tick in 3000-3499, so a rename of
    the middle chunk's lower bound changes nothing but contiguity."""
    ticks = [mk(t) for t in (1000, 1500, 2000, 2500, 3500, 4000, 4500, 5500)]
    go(tmp / "ds", FakeSource(ticks), REQ)
    return tmp / "ds"


@pytest.mark.parametrize("new_from", [3200, 2800])  # a hole before the chunk / an overlap
def test_T8_non_contiguous_chunks_are_reported(tmp_path: Path, new_from: int) -> None:
    ds = _two_gap_free_chunks(tmp_path)
    assert verify_dataset(ds) == []  # positive control
    old = ds / "chunk-3000-5000.jsonl"
    new_name = f"chunk-{new_from}-5000.jsonl"
    old.rename(ds / new_name)

    def edit(m: dict[str, Any]) -> None:
        (c,) = [c for c in m["chunks"] if c["file"] == old.name]
        c["file"], c["from_raw_msc"] = new_name, new_from

    reseal(ds, edit)
    assert verify_dataset(ds) == [f"chunk_not_contiguous:{new_name}"]


def test_T8_a_hole_at_the_window_edges_is_reported(tmp_path: Path) -> None:
    ds = _two_gap_free_chunks(tmp_path)
    first = ds / "chunk-1000-3000.jsonl"
    first.rename(ds / "chunk-900-3000.jsonl")  # starts BEFORE the window
    reseal(ds, lambda m: m["chunks"][0].update(file="chunk-900-3000.jsonl", from_raw_msc=900))
    assert verify_dataset(ds) == ["chunk_not_contiguous:chunk-900-3000.jsonl"]


# --------------------------------------------------------- C21: approved before dataset retrieved
def _candidate_created_before_retrieval(e: Any) -> None:
    """Operator attestation dated a day BEFORE the dataset's retrieved_at (NOW), so only the
    approval-vs-retrieval check can object to an approval timed in between."""
    e.candidate = make_candidate(e.manifest, signer=KEYS["ann"], created_at=NOW - timedelta(days=1))


def test_C21_approval_dated_before_the_dataset_was_retrieved_is_rejected(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    _candidate_created_before_retrieval(e)
    e.store.append(body_for(e, "carol", approved_at=(NOW - timedelta(hours=12)).isoformat()))
    assert "custody:approval_1_rejected:approved_before_dataset_retrieved" in _reasons(e)
    with pytest.raises(HistoryError, match="approved_before_dataset_retrieved"):
        approve(e, at=NOW - timedelta(hours=12))
    assert not (tmp_path / "store" / "entry-00000002.json").exists()


def test_C21_approval_at_exactly_retrieval_time_or_later_is_accepted(tmp_path: Path) -> None:
    for i, at in enumerate((NOW, NOW + timedelta(seconds=1), LATER)):
        e = make_env(tmp_path / str(i))
        _candidate_created_before_retrieval(e)
        approve(e, at=at)
        assert status(e).trusted, at


def test_C21_unreadable_retrieval_time_fails_closed(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    reseal(e.ds, lambda m: m["provenance"].update(retrieved_at="not a time"))
    s = status(e)
    assert not s.trusted


# --------------------------------------------------------------------- T9: provenance safety clai
@pytest.mark.parametrize("orders", [1, -1, 2**31, True, 0.0, "0", None, [], {}])
def test_T9_orders_submitted_must_be_exactly_integer_zero(tmp_path: Path, orders: Any) -> None:
    go(tmp_path / "ds")
    assert verify_dataset(tmp_path / "ds") == []  # positive control: the genuine 0 verifies
    reseal(tmp_path / "ds", lambda m: m["provenance"].update(orders_submitted=orders))
    assert "provenance_safety_claims_invalid" in verify_dataset(tmp_path / "ds")


def test_T9_missing_orders_submitted_is_refused(tmp_path: Path) -> None:
    go(tmp_path / "ds")
    reseal(tmp_path / "ds", lambda m: m["provenance"].pop("orders_submitted"))
    assert "provenance_safety_claims_invalid" in verify_dataset(tmp_path / "ds")


@pytest.mark.parametrize(
    "edit",
    [
        lambda m: m["provenance"].update(read_only_source_allowlist=False),
        lambda m: m["provenance"].update(read_only_source_allowlist="yes"),
        lambda m: m.update(immutable=False),
        lambda m: m.pop("immutable"),
    ],
)
def test_T9_the_other_safety_claims_are_enforced_too(tmp_path: Path, edit: Any) -> None:
    go(tmp_path / "ds")
    reseal(tmp_path / "ds", edit)
    assert "provenance_safety_claims_invalid" in verify_dataset(tmp_path / "ds")


# ------------------------------------------------- C12: reapproval must acknowledge a revocation
def test_C12_reapproval_that_omits_the_revocation_is_rejected_for_that_reason(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    approve(e)
    rv = _revoke(e)
    fresh_candidate(e)  # a FRESH attestation, so `candidate_reused` cannot mask the lineage check
    later = (LATER + timedelta(hours=2)).isoformat()
    e.store.append(body_for(e, "carol", approved_at=later, acknowledged_revocations=[]))
    assert "custody:approval_3_rejected:revocation_not_acknowledged" in _reasons(e)
    # positive control: the same re-approval WITH the acknowledgement restores trust
    fresh_candidate(e)
    e.store.append(
        body_for(
            e, "carol", approved_at=later, approval_id="56" * 16,
            acknowledged_revocations=[rv["revocation_id"]],
        )
    )  # fmt: skip
    assert status(e).trusted


def test_C12_the_tool_acknowledges_automatically_and_the_old_candidate_is_refused(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    approve(e)
    rv = _revoke(e)
    fresh_candidate(e)
    entry = approve(e, at=LATER + timedelta(hours=2))
    assert entry["acknowledged_revocations"] == [rv["revocation_id"]]
    assert status(e).trusted


# ----------------------------------------------- OPM: candidate operator vs recorded operator ---
def test_OPM_candidate_naming_a_different_operator_cannot_launder_self_approval(
    tmp_path: Path,
) -> None:
    """erin holds BOTH roles and is the operator recorded in the manifest. She signs the candidate
    with her own key but writes "ann" in its operator field, then approves as custodian. Without
    the recorded-operator check the independence test compares the approver with the forged name
    ("ann") and passes; the attestation must name exactly the operator the manifest records."""
    e = make_env(tmp_path, operator="erin")
    forged_manifest = {**e.manifest, "provenance": {**e.manifest["provenance"], "operator": "ann"}}
    e.candidate = make_candidate(forged_manifest, signer=KEYS["erin"], created_at=NOW)
    assert e.candidate["operator"] == "ann" and e.manifest["provenance"]["operator"] == "erin"
    with pytest.raises(HistoryError, match="candidate_operator_mismatch"):
        approve(e, "erin")
    e.store.append(body_for(e, "erin"))  # raw append, as an attacker with append access would
    assert "custody:approval_1_rejected:candidate_operator_mismatch" in _reasons(e)
    # positive control: erin's own honest attestation is still refused (self-approval), carol's
    # approval of an honest attestation by erin succeeds
    e.candidate = make_candidate(e.manifest, signer=KEYS["erin"], created_at=NOW)
    with pytest.raises(HistoryError, match="self_approval_by_operator"):
        approve(e, "erin")
    approve(e, "carol")
    assert status(e).trusted


# --------------------------- X3-X5: clock-skew and pin-capture boundaries (extra mutants) ---
def test_X3_approval_future_skew_is_exactly_max_skew(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    now = CHECK
    ok = body_for(e, "carol", approved_at=(now + MAX_SKEW).isoformat())
    late = body_for(
        e, "carol", approved_at=(now + MAX_SKEW + timedelta(microseconds=1)).isoformat()
    )
    assert check_approval_entry(ok, e.manifest, e.registry, now=now) == ""
    why = check_approval_entry(late, e.manifest, e.registry, now=now)
    assert why == "approved_in_the_future"


def test_X4_pin_future_skew_is_exactly_max_skew(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    entries = e.store.entries()

    def reasons(captured: Any) -> list[str]:
        pin = make_pin(
            entries, signer=KEYS["pia"], pinned_by="pia", captured_at=captured, initial=True
        )
        return pin_reasons(pin, entries, e.registry, now=CHECK, max_pin_age=timedelta(days=60))[0]

    assert reasons(CHECK + MAX_SKEW) == []
    assert reasons(CHECK + MAX_SKEW + timedelta(microseconds=1)) == ["custody:pin_in_the_future"]


def test_X5_approval_dated_after_the_pin_capture_does_not_count(tmp_path: Path) -> None:
    """The entry sits at a sequence the pin covers, but its signed date is later than the pin's
    independent capture time (plus skew): the pin cannot vouch for it (F4)."""
    e = make_env(tmp_path)
    approve(e, at=LATER)
    wide = timedelta(days=60)  # keep the pin fresh enough that coverage, not staleness, decides
    for offset in (MAX_SKEW, MAX_SKEW - timedelta(microseconds=1), timedelta(0)):
        pin = make_pin(
            e.store.entries(), signer=KEYS["pia"], pinned_by="pia", captured_at=LATER - offset,
            initial=True,
        )  # fmt: skip
        assert status(e, pin=pin, max_pin_age=wide).trusted, offset  # inclusive at the bound
    early = make_pin(
        e.store.entries(), signer=KEYS["pia"], pinned_by="pia",
        captured_at=LATER - MAX_SKEW - timedelta(microseconds=1), initial=True,
    )  # fmt: skip
    s = status(e, pin=early, max_pin_age=wide)
    assert not s.trusted and "custody:approval_1_rejected:approved_after_pin_capture" in s.reasons
