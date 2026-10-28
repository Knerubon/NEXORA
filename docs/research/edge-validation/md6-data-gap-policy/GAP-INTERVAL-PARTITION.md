# MD-6 gap interval partition

Status: **DRAFT / PROPOSAL. Quant sign-off PENDING. Not authoritative.**

This document specifies how an over-threshold gap is split into open, closed and unknown time before it is
judged. It refines [MD6-DECISION-REGISTER.md](MD6-DECISION-REGISTER.md) D03, D04, D05, D12 and D17 and
criterion A-06 in [ACCEPTANCE-CRITERIA.md](ACCEPTANCE-CRITERIA.md). No numeric threshold, tolerance or
limit is chosen here. Every such value stays `OPEN`. Numbers in examples are SYNTHETIC.

The rules are backed by an executable specification in `tests/md6_gap_partition_reference.py` with
adversarial tests in `tests/test_md6_gap_partition.py`. That code is a test-only reference model. It is
not runtime enforcement and does not change any package.

## 1. Problem (finding M1, C2/C9; finding m3)

At proposal head `3eaf57cb`:

- D03 classifies an over-threshold gap as `expected_closed` only if it lies **entirely** inside a verified
  closed interval; otherwise it is `source_fault_evidenced` or `unexplained`.
- D04 gives no rule for a gap that straddles a session boundary.
- A-06 divides unexplained gap duration by the **full** window duration.

Reproduction (SYNTHETIC): verified closed interval `[100 h, 148 h)`; last tick 2 min before the close,
first tick 5 min after the reopen. Only 7 min of open-market time passed without a tick, but the rule as
written classifies the whole 48.12 h span as `unexplained` and counts all of it in A-06. The same rule
also lets a disconnect recorded during the closure "explain" a gap (finding m3), because disconnect
overlap is tested against the whole gap rather than its open part.

## 2. Definitions

- **Instant:** an integer number of milliseconds on the UTC time line, produced by the verified D07
  conversion. Raw broker time is never used.
- **Interval:** half-open `[start, end)` with `start < end`. A zero-length interval is not an interval.
- **Gap:** the half-open span `[prev, next)` between two consecutive observed ticks in stored order
  (D01). Leading and trailing edges of a window (D06) are spans `[from, first)` and `[last, to)` and are
  partitioned the same way.
- **Calendar version:** one artifact with identity (artifact id, content SHA-256, version string), a time
  basis (`UTC` or `VENUE_LOCAL`), a coverage interval in which it is authoritative, regular open sessions,
  and exception entries (`EXCEPTION_CLOSED` for holidays and early closes, `EXCEPTION_OPEN` for special
  sessions). Inside coverage, any instant not open is closed.
- **Offset rule:** for a `VENUE_LOCAL` calendar, an evidenced transition table: a list of
  `(utc_start, offset)` segments plus a UTC coverage end, with an `evidence_ref`. In tests it is an
  explicit SYNTHETIC table, so no system time-zone database is needed.
- **Labels:**
  - `VERIFIED_OPEN`: exactly one admissible calendar version covers the instant and says open.
  - `VERIFIED_CLOSED`: exactly one admissible calendar version covers the instant and says closed, and
    no observed tick contradicts it.
  - `UNKNOWN`: anything else (no coverage, invalid calendar, conflicting evidence, two versions claiming
    authority, unresolved local time, missing offset evidence, unverified tick time, or a tick observed
    inside the closed run).
- **`expected_open`** (used by D17) means `VERIFIED_OPEN`.

## 3. Normative rules

### 3.1 Partition

- P1. A gap `[prev, next)` MUST be partitioned into contiguous, non-overlapping, maximal sub-intervals,
  each with exactly one label, one reason, the calendar identity that produced it (or none), and the
  offset-rule identity used (or none). Adjacent sub-intervals with the same label, reason, calendar
  identity and offset-rule identity MUST be merged.
- P2. The durations of the sub-intervals MUST sum to `next - prev`.
- P3. `prev == next` MUST give an empty partition with outcome `NO_GAP`.
- P4. `next < prev` MUST give outcome `REFUSED_NON_MONOTONIC` with no sub-intervals. Ticks MUST NOT be
  sorted to make a partition possible (D01, D08).
- P5. Inputs MUST be integer milliseconds. Non-integer or boolean values are rejected.
- P6. The partition MUST be a pure function of the gap instants, the calendar versions (with their
  offset rules) and the D07 verification flag. It MUST NOT take or depend on any price, spread, signal,
  trade, P/L or other strategy output. Reordering entries inside a calendar artifact MUST NOT change the
  result.

### 3.2 Calendar semantics and identity

- C1. Inside a version's coverage: regular open sessions are unioned (overlapping sessions count once and
  are reported as an `OVERLAPPING_OPEN_SESSIONS` calendar finding); `EXCEPTION_CLOSED` overrides regular
  sessions; `EXCEPTION_OPEN` overrides closed time; everything else is closed.
- C2. An instant covered by both an `EXCEPTION_OPEN` and an `EXCEPTION_CLOSED` entry of the same version
  is `UNKNOWN` (reason `CONFLICTING_EVIDENCE`).
- C3. An instant inside the coverage of two or more calendar versions is `UNKNOWN` (reason
  `CALENDAR_AUTHORITY_CONFLICT`), whether or not they agree, because no single identity can be recorded.
  Versions that tile time by effective date (disjoint coverage) are allowed; each sub-interval records
  its own version identity.
- C4. An instant outside every version's coverage is `UNKNOWN` (reason `NO_CALENDAR_COVERAGE`). A gap
  that runs past the end of coverage has an `UNKNOWN` tail. No calendar at all means the whole gap is
  `UNKNOWN`.
- C5. A calendar version with an incomplete identity, an empty or reversed coverage, or any entry with
  `start >= end` (including a zero-length session) is rejected: the whole partition is `UNKNOWN` (reason
  `CALENDAR_INVALID`) and a `CALENDAR_INVALID` defect is recorded. Zero-length entries are rejected, not
  ignored, so a malformed artifact is always visible.
- C6. The calendar is the authority. A holiday that is missing from the calendar version is counted as
  `VERIFIED_OPEN`. This errs toward flagging the gap, never toward excusing it. The recorded version
  identity shows which artifact made the claim, and a corrected calendar is a new version (D04, D13).
- C7. A calendar inferred from the ticks being judged remains forbidden (D04).

### 3.3 Time basis and offset provenance (D07)

- T1. Gap instants MUST come from the verified D07 conversion. If the tick UTC conversion for the gap is
  not verified, the whole partition is `UNKNOWN` (reason `TICK_UTC_UNVERIFIED`).
- T2. A `VENUE_LOCAL` calendar MUST carry an offset rule with an `evidence_ref`, strictly increasing
  segment starts and a coverage end after the last start. Otherwise, or if any calendar boundary
  (coverage or entry) falls outside the rule's coverage, the whole partition is `UNKNOWN` (reason
  `TZ_EVIDENCE_MISSING`) and a `TZ_EVIDENCE_MISSING` defect is recorded.
- T3. A local boundary with exactly one consistent UTC reading converts to that reading.
- T4. A local boundary that is ambiguous (repeated hour, two readings) or nonexistent (skipped hour, no
  reading) MUST NOT be guessed. The span between the two candidate readings is `UNKNOWN` (reason
  `TZ_AMBIGUOUS_OR_NONEXISTENT`) and a `TZ_BOUNDARY_UNRESOLVED` defect is recorded. Time that is open
  or closed under both readings keeps its label.
- T5. The offset-rule identity is recorded with every sub-interval it produced.

### 3.4 Observed ticks that contradict the calendar

- K1. If a tick at either end of the span lies at an instant labelled `VERIFIED_CLOSED`, the whole
  maximal closed run containing it becomes `UNKNOWN` (reason `TICK_INSIDE_VERIFIED_CLOSED`) and a
  `CALENDAR_DATA_CONTRADICTION` defect is recorded per tick. The calendar is not silently trusted, and
  the tick is not dropped or moved.
- K2. Because sessions are half-open, a tick exactly at a session's close instant lies in closed time and
  triggers K1. A tick exactly at an open instant is inside the session and does not.

### 3.5 Judging a gap (partition first, then evidence)

- J1. Only `VERIFIED_OPEN` time contributes to the eligible open-market gap duration.
  `VERIFIED_CLOSED` time is never unexplained. `UNKNOWN` time is never treated as closed and never as
  eligible open time; it is reported separately as `unknown_ms`.
- J2. Outcome per gap, in this order:
  1. `refused_non_monotonic` or `no_gap` per P3 and P4.
  2. `unverified` if the partition contains any `UNKNOWN` time. No gate may use this gap's result.
     The measured open time is still reported as a lower bound.
  3. `expected_closed` if open time is zero.
  4. `within_threshold` if the open measure (J3) does not exceed the threshold in the D01 direction.
  5. `source_fault_evidenced` if a D05 disconnect interval overlaps the `VERIFIED_OPEN` part of the
     gap (overlap in ms is reported, with overlapping disconnect intervals unioned).
  6. `unexplained` otherwise.
- J3. **Open measure (OPEN Quant choice).** Two structural options are defined and both are always
  reported:
  - `TOTAL_VERIFIED_OPEN`: the sum of all `VERIFIED_OPEN` time in the gap.
  - `LONGEST_CONTIGUOUS_VERIFIED_OPEN`: the longest run of adjacent `VERIFIED_OPEN` time.

  Recommended structural default for Quant review: `TOTAL_VERIFIED_OPEN`. Rationale: it is never
  smaller than the longest run, so it is the fail-closed choice; it catches a feed that stays silent
  across many short sessions separated by breaks (each run may be short while the total silence is
  long); and it is the same quantity that A-06 sums. The recommendation is structural only. Which
  option is used, and the threshold value, are fixed in the preregistration.
- J4. Disconnect evidence never applies to closed or unknown time (finding m3). A disconnect during a
  closure neither explains nor worsens the gap. Heartbeat tolerance and admissibility stay `OPEN` (D05).
- J5. The threshold, the comparison direction, the open measure and the edge threshold are required
  inputs with no default.

### 3.6 Window coverage (A-06, D12)

- W1. The leading edge, inter-tick gaps and trailing edge tile the window `[from, to)` (D06). Zero-length
  spans (equal timestamps) are skipped as `no_gap`.
- W2. `verified_open_ms` of a window is the sum of `VERIFIED_OPEN` time over all its spans. It is the
  denominator for both A-06 and D12.
- W3. A-06 numerator: the total `VERIFIED_OPEN` time of spans classified `unexplained`. The comparison
  of `unexplained / verified_open` with a limit is `OPEN` (Q4).
- W4. D12 coverage: `covered_open_ms = verified_open_ms - open time of spans classified unexplained or
  source_fault_evidenced`; coverage is `covered_open_ms / verified_open_ms`. The minimum is `OPEN` (Q9).
  The numerators always use total open time of a flagged span, whatever open measure J3 selects.
- W5. Window status: `REFUSED_NON_MONOTONIC` if stored ticks decrease; `NO_TICKS` if there are none
  (D06 state, never a success); `UNVERIFIED` if any span has `UNKNOWN` time (A-06 and D12 cannot pass);
  `NO_VERIFIED_OPEN_TIME` if the denominator is zero (the ratio is undefined, never treated as full
  coverage); otherwise `VERIFIED`. Ratios are kept as integer numerator and denominator.

## 4. Outcome vocabulary

| Level | Values |
|---|---|
| Sub-interval label | `VERIFIED_OPEN`, `VERIFIED_CLOSED`, `UNKNOWN` |
| Reason | open: `SESSION_OPEN`, `EXCEPTION_OPEN`; closed: `OUTSIDE_SESSIONS`, `EXCEPTION_CLOSED`; unknown: `NO_CALENDAR_COVERAGE`, `CALENDAR_INVALID`, `CALENDAR_AUTHORITY_CONFLICT`, `CONFLICTING_EVIDENCE`, `TZ_AMBIGUOUS_OR_NONEXISTENT`, `TZ_EVIDENCE_MISSING`, `TICK_UTC_UNVERIFIED`, `TICK_INSIDE_VERIFIED_CLOSED` |
| Partition status | `VERIFIED`, `UNVERIFIED`, `NO_GAP`, `REFUSED_NON_MONOTONIC` |
| Gap class | `no_gap`, `refused_non_monotonic`, `unverified`, `expected_closed`, `within_threshold`, `source_fault_evidenced`, `unexplained` |
| Defect / annotation | `CALENDAR_DATA_CONTRADICTION`, `CALENDAR_INVALID`, `OVERLAPPING_OPEN_SESSIONS`, `TZ_EVIDENCE_MISSING`, `TZ_BOUNDARY_UNRESOLVED` |
| Window status | `VERIFIED`, `UNVERIFIED`, `NO_TICKS`, `REFUSED_NON_MONOTONIC`, `NO_VERIFIED_OPEN_TIME` |

Each partition records the set of calendar identities and offset-rule identities it used.

## 5. Fail-closed behaviour

- Missing, partial, invalid or conflicting calendar evidence gives `UNKNOWN`, never closed.
- Missing offset evidence, or an unverified tick conversion, makes the whole partition `UNKNOWN`.
- Ambiguous or nonexistent local times are never resolved by guessing.
- A tick inside closed time withdraws trust in that closed run.
- Any `UNKNOWN` time makes the gap `unverified` and the window `UNVERIFIED` for A-06 and D12.
- Out-of-order ticks are refused, never sorted. Empty windows and zero-denominator windows are explicit
  non-success states.
- Existing rules are kept: absence of disconnect evidence is not evidence of continuity (D05), and a
  calendar inferred from the data under test is not allowed (D04).

## 6. What stays OPEN

| Item | Owner |
|---|---|
| Open measure: `TOTAL_VERIFIED_OPEN` (recommended structural default) or `LONGEST_CONTIGUOUS_VERIFIED_OPEN` | Quant |
| Gap threshold value(s) and comparison direction (D01, D02) | Quant |
| Edge threshold for leading and trailing spans (D06) | Quant |
| Maximum `unexplained / verified_open` per window and dataset (A-06, Q4) | Quant |
| Minimum D12 coverage (Q9) | Quant |
| Heartbeat tolerance and disconnect admissibility (D05, Q10) | Quant, Operator |
| Calendar source, maintenance and versioning process (D04, R1) | Rin / Architect, Market data |
| Offset rule evidence per venue and server (D07, O3) | Market data, Operator |
| Whether a tick at the exact close instant should be admissible through a calendar convention (for example a source that stamps the closing quote at the close) | Quant, Market data |

## 7. Adversarial cases

All inputs are SYNTHETIC. `H` = hour, `M` = minute, `D` = day; the weekly calendar has Mon-Fri sessions
`[00:00, 22:00)` UTC and day 0 is a Monday. Thresholds in the tests are SYNTHETIC test inputs only.

| Id | Input | Expected outcome | Test |
|---|---|---|---|
| A1 | Gap of 17 M inside a session | One `VERIFIED_OPEN` sub-interval of 17 M; status `VERIFIED` | `test_a1_gap_entirely_within_open_session` |
| A2 | Span (window leading edge) entirely inside the weekend closure | One `VERIFIED_CLOSED` sub-interval; class `expected_closed` | `test_a2_span_entirely_within_verified_closure_is_expected_closed` |
| A2b | Inter-tick gap with both ticks inside the closure | `UNKNOWN` (`TICK_INSIDE_VERIFIED_CLOSED`), two `CALENDAR_DATA_CONTRADICTION` defects; class `unverified` | `test_a2_inter_tick_gap_inside_closure_is_calendar_data_contradiction` |
| A3 | Last tick 21:50, next tick 00:20 next day | Open 10 M + closed 2 H + open 20 M; total 30 M, longest run 20 M | `test_a3_gap_crossing_a_close_and_reopen` |
| A4 | Closure `[100 H, 148 H)`, ticks at close - 2 M and open + 5 M | Open exactly 420000 ms, closed 48 H, longest run 300000 ms; total and longest measures can classify differently | `test_a4_weekend_straddle_counts_only_seven_open_minutes` |
| A4-old | Same input under D03 as written at `3eaf57cb` | `unexplained` with 48 H 7 M (48.12 h) counted | `test_a4_old_rule_classifies_full_span_unexplained` |
| A4-win | Window around the A4 gap | A-06 denominator 9 M of open time, numerator 7 M; old A-06 used 48.12 h over 48.15 h | `test_a4_window_unexplained_coverage_uses_verified_open_denominator` |
| A5 | Thursday 21:58 to Monday 00:05 | Five sub-intervals; open 22 H 7 M, closed 52 H, longest run 22 H | `test_a5_gap_spanning_daily_breaks_and_weekend` |
| A6 | Calendar v2 with Wednesday holiday and Thursday early close | Holiday `VERIFIED_CLOSED` (`EXCEPTION_CLOSED`); open 7 M; v2 identity on every sub-interval | `test_a6_holiday_and_exceptional_closure_from_versioned_calendar` |
| A6b | Same gap, calendar v1 without the holiday | Holiday counted `VERIFIED_OPEN` (22 H 7 M), gap flagged `unexplained`; documented limitation C6 | `test_a6_exception_missing_from_calendar_version_counts_as_open` |
| A6c | Special Saturday session | `VERIFIED_OPEN` (`EXCEPTION_OPEN`) | `test_a6_exception_open_special_session_on_weekend` |
| A7 | Venue-local sessions across a SYNTHETIC spring-forward | UTC boundaries shift by the evidenced offset; offset-rule identity recorded | `test_a7_dst_session_converted_with_evidenced_rule` |
| A7b | Session start in the repeated local hour (fall-back) | 1 H `UNKNOWN` (`TZ_AMBIGUOUS_OR_NONEXISTENT`); class `unverified` | `test_a7_ambiguous_local_boundary_is_unknown_not_guessed` |
| A7c | Session end in the skipped local hour (spring-forward) | 1 H `UNKNOWN` between the two readings | `test_a7_nonexistent_local_boundary_is_unknown_not_guessed` |
| A7d | No rule, rule without `evidence_ref`, rule not covering the calendar | Whole partition `UNKNOWN` (`TZ_EVIDENCE_MISSING`) | `test_a7_missing_offset_evidence_makes_whole_partition_unverified` |
| A7e | Tick UTC conversion not verified | Whole partition `UNKNOWN` (`TICK_UTC_UNVERIFIED`) | `test_a7_unverified_tick_utc_conversion_makes_partition_unknown` |
| A8 | No calendar | Whole gap `UNKNOWN` (`NO_CALENDAR_COVERAGE`) | `test_a8_missing_calendar_is_all_unknown` |
| A8b | Calendar coverage ends mid-gap | Tail `UNKNOWN`; class `unverified` | `test_a8_partial_calendar_tail_is_unknown` |
| A8c | Overlapping `EXCEPTION_OPEN` and `EXCEPTION_CLOSED` | Overlap `UNKNOWN` (`CONFLICTING_EVIDENCE`) | `test_a8_contradictory_exceptions_in_one_version_are_unknown` |
| A8d | Two versions with overlapping coverage | Overlap `UNKNOWN` (`CALENDAR_AUTHORITY_CONFLICT`) | `test_a8_two_versions_claiming_authority_are_unknown` |
| A8e | Two versions tiling by effective date | `VERIFIED`; each sub-interval carries its own identity | `test_a8_versions_tiling_by_effective_date_record_each_identity` |
| A9 | Tick exactly at the close instant | Closed run `UNKNOWN`, `CALENDAR_DATA_CONTRADICTION` | `test_a9_tick_exactly_at_close_is_inside_closed_and_flagged` |
| A9b | Tick 1 ms before close, next tick exactly at open | Open 1 ms, rest `VERIFIED_CLOSED`, no defect | `test_a9_tick_one_ms_before_close_and_exactly_at_open` |
| A9c | `prev == next` | Empty partition, `NO_GAP` | `test_a9_zero_duration_gap_is_empty_partition` |
| A9d | Calendar with a zero-length session | Rejected: `UNKNOWN` (`CALENDAR_INVALID`), deterministic | `test_a9_zero_length_session_rejects_calendar_deterministically` |
| A10 | Overlapping sessions `[0, 10 H)` and `[5 H, 12 H)` | Union, open counted once (8 H), `OVERLAPPING_OPEN_SESSIONS` finding | `test_a10_overlapping_sessions_are_unioned_without_double_counting` |
| A10b | `next < prev`; window with decreasing stored ticks | `REFUSED_NON_MONOTONIC`, no sorting | `test_a10_out_of_order_timestamps_refused_never_sorted` |
| m3 | Disconnect only during the closure | `unexplained` (old rule: `source_fault_evidenced`) | `test_m3_disconnect_over_closed_portion_does_not_explain_open_gap` |
| m3b | Disconnects overlapping the open part | Overlap counted on open time only (2 M), `source_fault_evidenced` | `test_m3_disconnect_over_open_portion_counts_only_open_overlap` |
| W | Window with an `UNKNOWN` tail; window tiling | `UNVERIFIED`; spans sum to `to - from` | `test_window_with_unknown_span_is_unverified`, `test_window_spans_tile_window_exactly` |
| INV | Several gaps incl. empty and reversed | Durations sum to `next - prev`; contiguous and maximal; identical output and hash on repeat and on reordered calendar entries | `test_invariant_partition_sums_and_is_deterministic` |
| INV | Signatures | No strategy input; threshold, direction, measure, edge threshold have no default; non-integer input rejected | `test_invariant_no_strategy_input_in_signatures`, `test_invariant_threshold_has_no_default`, `test_invariant_rejects_non_integer_timestamps` |

## 8. Limitations

- This is documentation plus a test-only reference model. Runtime enforcement, report fields (partition
  segments, identities, defects, open measures) and manifest wiring are implementation dependencies in
  the sense of D18 and are not authorized here.
- The calendar is the authority (C6). A wrong calendar gives wrong labels; the protection is that errors
  of omission flag gaps rather than excuse them, and every label cites the calendar version.
- The offset rule is modelled as an explicit transition table. Real rules depend on MD-2 evidence, which
  is BLOCKED; nothing here verifies any real offset, including the UNVERIFIED +10800 s value.
- A tick exactly at a close instant is a contradiction under the half-open convention. Sources that stamp
  a closing quote at the close may need a documented calendar convention; that is OPEN (section 6).
- Disconnect admissibility and heartbeat tolerance are not modelled beyond interval overlap.
