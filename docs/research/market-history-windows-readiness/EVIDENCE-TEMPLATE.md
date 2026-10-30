# Evidence template

One record per real-half execution. Machine-readable form:
[evidence-record.template.json](evidence-record.template.json) (schema version
`nexora.windows-readiness-evidence/5`; round 4 added the custody fields the gate needs, a
structured verifier argv, the recorded gate decision and the overall value `BLOCKED`; V9 the signed
pin and lineage fields (`/4`); V10 `custody.dataset_ref`, structured `execution.commands_run`,
principal-id people and positive version/build formats (`/5`)).

## Which artifact decides

| Artifact | Role |
|---|---|
| `tests/windows_integration_readiness/evidence_validator.py` | **Authoritative.** The single field specification, and `validate_record`, which rejects any record that is incomplete, contradictory or claims more than it proves. Run it: `python -m tests.windows_integration_readiness.evidence_validator <record.json>` (exit 0 only with no errors). |
| [evidence-record.schema.json](evidence-record.schema.json) | Generated from the validator (`build_schema`) and drift-checked by `test_evidence_template.py`. A structural gate only: it cannot express identity distinctness, checkout cross-checks or the derivation of `overall_result`. A record that passes the schema but not the validator is rejected. |
| This page | Explains the rules. `test_evidence_template.py` checks that the tables below name exactly the validator's fields and non-claims. |

Neither the validator nor the schema verifies that a value is **true**, that a person is who they
claim to be, or anything about Windows or MT5. The validator's report always says
`"windows_verified": false` and `"identity_authenticated": false`. Identities are declared strings
(`identity_basis` must be `declared_not_authenticated`); there is no signature or key check.

## Value rules

- Every field listed below is required. A field is either a real value of its kind (text, 40-hex
  SHA, 64-hex SHA-256, UTC timestamp ending in `Z` or `+00:00`, boolean, integer, a reference
  `{"path": ..., "sha256": ...}`, a non-empty list, a list of distinct positive sequence numbers
  (may be empty), or a non-empty object) or the literal `"NOT VERIFIED"`. Blank, whitespace-only,
  `[]` and `{}` are invalid (except an empty sequence list). Unknown keys are invalid.
- **Placeholders are not values (R3-4).** A text value (also a list item, a status reason or a
  reference path) is rejected as `placeholder_value` when, after NFKC normalisation, case folding and
  removal of every non-letter/digit character, it is empty or one of: `notverified`, `unverified`,
  `notyetverified`, `na`, `notapplicable`, `notavailable`, `tbd`, `tbc`, `tobedetermined`, `todo`,
  `fixme`, `unknown`, `placeholder`, `pending`, `null`, `xxx`. So `not verified`, `NOT  VERIFIED`,
  `n/a`, `-`, fullwidth or zero-width variants are all refused; only the exact literal
  `"NOT VERIFIED"` marks an unfilled field. Free text that merely contains such a word is accepted.
  The schema expresses the ASCII part of this rule; NFKC variants are caught by the validator only.
- **Positive formats (V10, RB-4).** Some fields accept only one shape, so a placeholder spelling the
  deny-list misses (`0`, `same as above`, `later`) is still refused: `node.python_version`
  `3.<minor>.<patch>`; `node.uv_version`, `node.numpy_version`, `node.metatrader5_version` and case 1
  `numpy_version` `<major>.<minor>.<patch>` digits only; `node.os_build` the Windows
  `platform.version()` value `10.0.<5-digit build>[.<revision>]`; `terminal.build` and case 1
  `terminal_build` the terminal build number (4-5 digits). Case 1 `numpy_version` and
  `terminal_build` must equal `node.numpy_version` and `terminal.build`. People
  (`operator`, `second_person_verifier`, `custodian`, `reviewer`), `custody.candidate_creator`,
  `registry_identities` and `approver_identities` are principal ids in the PR #96/#99 grammar
  `[a-z0-9][a-z0-9._@-]{0,127}` on the raw string.
- Do not embed raw ticks, account numbers, balances, passwords or tokens. Raw outputs are
  referenced by path + SHA-256 only.
- The `evidence_class` field is `TEMPLATE` (everything unfilled), `SYNTHETIC_FIXTURE` (record id starts with
  `SYNTHETIC-`, can never claim TRUSTED or PASS) or `REAL_WINDOWS_RUN`.

## Fail-closed rules for `overall_result`

`overall_result` is **derived**, never trusted: the validator recomputes it and rejects the record
when the claimed value differs.

- Any case `FAIL` (or a malformed case) derives `FAIL`.
- All seven cases `PASS` for a `REAL_WINDOWS_RUN` first requires **all** of these, otherwise the
  record derives `FAIL` and is rejected:
  - no field anywhere is `NOT VERIFIED`;
  - `checkout.status_short` is empty **and** `checkout.checkout_clean` is `true`;
    `checkout.head` equals `identity.extractor_sha` **and** `checkout.head_equals_sha` is `true`;
  - `custody.verifier_status` is `TRUSTED` with `verifier_exit_code` 0 and no status reasons,
    no revocation is recorded (a later one is `pass_requires_no_revocation_after_approval`; an
    earlier, acknowledged one, which #99 accepts, is still refused here as
    `pass_requires_no_revocation_in_lineage`), and `dataset.development_status` is `TRUSTED`;
  - every `execution.exit_codes` entry is 0, and case 5 `run_a_content_sha256` equals
    `dataset.content_sha256`;
  - the custody gate, re-run by the validator over the `custody` section, reports no reason other
    than a dependency block (`dep01_*`, `dep02_*`, unauthenticated principals).
- If those hold, real-half readiness is derived from **machine-checked dependency status**
  (`evidence_validator.dependency_status()`: DEP-01 from `custody_gate.V4_BASELINE_APPROVED` and
  `V4_PRINCIPAL_AUTHENTICATION_AVAILABLE`, DEP-03 from the BLOCKED steps of
  `execution_contract.REAL_HALF_STEPS`), never from a free-text field. While DEP-01 or DEP-03 is
  BLOCKED, or the gate still reports a dependency block, the record derives **`BLOCKED`** (N2). A
  record that claims `PASS` then is rejected with `real_readiness_pass_while_dependency_blocked:<DEP>`.
  Only with every dependency resolved can it derive `PASS`. Today no record can.
- Templates and synthetic fixtures derive `NOT_RUN` even when every case says `PASS`.
- Otherwise `NOT_RUN`.

The report separates the two questions: `offline_harness_consistent` (the record is complete and
consistent) and `real_readiness` (`NOT_CLAIMED`, `BLOCKED`, `FAIL` or `PASS`), plus the dependency
status it used and `dependency_source` (`derived`; only a test can make it `test_override`). A
`REAL_WINDOWS_RUN` with any error reports `real_readiness: FAIL`, whatever was derived (V10, RB-5),
so `real_readiness` never says `PASS` next to `structurally_valid: false`.

A case `PASS` needs every evidence field of that case verified, a non-empty `evidence_refs` list,
and the case-specific values in the case table below. Contradictions are rejected whatever the
overall result: `checkout_clean` disagreeing with `status_short`, `head_equals_sha` disagreeing with
the recorded head, a `TRUSTED` dataset without a `TRUSTED` verifier, or a `TRUSTED` verifier with a
non-zero exit or with reasons.

**Summary flags are derived (N1).** Whatever the result: case 2 `counts_equal` must equal
`quality_counts == independent_counts`; case 4 `ranges_equal` must equal
`derived_fold_ranges == independent_fold_ranges`; case 7 `forbidden_names_observed` must equal the
number of forbidden MT5 names in `observed_mt5_names`; case 5 `variant_content_sha256` must hold
SHA-256 values only. `commands_run` and `exit_codes` must have the same length,
`finished_at_utc` may not precede `started_at_utc`, and the window may not be pre-registered after
the run started. Under case `PASS` additionally: case 5 run A equals run B and every variant hash,
and case 7 observed no forbidden name.

**Case 6 results are validated positively (V10, RB-1).** `reparse_results`, `crash_results` and
`lock_results` are each a non-empty map of probe name to a `filesystem_probes` node, exactly
`{"outcome": observed|unsupported|manual_required|error, "detail": text}`. Any other node
(`{"status": "error"}`, a bare `"error"`, `{"result": "manual_required"}`, an extra key) is
`invalid_value:cases.6.evidence.<field>:probe_results`. `probe_error_count` and
`probe_manual_unresolved_count` must equal the number of `error` and `manual_required` nodes.

**Custody is cross-checked (R3-1).** `custody.verifier_argv` is an argv list checked by
`custody_gate.parse_verify_argv` against the reviewed V4 CLI: `scripts/verify_market_history.py`
(optionally after `python` or `uv run [--no-sync] python`) with `--dataset`, `--store`,
`--registry`, `--store-head` and `--require-trusted`; `--dataset`, `--store`, `--registry` and
`--store-head` must equal `dataset_ref`, `store_ref`, `registry_ref.path` and `store_head`. Anchor-only or arbitrary commands
(`echo TRUSTED`) are rejected, and a `TRUSTED` status with such a command is a contradiction.
`revocations_after_approval` must match `revocation_seqs`, and `approval_seq` must be one of
`candidate_approval_seqs`. The validator re-runs `custody_gate.trust_gate` over the section and
rejects the record if `gate_outcome`/`gate_reasons` differ from that decision.

**REAL records carry no mock markers (R3-1).** In a `REAL_WINDOWS_RUN`, `record_id`,
`hostname_class`, `terminal.company/name/server_name`, `source_descriptor`,
`broker_clock_evidence_kind`, `store_ref`, `verifier_argv`, `commands_run` and `clock_source` may not
contain `synthetic`, `mock`, `fixture`, `test-only`, `dummy` or `fake` as a word, nor the mock source
string of `execution_contract`. `record_id` additionally refuses rehearsal words (`simulated`, `sample`,
`test`, `emulated`, `demo`, `stub`, `rehearsal`, `dry-run`, `example`, `trial`, `practice`,
`sandbox`, `syn`); a terminal or server name may legitimately say `Demo`, so they are not refused
there. These are still deny-lists; the positive checks above do the real work.

**REAL commands are the reviewed commands (V10, RB-3).** `execution.commands_run` is a list of argv
lists, never shell strings. In a `REAL_WINDOWS_RUN` every entry must be one of: the extractor
(`scripts/extract_market_history.py`, optionally after `python` or `uv run [--no-sync] python`)
with arguments that satisfy `execution_contract` (required flags, one protected-range decision,
`--operator`/`--operator-key`/`--candidate-out`) and whose `--symbol`, `--from-raw-msc`,
`--to-raw-msc`, `--out` and `--operator` equal `window.symbol`, `window.window_raw_msc`,
`custody.dataset_ref` and `people.operator`; the verifier, equal to `custody.verifier_argv`;
`python -m tests.windows_integration_readiness.filesystem_probes <dir>`; or
`python -m tests.windows_integration_readiness.execution_contract plan --provider mt5 ...` for the
same symbol, extractor SHA and extractor arguments. Anything else (`echo ok`) is
`commands_run_not_a_reviewed_command`, and the extractor and verifier runs are both required. The
filesystem paths are not re-checked offline; `plan` checks them on the node.

**Independence.** `operator`, `second_person_verifier` and `custodian` must be pairwise distinct,
and none of them may equal `reviewer`. All four are principal ids (raw grammar above), so a
disguised spelling (hidden characters, homoglyphs, case, spaces) is refused as an invalid value
instead of passing as a different person; they are also compared after NFKC normalization and case
folding. `custody.approver_identities` must be exactly `[custodian]`: the #99 registry has one id
per principal and no aliases. This cannot detect two different names used by one person.

## Sections

| Section | Fields |
|---|---|
| `identity` | `record_id`, `package_version`, `extractor_sha`, `extractor_version`, `manifest_schema` |
| `checkout` | `head`, `status_short`, `status_ref`, `checkout_clean`, `head_equals_sha` |
| `node` | `hostname_class`, `os_build`, `filesystem_type`, `volume_ref`, `antivirus_indexer_state`, `python_version`, `uv_version`, `metatrader5_version`, `numpy_version` |
| `terminal` | `company`, `name`, `build`, `server_name`, `account_kind` |
| `people` | `operator`, `second_person_verifier`, `custodian`, `reviewer`, `verifier_attestation_ref`, `custodian_attestation_ref` |
| `window` | `symbol`, `window_raw_msc`, `window_utc_msc`, `preregistration_ref`, `preregistered_at_utc`, `protected_ranges_ref`, `protected_margin_ms` |
| `source_provenance` | `source_descriptor`, `broker_clock_evidence_ref`, `broker_clock_evidence_kind`, `time_conversion_sha256`, `clock_attestation_sha256`, `dst_rule_evidence_ref` |
| `dataset` | `dataset_id`, `content_sha256`, `manifest_sha256`, `candidate_sha256`, `development_status` |
| `custody` | `store_ref`, `dataset_ref`, `registry_ref`, `registry_identities`, `approver_identities`, `candidate_creator`, `approval_seq`, `approval_entry_sha256`, `candidate_approval_seqs`, `store_head`, `store_head_channel_ref`, `store_head_channel_kind`, `store_head_at_verification`, `store_chain`, `revocation_seqs`, `revocations_after_approval`, `extraction_platform`, `writer_lock`, `verifier_argv`, `verifier_exit_code`, `verifier_status`, `verifier_status_reasons`, `pin_ref`, `pin_id`, `pin_pinned_by`, `pin_key_id`, `pin_signature`, `pin_seq`, `pin_entry_sha256`, `pin_store_genesis_sha256`, `pin_captured_at_utc`, `max_pin_age_seconds`, `verified_at_utc`, `approved_at_utc`, `operator_key_id`, `operator_signature`, `approver_key_id`, `approval_signature`, `approval_id`, `candidate_sha256`, `revocation_ids`, `acknowledged_revocations`, `gate_outcome`, `gate_reasons` |
| `execution` | `commands_run`, `raw_output_refs`, `exit_codes` |
| `timestamps` | `started_at_utc`, `finished_at_utc`, `clock_source` |

Notes: `terminal.account_kind` is `investor` or `demo` (no account number). `custody.*` maps to the
V4 custody interface of PR #96 at the pinned candidate head (`dependency-manifest.json`); see
[V4-CUSTODY-COMPATIBILITY.md](V4-CUSTODY-COMPATIBILITY.md). `store_head` is `SEQ:SHA256` as accepted
by `verify_market_history.py --store-head`. `store_head_channel_kind` is
`independent_prior_channel` or `same_store_at_verification`; `gate_outcome` is
`RECORDABLE_PENDING_REVIEW`, `BLOCKED` or `STOP`. `verifier_exit_code` is 0-3 (0 = TRUSTED only, 1 untrusted, 2 invalid input, 3 operational error); `PASS` needs 0 and status `TRUSTED`, exit 0 with any other status is an error. There is deliberately no V3 `anchor_ref` field: a
V3 anchor never grants trust at V4.

## Case evidence (`cases.<n>.evidence`)

Each case is `{"result": PASS|FAIL|NOT_RUN, "evidence": {...}, "evidence_refs": [ref, ...], "notes": text}`.

| Case | Evidence fields |
|---|---|
| 1 | `descr`, `names`, `len`, `empty_behaviour`, `last_error`, `numpy_version`, `terminal_build`, `first_last_time_msc_in_window` |
| 2 | `vector_results`, `hostile_context_identical`, `quality_counts`, `independent_counts`, `counts_equal` |
| 3 | `evidence_ref`, `evidence_kind`, `segments`, `refusals_observed`, `terminal_contacted_before_validation` |
| 4 | `dst_rule_evidence_ref`, `transitions`, `derived_fold_ranges`, `independent_fold_ranges`, `ranges_equal`, `refusals_observed`, `split_windows` |
| 5 | `run_a_content_sha256`, `run_b_content_sha256`, `chunk_ms_variants`, `variant_content_sha256`, `order_counts`, `tamper_refusal` |
| 6 | `probe_json_ref`, `fs_type`, `volume_info_ref`, `av_indexer_state`, `reparse_results`, `crash_results`, `lock_results`, `probe_error_count`, `probe_manual_unresolved_count` |
| 7 | `observed_mt5_names`, `initialize_args`, `shutdown_paths`, `forbidden_names_observed` |

Case `PASS` additionally requires: case 1 `first_last_time_msc_in_window = true`; case 2
`hostile_context_identical = true` and `counts_equal = true`; case 3
`terminal_contacted_before_validation = false`; case 4 `ranges_equal = true`; case 6
`probe_error_count = 0` and `probe_manual_unresolved_count = 0`; case 7
`forbidden_names_observed = 0`. The independent verification of case 7 is recorded in `custody.*`.

## Mandatory `not_verified` entries

Each entry below must appear in `not_verified` unless `claim_evidence` maps it to a reference that
verifies it; never in both, never in neither. Unknown entries are rejected.

Round 4 (R3-1): a reference alone cannot prove a capability owned by an unresolved dependency. The
validator's `CLAIM_OWNER` map is a fail-closed allow-list: the six Windows/filesystem entries belong
to DEP-02, `independent_store_integrity` and `custodian_identity_authenticity` to DEP-01,
`terminal_history_determinism_between_fetches` and `documented_tick_dtype_matches_terminal_build` to
DEP-03, and `tick_completeness` has no reviewed evidence method at all. Moving any of those to
`claim_evidence` is rejected (`claim_requires_unresolved_dependency:<DEP>:<claim>` or
`claim_has_no_reviewed_evidence_method:<claim>`) while the owner is not RESOLVED. The remaining five
may be evidenced by a reviewed reference. DEP-02 claims are also passed to the custody gate, whose
guarantee allow-list is empty.

- `windows_ntfs_atomicity_and_hardlink_behaviour`
- `reparse_point_and_junction_handling`
- `antivirus_and_indexer_interaction`
- `network_share_behaviour`
- `power_loss_durability`
- `terminal_history_determinism_between_fetches`
- `broker_clock_rule_truth`
- `dst_fold_truth_for_single_segment_conversions`
- `account_is_read_only_or_demo`
- `no_other_process_trades_on_account`
- `tick_completeness`
- `documented_tick_dtype_matches_terminal_build`
- `independent_store_integrity`
- `terminal_request_bound_clock_interpretation`
- `custodian_identity_authenticity`
- `windows_msvcrt_lock_exclusivity`

The last three are new in schema version 2. They come from Reviewer B I1 (request bounds),
PR #96 Reviewer A F1 (approvals are unauthenticated strings) and PR #96 `lock.py` (the Windows
`msvcrt.locking` path is implemented but UNVERIFIED).

## Fixtures

- [evidence-record.template.json](evidence-record.template.json): `TEMPLATE`, every field
  `NOT VERIFIED`, every case `NOT_RUN`.
- `tests/windows_integration_readiness/evidence_fixtures/valid-synthetic.json`:
  `SYNTHETIC_FIXTURE`, fully populated with synthetic values, every case `PASS`, dataset
  `UNTRUSTED`, a structured synthetic verifier argv and the gate decision the gate actually returns
  for it (`BLOCKED`). It passes the structural checks and derives `NOT_RUN`. It is **not** a run and
  is **not** Windows-verified.

An evidence record is **not** an approval. A reviewer who neither operated nor implemented the run
must approve it, and approval of readiness must precede any real-data execution.
