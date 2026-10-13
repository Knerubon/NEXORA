"""PR-7 (NARROW scope, Rin-frozen): resolver port, adapter-mode gate, reconciler resolver,
OPEN-8 residual rule, dedup accessor freeze. Production remains DENY-ONLY.

Proof that production stays deny-only (see tasks/PR7-narrow-consumers.md):
``test_production_composition_denies_all_kinds_even_with_simulator_available``,
``test_wired_simulator_with_real_preflight_never_submits`` and
``test_real_preflight_never_allows_reduce_even_with_valid_residual``.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import random
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any, get_type_hints
from unittest import mock

import nexora.execution.broker_adapter as broker_adapter_module
import nexora.execution.instrument_resolution as resolution_module
import nexora.execution.pipeline as pipeline_module
import nexora.execution.reconciler as reconciler_module
import pytest
from nexora.autonomous.broker_capabilities import (
    REASON_VOLUME_ABOVE_MAX,
    REASON_VOLUME_BELOW_MIN,
    REASON_VOLUME_STEP_MISMATCH,
    BrokerCapabilities,
    validate_residual_volume,
    validate_volume,
)
from nexora.autonomous_contracts import TradeIntentKind, TradeState
from nexora.execution.broker_adapter import (
    PHASE1_EXECUTABLE_ADAPTER_MODES,
    SIMULATION_MODE,
    BrokerAdapterError,
    BrokerExecutionAdapter,
    SimulatedBrokerAdapter,
)
from nexora.execution.dedup_store import (
    DedupKeyStatus,
    ExecutionDedupStore,
    InMemoryExecutionDedupStore,
    JournalExecutionDedupStore,
)
from nexora.execution.instrument_resolution import (
    ExecutionBindingMissingError,
    resolve_binding_instrument_id,
)
from nexora.execution.models import ExecutionInstrumentBinding, ExecutionRequest
from nexora.execution.pipeline import (
    ExecutionPipeline,
    PipelineStatus,
    PipelineWiringError,
    non_production_transmission_seam,
)
from nexora.execution.preflight import (
    REASON_POLICY_EVALUATION_UNAVAILABLE,
    REASON_POLICY_UNDECIDED,
    REASON_REDUCE_RESIDUAL_VOLUME_INVALID,
    ExecutionPreflight,
)
from nexora.execution.reconciler import (
    BrokerPositionSnapshot,
    BrokerSnapshot,
    classify_reconciliation,
)
from nexora.execution.reconciliation import ReconciliationFinding as F
from nexora.position.models import PositionRecord, ProtectionLevels
from nexora.storage import SQLiteJournal

from tests.execution_resolver_fixtures import TableResolverForTestsOnly, identity_resolver
from tests.test_execution_pipeline import (
    BOUND,
    NOW,
    AllowPreflightForTestsOnly,
    FakeAdapterForTestsOnly,
    RecordingStore,
    Rig,
    _caps,
    _evidence,
    _inputs,
    _match,
    _position,
)
from tests.test_execution_preflight import _request

ROOT = Path(__file__).resolve().parents[1]
EXEC = ROOT / "packages" / "nexora" / "execution"
KINDS = [
    TradeIntentKind.OPEN,
    TradeIntentKind.REDUCE,
    TradeIntentKind.CLOSE,
    TradeIntentKind.MODIFY_PROTECTION,
]
WRITES = ("claim", "record_attempt", "record_abort", "record_result", "release_for_retry")


# =========================================================================== resolver helper


class _Raises:
    def resolve_execution_instrument(self, execution_symbol: str) -> ExecutionInstrumentBinding:
        raise RuntimeError("secret-payload-token")


class _Returns:
    def __init__(self, value: object) -> None:
        self.value = value

    def resolve_execution_instrument(self, execution_symbol: str) -> Any:
        return self.value


def _binding(symbol: str = "SYM", instrument: str = "inst-1", **kw: Any) -> Any:
    fields: dict[str, Any] = {
        "mode": "binding",
        "execution_symbol": symbol,
        "instrument_id": instrument,
        "feed_id": "feed-1",
        "binding_ref": "binding:1",
    }
    fields.update(kw)
    return ExecutionInstrumentBinding(**fields)


def test_resolver_exact_case_sensitive_match() -> None:
    resolver = TableResolverForTestsOnly({"SYM": "inst-1"})
    assert resolve_binding_instrument_id(resolver, "SYM") == "inst-1"
    for miss in ("sym", "Sym", " SYM", "SYM ", "SYM2", "SY"):
        with pytest.raises(ExecutionBindingMissingError) as err:
            resolve_binding_instrument_id(resolver, miss)
        assert err.value.code == "execution_binding_missing"


@pytest.mark.parametrize("symbol", ["", "   ", None, 5, b"SYM", ("SYM",)])
def test_resolver_blank_or_wrong_type_symbol_denied(symbol: object) -> None:
    resolver = TableResolverForTestsOnly({"SYM": "inst-1"})
    with pytest.raises(ExecutionBindingMissingError):
        resolve_binding_instrument_id(resolver, symbol)
    assert resolver.calls == []  # the port is never even consulted


def test_resolver_missing_wrong_type_raising_and_forged_bindings_denied() -> None:
    class Sub(ExecutionInstrumentBinding):
        pass

    forged = [
        None,
        "inst-1",
        object(),
        _binding("OTHER", "inst-1"),  # binding for a different symbol
        _binding("sym", "inst-1"),  # case differs
        _binding(mode="legacy", feed_id=None),  # legacy => denied (OPEN-9)
    ]
    for value in forged:
        with pytest.raises(ExecutionBindingMissingError):
            resolve_binding_instrument_id(_Returns(value), "SYM")
    for resolver in (None, object(), _Raises(), 7):
        with pytest.raises(ExecutionBindingMissingError) as err:
            resolve_binding_instrument_id(resolver, "SYM")
        assert "secret-payload-token" not in str(err.value)
    sub = Sub(
        mode="binding",
        execution_symbol="SYM",
        instrument_id="inst-1",
        feed_id=None,
        binding_ref="b",
    )
    with pytest.raises(ExecutionBindingMissingError):
        resolve_binding_instrument_id(_Returns(sub), "SYM")  # exact type only
    bad_id = _binding()
    object.__setattr__(bad_id, "instrument_id", 5)
    with pytest.raises(ExecutionBindingMissingError):
        resolve_binding_instrument_id(_Returns(bad_id), "SYM")


# =========================================================================== resolver in pipeline


def test_pipeline_has_no_callable_resolver_shape_any_more() -> None:
    assert not hasattr(
        __import__("nexora.execution.pipeline", fromlist=["x"]), "InstrumentResolver"
    )
    plain_callable = {"SYM": "inst-1"}.get
    rig_log: list[str] = []
    store = InMemoryExecutionDedupStore()
    store.establish_index_genesis()
    pipeline = ExecutionPipeline(
        dedup_store=store,
        clock=lambda: NOW,
        instrument_resolver=plain_callable,  # type: ignore[arg-type]
        max_reconciliation_evidence_age=BOUND,
        max_preflight_age=BOUND,
        transmission=non_production_transmission_seam(
            FakeAdapterForTestsOnly(rig_log), preflight=AllowPreflightForTestsOnly(rig_log)
        ),
    )
    outcome = pipeline.run(_inputs())
    assert outcome.reason_codes == ("execution_binding_missing",)  # a bare callable is denied


@pytest.mark.parametrize(
    "resolver",
    [
        TableResolverForTestsOnly({}),
        TableResolverForTestsOnly({"SYM": "inst-1"}, mode="legacy"),
        _Raises(),
        _Returns(None),
        _Returns(_binding("OTHER")),
        _Returns(_binding(mode="legacy", feed_id=None)),
    ],
)
@pytest.mark.parametrize("kind", KINDS)
def test_resolver_failure_denies_binding_missing_with_no_durable_write(
    resolver: Any, kind: TradeIntentKind
) -> None:
    rig = Rig(resolver=resolver)
    outcome = rig.run(_inputs(kind))
    assert outcome.status is PipelineStatus.DENIED
    assert outcome.reason_codes == ("execution_binding_missing",)
    assert outcome.trace == ("1", "2")
    assert rig.durable_writes() == [] and rig.adapter.calls == [] and rig.preflight.calls == 0


def test_no_resolver_wired_denies() -> None:
    rig = Rig()
    rig.pipeline._resolver = None
    assert rig.run(_inputs()).reason_codes == ("execution_binding_missing",)


def test_position_and_intent_symbols_must_resolve_to_the_same_instrument() -> None:
    table = TableResolverForTestsOnly({"SYM": "inst-1", "OTHER": "inst-2", "ALIAS": "inst-1"})
    for kind in (
        TradeIntentKind.REDUCE,
        TradeIntentKind.CLOSE,
        TradeIntentKind.MODIFY_PROTECTION,
    ):
        rig = Rig(resolver=table)
        different = _inputs(kind, local_positions=(_position(symbol="OTHER"),))
        outcome = rig.run(different)
        assert outcome.reason_codes == ("position_symbol_mismatch",), kind
        assert rig.durable_writes() == []
        # position symbol with NO binding is a binding failure, not a mismatch
        rig = Rig(resolver=table)
        unbound = _inputs(kind, local_positions=(_position(symbol="NOPE"),))
        assert rig.run(unbound).reason_codes == ("execution_binding_missing",), kind
        assert rig.durable_writes() == []
    # position and intent resolve independently (two execution symbols for one instrument)
    rig = Rig(resolver=table)
    ok = rig.run(_inputs(local_positions=(_position(symbol="ALIAS"),)))
    assert ok.status is PipelineStatus.COMPLETED
    assert rig.resolver_calls == ["SYM", "ALIAS"]  # intent first, then the local position


def test_resolver_is_consulted_once_per_symbol_per_run_for_open() -> None:
    rig = Rig()
    rig.run(_inputs(TradeIntentKind.OPEN))
    assert rig.resolver_calls == ["SYM"]


def test_same_resolver_instance_serves_pipeline_and_reconciler() -> None:
    shared = TableResolverForTestsOnly({"EXEC-1": "inst-1"})
    local = _position(symbol="EXEC-1")
    broker = BrokerPositionSnapshot(
        broker_position_ref="b:1",
        instrument_id="inst-1",
        side="long",
        quantity=Decimal("1.00"),
        nexora_position_ref="pos-1",
        stop_price=Decimal("90"),
        target_prices=(Decimal("120"),),
    )
    records = classify_reconciliation(
        local_positions=[local],
        broker_snapshot=BrokerSnapshot(positions=(broker,), complete=True),
        observed_at=NOW,
        instrument_resolver=shared,
    )
    assert [r.finding for r in records] == [F.MATCH]
    store = InMemoryExecutionDedupStore()
    store.establish_index_genesis()
    adapter = FakeAdapterForTestsOnly([])
    pipeline = ExecutionPipeline(
        dedup_store=store,
        clock=lambda: NOW,
        instrument_resolver=shared,  # the very same instance
        max_reconciliation_evidence_age=BOUND,
        max_preflight_age=BOUND,
        transmission=non_production_transmission_seam(
            adapter, preflight=AllowPreflightForTestsOnly([])
        ),
    )
    intent = dataclasses.replace(_inputs().intent, symbol="EXEC-1")
    inputs = _inputs(intent=intent, local_positions=(local,), reconciliation=_evidence(records[0]))
    outcome = pipeline.run(inputs)
    assert outcome.request is not None and outcome.request.instrument_id == "inst-1"
    assert shared.calls.count("EXEC-1") >= 2  # reconciler + pipeline, same object
    assert pipeline._resolver is shared


# =========================================================================== reconciler


INSTR = "inst-1"


def _local(ref: str = "pos:1", symbol: str = "EXEC", state: TradeState = TradeState.OPEN) -> Any:
    return PositionRecord(
        position_id=ref,
        symbol=symbol,
        side="long",
        state=state,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        protection=ProtectionLevels(stop_price=Decimal("90"), target_prices=(Decimal("110"),)),
        opened_at=NOW,
        source_signal_decision_ref="sig",
        source_entry_readiness_ref="er",
    )


def _bpos(ref: str = "b:1", nexora: str | None = "pos:1", instrument: str = INSTR) -> Any:
    return BrokerPositionSnapshot(
        broker_position_ref=ref,
        instrument_id=instrument,
        side="long",
        quantity=Decimal("1"),
        nexora_position_ref=nexora,
        stop_price=Decimal("90"),
        target_prices=(Decimal("110"),),
    )


def _classify(
    local: list[Any], broker: list[Any], resolver: Any, *, complete: bool = True
) -> list[F]:
    records = classify_reconciliation(
        local_positions=local,
        broker_snapshot=BrokerSnapshot(positions=tuple(broker), complete=complete),
        observed_at=NOW,
        instrument_resolver=resolver,
    )
    return [r.finding for r in records]


def test_classify_requires_the_resolver_with_no_default() -> None:
    parameter = inspect.signature(classify_reconciliation).parameters["instrument_resolver"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        classify_reconciliation(  # type: ignore[call-arg]
            local_positions=[_local()],
            broker_snapshot=BrokerSnapshot(positions=(_bpos(),), complete=True),
            observed_at=NOW,
        )


def test_reconciler_no_longer_assumes_symbol_equals_instrument_id() -> None:
    mapped = TableResolverForTestsOnly({"EXEC": INSTR})
    assert _classify([_local()], [_bpos()], mapped) == [F.MATCH]
    # identity is NOT assumed: broker position on the symbol-named instrument does not pair
    assert _classify([_local()], [_bpos(instrument="EXEC")], mapped) == [
        F.BROKER_POSITION_LOCAL_MISSING,
        F.LOCAL_OPEN_BROKER_MISSING,
    ]
    # a table that happens to be the identity pairs on the symbol (explicitly declared)
    assert _classify([_local()], [_bpos(instrument="EXEC")], identity_resolver("EXEC")) == [F.MATCH]


@pytest.mark.parametrize(
    "resolver",
    [
        TableResolverForTestsOnly({}),
        TableResolverForTestsOnly({"EXEC": INSTR}, mode="legacy"),
        _Raises(),
        _Returns(None),
        object(),
    ],
)
def test_unresolvable_local_symbol_fails_closed_never_pairs_never_confirms_flat(
    resolver: Any,
) -> None:
    # complete empty broker snapshot would otherwise confirm flat
    assert _classify([_local()], [], resolver) == [F.LOCAL_OPEN_BROKER_MISSING]
    assert F.MATCH not in _classify([_local()], [_bpos()], resolver)
    assert _classify([_local()], [_bpos()], resolver) == [
        F.BROKER_POSITION_LOCAL_MISSING,
        F.LOCAL_OPEN_BROKER_MISSING,
    ]


def test_ownership_is_never_inferred_from_symbol_side_or_quantity() -> None:
    mapped = TableResolverForTestsOnly({"EXEC": INSTR})
    unattributed = _bpos(nexora=None)  # same instrument, side and quantity as the local
    findings = _classify([_local()], [unattributed], mapped)
    assert F.MATCH not in findings
    assert F.BROKER_POSITION_LOCAL_MISSING in findings  # broker-only, unattributable
    assert F.BROKER_FLAT_CONFIRMED not in findings  # and it blocks the flat confirmation
    # a different instrument for the unattributed position does not block, and stays broker-only
    other = _bpos(nexora=None, instrument="inst-9")
    assert set(_classify([_local()], [other], mapped)) == {
        F.BROKER_FLAT_CONFIRMED,
        F.BROKER_POSITION_LOCAL_MISSING,
    }
    # an attribution naming no local position stays broker-only too
    # (unchanged PR-1b behavior: a foreign explicit ref does not block flat confirmation)
    assert set(_classify([_local()], [_bpos(nexora="pos:zzz")], mapped)) == {
        F.BROKER_POSITION_LOCAL_MISSING,
        F.BROKER_FLAT_CONFIRMED,
    }


def test_reconciler_module_has_no_symbol_equals_instrument_assumption() -> None:
    source = (EXEC / "reconciler.py").read_text(encoding="utf-8")
    assert "candidate.symbol" not in source and "== local.symbol" not in source
    assert reconciler_module.classify_reconciliation is classify_reconciliation


# =========================================================================== adapter-mode gate


def test_accessor_is_on_the_protocol_and_the_simulator() -> None:
    assert "adapter_mode" in BrokerExecutionAdapter.__protocol_attrs__  # type: ignore[attr-defined]
    simulator = SimulatedBrokerAdapter(mode=SIMULATION_MODE, capabilities=_caps())
    assert simulator.adapter_mode == SIMULATION_MODE == "simulation"
    assert isinstance(simulator, BrokerExecutionAdapter)

    class NoMode:
        def capabilities(self) -> BrokerCapabilities:
            return _caps()

        def submit(self, request: ExecutionRequest) -> Any:
            raise AssertionError

    assert not isinstance(NoMode(), BrokerExecutionAdapter)
    with pytest.raises(AttributeError):
        simulator.adapter_mode = "paper"  # type: ignore[misc]  # read-only


def test_mode_vocabulary_is_closed_and_simulation_only() -> None:
    assert PHASE1_EXECUTABLE_ADAPTER_MODES == frozenset({"simulation"})
    for forbidden in ("paper", "demo", "real", "live"):
        with pytest.raises(BrokerAdapterError):
            SimulatedBrokerAdapter(mode=forbidden, capabilities=_caps())
    names = {n.upper() for n in vars(broker_adapter_module)}
    assert not {"PAPER_MODE", "DEMO_MODE", "REAL_MODE", "LIVE_MODE"} & names
    assert not any(n.endswith("Mode") and n != "AdapterMode" for n in vars(broker_adapter_module))


class _ModeDouble(FakeAdapterForTestsOnly):
    """Marked TEST-ONLY double whose accessor and capabilities are scriptable."""

    def __init__(self, mode: Any = SIMULATION_MODE, caps: Any = None) -> None:
        super().__init__([])
        self.mode = mode
        self.caps = caps if caps is not None else _caps()

    @property
    def adapter_mode(self) -> Any:
        if isinstance(self.mode, Exception):
            raise self.mode
        return self.mode

    def capabilities(self) -> Any:
        if isinstance(self.caps, Exception):
            raise self.caps
        return self.caps


class _StrMode(str):
    pass


@pytest.mark.parametrize(
    "mode",
    [
        "paper",
        "demo",
        "real",
        "live",
        "SIMULATION",
        "simulation ",
        "",
        None,
        1,
        _StrMode("simulation"),
        RuntimeError("x"),
    ],
)
def test_wiring_refuses_every_mode_other_than_exact_simulation_str(mode: Any) -> None:
    with pytest.raises(PipelineWiringError) as err:
        non_production_transmission_seam(_ModeDouble(mode))
    assert err.value.code in ("adapter_mode_not_simulation", "adapter_mode_unreadable")


def test_wiring_refuses_bad_capabilities_from_the_adapter() -> None:
    for caps in (RuntimeError("x"), object(), "caps"):
        with pytest.raises(PipelineWiringError):
            non_production_transmission_seam(_ModeDouble(caps=caps))


def test_exact_class_allow_list() -> None:
    simulator = SimulatedBrokerAdapter(mode=SIMULATION_MODE, capabilities=_caps())
    seam = non_production_transmission_seam(simulator)
    assert seam.adapter is simulator and seam.adapter_mode == "simulation"

    class Sub(SimulatedBrokerAdapter):
        pass

    with pytest.raises(PipelineWiringError, match="not_marked"):
        non_production_transmission_seam(Sub(mode=SIMULATION_MODE, capabilities=_caps()))

    class MarkedSub(SimulatedBrokerAdapter):
        NEXORA_NON_PRODUCTION_TEST_ADAPTER = True

    MarkedSub.__module__ = "nexora.execution.hypothetical"
    with pytest.raises(PipelineWiringError, match="production_namespace"):
        non_production_transmission_seam(MarkedSub(mode=SIMULATION_MODE, capabilities=_caps()))

    class Marked(SimulatedBrokerAdapter):  # explicit test-only registration, outside nexora.
        NEXORA_NON_PRODUCTION_TEST_ADAPTER = True

    assert (
        non_production_transmission_seam(
            Marked(mode=SIMULATION_MODE, capabilities=_caps())
        ).adapter_mode
        == "simulation"
    )


def test_pipeline_constructor_rechecks_the_captured_mode() -> None:
    seam = non_production_transmission_seam(_ModeDouble())
    # replace() re-runs the seam gate: stale/forged captured values fail closed at once
    with pytest.raises(PipelineWiringError):
        dataclasses.replace(seam, adapter_mode="real")
    with pytest.raises(PipelineWiringError):
        dataclasses.replace(seam, capabilities="x")  # type: ignore[arg-type]
    # second layer: a seam forged WITHOUT __post_init__ (even carrying the token) is refused
    forged = object.__new__(type(seam))
    for name, value in (
        ("adapter", seam.adapter),
        ("preflight", seam.preflight),
        ("adapter_mode", "real"),
        ("capabilities", seam.capabilities),
        ("_token", pipeline_module._SEAM_TOKEN),
    ):
        object.__setattr__(forged, name, value)
    with pytest.raises(PipelineWiringError) as err:
        ExecutionPipeline(
            dedup_store=InMemoryExecutionDedupStore(), clock=lambda: NOW, transmission=forged
        )
    assert err.value.code == "adapter_mode_not_simulation"


class _RecordingAllow(AllowPreflightForTestsOnly):
    def __init__(self, log: list[str]) -> None:
        super().__init__(log)
        self.seen: list[Any] = []

    def evaluate(self, request: ExecutionRequest, capabilities: Any, *a: Any, **k: Any) -> Any:
        self.seen.append(capabilities)
        return super().evaluate(request, capabilities, *a, **k)


def _seam_pipeline(adapter: Any, preflight: Any) -> tuple[ExecutionPipeline, RecordingStore]:
    log: list[str] = []
    inner = InMemoryExecutionDedupStore()
    inner.establish_index_genesis()
    store = RecordingStore(inner, log)
    pipeline = ExecutionPipeline(
        dedup_store=store,
        clock=lambda: NOW,
        instrument_resolver=TableResolverForTestsOnly({"SYM": "inst-1"}),
        max_reconciliation_evidence_age=BOUND,
        max_preflight_age=BOUND,
        max_capabilities_age=BOUND,
        transmission=non_production_transmission_seam(adapter, preflight=preflight),
    )
    return pipeline, store


def test_capabilities_come_from_the_wired_adapter_not_from_the_caller() -> None:
    adapter = _ModeDouble()
    allow = _RecordingAllow([])
    pipeline, store = _seam_pipeline(adapter, allow)
    mismatching = dataclasses.replace(_caps(), volume_max=Decimal("5"))
    outcome = pipeline.run(_inputs(capabilities=mismatching))
    assert outcome.status is PipelineStatus.DENIED
    assert outcome.reason_codes == ("preflight_capabilities_adapter_mismatch",)
    assert allow.calls == 0 and adapter.calls == [] and WRITES_NOT_IN(store)
    # an equal object, or none at all, is accepted and the ADAPTER's object is what preflight sees
    pipeline, store = _seam_pipeline(adapter, allow)
    assert pipeline.run(_inputs(capabilities=None)).status is PipelineStatus.COMPLETED
    assert allow.seen == [adapter.caps]


def WRITES_NOT_IN(store: RecordingStore) -> bool:  # noqa: N802
    return not any(call in WRITES for call in store.log)


def test_toctou_changed_mode_or_capabilities_after_wiring_cannot_alter_the_gate() -> None:
    adapter = _ModeDouble()
    original = adapter.caps
    allow = _RecordingAllow([])
    pipeline, _ = _seam_pipeline(adapter, allow)
    seam = pipeline._transmission
    assert seam is not None
    adapter.mode = "real"  # flips AFTER wiring
    adapter.caps = dataclasses.replace(_caps(), volume_max=Decimal("1"))
    assert seam.adapter_mode == "simulation" and seam.capabilities == original
    outcome = pipeline.run(_inputs())  # captured mode/caps are used; nothing is re-read
    assert outcome.status is PipelineStatus.COMPLETED
    assert allow.seen == [original]
    # and a caller passing the NEW capabilities is denied as a mismatch
    again, store = _seam_pipeline(_ModeDouble(), _RecordingAllow([]))
    assert again.run(_inputs(capabilities=adapter.caps)).reason_codes == (
        "preflight_capabilities_adapter_mismatch",
    )
    assert WRITES_NOT_IN(store)


# ====================================================================== production DENY-ONLY


@pytest.mark.parametrize("kind", KINDS)
def test_production_composition_denies_all_kinds_even_with_simulator_available(
    tmp_path: Path, kind: TradeIntentKind
) -> None:
    log: list[str] = []
    store = RecordingStore(JournalExecutionDedupStore(SQLiteJournal(tmp_path / "d.sqlite")), log)
    store.inner.establish_index_genesis()
    simulator = SimulatedBrokerAdapter(mode=SIMULATION_MODE, capabilities=_caps())
    pipeline = ExecutionPipeline(  # production composition: NO transmission wiring
        dedup_store=store,
        clock=lambda: NOW,
        instrument_resolver=TableResolverForTestsOnly({"SYM": "inst-1"}),
        max_reconciliation_evidence_age=BOUND,
        max_preflight_age=BOUND,
        max_capabilities_age=BOUND,
    )
    with mock.patch.object(SimulatedBrokerAdapter, "submit", side_effect=AssertionError):
        outcome = pipeline.run(_inputs(kind))
    assert outcome.status is PipelineStatus.DENIED
    assert any(code.startswith("preflight_") for code in outcome.reason_codes)
    assert outcome.trace == ("1", "2", "2b", "3", "4")
    assert not any(call in WRITES for call in log)
    assert simulator.simulated_fill_count == 0 and pipeline._transmission is None


@pytest.mark.parametrize("kind", KINDS)
def test_wired_simulator_with_real_preflight_never_submits(kind: TradeIntentKind) -> None:
    simulator = SimulatedBrokerAdapter(mode=SIMULATION_MODE, capabilities=_caps())
    pipeline, store = _seam_pipeline(simulator, None)  # None => the REAL deny-only preflight
    assert pipeline._transmission is not None  # wiring (construction) succeeded
    with mock.patch.object(SimulatedBrokerAdapter, "submit", side_effect=AssertionError) as submit:
        outcome = pipeline.run(_inputs(kind))
    assert outcome.status is PipelineStatus.DENIED
    assert outcome.trace == ("1", "2", "2b", "3", "4")
    assert any(code.startswith("preflight_") for code in outcome.reason_codes)
    submit.assert_not_called()
    assert simulator.simulated_fill_count == 0
    assert WRITES_NOT_IN(store)  # no claim, no attempt marker, no abort, no result
    assert outcome.request is None and outcome.result is None


@pytest.mark.parametrize("kind", KINDS)
def test_real_preflight_never_allows_reduce_even_with_valid_residual(
    kind: TradeIntentKind,
) -> None:
    request = _request(
        kind if kind is not TradeIntentKind.REDUCE else TradeIntentKind.REDUCE,
        **({"quantity": Decimal("0.50")} if kind is TradeIntentKind.REDUCE else {}),
    )
    decision = ExecutionPreflight().evaluate(
        request,
        _caps(),
        None,
        now=NOW,
        max_capabilities_age=BOUND,
        position_quantity=Decimal("1.00") if kind is not TradeIntentKind.OPEN else None,
    )
    assert decision.allowed is False
    assert (
        REASON_POLICY_UNDECIDED in decision.reason_codes
        or REASON_POLICY_EVALUATION_UNAVAILABLE in decision.reason_codes
    )
    assert REASON_REDUCE_RESIDUAL_VOLUME_INVALID not in decision.reason_codes


def test_no_env_flag_or_default_adapter_switch_exists() -> None:
    for name in ("pipeline.py", "broker_adapter.py", "instrument_resolution.py", "preflight.py"):
        tree = ast.parse((EXEC / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                assert not any(
                    m.split(".")[0] in ("os", "socket", "requests", "httpx") for m in mods
                )
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"environ", "getenv", "order_send"}
    params = inspect.signature(ExecutionPipeline.__init__).parameters
    assert "adapter" not in params and params["transmission"].default is None


def test_broker_adapter_module_ships_only_the_simulator_and_no_broker_sdk() -> None:
    tree = ast.parse((EXEC / "broker_adapter.py").read_text(encoding="utf-8"))
    classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    assert not {c for c in classes if c.endswith("Adapter")} - {
        "BrokerExecutionAdapter",
        "SimulatedBrokerAdapter",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            assert not any(
                "metatrader" in m.lower()
                or m.split(".")[0]
                in {"MetaTrader5", "mt5", "socket", "requests", "httpx", "urllib"}
                for m in mods
            )
    assert "order_send" not in (EXEC / "broker_adapter.py").read_text(encoding="utf-8")


def test_resolution_module_is_pure() -> None:
    tree = ast.parse(Path(resolution_module.__file__ or "").read_text(encoding="utf-8"))
    imported = {
        (n.module if isinstance(n, ast.ImportFrom) else a.name)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    assert imported == {"__future__", "nexora.execution.models"}


# =========================================================================== OPEN-8 residual


def _reduce_reasons(position: Any, reduce: str, **caps_overrides: Any) -> tuple[str, ...]:
    caps = dataclasses.replace(_caps(), **caps_overrides) if caps_overrides else _caps()
    decision = ExecutionPreflight().evaluate(
        _request(TradeIntentKind.REDUCE, quantity=Decimal(reduce)),
        caps,
        None,
        now=NOW,
        max_capabilities_age=BOUND,
        position_quantity=position,
    )
    assert decision.allowed is False  # deny-only regardless
    return decision.reason_codes


_RESIDUAL_TABLE = [
    # (position, reduce, residual_valid, note)
    ("1.00", "0.50", True, "valid"),
    ("1.00", "0.90", True, "residual == volume_min"),
    ("1.00", "0.95", False, "residual 0.05 below min"),
    ("1.03", "0.50", False, "residual 0.53 off step"),
    ("1.00", "1.00", False, "reduce == position"),
    ("1.00", "1.50", False, "reduce > position"),
    ("25", "0.50", True, "residual 24.5 ABOVE volume_max still accepted"),
    ("10.5", "0.50", True, "residual above max, order ok"),
    ("1.00", "0.52", False, "off-step reduce also breaks the grid for the residual"),
]


@pytest.mark.parametrize(("position", "reduce", "valid", "note"), _RESIDUAL_TABLE)
def test_residual_table_via_function_and_preflight(
    position: str, reduce: str, valid: bool, note: str
) -> None:
    caps = _caps()
    assert (validate_residual_volume(caps, Decimal(position), Decimal(reduce)) is None) is valid, (
        note
    )
    codes = _reduce_reasons(Decimal(position), reduce)
    assert (REASON_REDUCE_RESIDUAL_VOLUME_INVALID in codes) is (not valid), note
    # a valid REDUCE still ends denied by the undecided-policy reason; there is no allow path
    assert REASON_POLICY_UNDECIDED in codes


def test_zero_negative_and_non_decimal_inputs_denied() -> None:
    caps = _caps()
    bad: list[Any] = [
        None,
        Decimal(0),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        1.0,
        1,
        "1.00",
        True,
        object(),
    ]
    for value in bad:
        assert validate_residual_volume(caps, value, Decimal("0.5")) is not None
        assert validate_residual_volume(caps, Decimal("1"), value) is not None
    assert validate_residual_volume("caps", Decimal("1"), Decimal("0.5")) is not None  # type: ignore[arg-type]
    for position in (None, 1.0, "1.00", Decimal("NaN"), Decimal("-1"), Decimal(0)):
        assert REASON_REDUCE_RESIDUAL_VOLUME_INVALID in _reduce_reasons(position, "0.50")


def test_requested_quantity_validation_stays_with_validate_volume() -> None:
    # requested qty above volume_max: denied by validate_volume; residual is NOT the reason
    codes = _reduce_reasons(Decimal("20"), "10.5")
    assert REASON_VOLUME_ABOVE_MAX in codes
    assert REASON_REDUCE_RESIDUAL_VOLUME_INVALID not in codes
    # requested qty off step: validate_volume reason present
    assert REASON_VOLUME_STEP_MISMATCH in _reduce_reasons(Decimal("1.00"), "0.52")
    # requested below min
    assert REASON_VOLUME_BELOW_MIN in _reduce_reasons(Decimal("1.00"), "0.05")
    # the residual rule does not apply to OPEN / CLOSE / MODIFY_PROTECTION
    for kind in (TradeIntentKind.OPEN, TradeIntentKind.CLOSE, TradeIntentKind.MODIFY_PROTECTION):
        decision = ExecutionPreflight().evaluate(
            _request(kind),
            _caps(),
            None,
            now=NOW,
            max_capabilities_age=BOUND,
            position_quantity="junk",
        )
        assert REASON_REDUCE_RESIDUAL_VOLUME_INVALID not in decision.reason_codes
    # the new keyword is additive/optional
    default = ExecutionPreflight().evaluate(
        _request(TradeIntentKind.CLOSE), _caps(), None, now=NOW, max_capabilities_age=BOUND
    )
    assert default.allowed is False


def test_residual_uses_the_declared_step_anchor() -> None:
    caps = dataclasses.replace(
        _caps(),
        volume_min=Decimal("0.08"),
        volume_step=Decimal("0.05"),
        volume_step_anchor=Decimal("0.03"),
    )
    assert validate_volume(caps, Decimal("0.53")) is None
    assert validate_residual_volume(caps, Decimal("1.06"), Decimal("0.53")) is None  # r = 0.53
    assert validate_residual_volume(caps, Decimal("1.03"), Decimal("0.53")) is not None  # r = 0.50


def test_decimal_representation_equivalence() -> None:
    caps = _caps()
    positions = ["1.00", "1", "1E+0", "100E-2", "1.0000000000000000000000000000000000001E+0"]
    reduces = ["0.5", "0.50", "5E-1", "50E-2"]
    results = {
        p: {validate_residual_volume(caps, Decimal(p), Decimal(r)) for r in reduces}
        for p in positions[:4]
    }
    assert all(v == {None} for v in results.values())
    assert validate_residual_volume(caps, Decimal(positions[4]), Decimal("0.5")) is not None


def test_ambient_decimal_context_cannot_change_the_answer() -> None:
    caps = _caps()
    with localcontext() as ctx:
        ctx.prec = 3
        assert validate_residual_volume(caps, Decimal("1000.00"), Decimal("0.50")) is None
        assert validate_residual_volume(caps, Decimal("1000.03"), Decimal("0.50")) is not None
    with localcontext() as ctx:
        ctx.prec = 1
        ctx.traps[__import__("decimal").Inexact] = True
        assert validate_residual_volume(caps, Decimal("1.00"), Decimal("0.50")) is None


def test_huge_exponents_are_total_and_never_raise() -> None:
    caps = _caps()
    huge = [
        ("1E+999999", "0.5"),
        ("1E+999999999999", "0.5"),
        ("1E-999999999", "0.5"),
        ("1", "5E-999999999"),
        ("1", "5E+999999999"),
        ("1E+999999", "5E-999999"),
        ("9" * 5000, "0.5"),
    ]
    for position, reduce in huge:
        out = validate_residual_volume(caps, Decimal(position), Decimal(reduce))
        assert out is None or isinstance(out, str)
        decision = ExecutionPreflight().evaluate(
            _request(TradeIntentKind.REDUCE, quantity=Decimal(reduce))
            if Decimal(reduce) > 0
            else _request(TradeIntentKind.CLOSE),
            caps,
            None,
            now=NOW,
            max_capabilities_age=BOUND,
            position_quantity=Decimal(position),
        )
        assert decision.allowed is False
    assert validate_residual_volume(caps, Decimal("1E+999999"), Decimal("0.5")) is None


def test_residual_matches_an_independent_fraction_reference() -> None:
    rng = random.Random(7)
    caps = _caps()
    step, vmin = Fraction(5, 100), Fraction(10, 100)
    for _ in range(2000):
        position = Fraction(rng.randint(1, 4000), 1000)
        reduce = Fraction(rng.randint(1, 4000), 1000)
        residual = position - reduce
        expected = residual >= vmin and residual > 0 and (residual - vmin) % step == 0
        actual = validate_residual_volume(
            caps,
            Decimal(position.numerator) / Decimal(position.denominator),
            Decimal(reduce.numerator) / Decimal(reduce.denominator),
        )
        assert (actual is None) is (reduce < position and expected), (position, reduce)


@pytest.mark.parametrize(
    ("position_qty", "requested", "expected"),
    [
        ("1.00", "0.50", None),
        ("1.03", "0.50", REASON_REDUCE_RESIDUAL_VOLUME_INVALID),
        ("1.00", "0.95", REASON_REDUCE_RESIDUAL_VOLUME_INVALID),
    ],
)
def test_pipeline_passes_position_quantity_and_real_preflight_reports_residual(
    position_qty: str, requested: str, expected: str | None
) -> None:
    quantity = Decimal(position_qty)
    simulator = SimulatedBrokerAdapter(mode=SIMULATION_MODE, capabilities=_caps())
    pipeline, store = _seam_pipeline(simulator, None)
    inputs = _inputs(
        TradeIntentKind.REDUCE,
        local_positions=(_position(quantity=quantity),),
        reconciliation=_evidence(_match(qty=position_qty)),
        requested_quantity=Decimal(requested),
    )
    outcome = pipeline.run(inputs)
    assert outcome.status is PipelineStatus.DENIED and WRITES_NOT_IN(store)
    assert (REASON_REDUCE_RESIDUAL_VOLUME_INVALID in outcome.reason_codes) is (expected is not None)
    assert REASON_POLICY_UNDECIDED in outcome.reason_codes
    # the allow double observes the resolved local position quantity (kw), None for OPEN
    allow = AllowPreflightForTestsOnly([])
    adapter = FakeAdapterForTestsOnly([])
    log_pipeline, _ = _seam_pipeline(adapter, allow)
    log_pipeline.run(inputs)
    assert allow.position_quantities == [quantity]
    open_allow = AllowPreflightForTestsOnly([])
    open_pipeline, _ = _seam_pipeline(FakeAdapterForTestsOnly([]), open_allow)
    open_pipeline.run(_inputs(TradeIntentKind.OPEN))
    assert open_allow.position_quantities == [None]


# =========================================================================== dedup accessor freeze


def test_enumerate_unresolved_accessor_name_and_type_are_frozen() -> None:
    signature = inspect.signature(ExecutionDedupStore.enumerate_unresolved)
    assert list(signature.parameters) == ["self"]
    hints = get_type_hints(
        ExecutionDedupStore.enumerate_unresolved, localns={"DedupKeyStatus": DedupKeyStatus}
    )
    assert hints["return"] == tuple[DedupKeyStatus, ...]
    for impl in (InMemoryExecutionDedupStore, JournalExecutionDedupStore):
        assert callable(impl.enumerate_unresolved)
        assert list(inspect.signature(impl.enumerate_unresolved).parameters) == ["self"]
    assert InMemoryExecutionDedupStore().establish_index_genesis is not None


def test_pipeline_never_references_genesis_or_known_key_registration() -> None:
    tree = ast.parse((EXEC / "pipeline.py").read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert not {"establish_index_genesis", "register_known_keys"} & names
    assert "enumerate_unresolved" in names  # the frozen accessor is the one consumed


def _unused(_: datetime, __: timedelta) -> None:  # keep imports honest for type checkers
    return None
