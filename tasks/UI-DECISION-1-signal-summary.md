# UI-DECISION-1 — Decision Clarity Signal Summary

status: in_review
translation_needed: false
base_commit: ec0aad50c97b67006414f2156b0bbc1b47fb4766 (origin/main, Merge PR #36)
branch: claude/ui-decision-signal-summary-v1
worktree: D:\NEXORA\NEXORA-UI-DECISION

## Assignment / context

Presentation-only addition to the floating Matrix card (`MatrixSummary`): a compact signal
summary under the existing `Decision {action} · Bias: {bias}` line. Sources:
[requirements](../docs/requirements.md), [architecture](../docs/architecture.md),
[root workflow](../AGENTS.md), [ADR-011](../docs/decisions/ADR-011-signal-evidence-policy.md),
[DC1](./DC1-decision-clarity-bias-v1.md).

Phase 1 (read-only inspection) reported; Rin architecture review APPROVED Phase 2 with the
frozen decisions below.

## Frozen decisions (Rin)

```text
Decision {action} · Bias: {bias}      <- unchanged
Signal Score {score}/100              <- or "Signal Score Unavailable"
{P&F positive evidence reason}        <- omitted when absent
```

- Score: existing backend `SignalDecision.score` only (`|buy_points − sell_points|`, 0–100,
  capped at `wait_score_max` under mixed Matrix or range/high-volatility regime). Shown as
  `{score}/100` only when `strength_available === true`, the score is an integer and
  0 ≤ score ≤ 100; otherwise `Signal Score Unavailable`. No new formula, normalisation or
  transformation.
- Explanation: the `reason` of the `decision.positive_evidence` item with
  `component === "pnf"`, rendered verbatim (backend `SignalEngine._pnf_evidence`). No fallback
  to non-P&F evidence, no inference from raw P&F state, no reconciliation with Bias.
- Toggle: `DECISION_SIGNAL_SUMMARY_ENABLED = true` in `signal-intelligence.tsx`;
  `MatrixSummary` takes optional `signalSummaryEnabled` defaulting to it. OFF renders markup
  identical to the pre-feature component. No backend flag, no `NEXT_PUBLIC_*` variable, no
  toggle framework.
- Out of scope: pre-existing `missing_trade_setup` score=0 behaviour of the Signal Engine.

## Scope / boundaries

Changed only `apps/web/app/signal-intelligence.tsx` (`MatrixSummary` + private
`SignalSummary`), new `apps/web/tests/decision-signal-summary.test.mjs`, and this record.
Not touched: `packages/nexora/**`, `apps/api/**`, `page.tsx`, `matrix-float.tsx`,
`overlay-model.ts`, `next.config.mjs`, `globals.css`, ADRs, full `SignalIntelligence` panel.
Desktop and mobile use the same component (`MatrixFloat` becomes static at ≤700px via
existing CSS).

## Missing-data behaviour

| Case | Rendering |
|---|---|
| Decision present, score invalid or `strength_available` not true | `Signal Score Unavailable` (+ P&F line if present) |
| Score valid, no P&F evidence | score line only |
| `decision` undefined (no state / API unavailable) | `Signal Score Unavailable`, no explanation |
| Connection error / recorded mode (stale) | last-received values under the existing `Snapshot only` / `Recorded / last received calculation` labels |

## Execution record

Self-review; independent review pending (Rin code review).

- `node --experimental-strip-types --test tests/decision-signal-summary.test.mjs` (apps/web): pass — 13/13
- `npm --prefix apps/web test`: pass — 68/68
- `npm --prefix apps/web run lint`: pass (exit 0)
- `npm --prefix apps/web run typecheck`: pass (exit 0)
- `npm --prefix apps/web run build`: pass (exit 0)
- `.venv/Scripts/python -m pytest -q` (isolated worktree venv, no `NEXORA_*` env, no `.env`): pass — 570 passed, 3 skipped
- `git diff --check`: pass

Not pushed; no PR; not merged.
