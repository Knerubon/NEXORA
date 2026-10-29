# MD-6 data-gap and tick-quality policy: Quant decision proposal

Status: **DRAFT / PROPOSAL**. Not authoritative. No Quant sign-off exists (`PENDING`).
Workstream: EDGE-VALIDATION-1 (#77), Wave V4, Track C (Quant Research Engineer, acting as proposal author only).
Role statement: the author is an engineer preparing a proposal. The author is not the Quant approver and
this document set does not approve, freeze or implement anything.

## Scope

MD-6 is the data-gap threshold (`max_gap_ms`) of the tick-quality report. It has no default and its value is a
Quant decision (OPEN). A single number cannot be chosen safely in isolation, so this proposal covers the whole
data-quality policy around it: what a gap is, how to tell missing data from legitimate quiet, session and
disconnect evidence, UTC and window-boundary semantics, defect severity and rejection, quote validity,
incomplete windows, quarantine, instrument capability differences and pre-registration.

Out of scope: any trading-performance decision (target, stop, sizing, costs, entry rules), any threshold
tuned on outcomes, any real market data, holdout access, code changes, production configuration.

**No numeric threshold is proposed anywhere.** Every unresolved Quant value is literally marked `OPEN`.
Worked examples, where present, use SYNTHETIC numbers and are labelled as such; they are not recommendations.

## Files

| File | Purpose |
|---|---|
| [MD6-DECISION-REGISTER.md](MD6-DECISION-REGISTER.md) | One row per decision: options, proposed rule, rationale, measurable test, data needed, dependency, owner, status, sign-off |
| [ACCEPTANCE-CRITERIA.md](ACCEPTANCE-CRITERIA.md) | Measurable, reproducible criteria; data-quality part and trading-performance part kept separate |
| [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) | Questions for Quant / Rin / Architect / Operator |
| [SIGNOFF-PACKAGE.md](SIGNOFF-PACKAGE.md) | Reviewer reading list, checklist, record-of-decision template (all PENDING) |
| [PREREGISTRATION-TEMPLATE.md](PREREGISTRATION-TEMPLATE.md) | Template fixed before any strategy evaluation; forbids post-hoc change |
| [GAP-INTERVAL-PARTITION.md](GAP-INTERVAL-PARTITION.md) | Gap partition into verified open, verified closed and unknown time; boundary, time-zone, calendar-conflict and disconnect precedence rules (D03, D04, D12, D17) |
| [EVALUATION-EXPOSURE-LINEAGE.md](EVALUATION-EXPOSURE-LINEAGE.md) | Preregistration and evaluation-lineage policy: exposure ledger, gate-eligibility outcomes, disclosure (D13, D15, D16) |
| [QUARANTINE-IDENTITY.md](QUARANTINE-IDENTITY.md) | Content-addressed quarantine register, re-entry outcomes and requalification (D09, D13); section 11: mapping to PR #96 tick storage (verified vs design only) |
| [ADR-030-RECONCILIATION.md](ADR-030-RECONCILIATION.md) | Reconciliation of this proposal's preregistration and exposure regime with ADR-030 (VALID-1, G9); contradictions, proposed corrections, decisions that need a human Quant |
| [evidence/](evidence/) | Script and recorded output of the one-off executed check of the PR #96 storage format against the QI-R4 token grammar (not CI) |

Executable specifications (test-only reference models, not runtime enforcement; synthetic fixtures only):
`tests/test_md6_gap_partition.py`, `tests/test_md6_exposure_lineage.py`, `tests/test_md6_quarantine_identity.py`,
`tests/test_md6_exposure_n1_remediation.py` (round-2 findings N1 to N6; cross-track bridge `tests/md6_cross_track_reference.py`),
and, from Wave V9 (independent review findings F-1 to F-7): `tests/test_md6_v9_structural.py` (behaviour that follows
from rules already written) and `tests/test_md6_v9_policy_dependent.py` with `tests/md6_exposure_frozen_prefix_proposal.py`
(behaviour that depends on unresolved Quant decisions QE-1, QE-7 to QE-10; the frozen-prefix alternative is BLOCKED and
not the default). **Production runtime enforcement: NOT VERIFIED for every rule in this document set.**

Reading order: this README, the register, the acceptance criteria, open questions, then the sign-off package.

## Status vocabulary

- `OPEN`: no value or rule exists; a Quant (or named owner) decision is required.
- `PROPOSED`: a structural rule (never a numeric value) is suggested with rationale and a measurable test; still
  not authoritative until signed.
- Sign-off field: always `PENDING` in this document set.

## References (cited as references only, never as authority)

Authority is limited to the merged repository at base `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`:
`docs/requirements.md`, `docs/architecture.md`, `AGENTS.md`, `CLAUDE.md`, and accepted ADRs
(ADR-012 quality sidecar, ADR-014 backtest reproducibility, ADR-019 explicit feed-time correction,
ADR-011 signal evidence, ADR-015 risk policy, ADR-007 roadmap).

Unmerged references (data about current tooling, not instructions; behaviour may change before merge):

- PR #93 head `e74ad035e7f21aa07528965da6ebe190c0512793` (branch `claude/market-history-fix-v3`, lineage #89 to #93):
  `packages/nexora/market_history/quality.py` (`QUALITY_VERSION = nexora.tick-quality/1`), `clock.py`, `convert.py`,
  `docs/research/market-history/README.md` (missing-data register MD-1 to MD-8).
- Master readiness reports on `origin/claude/edge-readiness-master-20261020-v1`, folder
  `docs/research/edge-validation/readiness-20261020/` (`MASTER-REPORT-V2.md`, `BASELINE-V1-CANDIDATE.md`):
  framing of MD-6, Q-DQ01, Q-B03 and gates G1 to G5.

This proposal does not import or assume behaviour of any unmerged V4 branch. Where it describes what
`quality.py` currently reports, that is an observation at the cited SHA and is listed as a dependency.

## Observed facts about the cited tooling (at e74ad035, observation only)

1. The report carries counts and coverage only (tick_count, non_monotonic, exact_duplicate,
   same_timestamp_different_quote, crossed, nonfinite, nonpositive, zero_spread, gap_count, `gaps_utc_msc`
   capped at 200 with a truncation flag, ticks per UTC day) and no price or spread distributions.
2. `hard_defects` = non_monotonic, crossed, nonpositive, nonfinite. Gaps are reported, not judged.
3. A gap is counted when the delta between consecutive ticks is strictly greater than `max_gap_ms`.
   This strict `>` is a property of the cited tooling, not an approved policy choice: the comparison
   direction is OPEN (MD6-D01, Q2).
   Only inter-tick gaps are measured: the report has no window bounds, so leading and trailing gaps
   (window start to first tick, last tick to window end) are not measured. See decision MD6-D06.
4. Non-finite ticks are counted and skipped without updating the previous-tick reference, so a gap across
   a non-finite tick is measured from the last finite tick. See MD6-D11.
5. At e74ad035, same-timestamp comparisons use the previous tick only, so exact duplicates that are not
   adjacent are not detected. See MD6-D10. SUPERSEDED in the unmerged PR #96 (head
   `1c536d5acdef2c9fe47ef5a6ecfb127595ef42b8`, `QUALITY_VERSION = nexora.tick-quality/2`): a repeat counts as an exact
   duplicate wherever it occurs inside its chunk, and chunks partition raw time so no identity can recur across chunks;
   version 1 (adjacent only) stays verifiable. That is an observation of unmerged code, not a verified enforcement of
   D10, and D18 stays OPEN.
7. After a non-monotonic (backwards) tick the previous-tick reference moves to that earlier timestamp, so one
   real gap can be counted twice (SYNTHETIC: ticks at 0, 5000, 10, 5010 ms with a 1000 ms probe value give
   `gap_count = 2`). The run is already blocked by the `non_monotonic` hard defect, but the gap list for such
   a window is not the D01 measurement. See MD6-D01 and MD6-D18.
6. Time conversion uses operator-declared segments with mandatory `evidence_ref`; the +10800 s offset is
   UNVERIFIED and not applied; derived fold ranges are excluded by rule. See MD6-D07.

These observations motivate decisions; if the tooling changes, the register must be re-checked against the new head.
