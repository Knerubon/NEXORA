# MD-6 open questions

Status: **DRAFT / PROPOSAL**. Answers are required from the named owner; the author does not answer them.
No value is proposed for any item marked OPEN.

## For the Quant reviewer

| ID | Question | Register |
|---|---|---|
| Q1 | Is a two-tier structure (hard ceiling plus context-dependent soft limit) acceptable, or should a single absolute `max_gap_ms` be used? Values: OPEN | D02 |
| Q2 | Gap comparison direction at exactly the threshold: strictly greater, or greater or equal? | D01 |
| Q3 | Which context keys parameterize the soft limit: instrument, session, both? Quantile estimator, level and minimum sample size (all OPEN)? | D02, D17 |
| Q4 | Maximum tolerated coverage of `unexplained` gaps per window and per dataset: OPEN | D03, D09 |
| Q5 | Are the proposed hard defects (non-monotonic, crossed, nonpositive, non-finite) accepted with zero tolerance, or is a tolerance wanted (value OPEN)? | D08 |
| Q6 | Tolerance and treatment for zero spread, same-timestamp-different-quote and exact duplicates: OPEN | D08, D10, D11 |
| Q7 | Does a non-finite tick count as activity for gap measurement? | D11 |
| Q8 | Consumer-side collapse rule for several quotes at one timestamp (none, first, last, reject): OPEN | D10 |
| Q9 | Edge tolerance for leading/trailing gaps, and minimum coverage for a complete window: OPEN | D06, D12 |
| Q10 | Heartbeat tolerance and what counts as admissible disconnect evidence: OPEN | D05 |
| Q11 | Spread cap policy, if any: OPEN | D11 |
| Q12 | Treatment of bar-only sources: out of tick gap policy, or a separate quality definition? | D14 |

## For Rin / Architect

| ID | Question | Register |
|---|---|---|
| R1 | Calendar source (MD-5 / Q-B03): broker-published sessions, venue calendar, or other; who maintains it? | D04 |
| R2 | Who is the independent data reviewer who applies quarantine criteria, separate from strategy authors? | D13 |
| R3 | Which contract (ADR or architecture review) carries the quality-policy fields (policy version, preregistration id, classification fields) in the dataset manifest and quality report? Any change to frozen contracts follows AGENTS.md section 3. | D18 |
| R4 | Does the custodian procedure for protected ranges need amendment so that gap statistics for protected ranges are produced only after preregistration? | D16 |
| R5 | Should relation to ADR-036 Data Quality Guard thresholds (Q-DQ01) be unified with MD-6, or kept as separate research-time vs runtime policies? | D02 |
| R6 | Reconcile terminology with ADR-012 completeness states (`complete|partial|unknown`). | D12 |

## For the Operator / Market data

| ID | Question | Register |
|---|---|---|
| O1 | Can connection status and retry events be captured during historical pulls on the Windows node? | D05 |
| O2 | Does the source preserve intra-millisecond order stably (MD-7)? | D10 |
| O3 | Evidence for raw-to-UTC offset, effective dates and DST behaviour per symbol and server (MD-2). The +10800 s value stays UNVERIFIED until evidenced. | D07 |
| O4 | Which approved non-holdout reference period can serve as reference distribution? | D17 |

## Dependencies that block decisions

- MD-1 real historical ticks: BLOCKED (no MT5 host in cloud). Without it D02, D03 and D17 cannot be sized.
- MD-2 offset evidence: BLOCKED. D07 stays UNVERIFIED.
- MD-5 calendar: BLOCKED. D04 stays OPEN.
- MD-7 capability discovery: UNKNOWN until real extraction.
- Tooling gaps in D18 are not authorized as code work by this proposal.

## Not decided here (belongs elsewhere)

Q-T01 to Q-T12, OPEN-VP-1 to 21, Q-B01 to Q-B03 (end-of-window rule), Q-DQ01 to Q-DQ06 (runtime guard), Dukascopy
(MD-8, not acted on: it is issue text, not an owner instruction).
