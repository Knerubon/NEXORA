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

**Review history, retained honestly, not overwritten:** at HEAD `6f8436af7c02e569098bef35fbe7ccd7131ab553`
an independent document-level reviewer approved the proposal with one MINOR finding. Separately,
at the same HEAD, the Orchestrator's integration review requested changes — two MAJOR findings
(Option A3 was wrongly framed as an available option rather than excluded fabrication; Options A1/
A2/B were wrongly framed as zero/near-zero contract-change alternatives) and three MINOR findings
(reject-vs-raise accuracy for `account_mismatch`; shared-budget double-consumption hazard across
engine instances; namespacing-alone overclaim; stop-size/unit policy flagged). Per the task owner's
instruction, the integration findings override readiness at that HEAD — no policy approval exists
for the corrected content below. This section intentionally keeps both outcomes visible rather than
replacing the record.

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
confirmed current-state inspection (including the `PaperSimulator.apply_decision()` binding-check
consumer surfaced only in this revision), the precise problem restatement, exhaustive options
(A1/A2/B, with A3 explicitly excluded as fabrication rather than listed as a sign-off-pending
option, and C rejected outright) with exact compatibility/consumer impact, cross-cutting
implications (TTL/freshness, idempotency namespacing limits, account/reservation shared-budget
hazard, replay scope, stop-size/unit policy, PaperSimulator and PositionRecord provenance
dependencies), and a QUANT DECISION REQUIRED section listing seven open questions. No option is
selected and no option is claimed to be free of ADR-governed impact.

## Decision gates / ขั้นตอน

1. Confirm `RiskProposal.signal: ResearchSignal` and `RiskDecision.signal_id: str` are both
   required/non-Optional today (confirmed, section 1 of the proposal) — do not assume from ADR
   text alone, verify against `packages/nexora/risk/models.py`.
2. Confirm `NewTradeAuthority.evaluate_manual()` reads only `risk_decision.action` — true, and
   unchanged under Option A — but do **not** generalize this into "the gap is isolated upstream
   under every option": for Option B it is not (a new sibling decision type requires widening or
   adding to `evaluate_manual()`'s already-frozen signature, itself an ADR-governed change). This
   narrower, corrected claim replaces the overstated version from the prior revision.
3. Enumerate every consumer of `RiskDecision`/`RiskProposal` outside `packages/nexora/risk/`
   (confirmed: `autonomous/authority.py` — reads only `.action`; `autonomous/risk_migration.py` —
   type reference only, no `RiskDecision` consumption; `execution/reconciliation.py` — docstring
   mention only, no field read; `paper/session.py` — constructs `RiskProposal`, passes it through;
   `paper/simulator.py::apply_decision()` — **actively cross-checks `.signal_id`/`.signal_hash`/
   `.symbol`/`.side`/`.account_id` against a separately-required `ResearchSignal` argument**,
   confirmed only in this revision after the integration review — see proposal section 1) and
   verify none of them would silently break under each option.
4. Identify every downstream blocker a successful manual-OPEN `RiskDecision` would still hit
   (confirmed: `PaperSimulator.apply_decision()`'s mandatory `ResearchSignal` argument and binding
   check, and separately `PositionRecord` provenance requirements in `position/models.py` — both
   flagged as out-of-scope dependencies, the latter for Track F, neither solved here).
5. **QUANT DECISION REQUIRED (see proposal section 5) — do not proceed past this gate.** No
   implementation, no ADR amendment, and no `status: ready` transition for any follow-on task may
   happen until Architect/Quant answer the seven questions in the proposal. This task's own status
   stays `blocked` until that happens.

## Acceptance / validation

- [x] Exact current-state inspection of `RiskProposal`/`RiskDecision`/`RiskEngine`/
  `NewTradeAuthority.evaluate_manual()`/`RiskReductionDecision`/`PaperSimulator.apply_decision()`
  recorded with file/line-level evidence in the proposal.
- [x] Option A3 (reusing `manual_request_id` as `signal_id`) is classified as **excluded
  fabrication**, not as an available option pending sign-off — corrected per integration review
  MAJOR 1.
- [x] No option (A1, A2, or B) is claimed to require zero change to any ADR-governed contract's
  shape, meaning, or signature — every option's actual compatibility cost is stated honestly,
  corrected per integration review MAJOR 1/MAJOR 2.
- [x] Option B is compared against Option A on its merits (a tradeoff table, not a dismissal) —
  corrected per integration review MAJOR 2.
- [x] Every `RiskDecision`/`RiskProposal` consumer outside `packages/nexora/risk/` enumerated and
  its exact field usage checked against every option, including `PaperSimulator.apply_decision()`'s
  active `.signal_id`/`.signal_hash` cross-check (newly surfaced this revision).
- [x] Reject-vs-raise distinction in `RiskEngine` (`account_mismatch` is a rejected `RiskDecision`,
  never a raised exception; contrasted against the five conditions that do raise
  `RiskInputError`) stated with exact line evidence — corrected per integration review MINOR
  finding.
- [x] Shared-budget double-consumption hazard across separate `RiskEngine` instances stated
  explicitly as a safety implication, not left implicit — per integration review MINOR finding.
- [x] Idempotency namespacing described as conditional on every proposal-identity source
  namespacing consistently, not as a standalone guarantee — per integration review MINOR finding.
- [x] Stop-size/monetary-unit/contract-size policy flagged as an explicit open Quant question, not
  assumed reusable from the signal path's formula — per integration review MINOR finding.
- [x] `PaperSimulator` consumer gap and `PositionRecord` provenance dependency both flagged as
  out-of-scope, not silently assumed solved.
- [ ] Architect/Quant decision on the seven QUANT DECISION REQUIRED questions (blocked —
  human/Quant action, not resolvable by this task).
- [ ] Independent **delta** review of this revision at the new HEAD (the prior independent
  document-level approval was recorded at HEAD `6f8436a`, before these corrections; it is retained
  as history in the "เป้าหมาย / traceability" section above, not claimed as still current — a
  fresh delta review against this HEAD is pending).
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
Self-review complete at this HEAD; independent **delta** review pending (the prior independent
approval at HEAD `6f8436a` predates this revision's corrections and is not carried forward as
still-valid for the corrected content).

## Execution record

- Implementation: none (design/proposal only, per task brief and owned-path restriction).
- Context additions beyond frontmatter: this revision additionally inspected
  `packages/nexora/paper/simulator.py` (specifically `apply_decision()`'s binding-check logic,
  lines ~75-165) in direct response to the integration review's MAJOR findings — reason: verify
  whether `RiskDecision.signal_id` is genuinely consumed/cross-checked anywhere, which the prior
  revision had claimed (incorrectly) that it was not.
- Decisions made by this task: none — by design, unchanged from the prior revision. The task
  produces decision *inputs* (options + honestly-costed impact analysis), not decisions. See
  proposal section 5 for the exact list of decisions deferred to Quant/Architect (now seven
  questions, was six — a new question on `proposal_id` namespace convention and a new question on
  stop-size/monetary-unit policy were added; the account/reservation question was sharpened with
  its exact failure mode).
- Corrections applied at this HEAD, per Orchestrator integration review of HEAD `6f8436a`:
  - MAJOR 1: Option A3 reclassified from "available option needing Quant sign-off" to "excluded
    outright — fabricates a signal identity in effect." Option A1 reclassified from "zero
    frozen-contract-change" to "changes the field's enforced semantic meaning, requires ADR-
    governed migration and consumer audit." Option A2's claim that journal rows/replay fixtures
    are unaffected is withdrawn; replaced with an explicit, scoped verification statement (checked
    `tests/test_risk.py` only, found no golden-shape assertion there; a repo-wide check was not
    performed and is left open).
  - MAJOR 2: withdrew the overstated claim that "the gap is entirely upstream... authority needs
    zero change under any option" — true only for Option A, false for Option B (which requires
    amending `evaluate_manual()`'s already-frozen signature). Option B is now compared against
    Option A in a tradeoff table instead of being dismissed for touching a frozen contract.
  - MINOR: corrected/confirmed the `account_mismatch` reject-vs-raise distinction with exact line
    evidence; added the shared-budget double-consumption hazard across separate `RiskEngine`
    instances as an explicit safety implication; corrected the namespacing claim to state it only
    prevents collisions if applied consistently across every proposal-identity source; added the
    stop-size/monetary-unit/contract-size Quant question explicitly rather than assuming the
    signal path's formula transfers unchanged.
  - New evidence surfaced by this revision's additional inspection (not a finding category, a
    direct consequence of re-checking the code): `PaperSimulator.apply_decision()` requires a
    `ResearchSignal` argument and actively cross-checks `.signal_id`/`.signal_hash` against it,
    which directly falsifies the prior revision's claim that nothing consumes `.signal_id` by
    joining it back to a signal store. This is now a documented third blocker (proposal section
    4.6), alongside the previously-flagged `PositionRecord` gap.
- Changed files: `tasks/RISK-MANUAL-OPEN-1.md` (this file, revised),
  `docs/proposals/risk-manual-open-v1.md` (revised). No other file touched.
- Checks:
  - Links/context paths: every path referenced in this file's frontmatter and body resolves in
    the current worktree tree — PASS (re-verified after this revision's edits).
  - `git diff --check` against the new staged diff: see PR for exact output.
  - `git fetch origin` + `origin/main` SHA re-checked for drift before this revision's commit: see
    PR for exact output.
  - Application tests (`pytest`/`npm test`/lint/type/build): **not_run** — docs-only change, no
    application code in the diff; see Acceptance section for the explicit reason/impact statement
    required by AGENTS.md section 14.
- Review: self-review complete at this HEAD; independent delta review pending (AGENTS.md section
  12 step 7 — not claimed as separately reviewed; the prior independent approval at HEAD `6f8436a`
  is retained as history, not reasserted as covering this content).
- Blockers: **QUANT DECISION REQUIRED** — see proposal section 5 (seven questions). This task
  cannot leave `blocked` until Architect/Quant answer them, and no implementation task should be
  opened against this topic before that happens. Additionally unresolved: the `PaperSimulator`
  consumer gap and the `PositionRecord` provenance gap (proposal section 4.6), both out of this
  task's scope.
- Next recommended action: independent delta reviewer re-reviews at the new HEAD; in parallel,
  Rin/Architect/Quant review [docs/proposals/risk-manual-open-v1.md](../docs/proposals/risk-manual-open-v1.md)
  section 5 and record a decision (ADR amendment, or an explicit extension-not-requiring-ADR
  ruling) before any Manual OPEN Risk implementation task is opened.
