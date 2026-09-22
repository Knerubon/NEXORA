---
task: TS1
status: blocked
depends_on: []
agents: []
skills: []
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-019-explicit-feed-time-correction.md"]
translation_needed: false
---

# Proposed follow-up — NEXORA Time Semantics

Proposal only; NOT authorized for implementation in PR23. Blocked on explicit task
approval and Architect/Quant acceptance of a versioned time contract and migration policy.
No formulas, code, timestamp normalization or historical records change in this proposal.
The empty dependency list does not make this task ready; the decision gate below applies.

## Problem and sources

Make storage, event knowledge, broker normalization, display and session clocks explicit
without changing historical research results accidentally. Requirements/architecture stay
source of truth. Review ADR-019's explicit feed correction as an existing compatibility
constraint, not a universal broker timezone specification.
Additional context to inspect only when approved: apps/api/nexora_api/quotes.py,
packages/nexora/{market_data,research,experience}, their tests and runtime configuration;
these implement event normalization, replay and measurement cutoff semantics.

## Proposed scope / decisions required

- UTC canonical storage: specify representations, precision, serialization and versioning.
- Require timezone-aware `event_time` and `received_at`; distinguish market occurrence
  from knowledge/receipt time and define rejection/quarantine of naive/ambiguous inputs.
- MT5/broker server time normalization: identify source timestamp semantics with actual
  broker/feed evidence; preserve raw timestamps, explicit correction and provenance.
  Do not infer timezone from a constant clock difference or apply a correction twice.
- Asia/Bangkok display timezone: presentation conversion only; display must not rewrite
  event identities, stored timestamps or computation inputs.
- Market/session timezone: explicitly configure the market/session calendar separately
  from browser, operating-system, broker and display timezones; define boundary ownership.
- DST-safe `America/New_York` and `Europe/London`: IANA zones and pinned/recorded tzdata
  provenance; define spring gaps, autumn folds and weeks when US/UK transitions differ.
  No fixed UTC-offset substitute for those session zones.
- Replay/Experience invariance: preserve knowledge-time T0, causal horizons, lifecycle
  ordering and outcome identity for unchanged canonical inputs. Changing display/system
  timezone must not alter P&F/Matrix/Signal/Risk/paper or Experience results.
- Backward compatibility: no silent reinterpretation of stored broker/raw times or the
  ADR-019 correction. If corrected normalization would change canonical inputs, require
  an approved versioned migration/new dataset scope, with explicit expected differences
  and rollback. Do not promise identical results for semantically changed input times.

## Proposed acceptance evidence

- Golden replay and Experience snapshot/outcome hashes match across UTC, Asia/Bangkok,
  America/New_York and Europe/London host/display settings for the same canonical data.
- Boundary tests: aware/naive values; raw/corrected provenance; duplicate/late/out-of-order
  receipt; T0 and exact 5/15/30/60-minute cutoffs; DST gap/fold and session boundaries;
  US/UK transition mismatch weeks; restart/serialization round trips; no future leakage.
- DEV/PROD with identical recorded inputs and time configuration produce equivalent
  canonical outputs independent of their runtime locations and browser timezone.
- Broker/MT5 normalization backed by recorded source evidence; unverifiable feeds retain
  explicit limitations. Required timezone/compatibility decisions documented in an ADR.
- Migration and rollback rehearsed only if separately approved; no historical data edits
  or live order behavior as part of the proposal.

## Unblock / handoff

Rin approves a separate task; Architect/Quant lock the contract and compatibility/version
policy, including DST ambiguity treatment and broker evidence. Then create an implementation
assignment with tests/inputs and required reviews. No work on TS1 is included in PR23.
