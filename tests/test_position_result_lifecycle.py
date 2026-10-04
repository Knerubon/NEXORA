"""Tests for the pure result-driven lifecycle helper (ADR-035 s3.11, PR-8)."""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import nexora.position.result_lifecycle as module
import pytest
from nexora.autonomous_contracts import TradeIntentKind, TradeState
from nexora.execution.models import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    ProtectionRequest,
)
from nexora.position.models import (
    ExitDecision,
    PositionInputError,
    PositionRecord,
    ProtectionLevels,
)
from nexora.position.result_lifecycle import apply_execution_result
from nexora.position.supervisor import apply_exit_decision, mark_closed
from nexora.signals.models import SignalEvidence

NOW = datetime(2026, 1, 1, tzinfo=UTC)
D = Decimal
K = TradeIntentKind
S = ExecutionStatus


def _position(
    state: TradeState = TradeState.OPEN, quantity: str = "2", side: str = "long"
) -> PositionRecord:
    if state in (TradeState.EXIT_PENDING, TradeState.CLOSED):
        quantity = "0"
    return PositionRecord(
        position_id="pos:1",
        symbol="XAUUSD",
        side=side,  # type: ignore[arg-type]
        state=state,
        quantity=D(quantity),
        entry_price=D("2000"),
        protection=ProtectionLevels(stop_price=D("1900"), target_prices=(D("2100"),)),
        opened_at=NOW,
        source_signal_decision_ref="sd:1",
        source_entry_readiness_ref="er:1",
    )


def _request(
    action: K = K.CLOSE,
    quantity: str | None = None,
    position_ref: str | None = "pos:1",
    side: str = "long",
) -> ExecutionRequest:
    protection = ProtectionRequest(stop_price=D("1950")) if action is K.MODIFY_PROTECTION else None
    return ExecutionRequest(
        request_id="req:1",
        idempotency_key="idem:1",
        intent_proposal_id="prop:1",
        origin_ref="origin:1",
        instrument_id="XAUUSD",
        side=side,  # type: ignore[arg-type]
        action=action,
        quantity=None if quantity is None else D(quantity),
        protection=protection,
        position_ref=position_ref,
        created_at=NOW,
    )


def _result(
    action: K | None = K.CLOSE,
    status: S = S.FILLED,
    requested: str = "2",
    request_ref: str = "req:1",
    position_ref: str | None = None,
) -> ExecutionResult:
    if action is K.MODIFY_PROTECTION:
        return ExecutionResult(
            result_id="res:1",
            request_ref=request_ref,
            status=status,
            requested_quantity=None,
            observed_at=NOW,
            action=action,
            nexora_position_ref=position_ref,
        )
    req = D(requested)
    if status is S.FILLED:
        fill = req
    elif status is S.PARTIALLY_FILLED:
        fill = D("1")
    else:
        fill = D("0")
    remaining = None if status is S.UNKNOWN else req - fill
    return ExecutionResult(
        result_id="res:1",
        request_ref=request_ref,
        status=status,
        requested_quantity=req,
        filled_quantity=fill,
        remaining_quantity=remaining,
        observed_at=NOW,
        action=action,
        nexora_position_ref=position_ref,
    )


def _oracle(action: str, reduce_quantity: str | None = None) -> ExitDecision:
    evidence = (
        SignalEvidence(
            component="structure",
            code="oracle",
            points=0,
            polarity="neutral",
            reason="oracle evidence",
            source_refs=("e:1",),
        ),
    )
    return ExitDecision(
        decision_id="xd:1",
        position_id="pos:1",
        action=action,  # type: ignore[arg-type]
        evidence=evidence,
        source_refs=("r:1",),
        decided_at=NOW,
        reduce_quantity=None if reduce_quantity is None else D(reduce_quantity),
    )


# ---------------------------------------------------------------- applied cases


@pytest.mark.parametrize("state", [TradeState.OPEN, TradeState.MANAGING])
def test_lifecycle_applied_without_exit_decision(state: TradeState) -> None:
    pos = _position(state)
    closed = apply_execution_result(pos, _request(K.CLOSE), _result(K.CLOSE))
    assert closed.state is TradeState.CLOSED
    assert closed.quantity == 0
    reduced = apply_execution_result(pos, _request(K.REDUCE, "1"), _result(K.REDUCE, requested="1"))
    assert reduced.state is TradeState.MANAGING
    assert reduced.quantity == D("1")


def test_no_fabricated_exit_decision() -> None:
    params = inspect.signature(apply_execution_result).parameters
    assert list(params) == ["position", "request", "result"]
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            assert name not in ("ExitDecision", "apply_exit_decision")
        if isinstance(node, ast.Import | ast.ImportFrom):
            imported = [a.name for a in node.names]
            assert "ExitDecision" not in imported
            assert "apply_exit_decision" not in imported


def test_result_driven_application_equals_exit_decision_application() -> None:
    for state in (TradeState.OPEN, TradeState.MANAGING):
        pos = _position(state)
        oracle_close = mark_closed(apply_exit_decision(pos, _oracle("CLOSE")))
        assert apply_execution_result(pos, _request(K.CLOSE), _result(K.CLOSE)) == oracle_close
        oracle_reduce = apply_exit_decision(pos, _oracle("PARTIAL_CLOSE", "1"))
        got = apply_execution_result(pos, _request(K.REDUCE, "1"), _result(K.REDUCE, requested="1"))
        assert got == oracle_reduce


def test_algorithmic_origin_uses_same_path() -> None:
    # Origin is not an input: a request with a PositionOrigin-style origin_ref goes
    # through the identical function and yields the identical record.
    pos = _position()
    manual = apply_execution_result(pos, _request(K.CLOSE), _result(K.CLOSE))
    algo_req = replace(_request(K.CLOSE), origin_ref="position:pos:1:exit:xd:1")
    assert apply_execution_result(pos, algo_req, _result(K.CLOSE)) == manual


def test_close_with_explicit_request_quantity() -> None:
    out = apply_execution_result(_position(), _request(K.CLOSE, "2"), _result(K.CLOSE))
    assert out.state is TradeState.CLOSED


# ----------------------------------------------------------------------- purity


def test_purity_inputs_unchanged_and_new_record() -> None:
    pos = _position()
    before = replace(pos)
    out = apply_execution_result(pos, _request(K.CLOSE), _result(K.CLOSE))
    assert pos == before
    assert pos.state is TradeState.OPEN
    assert pos.quantity == D("2")
    assert out is not pos


def test_purity_no_io_imports() -> None:
    tree = ast.parse(inspect.getsource(module))
    forbidden = {"MetaTrader5", "socket", "http", "urllib", "requests", "subprocess", "sqlite3"}
    forbidden_nexora = ("nexora.storage", "nexora.autonomous.mt5", "nexora.execution.dedup_store")
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for n in names:
            assert n.split(".")[0] not in forbidden, n
            assert not any(n.startswith(f) for f in forbidden_nexora), n


# --------------------------------------------------------------------- no-ops


@pytest.mark.parametrize("action", [K.CLOSE, K.REDUCE])
@pytest.mark.parametrize("status", [S.PARTIALLY_FILLED, S.ACCEPTED, S.UNKNOWN, S.REJECTED])
def test_unapplied_statuses_return_identical_position(action: K, status: S) -> None:
    pos = _position(TradeState.MANAGING)
    req_qty = "2" if action is K.REDUCE else None
    out = apply_execution_result(
        pos, _request(action, req_qty), _result(action, status, requested="2")
    )
    assert out is pos


@pytest.mark.parametrize("status", [S.ACCEPTED, S.REJECTED, S.UNKNOWN])
def test_modify_protection_never_applied(status: S) -> None:
    pos = _position()
    out = apply_execution_result(
        pos, _request(K.MODIFY_PROTECTION), _result(K.MODIFY_PROTECTION, status)
    )
    assert out is pos


# ----------------------------------------------------------------- fail closed


def _fails(code: str, pos: PositionRecord, req: ExecutionRequest, res: ExecutionResult) -> None:
    with pytest.raises(PositionInputError) as info:
        apply_execution_result(pos, req, res)
    assert info.value.code == code


def test_open_action_fails_closed() -> None:
    req = ExecutionRequest(
        request_id="req:1",
        idempotency_key="i",
        intent_proposal_id="p",
        origin_ref="o",
        instrument_id="XAUUSD",
        side="long",
        action=K.OPEN,
        quantity=D("2"),
        created_at=NOW,
    )
    _fails("open_result_not_applicable", _position(), req, _result(K.OPEN))


def test_legacy_result_without_action_fails_closed() -> None:
    _fails("result_action_required", _position(), _request(), _result(None))


@pytest.mark.parametrize(
    "state", [TradeState.EXIT_PENDING, TradeState.CLOSED, TradeState.EMERGENCY]
)
def test_non_open_managing_position_fails_closed(state: TradeState) -> None:
    pos = _position(state)
    _fails("position_not_open_or_managing", pos, _request(), _result())
    _fails(
        "position_not_open_or_managing",
        pos,
        _request(K.MODIFY_PROTECTION),
        _result(K.MODIFY_PROTECTION, S.ACCEPTED),
    )


def test_mismatches_fail_closed() -> None:
    pos = _position()
    _fails("result_request_mismatch", pos, _request(), _result(request_ref="other"))
    _fails("result_action_mismatch", pos, _request(K.CLOSE), _result(K.REDUCE, requested="1"))
    _fails("request_position_mismatch", pos, _request(position_ref="pos:2"), _result())
    _fails("result_position_mismatch", pos, _request(), _result(position_ref="pos:2"))
    _fails("request_side_mismatch", pos, _request(side="short"), _result())
    _fails("result_requested_quantity_mismatch", pos, _request(K.CLOSE, "1"), _result())
    ok = apply_execution_result(pos, _request(), _result(position_ref="pos:1"))
    assert ok.state is TradeState.CLOSED


def test_wrong_quantities_fail_closed() -> None:
    _fails("close_fill_quantity_mismatch", _position(quantity="3"), _request(), _result())
    pos = _position(quantity="2")
    _fails(
        "reduce_fill_quantity_invalid",
        pos,
        _request(K.REDUCE, "2"),
        _result(K.REDUCE, requested="2"),
    )
    _fails(
        "reduce_fill_quantity_invalid",
        pos,
        _request(K.REDUCE, "3"),
        _result(K.REDUCE, requested="3"),
    )


def test_reduce_to_zero_refused_like_exit_decision() -> None:
    pos = _position(quantity="2")
    with pytest.raises(PositionInputError):
        apply_exit_decision(pos, _oracle("PARTIAL_CLOSE", "2"))
    _fails(
        "reduce_fill_quantity_invalid",
        pos,
        _request(K.REDUCE, "2"),
        _result(K.REDUCE, requested="2"),
    )


# ---------------------------------------------------- OPEN-19 (current behavior)


def test_open19_current_behavior_documented_not_policy() -> None:
    # OPEN-19 is UNRESOLVED. This asserts what the memoryless helper does today;
    # it is NOT a decision that this behavior is correct or acceptable.
    closed = apply_execution_result(_position(quantity="2"), _request(), _result())
    _fails("position_not_open_or_managing", closed, _request(), _result())
    req, res = _request(K.REDUCE, "1"), _result(K.REDUCE, requested="1")
    once = apply_execution_result(_position(quantity="3"), req, res)
    twice = apply_execution_result(once, req, res)
    assert (once.quantity, twice.quantity) == (D("2"), D("1"))
