# Baseline V1 — freeze CANDIDATE (EDGE-VALIDATION-1, Agent C)

Status: **CANDIDATE, NOT FROZEN, NOT IMMUTABLE.** Self-authored; independent review pending.
Audited main `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`. Machine-readable inventory of every
field: `BASELINE-V1-FREEZE-PROPOSAL.json` (same directory). This file adds the specification
structure, the per-topic status and the version/hash rules.

Rules used: a code default is **evidence of what the code does, not an accepted value**; nothing
is inferred from `docs/examples/*`, from the legacy close-momentum "baseline", or from any
profitability result (none was looked at). Every value not verified from an operator attestation is
`UNRESOLVED`; a decision that cannot be made without another decision is `BLOCKED`.

## 1. Specification matrix

| # | Topic | What the audited code does (evidence, not acceptance) | Candidate value | Status | Decision / blocker |
|---|---|---|---|---|---|
| 1 | Box sizing | `AdaptiveBoxConfig` defaults atr_period 14, atr_multiplier 1.0, min 0.1, max 50.0; mode/fixed size/precision have no default | none | UNRESOLVED | A1 attestation of the actual run config; ADR-030 Q-V3 unit policy |
| 2 | Reversal | `PnfConfig.reversal_boxes` has no default | none | UNRESOLVED | A1 |
| 3 | Signal conditions | Legacy `SignalEngine` weights 20/25/20/20/15/10/15; wait_score_max 49; action_score_min 65; action_gap_min 8; pattern inputs not consumed by `evaluate` | none | UNRESOLVED | A1; Quant must state whether defaults are the observed process |
| 4 | Entry conditions | Entry Readiness consumes signal output; entry zone half-width default 0.5 box; pattern tolerance 0.8 | none | UNRESOLVED | Q-T (entry); AGENTS.md section 9: no invented semantics |
| 5 | Entry timing | Protocol draft requires at least one bar delay unless knowledge-before-open is shown | none | UNRESOLVED | OPEN-VP-6 |
| 6 | Exit conditions | Static SL/TP exit in PR #79 (`simulate_exit`), ambiguity closed vocabulary after #88 | none | UNRESOLVED | Q-T01, Q-T08 |
| 7 | Stop-loss / take-profit | Geometry only: target_rr_tp1 1.5, tp2 2.5; which target is traded unknown | none | UNRESOLVED | Q-T01, Q-T03 |
| 8 | Partial close | Not representable in the audited static single-target contract | none | BLOCKED | Q-T02 (needs contract change under AGENTS.md section 3) |
| 9 | Position sizing | None in the edge contract | none | UNRESOLVED | Q-T04 |
| 10 | Concurrent positions | `concurrency` policy has no default | none | UNRESOLVED | Q-T06 |
| 11 | Time stop / end of window | `max_hold_bars` null = no time stop; end-of-window handling undefined | none | UNRESOLVED | Q-T05; purge length cannot be derived while unbounded |
| 12 | Trading sessions | No session model verified | none | UNRESOLVED | Calendar source MD-5 + Quant |
| 13 | Costs (spread, slippage, commission, swap) | `CostModel` requires every component; unknown/unavailable never zero; arithmetic context engineering default pending Q-T12 | none | BLOCKED | MD-3, OPEN-VP-8, Q-T12; real bid/ask needs MD-1 |
| 14 | Missing / ambiguous data | Guard (#80) unwired; edge dataset audit (#79) | none | UNRESOLVED | Q-DQ02, Q-DQ05 |
| 15 | Data-quality thresholds | All thresholds are caller-supplied | none | UNRESOLVED | Q-DQ01, MD-6 |
| 16 | Information cutoff | None recorded | none | UNRESOLVED | A2; includes FIX3 samples (Q-D02) |
| 17 | Dataset split / holdout | `SplitPlan`/registry in #79; protocol in #81 draft | none | UNRESOLVED | EDGE-VP-1 freeze; lock before any holdout read |
| 18 | Statistical acceptance | Protocol draft only | none | UNRESOLVED | OPEN-VP (precommitted thresholds); must precede any result |

Count: 18 topics, 0 resolved, 1 BLOCKED-by-contract (8), 1 BLOCKED-by-data (13), 16 UNRESOLVED.

## 2. Version and hash specification (to apply at freeze, not now)

- **Version id:** `baseline-v1/<n>`; any change to any resolved value is a new version, never an edit.
- **Config document:** one JSON document with every field of the proposal expanded (no inherited
  defaults left implicit) and no `UNRESOLVED` token. Freeze is **refused** if any remains.
- **Hash:** SHA-256 over canonical JSON (sorted keys, `,`/`:` separators, ASCII, Decimals as
  normalised strings), hex lowercase. Separate hashes recorded: complete config; resolved feature
  document; execution contract; cost contract; arithmetic descriptor (`edge-decimal-context/1`
  today); dataset manifest `dataset_id` and `manifest_sha256`; split + registry; preregistration
  protocol.
- **Provenance at freeze:** clean code SHA, information cutoff, reviewers, approval date and Rin
  authority reference (per-PR token for any governance change).
- **Order:** config hash and acceptance thresholds are committed **before** the holdout is unlocked
  and before any net result exists; results then reference those hashes.

## 3. Decisions requiring approval

Authoritative register: PR #84 (Q-T01…Q-T11, OPEN-VP-1…21) plus the deltas in `MASTER-REPORT.md`
section 5 (Q-T12, Q-D01/02, Q-DQ01–06). Added here: **Q-B01** which topics 1–4 values are the
observed process (A1), **Q-B02** end-of-window rule, **Q-B03** session calendar source. All have
**no proposed value**; owners are Quant, Architect and Rin as in the register.

## 4. Not done

No freeze, no tuning, no holdout access, no profitability analysis. Candidate does not authorize
any trade, paper or demo activity.
