"""Decision Audit Trail V1 (ADR-036): mandatory, append-only, deterministic, fail-closed."""

from __future__ import annotations

import ast
import copy
import inspect
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import nexora.data_quality as data_quality_pkg
import nexora.decision_audit as audit_pkg
import pytest
from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.data_quality import (
    DataQualityGuard,
    MarketDataSnapshot,
    QualityExpectation,
    QualityGuardConfig,
)
from nexora.decision_audit import (
    AppendOutcome,
    AuditError,
    AuditGateOutcome,
    DecisionAuditGate,
    DecisionAuditRecord,
    DecisionAuditStore,
    build_decision_audit_record,
)
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research import ResearchPipeline
from nexora.storage import SQLiteJournal

from tests.validation_fixtures import pipeline_config, tick_events

QUALITY_CONFIG = QualityGuardConfig(
    version="dq-guard-test-v1",
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
EVENT_COUNT = 300


def _guard() -> DataQualityGuard:
    return DataQualityGuard(QUALITY_CONFIG, EXPECTED)


def _snap(event: NormalizedPriceEvent) -> MarketDataSnapshot:
    return MarketDataSnapshot((event,))


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[SQLiteJournal]:
    store = SQLiteJournal(tmp_path / "audit.sqlite")
    yield store
    store.close()


def _run(
    gate: DecisionAuditGate, count: int = EVENT_COUNT
) -> list[tuple[NormalizedPriceEvent, dict[str, Any], Any]]:
    pipeline = ResearchPipeline(pipeline_config())
    rows = []
    for event in tick_events(count):
        output = pipeline.process(event)
        result = gate.record_decision(
            output=output, event=event, snapshot=_snap(event), evaluated_at=event.received_at
        )
        rows.append((event, output, result))
    return rows


def _step(index: int) -> tuple[NormalizedPriceEvent, dict[str, Any]]:
    """Output of the pipeline after the event at zero-based ``index`` (fixture indices below)."""
    pipeline = ResearchPipeline(pipeline_config())
    events = tick_events(index + 1)
    output: dict[str, Any] = {}
    for event in events:
        output = pipeline.process(event)
    return events[-1], output


WAIT_INDEX, BUY_INDEX, SELL_INDEX = 0, 7, 12  # first of each action in the fixture


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


def _gate(journal: Any, environment: str = "development") -> DecisionAuditGate:
    return DecisionAuditGate(DecisionAuditStore(journal, environment=environment), _guard())


# 1. BUY/SELL/WAIT audit completeness ---------------------------------------------------------


def test_every_decision_is_audited_including_wait(journal: SQLiteJournal) -> None:
    gate = _gate(journal)
    rows = _run(gate)
    records = DecisionAuditStore(journal, environment="development").read("XAUUSD")
    assert len(records) == EVENT_COUNT  # one record per decision, WAIT included
    by_action = Counter(r.action for r in records)
    assert by_action["WAIT"] > 0 and by_action["BUY"] > 0 and by_action["SELL"] > 0
    assert all(result.record is not None for _, _, result in rows)
    for record in records:
        assert record.schema_version == 1
        assert record.decision_id and record.correlation_id.startswith("corr:")
        assert record.decided_at.utcoffset() == timedelta(0)  # timezone-aware UTC
        assert record.instrument.symbol == "XAUUSD" and record.instrument.source == "synthetic-test"
        assert record.market_data.event_identity_key and record.market_data.snapshot_hash
        assert record.versions.pipeline_config == "valid-test-v1"
        assert record.versions.signal_engine == "signal-v1:p8b-v2"
        assert record.versions.regime_config and record.versions.entry_readiness_config
        assert record.data_quality.state == "ok" and record.data_quality.snapshot_hash
        assert {e.component for e in record.evidence} >= {"regime", "structure"}


@pytest.mark.parametrize(
    ("index", "action"), [(WAIT_INDEX, "WAIT"), (BUY_INDEX, "BUY"), (SELL_INDEX, "SELL")]
)
def test_each_action_has_a_reconstructable_record(
    journal: SQLiteJournal, index: int, action: str
) -> None:
    event, output = _step(index)
    result = _gate(journal).record_decision(
        output=output, event=event, snapshot=_snap(event), evaluated_at=event.received_at
    )
    record = result.record
    assert record is not None and record.action == action
    assert record.market_data.event_identity_key == event.identity_key
    assert record.entry_readiness_state == output["entry_readiness"]["state"]
    assert record.future_conditions == tuple(output["signals"]["decision"]["future_conditions"])
    if action == "WAIT":
        assert record.signal_id is None and record.signal_emitted is False
        assert result.outcome is AuditGateOutcome.RECORDED_NOT_APPLICABLE
        assert result.new_trade_eligible is False
    else:
        latest = output["signals"]["latest"]
        assert record.signal_id == latest["signal_id"]  # copied, never invented
        assert record.signal_emitted is True
        assert record.reasons == tuple(latest["reasons"])
        assert record.reason_codes == tuple(latest["reason_codes"])
        assert result.outcome is AuditGateOutcome.RECORDED_ELIGIBLE
        assert result.new_trade_eligible is True


def test_wait_decisions_are_audited_even_when_quality_is_bad(journal: SQLiteJournal) -> None:
    event, output = _step(WAIT_INDEX)
    bad = replace(event, bid=D("101"), ask=D("100"))
    result = _gate(journal).record_decision(
        output=output, event=bad, snapshot=_snap(bad), evaluated_at=bad.received_at
    )
    assert result.record is not None and result.record.action == "WAIT"
    assert result.record.data_quality.state == "blocked"
    assert result.new_trade_eligible is False
    assert len(DecisionAuditStore(journal, environment="development").read("XAUUSD")) == 1


def test_signal_id_is_never_invented_for_non_emitted_actionable_decisions(
    journal: SQLiteJournal,
) -> None:
    event, output = _step(BUY_INDEX)
    stale = copy.deepcopy(output)
    stale["signals"]["latest"]["decision_time"] = "2000-01-01T00:00:00+00:00"
    record = (
        _gate(journal)
        .record_decision(
            output=stale, event=event, snapshot=_snap(event), evaluated_at=event.received_at
        )
        .record
    )
    assert record is not None
    assert record.signal_id is None and record.signal_emitted is False
    assert "signal:not_emitted" in record.evidence_gaps


# 2. Deterministic replay and stable correlation ---------------------------------------------


def test_replay_produces_identical_records_and_stable_ids(tmp_path: Path) -> None:
    runs = []
    for name in ("a", "b"):
        store_journal = SQLiteJournal(tmp_path / f"{name}.sqlite")
        _run(_gate(store_journal), 120)
        runs.append(DecisionAuditStore(store_journal, environment="development").read("XAUUSD"))
        store_journal.close()
    first, second = runs
    assert [canonical_hash(r) for r in first] == [canonical_hash(r) for r in second]
    assert [r.decision_id for r in first] == [r.decision_id for r in second]
    assert [r.correlation_id for r in first] == [r.correlation_id for r in second]
    assert len({r.decision_id for r in first}) == len(first)  # unique per decision


def test_replaying_into_the_same_store_is_idempotent(journal: SQLiteJournal) -> None:
    store = DecisionAuditStore(journal, environment="development")
    _run(DecisionAuditGate(store, _guard()), 60)
    before = store.read("XAUUSD")
    results = _run(DecisionAuditGate(store, _guard()), 60)
    assert store.read("XAUUSD") == before  # no duplicate rows, no overwrite
    assert all(r.outcome is not AuditGateOutcome.DENIED_AUDIT_FAILURE for _, _, r in results)


def test_correlation_is_stable_across_environments_but_decision_ids_are_not(
    journal: SQLiteJournal,
) -> None:
    event, output = _step(BUY_INDEX)
    dev = (
        _gate(journal, "development")
        .record_decision(
            output=output, event=event, snapshot=_snap(event), evaluated_at=event.received_at
        )
        .record
    )
    prod = (
        _gate(journal, "production")
        .record_decision(
            output=output, event=event, snapshot=_snap(event), evaluated_at=event.received_at
        )
        .record
    )
    assert dev is not None and prod is not None
    assert dev.correlation_id == prod.correlation_id
    assert dev.decision_id != prod.decision_id


# 3. Missing / invalid evidence ---------------------------------------------------------------


@pytest.mark.parametrize("missing", ["regime", "structure", "trendline", "entry_readiness"])
def test_missing_optional_evidence_is_recorded_not_fabricated(
    journal: SQLiteJournal, missing: str
) -> None:
    event, output = _step(BUY_INDEX)
    gate = _gate(journal)
    full = gate.record_decision(
        output=output, event=event, snapshot=_snap(event), evaluated_at=event.received_at
    ).record
    partial_output = {k: v for k, v in output.items() if k != missing}
    # A different environment keeps the second write from colliding with the first record.
    partial = (
        _gate(journal, "staging")
        .record_decision(
            output=partial_output,
            event=event,
            snapshot=_snap(event),
            evaluated_at=event.received_at,
        )
        .record
    )
    assert full is not None and partial is not None
    assert f"{missing}:absent" in partial.evidence_gaps
    assert f"{missing}:absent" not in full.evidence_gaps
    # The engine's own decision evidence is kept; only the references the builder derives
    # from the missing snapshot disappear, and nothing replaces them.
    removed = [e for e in full.evidence if e not in partial.evidence]
    assert all(e in full.evidence for e in partial.evidence)
    if missing in ("regime", "structure"):
        assert len(removed) == 1 and removed[0].component == missing
    assert len(partial.evidence) <= len(full.evidence)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o.pop("signals"),
        lambda o: o["signals"].pop("decision"),
        lambda o: o["signals"]["decision"].update(action="HOLD"),
        lambda o: o.pop("config_version"),
        lambda o: o["signals"].pop("sequence"),
        lambda o: o["signals"].update(symbol="EURUSD"),
    ],
)
def test_unreconstructable_decisions_are_denied_not_guessed(
    journal: SQLiteJournal, mutate: Any
) -> None:
    event, output = _step(BUY_INDEX)
    broken = copy.deepcopy(output)
    mutate(broken)
    result = _gate(journal).record_decision(
        output=broken, event=event, snapshot=_snap(event), evaluated_at=event.received_at
    )
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.new_trade_eligible is False and result.record is None
    assert DecisionAuditStore(journal, environment="development").read("XAUUSD") == ()


# 4. Audit write failure and fail-closed behavior ---------------------------------------------


@pytest.mark.parametrize("index", [WAIT_INDEX, BUY_INDEX, SELL_INDEX])
def test_audit_persistence_failure_denies_and_never_leaks(index: int) -> None:
    event, output = _step(index)
    gate = _gate(_FailingJournal(OSError("disk full password=hunter2")))
    result = gate.record_decision(
        output=output, event=event, snapshot=_snap(event), evaluated_at=event.received_at
    )
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.new_trade_eligible is False
    assert result.failure_code == "audit_persistence_failed"
    assert "hunter2" not in repr(result)


def test_identity_conflict_and_unexpected_errors_deny() -> None:
    event, output = _step(BUY_INDEX)
    conflict = _gate(_FailingJournal(ValueError("journal_identity_conflict")))
    assert (
        conflict.record_decision(
            output=output, event=event, snapshot=_snap(event), evaluated_at=event.received_at
        ).failure_code
        == "audit_identity_conflict"
    )
    boom = _gate(_FailingJournal(RuntimeError("anything")))
    result = boom.record_decision(
        output=output, event=event, snapshot=_snap(event), evaluated_at=event.received_at
    )
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE and not result.new_trade_eligible


def test_existing_record_cannot_be_overwritten(journal: SQLiteJournal) -> None:
    store = DecisionAuditStore(journal, environment="development")
    event, output = _step(BUY_INDEX)
    record = build_decision_audit_record(
        output=output,
        event=event,
        snapshot=_snap(event),
        guard=_guard(),
        evaluated_at=event.received_at,
        environment="development",
    )
    assert store.append(record) is AppendOutcome.CREATED
    assert store.append(record) is AppendOutcome.REPLAYED  # identical replay is a no-op
    tampered = replace(record, score=record.score + 1)
    with pytest.raises(AuditError) as caught:
        store.append(tampered)
    assert caught.value.code == "audit_identity_conflict"
    assert store.read("XAUUSD") == (record,)


def test_store_exposes_no_update_or_delete_surface() -> None:
    public = {n for n in dir(DecisionAuditStore) if not n.startswith("_")}
    assert public == {"append", "environment", "read", "stream"}


def test_poor_data_quality_denies_actionable_decisions_but_still_audits(
    journal: SQLiteJournal,
) -> None:
    event, output = _step(BUY_INDEX)
    bad = replace(event, bid=D("101"), ask=D("100"))  # crossed book
    result = _gate(journal).record_decision(
        output=output, event=bad, snapshot=_snap(bad), evaluated_at=bad.received_at
    )
    assert result.outcome is AuditGateOutcome.RECORDED_DENIED_DATA_QUALITY
    assert result.new_trade_eligible is False
    record = result.record
    assert record is not None and record.action == "BUY"  # engine output is not rewritten
    assert record.trade_eligibility == "denied_data_quality"
    assert "negative_spread" in record.data_quality.finding_codes
    assert any(b.source == "data_quality" and b.code == "negative_spread" for b in record.blockers)


@pytest.mark.parametrize("verdict_state", ["blocked", "unknown"])
def test_unknown_quality_also_denies(journal: SQLiteJournal, verdict_state: str) -> None:
    event, output = _step(BUY_INDEX)
    if verdict_state == "unknown":
        subject, at = event, None  # unsynchronized clock: validity cannot be established
    else:
        subject, at = replace(event, source="other-source"), event.received_at  # identity mismatch
    result = _gate(journal).record_decision(
        output=output, event=subject, snapshot=_snap(subject), evaluated_at=at
    )
    assert result.record is not None and result.record.data_quality.state == verdict_state
    assert result.outcome is AuditGateOutcome.RECORDED_DENIED_DATA_QUALITY
    assert result.new_trade_eligible is False


# Redaction ------------------------------------------------------------------------------------


def test_credential_like_content_is_rejected_not_persisted(journal: SQLiteJournal) -> None:
    event, output = _step(BUY_INDEX)
    leaky = copy.deepcopy(output)
    leaky["signals"]["decision"]["negative_evidence"] = [
        {
            "component": "pnf",
            "code": "x",
            "points": 1,
            "polarity": "bearish",
            "reason": "see postgres://trader:s3cr3t@db.internal/nexora",
            "source_refs": [],
        }
    ]
    result = _gate(journal).record_decision(
        output=leaky, event=event, snapshot=_snap(event), evaluated_at=event.received_at
    )
    assert result.outcome is AuditGateOutcome.DENIED_AUDIT_FAILURE
    assert result.failure_code == "audit_redaction_violation"
    assert "s3cr3t" not in repr(result)
    assert DecisionAuditStore(journal, environment="development").read("XAUUSD") == ()


# 8. DEV / PROD isolation ---------------------------------------------------------------------


def test_environment_streams_are_isolated_on_a_shared_journal(journal: SQLiteJournal) -> None:
    dev = DecisionAuditStore(journal, environment="development")
    prod = DecisionAuditStore(journal, environment="production")
    assert dev.stream("XAUUSD") != prod.stream("XAUUSD")
    assert dev.stream("XAUUSD") == "audit:v1:development:XAUUSD"
    _run(DecisionAuditGate(dev, _guard()), 20)
    assert len(dev.read("XAUUSD")) == 20
    assert prod.read("XAUUSD") == ()


def test_store_rejects_a_record_from_another_environment(journal: SQLiteJournal) -> None:
    event, output = _step(BUY_INDEX)
    record = build_decision_audit_record(
        output=output,
        event=event,
        snapshot=_snap(event),
        guard=_guard(),
        evaluated_at=event.received_at,
        environment="production",
    )
    with pytest.raises(AuditError) as caught:
        DecisionAuditStore(journal, environment="development").append(record)
    assert caught.value.code == "audit_environment_mismatch"
    with pytest.raises(AuditError, match="missing_environment"):
        DecisionAuditStore(journal, environment="")


# 9. Existing SignalEngine decisions are unchanged ---------------------------------------------


def test_signal_engine_decisions_are_pinned_on_the_fixture_and_untouched_by_audit(
    journal: SQLiteJournal,
) -> None:
    plain = ResearchPipeline(pipeline_config())
    plain_outputs = [plain.process(e) for e in tick_events(EVENT_COUNT)]
    counts = Counter(o["signals"]["decision"]["action"] for o in plain_outputs)
    assert counts == {"WAIT": 226, "BUY": 39, "SELL": 35}  # regression pin of existing behavior
    first: dict[str, int] = {}
    for i, o in enumerate(plain_outputs):
        first.setdefault(o["signals"]["decision"]["action"], i)
    assert first == {"WAIT": 0, "BUY": 7, "SELL": 12}

    gate = _gate(journal)
    audited = ResearchPipeline(pipeline_config())
    for event, expected in zip(tick_events(EVENT_COUNT), plain_outputs, strict=True):
        output = audited.process(event)
        snapshot = copy.deepcopy(output)
        gate.record_decision(
            output=output, event=event, snapshot=_snap(event), evaluated_at=event.received_at
        )
        assert output == snapshot  # the gate is read-only on engine output
        assert canonical_hash(output) == canonical_hash(expected)  # identical with/without audit


def test_audit_records_match_engine_output_not_a_recomputation(journal: SQLiteJournal) -> None:
    rows = _run(_gate(journal), 80)
    for _, output, result in rows:
        record = result.record
        decision = output["signals"]["decision"]
        assert record.action == decision["action"] and record.score == decision["score"]
        assert record.versions.signal_config == decision["config_version"]


def test_record_round_trips_through_canonical_storage(journal: SQLiteJournal) -> None:
    event, output = _step(BUY_INDEX)
    record = (
        _gate(journal)
        .record_decision(
            output=output,
            event=event,
            snapshot=_snap(event),
            evaluated_at=event.received_at,
            instrument_id="inst:xauusd",
            risk_authority_outcome="risk:ref-1",
            lifecycle_ref="lifecycle:ref-1",
        )
        .record
    )
    assert record is not None
    (stored,) = DecisionAuditStore(journal, environment="development").read("XAUUSD")
    assert stored == record and canonical_serialize(stored) == canonical_serialize(record)
    assert stored.instrument.instrument_id == "inst:xauusd"
    assert (stored.risk_authority_outcome, stored.lifecycle_ref) == (
        "risk:ref-1",
        "lifecycle:ref-1",
    )


# 10. Mandatory / no toggle / no execution --------------------------------------------------------

_FORBIDDEN_IMPORT_ROOTS = (
    "nexora.execution",
    "nexora.autonomous",
    "nexora.position",
    "nexora.paper",
    "nexora.risk",
    "nexora_api",
    "MetaTrader5",
    "requests",
    "httpx",
    "socket",
    "subprocess",
)


def _package_sources(package: Any) -> dict[str, str]:
    root = Path(package.__file__).parent
    return {p.name: p.read_text() for p in sorted(root.glob("*.py"))}


@pytest.mark.parametrize("package", [audit_pkg, data_quality_pkg])
def test_new_packages_cannot_reach_brokers_execution_network_or_environment(package: Any) -> None:
    for name, source in _package_sources(package).items():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                assert not module.startswith(_FORBIDDEN_IMPORT_ROOTS), f"{name} imports {module}"
        for forbidden in ("os.environ", "getenv", "order_send", "datetime.now", "utcnow"):
            assert forbidden not in source, f"{name} uses {forbidden}"


def test_audit_cannot_be_disabled_by_any_parameter_or_result_field() -> None:
    names = " ".join(
        [
            *inspect.signature(DecisionAuditGate.__init__).parameters,
            *inspect.signature(DecisionAuditGate.record_decision).parameters,
            *inspect.signature(DecisionAuditStore.__init__).parameters,
            *inspect.signature(build_decision_audit_record).parameters,
        ]
    ).lower()
    for word in ("enable", "disable", "skip", "bypass", "toggle", "dry_run", "optional"):
        assert word not in names
    public = [n for n in dir(DecisionAuditGate) if not n.startswith("_")]
    assert public == ["record_decision"]  # one entry point, no alternate or skip path


def test_gate_result_carries_eligibility_only_never_an_execution_capability() -> None:
    fields = set(inspect.signature(audit_pkg.AuditGateResult).parameters)
    assert fields == {"outcome", "new_trade_eligible", "record", "failure_code"}
    record_fields = set(DecisionAuditRecord.__dataclass_fields__)
    assert not {"order", "ticket", "transmission", "authorized"} & record_fields
    assert audit_pkg.AuditGateOutcome.RECORDED_ELIGIBLE.value == "recorded_eligible"
