# P&F Baseline V1 — decision register D1–D6 (closure record)

Date: 2026-11-16 (UTC). Issue #77. Source drafts: PR #105 `C-OWNER-DECISIONS.md` at
`5413bfe609030158cb49b1d90f8442c56293c1d7` and PR #104 gap audit at
`1d6b47ea213b5e681647b60d9e78fa5fdf45fa99`. Base `main` `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`.

**Status: Baseline V1 NOT FROZEN.** One rule is Owner-decided (TP1 full exit). Every other rule
below is the #105 recommendation, implemented in the harness as an explicit, named policy so that
it can be tested, but it stays `PROPOSED` until the Owner approves it. Numeric values listed under
"values required" are not chosen here; synthetic tests use test vectors only.

Legend: `OWNER DECIDED` · `PROPOSED (implemented)` = #105 recommendation, coded as the only
policy option, pending approval · `VALUE REQUIRED` = a number/record the Owner/Quant/custodian
must supply · `OPEN` = not implemented.

## D3 — Exits (Owner decision recorded first)

| Item | Status | Rule as implemented | Evidence |
|---|---|---|---|
| Target plan | **OWNER DECIDED** (D3-TP1-OWNER-20261030, “ออกที่ tp1”) | Close 100% at the signal's own TP1 (`SignalDecision.targets["TP1"]`, existing geometry formula). No TP2, no partial, no residual | `ReplayPolicy.target_plan="tp1_full_exit"`; `test_long_tp1_full_exit_exact_pnl` (qty 2 opened, qty 2 closed at 105) |
| Stop source / movement | PROPOSED (implemented) — #105 D3-A | Stop = signal `invalidation_price`, frozen at decision, never moved | `stop_plan="static_signal_invalidation"` |
| Stop fill | PROPOSED (implemented) | Stop-market: first quote whose exit side (bid long / ask short) touches stop; fills at that quote ± slippage, so gaps fill worse than the stop | `test_long_stop_gap_fills_worse_than_stop` (stop 95, fill 89.9) |
| TP1 fill | PROPOSED (implemented) | Limit: exit side touches TP1 → fill exactly TP1, never better, no slippage | `test_short_uses_ask_for_stop_and_target` |
| SL and TP in one bar | PROPOSED (implemented) — #105 D3 both-touch A | Bars are rejected; tick bid/ask required, so one quote cannot touch both | `tick_bid_ask_required` in `test_preflight_fails_closed` |
| Entry already through stop/target | PROPOSED (implemented) | Entry quote with exit side at/through stop or TP1 → cancelled, risk released | `test_no_exit_on_entry_quote_and_entry_through_stop_rejected` |
| Opposite signal | PROPOSED (implemented) — D3-A | Ignored while a position is open (recorded as `position_busy`) | `test_single_position_skips_overlapping_signal` |
| Time stop | VALUE REQUIRED | `max_hold_seconds` optional; `None` = hold until SL/TP1. A finite value bounds purge length | `test_max_hold_time_exit` |
| End of data | PROPOSED (implemented) — D3 end-window A | Open position marked at last exit-side quote, reported `open_censored`, excluded from closed-trade expectancy, included in equity | `test_open_position_at_end_is_censored_and_marked` |

## D1 — Configuration and cutoff

| Item | Status | Notes |
|---|---|---|
| Config authority (recover actual observed config vs. declare new V1 config) | VALUE REQUIRED | No approved config found (#105 A/B). Every `PipelineConfig` field must be supplied; harness pins it by `pipeline_config_hash` |
| Fixed vs ATR box mode | VALUE REQUIRED | Harness runs whatever `AdaptiveBoxConfig.mode` is supplied; it does **not** force a mode (unlike the ADR-014 runner) |
| Warmup | PROPOSED (implemented) — D1 option B | Optional `DataAuthorization.warmup` range before development: engines advance, no opportunity booked | `test_warmup_builds_state_without_booking_trades` |
| Optional PatternEngine | Retained (ADR-024) | Pipeline built without features ⇒ DISABLED, legacy scoring unchanged |
| Information cutoff | VALUE REQUIRED | Date of last data the config author had seen |

## D2 — Signal to entry

| Item | Status | Rule as implemented |
|---|---|---|
| Strategy boundary | PROPOSED (implemented) — D2-B | Signal + Entry Readiness READY + RiskEngine allow. Raw-signal runs are not offered |
| Readiness lifecycle | PROPOSED (implemented) — D2-A | Readiness must be READY with matching action on the **same event** that emitted the signal; otherwise `readiness_not_ready` / `readiness_mismatch`, no queueing |
| Entry type | PROPOSED (implemented) — D2-A | Market order at the first quote with `event_time ≥ decision_time + latency`, strictly after the decision event; long buys ask(+slip), short sells bid(−slip). Entry zone is not used as a limit |
| Latency / quote age | VALUE REQUIRED | `entry_latency_seconds`, `max_entry_quote_age_seconds` |
| Risk timing | PROPOSED (implemented) | RiskEngine evaluates at decision time with the decision event's own quote (ADR-015 freshness rule unchanged); entry after `RiskDecision.expires_at` is cancelled `risk_approval_expired` |

## D4 — Risk and account

| Item | Status | Rule as implemented |
|---|---|---|
| Ledger scope | PROPOSED (implemented) — D4-A | One account, one open position at a time across both sides; new signals while open/pending are skipped |
| Sizing units | PROPOSED (implemented) — D4-B | Adapter-supplied `InstrumentCapabilities.contract_multiplier`; Risk `stop_distance` = |reference − stop| × multiplier (account money per 1 quantity). `size_step` must equal capability `quantity_step`. ADR-015 formulas unchanged |
| Reservation | PROPOSED (implemented) | `exposure_in_use=0`; reservation lives only inside RiskEngine and is released exactly once (fill-cancel → 0, exit → net PnL) |
| Capital and every RiskPolicy value | VALUE REQUIRED | currency, initial equity, requested size, max risk/trade, total exposure, daily loss, drawdown, min stop, size step, TTL, input age |
| Portfolio drawdown | PROPOSED (implemented) | Event-time marked-to-market equity (cash + floating − accrued swap), absolute and % of peak | `test_floating_loss_counts_in_portfolio_drawdown` |

## D5 — Costs and arithmetic

| Item | Status | Rule as implemented |
|---|---|---|
| Spread | PROPOSED (implemented) — D5-A | Embedded in bid/ask fills; reported for information, never charged again | `test_spread_costs_money_with_unchanged_mid` |
| Commission / swap source | VALUE REQUIRED; unknown handling implemented — D5-B | `None` = UNKNOWN, never zero; any unknown component ⇒ `net_complete=False` ⇒ verdict BLOCKED | `test_unknown_commission_blocks_net_claim`, `test_swap_charged_per_rollover_and_unknown_swap_flagged` |
| Slippage | PROPOSED shape (D5-A, fixed price units per market fill); VALUE REQUIRED | Applied to market entry, stop and time exits; not to TP1 limit |
| Rollover | VALUE REQUIRED | `rollover_hour_utc` (default 21 is a placeholder, not a broker rule); triple-day/holiday schedule OPEN |
| Arithmetic | PROPOSED (implemented) — D5-B | Harness runs inside a local `decimal` context, precision 34, recorded in the manifest |

## D6 — Development data and evidence

| Item | Status | Notes |
|---|---|---|
| Development source | VALUE REQUIRED | Custodian export (A) or provider export such as the #90 Dukascopy path (B). MT5 export (C) is out of scope for cloud |
| Development `[start,end)` UTC, warmup, protected holdout registry + authority | VALUE REQUIRED | Harness refuses to run if the registry is not attested complete, if the scope overlaps a protected range, or if any event lies in a protected range or outside the authorised scope. Checked before any price is interpreted |
| Gap/closure policy | OPEN (MD-6, PR #94/#97/#100, G3) | Harness requires dataset `quality_status="complete"` |
| Pre-registered evidence criteria | VALUE REQUIRED | `EvidenceCriteria` (min closed trades, CI level, min profit factor, bootstrap count/seed) must be registered before the run; without it the verdict is BLOCKED |
| Verdict rule | PROPOSED (implemented) | BLOCKED (synthetic data / no criteria / unknown costs) → INSUFFICIENT (trades < minimum) → NO_EDGE (CI upper ≤ 0) → SUPPORTS_EDGE (CI lower > 0 and PF ≥ minimum) → otherwise INSUFFICIENT | `test_verdict_rules_are_preregistered_and_ordered` |

Holdout confirmation is a separate later step and is not part of this register.
