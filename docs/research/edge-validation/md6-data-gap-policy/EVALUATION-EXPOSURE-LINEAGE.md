# MD-6 evaluation exposure lineage and preregistration eligibility

Status: **DRAFT / PROPOSAL. Quant sign-off PENDING. Not authoritative.**
Every numeric value stays `OPEN`. All examples are SYNTHETIC. This document sets no threshold and
grants no gate eligibility to any real result.

Executable specification (test-only, not runtime enforcement): `tests/md6_exposure_reference.py`,
`tests/md6_cross_track_reference.py`, `tests/test_md6_exposure_lineage.py`,
`tests/test_md6_exposure_n1_remediation.py` and, from Wave V9, `tests/test_md6_v9_structural.py`
(structural behaviour only) and `tests/test_md6_v9_policy_dependent.py` plus
`tests/md6_exposure_frozen_prefix_proposal.py` (behaviour that depends on unresolved Quant decisions;
every test there names its QE item). **Production runtime enforcement: NOT VERIFIED.**

Relation to ADR-030 (VALID-1, G9 pre-registration): see [ADR-030-RECONCILIATION.md](ADR-030-RECONCILIATION.md).
This proposal's registration ledger is NOT the VALID-1 run manifest and does not replace it; the
reconciliation lists the contradictions and the decisions that need a human Quant.

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

Round-2 review finding N1 (MAJOR) against head `dd3504e347fd2c75b38d400f9ba40cf6d0a48bcc`: exposure was
checked only at the registration's ledger position. Three policy variants registered up front on one
window, then evaluated one after another, all came out INDEPENDENT; so did a run after an unregistered
peek or a protected-statistics view placed between registration and evaluation. The rules G-h and G-i
(section 5.3) and the multiplicity rule (section 5.4) close this. The round-2 MINORs N2 (later supersede
changed an earlier outcome) and N3 (content identity compared only as an opaque key) are closed by R2/D-4
and by section 2.

Wave V8 independent review of head `930af2aef829fa14064d0ad0be0cfa4bda12fbec` (Wave V9 remediation, this
revision): F-1 (R2 "nothing appended later changes an outcome" was false for invalid and later-discovered
appends), F-2 (free text accepted as Quant authority), F-3 (declared members with disjoint footprints were
each INDEPENDENT alone), F-4 (flags-dropped and trailing-zero copies under a relabelled instrument evaded
exposure matching), and the minors F-5 to F-7. Dispositions: R2 is restated truthfully (section 5.5) with
the unresolved choice isolated as QE-9; M5 and M7 (section 5.4) close F-2 and F-3; I6 (section 2) closes F-4
as far as an approved contract allows (the remaining evasions are the open item QE-10).

## 2. Dependency on QUARANTINE-IDENTITY.md (Track C)

This document uses two definitions owned by `QUARANTINE-IDENTITY.md` (written in parallel; not
redefined here):

- `content_identity` = the five fields defined in QUARANTINE-IDENTITY.md section 7:
  `canonicalization_version`, `hash_algorithm`, `ordered_content_hash`, `multiset_fingerprint`,
  `record_count`. This document treats it as opaque and defines no canonicalization.
- Dataset lineage: `lineage_parents` (`LineageRef(dataset_id, ordered_content_hash)`) and
  `previous_aliases` from QUARANTINE-IDENTITY.md. For exposure this resolves to a status `ROOT`,
  `DERIVED` (with parents and the parent interval each part is derived from) or `UNKNOWN`.

Integration rule: two targets share content for exposure purposes when the QUARANTINE-IDENTITY.md
content relation would match them, that is equal `ordered_content_hash`, equal `multiset_fingerprint`
(reordered copy), or record containment via `record_hashes` (subset or superset). Concretely:

- I1. The exposure key of a dataset's content is its Track C `multiset_fingerprint` together with its
  `canonicalization_version`. A reordered copy therefore has the same exposure key.
- I2. A dataset whose records are a subset of another known dataset's records is `DERIVED` from it over
  its own span. The superset side needs no edge: exposure flows both ways through the subset's edge (X3).
- I3. A partial overlap of records cannot be mapped to a parent interval: lineage `UNKNOWN`, so every
  outcome touching it is UNVERIFIED.
- I4. Two content identities under different canonicalization versions are not comparable. Any such
  comparison is a `CANONICALIZATION_CONFLICT` and the outcome is UNVERIFIED.
- I5. The same content under two instrument labels conflicts with the lineage record: UNVERIFIED. Both
  sides become UNKNOWN, so the outcome does not depend on which dataset was listed first (V9: the earlier
  rule kept the first-listed label and made the first run's outcome depend on registry order).
- I6. Representation drift (V9 F-4). Two datasets that share more records under Track C's shadow key
  (time, bid, ask with trailing zeros and sign of zero ignored, `flags` ignored) than under exact record
  hashes, with one record set re-expressing the other's values, are `REPRESENTATION_DRIFT`. Under a
  different instrument label the lineage of both is UNKNOWN (UNVERIFIED): a trivial representation change
  must not let a relabelled copy escape exposure. Under the same label the instrument key already carries
  the exposure (EXPLORATORY). Whether two different labels denote the same instrument is NOT defined by any
  approved contract; the model reports the unresolved mapping as UNKNOWN and does not guess (QE-10).
  Time-shifted or price-scaled copies are not detected (`xfail` tests in
  `tests/test_md6_v9_policy_dependent.py`).

`tests/md6_cross_track_reference.py` computes I1 to I3, I5 and I6 from Track C's `content_profile` and feeds
them to the exposure model; I4 is enforced in the exposure model itself. Runtime enforcement stays a D18
dependency.

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
  are exposure for every later preregistration on that range (closes the D16 gap), and for every
  evaluation that comes after the view in the ledger, even under an earlier registration (G-h).
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
- G-h. At the evaluation's ledger position, no exposure event other than the evaluation itself has
  touched any part of the registration's footprint. An unregistered peek, an evaluation under any other
  registration, or a protected statistics view between registration and evaluation makes it EXPLORATORY.
- G-i. No other ACTIVE registration that appears in the ledger before the evaluation has a footprint that
  overlaps this registration's footprint (contention). A registration superseded or abandoned before the
  evaluation does not contend; one withdrawn after it still does.

G-h and G-i together give the rule **one exposure unit, at most one qualification**: an exposure unit is
any (instrument, interval, content) piece of a footprint, resolved through lineage. Only an evaluation
that is the first exposure of every unit in its footprint, and that no other live registration also
claims, can be INDEPENDENT. The outcome does not depend on the order in which evaluations are run: when
several registrations contend, every one of them is EXPLORATORY whichever is evaluated first (C6 tests).
A registration whose own footprint was already partly exposed still contends for the unexposed rest, so
the result stays order-independent. Contention that cannot be decided because a footprint does not
resolve is UNVERIFIED (G-a).

Outcomes, in precedence order:

1. **UNVERIFIED**: G-a fails, the cited preregistration is missing, or the evaluation's registration
   timestamp contradicts the ledger. Fails closed; not eligible.
2. **REJECTED_RETROSPECTIVE**: the registration's ledger position is after the evaluation it covers.
   A claimed timestamp cannot override position.
3. **EXPLORATORY**: any of G-c to G-i fails, or the evaluation cites no preregistration, or its
   registration is a member of a multiplicity declaration (M5). Never eligible.
4. **PENDING_QUANT_AUTHORITY** (declared multiplicity sets only, section 5.4): every structural and exposure
   check passed, but the Quant authority and correction references are free text that no verifier has
   confirmed. Never gate eligible; never relabelled to INDEPENDENT. It is distinct from UNVERIFIED (a
   defect) and from EXPLORATORY (exposure): it says only that an authority check is missing.
5. **INDEPENDENT**: otherwise. Only INDEPENDENT is gate eligible.

Consequences:

- A strategy-config change after holdout exposure is EXPLORATORY on that holdout, whether run under a
  new registration (G-c) or under the old one (G-e).
- Declaring `EXPOSED` when the ledger shows none also yields EXPLORATORY: the declarant may know of
  exposure the ledger lacks.

### 5.4 Several variants on one exposure unit (multiplicity)

No default allows several pre-declared variants on one exposure unit to yield gate evidence. If the
Quant wants to allow it, the only route is an explicit **multiplicity declaration** appended to the
ledger:

- M1. It names two or more ACTIVE registrations that appear earlier in the ledger, a Quant authority
  reference and a reference to the Quant-decided correction. A registration may be in at most one
  declaration, and a declaration needs two or more distinct members (otherwise the ledger fails to
  verify). Both references must be structurally well formed: text, not blank, no leading or trailing
  whitespace, printable ASCII without whitespace, at least one alphanumeric character, and not a literal
  that asserts absence (`NONE`, `NULL`, `N/A`, `TBD`, `PENDING`, `UNKNOWN`, ...). A malformed reference
  makes the set UNVERIFIED. This is structure only: the real reference format is OPEN (QE-1, QE-7).
- M2. It must be appended before the first evaluation of any member.
- M3. Every member must have exactly one evaluation; an incomplete set is UNVERIFIED.
- M4. The members' contention with each other and their evaluations' exposure of each other are set
  aside. Anything else still counts: an outside registration, an unregistered peek or a protected view
  makes the set EXPLORATORY.
- M5. The set as a whole yields **one** qualification. No member is ever INDEPENDENT on its own, in any
  order and wherever the declaration sits in the ledger: a member's evaluation cited alone is EXPLORATORY
  and carries the declaration id, also when its footprint is disjoint from every other member and also when
  the set itself qualifies. The set outcome does not depend on the order in which members were evaluated.
  (V9 F-3: before this rule was implemented, members on disjoint windows were each INDEPENDENT alone.)
- M6. The correction method, and any number in it, is OPEN (QE-7). The model does not apply or invent a
  correction.
- M7. Authority is never inferred from free text. A declaration with well-formed references yields
  PENDING_QUANT_AUTHORITY unless a verifier supplied from outside approves the pair; a verifier that
  fails or raises makes the set UNVERIFIED. What a verifier checks (a registry, a signed record, a
  commit) is OPEN (QE-7). The reference model injects a callable and ships none; its tests use a
  SYNTHETIC test double that is not an authority registry. Until a verified authority exists no declared
  set is INDEPENDENT, which is the structural default consistent with "no default allows several
  variants to yield gate evidence".

Without a declaration, G-i applies and every contending registration is EXPLORATORY.

Selection ACROSS footprints is not governed by this section (QE-7, finding F-5): five registrations on
five disjoint windows give five INDEPENDENT results, adjacent half-open windows do not contend, and
correlated instruments over one period do not contend. Nothing counts, corrects or limits the choice among
them, and no independence threshold is proposed (any such value would be a Quant decision). The tests
`test_qe7_characterization_*` record this behaviour; they do not endorse it.

### 5.5 Relabelling and outcome stability

- R1. The outcome is computed from the ledger. No record field holds it and no one sets it by hand.
- R2. **Default semantics: monotone non-improving.** An evaluation's outcome is computed from the ledger
  prefix that ends at the evaluation, plus two ledger-wide fail-closed inputs (whole-ledger validity and
  the lineage registry). Consequently:
  - R2a. An append that keeps the ledger valid (registration, supersede, abandonment, view, evaluation)
    never changes an earlier outcome. The one later entry the check looks at is the cited registration
    itself: if it appears only after the evaluation, the outcome is REJECTED_RETROSPECTIVE.
  - R2b. A multiplicity declaration appended later that names the evaluation's registration downgrades the
    standalone outcome to EXPLORATORY (M5).
  - R2c. An append that makes the ledger invalid (duplicate preregistration or evaluation id, a member in
    two declarations, a declaration with fewer than two distinct members, abandonment or supersede of an
    unknown id, a broken chain) fails whole-ledger verification: every outcome, including earlier
    INDEPENDENT ones, becomes UNVERIFIED.
  - R2d. Lineage is one registry input, not captured per ledger entry. A dataset added or discovered later
    that partially overlaps or re-expresses an earlier one (I3, I6), or that carries identical content
    under another label (I5), makes both sides UNKNOWN, so every outcome touching either becomes UNVERIFIED.
  - R2e. No append of any kind makes a non-INDEPENDENT outcome INDEPENDENT.
  The earlier wording "nothing appended later changes it" was wrong for R2b to R2d; this restatement
  replaces it (V9 F-1). All of R2b to R2d move toward UNVERIFIED or EXPLORATORY, never toward eligibility.
- R3. A request to change an outcome is refused, except a voluntary downgrade INDEPENDENT to
  EXPLORATORY by the result family. EXPLORATORY, PENDING_QUANT_AUTHORITY, UNVERIFIED and
  REJECTED_RETROSPECTIVE evidence can never become INDEPENDENT. A downgrade or a later supersede does not
  remove the evaluation from disclosure (D-4): an unfavourable INDEPENDENT result cannot be filed away.

#### 5.5.1 Frozen or revisable outcomes (QE-9, F-1): competing semantics, BLOCKED pending the Quant

Finding F-1 is a defect in BOTH layers. Specification: R2 promised stability while section 6 failed the
whole ledger closed, so the text contradicted itself. Implementation: `classify_evaluation` verifies the
whole ledger and takes lineage from one external registry. The reference model keeps the fail-closed
behaviour (B) as the default because every deviation of B from A moves toward UNVERIFIED; changing to A
would allow a previously issued outcome to stand after later events, which is a policy relaxation that only
a human Quant and governance owner may approve.

| | A: frozen prefix (proposal) | B: whole-ledger fail-closed (DEFAULT, implemented) |
|---|---|---|
| Later invalid append | earlier outcomes stand; the defect is reported as a ledger integrity alarm | every outcome UNVERIFIED |
| Later partial overlap or drift dataset | earlier outcome uses lineage captured at its ledger position | both sides UNKNOWN, earlier outcome UNVERIFIED |
| Later declaration naming the registration | earlier standalone outcome stands; set is EXPLORATORY (M2) | standalone outcome EXPLORATORY (M5) |
| Needs | lineage snapshot digest recorded in each registration and evaluation entry (a change to a shared contract, AGENTS.md section 3, depends on QE-1 and QE-6) | nothing new |
| Risk | an issued INDEPENDENT survives tampering of later entries; disagreement between alarm and label | a single bad append, accidental or hostile, voids all evidence (denial of evidence, F-7) |
| Code | `tests/md6_exposure_frozen_prefix_proposal.py`, NOT the default | `tests/md6_exposure_reference.py` |
| Tests | `tests/test_md6_v9_policy_dependent.py` `test_qe9_*` | `tests/test_md6_v9_structural.py` `test_f1_*`, `test_append_order_*` |

No document or test may cite A as policy. Until the Quant decides, B is the reference behaviour and R2 is
stated as R2a to R2e.

### 5.6 Disclosure (forking paths)

- D-1. Every result family MUST list every preregistration whose footprint overlaps the family's
  footprint, with its status (ACTIVE, SUPERSEDED, ABANDONED), and the count.
- D-2. The list is recomputable from the ledger. Any missing, extra or miscounted entry makes the family
  UNVERIFIED.
- D-3. A family is INDEPENDENT only if every member evaluation is INDEPENDENT and disclosure is complete;
  otherwise it takes its worst member outcome (UNVERIFIED worst, then REJECTED_RETROSPECTIVE, then
  EXPLORATORY).
- D-4. Every result family MUST also list every evaluation (registered or not, any outcome, including
  ones voluntarily downgraded or under a registration superseded later) whose footprint overlaps the
  family's footprint. A missing or extra entry makes the family UNVERIFIED.

### 5.7 What stays possible

Evaluation on data whose footprint is unexposed and uncontended at the evaluation remains INDEPENDENT
(B10, C9), including a new instrument, a later period, or a derived dataset over unexposed intervals.
Earlier exposure elsewhere does not taint it. Withdrawing a competing registration before any exposure of
the shared units releases the contention (C9).

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
| Footprint of a possibly contending registration does not resolve | UNVERIFIED |
| Content identities under different canonicalization versions compared | UNVERIFIED |
| Records partially overlap a known dataset (no parent interval) | UNVERIFIED |
| Multiplicity declaration with a blank or structurally malformed Quant authority or correction reference, or with a member not evaluated exactly once, or a verifier that fails | UNVERIFIED |
| Multiplicity declaration with well-formed references and no verified authority | PENDING_QUANT_AUTHORITY (not gate eligible) |
| Any later append that makes the ledger invalid (R2c) | every outcome UNVERIFIED (QE-9) |
| Same content under two instrument labels, or a representation-drift copy under another label (I5, I6) | UNVERIFIED for both sides, whatever the registry order |
| Declared multiplicity member cited alone (M5) | EXPLORATORY with the declaration id |

## 7. What stays OPEN

| ID | Question | Owner |
|---|---|---|
| QE-1 | Physical home and format of the ledger and its anchor (signed commit, custodian store); entry hash serialization; and the time authority: `LedgerAnchor(position, entry_hash)` carries no time, `recorded_at` is a synthetic counter, so no rule can say that an anchor existed before a given wall-clock event (F-6). No timestamp authority is invented here | Architect, Rin |
| QE-2 | Who may append protected statistics views, and how views outside tooling are logged | Custodian, Rin |
| QE-3 | Whether count-and-coverage statistics on non-protected evaluation windows (Part A acceptance) are exempt from exposure (X5) | Quant |
| QE-4 | Whether one registration may pre-declare a finite set of evaluations (G-g currently allows one) | Quant |
| QE-5 | Treatment of evidence UNVERIFIED only because history predates the ledger (no grandfathering proposed) | Quant, Rin |
| QE-6 | Final `content_identity` and lineage schema | Track C, QUARANTINE-IDENTITY.md |
| QE-7 | Whether several pre-declared variants on one exposure unit may yield gate evidence at all (section 5.4) and, if so, the correction method and its parameters; what counts as verified Quant authority (M7); and whether and how selection across disjoint footprints, adjacent windows and correlated instruments is governed (F-5; no independence threshold is proposed). Default until decided: no; every contending registration is EXPLORATORY and no declared set is INDEPENDENT | Quant |
| QE-8 | Whether withdrawing (superseding or abandoning) a registration before any exposure of the shared units should release contention (current structural default: yes, G-i), and whether a withdrawal needs an authenticated appender: today any registration that lists `supersedes` releases contention without an authority check (F-7). Also the denial-of-evidence side effects of fail-closed lookups: one registration with unknown lineage, or content under another canonicalization version, makes later evaluations that could contend with it UNVERIFIED (F-7) | Quant, Architect |
| QE-9 | Frozen or revisable outcomes (section 5.5.1): may an issued outcome be revised by a later invalid append, later lineage discovery or later declaration (B, default) or must it stand (A, BLOCKED)? | Quant, Rin (governance) |
| QE-10 | Which relabelled copies are the same market data: instrument equivalence (aliases, contract rolls, scaling) and time or price mappings. No approved contract defines them (ADR-025 fixes canonical ids, MD-2 leaves the offset UNVERIFIED), so the model detects only representation drift (I6) and reports the rest as an unresolved mapping | Architect, Market data, Quant |

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
| B3 | `[0,100)` exposed; P2 `[50,150)` and P3 `[100,150)` both registered before `[100,150)` is exposed | both EXPLORATORY (P2 exposed on `[50,100)`; P3 contended by P2 on `[100,150)`) | `test_b3_overlapping_window_is_exploratory_and_disjoint_part_is_eligible` |
| B3 alone | P3 `[100,150)` registered and evaluated with no contender; P2 registered later | P3 INDEPENDENT; P2 EXPLORATORY | `test_b3_disjoint_part_registered_alone_is_eligible` |
| B3 late | `[100,150)` registered after it was exposed | EXPLORATORY | `test_b3_disjoint_part_registered_after_its_exposure_is_exploratory` |
| B4 | Policy re-registered (supersedes P1) after viewing results | E2 EXPLORATORY; E1 stays INDEPENDENT (frozen, R2) and must be disclosed (D-4) | `test_b4_reregister_policy_after_viewing_results_is_exploratory` |
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
| misc | Unregistered run; repeat run; registration abandoned before its run | EXPLORATORY (abandonment after the run: frozen INDEPENDENT) | `test_unregistered_evaluation_is_exploratory`, `test_repeat_evaluation_under_one_registration_is_exploratory`, `test_abandoned_registration_is_exploratory_and_disclosed` |
| misc | Disclosure list and count; family outcome | incomplete disclosure is UNVERIFIED | `test_family_disclosure_lists_all_preregistrations_with_count`, `test_family_outcome_requires_complete_disclosure` |
| misc | Records carry no outcome fields | none present | `test_ledger_records_carry_no_outcome_fields` |

N1 remediation cases, all in `tests/test_md6_exposure_n1_remediation.py`:

| ID | Input | Expected | Test |
|---|---|---|---|
| C1 | Three policy variants registered up front on `[0,100)` (one family), evaluated in turn (R-B1) | all EXPLORATORY; complete disclosure does not help | `test_c1_three_preregistered_variants_evaluated_sequentially_yield_no_independent` |
| C2 | Same window, three family ids: up front / interleaved register-evaluate | all EXPLORATORY / only the first INDEPENDENT | `test_c2_same_window_three_family_ids_upfront_yield_no_independent`, `test_c2_same_window_three_family_ids_interleaved_only_first_is_independent` |
| C3 | Same content under three dataset ids, up front | all EXPLORATORY | `test_c3_same_content_under_three_dataset_ids_upfront_yield_no_independent` |
| C4 | `[0,100)`, `[50,150)`, `[150,250)` up front | first two EXPLORATORY; disjoint one INDEPENDENT | `test_c4_partially_overlapping_windows_contend_and_disjoint_window_does_not` |
| C5 | Two children sharing a parent interval; child and root; children on disjoint parent intervals | contend / contend / both INDEPENDENT | `test_c5_*` (3 tests) |
| C6 | All 6 evaluation orders of C1; both orders of the B3 mix | identical outcomes in every order | `test_c6_three_variants_every_evaluation_order_gives_identical_outcomes`, `test_c6_overlap_and_disjoint_mix_is_order_independent` |
| C7 | Re-registration (supersede) after exposure | new run EXPLORATORY; earlier result frozen and must be disclosed | `test_c7_reregistration_after_exposure_is_exploratory_and_earlier_result_is_frozen` |
| C8 | No ledger copy, empty ledger, no anchor; set check without ledger | UNVERIFIED | `test_c8_missing_exposure_registry_is_unverified` |
| C9 | Lone registration on unexposed window; contender withdrawn before / after exposure | INDEPENDENT / INDEPENDENT / EXPLORATORY | `test_c9_*` (3 tests) |
| C10 | Weekend-straddle gap partition (M1 regression) | 7 min VERIFIED_OPEN, VERIFIED | `test_c10_weekend_straddle_partition_still_counts_only_verified_open_time` |
| C11 | Quarantined content renamed, re-declared, reordered (M3 regression) | QUARANTINED_MATCH | `test_c11_quarantined_content_under_new_id_and_metadata_is_still_caught` |
| C12 | UNKNOWN lineage on own target / on a possible contender | UNVERIFIED | `test_c12_unknown_lineage_of_own_target_is_unverified`, `test_c12_unknown_lineage_of_a_possible_contender_is_unverified` |
| N1 | Unregistered peek / protected view between registration and run (R-B2, R-B3); relabel request | EXPLORATORY; relabel refused | `test_n1_*` (3 tests) |
| M | Quant multiplicity declaration: valid; no authority or correction; with a peek; after a member run; member unevaluated; two sets on one window; member in two sets | one qualification, members EXPLORATORY / UNVERIFIED / EXPLORATORY / EXPLORATORY / UNVERIFIED / EXPLORATORY / ledger invalid | `test_multiplicity_*`, `test_two_declared_sets_on_one_window_yield_no_qualification`, `test_registration_in_two_declarations_breaks_the_ledger` |
| N2 | Later supersede on another window (R-B4); voluntary downgrade | outcome unchanged; downgraded result must be disclosed | `test_n2_*` (2 tests) |
| N3 | Reordered copy; plus relabelled instrument; other canonicalization version (R-B5); subset; superset; partial overlap; disjoint | EXPLORATORY / UNVERIFIED / UNVERIFIED / EXPLORATORY / EXPLORATORY / UNVERIFIED / INDEPENDENT | `test_n3_*` (7 tests) |

Wave V9 cases. Structural ones are in `tests/test_md6_v9_structural.py`; those that depend on an unresolved
Quant decision are in `tests/test_md6_v9_policy_dependent.py` (marked with the QE item):

| ID | Input | Expected | Test |
|---|---|---|---|
| F-1 | Later invalid append (duplicate preregistration id, member in two declarations, abandon unknown id, supersede unknown id, duplicate evaluation id) after an INDEPENDENT run | DEFAULT B: UNVERIFIED (pinned); alternative A: INDEPENDENT with an integrity alarm (BLOCKED, QE-9) | `test_f1_default_semantics_*`, `test_qe9_alternative_a_*` |
| F-1 | Later partially overlapping dataset | B: UNVERIFIED; A with captured lineage: INDEPENDENT | `test_f1_default_semantics_a_later_partially_overlapping_dataset_*`, `test_qe9_alternative_a_lineage_captured_*` |
| F-1 | Valid non-declaration appends, seeded random ledgers | earlier outcomes identical; no append ever grants eligibility | `test_append_order_*` (seeds 11, 2026, 94096) |
| F-2 | Blank, whitespace, padded, non-ASCII, `NONE`/`TBD` references, with and without an approve-everything verifier | UNVERIFIED | `test_f2_malformed_authority_or_correction_reference_is_unverified` |
| F-2 | Well-formed free text (`x`, `QA-SYN`), default | PENDING_QUANT_AUTHORITY, never INDEPENDENT; verifier approval needed; failing verifier UNVERIFIED | `test_f2_*` |
| F-3 | Three declared members on disjoint windows, 6 evaluation orders x declaration before / between / after | members never INDEPENDENT alone; set outcome identical across orders; undeclared and non-member runs stay INDEPENDENT | `test_f3_*` |
| F-4 | E0 on dataset D (instrument A); copy of D with flags dropped, flags rewritten, trailing zeros padded or stripped, reordered, subset, half-drifted, all at once, relabelled B; both registry orders; both run orders | UNVERIFIED for both runs; controls (disjoint content, same time grid with other prices) INDEPENDENT | `test_f4_*` |
| F-4 | Time-shifted or price-scaled relabelled copy | NOT detected (`xfail(strict=True)`, QE-10) | `test_qe10_*` |
| F-5 | Five disjoint windows; adjacent windows; correlated instruments | each INDEPENDENT today (characterization, QE-7) | `test_qe7_characterization_*` |
| F-6 | Anchor structure | position and hash only, no time (QE-1) | `test_qe1_characterization_ledger_anchors_carry_no_time` |
| F-7 | Rogue supersede; unknown-lineage registration; other canonicalization version | contention released / later runs UNVERIFIED (QE-8) | `test_qe8_characterization_*` |
| N1 | Three overlapping variants, 36 registration x evaluation orders; seeded random orders over 3 to 5 variants | zero INDEPENDENT; identical outcomes per identity | `test_three_overlapping_variants_*`, `test_overlapping_variants_random_permutation_trials_*` |

## 9. Limitations

- This is documentation plus a test-only reference model. It is not runtime enforcement and nothing
  writes or reads a real ledger. Implementing the ledger, anchors and custodian logging is an
  implementation dependency (D18-style) that this proposal does not authorize.
- The model cannot detect exposure that never reached the ledger (an unlogged look). Mitigation is
  procedural: attestation, the custodian procedure for protected ranges, and the declared prior
  exposure field (a declared `EXPOSED` fails closed).
- `content_identity` and lineage are taken from Track C through `tests/md6_cross_track_reference.py`;
  the exposure model trusts the lineage registry it is given and fails closed only when an entry is
  missing, `UNKNOWN` or inconsistent. The bridge treats synthetic `raw_msc` as verified UTC; real data
  would first need MD-2 (offset).
- Production runtime enforcement: NOT VERIFIED. Everything here is a reference model plus tests.
- The model cannot tell a genuine Quant authority from free text; it only refuses to treat free text as
  verified (M7). The verifier is an injected callable that does not exist outside tests.
- Copies changed by anything outside the shadow key (time shift, price scaling, contract roll, alias
  symbols) are not detected (QE-10). A dataset whose time was re-expressed under a verified MD-2 offset is
  such a case.
- The lineage registry is trusted as supplied (R2d). Datasets that were never put in it are invisible.
- G-i is deliberately conservative: a registration that can never be INDEPENDENT itself (its footprint
  was partly exposed already) still blocks others on the units it shares. This keeps outcomes
  independent of evaluation order; the cost is that such a registration can burn a fresh unit until it
  is withdrawn before exposure (QE-8).
- Validity of decision and approval references (that they point to a signed Quant record) is attested,
  not checked by the model. Quant sign-off is PENDING for every rule here.
