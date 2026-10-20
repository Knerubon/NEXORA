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
   Only inter-tick gaps are measured: the report has no window bounds, so leading and trailing gaps
   (window start to first tick, last tick to window end) are not measured. See decision MD6-D06.
4. Non-finite ticks are counted and skipped without updating the previous-tick reference, so a gap across
   a non-finite tick is measured from the last finite tick. See MD6-D11.
5. Same-timestamp comparisons use the previous tick only, so exact duplicates that are not adjacent are not
   detected. See MD6-D10.
6. Time conversion uses operator-declared segments with mandatory `evidence_ref`; the +10800 s offset is
   UNVERIFIED and not applied; derived fold ranges are excluded by rule. See MD6-D07.

These observations motivate decisions; if the tooling changes, the register must be re-checked against the new head.
