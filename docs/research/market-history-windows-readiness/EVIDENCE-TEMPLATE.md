# Evidence template

One record per real-half execution (or per case). Machine-readable form:
[evidence-record.template.json](evidence-record.template.json), validated structurally against
[evidence-record.schema.json](evidence-record.schema.json) (and checked for self-consistency by
`tests/windows_integration_readiness/test_evidence_template.py`).

Rules: a field is either a real value or the literal `"NOT VERIFIED"`. Blank is invalid. Do not embed
raw ticks, account numbers, balances, passwords or tokens. Raw outputs are referenced by path +
SHA-256 only. Nothing here is filled in by this PR: every value in the template is a placeholder or
`NOT VERIFIED`.

| Group | Fields |
|---|---|
| Identity | `record_id`, `package_version` (this readiness package's commit), `extractor_sha` (full 40-char), `checkout_clean`, `head_equals_sha` |
| Node | `hostname_class`, `os_build`, `filesystem_type`, `volume_ref`, `antivirus_indexer_state`, `python_version`, `uv_version`, `metatrader5_version`, `numpy_version` |
| Terminal | `terminal_company`, `terminal_name`, `terminal_build`, `server_name`, `account_kind` (investor/demo; no number) |
| People | `operator`, `second_person_verifier`, `reviewer` |
| Window | `symbol`, `window_raw_msc`, `window_utc_msc`, `preregistration_ref`, `preregistered_at_utc`, `protected_ranges_ref`, `protected_margin_ms` |
| Source provenance | `source_descriptor`, `broker_clock_evidence_ref`, `broker_clock_evidence_kind`, `time_conversion_sha256`, `clock_attestation_sha256`, `dst_rule_evidence_ref` |
| Dataset identity | `dataset_id`, `content_sha256`, `manifest_sha256`, `anchor_ref`, `development_status` |
| Execution | `commands_run` (verbatim list), `raw_output_refs` (path + sha256), `exit_codes` |
| Results | `cases` (1-7: `result` PASS/FAIL/NOT_RUN, `evidence_fields`, `notes`), `overall_result` |
| Timestamps | `started_at_utc`, `finished_at_utc`, `clock_source` |
| Explicit non-claims | `not_verified` (list; see below) |

## Mandatory `not_verified` entries (remove an entry only by attaching the evidence that verifies it)

- `windows_ntfs_atomicity_and_hardlink_behaviour`
- `reparse_point_and_junction_handling`
- `antivirus_and_indexer_interaction`
- `network_share_behaviour`
- `power_loss_durability`
- `terminal_history_determinism_between_fetches`
- `broker_clock_rule_truth` (the declaration is evidence-referenced, not proven true)
- `dst_fold_truth_for_single_segment_conversions`
- `account_is_read_only_or_demo` (operator attestation only)
- `no_other_process_trades_on_account`
- `tick_completeness`
- `documented_tick_dtype_matches_terminal_build`
- `independent_store_integrity`

An evidence record is **not** an approval. A reviewer who neither operated nor implemented the run
must approve it, and approval of readiness must precede any real-data execution.
