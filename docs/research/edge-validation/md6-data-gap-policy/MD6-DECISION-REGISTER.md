# MD-6 decision register

Status: **DRAFT / PROPOSAL**. Every sign-off field is `PENDING`. Nothing here is authoritative until signed
(see [SIGNOFF-PACKAGE.md](SIGNOFF-PACKAGE.md)). All numeric values are `OPEN`.

Conventions:

- "Proposed rule" is a structural rule only. It never contains a numeric threshold.
- "Measurable test" is a check a reviewer can run from the files of a dataset (manifest, chunks, quality report,
  calendar and heartbeat artifacts) without market judgement and without any P/L.
- Data class: **DQ** = data-quality decision; **TP** = trading-performance decision (none are made here; the
  column exists to prove separation, see ACCEPTANCE-CRITERIA).
- Owner = who must decide. Status is `OPEN` or `PROPOSED`.
- Any number in an example is SYNTHETIC and illustrative only.

## Summary

| ID | Topic | Class | Status | Numeric value | Sign-off |
|---|---|---|---|---|---|
| MD6-D01 | Definition and measurement of a gap | DQ | PROPOSED | comparison direction OPEN | PENDING |
| MD6-D02 | Parameterization of the maximum permitted gap | DQ | OPEN | `max_gap_ms` OPEN | PENDING |
| MD6-D03 | Missing data vs legitimate low activity | DQ | PROPOSED | quiet-period bound OPEN | PENDING |
| MD6-D04 | Session calendar source and boundaries | DQ | OPEN | n/a | PENDING |
| MD6-D05 | Source disconnect / heartbeat evidence | DQ | PROPOSED | heartbeat tolerance OPEN | PENDING |
| MD6-D06 | Window semantics, leading/trailing gaps | DQ | PROPOSED | edge tolerance OPEN | PENDING |
| MD6-D07 | UTC measurement, offset, DST/fold | DQ | PROPOSED (offset UNVERIFIED) | offset never assumed | PENDING |
| MD6-D08 | Defect severity classes | DQ | PROPOSED | soft-defect limits OPEN | PENDING |
| MD6-D09 | Rejection / quarantine / annotate mapping | DQ | PROPOSED | limits OPEN | PENDING |
| MD6-D10 | Same-timestamp semantics, duplicates, tie-breaking | DQ | PROPOSED | none | PENDING |
| MD6-D11 | Quote validity (crossed, zero-spread, nonpositive, nonfinite) | DQ | PROPOSED | spread bounds OPEN | PENDING |
| MD6-D12 | Incomplete windows | DQ | PROPOSED | min coverage OPEN | PENDING |
| MD6-D13 | Dataset exclusion and quarantine; who decides | DQ | PROPOSED | none | PENDING |
| MD6-D14 | Instrument capability profile | DQ | PROPOSED | none | PENDING |
| MD6-D15 | Pre-registration before any strategy evaluation | DQ/governance | PROPOSED | none | PENDING |
| MD6-D16 | Holdout custody of quality statistics | DQ/governance | PROPOSED | none | PENDING |
| MD6-D17 | Reference distribution for quantile parameterization | DQ | OPEN | quantile level OPEN | PENDING |
| MD6-D18 | Reporting extensions the policy would need | DQ (tooling dependency) | OPEN | none | PENDING |

No TP decision is in this register. Trading-performance decisions (Q-T01 to Q-T12, OPEN-VP-1 to 21, Q-B01 to Q-B03
in the master reports) are outside MD-6 and must not be inferred from it.

---

## MD6-D01 Definition and measurement of a gap

- **Question:** What exactly is a "gap", and between which instants is it measured?
- **Options:** (a) delta between consecutive ticks in stream order; (b) delta between consecutive ticks after
  sorting by UTC; (c) (a) plus synthetic edge points at window start and end (see D06); (d) measured only
  inside session intervals (see D04).
- **Proposed rule:** A gap is the duration `utc_msc[i+1] - utc_msc[i]` between consecutive ticks of the
  verified, ordered stream, measured on UTC instants only (never on raw broker time), in integer milliseconds.
  Comparison direction (strictly greater than vs greater or equal to the threshold) is fixed once, recorded in
  the preregistration, and applied identically everywhere. Gaps spanning a non-monotonic or excluded region
  are not measured by sorting around the defect (that would hide it); the region is flagged (D08).
  Gaps are measured per instrument and per window, never across instruments.
- **Rationale:** An unambiguous, integer, source-independent definition makes the count reproducible by any
  reviewer. Sorting before measuring would silently repair ordering defects and understate them.
- **Measurable test:** Recompute the list of (previous, next) tick pairs whose delta exceeds the registered
  threshold from the chunk files alone and compare to `gap_count` and `gaps_utc_msc`; equality required, with
  the truncation flag honoured (list capped at 200, count not capped). Synthetic fixture with deltas exactly
  at, one ms below and one ms above the threshold must classify identically in the stated comparison direction.
- **Data needed to decide:** none beyond the choice of comparison direction (Quant convention).
- **Dependency:** D06 (edges), D07 (UTC basis), D18 (tooling).
- **Owner:** Quant (comparison direction); Architect for contract wording.
- **Status:** PROPOSED. **Sign-off:** PENDING.

## MD6-D02 Parameterization of the maximum permitted gap

- **Question:** How is "too long without a tick" parameterized? All numeric values OPEN.
- **Options:**
  1. Single absolute `max_gap_ms` for all instruments and times. Simple; cannot distinguish an illiquid
     instrument or an overnight session from a feed outage; likely to over- or under-flag.
  2. Per-session absolute limits (for example a limit per session type from a calendar, D04). Needs a
     verified calendar.
  3. Per-instrument limit defined as a quantile of a documented reference distribution of inter-tick
     gaps (D17), optionally per session. Adapts to instrument activity; needs reference data and a rule
     that guarantees the reference is not the data under test.
  4. Combination: a hard absolute ceiling (any gap above it is a defect regardless of context) plus a
     context-dependent soft limit (option 2 or 3) that annotates rather than rejects.
- **Proposed rule:** Prefer option 4 as the structure to evaluate (two tiers, both OPEN), because it separates
  "certainly not a quiet market" from "unusual for this instrument and session". The parameterization
  (which option, which context keys, which quantile definition) is chosen and fixed in the preregistration
  before any strategy evaluation. No value is proposed. The parameters must be a function of documented
  non-holdout reference data and calendar facts only.
- **Rationale:** A single global number encodes an untested assumption that all instruments and sessions
  have the same activity profile. A two-tier form keeps hard rejection conservative and auditable.
- **Measurable test:** (a) Parameters are written in the preregistration with a commit/hash reference before
  the first strategy run (git history shows ordering). (b) Re-deriving per-instrument or per-session limits
  from the named reference artifact reproduces the registered numbers exactly. (c) Sensitivity table (D-level
  report): number of flagged gaps and affected coverage fraction at registered value and at neighbouring
  registered values, computed on non-holdout data without any strategy output.
- **Data needed to decide:** per-instrument inter-tick gap distributions from approved non-holdout reference
  data (does not exist yet: real extraction MD-1 is BLOCKED); session calendar (MD-5).
- **Dependency:** MD-1, MD-5, D03, D04, D17.
- **Owner:** Quant, with Market data for reference data.
- **Status:** OPEN (`max_gap_ms` = OPEN; tier ceiling = OPEN; quantile level = OPEN). **Sign-off:** PENDING.

## MD6-D03 Missing ticks vs legitimate low-activity periods

- **Question:** How can a missing-data interval be told apart from a real quiet market without using P/L?
- **Options for evidence (none sufficient alone):**
  1. Per-instrument tick-rate history from approved non-holdout reference data (D17).
  2. Session calendar and holiday calendar (D04): closed time inside a gap is expected and never counts as unexplained.
  3. Source heartbeat / connection logs (D05): terminal connected status, connection loss and
     reconnect records, extraction retry records.
  4. Cross-instrument concurrency: simultaneous gaps across many instruments from the same source
     suggest a source or connection fault; a gap in one instrument only suggests quiet or illiquidity.
     Use only as annotation, because correlated quiet periods exist (for example holidays).
  5. Source-reported sequence or flags, where the source provides them (capability dependent, D14).
- **Proposed rule:** First partition each gap `[prev, next)` into VERIFIED_OPEN, VERIFIED_CLOSED and
  UNKNOWN sub-intervals per [GAP-INTERVAL-PARTITION.md](GAP-INTERVAL-PARTITION.md). Then classify it on its
  VERIFIED_OPEN time only: `unverified` (any UNKNOWN time; no gate use), `expected_closed` (no open time),
  `within_threshold`, `source_fault_evidenced` (a recorded disconnect overlaps the VERIFIED_OPEN part), or
  `unexplained`. The open measure compared with the threshold (total VERIFIED_OPEN time or longest
  contiguous VERIFIED_OPEN run) is OPEN (Q14). `unexplained` gaps are never assumed to be quiet markets and never assumed
  to be faults: they are retained as annotations and counted. Classification uses only calendar,
  heartbeat and reference-rate evidence. It must never use price movement, spread behaviour after the gap,
  signals, trades or P/L.
- **Rationale:** Declaring a gap "legitimate" because it makes results look better is a look-ahead and
  survivorship risk (see threats). Evidence classes that exist independently of strategy output keep the
  classification auditable.
- **Measurable test:** For a labelled SYNTHETIC fixture with injected disconnects and closed intervals,
  classification reproduces labels exactly; classification is invariant when any strategy output file is
  added to or removed from the input (determinism test: hash of classification output equal). Each
  classified gap cites the evidence artifact id and its hash.
- **Data needed to decide:** a real calendar source (MD-5), heartbeat artifacts (D05), reference tick rates.
- **Dependency:** D02, D04, D05, D17, GAP-INTERVAL-PARTITION.md.
- **Owner:** Quant (rule), Market data (evidence availability).
- **Status:** PROPOSED (maximum tolerated `unexplained` coverage = OPEN). **Sign-off:** PENDING.

## MD6-D04 Session calendar and market-session boundaries

- **Question:** What defines when an instrument is expected to tick, and who supplies that fact?
- **Options:** (a) broker-published trading sessions captured at extraction time; (b) an exchange or
  venue calendar maintained in-repo; (c) a calendar inferred from observed ticks (rejected below);
  (d) no calendar (every gap treated alike).
- **Proposed rule:** Use an explicit, versioned, hashed calendar artifact per instrument whose source and
  evidence reference are recorded; the calendar is data, not code. A calendar inferred from the same ticks
  being judged is not allowed (circular). Session boundaries are interpreted in UTC after the verified time
  conversion (D07). A missing, partial, invalid or conflicting calendar makes the affected time UNKNOWN (gap
  `unverified`, never `expected_closed`), and the per-session option of D02 is unavailable. Boundary
  straddling, versions, venue-local conversion, overlaps and ticks inside closed time follow
  [GAP-INTERVAL-PARTITION.md](GAP-INTERVAL-PARTITION.md). Calendar changes (holidays, effective dates) are versioned.
- **Rationale:** Calendar source is itself an unresolved dependency (MD-5, Q-B03). Inference from the data
  under test would pass any outage as a closure.
- **Measurable test:** Calendar artifact has a content hash recorded in the manifest or preregistration;
  recomputing from the file matches; a check that every calendar interval has start < end and no overlaps
  per instrument; boundary instants are half-open (D06). Overlapping open sessions are reported as a
  calendar finding and unioned for measurement; zero-length or reversed entries reject the calendar version.
- **Data needed to decide:** calendar source, coverage dates, DST and holiday handling per instrument.
- **Dependency:** MD-5, Q-B03, D07.
- **Owner:** Quant and Market data; Rin if the calendar source implies a new data adapter.
- **Status:** OPEN. **Sign-off:** PENDING.

## MD6-D05 Broker/source disconnects and heartbeat evidence

- **Question:** What evidence of a connection outage is admissible and how is it recorded?
- **Options:** (a) terminal connection-status samples taken during extraction; (b) extraction retry log;
  (c) adapter-level heartbeat events in the NEXORA quality sidecar (ADR-012: disconnect/reconnect counters,
  freshness, market-closed states); (d) none (outages invisible).
- **Proposed rule:** Disconnect evidence is recorded as an append-only, hashed artifact with UTC
  intervals, source id and collection method, and is referenced from the manifest or preregistration.
  Historical bulk retrieval may have no heartbeat at all: in that case the artifact is declared absent and
  no over-threshold gap can be `source_fault_evidenced` (D03). Disconnect evidence applies only to the
  VERIFIED_OPEN part of a gap; absence of evidence is never evidence of continuity.
  Heartbeat evidence collected later than the data is labelled as such ("retrieval time, not original
  knowledge time").
- **Rationale:** Retrieved history cannot reveal its own outages; fail-closed labelling avoids false
  confidence. This aligns with ADR-012, which does not assert hidden tick completeness.
- **Measurable test:** Artifact schema validates; intervals are within the dataset window; hash matches;
  a SYNTHETIC fixture with a declared outage marks the overlapping gap `source_fault_evidenced`; a fixture
  without an artifact marks it `unexplained`.
- **Data needed to decide:** whether the Windows node can capture connection status during historical
  pulls (Operator); heartbeat tolerance (OPEN).
- **Dependency:** MD-1, ADR-012, D03.
- **Owner:** Operator / Market data (capture), Quant (admissibility).
- **Status:** PROPOSED (heartbeat tolerance = OPEN). **Sign-off:** PENDING.

## MD6-D06 Window boundaries, leading and trailing gaps

- **Question:** How are windows bounded and how are gaps at the edges measured?
- **Options:** (a) ignore edges (current inter-tick-only observation); (b) measure leading gap from window
  start to the first tick and trailing gap from last tick to window end; (c) additionally require a tick
  within a registered tolerance of each edge; (d) treat edges as unknown.
- **Proposed rule:** Windows are half-open in UTC: `[from_utc_msc, to_utc_msc)`. A tick at `from` belongs to
  the window; a tick at `to` does not. The leading gap is `first_tick - from`, the trailing gap is
  `to - last_tick`; both are reported separately from inter-tick gaps and judged by the same classification
  (D03) with the same threshold unless the preregistration states a different edge tolerance (OPEN). An
  empty window is a defined state (`no_ticks`), not a zero-gap success. The measurement convention
  (inclusive or exclusive at exactly the threshold) is the one fixed in D01.
- **Rationale:** Without edge measurement a window that starts with hours of silence reports no gap. Half-open
  windows tile without double-counting ticks or double-counting gaps.
- **Measurable test:** SYNTHETIC fixtures: tick exactly at `from`, exactly at `to`, no ticks, one tick; tiling
  two adjacent windows yields each tick exactly once and the sum of tick counts equals the combined window;
  leading plus inter-tick plus trailing durations sum to `to - from`.
- **Data needed to decide:** edge tolerance (OPEN); whether the window query semantic of the source is
  inclusive of the end (a source property verified in the dataset manifest).
- **Dependency:** D01, D07, D12, D18 (current report has no window bounds).
- **Owner:** Quant (tolerance), Architect (contract).
- **Status:** PROPOSED. **Sign-off:** PENDING.

## MD6-D07 UTC measurement, time offset and DST / fold windows

- **Question:** On which clock are gaps measured, and what happens when the offset or DST is not established?
- **Facts:** raw broker `time_msc` is stored; UTC is derived only via operator-declared segments with
  mandatory `evidence_ref`; no default offset; fold ranges are derived by rule and cannot be waived (cited
  tooling). The +10800 s offset seen on one feed (ADR-019 local workaround) is **UNVERIFIED**.
- **Proposed rule:** All gap measurement uses UTC instants produced by a verified conversion. If the offset
  or its effective dates are not documented with an `evidence_ref` for the window, gap results for that
  window are `UNVERIFIED` and cannot support any gate. Windows touching a derived fold range are excluded
  (quarantined, D13), not repaired. Gap measurement near a segment boundary must not mix raw and UTC. The
  value +10800 s is never assumed by this policy; if verified, it enters only through the evidence-backed
  segment declaration, never through this document.
- **Rationale:** A wrong offset shifts calendars and window edges; a repeated raw hour fabricates duplicate
  or negative-delta evidence; fail-closed exclusion avoids repairing what cannot be known.
- **Measurable test:** For every dataset used, every window tick range is covered by a segment with an
  `evidence_ref`; no tick with raw time in a derived fold range is present in an accepted window; changing a
  segment offset in a SYNTHETIC fixture changes the manifest identity (hash mismatch is detected).
- **Data needed to decide:** MD-2 offset evidence per segment and per symbol/server; DST behaviour.
- **Dependency:** MD-2, ADR-019.
- **Owner:** Market data / Quant (evidence), Operator (paired samples).
- **Status:** PROPOSED; offset itself **UNVERIFIED**. **Sign-off:** PENDING.

## MD6-D08 Defect severity classes

- **Question:** Which defects are hard (block use), soft (limited tolerance) or informational?
- **Options:** (a) keep the current split; (b) add classes per defect with thresholds; (c) all defects
  informational.
- **Proposed rule:** Three classes. **Hard:** defects that invalidate ordering or arithmetic and cannot be
  interpreted safely: non-monotonic timestamps, crossed quotes (ask < bid), nonpositive prices, non-finite
  quotes. These start from the existing `hard_defects` set as an observation. The structural proposal is
  that any single hard defect triggers the D09 disposition, which is equivalent to a tolerance of zero. That
  zero is itself a numeric value: it is a PROPOSED value pending Quant answer to Q5, not an approved or frozen
  one, and any other tolerance is OPEN. **Soft:** defects
  with a plausible benign cause whose tolerance is a Quant value: over-threshold gaps (`unexplained`),
  same-timestamp-different-quote, zero spread, exact duplicates. **Annotate:** facts recorded but not
  judged (ticks per day, `expected_closed` gaps, `source_fault_evidenced` gaps below any rejection rule).
  A defect class assignment may not change after preregistration.
- **Rationale:** Separating invalid from suspicious from informational lets each have an auditable
  disposition and avoids silently dropping data.
- **Measurable test:** Each defect counter in the report maps to exactly one class in the preregistration;
  fixture with one injected defect per type produces the registered disposition (reject, quarantine or
  annotate).
- **Data needed to decide:** per-defect tolerance for soft class (OPEN) from reference data.
- **Dependency:** D09, D10, D11.
- **Owner:** Quant.
- **Status:** PROPOSED (all tolerances OPEN). **Sign-off:** PENDING.

## MD6-D09 Rejection, quarantine and annotation policy

- **Question:** What happens to the data when a defect is present: reject the dataset, quarantine a window,
  or annotate and proceed?
- **Options:** (a) reject whole dataset on any defect; (b) reject only affected windows; (c) repair
  (interpolate, de-duplicate, sort); (d) annotate only.
- **Proposed rule:** No repair, interpolation, sorting or silent de-duplication of stored ticks. Hard
  defect: the scope is the whole dataset. The dataset is not accepted as evidence, its status stays
  `UNTRUSTED`, a dataset-level rejection entry (scope `DATASET`, keyed by content identity per
  [QUARANTINE-IDENTITY.md](QUARANTINE-IDENTITY.md)) is appended to the quarantine register (D13), and every window
  drawn from that dataset is recorded as quarantined (none is silently dropped). Soft defect over its registered tolerance (OPEN): window quarantined.
  Soft defect within tolerance: annotated and carried into results as a covariate field of the run, not
  hidden. Decisions are produced by a documented deterministic procedure and listed in a quarantine register
  (D13), never decided per run by inspecting results.
- **Rationale:** Repairs change evidence; per-run discretion invites tuning. A quarantine register makes
  survivorship visible.
- **Measurable test:** Applying the procedure twice to identical inputs yields byte-identical register;
  the number of excluded windows and excluded coverage fraction are reported; accepted plus quarantined
  windows equals the planned windows (no window vanishes).
- **Data needed to decide:** tolerances (OPEN).
- **Dependency:** D08, D13, QUARANTINE-IDENTITY.md.
- **Owner:** Quant (policy), Rin if governance is affected.
- **Status:** PROPOSED. **Sign-off:** PENDING.

## MD6-D10 Same-timestamp semantics, duplicates and tie-breaking

- **Question:** How are multiple ticks with the same millisecond handled?
- **Options:** (a) keep all in source order; (b) drop exact duplicates; (c) collapse to last quote; (d)
  collapse to first; (e) reject window.
- **Proposed rule:** Exact duplicate = same UTC ms and identical bid, ask (and identical flags where the
  source supplies them, D14), regardless of adjacency; detection is over the whole window, not only
  adjacent ticks. Same timestamp with a different quote is a distinct soft defect: source order is
  preserved exactly as stored (stable, source-defined), and no collapse rule is applied by the data layer.
  Any downstream collapse (for example "last quote at a timestamp") would be a semantics decision for
  the consumer and is OPEN. Stream order ties are never broken by price value (that would use market
  information) but only by stored source order.
- **Rationale:** Adjacent-only detection undercounts; price-based tie-breaking leaks market content into
  ordering; preserving source order keeps the dataset hash meaningful.
- **Measurable test:** SYNTHETIC fixture with non-adjacent exact duplicates is detected; shuffling order
  within a timestamp is detected as a content-hash change; a rule-based tie-break test shows output
  independent of bid/ask values.
- **Data needed to decide:** whether the source provides stable intra-millisecond order (MD-7 discovery).
- **Dependency:** MD-7, D14, D18 (current detection is adjacent-only).
- **Owner:** Quant, Market data.
- **Status:** PROPOSED (consumer collapse rule OPEN). **Sign-off:** PENDING.

## MD6-D11 Quote validity: crossed, zero-spread, nonpositive, non-finite

- **Question:** Which quote states are invalid, suspicious or valid?
- **Proposed rule:** Non-finite and nonpositive prices: hard (invalid). Crossed (ask < bid): hard.
  Zero spread (ask == bid): soft, annotated, tolerance OPEN, because it can be benign on some sources and
  can instead indicate a one-sided or last-trade quote; its meaning is established per source in the
  capability profile (D14), not assumed. Any spread cap (maximum plausible spread) is OPEN and is not
  derived from this document. A non-finite tick must not silently bridge a gap: gap measurement uses
  an explicit rule (OPEN: whether a non-finite tick counts as a tick for activity), fixed in preregistration,
  because the observed tooling skips such ticks for gap purposes.
- **Rationale:** Validity must be decidable per tick from the tick alone; any judgement needing price
  history is a distribution decision under holdout custody (D16).
- **Measurable test:** per-tick predicate tests on SYNTHETIC fixtures (NaN, infinity, zero, negative,
  ask<bid, ask==bid); counts equal the report counters; fixture with a non-finite tick inside a long gap
  shows the registered gap treatment.
- **Data needed to decide:** per-source spread semantics; spread caps (OPEN).
- **Dependency:** D08, D14, D16.
- **Owner:** Quant.
- **Status:** PROPOSED (zero-spread tolerance, spread cap, non-finite-activity rule: OPEN).
  **Sign-off:** PENDING.
- **Not covered:** stale/frozen quotes and quote bursts are outside this rule set (no gap or validity check
  detects them). Scope and definition are deferred to the Quant as Q13 in [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md).

## MD6-D12 Incomplete windows

- **Question:** What if a window has partial coverage, truncated retrieval or an unfilled tail?
- **Options:** (a) use what exists; (b) drop the window; (c) mark and keep with covariate; (d) require
  minimum coverage.
- **Proposed rule:** A window is `complete` only if it has a verified manifest, contiguous chunks, no
  truncated gap list affecting classification (or the full list is recomputed from files), edges measured
  per D06, and coverage above a registered minimum (OPEN). Coverage is `(verified_open_ms - open time of
  gaps classified unexplained or source_fault_evidenced) / verified_open_ms` for the window; any UNKNOWN time
  makes coverage UNVERIFIED, and a zero denominator is `NO_VERIFIED_OPEN_TIME` (GAP-INTERVAL-PARTITION.md). Otherwise it is `incomplete` and is excluded
  from performance evaluation, never filled, extrapolated or truncated to a clean subperiod after seeing
  results. The end-of-window rule (Q-B02 in master reports) is a separate, trading-side decision and is not
  decided here.
- **Rationale:** Choosing the usable subperiod after seeing outcomes is the classic survivorship path.
- **Measurable test:** `verify_dataset` equivalent (files only) recomputation yields the same status;
  planned windows vs completed windows listed; excluded fraction reported by instrument; every result family
  carries the list of excluded windows with their UTC bounds and exclusion reason, so a reviewer can see
  whether exclusions cluster in particular market periods (outcome-blind rules can still be
  regime-correlated). The list is disclosed, never used to re-select windows.
- **Data needed to decide:** minimum coverage (OPEN).
- **Dependency:** D06, D09, D13.
- **Owner:** Quant.
- **Status:** PROPOSED (minimum coverage OPEN). **Sign-off:** PENDING.

## MD6-D13 Dataset exclusion and quarantine; who decides

- **Question:** By what criteria is a dataset or window excluded, and who is authorized to decide?
- **Proposed rule:** Exclusion criteria are exactly those in preregistration (D15). A quarantine register
  (append-only, hash-chained, anchored by entry count and head hash stored independently, committed)
  records dataset-level and window-level decisions keyed by canonical `content_identity` (canonicalization
  version, SHA-256, ordered content hash, multiset fingerprint, record count) and per-record hashes, plus
  `dataset_id`, previous aliases, declared lineage, window id, instrument, defect class and preregistered
  criteria id, evidence ids, decision timestamp, post-evaluation label, decider and requalification history,
  as specified in [QUARANTINE-IDENTITY.md](QUARANTINE-IDENTITY.md). Labels alone never key an entry. The deciding authority is the Quant approver for criteria and the independent data reviewer
  (not the strategy author) for application; the author of a strategy run may not add or remove register
  entries. Decisions after the first strategy evaluation are labelled `post-evaluation` and any result
  computed on a changed inclusion set is invalid for gates G4/G5 and must be reported as exploratory.
  A new dataset identity never releases quarantined content: re-entry is checked by content
  (QUARANTINED_MATCH, DERIVED_FROM_QUARANTINED). Release is only by an appended requalification entry with
  new evidence (new retrieval, calendar or offset evidence), decided by the independent data reviewer or
  Quant approver who is neither the submitter nor a strategy author; it applies to one specific content
  identity and never grants TRUSTED. A missing or corrupt register makes every check UNVERIFIED. Release
  from quarantine does not reset exposure: exposure follows instrument, interval and content identity
  through lineage ([EVALUATION-EXPOSURE-LINEAGE.md](EVALUATION-EXPOSURE-LINEAGE.md) X1 to X3).
- **Rationale:** Separation of duties and a visible register prevent silent cherry-picking.
- **Measurable test:** Register entries reference preregistered criteria ids; every register entry's
  decision timestamp precedes the first strategy-evaluation timestamp or is labelled post-evaluation;
  inclusion set hash is recorded with every result; the reference tests in
  `tests/test_md6_quarantine_identity.py` (C1 to C10) pass.
- **Data needed to decide:** none; governance only.
- **Dependency:** D09, D15; AGENTS.md review independence.
- **Owner:** Quant (criteria), independent data reviewer (application), Rin (authority questions).
- **Status:** PROPOSED. **Sign-off:** PENDING.

## MD6-D14 Instrument capability profile (broker-agnostic)

- **Question:** How are differences among instruments and sources handled without hardcoding a broker?
- **Proposed rule:** A per-(instrument, source) **capability profile** is a data artifact with a hash,
  produced from adapter-supplied capabilities and canonical `instrument_id` (ADR-025), listing: source kind
  (tick or bar), bid/ask availability (`known` / `unavailable` / `unknown`), last-trade availability, flags
  availability, millisecond resolution, tick size and digits, lot constraints, whether commission and swap
  are `unavailable` (never zero), session-calendar availability, intra-millisecond order stability. Policy
  rules branch on profile fields, never on broker or terminal names. A bar-only source cannot support tick
  gap policy; it is outside this policy (a different quality definition is OPEN). Unknown fields default to
  the stricter treatment and block gates that need them.
- **Rationale:** Core code must stay broker-agnostic (CLAUDE.md section 4); unknown capability must not
  be read as capability.
- **Measurable test:** Two SYNTHETIC profiles differing only in broker label yield identical policy
  outputs; a profile with `unknown` bid/ask blocks bid/ask-dependent checks; profile hash is stored with
  each dataset identity.
- **Data needed to decide:** MD-7 real capability discovery.
- **Dependency:** MD-7, ADR-025.
- **Owner:** Architect (schema), Market data (population), Quant (gating rules).
- **Status:** PROPOSED. **Sign-off:** PENDING.

## MD6-D15 Pre-registration before any strategy evaluation

- **Question:** When and how are the thresholds and rules fixed?
- **Proposed rule:** All MD-6 parameters and rules above are written into a preregistration record
  ([template](PREREGISTRATION-TEMPLATE.md)) and committed with a hash/commit reference before the first
  strategy evaluation on the affected instruments and windows. After that, changes are forbidden. A change
  requires a new preregistration id and labels all earlier results superseded. A new preregistration id,
  dataset identity or evaluation family never resets exposure. Gate eligibility (INDEPENDENT / EXPLORATORY /
  UNVERIFIED / REJECTED_RETROSPECTIVE) is computed from the hash-chained exposure ledger in
  [EVALUATION-EXPOSURE-LINEAGE.md](EVALUATION-EXPOSURE-LINEAGE.md). Evidence is INDEPENDENT only if the
  preregistration was committed, by ledger position, before any exposure of any part of its windows or
  content (including overlapping windows and lineage-related datasets), and before the evaluation.
  Otherwise it is EXPLORATORY permanently. Missing or conflicting exposure history is UNVERIFIED. Every
  result family lists all preregistrations touching its instruments and windows, including superseded and
  abandoned ones, with a count.
- **Rationale:** Prevents threshold tuning toward desired results.
- **Measurable test:** Git ordering shows the preregistration commit precedes every evaluation artifact
  that cites it; the cited hash equals the hash of the committed file. The exposure ledger verifies (hash
  chain, contiguous positions, independent anchor, copies agree); the outcome of each evaluation is
  recomputed from the ledger and lineage alone; family disclosure equals the ledger-derived list and count.
- **Data needed to decide:** none.
- **Dependency:** G3 baseline freeze ordering (config hash before holdout);
  EVALUATION-EXPOSURE-LINEAGE.md; QUARANTINE-IDENTITY.md (content identity, lineage).
- **Owner:** Quant, Architect; Rin for freeze authority (this proposal does not freeze).
- **Status:** PROPOSED. **Sign-off:** PENDING.

## MD6-D16 Holdout custody of quality statistics

- **Question:** Can quality statistics on a protected holdout leak information?
- **Proposed rule:** For any protected range, only the existing count-and-coverage fields may be produced,
  and only by the custodian procedure; no price, spread or return distributions. Gap threshold values and
  reference distributions are never computed from protected-range data. Any observation of any statistic on a
  protected range, at any time, including after a preregistration, is an exposure event in the exposure
  ledger and makes every later preregistration touching that range EXPLORATORY for that range
  ([EVALUATION-EXPOSURE-LINEAGE.md](EVALUATION-EXPOSURE-LINEAGE.md) X4). Any new report field must be reviewed for holdout
  leakage before use (D18).
- **Rationale:** Even gap counts per day reveal volatility regimes and event days; thresholds set after
  seeing them carry information.
- **Measurable test:** Report schema check: no price-derived key; every protected-range access is a ledger
  entry; no protected-range access precedes, in ledger position, any preregistration whose evidence is
  labelled INDEPENDENT; reference-distribution input manifest excludes protected ranges.
- **Data needed to decide:** the protected-range declaration (exists in tooling as a required input).
- **Dependency:** G3, D15.
- **Owner:** Custodian / Rin; Quant (acceptance).
- **Status:** PROPOSED. **Sign-off:** PENDING.

## MD6-D17 Reference distribution for quantile parameterization

- **Question:** If limits are quantiles of a reference distribution, which data is the reference?
- **Options:** (a) a disjoint approved non-holdout period of the same instrument; (b) the dataset under test
  (rejected: circular, and masks degradations); (c) a pooled cross-instrument distribution.
- **Proposed rule:** The reference must be a named, hashed, non-holdout period disjoint from every
  evaluation window, with its own manifest and passing hard-defect checks, from the same source and a
  comparable calendar regime. Quantile definition (estimator), level, minimum sample size and per-session
  stratification are OPEN. Reference gaps are computed on `expected_open` (= VERIFIED_OPEN, GAP-INTERVAL-PARTITION.md) time only.
- **Rationale:** Separation of the calibration set from the evaluation set.
- **Measurable test:** Interval-disjointness check between reference and evaluation windows; reproducibility
  of the reference quantile from the reference manifest alone.
- **Data needed to decide:** real reference data (MD-1 BLOCKED).
- **Dependency:** MD-1, D04, D16.
- **Owner:** Quant.
- **Status:** OPEN. **Sign-off:** PENDING.

## MD6-D18 Reporting extensions the policy would need

- **Question:** Which report fields would the policy require that the cited tooling lacks?
- **Proposed list (requirements only, not an implementation request and not assumed present):**
  window bounds and leading/trailing gaps; non-adjacent duplicate detection; explicit treatment of
  non-finite ticks in gap measurement; gap classification fields (D03) with evidence references;
  uncapped or separately stored full gap list for verification; policy version and preregistration id in the
  report; no gap measurement across a non-monotonic tick (README observed fact 7: the cited tooling measures
  from the regressed tick and can double-count).
- **Rationale:** The policy cannot be tested end to end without them, and none may be assumed to exist.
- **Measurable test:** Each field is covered by a SYNTHETIC fixture before use for any gate.
- **Dependency:** any tooling change is its own task under AGENTS.md workflow; this proposal does not
  authorize code changes.
- **Owner:** Architect (assign), DEV owner of market-history lane.
- **Status:** OPEN. **Sign-off:** PENDING.
