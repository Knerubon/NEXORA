# MD-6 preregistration template

Status: **TEMPLATE. Not a preregistration.** All fields are `PENDING` or `OPEN`. A preregistration is valid
only if it is completed from a signed Quant decision record (see [SIGNOFF-PACKAGE.md](SIGNOFF-PACKAGE.md))
and committed **before any strategy evaluation** on the instruments and windows it names.

## Binding rules

1. Fixed before evaluation: thresholds, instruments, windows, quarantine rules and analysis plan.
2. Post-hoc change is forbidden. A change creates a new preregistration id; results under a superseded id
   are exploratory only. A new id, family or dataset id does not reset exposure; eligibility follows
   [EVALUATION-EXPOSURE-LINEAGE.md](EVALUATION-EXPOSURE-LINEAGE.md).
3. No field may be derived from P/L, win rate, trades, signals, backtest output or holdout results.
4. Commit/hash references below must resolve; the hash of this file as committed is cited by every
   evaluation artifact that depends on it.
5. Nothing here authorizes data extraction, a freeze, a merge or an order.

## Header

```text
Preregistration id:                 <PENDING>
Policy version (MD-6 register):     <PENDING>   (proposal head SHA: <PENDING>)
Signed Quant decision record ref:   <PENDING>   (commit/hash)
Author / independent data reviewer: <PENDING> / <PENDING>
Created (UTC):                      <PENDING>
This file's content SHA-256:        <PENDING>   (computed after commit; cited by evaluation artifacts)
Commit containing this file:        <PENDING>   (must precede first evaluation artifact commit)
First strategy-evaluation commit:   <PENDING>   (must be later than the line above, or NOT STARTED)
Evaluation family id:                <PENDING>
Strategy / baseline version:         <PENDING>
Per target: instrument, [from,to) UTC ms, dataset id, content_identity (QUARANTINE-IDENTITY.md): <PENDING>
Prior exposure status (UNEXPOSED / EXPOSED, ledger-computed): <PENDING>
Registration timestamp = exposure ledger recorded_at:          <PENDING>
Exposure ledger position and entry hash / anchor ref:          <PENDING>
Decision reference / approval reference:                       <PENDING> / <PENDING>
Supersedes preregistration ids:                                <PENDING / none>
All preregistrations touching these instruments/windows (id, status) and count: <PENDING>
```

## 1. Scope

| Field | Value |
|---|---|
| Instruments (canonical `instrument_id`, ADR-025) | PENDING |
| Source kind and source version per instrument | PENDING |
| Capability profile hash per instrument/source (D14) | PENDING |
| Evaluation windows, half-open UTC `[from, to)` | PENDING |
| Protected/holdout ranges (declared; not accessed) | PENDING |
| Dataset ids and manifest hashes | PENDING |
| Content identity (ordered hash, multiset fingerprint, canonicalization version) per dataset | PENDING |
| Time conversion segments with `evidence_ref`, derived fold ranges | PENDING (offset UNVERIFIED until evidenced) |

## 2. Thresholds and rules (all numeric values OPEN until signed)

| Parameter | Value | Source of decision |
|---|---|---|
| Gap comparison direction (D01) | OPEN | Quant record |
| Gap parameterization option (D02) | OPEN | Quant record |
| `max_gap_ms` or per-session / per-instrument limits (D02) | OPEN | Quant record |
| Hard ceiling gap (D02, tier 1) | OPEN | Quant record |
| Reference distribution id, hash, period, estimator, quantile level, minimum sample (D17) | OPEN | Quant record |
| Edge tolerance, leading/trailing (D06) | OPEN | Quant record |
| Minimum window coverage (D12) | OPEN | Quant record |
| Heartbeat tolerance (D05) | OPEN | Quant record |
| Max `unexplained` gap coverage (D03) | OPEN | Quant record |
| Soft-defect tolerances: zero spread, same-timestamp-different-quote, exact duplicate (D08) | OPEN | Quant record |
| Spread cap, if any (D11) | OPEN | Quant record |
| Non-finite tick effect on gap measurement (D11) | OPEN | Quant record |
| Gap open measure: total VERIFIED_OPEN or longest contiguous run (D03, Q14) | OPEN | Quant record |
| Offset rule id, hash, evidence_ref per venue-local calendar (D04, D07) | OPEN | Market data record |
| Consumer collapse rule for same timestamp (D10) | OPEN | Quant record |
| Calendar artifact id, hash, source, effective dates (D04) | OPEN | Quant/Rin record |
| Disconnect evidence artifact id, hash, or `ABSENT` (D05) | PENDING | Operator |

## 3. Defect to disposition map (D08, D09)

| Report counter | Class (hard/soft/annotate) | Disposition (reject/quarantine/annotate) | Tolerance |
|---|---|---|---|
| non_monotonic_count | PENDING | PENDING | OPEN |
| crossed_quote_count | PENDING | PENDING | OPEN |
| nonpositive_price_count | PENDING | PENDING | OPEN |
| nonfinite_quote_count | PENDING | PENDING | OPEN |
| exact_duplicate_count | PENDING | PENDING | OPEN |
| same_timestamp_different_quote_count | PENDING | PENDING | OPEN |
| zero_spread_count | PENDING | PENDING | OPEN |
| gap_count (by class: unverified / expected_closed / within_threshold / source_fault_evidenced / unexplained) | PENDING | PENDING | OPEN |
| calendar_data_contradiction_count | PENDING | PENDING | OPEN |
| leading/trailing gap | PENDING | PENDING | OPEN |

## 4. Quarantine and exclusion rules (D09, D12, D13)

- Criteria ids and text: PENDING (must be exhaustive; no discretionary criteria).
- Applier (independent data reviewer): PENDING.
- Register location, canonicalization version (`nexora.md6-tick-content/1` proposed), hash algorithm
  (SHA-256) and anchor (entry count, head hash) location: PENDING.
- Re-entry is checked by content identity per [QUARANTINE-IDENTITY.md](QUARANTINE-IDENTITY.md); a new
  dataset identity never releases quarantine. Release only by an appended requalification (independent
  authority, new evidence, specific content identity); never grants TRUSTED.
- Planned windows = accepted + quarantined + incomplete (must reconcile): PENDING.

## 5. Analysis plan (data-quality only)

- Reports to be produced per window: quality report, gap classification, sensitivity table at registered
  and neighbouring registered values, excluded-coverage fraction per instrument: PENDING.
- Sensitivity table uses non-holdout data and no strategy output: PENDING.
- Statistics are counts and coverage only; no price/spread distributions for protected ranges (D16).
- Stopping rule for data acceptance: PENDING.
- Trading-performance analysis plan is **not** part of this record and is preregistered separately,
  after this record is committed (Part B firewall).

## 6. Attestations (to be completed by the humans responsible; never pre-filled)

```text
No P/L, win-rate, trade, signal, backtest or holdout output was used to choose any value above: <PENDING>
No protected-range data was accessed to compute any value above: <PENDING>
Real data used for calibration is non-holdout and disjoint from evaluation windows: <PENDING>
Offset evidence_ref present for every window or status recorded UNVERIFIED: <PENDING>
No evaluation, backtest or protected-range statistic touching these windows, overlapping windows or
lineage-related datasets was observed before this registration's ledger position: <PENDING>
Every earlier preregistration touching these instruments/windows (including superseded and abandoned)
is listed above: <PENDING>
```

## 7. Amendments

Amendments are not allowed in place. Append only: a new preregistration id referencing the superseded id,
with reason and date. Superseded id: PENDING / none.
