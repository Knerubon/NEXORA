# P8B — Independent evidence strength and signal intelligence

Status: in_review. Base: tag `1.0.1`, commit c136dc74eecc98ac6b85152d54aa120f7c3ac30f.
Backup tag `1.0.0`: c106824590cfd4724582c46390f6110d25509e30; do not mutate tags.

Inputs: docs/requirements.md, docs/architecture.md, docs/development.md,
tasks/P8A-signal-pattern-scoring.md, packages/nexora/signals, tests/test_signals.py,
tests/signal_pattern_golden.py, packages/nexora/structure/models.py,
packages/nexora/pnf, packages/nexora/research, packages/nexora/backtest,
apps/api/nexora_api, apps/web/app, apps/web/tests and scoped AGENTS.md.
These paths establish existing contracts, serialization, past-only source availability,
backtest configuration and chart integration. Read relevant tests for regression evidence.

Scope: additive strengths, incremental pattern corrections/coverage, compact UI,
backend-level presentation overlays, research comparison audit and validation.
No P&F/Matrix/MT5/risk/paper semantic changes. No merge. Self-review; independent review pending.

## Recovery inspection (2026-09-19)

- Existing branch: `codex/signal-intelligence`, HEAD and freshly fetched `origin/main`
  both `c136dc74eecc98ac6b85152d54aa120f7c3ac30f`; no remote branch of this name.
- Existing unstaged work: `packages/nexora/signals/engine.py` (+115/-33),
  `packages/nexora/signals/models.py` (+4), `tests/test_signals.py` (+1/-1).
  Existing untracked file: this task. No staged diff or local commits ahead of main.
- Interrupted implementation already exposed clamped strengths with availability,
  retained assessed evidence on missing plans, added decision-time input guards,
  symmetric pattern relation classification, six-pivot H&S/triangle and four-pivot
  failed-break patterns, and advanced engine version to `p8b-v2`.
  Test fixture decision time was moved after its confirmed pivots. No UI work existed.
- Continued those changes in place. No reset, discard, replacement checkout or tag mutation.
  Tag `1.0.0` remains c106824590cfd4724582c46390f6110d25509e30.
  Annotated tag `1.0.1` object remains 4341113e9ed0d062992390a0cff6712f6480d4e1,
  peeled commit c136dc74eecc98ac6b85152d54aa120f7c3ac30f; remote tags match.

## Contracts and new work

- Strength = independently clamp existing post-evidence buy/sell points to 0..100.
  No normalization, complementary calculation, win-rate mapping or new score formula.
- Unassessed initial/missing/future-input/cooldown decisions use null strengths and
  `strength_available=false`; evaluated WAIT (including missing plan) retains both sides.
  Defaults allow decoding old decisions without inventing an evaluated zero.
- Legacy score/action resolution, WAIT cap, entry/target calculation and score field
  remain unchanged. Pattern corrections can intentionally change evidence and thus
  numerical scores; compatibility does not promise identical scores for corrected patterns.
- Compact Signal Intelligence follows the dominant chart. It reads current snapshot
  decision, not historical latest signal; all plan/pattern/evidence values come from backend.
  Historical win rate remains in Backtest Lab. Empty/legacy/cooldown strength is unavailable.
- New tests cover independent/bounded points, symmetric conflicts, legacy decode/score,
  evaluated and unassessed WAIT, incomplete neckline breaks, future input rejection,
  snapshot immutability, trade plans, REST/WebSocket serialization and React rendering.
- No P&F, Matrix, MT5, Risk, Paper semantics or execution path changed.

## Pattern audit and limits

H&S/inverse H&S and triangles require six alternating confirmed pivots, a head or
converging structure, and a confirmed break beyond both neckline extrema (H&S) or
initial boundary (triangle). Failed breakout/down requires four confirmed alternating
pivots. These are conservative pivot-based research definitions, not bar-level detection.
All are available only when their final pivot is confirmed; future snapshot inputs are
rejected before assessment, trade planning or publication.

Existing double top/bottom remains a three-confirmed-pivot shape, not a claim that a
neckline breakout has occurred. P&F reversals/extensions already appear in evidence.
P&F double-top/flat-top breakout, flags and pennants need explicit prior-column geometry,
impulse/consolidation definitions and versioned fixtures; do not infer them from one
latest transition or label a three-pivot shape H&S. Performance improvement is unproven.

Existing R:R uses risk from the favorable entry-zone edge to invalidation, while target
reward is measured from transition price. This task preserves that convention; displayed
R:R is the backend TP2 value, not a recalculation at an assumed fill price.

## A–E backtest audit / follow-up

Inspected `packages/nexora/research/pipeline.py`, `packages/nexora/backtest/models.py`
and `runner.py`. Existing modes are baseline completed-bar momentum, fixed P&F and
adaptive P&F. Both P&F modes use the shared full pipeline. They are not stages A–E.
Zero weights cannot implement true ablation: input requirements and mixed-Matrix /
range/high-volatility WAIT gates still apply, and trade plans depend on structure.
The simulator uses its existing execution policy, not signal TP1/TP2 as realized exits.

Defer A–E implementation to a separate versioned research contract:
1. Define A P&F; B +Structure (including S/R); C +Matrix; D +Regime; E +Patterns,
   including per-stage readiness gates, score thresholds and a shared causal plan/exit policy.
2. Add explicit stage to hashed configuration; preserve default full-pipeline behavior.
3. Use identical dataset/split/cost/latency assumptions and report sample size, win rate,
   expectancy, profit factor, drawdown, false-entry proxy, signal frequency and entry delay.
4. Add causal prefix replay/ablation fixtures and evaluate held-out historical data.
No fabricated A–E results, calibrated probabilities or performance acceptance are claimed.

## Validation / self-review

Final local evidence:
- `.venv/Scripts/python -m pytest -q`: 122 passed, 1 skipped (optional PostgreSQL
  integration environment unavailable), 2 dependency deprecation warnings.
- `.venv/Scripts/ruff check .`: passed.
- `.venv/Scripts/mypy`: passed, 86 source files.
- `.venv/Scripts/ruff format --check packages/nexora/signals tests/test_signals.py tests/test_signal_intelligence.py`:
  passed, 6 files. Full `.venv/Scripts/ruff format --check .` reports 15 pre-existing
  unformatted files outside this change; their paths are unchanged against HEAD.
  Full formatting is not a CI gate; no unrelated engine formatting was applied.
- `npm --prefix apps/web test`: 13 passed (4 new rendering/integration assertions).
- `npm --prefix apps/web run lint`, `run typecheck`, `run build`: all passed.
- `.tools/Scripts/uv build`: source distribution and wheel built successfully.
- `.tools/Scripts/uv lock --check`: passed.
- `.venv/Scripts/python scripts/recovery_drill.py`: SQLite fresh-process recovery passed;
  PostgreSQL recovery, crash/RPO/RTO and remote access remain unverified.
- `.venv/Scripts/python scripts/smoke_api.py`: initial workstation-environment invocation
  timed out; isolated child environment (remove NEXORA_* variables, set temporary
  NEXORA_JOURNAL_PATH and NO_PROXY=localhost,127.0.0.1) passed loopback HTTP health.
  No workstation configuration was changed.
- `git diff --check`: passed. Reviewed tracked and added files for scope, secrets,
  backend-only numeric presentation, no order paths and tag preservation.

Self-review; independent review pending. Draft PR only; no merge. Remaining work:
independent review, optional PostgreSQL validation and the explicit A–E research contract.
No empirical claim that the new pattern definitions improve trading performance.
