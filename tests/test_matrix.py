from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from nexora.adaptive_box import AdaptiveBoxConfig, AdaptiveBoxSizer, AdaptivePnfRunner
from nexora.market_data.models import NormalizedPriceEvent
from nexora.matrix import (
    MatrixEngine,
    MatrixResolutionConfig,
    MatrixSnapshot,
    MatrixSnapshotStore,
)
from nexora.pnf import PnfConfig


def _event(symbol: str, sequence: int, price: str) -> NormalizedPriceEvent:
    event_time = datetime(2026, 2, 1, 9, 0, 0, tzinfo=UTC) + timedelta(seconds=sequence)
    return NormalizedPriceEvent(
        schema_version=1,
        identity_key=f"{symbol}:{sequence}",
        source="MT5",
        symbol=symbol,
        kind="tick",
        event_time=event_time,
        received_at=event_time + timedelta(milliseconds=150),
        source_sequence=sequence,
        source_order=sequence,
        source_event_id=f"evt-{symbol}-{sequence}",
        price_source="ask",
        units="USD/oz",
        precision=1,
        price=Decimal(price),
        bid=Decimal(price) - Decimal("0.1"),
        ask=Decimal(price),
    )


def _runner(symbol: str, box_size: str, version: str) -> AdaptivePnfRunner:
    pnf = PnfConfig(
        symbol=symbol,
        box_size=Decimal(box_size),
        reversal_boxes=2,
        price_precision=1,
        price_source="ask",
        version=version,
    )
    sizer = AdaptiveBoxSizer(
        AdaptiveBoxConfig(
            mode="fixed",
            fixed_box_size=Decimal(box_size),
            price_precision=1,
            rule_version=f"{version}-fixed",
        )
    )
    return AdaptivePnfRunner((pnf,), sizer)


def _engine(symbol: str) -> MatrixEngine:
    configs = (
        MatrixResolutionConfig(
            name="fast",
            symbol=symbol,
            pnf_config_version="p3-fast-v1",
            sizing_rule_version="p4-fast-fixed-v1",
        ),
        MatrixResolutionConfig(
            name="medium",
            symbol=symbol,
            pnf_config_version="p3-medium-v1",
            sizing_rule_version="p4-medium-fixed-v1",
        ),
        MatrixResolutionConfig(
            name="slow",
            symbol=symbol,
            pnf_config_version="p3-slow-v1",
            sizing_rule_version="p4-slow-fixed-v1",
        ),
    )
    return MatrixEngine(
        symbol=symbol,
        resolutions=configs,
        runners={
            "fast": _runner(symbol, "0.5", "p3-fast-v1"),
            "medium": _runner(symbol, "1.0", "p3-medium-v1"),
            "slow": _runner(symbol, "2.0", "p3-slow-v1"),
        },
        stale_after_events=2,
    )


def test_matrix_warmup_aligned_and_mixed_transitions_are_explicit() -> None:
    engine = _engine("XAUUSD")
    warmup = engine.process(_event("XAUUSD", 1, "100.0"), now=datetime(2026, 2, 1, tzinfo=UTC))
    assert warmup.alignment == "unavailable"
    assert {state.status for state in warmup.resolutions} == {"warmup"}

    aligned = engine.process(
        _event("XAUUSD", 2, "104.0"),
        now=datetime(2026, 2, 1, 0, 0, 1, tzinfo=UTC),
    )
    assert aligned.alignment == "aligned_bullish"
    assert aligned.strength == 3
    assert all(state.direction == "X" for state in aligned.resolutions)

    mixed = engine.process(
        _event("XAUUSD", 3, "102.0"),
        now=datetime(2026, 2, 1, 0, 0, 2, tzinfo=UTC),
    )
    assert mixed.alignment == "mixed"
    assert any(state.direction == "O" for state in mixed.resolutions)
    assert any(state.direction == "X" for state in mixed.resolutions)


def test_matrix_interleaved_stream_matches_isolated_replay() -> None:
    xau_engine = _engine("XAUUSD")
    eur_engine = _engine("EURUSD")
    xau_events = (
        _event("XAUUSD", 1, "100.0"),
        _event("XAUUSD", 2, "103.0"),
        _event("XAUUSD", 3, "101.0"),
    )
    eur_events = (
        _event("EURUSD", 1, "50.0"),
        _event("EURUSD", 2, "54.0"),
        _event("EURUSD", 3, "52.0"),
    )

    interleaved = [
        xau_events[0],
        eur_events[0],
        xau_events[1],
        eur_events[1],
        xau_events[2],
        eur_events[2],
    ]
    interleaved_xau: list[MatrixSnapshot] = []
    xau_counter = 0
    for index, event in enumerate(interleaved, 1):
        if event.symbol == "XAUUSD":
            xau_counter += 1
            interleaved_xau.append(
                xau_engine.process(
                    event,
                    now=datetime(2026, 2, 1, 0, 1, xau_counter, tzinfo=UTC),
                )
            )
        else:
            eur_engine.process(event, now=datetime(2026, 2, 1, 0, 1, index, tzinfo=UTC))

    isolated = _engine("XAUUSD")
    isolated_xau = [
        isolated.process(event, now=datetime(2026, 2, 1, 0, 2, index, tzinfo=UTC))
        for index, event in enumerate(xau_events, 1)
    ]
    anchor_time = datetime(2026, 2, 1, tzinfo=UTC)
    normalized_interleaved = [
        replace(item, generated_at=anchor_time) for item in interleaved_xau
    ]
    normalized_isolated = [replace(item, generated_at=anchor_time) for item in isolated_xau]
    assert normalized_interleaved == normalized_isolated


def test_matrix_snapshot_store_replay_and_rebuild_are_deterministic() -> None:
    engine = _engine("XAUUSD")
    store = MatrixSnapshotStore()
    first = engine.process(_event("XAUUSD", 1, "100.0"), now=datetime(2026, 2, 1, tzinfo=UTC))
    second = engine.process(
        _event("XAUUSD", 2, "104.0"),
        now=datetime(2026, 2, 1, 0, 0, 1, tzinfo=UTC),
    )
    store.append(first)
    store.append(second)

    replay = store.replay()
    rebuild = store.rebuild()
    assert replay == rebuild
    second_snapshot = cast(dict[str, Any], replay[1])
    assert second_snapshot["watermark_sequence"] == 2
    resolutions = cast(list[dict[str, Any]], second_snapshot["resolutions"])
    latest_transition = cast(dict[str, Any], resolutions[0]["latest_transition"])
    assert latest_transition["identity_key"] == "XAUUSD:2"
