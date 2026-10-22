# MD-6 evaluation exposure lineage and preregistration eligibility

Status: **DRAFT / PROPOSAL. Quant sign-off PENDING. Not authoritative.**
Every numeric value stays `OPEN`. All examples are SYNTHETIC. This document sets no threshold and
grants no gate eligibility to any real result.

Executable specification (test-only, not runtime enforcement): `tests/md6_exposure_reference.py` and
`tests/test_md6_exposure_lineage.py`.

## 1. Problem

Review finding M2 (MAJOR, design risk) against PR #94 head `3eaf57cbbedbc696fe69dcb6294933b34347c2cc`:

- [MD6-DECISION-REGISTER.md](MD6-DECISION-REGISTER.md) D15 says a post-preregistration change "requires
  a new preregistration id, a new dataset identity or evaluation family, and labels all earlier results
  superseded". Nothing says that results on instruments and windows that were already evaluated stay
  ineligible for gates under a later preregistration.
- [ACCEPTANCE-CRITERIA.md](ACCEPTANCE-CRITERIA.md) B-04 only invalidates "dependent" results.
- D16 says protected-range gap statistics are "not consulted before preregistration". Consultation after
  a first preregistration is therefore allowed and can feed a second preregistration.

Failure path: preregistration P1, evaluation, poor results, then P2 with a different `max_gap_ms` under
a "new evaluation family" on the same windows. Under the PR-head text P2 results are arguably
gate-eligible. The test `test_b1_old_rule_new_family_resets_and_wrongly_admits` encodes the PR-head rule
and shows it admits this case. The same reset works through a new dataset id (B2) and through
protected-range statistics viewed after P1 (`test_d16_protected_statistics_view_counts_as_exposure`).

The fix: exposure is a property of the data (instrument, time interval, content), recorded in an
append-only ledger. Labels such as family id, dataset id or preregistration id cannot reset it.

## 2. Dependency on QUARANTINE-IDENTITY.md (Track C)

This document uses two definitions owned by `QUARANTINE-IDENTITY.md` (written in parallel; not
redefined here):

- `content_identity` = the five fields defined in QUARANTINE-IDENTITY.md section 7:
  `canonicalization_version`, `hash_algorithm`, `ordered_content_hash`, `multiset_fingerprint`,
  `record_count`. This document treats it as opaque and defines no canonicalization.
- Dataset lineage: `lineage_parents` (`LineageRef(dataset_id, ordered_content_hash)`) and
  `previous_aliases` from QUARANTINE-IDENTITY.md. For exposure this resolves to a status `ROOT`,
  `DERIVED` (with parents and the parent interval each part is derived from) or `UNKNOWN`.

Integration rule (orchestrator reconciliation): two targets share content for exposure purposes when the
QUARANTINE-IDENTITY.md re-entry check would match them, that is equal `ordered_content_hash`, equal
`multiset_fingerprint` (reordered copy), or record containment via `record_hashes` (subset or superset).
A `CANONICALIZATION_CONFLICT` or `UNVERIFIED` from that check makes the exposure outcome UNVERIFIED. The
test-only reference model below compares `content_identity` as an opaque key and does not re-implement
the multiset or containment match; the (instrument, interval) key still catches a reordered copy on the
same instrument. Combining both checks in one model is a runtime implementation dependency (D18).

## 3. Definitions

- **Window**: half-open UTC interval `[from, to)` in integer milliseconds on one canonical
  `instrument_id` (ADR-025).
- **Target**: one (instrument, window, dataset id, `content_identity`) named by a record.
- **Registration record**: one preregistration committed to the ledger.
- **Evaluation record**: any run that observed strategy outcomes on any target, registered or not.
- **Protected statistics view**: any observation by any person or agent of any statistic (including
  count-and-coverage gap statistics) on a protected or holdout range.
- **Exposure event**: an evaluation record or a protected statistics view.
- **Exposure ledger**: append-only sequence of entries; each entry has position, previous entry hash,
  synthetic `recorded_at`, kind, record, and its own hash. Order is ledger position, never wall clock.
- **Footprint**: the set of (instrument, interval, `content_identity`) pieces of a target plus every
  ancestor piece it is derived from, resolved through lineage.
- **Exposed part**: the intersection of a footprint piece with an earlier exposure-event piece that has
  the same instrument or the same `content_identity`.
- **Anchor**: an independently stored (position, entry hash) pair, for example in a signed commit.

## 4. Required identities on every record

Every registration and evaluation record MUST carry all of these. A missing or empty field makes every
dependent result UNVERIFIED.

| Field | Registration | Evaluation |
|---|---|---|
| Strategy / baseline version | required | required |
| Policy version (MD-6 register version) | required | required |
| Evaluation family id | required | required |
| Per target: dataset id AND `content_identity` | required | required |
| Per target: canonical instrument and window `[from, to)` UTC ms | required | required |
| Prior exposure status (`UNEXPOSED` or `EXPOSED`, declared) | required | required |
| Registration timestamp | required (equals its ledger `recorded_at`) | the cited registration's, or none for an unregistered run |
| Decision reference and approval reference | required | required |
| Preregistration id cited | its own | the one it runs under, or none |
| Superseded preregistration ids | listed if any | n/a |

## 5. Normative rules

### 5.1 Ledger

- L1. Every registration, evaluation, protected statistics view and abandonment MUST be appended to the
  exposure ledger. Nothing is edited or removed.
- L2. Each entry MUST cite the previous entry hash (genesis: 64 zeros) and carry its own hash over its
  position, previous hash, `recorded_at`, kind and record.
- L3. Positions MUST be contiguous from 0. `recorded_at` MUST NOT decrease, and MUST equal the record's
  own timestamp.
- L4. Preregistration, evaluation and view ids MUST be unique. A supersede or abandonment MUST reference a
  preregistration that appears earlier in the ledger.
- L5. The ledger head MUST be anchored independently. An evaluation is assessable only when an anchor
  exists at or after its position and every anchor matches the ledger.
- L6. When more than one copy of the ledger exists, every copy MUST be a hash prefix of the longest
  copy. Any divergence is a conflict.
- L7. Records MUST NOT carry outcome values (P/L, win rate, trades, signals, drawdown). No rule reads one.

### 5.2 Exposure

- X1. Exposure is keyed by (instrument, time interval) and by `content_identity`. It MUST NOT be keyed by
  evaluation family id, dataset id or preregistration id. A new value of any of these does not reset it.
- X2. Overlap inheritance: any overlap of an exposure event with a later target exposes the overlapping
  part. Only the overlapping part is exposed.
- X3. Lineage inheritance: footprints are resolved through lineage in both directions. A derived dataset
  whose source interval was exposed is exposed on that interval. Exposure of a derived dataset exposes the
  parent interval it was derived from.
- X4. Protected-range or holdout statistics observed at any time, including after a preregistration,
  are exposure for every later preregistration on that range (closes the D16 gap).
- X5. Count-and-coverage quality statistics on non-protected evaluation windows, produced for Part A
  acceptance, are not exposure events. Whether this exemption is acceptable is OPEN (QE-3 below).

### 5.3 Gate eligibility

An evaluation is **INDEPENDENT** (eligible as independent evidence for G4/G5) only if all hold:

- G-a. The ledger, its copies and anchors verify (5.1), all lineage resolves, and every required
  identity is present.
- G-b. It cites a preregistration that appears in the ledger before it (ledger position).
- G-c. At the registration's ledger position, no part of the registration's footprint had been exposed.
- G-d. Declared prior exposure status of registration and evaluation equals the ledger-computed status.
- G-e. The evaluation matches its registration exactly: strategy version, policy version, family id
  and targets.
- G-f. The registration is still ACTIVE (not superseded, not abandoned).
- G-g. It is the first evaluation under that registration.

Outcomes, in precedence order:

1. **UNVERIFIED**: G-a fails, the cited preregistration is missing, or the evaluation's registration
   timestamp contradicts the ledger. Fails closed; not eligible.
2. **REJECTED_RETROSPECTIVE**: the registration's ledger position is after the evaluation it covers.
   A claimed timestamp cannot override position.
3. **EXPLORATORY**: any of G-c to G-g fails, or the evaluation cites no preregistration. Never eligible.
4. **INDEPENDENT**: otherwise.

Consequences:

- A strategy-config change after holdout exposure is EXPLORATORY on that holdout, whether run under a
  new registration (G-c) or under the old one (G-e).
- Declaring `EXPOSED` when the ledger shows none also yields EXPLORATORY: the declarant may know of
  exposure the ledger lacks.

### 5.4 Relabelling

- R1. The outcome is computed from the ledger. No record field holds it and no one sets it by hand.
- R2. A request to change an outcome is refused, except a voluntary downgrade INDEPENDENT to
  EXPLORATORY. EXPLORATORY, UNVERIFIED and REJECTED_RETROSPECTIVE evidence can never become INDEPENDENT.
  Appending new registrations later does not change an earlier evaluation's outcome.

### 5.5 Disclosure (forking paths)

- D-1. Every result family MUST list every preregistration whose footprint overlaps the family's
  footprint, with its status (ACTIVE, SUPERSEDED, ABANDONED), and the count.
- D-2. The list is recomputable from the ledger. Any missing, extra or miscounted entry makes the family
  UNVERIFIED.
- D-3. A family is INDEPENDENT only if every member evaluation is INDEPENDENT and disclosure is complete;
  otherwise it takes its worst member outcome (UNVERIFIED worst, then REJECTED_RETROSPECTIVE, then
  EXPLORATORY).

### 5.6 What stays possible

Evaluation on data whose footprint is unexposed at registration time remains INDEPENDENT (B10),
including a new instrument, a later period, or a derived dataset over unexposed intervals. Earlier
exposure elsewhere does not taint it.

## 6. Fail-closed behaviour

| Condition | Outcome |
|---|---|
| No ledger copy, empty ledger, or no anchor at or after the evaluation | UNVERIFIED |
| Broken hash chain, position gap, decreasing `recorded_at`, duplicate id | UNVERIFIED |
| Ledger copies diverge, or ledger truncated before an anchor | UNVERIFIED |
| Lineage missing, `UNKNOWN`, cyclic, or a derived window part without a recorded parent | UNVERIFIED |
| Target instrument or window conflicts with the lineage record of its `content_identity` | UNVERIFIED |
| Missing required identity field | UNVERIFIED |
| Cited preregistration not in ledger, or timestamp conflict | UNVERIFIED |
| Registration after the evaluation it covers | REJECTED_RETROSPECTIVE |

## 7. What stays OPEN

| ID | Question | Owner |
|---|---|---|
| QE-1 | Physical home and format of the ledger and its anchor (signed commit, custodian store); entry hash serialization | Architect, Rin |
| QE-2 | Who may append protected statistics views, and how views outside tooling are logged | Custodian, Rin |
| QE-3 | Whether count-and-coverage statistics on non-protected evaluation windows (Part A acceptance) are exempt from exposure (X5) | Quant |
| QE-4 | Whether one registration may pre-declare a finite set of evaluations (G-g currently allows one) | Quant |
| QE-5 | Treatment of evidence UNVERIFIED only because history predates the ledger (no grandfathering proposed) | Quant, Rin |
| QE-6 | Final `content_identity` and lineage schema | Track C, QUARANTINE-IDENTITY.md |

No numeric value is introduced by this document.

## 8. Adversarial cases

All in `tests/test_md6_exposure_lineage.py`. Windows and identities are SYNTHETIC.

| ID | Input | Expected | Test |
|---|---|---|---|
| B1 | P1 on `[0,100)`, evaluated; P2 same window, new family id | EXPLORATORY | `test_b1_identical_windows_new_family_id_is_exploratory` |
| B1 old | Same, under PR-head D15 text | old rule: eligible (defect) | `test_b1_old_rule_new_family_resets_and_wrongly_admits` |
| B2 | Same content, new dataset id | EXPLORATORY (old rule: eligible) | `test_b2_identical_data_new_dataset_id_is_exploratory` |
| B2 alias | Same content, relabelled instrument | UNVERIFIED (identity conflict) | `test_b2_instrument_relabel_with_same_content_is_not_eligible` |
| B2 key | Content key collides across instrument labels | exposed part found | `test_b2_content_key_collides_across_instrument_labels` |
| B3 | `[0,100)` exposed; P2 `[50,150)`; P3 `[100,150)` registered before its exposure | P2 EXPLORATORY (exposed `[50,100)`); P3 INDEPENDENT | `test_b3_overlapping_window_is_exploratory_and_disjoint_part_is_eligible` |
| B3 late | `[100,150)` registered after it was exposed | EXPLORATORY | `test_b3_disjoint_part_registered_after_its_exposure_is_exploratory` |
| B4 | Policy re-registered (supersedes P1) after viewing results | EXPLORATORY; E1 also EXPLORATORY (superseded) | `test_b4_reregister_policy_after_viewing_results_is_exploratory` |
| B5 | Strategy v2 registered on exposed holdout | EXPLORATORY | `test_b5_strategy_change_after_holdout_exposure_is_exploratory` |
| B5 old reg | Strategy v2 run under P1 | EXPLORATORY | `test_b5_changed_strategy_under_original_registration_is_exploratory` |
| B6 | Request INDEPENDENT for exploratory evidence; append a laundering registration | refused; stays EXPLORATORY | `test_b6_relabel_exploratory_as_independent_is_refused` |
| B6 decl | False `UNEXPOSED` declaration | EXPLORATORY | `test_b6_false_unexposed_declaration_does_not_help` |
| B7 | Child derived from exposed parent over `[50,150)` | EXPLORATORY | `test_b7_partially_overlapping_derived_dataset_is_exploratory` |
| B7 ctrl | Child over unexposed `[100,200)` | INDEPENDENT | `test_b7_derived_dataset_on_unexposed_interval_is_independent` |
| B7 up | Exposure of child taints parent | EXPLORATORY | `test_b7_exposure_of_child_taints_parent` |
| B8 | Broken hash chain | UNVERIFIED | `test_b8_broken_hash_chain_is_unverified` |
| B8 | Gap in ledger (exposure entry removed) | UNVERIFIED | `test_b8_gap_in_ledger_is_unverified` |
| B8 | Two ledger copies diverge (stale prefix accepted) | UNVERIFIED | `test_b8_two_ledgers_disagree_is_unverified` |
| B8 | Tail truncated before anchor | UNVERIFIED | `test_b8_tail_truncated_against_anchor_is_unverified` |
| B8 | No ledger, no anchor, UNKNOWN lineage, missing parent | UNVERIFIED | `test_b8_missing_history_and_unknown_lineage_are_unverified` |
| B8 | Missing approval reference | UNVERIFIED | `test_b8_missing_required_identity_is_unverified` |
| B8 | Registration timestamp conflict | UNVERIFIED | `test_b8_registration_timestamp_conflict_is_unverified` |
| B9 | Registration appended after the evaluation it covers | REJECTED_RETROSPECTIVE | `test_b9_retrospective_registration_is_rejected` |
| B10 | Fresh window and fresh instrument, registered before exposure | INDEPENDENT | `test_b10_future_evaluation_on_unexposed_data_is_independent` |
| D16 | Holdout gap stats viewed after P1, then P2 on holdout | EXPLORATORY (old D15/D16: permitted, eligible) | `test_d16_protected_statistics_view_counts_as_exposure` |
| misc | Unregistered run; repeat run; abandoned registration | EXPLORATORY | `test_unregistered_evaluation_is_exploratory`, `test_repeat_evaluation_under_one_registration_is_exploratory`, `test_abandoned_registration_is_exploratory_and_disclosed` |
| misc | Disclosure list and count; family outcome | incomplete disclosure is UNVERIFIED | `test_family_disclosure_lists_all_preregistrations_with_count`, `test_family_outcome_requires_complete_disclosure` |
| misc | Records carry no outcome fields | none present | `test_ledger_records_carry_no_outcome_fields` |

## 9. Limitations

- This is documentation plus a test-only reference model. It is not runtime enforcement and nothing
  writes or reads a real ledger. Implementing the ledger, anchors and custodian logging is an
  implementation dependency (D18-style) that this proposal does not authorize.
- The model cannot detect exposure that never reached the ledger (an unlogged look). Mitigation is
  procedural: attestation, the custodian procedure for protected ranges, and the declared prior
  exposure field (a declared `EXPOSED` fails closed).
- `content_identity` and lineage are taken from Track C; this model trusts the lineage registry it is
  given and fails closed only when an entry is missing, `UNKNOWN` or inconsistent.
- Validity of decision and approval references (that they point to a signed Quant record) is attested,
  not checked by the model. Quant sign-off is PENDING for every rule here.
