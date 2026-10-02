"""News/Social scanner configuration and toggles.

Pure data only. Nothing here is wired to RiskEngine, ExperienceEngine, EntryReadiness,
or any execution path (none of that integration is in scope for this task).

Per instruction, two toggles are kept intentionally separate and must never be
collapsed into one flag:
  - `enabled` (NEWS SCANNER ON/OFF) controls whether the scanner runs at all.
  - `trade_during_news` (TRADE DURING NEWS ON/OFF) controls a *future* consumer's
    willingness to allow new entries during a blackout window. It has no effect by
    itself here; this module does not gate anything.

`SocialScannerConfig.policy_mode` names the WARN/BLOCK vocabulary from the News/Social
pre-flight design, but BLOCK is not implemented or wired anywhere in this task —
ADR-033 section 16 leaves WARN-vs-BLOCK policy an open Quant/Architect decision.
Defining the vocabulary value is not the same as authorizing its use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nexora.events.models import ImpactLevel

SocialPolicyMode = Literal["warn", "block"]


@dataclass(frozen=True, slots=True)
class NewsScannerConfig:
    config_version: str
    enabled: bool = False
    trade_during_news: bool = False
    pre_blackout_minutes: tuple[tuple[ImpactLevel, int], ...] = ()
    post_blackout_minutes: tuple[tuple[ImpactLevel, int], ...] = ()
    watched_currencies: tuple[str, ...] = ()
    watched_symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.config_version.strip():
            raise ValueError("missing_config_version")
        for level, minutes in (*self.pre_blackout_minutes, *self.post_blackout_minutes):
            if not isinstance(level, ImpactLevel):
                raise ValueError("invalid_blackout_impact_level")
            if minutes < 0:
                raise ValueError(f"invalid_blackout_minutes:{level}")

    def pre_blackout_minutes_for(self, impact: ImpactLevel) -> int:
        return dict(self.pre_blackout_minutes).get(impact, 0)

    def post_blackout_minutes_for(self, impact: ImpactLevel) -> int:
        return dict(self.post_blackout_minutes).get(impact, 0)


@dataclass(frozen=True, slots=True)
class SocialScannerConfig:
    config_version: str
    enabled: bool = False
    policy_mode: SocialPolicyMode = "warn"
    watched_accounts: tuple[str, ...] = ()
    watched_symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.config_version.strip():
            raise ValueError("missing_config_version")
        if self.policy_mode not in ("warn", "block"):
            raise ValueError("invalid_policy_mode")
