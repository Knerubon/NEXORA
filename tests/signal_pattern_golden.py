from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from nexora.market_regime import RegimeLabel
from nexora.matrix import MatrixAlignment
from nexora.pnf import ColumnDirection, PnfTransitionType


@dataclass(frozen=True, slots=True)
class GoldenPatternCase:
    name: str
    pivot_shapes: tuple[
        tuple[
            Literal["high", "low"],
            str,
            Literal["confirmed", "invalidated", "candidate", "unavailable"],
        ],
        ...,
    ]
    levels: tuple[
        tuple[
            Literal["support", "resistance"],
            str,
            Literal["confirmed", "invalidated", "candidate", "unavailable"],
        ],
        ...,
    ]
    matrix_alignment: MatrixAlignment
    matrix_direction: ColumnDirection
    matrix_transition_type: PnfTransitionType
    matrix_price: str
    regime: RegimeLabel
    expected_action: Literal["BUY", "SELL", "WAIT"]


def golden_cases(now: datetime) -> tuple[GoldenPatternCase, ...]:
    _ = now
    return (
        GoldenPatternCase(
            name="buy-double-bottom",
            pivot_shapes=(
                ("low", "99.8", "confirmed"),
                ("high", "104.0", "confirmed"),
                ("low", "100.0", "confirmed"),
            ),
            levels=(("support", "99.5", "confirmed"), ("resistance", "108.0", "confirmed")),
            matrix_alignment="aligned_bullish",
            matrix_direction="X",
            matrix_transition_type="reversal",
            matrix_price="100.2",
            regime="trend",
            expected_action="BUY",
        ),
        GoldenPatternCase(
            name="sell-double-top",
            pivot_shapes=(
                ("high", "111.0", "confirmed"),
                ("low", "104.5", "confirmed"),
                ("high", "111.2", "confirmed"),
            ),
            levels=(("support", "100.0", "confirmed"), ("resistance", "111.5", "confirmed")),
            matrix_alignment="aligned_bearish",
            matrix_direction="O",
            matrix_transition_type="reversal",
            matrix_price="110.8",
            regime="trend",
            expected_action="SELL",
        ),
        GoldenPatternCase(
            name="wait-mixed-matrix",
            pivot_shapes=(
                ("low", "100.0", "confirmed"),
                ("high", "104.0", "confirmed"),
                ("low", "100.1", "confirmed"),
            ),
            levels=(("support", "99.8", "confirmed"), ("resistance", "104.2", "confirmed")),
            matrix_alignment="mixed",
            matrix_direction="X",
            matrix_transition_type="extension",
            matrix_price="103.9",
            regime="range",
            expected_action="WAIT",
        ),
    )
