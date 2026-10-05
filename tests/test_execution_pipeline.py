"""PR-4: ExecutionPipeline (ADR-035 s3) - fully fail-closed, NO real transmission.

Two families of tests:

* PRODUCTION-COMPOSITION tests build the pipeline exactly as production could (no
  transmission wiring) and prove the claim / attempt marker / ``adapter.submit`` are
  unreachable because ExecutionPreflight is deny-only.
* SEAM tests use the clearly marked TEST-ONLY fake adapter and TEST-ONLY allowing
  preflight below to prove order / idempotency mechanics. Neither is importable from a
  production module and no real adapter exists anywhere.
"""

from __future__ import annotations

import ast
import copy
import inspect
from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import nexora.execution.pipeline as pipeline_module
import pytest
from nexora.autonomous.authority import TradingConfig
from nexora.autonomous.broker_capabilities import BrokerCapabilities
from nexora.autonomous.health import Health, SystemHealthSnapshot
from nexora.autonomous_contracts import (
    EntryOrigin,
    PositionOrigin,
    TradeIntent,
    TradeIntentKind,
    TradeState,
    TradingMode,
)
from nexora.execution.broker_adapter import SIMULATION_MODE, SimulatedBrokerAdapter
from nexora.execution.dedup_store import (
    ClaimOutcome,
    DedupEnumerationIncompleteError,
    DedupKeyState,
    DedupKeyStatus,
    DedupMarkerRefusedError,
    DedupStoreCorruptError,
    DedupStoreError,
    DedupStoreIOError,
    InMemoryExecutionDedupStore,
    JournalExecutionDedupStore,
)
from nexora.execution.idempotency import execution_request_idempotency_key
from nexora.execution.models import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    ProtectionRequest,
)
from nexora.execution.pipeline import (
    ExecutionPipeline,
    LifecycleStatus,
    NonProductionTransmissionSeam,
    PipelineInputs,
    PipelineOutcome,
    PipelineStatus,
    PipelineWiringError,
    ReconciliationEvidence,
    is_valid_evidence_ref,
    non_production_transmission_seam,
)
from nexora.execution.preflight import PreflightDecision
from nexora.execution.reconciliation import ReconciliationFinding, ReconciliationRecord
from nexora.execution.recovery import RECOVERY_AUTHORIZATION_NOT_RESOLVED
from nexora.market_data.instruments import FeedBinding, InstrumentDefinition, PriceGrid
from nexora.position.models import PositionInputError, PositionRecord, ProtectionLevels
from nexora.risk.models import RiskDecision
from nexora.storage import SQLiteJournal

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
BOUND = timedelta(seconds=60)
EVIDENCE_REF = "sha256:" + "a" * 64
ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SOURCE = ROOT / "packages" / "nexora" / "execution" / "pipeline.py"


# --------------------------------------------------------------------------- test-only fakes


class FakeAdapterForTestsOnly:
    """TEST-ONLY fake adapter. Explicitly marked; lives in a test module, never in a
    production module. It records calls and never touches anything real."""

    NEXORA_NON_PRODUCTION_TEST_ADAPTER = True

    def __init__(
        self,
        log: list[str],
        behavior: Callable[[ExecutionRequest], Any] | None = None,
    ) -> None:
        self.log = log
        self.behavior = behavior or _filled
        self.calls: list[ExecutionRequest] = []
        self.on_submit: Callable[[], None] | None = None

    def capabilities(self) -> BrokerCapabilities:  # pragma: no cover - never used
        return _caps()

    def submit(self, request: ExecutionRequest) -> ExecutionResult:
        self.log.append("submit")
        self.calls.append(request)
        if self.on_submit is not None:
            self.on_submit()
        return self.behavior(request)


class AllowPreflightForTestsOnly:
    """TEST-ONLY allowing preflight (the real one is deny-only and cannot allow)."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.calls = 0

    def evaluate(
        self,
        request: ExecutionRequest,
        capabilities: BrokerCapabilities | None,
        market_refs: object,
        *,
        now: datetime,
        max_capabilities_age: timedelta | None = None,
    ) -> PreflightDecision:
        self.log.append("preflight")
        self.calls += 1
        return PreflightDecision(
            allowed=True,
            reason_codes=(),
            request_ref=request.request_id,
            evaluated_at=now,
            capabilities_observed_at=NOW,
        )


class RecordingStore:
    """Wraps a real store; records every call into ``log``; supports failure/override hooks."""

    def __init__(self, inner: Any, log: list[str]) -> None:
        self.inner = inner
        self.log = log
        self.raise_on: dict[str, Exception] = {}
        self.hooks: dict[str, Callable[..., Any]] = {}
        self.attempt_kwargs: list[dict[str, Any]] = []

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self.log.append(name)
        if name == "record_attempt":
            self.attempt_kwargs.append(dict(kwargs))
        if name in self.raise_on:
            raise self.raise_on[name]
        if name in self.hooks:
            return self.hooks[name](*args, **kwargs)
        return getattr(self.inner, name)(*args, **kwargs)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_") or name in ("inner", "log", "raise_on", "hooks"):
            raise AttributeError(name)

        def call(*args: Any, **kwargs: Any) -> Any:
            return self._call(name, *args, **kwargs)

        return call


# --------------------------------------------------------------------------- builders


def _caps() -> BrokerCapabilities:
    return BrokerCapabilities(
        binding=FeedBinding(
            instrument_id="inst-1",
            broker_id="broker-a",
            symbol="SYM",
            price_grid=PriceGrid(digits=2, point=Decimal("0.01"), trade_tick_size=None),
            time_offset_seconds=0,
        ),
        instrument=InstrumentDefinition(
            instrument_id="inst-1",
            currency_base="USD",
            currency_profit="USD",
            trade_calc_mode=0,
            trade_contract_size=Decimal("100"),
            chart_mode=0,
        ),
        volume_min=Decimal("0.10"),
        volume_max=Decimal("10"),
        volume_step=Decimal("0.05"),
        stops_level=Decimal("0"),
        freeze_level=Decimal("0"),
        filling_modes=("FOK",),
        execution_mode="market",
        session_policy_ref="p:s",
        spread_policy_ref="p:sp",
        margin_policy_ref="p:m",
        observed_at=NOW,
    )


def _position(**overrides: Any) -> PositionRecord:
    fields_: dict[str, Any] = {
        "position_id": "pos-1",
        "symbol": "SYM",
        "side": "long",
        "state": TradeState.OPEN,
        "quantity": Decimal("1.00"),
        "entry_price": Decimal("100"),
        "protection": ProtectionLevels(stop_price=Decimal("90"), target_prices=(Decimal("120"),)),
        "opened_at": NOW,
        "source_signal_decision_ref": "sig-1",
        "source_entry_readiness_ref": "er-1",
    }
    fields_.update(overrides)
    return PositionRecord(**fields_)


def _intent(kind: TradeIntentKind, pid: str = "p-1") -> TradeIntent:
    if kind is TradeIntentKind.OPEN:
        return TradeIntent(
            kind=kind,
            symbol="SYM",
            side="long",
            origin=EntryOrigin(signal_decision_ref="sig-1", entry_readiness_ref="er-1"),
            proposal_id=pid,
        )
    return TradeIntent(
        kind=kind,
        symbol="SYM",
        side="long",
        origin=PositionOrigin(position_id="pos-1", exit_decision_ref="exit-1"),
        proposal_id=pid,
    )


def _match(ref: str = "pos-1", qty: str | None = "1.00", at: datetime = NOW) -> Any:
    quantity = None if qty is None else Decimal(qty)
    return ReconciliationRecord(
        position_ref=ref,
        finding=ReconciliationFinding.MATCH,
        local_quantity=quantity,
        broker_quantity=quantity,
        observed_at=at,
    )


def _evidence(*records: ReconciliationRecord, ref: str = EVIDENCE_REF) -> ReconciliationEvidence:
    return ReconciliationEvidence(records=records or (_match(),), evidence_ref=ref, observed_at=NOW)


def _risk() -> RiskDecision:
    return RiskDecision(
        decision_id="d-1",
        proposal_id="p-1",
        signal_id="s-1",
        action="allow",
        reason="ok",
        reason_codes=(),
        approved_size=Decimal("1"),
        reserved_risk=Decimal("1"),
        effective_time=NOW,
        policy_version="v1",
        source_refs=(),
    )


def _health(**overrides: Health) -> SystemHealthSnapshot:
    axes = {
        axis: Health.HEALTHY
        for axis in (
            "market_data",
            "broker",
            "account",
            "capabilities",
            "reconciliation",
            "journal",
        )
    }
    axes.update({k: v for k, v in overrides.items()})
    return SystemHealthSnapshot(observed_at=NOW, **axes)


def _inputs(kind: TradeIntentKind = TradeIntentKind.CLOSE, **overrides: Any) -> PipelineInputs:
    params: dict[str, Any] = {
        "intent": _intent(kind),
        "request_id": "req-1",
        "local_positions": () if kind is TradeIntentKind.OPEN else (_position(),),
        "reconciliation": _evidence(),
        "health": _health(),
        "config": TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        "kill_switch": False,
        "entry_readiness": "READY",
        "risk_decision": _risk(),
        "capabilities": _caps(),
    }
    if kind is TradeIntentKind.OPEN:
        params["requested_quantity"] = Decimal("1.00")
        params["reconciliation"] = _evidence(_match("pos-other"))
    elif kind is TradeIntentKind.REDUCE:
        params["requested_quantity"] = Decimal("0.50")
    elif kind is TradeIntentKind.MODIFY_PROTECTION:
        params["protection"] = ProtectionRequest(stop_price=Decimal("95"))
    params.update(overrides)
    return PipelineInputs(**params)


def _filled(request: ExecutionRequest) -> ExecutionResult:
    return _result(request, ExecutionStatus.FILLED)


def _result(
    request: ExecutionRequest, status: ExecutionStatus, *, fill: str | None = None
) -> ExecutionResult:
    if request.action is TradeIntentKind.MODIFY_PROTECTION:
        return ExecutionResult(
            result_id="r-1",
            request_ref=request.request_id,
            status=status,
            requested_quantity=None,
            observed_at=NOW,
            action=request.action,
            nexora_position_ref=request.position_ref,
        )
    quantity = request.quantity
    assert quantity is not None
    if status is ExecutionStatus.FILLED:
        filled = quantity
    elif fill is not None:
        filled = Decimal(fill)
    else:
        filled = Decimal("0")
    return ExecutionResult(
        result_id="r-1",
        request_ref=request.request_id,
        status=status,
        requested_quantity=quantity,
        filled_quantity=filled,
        remaining_quantity=None if status is ExecutionStatus.UNKNOWN else quantity - filled,
        observed_at=NOW,
        action=request.action,
        nexora_position_ref=request.position_ref or request.new_position_ref,
    )


class Rig:
    """One pipeline wired through the TEST-ONLY seam over a recording store."""

    def __init__(
        self,
        *,
        behavior: Callable[[ExecutionRequest], Any] | None = None,
        genesis: bool = True,
        clock: Callable[[], datetime] | None = None,
        resolver: Callable[[str], str | None] | None = None,
        bounds: bool = True,
    ) -> None:
        self.log: list[str] = []
        inner = InMemoryExecutionDedupStore()
        if genesis:
            inner.establish_index_genesis()
        self.inner = inner
        self.store = RecordingStore(inner, self.log)
        self.adapter = FakeAdapterForTestsOnly(self.log, behavior)
        self.preflight = AllowPreflightForTestsOnly(self.log)
        self.resolver_calls: list[str] = []
        base_resolver = resolver or {"SYM": "inst-1"}.get

        def recording_resolver(symbol: str) -> str | None:
            self.resolver_calls.append(symbol)
            self.log.append("resolve")
            return base_resolver(symbol)

        self.pipeline = ExecutionPipeline(
            dedup_store=self.store,
            clock=clock or (lambda: NOW),
            instrument_resolver=recording_resolver,
            max_reconciliation_evidence_age=BOUND if bounds else None,
            max_preflight_age=BOUND if bounds else None,
            max_capabilities_age=BOUND,
            transmission=non_production_transmission_seam(self.adapter, preflight=self.preflight),
        )

    def run(self, inputs: PipelineInputs) -> PipelineOutcome:
        return self.pipeline.run(inputs)

    def durable_writes(self) -> list[str]:
        writes = ("claim", "record_attempt", "record_abort", "record_result", "release_for_retry")
        return [c for c in self.log if c in writes]

    def key_state(self, kind: TradeIntentKind = TradeIntentKind.CLOSE) -> DedupKeyState:
        return self.inner.state(execution_request_idempotency_key(_intent(kind)))


def _production_pipeline(
    tmp_path: Path, log: list[str]
) -> tuple[ExecutionPipeline, RecordingStore]:
    store = RecordingStore(JournalExecutionDedupStore(SQLiteJournal(tmp_path / "d.sqlite")), log)
    store.inner.establish_index_genesis()
    pipeline = ExecutionPipeline(
        dedup_store=store,
        clock=lambda: NOW,
        instrument_resolver={"SYM": "inst-1"}.get,
        max_reconciliation_evidence_age=BOUND,
        max_preflight_age=BOUND,
        max_capabilities_age=BOUND,
    )
    return pipeline, store


# --------------------------------------------------------------------------- production composition

_KINDS = [
    TradeIntentKind.OPEN,
    TradeIntentKind.REDUCE,
    TradeIntentKind.CLOSE,
    TradeIntentKind.MODIFY_PROTECTION,
]


@pytest.mark.parametrize("kind", _KINDS)
def test_production_composition_never_reaches_claim_attempt_or_submit(
    tmp_path: Path, kind: TradeIntentKind
) -> None:
    log: list[str] = []
    pipeline, store = _production_pipeline(tmp_path, log)
    outcome = pipeline.run(_inputs(kind))
    assert outcome.status is PipelineStatus.DENIED
    assert outcome.trace == ("1", "2", "2b", "3", "4")  # reached preflight, stopped there
    assert any(code.startswith("preflight_") for code in outcome.reason_codes)
    for write in ("claim", "record_attempt", "record_abort", "record_result", "release_for_retry"):
        assert write not in log
    assert outcome.request is None and outcome.result is None


def test_production_composition_denies_even_if_preflight_were_allowed(tmp_path: Path) -> None:
    log: list[str] = []
    pipeline, _ = _production_pipeline(tmp_path, log)
    allow = AllowPreflightForTestsOnly(log)
    pipeline._production_preflight = allow  # type: ignore[assignment]
    outcome = pipeline.run(_inputs())
    assert outcome.status is PipelineStatus.DENIED
    assert outcome.reason_codes == ("transmission_not_wired",)
    assert "claim" not in log and "record_attempt" not in log


def test_in_memory_store_refused_without_the_test_seam() -> None:
    with pytest.raises(PipelineWiringError) as err:
        ExecutionPipeline(dedup_store=InMemoryExecutionDedupStore(), clock=lambda: NOW)
    assert err.value.code == "in_memory_dedup_store_refused"


def test_no_default_adapter_and_no_adapter_parameter() -> None:
    params = inspect.signature(ExecutionPipeline.__init__).parameters
    assert "adapter" not in params
    assert params["transmission"].default is None
    pipeline = ExecutionPipeline(
        dedup_store=JournalExecutionDedupStore(SQLiteJournal(Path(":memory:"))), clock=lambda: NOW
    )
    assert pipeline._transmission is None


def test_pipeline_refuses_a_raw_adapter_and_a_forged_seam() -> None:
    log: list[str] = []
    adapter = FakeAdapterForTestsOnly(log)
    store = InMemoryExecutionDedupStore()
    with pytest.raises(PipelineWiringError):
        ExecutionPipeline(dedup_store=store, clock=lambda: NOW, transmission=adapter)  # type: ignore[arg-type]
    forged = NonProductionTransmissionSeam(
        adapter=adapter, preflight=AllowPreflightForTestsOnly(log), _token=object()
    )
    with pytest.raises(PipelineWiringError) as err:
        ExecutionPipeline(dedup_store=store, clock=lambda: NOW, transmission=forged)
    assert err.value.code == "transmission_wiring_not_permitted"


def test_seam_refuses_unmarked_namespaced_and_non_adapters() -> None:
    class Unmarked(FakeAdapterForTestsOnly):
        NEXORA_NON_PRODUCTION_TEST_ADAPTER = False

    class InProductionNamespace(FakeAdapterForTestsOnly):
        pass

    InProductionNamespace.__module__ = "nexora.execution.some_adapter"

    with pytest.raises(PipelineWiringError, match="not_marked"):
        non_production_transmission_seam(Unmarked([]))
    with pytest.raises(PipelineWiringError, match="production_namespace"):
        non_production_transmission_seam(InProductionNamespace([]))
    with pytest.raises(PipelineWiringError, match="does_not_implement"):
        non_production_transmission_seam(object())
    simulated = SimulatedBrokerAdapter(mode=SIMULATION_MODE, capabilities=_caps())
    with pytest.raises(PipelineWiringError):  # shipped simulator is refused too
        non_production_transmission_seam(simulated)


def _imports(tree: ast.AST) -> list[tuple[str, str | None]]:
    found: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, None) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.extend((node.module or "", alias.name) for alias in node.names)
    return found


def _code_only() -> str:
    """Module source without its docstring/comments (prose may name forbidden things)."""
    tree = ast.parse(PIPELINE_SOURCE.read_text(encoding="utf-8"))
    tree.body = tree.body[1:]  # drop the module docstring
    return ast.unparse(tree)


def test_pipeline_module_has_no_broker_sdk_order_send_or_real_adapter() -> None:
    source = _code_only()
    tree = ast.parse(source)
    forbidden = ("MetaTrader5", "mt5", "socket", "requests", "httpx", "urllib", "aiohttp", "http")
    for module, _ in _imports(tree):
        assert module.split(".")[0] not in forbidden
        assert "metatrader" not in module.lower()
    assert "order_send" not in source
    from_broker = {n for m, n in _imports(tree) if m == "nexora.execution.broker_adapter"}
    assert from_broker == {"BrokerExecutionAdapter"}  # the Protocol only
    assert "SimulatedBrokerAdapter" not in source


def test_pipeline_never_touches_recovery_application_genesis_or_latest_result() -> None:
    source = _code_only()
    for token in (
        "plan_emergency_recovery",
        "recover_from_emergency",
        "establish_index_genesis",
        "register_known_keys",
        "latest_result",
        ".lookup(",
        "operator_ref",
        "unresolved_among",
    ):
        assert token not in source, token
    recovery_imports = {
        n for m, n in _imports(ast.parse(source)) if m.endswith("execution.recovery")
    }
    assert recovery_imports == {"RECOVERY_AUTHORIZATION_NOT_RESOLVED"}


def test_seam_is_referenced_only_by_the_pipeline_module_and_tests() -> None:
    needles = ("non_production_transmission_seam", "NonProductionTransmissionSeam")
    offenders: list[str] = []
    for base in (ROOT / "packages", ROOT / "apps", ROOT / "scripts"):
        for path in base.rglob("*.py"):
            if path == PIPELINE_SOURCE or ".venv" in path.parts or "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(n in text for n in needles):
                offenders.append(str(path))
    assert offenders == []


def test_pipeline_is_not_exported_or_wired_in_the_package() -> None:
    init = (ROOT / "packages" / "nexora" / "execution" / "__init__.py").read_text("utf-8")
    assert "execution.pipeline" not in init and "import pipeline" not in init
    for base in (ROOT / "apps", ROOT / "packages"):
        for path in base.rglob("*.py"):
            if path == PIPELINE_SOURCE or "node_modules" in path.parts or ".venv" in path.parts:
                continue
            assert "execution.pipeline" not in path.read_text(encoding="utf-8", errors="ignore")


def test_auto_mode_lock_unchanged_denies_without_claim() -> None:
    rig = Rig()
    outcome = rig.run(_inputs(config=TradingConfig(mode=TradingMode.AUTO)))
    assert outcome.status is PipelineStatus.DENIED
    assert "auto_mode_not_governed" in outcome.reason_codes
    assert rig.durable_writes() == [] and rig.adapter.calls == []


def test_inputs_have_no_authority_or_ai_field() -> None:
    names = {f.name for f in fields(PipelineInputs)}
    assert not {"authority", "authority_decision", "ai_analysis", "operator_ref"} & names
    annotations = " ".join(str(f.type) for f in fields(PipelineInputs))
    assert "AuthorityDecision" not in annotations and "AIAnalysis" not in annotations


# --------------------------------------------------------------------------- happy path / order


def test_seam_happy_path_follows_adr_order_claim_attempt_submit_result() -> None:
    rig = Rig()
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.COMPLETED
    assert outcome.trace == ("1", "2", "2b", "3", "4", "5", "6", "7", "8", "9")
    order = [c for c in rig.log if c in ("claim", "record_attempt", "submit", "record_result")]
    assert order == ["claim", "record_attempt", "submit", "record_result"]
    assert rig.log.index("enumerate_unresolved") < rig.log.index("resolve")
    assert rig.log.index("resolve") < rig.log.index("preflight") < rig.log.index("claim")
    assert len(rig.adapter.calls) == 1 and rig.adapter.calls[0].quantity == Decimal("1.00")
    assert outcome.result_persisted and outcome.result is not None
    assert rig.key_state() is DedupKeyState.RESULT_UNSAFE  # FILLED is never auto-released
    assert outcome.lifecycle is LifecycleStatus.APPLIED
    assert outcome.next_position is not None
    assert outcome.next_position.state is TradeState.CLOSED
    assert outcome.next_position.quantity == Decimal("0")
    assert outcome.post_execution_reconciliation_required is True


def test_attempt_marker_refs_are_canonical_sha256() -> None:
    rig = Rig()
    rig.run(_inputs())
    (kwargs,) = rig.store.attempt_kwargs
    for name in ("request_digest", "reconciliation_evidence_ref", "preflight_decision_ref"):
        assert is_valid_evidence_ref(kwargs[name]), name
    assert kwargs["reconciliation_evidence_ref"] == EVIDENCE_REF
    assert kwargs["resolved_quantity"] == Decimal("1.00")


@pytest.mark.parametrize(
    ("kind", "expected_state", "expected_qty"),
    [
        (TradeIntentKind.REDUCE, TradeState.MANAGING, Decimal("0.50")),
        (TradeIntentKind.MODIFY_PROTECTION, TradeState.OPEN, Decimal("1.00")),
    ],
)
def test_seam_other_kinds_complete(
    kind: TradeIntentKind, expected_state: TradeState, expected_qty: Decimal
) -> None:
    rig = Rig(
        behavior=lambda r: _result(
            r, ExecutionStatus.FILLED if r.quantity else ExecutionStatus.ACCEPTED
        )
    )
    outcome = rig.run(_inputs(kind))
    assert outcome.status is PipelineStatus.COMPLETED
    position = outcome.next_position or _position()
    assert position.state is expected_state and position.quantity == expected_qty


def test_seam_open_completes_without_lifecycle_application() -> None:
    rig = Rig()
    outcome = rig.run(_inputs(TradeIntentKind.OPEN))
    assert outcome.status is PipelineStatus.COMPLETED
    assert (
        outcome.lifecycle is LifecycleStatus.NOT_APPLICABLE_OPEN and outcome.next_position is None
    )
    assert rig.adapter.calls[0].new_position_ref == "pos:p-1"


def test_modify_protection_authority_uses_derived_tighten_under_degradation() -> None:
    # Degraded (non-broker) health only allows a MODIFY_PROTECTION that step 2 derived as TIGHTEN.
    rig = Rig(behavior=lambda r: _result(r, ExecutionStatus.ACCEPTED))
    degraded = _inputs(TradeIntentKind.MODIFY_PROTECTION, health=_health(market_data=Health.STALE))
    assert rig.run(degraded).status is PipelineStatus.COMPLETED
    widen = _inputs(
        TradeIntentKind.MODIFY_PROTECTION,
        health=_health(market_data=Health.STALE),
        protection=ProtectionRequest(stop_price=Decimal("85")),
    )
    other = Rig()
    denied = other.run(widen)
    assert denied.reason_codes == ("protection_change_unclassified",)
    assert denied.trace == ("1", "2") and other.durable_writes() == []


# A failing earlier step prevents every later step, including claim / attempt / submit.
_ORDER_CASES: list[tuple[str, dict[str, Any], tuple[str, ...], str]] = [
    ("1", {"reconciliation": None}, ("1",), "reconciliation_evidence_missing"),
    ("2", {"requested_quantity": None}, ("1", "2"), "quantity_payload_missing"),
    ("2b", {"risk_decision": None}, ("1", "2", "2b"), "authority_inputs_invalid"),
    ("3", {"kill_switch": True}, ("1", "2", "2b", "3"), "kill_switch_armed"),
]


@pytest.mark.parametrize(("step", "overrides", "trace", "reason"), _ORDER_CASES)
def test_earlier_denial_prevents_all_later_steps(
    step: str, overrides: dict[str, Any], trace: tuple[str, ...], reason: str
) -> None:
    rig = Rig()
    outcome = rig.run(_inputs(TradeIntentKind.OPEN, **overrides))
    assert outcome.status is PipelineStatus.DENIED and outcome.trace == trace, step
    assert reason in outcome.reason_codes
    assert rig.durable_writes() == [] and rig.adapter.calls == []
    if step in ("1", "2"):
        assert rig.preflight.calls == 0
    if step in ("1",):
        assert rig.resolver_calls == []  # resolution never ran
    if step in ("1", "2", "2b", "3"):
        assert rig.preflight.calls == 0  # guard (3) precedes preflight (4)


def test_resolver_missing_or_unbound_symbol_denies_before_authority() -> None:
    rig = Rig(resolver=lambda s: None)
    outcome = rig.run(_inputs())
    assert outcome.reason_codes == ("execution_binding_missing",) and outcome.trace == ("1", "2")
    genesis_store = JournalExecutionDedupStore(SQLiteJournal(Path(":memory:")))
    genesis_store.establish_index_genesis()
    no_resolver = ExecutionPipeline(
        dedup_store=genesis_store,
        clock=lambda: NOW,
        max_reconciliation_evidence_age=BOUND,
        max_preflight_age=BOUND,
    )
    assert no_resolver.run(_inputs()).reason_codes == ("execution_binding_missing",)


def test_preflight_denial_prevents_claim_attempt_and_submit() -> None:
    rig = Rig()

    class Deny(AllowPreflightForTestsOnly):
        def evaluate(self, request: ExecutionRequest, *a: Any, now: datetime, **k: Any) -> Any:
            self.log.append("preflight")
            return PreflightDecision(
                allowed=False,
                reason_codes=("preflight_policy_undecided",),
                request_ref=request.request_id,
                evaluated_at=now,
                capabilities_observed_at=None,
            )

    rig.pipeline._transmission = non_production_transmission_seam(
        rig.adapter, preflight=Deny(rig.log)
    )
    outcome = rig.run(_inputs())
    assert outcome.reason_codes == ("preflight_policy_undecided",)
    assert outcome.trace == ("1", "2", "2b", "3", "4")
    assert rig.durable_writes() == [] and rig.adapter.calls == []


def test_reconciliation_gate_blocks_every_kind_unless_synchronized() -> None:
    mismatch = ReconciliationRecord(
        position_ref="pos-1",
        finding=ReconciliationFinding.QUANTITY_MISMATCH,
        local_quantity=Decimal("1"),
        broker_quantity=Decimal("2"),
        observed_at=NOW,
    )
    unknown = ReconciliationRecord(
        position_ref=None,
        finding=ReconciliationFinding.EXECUTION_RESULT_UNKNOWN,
        details_ref="x",
        observed_at=NOW,
    )
    for kind in _KINDS:
        for records, label in (((mismatch,), "UNSYNCHRONIZED"), ((unknown,), "UNKNOWN")):
            rig = Rig()
            outcome = rig.run(_inputs(kind, reconciliation=_evidence(*records)))
            assert outcome.reason_codes == (f"reconciliation_not_synchronized:{label}",)
            assert rig.durable_writes() == [] and rig.adapter.calls == []


def test_evidence_must_be_one_snapshot_fresh_and_bound_must_exist() -> None:
    rig = Rig()
    mixed = _evidence(_match(), _match("pos-2", at=NOW + timedelta(seconds=1)))
    assert rig.run(_inputs(reconciliation=mixed)).reason_codes == (
        "reconciliation_evidence_not_single_snapshot",
    )
    stale = Rig(clock=lambda: NOW + timedelta(seconds=61))
    assert stale.run(_inputs()).reason_codes == ("reconciliation_evidence_stale",)
    future = Rig(clock=lambda: NOW - timedelta(seconds=1))
    assert future.run(_inputs()).reason_codes == ("reconciliation_evidence_stale",)
    unbounded = Rig(bounds=False)  # OPEN-1: no bound => deny, nothing invented
    assert unbounded.run(_inputs()).reason_codes == (
        "reconciliation_evidence_freshness_bound_missing",
    )
    for r in (rig, stale, future, unbounded):
        assert r.durable_writes() == []


def test_emergency_position_is_denied_and_recovery_is_never_attempted() -> None:
    rig = Rig()
    emergency = _position(state=TradeState.EMERGENCY)
    outcome = rig.run(_inputs(local_positions=(emergency,)))
    assert outcome.reason_codes == ("position_not_open_or_managing",)
    assert rig.durable_writes() == []


def test_resolution_requires_confirming_match_evidence_for_the_position() -> None:
    for evidence in (_evidence(_match("pos-other")), _evidence(_match(qty=None))):
        rig = Rig()
        outcome = rig.run(_inputs(reconciliation=evidence))
        assert outcome.status is PipelineStatus.DENIED and outcome.trace == ("1", "2")
        assert rig.durable_writes() == []


def test_reduce_quantity_and_payloads_are_never_defaulted() -> None:
    for overrides, reason in (
        ({"requested_quantity": Decimal("1.00")}, "reduce_quantity_invalid"),
        ({"requested_quantity": None}, "quantity_payload_missing"),
        ({"requested_quantity": Decimal("0")}, "quantity_payload_missing"),
    ):
        assert Rig().run(_inputs(TradeIntentKind.REDUCE, **overrides)).reason_codes == (reason,)
    assert Rig().run(_inputs(TradeIntentKind.MODIFY_PROTECTION, protection=None)).reason_codes == (
        "protection_payload_missing",
    )


# --------------------------------------------------------------------------- OPEN-16 global gate


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (DedupStoreIOError("io"), "dedup_store_io_error"),
        (DedupStoreCorruptError("corrupt"), "dedup_store_corrupt"),
        (DedupEnumerationIncompleteError("genesis"), "dedup_enumeration_incomplete"),
        (DedupStoreError("other"), "dedup_store_error"),
        (RuntimeError("boom"), "dedup_enumeration_failed"),
    ],
)
def test_global_gate_denies_on_any_enumeration_error(error: Exception, code: str) -> None:
    rig = Rig()
    rig.store.raise_on["enumerate_unresolved"] = error
    outcome = rig.run(_inputs())
    assert outcome.reason_codes == (code,) and outcome.trace == ("1",)
    assert rig.durable_writes() == [] and rig.adapter.calls == []


def test_global_gate_without_genesis_marker_denies_incomplete() -> None:
    rig = Rig(genesis=False)  # real store: enumeration raises DedupEnumerationIncompleteError
    outcome = rig.run(_inputs())
    assert outcome.reason_codes == ("dedup_enumeration_incomplete",)
    assert rig.durable_writes() == []


def test_empty_enumeration_without_error_is_the_only_pass() -> None:
    rig = Rig()
    assert rig.inner.enumerate_unresolved() == ()
    assert rig.run(_inputs()).status is PipelineStatus.COMPLETED
    bad = Rig()
    bad.store.hooks["enumerate_unresolved"] = lambda: None  # malformed => deny, not "empty"
    assert bad.run(_inputs()).reason_codes == ("dedup_enumeration_malformed",)


@pytest.mark.parametrize("state", [s for s in DedupKeyState if s is not DedupKeyState.UNCLAIMED])
def test_global_gate_blocks_on_every_returned_status(state: DedupKeyState) -> None:
    rig = Rig()
    status = DedupKeyStatus(
        idempotency_key="exec:CLOSE:other",
        state=state,
        generation=None if state is DedupKeyState.QUARANTINED else 0,
        latest_result=None,
        violation_code="x" if state is DedupKeyState.QUARANTINED else None,
    )
    rig.store.hooks["enumerate_unresolved"] = lambda: (status,)
    outcome = rig.run(_inputs())
    assert outcome.reason_codes == (f"dedup_unresolved:{state.value}",)
    assert rig.durable_writes() == [] and rig.adapter.calls == []


def test_unsafe_result_blocks_later_intents_and_the_same_intent_without_retry() -> None:
    rig = Rig()
    first = rig.run(_inputs())
    assert first.status is PipelineStatus.COMPLETED
    again = rig.run(_inputs())  # same intent
    other = rig.run(_inputs(intent=_intent(TradeIntentKind.CLOSE, "p-2")))
    for outcome in (again, other):
        assert outcome.status is PipelineStatus.DENIED
        assert outcome.reason_codes == ("dedup_unresolved:RESULT_UNSAFE",)
    assert len(rig.adapter.calls) == 1  # no retry, no reclaim


def test_dedup_state_is_authoritative_not_latest_result() -> None:
    rig = Rig()
    rig.store.hooks["lookup"] = lambda *a, **k: (_ for _ in ()).throw(AssertionError("lookup used"))
    clean = _result(
        ExecutionRequest(
            request_id="r",
            idempotency_key="exec:CLOSE:other",
            intent_proposal_id="other",
            origin_ref="o",
            instrument_id="inst-1",
            side="long",
            action=TradeIntentKind.CLOSE,
            quantity=Decimal("1"),
            position_ref="pos-1",
            created_at=NOW,
        ),
        ExecutionStatus.REJECTED,
    )
    poisoned = DedupKeyStatus(
        idempotency_key="exec:CLOSE:other",
        state=DedupKeyState.RESULT_UNSAFE,
        generation=0,
        latest_result=clean,  # looks clean, state says unsafe: state wins
        violation_code=None,
    )
    rig.store.hooks["enumerate_unresolved"] = lambda: (poisoned,)
    assert rig.run(_inputs()).reason_codes == ("dedup_unresolved:RESULT_UNSAFE",)


def test_pipeline_never_calls_index_genesis_or_known_key_registration() -> None:
    rig = Rig()
    rig.run(_inputs())
    assert "establish_index_genesis" not in rig.log and "register_known_keys" not in rig.log
    assert "lookup" not in rig.log and "unresolved_among" not in rig.log


def test_duplicate_claim_never_transmits() -> None:
    rig = Rig()
    key = execution_request_idempotency_key(_intent(TradeIntentKind.CLOSE))

    def lose_the_race(k: str) -> ClaimOutcome:
        rig.inner.claim(k)  # "another caller" wins first
        return rig.inner.claim(k)

    rig.store.hooks["claim"] = lose_the_race
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.DUPLICATE
    assert "dedup_state:CLAIMED_NOT_ATTEMPTED" in outcome.reason_codes
    assert rig.adapter.calls == [] and "record_attempt" not in rig.log
    assert rig.inner.state(key) is DedupKeyState.CLAIMED_NOT_ATTEMPTED


def test_quarantined_own_key_denies() -> None:
    rig = Rig()
    quarantined = DedupKeyStatus(
        idempotency_key="k",
        state=DedupKeyState.QUARANTINED,
        generation=None,
        latest_result=None,
        violation_code="v",
    )
    rig.store.hooks["inspect"] = lambda key: quarantined
    assert rig.run(_inputs()).reason_codes == ("dedup_quarantined",)
    assert rig.durable_writes() == []


# ------------------------------------------------------------- evidence ref allow-list

_VALID_HEX = "0123456789abcdef" * 4


@pytest.mark.parametrize("value", ["sha256:" + _VALID_HEX, "sha256:" + "0" * 64])
def test_evidence_ref_accepts_only_canonical_sha256_lowercase(value: str) -> None:
    assert is_valid_evidence_ref(value)


@pytest.mark.parametrize(
    "value",
    [
        "sha256:" + _VALID_HEX.upper(),
        "sha256:" + _VALID_HEX[:-1],
        "sha256:" + _VALID_HEX + "0",
        "SHA256:" + _VALID_HEX,
        "sha1:" + _VALID_HEX,
        "sha256-" + _VALID_HEX,
        _VALID_HEX,
        " sha256:" + _VALID_HEX,
        "sha256:" + _VALID_HEX + "\n",
        "sha256:" + _VALID_HEX[:-1] + "g",
        "free text evidence",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJl",
        "https://broker.example/orders/1?token=abc",
        "user@example.com",
        "api_key=" + _VALID_HEX,
        "",
        None,
        123,
        b"sha256:" + _VALID_HEX.encode(),
    ],
)
def test_evidence_ref_rejects_everything_else(value: object) -> None:
    assert not is_valid_evidence_ref(value)


def test_invalid_evidence_ref_is_denied_before_any_write_and_never_echoed() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJl"
    rig = Rig()
    outcome = rig.run(_inputs(reconciliation=_evidence(ref=jwt)))
    assert outcome.reason_codes == ("reconciliation_evidence_ref_invalid",)
    assert jwt not in repr(outcome)
    assert rig.durable_writes() == []


# --------------------------------------------------------------------------- crash / failure points


def test_marker_failure_prevents_transmission_and_keeps_the_key_blocking() -> None:
    rig = Rig()
    rig.store.raise_on["record_attempt"] = DedupMarkerRefusedError("lost_race")
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.MARKER_FAILED and rig.adapter.calls == []
    assert rig.key_state() is DedupKeyState.CLAIMED_NOT_ATTEMPTED
    rig.store.raise_on.clear()
    assert rig.run(_inputs()).reason_codes == ("dedup_unresolved:CLAIMED_NOT_ATTEMPTED",)
    assert rig.adapter.calls == []


def test_unverified_attempt_marker_prevents_transmission() -> None:
    rig = Rig()
    rig.store.hooks["record_attempt"] = lambda *a, **k: None  # "returns normally", not durable
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.MARKER_FAILED and rig.adapter.calls == []


def test_last_look_failure_aborts_the_claim_and_never_transmits() -> None:
    clock = {"now": NOW}
    rig = Rig(clock=lambda: clock["now"])

    def claim_then_time_passes(key: str) -> ClaimOutcome:
        outcome = rig.inner.claim(key)
        clock["now"] = NOW + timedelta(seconds=120)
        return outcome

    rig.store.hooks["claim"] = claim_then_time_passes
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.ABORTED
    assert outcome.reason_codes == ("last_look:reconciliation_evidence_stale",)
    assert rig.adapter.calls == [] and "record_attempt" not in rig.log
    assert rig.key_state() is DedupKeyState.ABORTED_NEVER_ATTEMPTED  # stays claimed (OPEN-14)
    assert rig.run(_inputs()).status is PipelineStatus.DENIED


def test_abort_marker_failure_is_reported_and_still_does_not_transmit() -> None:
    clock = {"now": NOW}
    rig = Rig(clock=lambda: clock["now"])

    def claim_then_time_passes(key: str) -> ClaimOutcome:
        outcome = rig.inner.claim(key)
        clock["now"] = NOW + timedelta(seconds=120)
        return outcome

    rig.store.hooks["claim"] = claim_then_time_passes
    rig.store.raise_on["record_abort"] = DedupStoreIOError("io")
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.ABORTED
    assert "abort_marker_failed" in outcome.reason_codes and rig.adapter.calls == []


def test_submit_raising_leaves_attempted_no_result_and_blocks_without_retry() -> None:
    def boom(request: ExecutionRequest) -> ExecutionResult:
        raise RuntimeError("secret broker payload text")

    rig = Rig(behavior=boom)
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.SUBMIT_RAISED and outcome.unknown_pending
    assert "secret" not in repr(outcome)
    assert rig.key_state() is DedupKeyState.ATTEMPTED_NO_RESULT
    assert "record_result" not in rig.log
    assert rig.run(_inputs()).reason_codes == ("dedup_unresolved:ATTEMPTED_NO_RESULT",)
    assert len(rig.adapter.calls) == 1


def test_result_persist_failure_surfaces_unknown_pending_and_applies_nothing() -> None:
    rig = Rig()
    rig.store.raise_on["record_result"] = DedupStoreIOError("io")
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.RESULT_PERSIST_FAILED
    assert outcome.unknown_pending and not outcome.result_persisted
    assert outcome.lifecycle is LifecycleStatus.NOT_RUN and outcome.next_position is None
    assert rig.key_state() is DedupKeyState.ATTEMPTED_NO_RESULT


def test_result_without_durable_attempt_is_rejected_and_not_recorded() -> None:
    rig = Rig()
    seen = {"submitted": False}
    rig.adapter.on_submit = lambda: seen.__setitem__("submitted", True)
    real_inspect = rig.inner.inspect

    def hide_attempt_after_submit(key: str) -> DedupKeyStatus:
        status: DedupKeyStatus = real_inspect(key)
        if seen["submitted"]:
            return DedupKeyStatus(
                idempotency_key=key,
                state=DedupKeyState.CLAIMED_NOT_ATTEMPTED,
                generation=status.generation,
                latest_result=None,
                violation_code=None,
            )
        return status

    rig.store.hooks["inspect"] = hide_attempt_after_submit
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.RESULT_REJECTED
    assert outcome.reason_codes == ("result_without_durable_attempt",)
    assert "record_result" not in rig.log and not outcome.result_persisted


def test_step8_refuses_a_result_when_the_key_only_has_a_claim() -> None:
    rig = Rig()
    key = execution_request_idempotency_key(_intent(TradeIntentKind.CLOSE))
    rig.inner.claim(key)  # CLAIMED_NOT_ATTEMPTED: legacy store would accept record_result
    request = ExecutionRequest(
        request_id="req-1",
        idempotency_key=key,
        intent_proposal_id="p-1",
        origin_ref="o",
        instrument_id="inst-1",
        side="long",
        action=TradeIntentKind.CLOSE,
        quantity=Decimal("1.00"),
        position_ref="pos-1",
        created_at=NOW,
    )
    persisted, codes, released = rig.pipeline._step8_persist(key, _filled(request))
    assert (persisted, codes, released) == (False, ("result_without_durable_attempt",), False)
    assert rig.inner.lookup(key) is not None and rig.inner.state(key) is (
        DedupKeyState.CLAIMED_NOT_ATTEMPTED
    )


@pytest.mark.parametrize("kind", ["legacy_no_action", "not_a_result", "wrong_position"])
def test_inconsistent_adapter_result_is_persisted_as_unknown(kind: str) -> None:
    def behavior(request: ExecutionRequest) -> Any:
        good = _filled(request)
        if kind == "not_a_result":
            return {"status": "FILLED"}
        if kind == "legacy_no_action":
            return ExecutionResult(
                result_id="r-1",
                request_ref=request.request_id,
                status=ExecutionStatus.FILLED,
                requested_quantity=request.quantity,
                filled_quantity=request.quantity or Decimal(0),
                remaining_quantity=Decimal(0),
                observed_at=NOW,
            )
        return ExecutionResult(
            result_id="r-1",
            request_ref=request.request_id,
            status=ExecutionStatus.FILLED,
            requested_quantity=good.requested_quantity,
            filled_quantity=good.filled_quantity,
            remaining_quantity=Decimal(0),
            observed_at=NOW,
            action=request.action,
            nexora_position_ref="someone-else",
        )

    rig = Rig(behavior=behavior)
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.COMPLETED
    assert outcome.result is not None and outcome.result.status is ExecutionStatus.UNKNOWN
    assert outcome.result.reason_code == "result_inconsistent_with_request"
    assert rig.key_state() is DedupKeyState.RESULT_UNSAFE
    assert outcome.next_position is None


def test_clean_rejected_is_released_but_never_resubmitted() -> None:
    rig = Rig(behavior=lambda r: _result(r, ExecutionStatus.REJECTED))
    first = rig.run(_inputs())
    assert first.status is PipelineStatus.COMPLETED and first.released
    again = rig.run(_inputs())  # explicit second submission: same-key retry is OPEN-15
    assert again.reason_codes == ("dedup_same_key_retry_not_supported",)
    assert len(rig.adapter.calls) == 1


def test_partial_and_accepted_results_stay_unsafe_and_unchanged() -> None:
    for status, fill in (
        (ExecutionStatus.PARTIALLY_FILLED, "0.40"),
        (ExecutionStatus.ACCEPTED, None),
    ):
        rig = Rig(behavior=lambda r, s=status, f=fill: _result(r, s, fill=f))  # type: ignore[misc]
        outcome = rig.run(_inputs())
        assert outcome.lifecycle is LifecycleStatus.UNCHANGED and not outcome.released
        assert rig.key_state() is DedupKeyState.RESULT_UNSAFE


# --------------------------------------------------------------------------- lifecycle exceptions


def _completed_with_lifecycle_error(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> tuple[PipelineOutcome, Rig]:
    def raiser(*args: Any, **kwargs: Any) -> PositionRecord:
        raise error

    monkeypatch.setattr(pipeline_module, "apply_execution_result", raiser)
    rig = Rig()
    return rig.run(_inputs()), rig


def test_stale_lifecycle_result_requires_reconciliation_and_never_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, rig = _completed_with_lifecycle_error(
        monkeypatch, PositionInputError("illegal_position_state_transition")
    )
    assert outcome.status is PipelineStatus.COMPLETED and outcome.result_persisted
    assert outcome.lifecycle is LifecycleStatus.RECONCILIATION_REQUIRED
    assert outcome.next_position is None and outcome.post_execution_reconciliation_required
    assert len(rig.adapter.calls) == 1 and rig.durable_writes().count("claim") == 1
    assert rig.run(_inputs()).status is PipelineStatus.DENIED  # still no retry/reclaim


def test_recovery_denial_is_distinct_from_a_stale_result_even_as_a_subclass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecoveryAuthorizationNotResolvedError(PositionInputError):
        """Stand-in for the PR-3 class (not on this base); only ``.code`` is relied on."""

    outcome, _ = _completed_with_lifecycle_error(
        monkeypatch, RecoveryAuthorizationNotResolvedError(RECOVERY_AUTHORIZATION_NOT_RESOLVED)
    )
    assert outcome.lifecycle is LifecycleStatus.RECOVERY_DENIED
    assert outcome.reason_codes == (RECOVERY_AUTHORIZATION_NOT_RESOLVED,)


def test_unexpected_lifecycle_exception_is_reconciliation_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, _ = _completed_with_lifecycle_error(monkeypatch, RuntimeError("x"))
    assert outcome.lifecycle is LifecycleStatus.RECONCILIATION_REQUIRED


# --------------------------------------------------------------------------- determinism / purity


def test_runs_are_deterministic_and_inputs_are_not_mutated() -> None:
    inputs = _inputs()
    snapshot = copy.deepcopy(inputs)
    first = Rig().run(inputs)
    second = Rig().run(inputs)
    assert first == second
    assert inputs == snapshot
    assert first.request == second.request


def test_pipeline_has_no_ambient_clock() -> None:
    source = PIPELINE_SOURCE.read_text(encoding="utf-8")
    for token in ("datetime.now", "datetime.utcnow", "time.time", "time.monotonic", "date.today"):
        assert token not in source


def test_naive_clock_denies() -> None:
    rig = Rig(clock=lambda: datetime(2026, 1, 1))
    assert rig.run(_inputs()).reason_codes == ("clock_requires_timezone",)
    assert rig.durable_writes() == []


# --------------------------------------------------------------------------- security delta


def _status(key: str, state: DedupKeyState, generation: int | None) -> DedupKeyStatus:
    return DedupKeyStatus(
        idempotency_key=key,
        state=state,
        generation=generation,
        latest_result=None,
        violation_code=None,
    )


def test_released_generation_claim_race_never_transmits_deterministic() -> None:
    """A runs claim->attempt->submit->clean REJECTED->release between B's step-1 gate and
    B's claim; B's claim then wins generation 1 and must NOT transmit (OPEN-14/15)."""

    rig_b = Rig()
    a_adapter = FakeAdapterForTestsOnly([], lambda r: _result(r, ExecutionStatus.REJECTED))
    a_pipeline = ExecutionPipeline(
        dedup_store=rig_b.inner,
        clock=lambda: NOW,
        instrument_resolver={"SYM": "inst-1"}.get,
        max_reconciliation_evidence_age=BOUND,
        max_preflight_age=BOUND,
        transmission=non_production_transmission_seam(
            a_adapter, preflight=AllowPreflightForTestsOnly([])
        ),
    )
    key = execution_request_idempotency_key(_intent(TradeIntentKind.CLOSE))
    ran: list[PipelineOutcome] = []

    def a_runs_first_then_b_claims(k: str) -> ClaimOutcome:
        ran.append(a_pipeline.run(_inputs()))
        return rig_b.inner.claim(k)

    rig_b.store.hooks["claim"] = a_runs_first_then_b_claims
    outcome = rig_b.run(_inputs())
    assert ran[0].released and len(a_adapter.calls) == 1
    assert outcome.status is PipelineStatus.DENIED
    assert outcome.reason_codes == ("released_generation_not_retried",)
    assert rig_b.adapter.calls == [] and "record_attempt" not in rig_b.log
    assert "record_abort" in rig_b.log
    assert rig_b.inner.state(key) is DedupKeyState.ABORTED_NEVER_ATTEMPTED


def test_post_claim_inspect_failure_aborts_and_never_transmits() -> None:
    rig = Rig()
    calls = {"n": 0}
    real = rig.inner.inspect

    def flaky(key: str) -> DedupKeyStatus:
        calls["n"] += 1
        if calls["n"] == 2:  # 1 = own-key gate, 2 = post-claim check
            raise DedupStoreIOError("io")
        status: DedupKeyStatus = real(key)
        return status

    rig.store.hooks["inspect"] = flaky
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.DENIED
    assert outcome.reason_codes == ("dedup_post_claim_inspect_failed",)
    assert rig.adapter.calls == [] and "record_attempt" not in rig.log
    assert "record_abort" in rig.log


def test_pre_submit_reinspect_also_requires_the_first_generation() -> None:
    rig = Rig()
    calls = {"n": 0}
    real = rig.inner.inspect

    def third_inspect_reports_generation_one(key: str) -> DedupKeyStatus:
        calls["n"] += 1
        status: DedupKeyStatus = real(key)
        if calls["n"] == 3:  # 3 = re-inspect after the attempt marker
            return _status(key, DedupKeyState.ATTEMPTED_NO_RESULT, 1)
        return status

    rig.store.hooks["inspect"] = third_inspect_reports_generation_one
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.MARKER_FAILED and rig.adapter.calls == []


class _HostileEvidence(ReconciliationEvidence):
    """Returns a valid ref on the first read and a hostile value afterwards (TOCTOU)."""

    reads = 0

    @property
    def evidence_ref(self) -> str:
        type(self).reads += 1
        return EVIDENCE_REF if type(self).reads == 1 else "HOSTILE api_key=secret"


def test_evidence_toctou_hostile_subclass_never_reaches_the_store() -> None:
    hostile = object.__new__(_HostileEvidence)
    object.__setattr__(hostile, "records", (_match(),))
    object.__setattr__(hostile, "observed_at", NOW)
    rig = Rig()
    outcome = rig.run(_inputs(reconciliation=hostile))
    assert outcome.reason_codes == ("reconciliation_evidence_missing",)
    assert rig.durable_writes() == [] and rig.store.attempt_kwargs == []
    assert "HOSTILE" not in repr(outcome)


def test_evidence_is_snapshotted_so_later_steps_use_the_validated_values() -> None:
    rig = Rig()
    rig.run(_inputs())
    (kwargs,) = rig.store.attempt_kwargs
    assert kwargs["reconciliation_evidence_ref"] == EVIDENCE_REF


def test_record_subclass_in_evidence_is_rejected() -> None:
    class SneakyRecord(ReconciliationRecord):
        pass

    sneaky = SneakyRecord(
        position_ref="pos-1",
        finding=ReconciliationFinding.MATCH,
        local_quantity=Decimal("1.00"),
        broker_quantity=Decimal("1.00"),
        observed_at=NOW,
    )
    rig = Rig()
    outcome = rig.run(_inputs(reconciliation=_evidence(sneaky)))
    assert outcome.reason_codes == ("reconciliation_evidence_records_invalid",)
    assert rig.durable_writes() == []


class _HostileResult(ExecutionResult):
    @property
    def action(self) -> Any:
        raise RuntimeError("hostile attribute access with broker payload")


def test_post_submit_hostile_result_is_unknown_pending_not_a_plain_denial() -> None:
    def hostile(request: ExecutionRequest) -> Any:
        good = _filled(request)
        obj = object.__new__(_HostileResult)
        for name in (
            "result_id",
            "request_ref",
            "status",
            "requested_quantity",
            "filled_quantity",
            "remaining_quantity",
            "observed_at",
        ):
            object.__setattr__(obj, name, getattr(good, name))
        return obj

    rig = Rig(behavior=hostile)
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.RESULT_PERSIST_FAILED
    assert outcome.reason_codes == ("post_submit_processing_failed",)
    assert outcome.unknown_pending and outcome.post_execution_reconciliation_required
    assert "payload" not in repr(outcome)
    assert rig.key_state() is DedupKeyState.ATTEMPTED_NO_RESULT
    assert rig.run(_inputs()).status is PipelineStatus.DENIED and len(rig.adapter.calls) == 1


def test_post_submit_failure_in_retry_safety_check_is_unknown_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(result: ExecutionResult) -> bool:
        raise RuntimeError("x")

    monkeypatch.setattr(pipeline_module, "is_safe_to_retry_without_reconciliation", boom)
    rig = Rig(behavior=lambda r: _result(r, ExecutionStatus.REJECTED))
    outcome = rig.run(_inputs())
    assert outcome.status is PipelineStatus.RESULT_PERSIST_FAILED
    assert outcome.unknown_pending and outcome.post_execution_reconciliation_required
    assert len(rig.adapter.calls) == 1


class _StatefulTuple(tuple[Any, ...]):
    """Tuple subclass whose iteration changes between reads (check/use divergence)."""

    reads = 0

    def __iter__(self) -> Any:
        type(self).reads += 1
        return tuple.__iter__(self) if type(self).reads == 1 else iter(())


class _LyingDatetime(datetime):
    """Reports a zero age on subtraction although it is really 5 hours stale."""

    def __rsub__(self, other: Any) -> timedelta:
        return timedelta(0)


def test_records_tuple_subclass_with_stateful_iter_is_denied_before_any_write() -> None:
    rig = Rig()
    records = _StatefulTuple((_match(),))
    outcome = rig.run(_inputs(reconciliation=_evidence_raw(records)))
    assert outcome.reason_codes == ("reconciliation_evidence_records_invalid",)
    assert rig.durable_writes() == [] and rig.adapter.calls == []
    assert rig.preflight.calls == 0


def test_datetime_subclass_with_lying_sub_cannot_make_stale_evidence_fresh() -> None:
    stale = _LyingDatetime(2026, 10, 5, 7, 0, tzinfo=UTC)  # real age vs NOW: 5 hours
    assert NOW - stale == timedelta(0)  # the lie works on a naive subtraction
    rig = Rig()
    record = ReconciliationRecord(
        position_ref="pos-1",
        finding=ReconciliationFinding.MATCH,
        local_quantity=Decimal("1.00"),
        broker_quantity=Decimal("1.00"),
        observed_at=stale,
    )
    evidence = ReconciliationEvidence(
        records=(record,), evidence_ref=EVIDENCE_REF, observed_at=stale
    )
    outcome = rig.run(_inputs(reconciliation=evidence))
    assert outcome.reason_codes == ("reconciliation_evidence_observed_at_invalid",)
    assert rig.durable_writes() == [] and rig.adapter.calls == [] and rig.preflight.calls == 0


def test_plain_exact_tuple_and_datetime_are_still_accepted() -> None:
    assert type(_evidence().records) is tuple and type(_evidence().observed_at) is datetime
    assert Rig().run(_inputs()).status is PipelineStatus.COMPLETED


def _evidence_raw(records: Any) -> ReconciliationEvidence:
    return ReconciliationEvidence(records=records, evidence_ref=EVIDENCE_REF, observed_at=NOW)


class _LyingRecordDatetime(datetime):
    """Record-level observed_at that lies in every comparison/subtraction."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = datetime.__hash__

    def __sub__(self, other: Any) -> Any:
        return timedelta(0)

    def __rsub__(self, other: Any) -> timedelta:
        return timedelta(0)


def _lying_record_evidence() -> ReconciliationEvidence:
    lying = _LyingRecordDatetime(2026, 10, 5, 7, 0, tzinfo=UTC)  # really 5h stale
    record = ReconciliationRecord(
        position_ref="pos-1",
        finding=ReconciliationFinding.MATCH,
        local_quantity=Decimal("1.00"),
        broker_quantity=Decimal("1.00"),
        observed_at=lying,
    )
    # evidence-level observed_at is exact and fresh; only the record-level value lies
    return ReconciliationEvidence(records=(record,), evidence_ref=EVIDENCE_REF, observed_at=NOW)


def test_record_level_datetime_subclass_denied_in_seam_composition() -> None:
    rig = Rig()
    outcome = rig.run(_inputs(reconciliation=_lying_record_evidence()))
    assert outcome.reason_codes == ("reconciliation_evidence_records_invalid",)
    assert outcome.trace == ("1",)
    assert rig.durable_writes() == [] and rig.adapter.calls == [] and rig.preflight.calls == 0
    assert "preflight" not in rig.log and "resolve" not in rig.log


def test_record_level_datetime_subclass_denied_in_production_composition(tmp_path: Path) -> None:
    log: list[str] = []
    pipeline, _ = _production_pipeline(tmp_path, log)
    outcome = pipeline.run(_inputs(reconciliation=_lying_record_evidence()))
    assert outcome.reason_codes == ("reconciliation_evidence_records_invalid",)
    assert outcome.trace == ("1",)
    for write in ("claim", "record_attempt", "record_abort", "record_result", "release_for_retry"):
        assert write not in log


def test_record_with_plain_exact_datetime_is_still_accepted() -> None:
    assert type(_match().observed_at) is datetime
    assert Rig().run(_inputs()).status is PipelineStatus.COMPLETED
