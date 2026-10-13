"""Decision Audit hardening round 1 (ADR-036): adversarial and regression tests.

Reproduces the three MAJOR findings of the independent review and the security items:

* F1 - QualityVerdict forgery (forged / mismatched / reused / hash-less verdicts)
* F3 - BUY/SELL without a genuinely emitted, valid signal must never be eligible
* P1 - payload allowlist, stream collisions, replay != authorization, price/OHLC boundary,
       documentation drift
* EVALUATION_BLOCKED - isolated, additive, never eligible, never a decision

(F2, invalid Guard configuration, lives in ``test_data_quality_guard_hardening.py``.)
"""

from __future__ import annotations

import ast
import copy
import inspect
from collections.abc import Iterator
from dataclasses import fields, make_dataclass, replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import nexora.decision_audit as audit_pkg
import nexora.decision_audit.blocked as blocked_module
import pytest
from nexora.artifacts import canonical_serialize
from nexora.data_quality import (
    DataQualityGuard,
    MarketDataSnapshot,
    QualityExpectation,
    QualityGuardConfig,
    QualityVerdict,
)
from nexora.decision_audit import (
    EVALUATION_BLOCKED_KIND,
    AppendOutcome,
    AuditBlocker,
    AuditError,
    AuditGateOutcome,
    AuditVersions,
    DataQualityRef,
    DecisionAuditGate,
    DecisionAuditRecord,
    DecisionAuditStore,
    EvaluationBlockedOutcome,
    EvaluationBlockedRecord,
    EvaluationBlockedRecorder,
    EvaluationBlockedStore,
    EvidenceRef,
    InstrumentIdentity,
    MarketDataReference,
    build_decision_audit_record,
    build_evaluation_blocked_record,
)
from nexora.decision_audit import identity as ident
from nexora.decision_audit import payload_policy as policy
from nexora.decision_audit.invariants import check_record_invariants, verify_quality_binding
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research import ResearchPipeline
from nexora.storage import SQLiteJournal

from tests.validation_fixtures import pipeline_config, tick, tick_events

CONFIG = QualityGuardConfig(
    version="dq-guard-hardening-v1",
    max_quote_age_seconds=60,
    max_future_skew_seconds=5,
    max_latency_ms=3000,
    max_gap_seconds=120,
    min_events=1,
    require_bid_ask=True,
    allow_zero_spread=False,
    max_spread=D("1"),
    max_spread_to_price=None,
)
EXPECTED = QualityExpectation("synthetic-test", "XAUUSD", "USD/oz")
WAIT_INDEX, BUY_INDEX, SELL_INDEX = 0, 7, 12  # first of each action in the fixture
ENV = "development"


def _guard() -> DataQualityGuard:
    return DataQualityGuard(CONFIG, EXPECTED)


def _snap(*events: NormalizedPriceEvent) -> MarketDataSnapshot:
    return MarketDataSnapshot(tuple(events))


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[SQLiteJournal]:
    store = SQLiteJournal(tmp_path / "hardening.sqlite")
    yield store
    store.close()


def _gate(journal: Any, environment: str = ENV) -> DecisionAuditGate:
    return DecisionAuditGate(DecisionAuditStore(journal, environment=environment), _guard())


def _step(index: int) -> tuple[NormalizedPriceEvent, dict[str, Any]]:
    pipeline = ResearchPipeline(pipeline_config())
    events = tick_events(index + 1)
    output: dict[str, Any] = {}
    for event in events:
        output = pipeline.process(event)
    return events[-1], output


def _decide(
    gate: DecisionAuditGate,
    event: NormalizedPriceEvent,
    output: dict[str, Any],
    **kwargs: Any,
) -> Any:
    kwargs.setdefault("snapshot", _snap(event))
    kwargs.setdefault("evaluated_at", event.received_at)
    return gate.record_decision(output=output, event=event, **kwargs)


def _record(index: int = BUY_INDEX) -> DecisionAuditRecord:
    event, output = _step(index)
    record = build_decision_audit_record(
        output=output,
        event=event,
        snapshot=_snap(event),
        guard=_guard(),
        evaluated_at=event.received_at,
        environment=ENV,
    )
    return record


# =============================================================================================
# F1 - QualityVerdict forgery
# =============================================================================================


def test_no_public_surface_accepts_a_caller_supplied_verdict() -> None:
    for fn in (
        DecisionAuditGate.record_decision,
        build_decision_audit_record,
        build_evaluation_blocked_record,
        EvaluationBlockedRecorder.record_blocked,
    ):
        names = set(inspect.signature(fn).parameters)
        assert not {"quality", "verdict", "quality_verdict"} & names, fn
        assert {"snapshot", "evaluated_at"} <= names, fn


def test_passing_a_verdict_to_the_gate_is_a_type_error(journal: SQLiteJournal) -> None:
    event, output = _step(BUY_INDEX)
    verdict = _guard().evaluate(_snap(event), evaluated_at=event.received_at)
    with pytest.raises(TypeError):
        _gate(journal).record_decision(  # type: ignore[call-arg]
            output=output,
            event=event,
            snapshot=_snap(event),
            evaluated_at=event.received_at,
            quality=verdict,
        )


def test_gate_requires_a_real_store_and_a_real_guard(journal: SQLiteJournal) -> None:
    store = DecisionAuditStore(journal, environment=ENV)
    duck = SimpleNamespace(evaluate=lambda *a, **k: None, config_version="x", expectation=None)
    with pytest.raises(TypeError, match="invalid_guard"):
        DecisionAuditGate(store, duck)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="invalid_store"):
        DecisionAuditGate(journal, _guard())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        DecisionAuditGate(store)  # type: ignore[call-arg]


def _good(index: int = BUY_INDEX) -> tuple[Any, Any, Any, QualityVerdict]:
    event, _ = _step(index)
    snapshot = _snap(event)
    guard = _guard()
    verdict = guard.evaluate(snapshot, evaluated_at=event.received_at)
    assert verdict.state == "ok"
    return event, snapshot, guard, verdict


def _verify(verdict: Any, event: Any, snapshot: Any, guard: Any, at: Any = ...) -> QualityVerdict:
    at = event.received_at if at is ... else at
    return verify_quality_binding(
        quality=verdict, snapshot=snapshot, event=event, guard=guard, evaluated_at=at
    )


def test_a_genuine_verdict_verifies() -> None:
    event, snapshot, guard, verdict = _good()
    assert _verify(verdict, event, snapshot, guard) == verdict


def test_reused_verdict_for_another_event_is_rejected() -> None:
    event_a, snapshot_a, guard, verdict_a = _good(BUY_INDEX)
    event_b = tick_events(BUY_INDEX + 5)[-1]  # a different, perfectly clean event
    snapshot_b = _snap(event_b)
    assert guard.evaluate(snapshot_b, evaluated_at=event_b.received_at).state == "ok"
    with pytest.raises(AuditError) as caught:
        _verify(verdict_a, event_b, snapshot_b, guard, event_b.received_at)
    assert caught.value.code == "quality_event_mismatch"


def test_mismatched_event_keys_are_rejected() -> None:
    event, snapshot, guard, verdict = _good()
    forged = replace(verdict, event_keys=("t:999",))  # self-consistent, wrong event
    with pytest.raises(AuditError) as caught:
        _verify(forged, event, snapshot, guard)
    assert caught.value.code == "quality_event_mismatch"
    forged = replace(verdict, event_keys=(*verdict.event_keys, "t:1000"))
    with pytest.raises(AuditError) as caught:
        _verify(forged, event, snapshot, guard)
    assert caught.value.code == "quality_event_mismatch"


def test_missing_snapshot_hash_on_an_ok_verdict_is_rejected() -> None:
    event, snapshot, guard, verdict = _good()
    object.__setattr__(verdict, "snapshot_hash", None)  # bypass the frozen dataclass
    with pytest.raises(AuditError) as caught:
        _verify(verdict, event, snapshot, guard)
    assert caught.value.code == "quality_invalid"


def test_wrong_snapshot_hash_is_rejected() -> None:
    event, snapshot, guard, verdict = _good()
    forged = replace(verdict, snapshot_hash="0" * 64)
    with pytest.raises(AuditError) as caught:
        _verify(forged, event, snapshot, guard)
    assert caught.value.code == "quality_snapshot_mismatch"


def test_hash_of_a_clean_snapshot_cannot_vouch_for_a_tampered_one() -> None:
    event, _, guard, clean_verdict = _good()
    tampered = replace(event, bid=D("101"), ask=D("100"))  # same identity key, crossed book
    with pytest.raises(AuditError) as caught:
        _verify(clean_verdict, tampered, _snap(tampered), guard)
    assert caught.value.code in {"quality_event_mismatch", "quality_snapshot_mismatch"}


def test_self_consistent_forged_ok_verdict_is_not_reproducible() -> None:
    event, _ = _step(BUY_INDEX)
    bad = replace(event, bid=D("101"), ask=D("100"))  # crossed book -> the Guard says blocked
    guard = _guard()
    real = guard.evaluate(_snap(bad), evaluated_at=bad.received_at)
    assert real.state == "blocked"
    forged = replace(real, state="ok", new_trade_permitted=True, findings=())  # constructible!
    assert forged.new_trade_permitted is True  # the value itself cannot prove provenance...
    with pytest.raises(AuditError) as caught:  # ...so verification re-derives it via the Guard
        _verify(forged, bad, _snap(bad), guard, bad.received_at)
    assert caught.value.code == "quality_not_reproducible"


def test_flipping_the_permission_bit_after_construction_is_rejected() -> None:
    event, _ = _step(BUY_INDEX)
    bad = replace(event, bid=D("101"), ask=D("100"))
    guard = _guard()
    verdict = guard.evaluate(_snap(bad), evaluated_at=bad.received_at)
    object.__setattr__(verdict, "new_trade_permitted", True)
    with pytest.raises(AuditError) as caught:
        _verify(verdict, bad, _snap(bad), guard, bad.received_at)
    assert caught.value.code == "quality_invalid"


def test_wrong_evaluation_time_and_config_version_are_rejected() -> None:
    event, snapshot, guard, verdict = _good()
    with pytest.raises(AuditError) as caught:
        _verify(
            replace(verdict, evaluated_at=event.received_at + timedelta(seconds=1)),
            event,
            snapshot,
            guard,
        )
    assert caught.value.code == "quality_evaluation_time_mismatch"
    with pytest.raises(AuditError) as caught:
        _verify(replace(verdict, config_version="another-config-v9"), event, snapshot, guard)
    assert caught.value.code == "quality_config_mismatch"
    with pytest.raises(AuditError) as caught:  # an unsynchronized-clock request cannot reuse "ok"
        _verify(verdict, event, snapshot, guard, None)
    assert caught.value.code == "quality_evaluation_time_mismatch"


def test_snapshot_must_end_in_the_evaluated_event() -> None:
    events = tick_events(BUY_INDEX + 3)[-3:]
    guard = _guard()
    snapshot = _snap(*events)
    verdict = guard.evaluate(snapshot, evaluated_at=events[-1].received_at)
    assert verdict.state == "ok"
    with pytest.raises(AuditError) as caught:  # decision is about the first, not the latest
        _verify(verdict, events[0], snapshot, guard, events[-1].received_at)
    assert caught.value.code == "quality_event_mismatch"
    assert _verify(verdict, events[-1], snapshot, guard, events[-1].received_at) == verdict


@pytest.mark.parametrize(
    "fake",
    [
        None,
        {"state": "ok", "new_trade_permitted": True},
        SimpleNamespace(state="ok", new_trade_permitted=True, snapshot_hash="0" * 64),
        "ok",
    ],
)
def test_non_verdict_objects_are_never_proof(fake: Any) -> None:
    event, snapshot, guard, _ = _good()
    with pytest.raises(AuditError) as caught:
        _verify(fake, event, snapshot, guard)
    assert caught.value.code == "quality_invalid"


def test_binding_requires_a_real_guard_event_and_snapshot() -> None:
    event, snapshot, guard, verdict = _good()
    duck = SimpleNamespace(config_version=verdict.config_version)
    with pytest.raises(AuditError) as caught:
        _verify(verdict, event, snapshot, duck)
    assert caught.value.code == "invalid_guard"
    for bad_event, bad_snapshot in ((object(), snapshot), (event, (event,))):
        with pytest.raises(AuditError) as caught:
            _verify(verdict, bad_event, bad_snapshot, guard, verdict.evaluated_at)
        assert caught.value.code == "invalid_quality_input"


def test_gate_denies_when_the_guard_hands_back_a_reused_verdict(
    journal: SQLiteJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    event, output = _step(BUY_INDEX)
    foreign = tick(999, "100")
    reused = _guard().evaluate(_snap(foreign), evaluated_at=foreign.received_at)
    monkeypatch.setattr(
        DataQualityGuard, "evaluate", lambda self, snapshot, *, evaluated_at: reused
    )
    result = _decide(_gate(journal), event, output)
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code == "quality_event_mismatch"
    assert result.new_trade_eligible is False and result.record is None
    assert DecisionAuditStore(journal, environment=ENV).read("XAUUSD") == ()


def test_gate_denies_when_the_guard_hands_back_a_verdict_for_a_clean_twin_snapshot(
    journal: SQLiteJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    event, output = _step(BUY_INDEX)
    twin = _guard().evaluate(_snap(event), evaluated_at=event.received_at)  # ok, clean data
    crossed = replace(event, bid=D("101"), ask=D("100"))  # same identity key, bad data
    monkeypatch.setattr(DataQualityGuard, "evaluate", lambda self, snapshot, *, evaluated_at: twin)
    result = _decide(_gate(journal), crossed, output)
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code in {"quality_event_mismatch", "quality_snapshot_mismatch"}
    assert result.new_trade_eligible is False


def test_gate_denies_when_the_guard_raises(
    journal: SQLiteJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: Any, snapshot: Any, *, evaluated_at: Any) -> None:
        raise RuntimeError("password=hunter2")

    monkeypatch.setattr(DataQualityGuard, "evaluate", boom)
    event, output = _step(BUY_INDEX)
    result = _decide(_gate(journal), event, output)
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code == "quality_evaluation_failed"
    assert "hunter2" not in repr(result)


@pytest.mark.parametrize(
    ("snapshot_for", "code"),
    [
        (lambda e: (e,), "invalid_quality_input"),  # raw tuple, not a snapshot
        (lambda e: MarketDataSnapshot(()), "quality_event_mismatch"),  # empty evidence
        (lambda e: _snap(tick(999, "100")), "quality_event_mismatch"),  # evidence for another event
        (lambda e: _snap(e, tick(999, "100")), "quality_event_mismatch"),  # event not the latest
        (lambda e: None, "invalid_quality_input"),
    ],
)
def test_gate_denies_missing_or_mismatched_quality_evidence(
    journal: SQLiteJournal, snapshot_for: Any, code: str
) -> None:
    event, output = _step(BUY_INDEX)
    result = _decide(_gate(journal), event, output, snapshot=snapshot_for(event))
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code == code
    assert result.new_trade_eligible is False


def test_multi_event_history_snapshot_is_accepted_and_hash_bound(journal: SQLiteJournal) -> None:
    events = tick_events(BUY_INDEX + 1)[-4:]
    _, output = _step(BUY_INDEX)
    result = _decide(
        _gate(journal),
        events[-1],
        output,
        snapshot=_snap(*events),
        evaluated_at=events[-1].received_at,
    )
    assert result.outcome is AuditGateOutcome.RECORDED_ELIGIBLE
    record = result.record
    assert (
        record is not None and record.market_data.snapshot_hash == record.data_quality.snapshot_hash
    )
    assert record.data_quality.snapshot_hash is not None


def test_out_of_order_history_blocks_eligibility(journal: SQLiteJournal) -> None:
    events = tick_events(BUY_INDEX + 1)[-3:]
    _, output = _step(BUY_INDEX)
    shuffled = (events[1], events[0], events[2])
    result = _decide(
        _gate(journal),
        events[2],
        output,
        snapshot=_snap(*shuffled),
        evaluated_at=events[2].received_at,
    )
    assert result.outcome is AuditGateOutcome.RECORDED_DENIED_DATA_QUALITY
    assert result.new_trade_eligible is False


# ---- hand-built records cannot claim eligibility they have not earned ---------------------------

_FORGERIES: list[tuple[str, Any, str]] = [
    (
        "eligible without a signal",
        lambda r: replace(r, signal_id=None, signal_emitted=False),
        "eligibility_inconsistent",
    ),
    ("id without emission flag", lambda r: replace(r, signal_emitted=False), "signal_inconsistent"),
    ("emission flag without id", lambda r: replace(r, signal_id=None), "signal_inconsistent"),
    (
        "permitted while blocked",
        lambda r: replace(r, data_quality=replace(r.data_quality, state="blocked")),
        "quality_inconsistent",
    ),
    (
        "unknown state",
        lambda r: replace(r, data_quality=replace(r.data_quality, state="fine")),
        "quality_inconsistent",
    ),
    (
        "eligible with findings",
        lambda r: replace(r, data_quality=replace(r.data_quality, finding_codes=("stale_quote",))),
        "eligibility_inconsistent",
    ),
    (
        "eligible without a verified hash",
        lambda r: replace(
            r,
            data_quality=replace(r.data_quality, snapshot_hash=None),
            market_data=replace(r.market_data, snapshot_hash=None),
        ),
        "eligibility_inconsistent",
    ),
    (
        "hash disagreement",
        lambda r: replace(r, market_data=replace(r.market_data, snapshot_hash="0" * 64)),
        "quality_snapshot_mismatch",
    ),
    (
        "eligible without evaluation time",
        lambda r: replace(r, data_quality=replace(r.data_quality, evaluated_at=None)),
        "eligibility_inconsistent",
    ),
    (
        "eligible with a not-emitted gap",
        lambda r: replace(r, evidence_gaps=(*r.evidence_gaps, "signal:not_emitted")),
        "eligibility_inconsistent",
    ),
    (
        "actionable but not_applicable",
        lambda r: replace(r, trade_eligibility="not_applicable"),
        "eligibility_inconsistent",
    ),
    (
        "unknown eligibility value",
        lambda r: replace(r, trade_eligibility="authorized"),
        "eligibility_inconsistent",
    ),
    ("invented action", lambda r: replace(r, action="HOLD"), "invalid_action"),
    ("boolean score", lambda r: replace(r, score=True), "audit_redaction_violation"),
]


@pytest.mark.parametrize(("label", "forge", "code"), _FORGERIES, ids=[f[0] for f in _FORGERIES])
def test_store_rejects_a_forged_record(
    journal: SQLiteJournal, label: str, forge: Any, code: str
) -> None:
    store = DecisionAuditStore(journal, environment=ENV)
    forged = forge(_record(BUY_INDEX))
    with pytest.raises(AuditError) as caught:
        store.append(forged)
    assert caught.value.code == code, label
    assert store.read("XAUUSD") == ()


def test_wait_records_can_never_carry_a_signal_or_eligibility(journal: SQLiteJournal) -> None:
    wait = _record(WAIT_INDEX)
    assert wait.action == "WAIT" and wait.signal_id is None
    store = DecisionAuditStore(journal, environment=ENV)
    for forged in (
        replace(wait, signal_id="XAUUSD:1:long", signal_emitted=True),
        replace(wait, trade_eligibility="eligible_for_downstream_gates"),
        replace(wait, signal_emitted=True),
    ):
        with pytest.raises(AuditError):
            store.append(forged)
    check_record_invariants(wait)  # the genuine one is fine


def test_store_rejects_non_records_and_foreign_types(journal: SQLiteJournal) -> None:
    store = DecisionAuditStore(journal, environment=ENV)
    for bad in (None, {"action": "BUY"}, SimpleNamespace(), object()):
        with pytest.raises(AuditError) as caught:
            store.append(bad)  # type: ignore[arg-type]
        assert caught.value.code == "invalid_record"


# =============================================================================================
# F3 - BUY/SELL without a genuinely emitted, valid signal
# =============================================================================================


def _mutated(index: int, mutate: Any) -> tuple[NormalizedPriceEvent, dict[str, Any]]:
    event, output = _step(index)
    broken = copy.deepcopy(output)
    mutate(broken)
    return event, broken


def _earlier_signal(before: int) -> dict[str, Any]:
    """The ``latest`` signal a duplicate-suppressing engine would still be exposing."""
    _, output = _step(before)
    return copy.deepcopy(output["signals"]["latest"])


def test_baseline_buy_and_sell_are_eligible_with_a_real_signal(journal: SQLiteJournal) -> None:
    for index, side in ((BUY_INDEX, "long"), (SELL_INDEX, "short")):
        event, output = _step(index)
        result = _decide(_gate(journal), event, output)
        record = result.record
        assert result.outcome is AuditGateOutcome.RECORDED_ELIGIBLE and result.new_trade_eligible
        assert record is not None and record.signal_emitted and record.signal_id
        assert record.signal_id == output["signals"]["latest"]["signal_id"]
        assert output["signals"]["latest"]["side"] == side


def test_suppressed_duplicate_signal_is_not_eligible(journal: SQLiteJournal) -> None:
    # The engine's duplicate suppression returns without appending, so ``latest`` is still the
    # previous signal while the decision is BUY/SELL again. Reproduce exactly that state.
    stale_latest = _earlier_signal(BUY_INDEX)
    event, output = _mutated(
        BUY_INDEX + 2,
        lambda o: o["signals"].update(latest=copy.deepcopy(stale_latest)),
    )
    assert output["signals"]["decision"]["action"] != "WAIT"
    result = _decide(_gate(journal), event, output)
    assert result.outcome is AuditGateOutcome.RECORDED_DENIED_NO_SIGNAL
    assert result.new_trade_eligible is False
    record = result.record
    assert record is not None
    assert record.signal_id is None and record.signal_emitted is False
    assert record.trade_eligibility == "denied_no_emitted_signal"
    assert "signal:not_emitted" in record.evidence_gaps
    assert stale_latest["signal_id"] not in repr(record)  # the old id is never borrowed


@pytest.mark.parametrize(
    ("label", "mutate", "gap"),
    [
        ("latest absent", lambda o: o["signals"].update(latest=None), "signal:absent"),
        ("latest not a mapping", lambda o: o["signals"].update(latest="x"), "signal:absent"),
        (
            "stale decision time",
            lambda o: o["signals"]["latest"].update(decision_time="2000-01-01T00:00:00+00:00"),
            "signal:not_emitted",
        ),
        (
            "naive decision time",
            lambda o: o["signals"]["latest"].update(decision_time="2026-03-02T09:02:40.150000"),
            "signal:not_emitted",
        ),
        (
            "garbage decision time",
            lambda o: o["signals"]["latest"].update(decision_time="not-a-time"),
            "signal:not_emitted",
        ),
        (
            "missing decision time",
            lambda o: o["signals"]["latest"].pop("decision_time"),
            "signal:not_emitted",
        ),
    ],
)
@pytest.mark.parametrize("index", [BUY_INDEX, SELL_INDEX])
def test_actionable_decision_without_an_emitted_signal_is_denied(
    journal: SQLiteJournal, index: int, label: str, mutate: Any, gap: str
) -> None:
    event, output = _mutated(index, mutate)
    result = _decide(_gate(journal), event, output)
    assert result.outcome is AuditGateOutcome.RECORDED_DENIED_NO_SIGNAL, label
    assert result.new_trade_eligible is False
    record = result.record
    assert record is not None and record.action in ("BUY", "SELL")
    assert record.signal_id is None and record.signal_emitted is False
    assert gap in record.evidence_gaps
    # The engine's own decision is audited unchanged; only eligibility is withheld.
    assert record.score == output["signals"]["decision"]["score"]


def _set(path: tuple[str, ...], value: Any) -> Any:
    def apply(output: dict[str, Any]) -> None:
        node = output
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return apply


_INCONSISTENT = [
    ("signal_id", _set(("signals", "latest", "signal_id"), None)),
    ("signal_id", _set(("signals", "latest", "signal_id"), "")),
    ("signal_id", _set(("signals", "latest", "signal_id"), 12345)),
    ("signal_id", _set(("signals", "latest", "signal_id"), "x" * 129)),
    ("symbol", _set(("signals", "latest", "symbol"), "EURUSD")),
    ("sequence", _set(("signals", "latest", "sequence"), 9999)),
    ("sequence", _set(("signals", "latest", "sequence"), "8")),
    ("side", _set(("signals", "latest", "side"), "short")),
    ("status", _set(("signals", "latest", "status"), "expired")),
    ("action", _set(("signals", "latest", "decision", "action"), "SELL")),
    ("evidence", _set(("signals", "latest", "reason_codes"), ["forged_code"])),
    ("evidence", _set(("signals", "latest", "reason_codes"), [])),
]


@pytest.mark.parametrize(("name", "mutate"), _INCONSISTENT)
def test_inconsistent_signal_identity_is_denied_not_authorized(
    journal: SQLiteJournal, name: str, mutate: Any
) -> None:
    event, output = _mutated(BUY_INDEX, mutate)
    result = _decide(_gate(journal), event, output)
    assert result.outcome is AuditGateOutcome.RECORDED_DENIED_INCONSISTENT_OUTPUT
    assert result.new_trade_eligible is False
    record = result.record
    assert record is not None
    assert record.signal_id is None and record.signal_emitted is False  # nothing is vouched for
    assert record.trade_eligibility == "denied_inconsistent_output"
    assert f"signal:inconsistent:{name}" in record.evidence_gaps


def test_actionable_decision_without_any_positive_evidence_is_denied(
    journal: SQLiteJournal,
) -> None:
    def strip(output: dict[str, Any]) -> None:
        output["signals"]["decision"]["positive_evidence"] = []
        output["signals"]["latest"]["reason_codes"] = []

    event, output = _mutated(BUY_INDEX, strip)
    result = _decide(_gate(journal), event, output)
    assert result.outcome is AuditGateOutcome.RECORDED_DENIED_INCONSISTENT_OUTPUT
    assert "signal:inconsistent:evidence_empty" in result.record.evidence_gaps


def test_wait_never_gets_a_signal_id_even_if_latest_is_present(journal: SQLiteJournal) -> None:
    event, output = _step(WAIT_INDEX)
    injected = copy.deepcopy(_earlier_signal(BUY_INDEX))
    injected["decision_time"] = event.received_at.isoformat()  # even decided "now"
    output = copy.deepcopy(output)
    output["signals"]["latest"] = injected
    result = _decide(_gate(journal), event, output)
    record = result.record
    assert result.outcome is AuditGateOutcome.RECORDED_NOT_APPLICABLE
    assert record is not None and record.action == "WAIT"
    assert record.signal_id is None and record.signal_emitted is False
    assert record.trade_eligibility == "not_applicable" and result.new_trade_eligible is False


@pytest.mark.parametrize("bad", [True, False, "7", 7.0, None, [7], {"v": 7}])
@pytest.mark.parametrize("index", [WAIT_INDEX, BUY_INDEX])
def test_missing_or_non_integer_score_is_never_fabricated(
    journal: SQLiteJournal, index: int, bad: Any
) -> None:
    event, output = _mutated(index, _set(("signals", "decision", "score"), bad))
    result = _decide(_gate(journal), event, output)
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code == "invalid_score" and result.record is None


def test_absent_score_is_not_defaulted_to_zero(journal: SQLiteJournal) -> None:
    event, output = _mutated(BUY_INDEX, lambda o: o["signals"]["decision"].pop("score"))
    result = _decide(_gate(journal), event, output)
    assert result.failure_code == "invalid_score"
    assert DecisionAuditStore(journal, environment=ENV).read("XAUUSD") == ()


def test_full_run_never_authorizes_without_a_real_unique_signal(journal: SQLiteJournal) -> None:
    gate = _gate(journal)
    pipeline = ResearchPipeline(pipeline_config())
    results = []
    final: dict[str, Any] = {}
    for event in tick_events(300):
        final = pipeline.process(event)
        results.append(_decide(gate, event, final))
    records = [r.record for r in results]
    assert all(r is not None for r in records)
    actionable = [r for r in records if r.action != "WAIT"]
    eligible = [r for r in records if r.trade_eligibility == "eligible_for_downstream_gates"]
    assert eligible and len(eligible) == len(actionable)  # fixture: every actionable one emitted
    ids = [r.signal_id for r in eligible]
    assert all(i for i in ids) and len(set(ids)) == len(ids)  # unique, real ids
    history_ids = {s["signal_id"] for s in final["signals"]["history"]}
    assert set(ids) <= history_ids  # every authorization maps to a signal the engine emitted
    for result in results:
        assert result.new_trade_eligible == (result.outcome is AuditGateOutcome.RECORDED_ELIGIBLE)
        if result.new_trade_eligible:
            assert result.record.signal_emitted


# =============================================================================================
# P1-3 - duplicate / replay behavior
# =============================================================================================


def test_replay_is_idempotent_but_never_a_new_authorization(journal: SQLiteJournal) -> None:
    event, output = _step(BUY_INDEX)
    store = DecisionAuditStore(journal, environment=ENV)
    gate = DecisionAuditGate(store, _guard())
    first = _decide(gate, event, output)
    again = _decide(gate, event, output)
    restarted = _decide(_gate(journal), event, output)  # e.g. crash-recovery re-run

    assert first.outcome is AuditGateOutcome.RECORDED_ELIGIBLE and first.new_trade_eligible
    for replay in (again, restarted):
        assert replay.outcome is AuditGateOutcome.RECORDED_REPLAY
        assert replay.new_trade_eligible is False
        assert replay.record == first.record  # same audit evidence, no fresh authority
        assert replay.failure_code is None
    assert len(store.read("XAUUSD")) == 1  # idempotent: nothing duplicated


def test_whole_run_replay_authorizes_nothing(journal: SQLiteJournal) -> None:
    def run() -> list[Any]:
        gate, pipeline = _gate(journal), ResearchPipeline(pipeline_config())
        return [_decide(gate, e, pipeline.process(e)) for e in tick_events(60)]

    first, second = run(), run()
    assert sum(r.new_trade_eligible for r in first) > 0
    assert sum(r.new_trade_eligible for r in second) == 0
    assert all(r.outcome is AuditGateOutcome.RECORDED_REPLAY for r in second)


def test_same_decision_with_different_quality_evidence_cannot_authorize_twice(
    journal: SQLiteJournal,
) -> None:
    event, output = _step(BUY_INDEX)
    gate = _gate(journal)
    assert _decide(gate, event, output).new_trade_eligible is True
    later = _decide(gate, event, output, evaluated_at=event.received_at + timedelta(seconds=1))
    assert later.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert later.failure_code == "audit_identity_conflict"
    assert (
        later.new_trade_eligible is False
        and len(DecisionAuditStore(journal, environment=ENV).read("XAUUSD")) == 1
    )


def test_append_reports_created_then_replayed(journal: SQLiteJournal) -> None:
    store = DecisionAuditStore(journal, environment=ENV)
    record = _record(BUY_INDEX)
    assert store.append(record) is AppendOutcome.CREATED
    assert store.append(record) is AppendOutcome.REPLAYED


# =============================================================================================
# P1-1 - structured allowlist, not just regex redaction
# =============================================================================================

_SENSITIVE_INPUTS = [
    ("top level", _set(("api_token",), "s3cr3tvalue")),
    ("decision", _set(("signals", "decision", "db_password"), "s3cr3tvalue")),
    ("latest", _set(("signals", "latest", "authorization"), "s3cr3tvalue")),
    ("readiness", _set(("entry_readiness", "credentials"), "s3cr3tvalue")),
    ("regime", _set(("regime", "state", "private_key"), "s3cr3tvalue")),
    ("structure", _set(("structure", "secret_note"), "s3cr3tvalue")),
    ("trendline", _set(("trendline", "session_id"), "s3cr3tvalue")),
    (
        "nested list item",
        lambda o: o["signals"]["decision"]["positive_evidence"][0].update(dsn="s3cr3tvalue"),
    ),
    ("deep history skip bypass", _set(("signals", "decision", "history"), [{"token": "s3cr3t"}])),
]


@pytest.mark.parametrize(
    ("label", "mutate"), _SENSITIVE_INPUTS, ids=[s[0] for s in _SENSITIVE_INPUTS]
)
def test_unexpected_sensitive_fields_are_rejected_not_ignored(
    journal: SQLiteJournal, label: str, mutate: Any
) -> None:
    event, output = _mutated(BUY_INDEX, mutate)
    result = _decide(_gate(journal), event, output)
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE, label
    assert result.failure_code == "audit_unexpected_sensitive_field"
    assert "s3cr3t" not in repr(result) and result.new_trade_eligible is False
    assert DecisionAuditStore(journal, environment=ENV).read("XAUUSD") == ()


def test_unread_history_is_not_scanned_but_unknown_benign_keys_are_never_persisted(
    journal: SQLiteJournal,
) -> None:
    event, clean = _step(BUY_INDEX)
    baseline = _decide(_gate(journal, "baseline"), event, clean).record

    noisy = copy.deepcopy(clean)
    noisy["signals"]["history"].append({"token": "ignored-unread-history"})
    noisy["signals"]["decision"]["internal_note"] = "visible?"
    noisy["extra_section"] = {"anything": 1}
    result = _decide(_gate(journal, "noisy"), event, noisy)
    assert result.outcome is AuditGateOutcome.RECORDED_ELIGIBLE
    persisted = str(canonical_serialize(result.record))
    for leaked in ("internal_note", "visible?", "extra_section", "ignored-unread-history"):
        assert leaked not in persisted
    assert baseline is not None and result.record is not None
    assert (
        replace(result.record, environment="baseline", decision_id=baseline.decision_id) == baseline
    )


@pytest.mark.parametrize(
    "secret",
    [
        "see postgres://trader:s3cr3t@db.internal/nexora",
        "Authorization: Bearer abcdefghijklmnop",
        "password=hunter2",
        "api_key: abcdef123456",
        "-----BEGIN RSA PRIVATE KEY-----",
        "A" * 48,  # opaque, key-shaped blob
        "line\x1b[31mcolored",
        "x" * 600,
    ],
)
def test_secret_shaped_free_text_is_rejected_and_never_echoed(
    journal: SQLiteJournal, secret: str
) -> None:
    event, output = _mutated(
        BUY_INDEX, _set(("signals", "decision", "future_conditions"), [secret])
    )
    result = _decide(_gate(journal), event, output)
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code == "audit_redaction_violation"
    assert secret not in repr(result) and result.new_trade_eligible is False


@pytest.mark.parametrize(
    "override",
    [
        {"risk_authority_outcome": "Bearer abcdefghijklmnop"},
        {"risk_authority_outcome": "password"},
        {"lifecycle_ref": "ref:" + "A" * 45},  # long opaque run
        {"lifecycle_ref": "x" * 65},
        {"instrument_id": "has space"},
        {"instrument_id": "key=abc"},
        {"instrument_id": ""},
    ],
)
def test_caller_supplied_references_must_be_short_clean_tokens(
    journal: SQLiteJournal, override: dict[str, str]
) -> None:
    event, output = _step(BUY_INDEX)
    result = _decide(_gate(journal), event, output, **override)
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code == "audit_redaction_violation"


def test_malformed_version_and_identifier_fields_are_rejected(journal: SQLiteJournal) -> None:
    for mutate in (
        _set(("config_version",), "valid test v1"),
        _set(("signals", "decision", "engine_version"), "v1;DROP"),
        _set(("signals", "decision", "config_version"), "v\x00"),
    ):
        event, output = _mutated(BUY_INDEX, mutate)
        result = _decide(_gate(journal), event, output)
        assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
        assert result.failure_code == "audit_redaction_violation"


def test_every_persisted_field_is_declared_in_the_allowlist() -> None:
    for cls in (
        InstrumentIdentity,
        MarketDataReference,
        EvidenceRef,
        AuditBlocker,
        AuditVersions,
        DataQualityRef,
        DecisionAuditRecord,
        EvaluationBlockedRecord,
    ):
        assert set(policy._SCHEMA[cls.__name__]) == {f.name for f in fields(cls)}, cls.__name__


def test_an_undeclared_field_cannot_be_persisted() -> None:
    record = _record(BUY_INDEX)
    spec = [(f.name, Any) for f in fields(DecisionAuditRecord)] + [("api_token", str)]
    shadow = make_dataclass("DecisionAuditRecord", spec, frozen=True)
    values = {f.name: getattr(record, f.name) for f in fields(DecisionAuditRecord)}
    smuggled = shadow(**values, api_token="s3cr3t")
    with pytest.raises(AuditError) as caught:
        policy.validate_payload(smuggled)
    assert caught.value.code == "audit_unexpected_field"
    with pytest.raises(AuditError) as caught:
        policy.validate_payload(SimpleNamespace(**values))
    assert caught.value.code == "audit_unexpected_field"
    policy.validate_payload(record)  # the genuine record conforms


# =============================================================================================
# P1-2 - environment / stream identity
# =============================================================================================


def test_streams_are_structured_and_unambiguous() -> None:
    assert ident.decision_stream("development", "XAUUSD") == "audit:v1:development:XAUUSD"
    assert ident.decision_stream("a:b", "c") == "audit:v1:a%3Ab:c"
    assert ident.decision_stream("a", "b:c") == "audit:v1:a:b%3Ac"
    assert ident.decision_stream("a:b", "c") != ident.decision_stream("a", "b:c")
    assert ident.decision_stream("a%3Ab", "c") != ident.decision_stream("a:b", "c")  # '%' escaped


def test_stream_encoding_is_injective_over_a_hostile_alphabet() -> None:
    alphabet = "ab:%3|-."
    parts = [""]
    frontier = [""]
    for _ in range(3):
        frontier = [p + c for p in frontier for c in alphabet]
        parts.extend(frontier)
    parts = [p for p in parts if p]
    seen: dict[str, tuple[str, str]] = {}
    for env in parts:
        for sym in parts:
            name = ident.decision_stream(env, sym)
            assert seen.setdefault(name, (env, sym)) == (env, sym), (env, sym, seen[name])
    assert len(seen) == len(parts) ** 2


def test_kinds_never_share_a_stream_namespace() -> None:
    assert ident.decision_stream(ENV, "XAUUSD") != ident.blocked_stream(ENV, "XAUUSD")
    assert ident.blocked_stream(ENV, "XAUUSD").startswith("audit-blocked:v1:")
    assert not ident.decision_stream("blocked", "x").startswith("audit-blocked")


@pytest.mark.parametrize("bad", ["", " ", "dev ", " dev", "a\nb", "a\x00", "x" * 65, None, 5, b"x"])
def test_environment_and_symbol_components_are_validated_not_normalized(
    journal: SQLiteJournal, bad: Any
) -> None:
    with pytest.raises(AuditError, match="missing_environment"):
        DecisionAuditStore(journal, environment=bad)
    with pytest.raises(AuditError, match="missing_environment"):
        EvaluationBlockedStore(journal, environment=bad)
    store = DecisionAuditStore(journal, environment=ENV)
    with pytest.raises(AuditError, match="missing_symbol"):
        store.stream(bad)


def test_old_ambiguous_scheme_collision_no_longer_crosses_environments(
    journal: SQLiteJournal,
) -> None:
    # Old scheme: env "x:y" + symbol "XAUUSD"  ==  env "x" + symbol "y:XAUUSD".
    victim = DecisionAuditStore(journal, environment="x")
    attacker = DecisionAuditStore(journal, environment="x:y")
    event, output = _step(BUY_INDEX)
    record = build_decision_audit_record(
        output=output,
        event=event,
        snapshot=_snap(event),
        guard=_guard(),
        evaluated_at=event.received_at,
        environment="x:y",
    )
    assert attacker.append(record) is AppendOutcome.CREATED
    assert victim.read("y:XAUUSD") == ()  # the victim stream is not the attacker's stream
    assert attacker.stream("XAUUSD") != victim.stream("y:XAUUSD")
    assert len(attacker.read("XAUUSD")) == 1


def test_correlation_id_is_structured_and_stable() -> None:
    assert ident.correlation_id("a|b", "c") != ident.correlation_id("a", "b|c")
    assert ident.correlation_id("XAUUSD", "k1") == ident.correlation_id("XAUUSD", "k1")
    assert ident.correlation_id("XAUUSD", "k1").startswith("corr:")


# =============================================================================================
# EVALUATION_BLOCKED - isolated, additive, never eligible
# =============================================================================================


def _blocked_inputs() -> tuple[NormalizedPriceEvent, MarketDataSnapshot]:
    event = replace(tick(5, "100"), bid=D("101"), ask=D("100"))  # crossed book -> blocked
    return event, _snap(event)


def _recorder(journal: Any, environment: str = ENV) -> EvaluationBlockedRecorder:
    return EvaluationBlockedRecorder(
        EvaluationBlockedStore(journal, environment=environment), _guard()
    )


def test_blocked_record_has_no_decision_fields_at_all() -> None:
    names = {f.name for f in fields(EvaluationBlockedRecord)}
    forbidden = {
        "action",
        "score",
        "signal_id",
        "signal_emitted",
        "signal_sequence",
        "decision_id",
        "trade_eligibility",
        "reasons",
        "evidence",
    }
    assert not names & forbidden  # there is nowhere to put a fabricated BUY/SELL/WAIT/score/id
    assert {"record_kind", "blocked_id", "event", "quality", "reason_code", "recorded_at"} <= names
    assert EVALUATION_BLOCKED_KIND == "EVALUATION_BLOCKED"


def test_blocked_evaluation_is_recorded_with_event_quality_reason_and_time(
    journal: SQLiteJournal,
) -> None:
    event, snapshot = _blocked_inputs()
    result = _recorder(journal).record_blocked(
        event=event, snapshot=snapshot, evaluated_at=event.received_at
    )
    record = result.record
    assert result.outcome is EvaluationBlockedOutcome.RECORDED and result.failure_code is None
    assert record is not None
    assert record.record_kind == "EVALUATION_BLOCKED" and record.schema_version == 1
    assert (
        record.blocked_id.startswith("blocked:") and len(record.blocked_id) == len("blocked:") + 64
    )
    assert record.reason_code == "data_quality_blocked"
    assert record.event.event_identity_key == event.identity_key
    assert record.event.source_sequence == event.source_sequence
    assert record.quality.state == "blocked" and record.quality.new_trade_permitted is False
    assert "negative_spread" in record.quality.finding_codes
    assert record.recorded_at == event.received_at  # the verdict's evaluated_at, never wall clock
    assert record.environment == ENV and record.instrument.symbol == "XAUUSD"


def test_unknown_quality_records_its_own_reason_and_falls_back_to_event_time(
    journal: SQLiteJournal,
) -> None:
    event, snapshot = _blocked_inputs()
    good = tick(6, "100")
    result = _recorder(journal).record_blocked(event=good, snapshot=_snap(good), evaluated_at=None)
    record = result.record
    assert result.outcome is EvaluationBlockedOutcome.RECORDED and record is not None
    assert record.reason_code == "data_quality_unknown" and record.quality.state == "unknown"
    assert record.recorded_at == good.received_at  # unsynchronized clock: event time, not now()
    assert record.quality.evaluated_at is None


def test_blocked_records_are_idempotent_and_distinct_per_evaluation_time(
    journal: SQLiteJournal,
) -> None:
    event, snapshot = _blocked_inputs()
    recorder = _recorder(journal)
    store = EvaluationBlockedStore(journal, environment=ENV)
    first = recorder.record_blocked(event=event, snapshot=snapshot, evaluated_at=event.received_at)
    again = recorder.record_blocked(event=event, snapshot=snapshot, evaluated_at=event.received_at)
    assert again.outcome is EvaluationBlockedOutcome.REPLAYED and again.record == first.record
    later = recorder.record_blocked(
        event=event, snapshot=snapshot, evaluated_at=event.received_at + timedelta(seconds=2)
    )
    assert later.outcome is EvaluationBlockedOutcome.RECORDED
    assert later.record.blocked_id != first.record.blocked_id  # type: ignore[union-attr]
    assert len(store.read("XAUUSD")) == 2  # append-only history, nothing overwritten


def test_blocked_records_live_in_their_own_namespace_and_never_touch_decisions(
    journal: SQLiteJournal,
) -> None:
    event, snapshot = _blocked_inputs()
    _recorder(journal).record_blocked(
        event=event, snapshot=snapshot, evaluated_at=event.received_at
    )
    assert DecisionAuditStore(journal, environment=ENV).read("XAUUSD") == ()
    blocked = EvaluationBlockedStore(journal, environment=ENV).read("XAUUSD")
    assert len(blocked) == 1 and blocked[0].blocked_id.startswith("blocked:")
    clean_event, output = _step(BUY_INDEX)
    decision = _decide(_gate(journal), clean_event, output).record
    assert decision is not None and decision.decision_id != blocked[0].blocked_id
    assert not decision.decision_id.startswith("blocked:")


def test_an_ok_evaluation_is_never_recorded_as_blocked(journal: SQLiteJournal) -> None:
    event = tick(7, "100")
    result = _recorder(journal).record_blocked(
        event=event, snapshot=_snap(event), evaluated_at=event.received_at
    )
    assert result.outcome is EvaluationBlockedOutcome.REJECTED
    assert result.failure_code == "evaluation_not_blocked" and result.record is None
    assert EvaluationBlockedStore(journal, environment=ENV).read("XAUUSD") == ()


def test_blocked_recorder_rejects_mismatched_evidence(journal: SQLiteJournal) -> None:
    event, snapshot = _blocked_inputs()
    other = tick(99, "100")
    for bad_snapshot in (_snap(other), MarketDataSnapshot(()), None):
        result = _recorder(journal).record_blocked(
            event=event,
            snapshot=bad_snapshot,  # type: ignore[arg-type]
            evaluated_at=event.received_at,
        )
        assert result.outcome is EvaluationBlockedOutcome.REJECTED and result.record is None


def test_blocked_result_and_recorder_expose_no_authorization_surface() -> None:
    result_fields = set(inspect.signature(audit_pkg.EvaluationBlockedResult).parameters)
    assert result_fields == {"outcome", "record", "failure_code"}
    public = [n for n in dir(EvaluationBlockedRecorder) if not n.startswith("_")]
    assert public == ["record_blocked"]
    store_public = {n for n in dir(EvaluationBlockedStore) if not n.startswith("_")}
    assert store_public == {"append", "environment", "read", "stream"}  # no update/delete


def test_blocked_store_rejects_tampered_foreign_and_cross_environment_records(
    journal: SQLiteJournal,
) -> None:
    event, snapshot = _blocked_inputs()
    record = build_evaluation_blocked_record(
        event=event,
        snapshot=snapshot,
        guard=_guard(),
        evaluated_at=event.received_at,
        environment=ENV,
    )
    store = EvaluationBlockedStore(journal, environment=ENV)
    for forged in (
        replace(record, reason_code="data_quality_unknown"),  # reason disagrees with the state
        replace(record, quality=replace(record.quality, new_trade_permitted=True)),
        replace(record, quality=replace(record.quality, state="ok", new_trade_permitted=True)),
        replace(record, blocked_id="a" * 64),  # decision-shaped id in the blocked namespace
        replace(record, record_kind="DECISION"),  # type: ignore[arg-type]
    ):
        with pytest.raises(AuditError):
            store.append(forged)
    with pytest.raises(AuditError) as caught:
        EvaluationBlockedStore(journal, environment="production").append(record)
    assert caught.value.code == "audit_environment_mismatch"
    with pytest.raises(AuditError) as caught:
        store.append(_record(BUY_INDEX))  # type: ignore[arg-type]
    assert caught.value.code == "invalid_record"
    with pytest.raises(AuditError) as caught:
        DecisionAuditStore(journal, environment=ENV).append(record)  # type: ignore[arg-type]
    assert caught.value.code == "invalid_record"
    assert store.read("XAUUSD") == ()


def test_blocked_record_round_trips_through_canonical_storage(journal: SQLiteJournal) -> None:
    event, snapshot = _blocked_inputs()
    result = _recorder(journal).record_blocked(
        event=event, snapshot=snapshot, evaluated_at=event.received_at, instrument_id="inst:xauusd"
    )
    (stored,) = EvaluationBlockedStore(journal, environment=ENV).read("XAUUSD")
    assert stored == result.record
    assert canonical_serialize(stored) == canonical_serialize(result.record)
    assert stored.instrument.instrument_id == "inst:xauusd"


class _FailingJournal:
    backend = "failing"

    def __init__(self, error: Exception) -> None:
        self.error = error

    def append(self, *args: Any, **kwargs: Any) -> bool:
        raise self.error

    def read(self, stream: str) -> tuple[dict[str, Any], ...]:
        return ()

    def iter_read(self, stream: str) -> Iterator[dict[str, Any]]:
        return iter(())

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (OSError("disk full password=hunter2"), "audit_persistence_failed"),
        (ValueError("journal_identity_conflict"), "audit_identity_conflict"),
        (RuntimeError("anything"), "audit_persistence_failed"),
    ],
)
def test_blocked_persistence_failures_reject_and_never_leak(error: Exception, code: str) -> None:
    event, snapshot = _blocked_inputs()
    result = _recorder(_FailingJournal(error)).record_blocked(
        event=event, snapshot=snapshot, evaluated_at=event.received_at
    )
    assert result.outcome is EvaluationBlockedOutcome.REJECTED
    assert result.failure_code == code and "hunter2" not in repr(result)


def test_blocked_contract_is_isolated_from_engines_and_runtime() -> None:
    forbidden = (
        "nexora.signals",
        "nexora.research",
        "nexora.execution",
        "nexora.autonomous",
        "nexora.position",
        "nexora.paper",
        "nexora.risk",
        "nexora_api",
    )
    tree = ast.parse(Path(blocked_module.__file__).read_text())
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            assert not module.startswith(forbidden), module


# =============================================================================================
# P1-6 - ADR-036 documentation drift
# =============================================================================================

ADR = (
    Path(__file__).resolve().parents[1]
    / "docs/decisions/ADR-036-decision-audit-trail-data-quality-guard-v1.md"
)


def test_adr_036_describes_the_code_that_actually_exists() -> None:
    text = ADR.read_text()
    # Names the implementation really has; names the old text invented are gone.
    for present in (
        "record_decision",
        "RECORDED_REPLAY",
        "EVALUATION_BLOCKED",
        "SequenceHistoryProvider",
    ):
        assert present in text, present
    for stale in (".authorize(", "AuditOutcome.DENIED", "read-back hash matched"):
        assert stale not in text, stale
    assert "stateless" in text.lower()
