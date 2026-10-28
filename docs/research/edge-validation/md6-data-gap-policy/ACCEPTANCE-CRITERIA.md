# MD-6 acceptance criteria

Status: **DRAFT / PROPOSAL**. Criteria are proposals for Quant review; none is authoritative until signed
(Quant sign-off: `PENDING`). Every number is `OPEN` unless stated. Examples are SYNTHETIC.

The criteria are split into two parts that must never be merged:

- **Part A, data-quality decisions:** judged only from data, calendar, heartbeat and manifest artifacts.
- **Part B, trading-performance decisions:** out of MD-6 scope; listed only to state the firewall.

## Part A: data-quality criteria (measurable, reproducible)

Each criterion is checked from files alone (manifest, chunk files, quality report, calendar, heartbeat,
capability profile, preregistration), by an independent reviewer, with no strategy output.

| ID | Criterion | Check (recomputable) | Register link | Value |
|---|---|---|---|---|
| A-01 | Gap definition is fixed and applied in one comparison direction | Fixture at threshold-1 ms, threshold, threshold+1 ms classified per the registered direction | D01 | direction OPEN |
| A-02 | Gap list recomputes exactly | Count and listed pairs from chunk files equal report; truncation flag honoured | D01 | none |
| A-03 | Threshold parameterization and values exist in preregistration before first strategy evaluation | Commit order; hash of file equals cited hash | D02, D15 | `max_gap_ms` OPEN |
| A-04 | Reference distribution disjoint from evaluation and protected windows | Interval overlap check = empty; reference quantile recomputes from reference manifest | D17 | quantile level OPEN |
| A-05 | Every gap has a partition (VERIFIED_OPEN / VERIFIED_CLOSED / UNKNOWN with calendar and offset-rule identity) and a class: unverified, expected_closed, within_threshold, source_fault_evidenced or unexplained, with evidence id and hash | Classification output complete; invariant when strategy files are added | D03, GAP-INTERVAL-PARTITION | open measure OPEN |
| A-06 | Unexplained-gap coverage within registered limit | Sum of VERIFIED_OPEN time of unexplained gaps / VERIFIED_OPEN time of the window compared with limit; any UNKNOWN time gives UNVERIFIED | D03, D09 | limit OPEN |
| A-07 | Calendar artifact present, hashed, non-overlapping, source and evidence recorded | Schema and overlap checks; missing calendar disables per-session option | D04 | n/a |
| A-08 | Disconnect evidence present or declared absent | Artifact schema and hash; absent artifact means no gap can be source_fault_evidenced | D05 | tolerance OPEN |
| A-09 | Windows half-open in UTC; leading and trailing gaps measured | Tiling test: each tick once; durations sum to `to - from` | D06 | edge tolerance OPEN |
| A-10 | Offset and effective dates evidenced for every window tick range | Each range covered by a segment with `evidence_ref`; otherwise status UNVERIFIED and gate use refused | D07 | offset never assumed |
| A-11 | No accepted-window tick lies in a derived fold range | Raw-time range check | D07 | none |
| A-12 | Every defect counter mapped to exactly one class and one disposition | Mapping table in preregistration vs report counters | D08, D09 | soft tolerances OPEN |
| A-13 | No repair of stored ticks | Content hash of accepted chunks equals manifest hash; no derived chunk replaces source | D09 | none |
| A-14 | Exact duplicates detected over the whole window, not only adjacent | Non-adjacent duplicate fixture detected | D10 | none |
| A-15 | Same-timestamp order preserved as stored; tie-break independent of price | Permutation test shows hash change; price-independence test | D10 | consumer collapse rule OPEN |
| A-16 | Quote validity predicates per tick match counters | Fixture per state: NaN, inf, 0, negative, crossed, zero spread | D11 | zero-spread tolerance, spread cap OPEN |
| A-17 | Non-finite ticks have a registered effect on gap measurement | Fixture with non-finite tick inside long gap | D11 | rule OPEN |
| A-18 | Incomplete windows excluded and listed; none dropped silently; excluded windows disclosed with UTC bounds and reason in every result family | planned = accepted + quarantined + incomplete; excluded-window list present and equal to the register | D12, D13 | minimum coverage OPEN |
| A-19 | Quarantine register content-addressed, append-only, hash-chained and anchored; dataset-level and window-level entries carry content identity, dataset_id, aliases, lineage, criteria id, evidence, timestamp, decider, requalification history | Chain and anchor verify; entries precede first evaluation or labelled post-evaluation; missing or corrupt register gives UNVERIFIED | D09, D13 | none |
| A-20 | Capability profile hashed per instrument and source; policy output independent of broker label | Two profiles differing only in label give identical output | D14 | none |
| A-21 | No price/spread distribution in any report for a protected range; every protected-range access is an exposure ledger entry | Schema check; ledger check | D16 | none |
| A-22 | Inclusion set hash recorded with each result family | Hash present and equal to recomputed set | D13 | none |
| A-23 | Gap partition sums to `next - prev`, is deterministic, and fails closed on UNKNOWN | `tests/test_md6_gap_partition.py` cases A1 to A10 | D03, D04, D07 | open measure OPEN |
| A-24 | Every candidate (checked per evaluation window; a WINDOW-scope entry blocks candidates containing its records, not sibling windows) passes the content re-entry check (not QUARANTINED_MATCH, DERIVED_FROM_QUARANTINED, CANONICALIZATION_CONFLICT or UNVERIFIED); NOT_KNOWN or REQUALIFIED never implies trust | Recompute content identity from chunks and check against the anchored register; `tests/test_md6_quarantine_identity.py` C1 to C10; `tests/test_md6_exposure_n1_remediation.py` N4 to N6 | D13 | none |

A dataset is `acceptable for evaluation` only if every applicable Part A criterion passes and the dataset passes
the trust rule of the tooling (independently stored anchor). A failing criterion produces an explicit
recorded outcome (reject, quarantine or annotate per D09), never a silent pass.

## Part B: trading-performance decisions (firewall; none decided here)

Out of scope and must be decided elsewhere (Quant register Q-T01 to Q-T12, OPEN-VP, Q-B01 to Q-B03):
targets, stops, partial closes, sizing, costs, time stop, end-of-window trading rule, entry and exit rules,
performance acceptance metrics.

Firewall rules:

| ID | Rule | Check |
|---|---|---|
| B-01 | No Part A parameter may be chosen, changed or justified by any Part B quantity (P/L, win rate, expectancy, drawdown, trade list, signal output) | Preregistration cites only Part A evidence; reviewer greps the decision record for outcome references |
| B-02 | No Part B decision may rely on a Part A value being "good enough because results look fine" | Part B records cite the signed Part A record id, not results |
| B-03 | Part A decisions are signed and committed before Part B evaluation begins on the same instruments and windows | Commit ordering and exposure-ledger position: the registration precedes every exposure of its footprint and the evaluation |
| B-04 | A change to Part A or to the strategy configuration after any exposure makes every later result on the exposed instruments, intervals or content EXPLORATORY permanently, regardless of new preregistration id, family id or dataset id; exploratory evidence can never be relabelled independent; one exposure unit yields at most one qualification, whatever the evaluation order, unless a Quant-authorised multiplicity declaration (QE-7) makes the whole set one qualification | Outcome recomputed from the exposure ledger (EVALUATION-EXPOSURE-LINEAGE.md); `tests/test_md6_exposure_lineage.py` B1 to B10; `tests/test_md6_exposure_n1_remediation.py` C1 to C12 and N1 to N3. Cross-track content matching is tested through `tests/md6_cross_track_reference.py` (reordered copy, subset, superset, partial overlap, canonicalization conflict) |
| B-05 | Holdout and backtest outputs, and protected-range statistics observed at any time, were not consulted to derive any Part A value; every such observation is an exposure ledger entry | Attestation in the sign-off record, plus exposure ledger |
| B-06 | Each result family discloses every preregistration (active, superseded, abandoned) touching its instruments and windows, with count, and every evaluation touching them, including downgraded or later-superseded ones | Recomputed from ledger; mismatch makes the family UNVERIFIED |

## Threats to validity

| Threat | Mechanism | Mitigation in this proposal |
|---|---|---|
| Look-ahead through threshold tuning | Adjusting `max_gap_ms` or defect tolerances until a strategy looks better or a window passes | Preregistration before evaluation (D15); B-01 to B-05; reference set disjoint (D17); exposure ledger (EVALUATION-EXPOSURE-LINEAGE.md) |
| Survivorship by excluding windows | Quarantining windows that hurt results, keeping those that help | Criteria fixed in advance; quarantine register with independent applier; planned = accepted + quarantined + incomplete (A-18, A-19); excluded fraction and excluded-window UTC list reported, so regime-correlated exclusion is visible |
| Holdout leakage through quality statistics | Gap counts, tick rates or spreads on protected ranges reveal event days or regimes | D16; counts-and-coverage only; no use of protected ranges for thresholds; access log |
| Circular calendar or reference | Calendar or reference inferred from data under test | D04, D17 forbid inference from the evaluated data |
| Evidence selection | Classifying gaps as "quiet" using price behaviour | D03 uses calendar, heartbeat, reference rates only; determinism test with strategy files present or absent |
| Silent repair | Sorting, de-duplicating or interpolating hides defects | D09 no-repair rule; A-13 |
| Time-base error | Unverified offset shifts windows and calendars | D07; UNVERIFIED blocks gate use |
| Single-source blindness | Retrieved history cannot reveal its own outages | D05 absence is not evidence of continuity |
| Reviewer shares author's lineage | Same-lineage review gives weak independence | Gate C requires an independent Quant reviewer; sign-off recorded PENDING |

## Reproducibility

A reviewer can recompute from files alone, with no network, no broker, no terminal and no strategy output:

1. Dataset manifest self-hash, chunk hashes, tick counts and contiguity; the research `dataset_id` binding of
   content, symbol, window, conversion with fold ranges, clock attestation, source version and instrument facts.
2. The quality report (counts, coverage, gap list) and its recomputation from chunks.
3. Over-threshold gap list and classification from calendar, heartbeat and reference artifacts, each with hash.
4. Time conversion segments, `evidence_ref` presence and the derived fold ranges.
5. Preregistration file hash and commit order relative to evaluation artifacts.
6. Quarantine register chain and anchor, each dataset's content identity, and inclusion set hash.
7. Capability profile hash.

Not recomputable from files (must be attested and labelled): original source behaviour, whether a gap is a real
market lull, broker clock semantics beyond the recorded evidence, completeness of retrieved history.

Environment: deterministic procedures (integer milliseconds, canonical JSON, SHA-256), no ambient decimal
context dependence, no wall-clock or random input; software and policy version stamped in the report.

## Synthetic worked example (SYNTHETIC; not data; not a recommendation)

Illustration of D01 and D06 only. Assume a SYNTHETIC window `[0, 1000)` ms, a SYNTHETIC threshold of `T` ms
(its value is OPEN, so a symbol is used, not a number), and ticks at 100, 400, 400, 900 ms.
Leading gap 100 ms, inter-tick deltas 300, 0 (same timestamp, D10), 500, trailing gap `1000 - 900 = 100`.
Durations sum to `100 + 300 + 0 + 500 + 100 = 1000`, matching `to - from`. Which of these exceed `T` depends on
`T` and on the registered comparison direction; no value of `T` is implied.
