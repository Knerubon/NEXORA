# P&F Baseline V1 — historical replay harness

Issue #77 (EDGE-VALIDATION-1). Branch `claude/pnf-baseline-v1-replay-v1`, base `main`
`36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`. Research/simulation only: no broker, MT5, order,
PAPER/DEMO/REAL/AUTO or PROD path exists in this code.

**Status:** harness and synthetic proofs ready for independent review. Baseline V1 is **not
frozen** (see [DECISIONS.md](DECISIONS.md)). Historical development run is **BLOCKED** on D6
(no approved development source/window and no attested protected holdout registry).

## What it closes from PR #104

| #104 gap | Harness behaviour | Proof |
|---|---|---|
| `baseline` mode is close momentum, not P&F | Separate contract `nexora.backtest.baseline_v1`; runs `ResearchPipeline` (P&F → Matrix → Structure → Regime → Signal → Entry Readiness) on the supplied config without forcing box mode. ADR-014 runner untouched | `test_real_pipeline_emits_and_gates_opportunities` |
| Readiness bypassed | Entry only when Readiness is READY with matching action on the signal's own event | `test_readiness_not_ready_blocks_entry`, real-chain funnel shows `readiness_not_ready` |
| Risk bypassed | Unchanged `RiskEngine` evaluates every eligible signal; size from Risk; reservation released once | `test_risk_rejection_blocks_entry`, `test_risk_sizes_by_stop_distance` |
| Stop/targets ignored | Static stop-market + TP1 full-exit limit on tick bid/ask | exit tests |
| Same price both sides / spread as fee | Long buys ask sells bid, short reverse; spread never charged twice | `test_spread_costs_money_with_unchanged_mid`, `test_short_uses_ask_for_stop_and_target` |
| Overlapping trades, closed-trade drawdown | Single-position account ledger; marked-to-market drawdown | `test_single_position_skips_overlapping_signal`, `test_floating_loss_counts_in_portfolio_drawdown` |
| Open positions dropped at end | Reported `open_censored`, included in equity | `test_open_position_at_end_is_censored_and_marked` |
| No protected-range enforcement | Preflight refuses incomplete registry, overlap, out-of-scope events before replay | `test_preflight_fails_closed` |
| Restore identity / double counting | Harness never restores engine snapshots mid-run; duplicate identities fail closed. The `PnfEngine.from_snapshot` seed-identity defect itself is characterised, not fixed (DEV-PNF owns it; contract change under AGENTS §3) | `test_duplicate_identity_rejected_so_reprocessed_seed_cannot_double_count`, `test_pnf_engine_restore_seed_identity_defect_is_characterized` |

Look-ahead: `test_no_look_ahead_suffix_mutation_does_not_change_prefix_decisions` mutates every
event after index 250 (+50 price) and also runs the 250-event prefix alone; decisions, Readiness,
regime and closed trades before index 250 are identical in all three runs. Determinism:
`test_real_pipeline_replay_is_deterministic` and the rerun script below.

## Per-event order (canonical index k)

1. Accrue swap if a rollover passed since `e_(k-1)` (unknown rate ⇒ counted, never zero).
2. Exit check for a position opened at `j < k`: stop (exit side ≤/≥ stop) before TP1, then time stop.
3. Pending entry: cancel if Risk approval expired, quote too old, or quote already through stop/TP1; else fill.
4. `pipeline.replay(e_k)`; a newly emitted active signal becomes an opportunity (Readiness → single position → geometry → Risk). Entry is scheduled strictly after `e_k`.
5. Mark-to-market equity, peak and drawdown.

## Rerun

```bash
python -m pip install uv==0.12.15
uv sync --locked --extra api --extra postgres
uv run --no-sync pytest -q tests/test_baseline_v1_replay.py
uv run --no-sync python scripts/baseline_v1_replay.py --synthetic --out /tmp/baseline_v1_synthetic.json
```

PowerShell (Windows node): `uv run --no-sync python scripts/baseline_v1_replay.py --synthetic --out "$env:TEMP\baseline_v1_synthetic.json"`.
The script creates the output folder, always writes LF line endings, and refuses an `--out` path inside the checkout (it would make the next run `code_dirty`).

The script prints `verdict`, `verdict_reasons` and `result_hash`; two runs at the same commit give
byte-identical JSON. `result_hash` also binds the manifest, which includes the code SHA, the
`code_dirty` flag and the full CPython version (e.g. `3.13.18`). To compare runs across machines
or Python patch versions, compare `equity_curve_hash`, `metrics`, `trades` and `opportunities`
(and `events_hash`/`config_hash` in the manifest) rather than `result_hash`.

## Synthetic run (mechanics only, not market evidence)

Deterministic 400-tick zig-zag (`nexora.backtest.baseline_v1.synthetic`), test-vector config:
16 entered, 22 `readiness_not_ready`, 2 `entry_through_stop`, 57 `position_busy`; 16 closed
trades (15 stop, 1 TP1), net −848.834 synthetic units, max drawdown 871.096. Verdict **BLOCKED**
(`synthetic_data_is_not_market_evidence`, `evidence_criteria_not_registered`). These numbers only
show that the chain runs; they say nothing about edge.

## Limitations

- Tick bid/ask only; bar data is rejected rather than approximated.
- Bootstrap CI assumes independent trades (no block bootstrap yet); regime breakdown uses the
  regime label at decision time.
- Real-data loader is not written: format/source depend on D6.
- Rollover schedule is a single daily UTC hour counted every calendar day; weekends, triple-swap days and holidays are OPEN.
- Verdict uses closed trades only; open-at-end positions appear in equity/drawdown but not in expectancy.
- RiskEngine sees account equity only when a signal is evaluated; its daily-loss day boundary is a known ADR-015 limitation (see DECISIONS.md).
- Risk is evaluated at decision time, which deviates from the #105 recommendation (see DECISIONS.md D2).
- Each event is processed through the full pipeline; long tick histories may be slow (not benchmarked).
- `ResearchPipeline` gained two read-only accessors (`latest_entry_readiness`, `latest_regime`); no
  behaviour change.

## Handoff (AGENTS §16)

```text
ROLE: DEV (harness) + QA self-test; Lead Orchestrator
OWNER SESSION: cloud session (Claude Code) for Issue #77 thread
WORKSTREAM: P&F Baseline V1 historical replay harness
WORKTREE: cloud session
BRANCH: claude/pnf-baseline-v1-replay-v1
BASE SHA: 36c90c59e2aa4f0d221c9c468a8379c5f57cbb87
STATUS: READY FOR REVIEW (harness); BLOCKED (historical run, Baseline freeze)
BLOCKERS: D1 config values; D2–D5 rule approval and values; D6 data source/window/holdout
          registry; pre-registered evidence criteria
RUNTIME / DATA IMPACT: none; synthetic inputs only, no historical payload opened
MERGE: NOT PERFORMED
NEXT: Owner decision on the D1–D6 package, then real-data loader + bounded development run
```
