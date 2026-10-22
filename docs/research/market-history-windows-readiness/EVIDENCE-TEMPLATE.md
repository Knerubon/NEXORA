# Evidence template

One record per real-half execution. Machine-readable form:
[evidence-record.template.json](evidence-record.template.json) (schema version
`nexora.windows-readiness-evidence/2`).

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
  `{"path": ..., "sha256": ...}`, a non-empty list, or a non-empty object) or the literal
  `"NOT VERIFIED"`. Blank, whitespace-only, `[]` and `{}` are invalid. Unknown keys are invalid.
- Do not embed raw ticks, account numbers, balances, passwords or tokens. Raw outputs are
  referenced by path + SHA-256 only.
- The `evidence_class` field is `TEMPLATE` (everything unfilled), `SYNTHETIC_FIXTURE` (record id starts with
  `SYNTHETIC-`, can never claim TRUSTED or PASS) or `REAL_WINDOWS_RUN`.

## Fail-closed rules for `overall_result`

`overall_result` is **derived**, never trusted: the validator recomputes it and rejects the record
when the claimed value differs.

- Any case `FAIL` (or a malformed case) derives `FAIL`.
- All seven cases `PASS` derives `PASS` only for `REAL_WINDOWS_RUN` and only when **all** of these
  hold, otherwise the record is rejected:
  - no field anywhere is `NOT VERIFIED`;
  - `checkout.status_short` is empty **and** `checkout.checkout_clean` is `true`;
    `checkout.head` equals `identity.extractor_sha` **and** `checkout.head_equals_sha` is `true`;
  - `custody.verifier_status` is `TRUSTED` with `verifier_exit_code` 0 and no status reasons,
    `custody.revocations_after_approval` is 0, and `dataset.development_status` is `TRUSTED`.
- Templates and synthetic fixtures derive `NOT_RUN` even when every case says `PASS`.
- Otherwise `NOT_RUN`.

A case `PASS` needs every evidence field of that case verified, a non-empty `evidence_refs` list,
and the case-specific values in the case table below. Contradictions are rejected whatever the
overall result: `checkout_clean` disagreeing with `status_short`, `head_equals_sha` disagreeing with
the recorded head, a `TRUSTED` dataset without a `TRUSTED` verifier, or a `TRUSTED` verifier with a
non-zero exit or with reasons.

**Independence.** `operator`, `second_person_verifier` and `custodian` must be pairwise distinct,
and none of them may equal `reviewer`, compared after NFKC normalization, trimming, case folding and
whitespace collapsing. This catches the same declared name in disguise; it cannot detect two
different names used by one person.

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
| `custody` | `store_ref`, `registry_ref`, `approval_seq`, `approval_entry_sha256`, `store_head`, `store_head_channel_ref`, `revocations_after_approval`, `verifier_command`, `verifier_exit_code`, `verifier_status`, `verifier_status_reasons` |
| `execution` | `commands_run`, `raw_output_refs`, `exit_codes` |
| `timestamps` | `started_at_utc`, `finished_at_utc`, `clock_source` |

Notes: `terminal.account_kind` is `investor` or `demo` (no account number). `custody.*` maps to the
V4 custody interface of PR #96 at `d6a7cda72a88600e2ccf6554605f1c50478893ea`; see
[V4-CUSTODY-COMPATIBILITY.md](V4-CUSTODY-COMPATIBILITY.md). `store_head` is `SEQ:SHA256` as accepted
by `verify_market_history.py --store-head`. There is deliberately no V3 `anchor_ref` field: a V3
anchor never grants trust at V4.

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
  `UNTRUSTED`. It passes the structural checks and derives `NOT_RUN`. It is **not** a run and is
  **not** Windows-verified.

An evidence record is **not** an approval. A reviewer who neither operated nor implemented the run
must approve it, and approval of readiness must precede any real-data execution.
