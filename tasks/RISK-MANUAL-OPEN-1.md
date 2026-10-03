---
task: "RISK-MANUAL-OPEN-1"
status: "blocked"
depends_on: ["tasks/P11-risk-engine.md", "docs/decisions/ADR-033-autonomous-trading-contracts-v1.md", "docs/decisions/ADR-034-execution-contracts-v1.md"]
agents: ["agents/architect/AGENT.md", "agents/quant/AGENT.md", "agents/developer/AGENT.md", "agents/reviewer/AGENT.md", "agents/security/AGENT.md"]
skills: []
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-015-risk-engine-policy.md", "docs/decisions/ADR-033-autonomous-trading-contracts-v1.md", "docs/decisions/ADR-034-execution-contracts-v1.md", "docs/development.md"]
translation_needed: false
---

# RISK-MANUAL-OPEN-1 — Manual OPEN Risk Provenance (design only)

role: ARCHITECT/INTEGRATOR design worker (Claude E), per AGENTS.md section 1; quant/formula/policy
decisions are explicitly not made by this role per AGENTS.md section 1 ("Quant still owns
formula/threshold decisions")
worktree: D:\NEXORA\NEXORA-RISK-MANUAL-OPEN
branch: claude/risk-manual-open-design-v1
base_sha: d9ac44537c785490a5d2b37b503ff34cffdce269

## เป้าหมาย / traceability

ADR-034 section 1 explicitly leaves open "how a manual OPEN's `RiskDecision` is produced" and
instructs: "Do not implement a path around this without an explicit Architect/Quant decision."
This task produces that decision's input — options, consumer impact, safety/compatibility effects
— without implementing, freezing, or silently selecting a policy. Tracked on the Wave 1
coordination board (`D:\NEXORA\NEXORA-WAVE1-COORDINATION-20261003\BOARD.md`, Track E).

## Entry gate / context

Read [AGENTS.md](../AGENTS.md) first. Context manifest for this task, with reasons:

- [docs/requirements.md](../docs/requirements.md), [docs/architecture.md](../docs/architecture.md)
  — product scope / system design source of truth (AGENTS.md section 0).
- [ADR-015](../docs/decisions/ADR-015-risk-engine-policy.md) — accepted Risk engine policy this
  proposal must extend, not silently override.
- [ADR-033](../docs/decisions/ADR-033-autonomous-trading-contracts-v1.md) sections 10/12/15 —
  frozen `TradeIntent`/`PositionOrigin` boundary and the "never a synthetic ResearchSignal" rule
  this task applies symmetrically to the OPEN side; section 15's note that
  `RiskDecision.signal_id` is non-optional today.
- [ADR-034](../docs/decisions/ADR-034-execution-contracts-v1.md) sections 1/2/3/9/11 — the exact
  open question this task answers the input to (`ManualOrigin`, `evaluate_manual()`,
  `RiskReductionDecision` precedent, the execution safety invariant table).
- [P11 task](P11-risk-engine.md) — existing Risk Engine task/decision record this proposal
  extends.
- [MANUAL-EXEC-1](MANUAL-EXEC-1-manual-execution-ui.md) — UI-side task that first surfaced this
  exact gap and recorded Rin's ADR-033 approval verdict despite the ADR header still reading
  PROPOSED.
- [docs/development.md](../docs/development.md) — validation commands reference (docs-only
  validation applies to this task; see Acceptance).
- Inspected code (read-only): `packages/nexora/risk/{models,engine,repository,replay,fixtures}.py`,
  `packages/nexora/autonomous/{authority,risk_migration}.py`,
  `packages/nexora/autonomous_contracts.py`, `packages/nexora/execution/{models,idempotency,
  reconciliation}.py`, `packages/nexora/position/models.py`, `packages/nexora/paper/session.py`.
  Reason: identify every `RiskDecision`/`RiskProposal` consumer, the exact fields each one reads,
  and every frozen-contract boundary a proposed option would touch.

Dependency evidence checked, not assumed: P11 is `status: in_review` (self-review complete,
independent review pending; security review pending before P12) — its `RiskProposal`/`RiskDecision`
/`RiskEngine` implementation exists and is runnable on this baseline (confirmed by direct file
inspection, not by the roadmap). ADR-033 and ADR-034 both carry `Status: PROPOSED` headers; per the
Wave 1 coordination board, ADR-033's approval is recorded via the MANUAL-EXEC-1 governance update
(Rin verdict, PR #44) despite the stale header, and ADR-034's ratification is an open question the
Orchestrator has already raised with the owner — this task does not resolve either header
discrepancy and does not treat ADR-034 as ratified for anything beyond reading its already-merged
code and its own explicitly-recorded open question (section 1).

## Scope / deliverables

Owned, editable paths only: this file and
[docs/proposals/risk-manual-open-v1.md](../docs/proposals/risk-manual-open-v1.md). No production
code, no frozen ADR edits, no edits to any other task file.

Deliverable: [docs/proposals/risk-manual-open-v1.md](../docs/proposals/risk-manual-open-v1.md) —
confirmed current-state inspection, the precise problem restatement, exhaustive options (A1/A2/A3/
B/C-rejected) with exact compatibility/consumer impact, cross-cutting implications (TTL/freshness,
idempotency/replay, account/reservation scope, PositionRecord provenance dependency), and a
QUANT DECISION REQUIRED section listing exact open questions. No option is selected; an
engineering recommendation is stated but marked explicitly non-binding.

## Decision gates / ขั้นตอน

1. Confirm `RiskProposal.signal: ResearchSignal` and `RiskDecision.signal_id: str` are both
   required/non-Optional today (confirmed, section 1 of the proposal) — do not assume from ADR
   text alone, verify against `packages/nexora/risk/models.py`.
2. Confirm `NewTradeAuthority.evaluate_manual()` does not itself require signal-shaped provenance
   — only `risk_decision.action` (confirmed, section 1 of the proposal) — so the gap is isolated
   to `RiskProposal`/`RiskEngine.evaluate()`, not the authority layer.
3. Enumerate every consumer of `RiskDecision`/`RiskProposal` outside `packages/nexora/risk/`
   (confirmed: `autonomous/authority.py`, `autonomous/risk_migration.py` — type reference only, no
   `RiskDecision` consumption — `execution/reconciliation.py` — docstring mention only, no field
   read — `paper/session.py`, `paper/simulator.py`) and verify none of them would silently break
   under each option.
4. Identify every downstream blocker a successful manual-OPEN `RiskDecision` would still hit
   (confirmed: `PositionRecord` provenance requirements in `position/models.py` — flagged as
   out-of-scope dependency for Track F, not solved here).
5. **QUANT DECISION REQUIRED (see proposal section 5) — do not proceed past this gate.** No
   implementation, no ADR amendment, and no `status: ready` transition for any follow-on task may
   happen until Architect/Quant answer the six questions in the proposal. This task's own status
   stays `blocked` until that happens.

## Acceptance / validation

- [x] Exact current-state inspection of `RiskProposal`/`RiskDecision`/`RiskEngine`/
  `NewTradeAuthority.evaluate_manual()`/`RiskReductionDecision` recorded with file/line-level
  evidence in the proposal.
- [x] At least one option that requires zero change to any already-frozen/accepted contract shape
  identified (A1/A3).
- [x] At least one option that would require touching a frozen contract identified and explicitly
  not recommended, with the reason stated (Option B vs. `evaluate_manual()`'s frozen signature).
- [x] Every `RiskDecision`/`RiskProposal` consumer outside `packages/nexora/risk/` enumerated and
  its exact field usage checked against every option.
- [x] TTL/freshness, idempotency/proposal-identity, account/reservation scope, and replay
  implications each stated as an explicit open question, not defaulted.
- [x] `PositionRecord` provenance dependency flagged as out-of-scope, not silently assumed solved.
- [x] No fabricated `signal_id`, `ResearchSignal`, `signal_decision_ref`, or `entry_readiness_ref`
  appears anywhere in the proposal's recommended or rejected options as an *implemented* value —
  every option either avoids the field, leaves it empty, or flags the risk of reusing it
  explicitly rather than silently fabricating a plausible-looking fake.
- [ ] Architect/Quant decision on the six QUANT DECISION REQUIRED questions (blocked — human/Quant
  action, not resolvable by this task).
- [ ] Independent review of this proposal (self-review only so far; independent review pending).
- [ ] Root Definition of Done — blocked on the two items above plus human merge evidence.

Docs-only validation performed (AGENTS.md section 14, docs-only row): links/context paths,
metadata (`task`/`status`/`depends_on` frontmatter), dependency graph (every `docs`/`agents` path
in frontmatter and in-body links resolved against the actual tree), `git diff --check` (no
whitespace errors). Application tests are **not_run** — reason: this task touches no application
code (two markdown files only); impact: none, since no Python/TypeScript/test file is in the diff.

## Handoff

Rin -> Architect/Quant (this task's actual decision gate) -> (future, separate task) Developer ->
Tester -> Reviewer (+ Security once persistence/network/risk/paper boundary code exists) -> Rin.
This task stops at "ready for Architect/Quant decision"; it does not proceed to implementation.
Self-review only; independent review pending — not claimed otherwise.

## Execution record

- Implementation: none (design/proposal only, per task brief and owned-path restriction).
- Context additions beyond frontmatter: none loaded outside the manifest listed in Entry gate.
- Decisions made by this task: none — by design. The task produces decision *inputs*
  (options + impact analysis), not decisions. See proposal section 5 for the exact list of
  decisions deferred to Quant/Architect.
- Changed files: `tasks/RISK-MANUAL-OPEN-1.md` (this file, new),
  `docs/proposals/risk-manual-open-v1.md` (new). No other file touched.
- Checks:
  - Links/context paths: every path referenced in this file's frontmatter and body resolves in
    the current worktree tree — PASS (verified by direct `Read`/`Glob` inspection during
    authoring, not assumed).
  - `git diff --check` (whitespace-error scan on the staged diff): see PR for exact output.
  - Application tests (`pytest`/`npm test`/lint/type/build): **not_run** — docs-only change, no
    application code in the diff; see Acceptance section for the explicit reason/impact statement
    required by AGENTS.md section 14.
- Review: self-review complete; independent review pending (AGENTS.md section 12 step 7 — not
  claimed as separately reviewed).
- Blockers: **QUANT DECISION REQUIRED** — see proposal section 5. This task cannot leave `blocked`
  until Architect/Quant answer those six questions, and no implementation task should be opened
  against this topic before that happens.
- Next recommended action: Rin/Architect/Quant review
  [docs/proposals/risk-manual-open-v1.md](../docs/proposals/risk-manual-open-v1.md) section 5 and
  record a decision (new ADR amendment, or an explicit extension-not-requiring-ADR ruling) before
  any Manual OPEN Risk implementation task is opened.
