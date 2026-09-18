"""Contracts for multi-resolution matrix snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from nexora.pnf import PnfTransition

ResolutionStatus = Literal["ready", "warmup", "stale", "unavailable"]
MatrixAlignment = Literal["aligned_bullish", "aligned_bearish", "mixed", "unavailable"]


@dataclass(frozen=True, slots=True)
class MatrixResolutionConfig:
    name: str
    symbol: str
    pnf_config_version: str
    sizing_rule_version: str


@dataclass(frozen=True, slots=True)
class MatrixResolutionState:
    name: str
    symbol: str
    direction: Literal["X", "O", "none"]
    latest_transition: PnfTransition | None
    status: ResolutionStatus


@dataclass(frozen=True, slots=True)
class MatrixSnapshot:
    schema_version: Literal[1]
    symbol: str
    sequence: int
    watermark_sequence: int
    generated_at: datetime
    alignment: MatrixAlignment
    strength: int
    resolutions: tuple[MatrixResolutionState, ...]

