---
id: OPEN-21
task: OPEN-21-execution-market-policy
status: blocked
owner: Quant (numeric content); Architect + Rin (approval)
depends_on: ["Quant decision on spread/slippage/margin/session policy", "Rin/Architect approval (ADR or accepted decision record)"]
unblock_condition: "An accepted ADR / decision record that defines the policy content listed under 'What OPEN-21 must define'."
agents: []
skills: []
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-035-execution-integration-safety-amendment.md", "docs/decisions/ADR-033-autonomous-trading-contracts-v1.md"]
translation_needed: false
---

# OPEN-21 - Execution Market Policy

Requirements: [requirements](../docs/requirements.md). Architecture: [architecture](../docs/architecture.md).
Contracts: [ADR-035](../docs/decisions/ADR-035-execution-integration-safety-amendment.md) s3.5, s4.5, s8, s11 (OPEN-1, OPEN-11), s12 #5;
[ADR-033](../docs/decisions/ADR-033-autonomous-trading-contracts-v1.md) s13, s14.

This is a TRACKING ITEM only (docs-only). It changes **no frozen ADR text** (no ADR, AGENTS.md or code is edited), invents **no value**,
and implements **no allow path**. Its status is `blocked` (the repo task vocabulary requires an unblock condition for items lacking a
decision); the unblock condition is in the metadata above.

## Rin decision (2026-10-05)
- Spread / slippage / margin / session policy is **REQUIRED before any Preflight ALLOW**.
- It is tracked **separately** from OPEN-1/2/3/5/11/14/19/20 (which are also required before Paper/Demo).
- It does **not block PR-7** (narrow scope; production stays DENY-ONLY).

## Why this item exists
The decision packet found that these numeric policy checks block every preflight ALLOW yet have **no OPEN id** in ADR-035 s11. ADR-035
s3.5 declares the policy checks (session, spread via `spread_policy_ref`, slippage via `price_constraint`/policy, margin via
`margin_policy_ref`) to consume opaque references whose numeric content is Quant-owned and "not decided here"; ADR-033 s13 marks
spread/slippage/margin as BLOCKED until a real `BrokerCapabilities` provider, and ADR-033 s14 defines the opaque `session_policy_ref`,
`spread_policy_ref`, `margin_policy_ref` fields. ADR-035 s11 OPEN-11 covers only whether the checks apply to risk-reducing kinds, not the
policy content itself.

Current behavior (`packages/nexora/execution/preflight.py`, documented in `tasks/PR6-execution-preflight.md`, ADR gap 3):
- OPEN kind: denied `preflight_policy_evaluation_unavailable` (ADR silent on OPEN policy evaluation).
- REDUCE / CLOSE / MODIFY_PROTECTION: denied `preflight_policy_undecided` (OPEN-11).
- Net effect: `ExecutionPreflight.evaluate` can never ALLOW; production is DENY-ONLY and non-operational.

## What OPEN-21 must define (Quant-owned numeric content; Rin/Architect approval)
1. Spread limit (the content behind `spread_policy_ref`).
2. Slippage limit (the content behind `price_constraint`/slippage policy).
3. Margin sufficiency rule (the content behind `margin_policy_ref`).
4. Trading-session / market-open policy (the content behind `session_policy_ref`).
5. Applicability per request kind: OPEN versus REDUCE / CLOSE / MODIFY_PROTECTION. Overlaps and must be reconciled with OPEN-11
   (risk-reducing applicability); OPEN-21 does not pre-empt OPEN-11.
6. Freshness of market inputs (quotes/spread/margin snapshots) used by the checks; reconcile with OPEN-1 (maximum age bounds) and take
   any bound from approved policy/configuration, never hard-coded.
7. Policy versioning, config reference, and evidence: the policy reference/version recorded in the PreflightDecision evidence so every
   decision is traceable (AGENTS.md s0: data/config/version references).
8. Failure semantics: fail closed (missing/stale/malformed policy, inputs or evaluation error => deny with an approved reason code; any
   new reason code needs Rin approval because the preflight vocabulary is pinned at ten `preflight_*` codes).
9. Determinism / replay: evaluation is a pure function of request, capabilities, market refs, policy and injected `now`; no wall clock,
   randomness or I/O.
10. Where it is evaluated and its inputs: in `ExecutionPreflight.evaluate` (ADR-035 s3.5, step 4 of s3.1/3.2), before the claim, creating
    no durable state. Requires a frozen `market_refs` shape (currently undefined; PR-6 ADR gap 1) carrying the spread/price/margin
    inputs, and a policy-reference resolution mechanism.

## Non-goals
- No numeric value, default or temporary policy is chosen or implied here.
- No allow path is implemented; preflight stays DENY-ONLY.
- Not a PR-7 deliverable and not a condition for merging PR-7.
- No change to ADR-033 / ADR-035 text. Adding OPEN-21 to the ADR-035 s11 OPEN table, if desired, requires a **separate
  Rin-authorized ADR amendment** and is NOT done here.
- No Track E / manual-OPEN, broker-order, or live-trading change (Phase 1: research/paper only, AGENTS.md s0).

## Acceptance criteria for closing OPEN-21
- [ ] Quant-approved definition of items 1-10 recorded in an accepted ADR or accepted decision record (Rin/Architect approval).
- [ ] Applicability to OPEN vs REDUCE/CLOSE/MODIFY_PROTECTION stated and consistent with the OPEN-11 decision.
- [ ] Freshness of market inputs consistent with the OPEN-1 decision.
- [ ] `market_refs` contract frozen (AGENTS.md s3 contract governance) before dependent implementation.
- [ ] Fail-closed and determinism rules stated; policy ref/version appears in decision evidence.
- [ ] Any new reason codes approved by Rin; contract and preflight vocabulary tests updated in the implementing PR.
- [ ] Only then may an implementation PR add a preflight ALLOW path, with negative/boundary/replay tests.

## Dependencies and ownership
- Quant: formulas and thresholds (AGENTS.md s1). Architect/Rin: approval; Architect: `market_refs` and policy-ref resolution contract.
- ADR or accepted decision record is required **before** any implementation.
- Related: OPEN-11, OPEN-1 (ADR-035 s11, s8 BEFORE-PAPER-DEMO gates); [PR6](PR6-execution-preflight.md) (DENY-ONLY preflight);
  ADR-035 s12 #5 (`preflight_policy_undecided` blocks risk-reducing transmission); ADR-033 s13/s14.

## Execution record
- 2026-10-05: tracking item created (docs-only; self-review; independent review pending). No application tests run or claimed.
