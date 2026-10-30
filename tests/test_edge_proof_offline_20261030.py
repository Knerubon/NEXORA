"""Independent handcrafted engineering probes; synthetic results prove no trading edge.

Run without repository conftest: PYTHONPATH=packages python -m unittest discover
-s tests -p test_edge_proof_offline_20261030.py -v
Known defects use expectedFailure: unexpected success fails the suite and needs review.
All prices/configuration below are synthetic test vectors, never proposed trading policy.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from unittest import TestCase, expectedFailure

from nexora.backtest.models import BacktestConfig, CostPolicy, ExecutionPolicy
from nexora.backtest.runner import BacktestRunner
from nexora.market_data.models import NormalizedPriceEvent
from nexora.pnf.engine import PnfEngine
from nexora.pnf.models import PnfConfig
from nexora.signals.models import ResearchSignal

D = Decimal
ORIGIN = datetime(2026, 1, 1, tzinfo=UTC)


def event(index: int, price: str) -> NormalizedPriceEvent:
    """Explicit ordered closed bars with independent identities and equal availability."""
    when = ORIGIN + timedelta(seconds=index)
    value = D(price)
    return NormalizedPriceEvent(
        schema_version=1,
        identity_key=f"synthetic:{index}",
        source="synthetic-proof",
        symbol="SYNTH",
        kind="bar",
        event_time=when,
        received_at=when,
        source_sequence=index,
        source_order=index,
        source_event_id=f"event:{index}",
        price_source="close",
        units="synthetic-units",
        precision=2,
        price=value,
        open_price=value,
        high=value,
        low=value,
        close=value,
    )


def engine() -> PnfEngine:
    return PnfEngine((PnfConfig("SYNTH", D("1"), 3, 2, "close", "synthetic-v1"),))


def config() -> BacktestConfig:
    return BacktestConfig(
        mode="baseline",
        engine_version="synthetic-probe",
        signal_version="synthetic-probe",
        split="eval",
        seed=None,
        cost_policy=CostPolicy(D("0.2"), D("0.1"), D("0.3"), "TEST", "synthetic-v1"),
        execution_policy=ExecutionPolicy(0, 3, D("2"), D("1"), "synthetic-v1"),
        baseline_lookback=2,
    )


def signal(side: Literal["long", "short"] = "long") -> ResearchSignal:
    return ResearchSignal(
        signal_id="synthetic-signal",
        symbol="SYNTH",
        side=side,
        sequence=0,
        occurrence_time=ORIGIN,
        confirmation_time=ORIGIN,
        decision_time=ORIGIN,
        reasons=("synthetic",),
        reason_codes=("synthetic",),
        source_refs=("synthetic:0",),
        config_version="synthetic-v1",
        engine_versions=("synthetic-v1",),
        status="active",
    )


class PnfOfflineProofTests(TestCase):
    def test_prefix_causality_and_future_transition_append_only(self) -> None:
        prefix = tuple(event(i, p) for i, p in enumerate(("100", "104", "103", "101")))
        a, b = engine(), engine()
        for item in prefix:
            self.assertEqual(a.process(item), b.process(item))
        frozen = a.snapshot()
        before = frozen.symbols[0].transitions
        for item in (event(4, "150"), event(5, "70")):
            a.process(item)
        self.assertEqual(a.state_for("SYNTH").transitions[: len(before)], before)
        self.assertEqual(b.snapshot(), frozen)
        self.assertEqual(frozen.symbols[0].columns[-1].close_price, D("101"))

    def test_completed_columns_and_prior_snapshots_do_not_repaint(self) -> None:
        subject = engine()
        for i, p in enumerate(("100", "104", "101")):
            subject.process(event(i, p))
        saved = subject.snapshot()
        closed = saved.symbols[0].columns[0]
        subject.process(event(3, "98"))
        subject.process(event(4, "103"))
        self.assertEqual(subject.state_for("SYNTH").columns[0], closed)
        self.assertEqual(saved.symbols[0].columns[-1].close_price, D("101"))

    def test_reversal_occurs_at_threshold_not_one_cent_before_both_sides(self) -> None:
        for prices, direction in (
            (("100", "104", "101.01", "101"), "O"),
            (("100", "96", "98.99", "99"), "X"),
        ):
            with self.subTest(direction=direction):
                subject = engine()
                subject.process(event(0, prices[0]))
                subject.process(event(1, prices[1]))
                self.assertEqual(subject.process(event(2, prices[2])), ())
                result = subject.process(event(3, prices[3]))
                self.assertEqual(len(result), 1)
                self.assertEqual((result[0].type, result[0].direction), ("reversal", direction))
                self.assertEqual(result[0].event_time, ORIGIN + timedelta(seconds=3))
                self.assertEqual(result[0].boxes_moved, 3)

    def test_reversal_gap_records_observed_event_time(self) -> None:
        subject = engine()
        subject.process(event(0, "100"))
        subject.process(event(1, "104"))
        result = subject.process(event(2, "95"))[0]
        self.assertEqual(result.boxes_moved, 9)
        self.assertEqual(result.event_time, ORIGIN + timedelta(seconds=2))
        self.assertEqual(result.source_event_id, "event:2")

    def test_replay_deterministic_and_chunk_restore_for_unseen_suffix(self) -> None:
        events = tuple(
            event(i, p)
            for i, p in enumerate(("100", "104", "103.9", "101", "98", "102", "105", "100"))
        )
        uninterrupted, replay = engine(), engine()
        for item in events:
            uninterrupted.process(item)
            replay.process(item)
        self.assertEqual(uninterrupted.snapshot(), replay.snapshot())
        for cut in range(1, len(events)):
            with self.subTest(cut=cut):
                chunked = engine()
                for item in events[:cut]:
                    chunked.process(item)
                restored = PnfEngine.from_snapshot(chunked.snapshot())
                for item in events[cut:]:
                    restored.process(item)
                self.assertEqual(restored.snapshot(), uninterrupted.snapshot())

    def test_duplicate_seed_is_ignored_without_restore(self) -> None:
        subject = engine()
        subject.process(event(0, "100"))
        subject.process(event(1, "104"))
        before = subject.snapshot()
        self.assertEqual(subject.process(event(0, "100")), ())
        self.assertEqual(subject.snapshot(), before)

    @expectedFailure
    def test_restore_preserves_duplicate_seed_identity_known_defect(self) -> None:
        # Snapshot stores transition identities only; seed/no-transition identities are lost.
        subject = engine()
        subject.process(event(0, "100"))
        subject.process(event(1, "104"))
        before = subject.snapshot()
        restored = PnfEngine.from_snapshot(before)
        self.assertEqual(restored.process(event(0, "100")), ())
        self.assertEqual(restored.snapshot(), before)


class ExecutionOfflineProofTests(TestCase):
    def test_execution_costs_exact_algebra_for_both_sides(self) -> None:
        events = tuple(event(i, p) for i, p in enumerate(("100", "99", "102", "105")))
        cases: tuple[tuple[Literal["long", "short"], Decimal], ...] = (
            ("long", D("10")),
            ("short", D("-10")),
        )
        for side, gross in cases:
            with self.subTest(side=side):
                result = BacktestRunner._to_trade(signal(side), config(), events)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.gross_pnl, gross)
                self.assertEqual(result.fees, D("1.2"))
                self.assertEqual(result.net_pnl, gross - D("1.2"))

    def test_more_costs_reduce_net_without_changing_gross(self) -> None:
        events = (event(0, "100"), event(3, "105"))
        cfg = config()
        higher = replace(cfg, cost_policy=replace(cfg.cost_policy, spread=D("1.2")))
        low = BacktestRunner._to_trade(signal(), cfg, events)
        high = BacktestRunner._to_trade(signal(), higher, events)
        assert low is not None and high is not None
        self.assertEqual(low.gross_pnl, high.gross_pnl)
        self.assertEqual(low.net_pnl - high.net_pnl, D("2"))

    def test_entry_delay_selects_first_eligible_observed_event(self) -> None:
        events = (event(0, "100"), event(2, "102"), event(5, "105"))
        cfg = config()
        delayed = replace(
            cfg, execution_policy=replace(cfg.execution_policy, entry_delay_seconds=1)
        )
        result = BacktestRunner._to_trade(signal(), delayed, events)
        assert result is not None
        self.assertEqual(result.entry_time, events[1].event_time)
        self.assertEqual(result.exit_time, events[2].event_time)

    def test_stop_distance_is_ignored_characterization(self) -> None:
        # Price passes the nominal stop and recovers; current runner exits by time only.
        events = (event(0, "100"), event(1, "90"), event(3, "110"))
        cfg = config()
        wide = replace(cfg, execution_policy=replace(cfg.execution_policy, stop_distance=D("50")))
        tight_trade = BacktestRunner._to_trade(signal(), cfg, events)
        self.assertEqual(tight_trade, BacktestRunner._to_trade(signal(), wide, events))
        assert tight_trade is not None
        self.assertEqual(tight_trade.exit_price, D("110"))

    @expectedFailure
    def test_nominal_stop_breach_must_not_be_ignored_known_defect(self) -> None:
        events = (event(0, "100"), event(1, "90"), event(3, "110"))
        result = BacktestRunner._to_trade(signal(), config(), events)
        assert result is not None
        # No assumption about stop-price vs gap-price fill; only temporal breach recognition.
        self.assertEqual(result.exit_time, events[1].event_time)

    def test_current_baseline_is_close_momentum_not_pnf(self) -> None:
        # All moves are below a one-unit PNF box, yet baseline generates a long signal.
        events = (event(0, "100"), event(1, "100.1"), event(2, "100.2"))
        subject = engine()
        for item in events:
            subject.process(item)
        self.assertEqual(subject.state_for("SYNTH").columns, ())
        result = BacktestRunner.generate_signals(events, config())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].reason_codes, ("close_momentum",))

    def test_momentum_signal_prefix_survives_adversarial_future(self) -> None:
        prefix = (event(0, "100"), event(1, "101"), event(2, "102"))
        initial = BacktestRunner.generate_signals(prefix, config())
        extended = BacktestRunner.generate_signals(prefix + (event(3, "1"),), config())
        self.assertEqual(extended[: len(initial)], initial)

    def test_execution_repeat_has_equal_dataclass_values(self) -> None:
        events = (event(0, "100"), event(1, "90"), event(3, "110"))
        first = BacktestRunner._to_trade(signal(), config(), events)
        self.assertEqual(first, BacktestRunner._to_trade(signal(), config(), events))
