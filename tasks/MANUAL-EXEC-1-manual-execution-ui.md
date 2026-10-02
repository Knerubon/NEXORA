# MANUAL-EXEC-1 — Manual Execution Test Panel V1

status: in_review
translation_needed: false
base_commit: 1689675 (origin/main, Merge PR #44 "claude/autonomous-contract-freeze-v1")
branch: claude/manual-execution-ui-v1
worktree: D:\NEXORA\NEXORA-MANUAL-EXECUTION-UI
role: DEV-ENTRY-adjacent frontend presentation (no existing role owns this exact surface; treated
as frontend/UI scaffold work, self-review; independent review pending (Rin)

## Assignment / context

Add a WEB-only "Manual Order Test" panel (BUY / SELL / CLOSE ALL) intended as a future Execution
Test Harness proving the same execution pipeline AUTO will eventually use. **V1 is UI + contract/
scaffold only**: no broker/MT5 call, no execution wiring, AUTO stays unavailable. Sources:
[AGENTS.md](../AGENTS.md) sections 0, 9, 10; [apps/web/AGENTS.md](../apps/web/AGENTS.md);
[ADR-033](../docs/decisions/ADR-033-autonomous-trading-contracts-v1.md) (status: **PROPOSED —
Rin review required; contract freeze NOT declared**); `packages/nexora/autonomous_contracts.py`.

## Inspection evidence (base 1689675)

- **ADR-033 status**: the document itself states `Status: PROPOSED — Rin review required;
  CONTRACT FREEZE V1 NOT DECLARED until Rin approves` (unchanged by the later
  "review fast-follow" commit `9cde9b9`). It was merged to `main` as a proposal under review, not
  as a ratified freeze. This task treats ADR-033's shapes as *directional* evidence of where the
  system is headed, not as an approved contract to build execution logic against.
- `packages/nexora/autonomous_contracts.py` (123 lines) is a pure, unimported, no-I/O module per
  its own docstring: `TradingMode`, `TradeState`/`TRADE_STATE_TRANSITIONS`, `TradeIntentKind`,
  `EntryOrigin`, `PositionOrigin`, `TradeIntent`. **No `ManualTradeOrigin` or equivalent exists.**
  `EntryOrigin.__post_init__` requires non-empty `signal_decision_ref` and `entry_readiness_ref`;
  `TradeIntent.__post_init__` requires `kind == OPEN ⇒ isinstance(origin, EntryOrigin)`. A manual,
  operator-initiated order has neither a `SignalDecision` nor an `EntryReadinessSnapshot` behind
  it, so **a manual order cannot be represented as a conforming `TradeIntent` today** without
  fabricating fake references — which AGENTS.md section 9 ("do not invent trading semantics") and
  ADR-033 section 10 (`PositionOrigin` must never be a synthetic `ResearchSignal`) both forbid by
  the same logic applied to the symmetric case.
- Position Phase 1 / Position Supervisor: **not implemented anywhere on `origin/main`**
  (`git ls-tree -r origin/main | grep -i position` returns only `matrix-position.ts`, an unrelated
  chart UI module, plus ADR-033 itself). ADR-033 section 15 confirms Position Supervisor is
  interface-only and unimplemented. Ownership resolution for "positions NEXORA owns and manages"
  (required for a real CLOSE ALL) does not exist yet.
- Control Center: no Control Center UI exists on `main`; it lives only in the separate, unmerged
  worktree `D:\NEXORA\NEXORA-CONTROL-CENTER` (per ADR-033 section 6). Not touched by this task.
- `apps/web/app/page.tsx` (349 lines, base) is a single-file dashboard composed of small,
  self-contained panel components (`SignalIntelligence`, `EntryReadiness`, `MatrixFloat`,
  `ChartOverlays`, manual drawing). The established pattern this task follows: a new isolated
  `apps/web/app/<feature>.tsx` component, mounted in `page.tsx` with a couple of lines, backed by
  Node-test-runner tests compiling the `.tsx` via `typescript.transpileModule` and rendering with
  `react-dom/server`'s `renderToStaticMarkup` (see `apps/web/tests/entry-readiness*.{mjs}`).
- No execution/order endpoint exists in `apps/api/nexora_api/main.py` on this baseline, and this
  task adds none.

## Decisions made in this task (frontend-scope only)

- **New, explicitly-not-`TradeIntent` local type**: `ManualTradeRequest` (tag
  `"MANUAL_TRADE_REQUEST_PROPOSAL"`) and `CloseAllRequest` (tag
  `"MANUAL_CLOSE_ALL_REQUEST_PROPOSAL"`) in `apps/web/app/manual-execution.tsx`. These exist only
  to let the panel preview/validate a request locally; they are never sent anywhere and never
  claim to satisfy ADR-033's `TradeIntent`/`EntryOrigin`/`PositionOrigin` shapes.
- **Execution is unconditionally locked**: `EXECUTION_LOCKED = true` is a local `const` with no
  prop, state, storage, or interaction that can flip it in this version. BUY/SELL/CLOSE ALL render
  `disabled` always. The panel contains **no `fetch`/`XMLHttpRequest`/`WebSocket` call of any
  kind** for execution — there is no code path capable of transmitting an order, not merely a
  disabled button guarding one.
- **CLOSE ALL confirmation** is modeled as a pure 3-phase state machine
  (`idle → confirming → confirmed`, `closeAllReducer`) wired to a non-destructive "Preview Close
  All" control that only ever builds the local `CloseAllRequest` preview object — it does not
  bypass the lock, since there is still no transmission path behind it. `buildCloseAllRequest`
  returns `null` for any phase other than `confirmed`.
- **CLOSE ALL scope is never "every MT5 position"**: the preview's `scope` field is the viewed
  symbol (the only ownership signal available pre-Position-Supervisor); the UI text says
  "positions NEXORA owns and manages", not "all positions".
- **Health/mode fields default to an honest `UNKNOWN`**, not an invented value. `tradingMode` is
  passed as `undefined` from `page.tsx` (no `TradingModeGate` exists to source it from — ADR-033
  section 8/20, still `BLOCKED`). `systemHealth`/`brokerHealth` are passed from the *existing*
  `connectionStatus`/`quote.status` feed-connection signals already shown elsewhere on the page,
  explicitly labelled as not the ADR-033 `SystemHealthGate` (section 18, also `BLOCKED` — no real
  health source is wired anywhere yet). This avoids presenting an unwired concept as if it were
  live backend state.
- **Validation** (`validateManualTradeRequest`): symbol required, side ∈ {BUY, SELL}, quantity
  parses to a finite number > 0, SL/TP optional but numeric when present. No lot-sizing, margin,
  or Risk policy is invented — those stay with the future Risk Guard / `BrokerCapabilities` layers
  per ADR-033 sections 12–14.

## Proposed future contract (dependency on Rin / Architect, not decided by this task)

ADR-033's `EntryOrigin` cannot represent a manual order (see evidence above). If/when a real
Manual Execution pipeline is built, something like the following would need Architect review and
an ADR update — **not implemented or assumed here**:

```text
ManualOrigin {                      # proposed alternative/sibling to EntryOrigin, kind == OPEN
  operator_ref: str                 # who requested it — identity model not decided
  ai_analysis_ref: str | None       # same advisory-only role as EntryOrigin.ai_analysis_ref
}
```

Open questions for Rin, deliberately not answered here: does a manual `OPEN` still require
`EntryReadiness == READY` (per ADR-033 section 10's table, today only `OPEN` with `EntryOrigin`
is defined), or does manual origin bypass `EntryReadiness` by design while still passing through
Risk Guard / Execution Guard unchanged? Does `TradeIntent.origin`'s type become
`EntryOrigin | ManualOrigin | PositionOrigin`, or does `kind` grow a `MANUAL_OPEN` variant instead?
This task takes no position on either question — it only establishes that the gap exists and must
be resolved by ADR before any future version of this panel can build a real `TradeIntent`.

## Documented future invariant (AUTO acceptance gate)

AUTO must not be enabled until the shared execution pipeline this Manual Execution Test Harness
will eventually drive has demonstrated, deterministically: order submission, broker
acknowledgement, position reconciliation, duplicate-order protection, close handling, restart/
recovery reconciliation, and stale/unhealthy-broker fail-closed behavior. Manual Test and AUTO
must reach the broker through **one shared execution path** — `TradeIntent`/`ManualOrigin` (or
equivalent) differing only in command origin, never in the Risk/Execution Guard/Broker Adapter
chain itself. This is a statement of intent for future work, not something this task implements or
enforces in code.

## What this task explicitly did not do

- No `order_send()`, no broker/MT5 call, no direct Web→MT5 path.
- No change to `RiskEngine`, `PaperSimulator`, `PositionSupervisor` (doesn't exist), or any Python
  module.
- No change to ADR-033 or `autonomous_contracts.py` — the frozen (proposed) contract is read-only
  to this task.
- No AUTO activation path, no toggle, no backend call for trading mode.
- No PROD contact. No merge to `main`. No tags/releases. No change to PR #45.

## Execution record

- Files added: `apps/web/app/manual-execution.tsx`, `apps/web/tests/manual-execution.test.mjs`,
  `apps/web/tests/manual-execution-harness.mjs`, this task file.
- Files changed: `apps/web/app/page.tsx` (import + mount, 5 lines added, nothing removed),
  `apps/web/app/globals.css` (new `.manual-execution*` rules appended; no existing rule changed).
- Tests: `npm test` (full `apps/web` suite, Node test runner) — **111 passed, 0 failed**, including
  12 new tests covering the 9 required acceptance proofs plus 3 supporting checks.
- Typecheck: `npm run typecheck` (`next typegen && tsc --noEmit`) — **clean**.
- Lint: `npm run lint` (`eslint . --max-warnings 0`) — see handoff for result captured after this
  file was written.

MANUAL EXECUTION UI V1 READY FOR RIN REVIEW
