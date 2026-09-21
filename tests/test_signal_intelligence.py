from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Literal, TypedDict

import pytest
from nexora.artifacts import canonical_serialize
from nexora.market_regime import RegimeSnapshot
from nexora.matrix import MatrixSnapshot
from nexora.signals import SignalEngine
from nexora.structure import StructureSnapshot

from tests.test_signals import NOW, _config, _matrix, _regime, _structure


class Inputs(TypedDict):
    structure: StructureSnapshot
    regime: RegimeSnapshot
    matrix: MatrixSnapshot


def inputs() -> Inputs:
    return dict(
        structure=_structure(
            pivots=(
                ("low", "99", "confirmed"),
                ("high", "104", "confirmed"),
                ("low", "99.8", "confirmed"),
            )
        ),
        regime=_regime("trend"),
        matrix=_matrix("aligned_bullish"),
    )


@pytest.mark.parametrize("buy,sell", [(0, 0), (78, 31), (80, 80), (100, 100), (100, 0), (0, 100)])
def test_strength_is_independent_of_legacy_score(buy: int, sell: int) -> None:
    from unittest.mock import patch

    engine = SignalEngine(_config())
    data = inputs()
    assessment = replace(engine._assess_components(**data), buy_points=buy, sell_points=sell)
    expected_action, expected_score = engine._resolve_action_and_score(buy, sell)
    with patch.object(SignalEngine, "_assess_components", return_value=assessment):
        # Guarantee a plan for either side without changing assessment or score.
        data["structure"] = replace(
            data["structure"],
            levels=_structure(
                pivots=(),
                levels=(("support", "99", "confirmed"), ("resistance", "110", "confirmed")),
            ).levels,
        )
        d = engine.evaluate(**data).decision
    assert (d.buy_strength, d.sell_strength) == (buy, sell)
    assert (d.action, d.score) == (expected_action, expected_score)
    assert d.strength_available


def test_strength_matches_real_internal_points_and_wait_is_evaluated() -> None:
    data = inputs()
    data["matrix"] = _matrix("mixed")
    engine = SignalEngine(_config())
    a = engine._assess_components(**data)
    d = engine.evaluate(**data).decision
    assert d.action == "WAIT" and d.strength_available
    assert (d.buy_strength, d.sell_strength) == (a.buy_points, a.sell_points)
    assert d.buy_strength is not None and d.sell_strength is not None
    assert d.buy_strength + d.sell_strength != 100
    assert 0 <= d.buy_strength <= 100 and 0 <= d.sell_strength <= 100


def test_initial_missing_and_cooldown_strengths_are_null() -> None:
    engine = SignalEngine(replace(_config(), cooldown_events=2))
    initial = engine.snapshot().decision
    data = inputs()
    missing = engine.evaluate(**(data | {"regime": _regime("unknown")})).decision
    assert engine.evaluate(**data).decision.action == "BUY"
    cooldown = engine.evaluate(**data).decision
    for d in (initial, missing, cooldown):
        assert d.action == "WAIT" and not d.strength_available
        assert d.buy_strength is None and d.sell_strength is None
        assert canonical_serialize(d)["buy_strength"] is None


def test_missing_plan_retains_evaluated_evidence() -> None:
    data = inputs()
    data["structure"] = _structure(pivots=(("high", "104", "confirmed"),), levels=())
    d = SignalEngine(_config()).evaluate(**data).decision
    assert d.action == "WAIT" and d.strength_available
    assert d.entry_zone is None and d.targets == ()
    assert d.buy_strength is not None and d.buy_strength > 0
    assert any(e.code == "missing_trade_setup" for e in d.negative_evidence)


@pytest.mark.parametrize("source", ["pivot", "level", "regime", "transition"])
def test_future_inputs_rejected_without_strength_or_trade_plan(source: str) -> None:
    data = inputs()
    future = NOW + timedelta(days=1)
    if source == "pivot":
        s = data["structure"]
        data["structure"] = replace(
            s, pivots=(*s.pivots[:-1], replace(s.pivots[-1], confirmation_time=future))
        )
    elif source == "level":
        s = data["structure"]
        data["structure"] = replace(s, levels=(replace(s.levels[0], updated_at=future),))
    elif source == "regime":
        r = data["regime"]
        data["regime"] = replace(r, state=replace(r.state, effective_time=future))
    else:
        m = data["matrix"]
        resolution = m.resolutions[0]
        assert resolution.latest_transition is not None
        data["matrix"] = replace(
            m,
            resolutions=(
                replace(
                    resolution,
                    latest_transition=replace(resolution.latest_transition, event_time=future),
                ),
            ),
        )
    d = SignalEngine(_config()).evaluate(**data).decision
    assert d.action == "WAIT" and d.buy_strength is None
    assert d.patterns == () and d.entry_zone is None
    assert d.negative_evidence[0].code == "future_inputs"


@pytest.mark.parametrize(
    "name,prices",
    [
        ("head_and_shoulders", [110, 100, 115, 101, 110, 99]),
        ("inverse_head_and_shoulders", [100, 110, 95, 109, 100, 111]),
        ("triangle_breakdown", [115, 100, 112, 102, 109, 99]),
        ("triangle_breakout", [95, 110, 98, 108, 101, 111]),
        ("failed_breakout", [110, 100, 112, 99]),
        ("failed_breakdown", [100, 110, 98, 111]),
    ],
)
def test_patterns_require_complete_confirmed_sequence(name: str, prices: list[int]) -> None:
    bullish = name in {"inverse_head_and_shoulders", "triangle_breakout", "failed_breakdown"}
    kinds: tuple[Literal["low", "high"], Literal["low", "high"]] = (
        ("low", "high") if bullish else ("high", "low")
    )
    structure = _structure(
        pivots=tuple((kinds[i % 2], str(p), "confirmed") for i, p in enumerate(prices))
    )
    engine = SignalEngine(_config())
    assert name not in {
        p.pattern_type for p in engine._patterns(replace(structure, pivots=structure.pivots[:-1]))
    }
    pattern = next(p for p in engine._patterns(structure) if p.pattern_type == name)
    assert pattern.confirmation_time == structure.pivots[-1].confirmation_time
    assert pattern.direction == ("bullish" if bullish else "bearish")
    assert all(p.source_transition_id in pattern.source_data_reference for p in structure.pivots)
    if len(prices) == 6:
        unbroken = replace(
            structure,
            pivots=(
                *structure.pivots[:-1],
                replace(structure.pivots[-1], price=structure.pivots[1].price),
            ),
        )
        assert name not in {p.pattern_type for p in engine._patterns(unbroken)}


def test_three_pivots_never_claim_head_and_shoulders() -> None:
    s = _structure(
        pivots=(
            ("high", "110", "confirmed"),
            ("low", "100", "confirmed"),
            ("high", "109", "confirmed"),
        )
    )
    assert not any("shoulders" in p.pattern_type for p in SignalEngine(_config())._patterns(s))


def test_past_decision_immutable_when_future_is_evaluated() -> None:
    engine = SignalEngine(_config())
    data = inputs()
    before = engine.evaluate(**data)
    saved = canonical_serialize(before)
    data["matrix"] = replace(data["matrix"], generated_at=NOW + timedelta(days=1))
    engine.evaluate(**data)
    assert canonical_serialize(before) == saved


def test_trade_plan_uses_backend_rr_without_strength_inference() -> None:
    d = SignalEngine(_config()).evaluate(**inputs()).decision
    assert d.entry_zone is not None and d.invalidation_price is not None
    reference = (d.entry_zone.low + d.entry_zone.high) / 2
    risk = d.entry_zone.low - d.invalidation_price
    assert d.targets[0].price == reference + risk * _config().target_rr_tp1
    assert d.targets[1].price == reference + risk * _config().target_rr_tp2
    assert d.risk_reward == _config().target_rr_tp2


def test_old_decision_payload_decodes_without_invented_strength() -> None:
    from nexora.artifacts import decode
    from nexora.signals.models import SignalDecision

    payload = canonical_serialize(SignalEngine(_config()).evaluate(**inputs()).decision)
    old_score = payload["score"]
    for key in ("buy_strength", "sell_strength", "strength_available"):
        payload.pop(key)
    old = decode(SignalDecision, payload)
    assert old.score == old_score
    assert old.buy_strength is None and not old.strength_available


def test_api_and_websocket_serialize_current_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient
    from nexora.research.runtime import ResearchRuntime, RuntimeConfig
    from nexora.storage import SQLiteJournal
    from nexora_api.main import create_app
    from nexora_api.quotes import QuoteService

    from tests.test_dashboard_api import _FailingSource
    from tests.test_readiness_regressions import pipeline_config

    journal = SQLiteJournal(tmp_path / "strength.sqlite")
    runtime = ResearchRuntime(RuntimeConfig(pipeline_config(), "USD/oz"), journal)
    engine = SignalEngine(_config())
    app = create_app(
        QuoteService(_FailingSource(), "XAUUSD"),
        start_worker=False,
        journal=journal,
        runtime=runtime,
        backtest_configs={},
    )
    with TestClient(app, base_url="http://localhost") as client:
        for snapshot in (engine.snapshot(), engine.evaluate(**inputs())):
            payload = canonical_serialize(snapshot)
            monkeypatch.setattr(
                runtime,
                "snapshot",
                lambda payload=payload: {
                    "output": {"signals": payload},
                    "error": None,
                    "event_count": 1,
                },
            )
            response = client.get("/state")
            assert response.status_code == 200
            assert response.json()["research"]["output"]["signals"] == payload
            with client.websocket_connect(
                "/ws/events", headers={"origin": "http://localhost:3000"}
            ) as ws:
                # Research is published once for all clients; quote heartbeats
                # keep flowing while the next shared research snapshot is built.
                for _ in range(12):
                    message = ws.receive_json()
                    if message["event_type"] == "state_snapshot":
                        current = message["payload"]["research"]["output"].get("signals")
                        if current == payload:
                            break
                else:
                    raise AssertionError("current decision was not published")


@pytest.mark.parametrize("buy,sell,expected", [(150, 120, (100, 100)), (-5, 35, (0, 35))])
def test_exported_strength_clamps_each_side(buy: int, sell: int, expected: tuple[int, int]) -> None:
    from unittest.mock import patch

    data = inputs()
    data["matrix"] = _matrix("mixed")
    engine = SignalEngine(_config())
    assessment = replace(engine._assess_components(**data), buy_points=buy, sell_points=sell)
    with patch.object(SignalEngine, "_assess_components", return_value=assessment):
        d = engine.evaluate(**data).decision
    assert (d.buy_strength, d.sell_strength) == expected


def test_real_conflicting_points_remain_independent() -> None:
    data = inputs()
    data["structure"] = _structure(
        pivots=(
            ("high", "110", "confirmed"),
            ("low", "100", "confirmed"),
            ("high", "109.8", "confirmed"),
        )
    )
    engine = SignalEngine(_config())
    a = engine._assess_components(**data)
    d = engine.evaluate(**data).decision
    assert d.buy_strength == a.buy_points and d.sell_strength == a.sell_points
    assert d.buy_strength is not None and d.sell_strength is not None
    assert d.buy_strength > 0 and d.sell_strength > 0
    assert d.buy_strength + d.sell_strength != 100
    assert any(p.relation == "conflict" for p in d.patterns)


def test_pattern_relation_is_symmetric_for_bullish_conflict() -> None:
    data = inputs()
    data["matrix"] = _matrix("aligned_bearish", direction="O", price="102")
    a = SignalEngine(_config())._assess_components(**data)
    assert any(p.direction == "bullish" and p.relation == "conflict" for p in a.patterns)
    assert any(e.component == "pattern" and e.polarity == "bullish" for e in a.negative_evidence)
