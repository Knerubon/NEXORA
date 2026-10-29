# MD-6 exposure regime versus ADR-030 (VALID-1): reconciliation

Status: **DRAFT / PROPOSAL. Quant sign-off PENDING. Not authoritative.** This document does not change ADR-030,
does not change the status of any ADR and freezes nothing. Author: an engineer preparing a proposal (Wave V9
remediation, same-lineage self-review; independent review pending). It is not the human Quant approver.
Production runtime enforcement: **NOT VERIFIED** (documentation, reference models and tests only).

Authority consulted: `docs/decisions/ADR-030-replay-validation-framework-v1.md` ("architecture approved with
required doc fixes"; Phase 2A authorized; Q-V2 to Q-V5 open), AGENTS.md sections 0, 2, 3, 9, and the
implementation that exists at the base, `packages/nexora/validation/` (read only, not modified).

## 1. Why this document exists

[EVALUATION-EXPOSURE-LINEAGE.md](EVALUATION-EXPOSURE-LINEAGE.md) and [PREREGISTRATION-TEMPLATE.md](PREREGISTRATION-TEMPLATE.md)
introduce a preregistration ledger and gate-eligibility outcomes. ADR-030 already contains a pre-registration
safeguard (G9) inside an approved architecture. Two regimes for the same idea, written by different tracks,
must be reconciled before either is relied on. AGENTS.md section 3 requires that a cross-subsystem contract be
frozen by an ADR or recorded architecture review before dependent work; neither document is that decision.

## 2. Current behaviour of the PR #94 proposal

| Topic | Behaviour (proposal and reference model) |
|---|---|
| Unit of pre-registration | One `Registration` record per preregistration id, in an append-only hash-chained ledger; one evaluation per registration (G-g) |
| What is registered | strategy version, policy version, evaluation family id, targets (instrument, half-open window, dataset id, `content_identity`), declared prior exposure, decision and approval references (attested, not verified) |
| What makes evidence eligible | Computed, never labelled: exposure of the footprint (instrument, interval, content, lineage) before registration and before evaluation, no contending ACTIVE registration, exact match to the registration, first evaluation only |
| Outcomes | INDEPENDENT, EXPLORATORY, UNVERIFIED, REJECTED_RETROSPECTIVE, and (V9) PENDING_QUANT_AUTHORITY for declared multiplicity sets |
| Re-registration after seeing results | Permanently EXPLORATORY on that footprint; a new preregistration id, dataset id or family id never resets exposure |
| Multiplicity | Several variants on one exposure unit: no gate evidence, except one qualification per Quant-declared set whose authority is verified; verification is OPEN (QE-7). The correction method is OPEN |
| Disclosure | Every result family lists every preregistration and evaluation touching its footprint |
| Storage and custody | Ledger home, anchor custody and appender authentication are OPEN (QE-1, QE-8) |
| Enforcement | Test-only reference model. No runtime code reads or writes a ledger |

## 3. What ADR-030 requires (read from the ADR and the Phase 2A code)

| Ref | Requirement |
|---|---|
| G9 (Decision 5) | Pre-registration: subject selection, outcome definitions, splits and baselines are part of the **hashed run manifest**, fixed before Stage A. A result is only reported under the definitions it was run with. Changing a definition is **a new run**; **every run, including discarded ones, is kept** |
| Decision 9 | Descriptive metrics only; mandatory unconditional baseline; stratification pre-registered; **dependence disclosure** (overlapping subjects are not independent); **multiplicity disclosure** (total groups x definitions evaluated in the run); small-sample flag. No composite score. Statistical reporting method is Q-V5 (Quant, open) |
| Decision 10 | `run_id = "valid1-" + canonical_hash(manifest)`. Manifest covers dataset (ADR-014 `dataset_hash`), time contract, engines, code fingerprint and `git_commit`/`git_dirty`, evaluation definitions and baseline, splits (train/eval), mode. `git_dirty` or unknown commit marks the run **EXPLORATORY** (Q-V7). Definitions may be explored on `train` segments; cited evidence must come from a **pre-registered `eval` segment that was never used for exploration** |
| Decision 11 | File-based write-once bundle under `<runtime_root>/validation/<run_id>/`; nothing is deleted by the framework; discarded and INVALID runs stay on disk with their reason (supports G9) |
| Decision 17 | Results are evidence, never configuration. Changing a deterministic rule from VALID-1 evidence needs an ADR citing non-exploratory `run_id`s from pre-registered `eval` segments, Quant review and human approval |
| Q-V2 to Q-V5 | Quant decisions on the first outcome definition set, box units, gap policy default (Q-V4 overlaps MD-6), statistical reporting. All open; no default frozen |
| Phase 2A code | `qualification` is `DEVELOPMENT_ONLY` or `REVISION_QUALIFIED` (clean 40-hex commit and VALID result). The word EXPLORATORY of Decision 10 is not used in the code. A constant records that citation "also requires Quant-approved pre-registered definitions"; **nothing in `packages/nexora/validation` verifies any pre-registration, exposure or approval** |

## 4. Contradictions and gaps

| ID | Finding | Kind | Consequence |
|---|---|---|---|
| R-1 | **Two parallel pre-registration regimes.** G9 puts pre-registration inside the hashed run manifest (one run = one pre-registration); the proposal puts it in a separate ledger keyed by preregistration id. Nothing maps a VALID-1 `run_id` or manifest to a `Registration` or `Evaluation`, or says which one is authoritative | Contradiction (duplicate source of truth) | Evidence could be INDEPENDENT in one regime and unregistered in the other. AGENTS.md section 2 style "single source" principle is violated for pre-registration |
| R-2 | **Consequence of changing a definition.** G9: a changed definition "is a new run" with no stated penalty. Proposal: re-registration on an exposed footprint is permanently EXPLORATORY for gates (no reset) | Gap in ADR-030 / stricter proposal | The proposal is the missing "never used for exploration" mechanism that Decision 10 names but does not define. Needs the owners to confirm it is the intended meaning |
| R-3 | **Vocabulary collisions.** EXPLORATORY means "dirty or unknown revision" in ADR-030 Decision 10 but "exposure-tainted" in the proposal; the ADR itself says `DEVELOPMENT_ONLY` in Phase 2A. "INDEPENDENT" means gate-eligible evidence in the proposal and statistical independence of subjects in Decision 9. "G3", "G4", "G5" are ADR-030 safeguards (truncation, future-injection, future-state) and also EDGE-VALIDATION gate names (G3 baseline freeze; G4/G5 independent evidence) | Contradiction (terms) | Reports could cite a run as "EXPLORATORY" or "G3" meaning different things. A result can be REVISION_QUALIFIED and exposure-EXPLORATORY, or DEVELOPMENT_ONLY and exposure-INDEPENDENT; the combination rule is undefined |
| R-4 | **Unit of multiplicity.** ADR-030 allows many pre-registered groups x definitions in ONE run, with disclosure only (no correction, Q-V5 open). The proposal treats several variants registered on one exposure unit as contending registrations, all EXPLORATORY unless a Quant declaration exists; a single registration may carry only one evaluation (G-g, QE-4) | Contradiction (rules) | A single VALID-1 run with N definitions could be read as N variants (all EXPLORATORY) or one registration with N evaluations (disallowed by G-g) |
| R-5 | **Exposure semantics versus train/eval.** ADR-030 permits exploration on `train` segments and cites only `eval` segments; the proposal counts any observation of a footprint as exposure, with a possible exemption for Part A statistics (QE-3). The proposal has no `train`/`eval` notion; ADR-030 has no overlap rule between segments of different datasets, lineage-related datasets or instruments | Gap on both sides | Disjointness of `eval` from everything ever explored is asserted by ADR-030 but not checkable without a footprint ledger |
| R-6 | **Three dataset identities.** ADR-014 `dataset_hash` and `parent_dataset_id` (VALID-1), PR #96 `dataset_id` and `content_sha256` (tick extraction), and the proposal's QI `content_identity` and lineage. No mapping exists, and VALID-1 consumes ADR-014 event datasets while MD-6 concerns raw tick history | Gap | An exposure footprint cannot be computed for a VALID-1 run today |
| R-7 | **Every run is kept versus ledger placement.** Decision 11 keeps every run on disk under a runtime root; the proposal leaves the ledger home OPEN (QE-1) and requires every evaluation, including discarded ones, to be in the ledger. Two stores for the same fact risk divergence | Gap | A discarded VALID-1 run that never reached the ledger is an unlogged look (the proposal's stated limitation) |
| R-8 | **Approval references.** Decision 10 and the code say citation needs "Quant-approved pre-registered definitions"; the proposal carries `decision_ref` and `approval_ref` as attested text, and the multiplicity authority is unverified (QE-7). No registry of approvals exists in either | Shared gap | Neither regime can verify approval. V9 makes the proposal refuse to treat free text as verified; ADR-030 has no such guard |
| R-9 | **Time and offset.** ADR-030 records `time_contract = dataset_recorded_utc_event_time_and_received_at` and defers ADR-019/ADR-025 offset provenance; MD-6 treats the raw-to-UTC offset as UNVERIFIED (MD-2). Consistent in direction, not integrated | Alignment note | Both must wait for MD-2 before any real-data window claim |
| R-10 | **Gap policy overlap.** Q-V4 (gap policy default and `max_sample_gap_s`) and MD-6 (`max_gap_ms`) both set gap tolerances for different pipelines (event windows versus tick quality) | Overlap | Needs a statement that they are separate parameters or one decision |

## 5. Proposed engineering corrections

Within PR #94's own files and test-only code (no change to ADR-030, no production code):

| ID | Correction | Status |
|---|---|---|
| E-1 | State in the exposure document that it does not replace ADR-030 G9 and that the ledger is not the VALID-1 manifest (done in EVALUATION-EXPOSURE-LINEAGE.md header) | DONE |
| E-2 | Keep outcome vocabulary distinct in text and tests: use "exposure outcome" for the proposal's labels and "qualification" for ADR-030's (`DEVELOPMENT_ONLY`, `REVISION_QUALIFIED`); never combine them into one label | Documented here; wording pass in the exposure document is a follow-up |
| E-3 | Record in this document a candidate field mapping (below) so a future Architect decision has something concrete to accept or reject | PROPOSAL, below |
| E-4 | Add to the proposal that a VALID-1 run observing outcomes on a footprint is an Evaluation record in the ledger, including discarded and `DEVELOPMENT_ONLY` runs (consistent with "every run is kept") | PROPOSAL, depends on D-30-1 |

Candidate mapping, for discussion only (nothing here is implemented or frozen):

| Proposal record field | VALID-1 source | Gap |
|---|---|---|
| `evaluation_id` / `preregistration_id` | `run_id` (hash of the manifest) | Which one is the pre-registration id: the manifest hash, or a separate id issued before Stage A? Needs D-30-1 |
| strategy / policy version | `code_fingerprint`, `git_commit`, engine config versions | Policy version of MD-6 has no manifest field |
| evaluation family id | none | VALID-1 has no family concept; the proposal's family never resets exposure, so it can be dropped or kept as a label |
| targets (instrument, window, dataset, content identity) | manifest `dataset_hash`, `symbol`, `range_start`, `range_end`, `parent_dataset_id` | No `content_identity` or tick lineage for ADR-014 datasets (R-6) |
| prior exposure status | none | Must be computed from the ledger, not declared |
| `decision_ref`, `approval_ref` | none (Q-V2 to Q-V5 undecided) | Missing in ADR-030 (R-8) |

## 6. Decisions that require a human Quant, Architect or Rin

None of these is made or defaulted here. "Downstream impact" is on tests and evidence in this proposal.

| ID | Decision | Options | Owner | Downstream impact |
|---|---|---|---|---|
| D-30-1 | Is there ONE pre-registration source of truth, and which? | (a) the VALID-1 manifest is authoritative and the ledger records manifest hashes; (b) the ledger is authoritative and a manifest cites a preregistration id; (c) both, with a verified mapping | Architect, Rin (shared contract, AGENTS.md section 3), Quant | (a) shrinks `Registration` to a manifest reference; (b) needs ADR-030 amendment; (c) needs the mapping table above. Existing exposure tests stay valid as the model of the ledger |
| D-30-2 | Is "never used for exploration" (Decision 10) defined by the proposal's exposure rule? | yes / no / yes with exceptions (QE-3 Part A statistics) | Quant | If yes, R-2 and R-5 close; the `train`/`eval` split becomes a property of the ledger footprint |
| D-30-3 | Unit of multiplicity: a run with N pre-registered definitions | N registrations (contending, all EXPLORATORY without a declaration), or one registration with a finite set of evaluations (QE-4), or a declaration per run | Quant | Changes G-g and the declaration rules; decides whether Decision 9 disclosure alone is enough |
| D-30-4 | Combination rule of ADR-030 qualification and exposure outcome | citable only if `REVISION_QUALIFIED` and INDEPENDENT and approved; or another rule | Quant, Architect | Vocabulary and report shape |
| D-30-5 | Frozen versus revisable outcomes (QE-9) interacts with "every run is kept": whether a later invalid append voids earlier evidence | A or B (EVALUATION-EXPOSURE-LINEAGE.md section 5.5.1) | Quant, Rin (governance) | Selects which reference model is the default |
| D-30-6 | Verified Quant authority: what a verifier checks (QE-7, R-8) | registry, signed record, commit reference | Quant, Rin | Decides when a declared set can leave PENDING_QUANT_AUTHORITY |
| D-30-7 | Ledger home and custody for both regimes (QE-1, R-7) | `<runtime_root>/validation/...` alongside bundles, custodian store, signed commit | Architect, Rin, Operator | Storage decision; no test depends on it |
| D-30-8 | Separate or unified gap-tolerance parameters (R-10) | separate (event windows vs ticks) or unified | Quant | MD6-D02 and Q-V4 |

## 7. Impact on existing tests and evidence

- No test in `tests/test_md6_*` references ADR-030 or `packages/nexora/validation`. All passing `tests/test_md6_*` tests (and the two
  `xfail(strict=True)` tests) stay valid as executable specification of the proposal; none is evidence that D-30-1 to D-30-8
  were decided.
- If D-30-1 chooses (a): `Registration`/`Evaluation` fields in `tests/md6_exposure_reference.py` would shrink or become manifest
  references; exposure, contention, multiplicity and disclosure tests would be kept with new fixtures.
- If D-30-3 changes the unit of multiplicity: `test_c1_*`, `test_c2_*`, `test_f3_*`, `test_multiplicity_*` change.
- If D-30-5 chooses A: `tests/md6_exposure_frozen_prefix_proposal.py` becomes the reference and `test_f1_default_semantics_*`
  are replaced by `test_qe9_*` (which already exist, marked BLOCKED).
- ADR-030 Phase 2A evidence (its own tests and bundle checks) is untouched; this proposal adds no path into
  `packages/`.

## 8. What this document does not do

It does not decide any item in section 6, change ADR-030 or any ADR status, claim that either regime is enforced
at runtime, approve a gate, or set any numeric value. G3 (baseline freeze) remains NO-GO: no Quant sign-off exists
for any rule in this document set.
