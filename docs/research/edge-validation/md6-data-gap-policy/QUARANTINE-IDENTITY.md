# MD-6 content-addressed quarantine and requalification

Status: **DRAFT / PROPOSAL. Quant sign-off PENDING. Not authoritative.**
This file proposes a rule. It approves, freezes and implements nothing. No numeric MD-6 threshold is chosen,
defaulted or implied here. All examples and test values are SYNTHETIC.

Executable specification (test-only reference model, not runtime enforcement):
`tests/md6_quarantine_reference.py`, tests in `tests/test_md6_quarantine_identity.py`.

## 1. Problem

Review finding M3 (MAJOR, case C8, design risk) against [MD6-DECISION-REGISTER.md](MD6-DECISION-REGISTER.md):

- D13 says the quarantine register records window id, instrument, defect class, evidence ids, decision date and
  decider. It records no content hash, no `dataset_id` and no back-link to a parent or earlier identity.
- D13 says "Release from quarantine requires new evidence ... and a new dataset identity". The research
  `dataset_id` binds content, symbol, window, conversion, clock attestation, source version and instrument
  facts. So the same rejected ticks, re-extracted, or kept unchanged but with a re-declared conversion segment,
  get a new `dataset_id`. Nothing in the register links the new identity to the rejected one.
- D09 rejects a whole dataset on a hard defect, but no register records a dataset-level rejection.

Consequence: rejected content can re-enter evaluation under a new name. The old register can only be looked up by
labels, so its answer does not depend on content at all (shown by
`test_c2_old_rule_misses_same_content_under_new_identity` and `test_old_rule_is_content_blind`).

## 2. Definitions

**Content.** The stored tick records, in stored order, and nothing else. Each record has exactly four content
fields:

| Field | Canonical token |
|---|---|
| `raw_msc` | the stored integer raw broker time in ms, base 10, as Python `str(int)` (booleans are not integers here) |
| `bid` | the exact stored decimal string. Grammar: `-?(0\|[1-9][0-9]*)(\.[0-9]+)?`, or exactly one of `NaN`, `Infinity`, `-Infinity` |
| `ask` | same grammar as `bid` |
| `flags` | the exact stored unsigned decimal string (`0\|[1-9][0-9]*`), or absent (JSON `null`) when the source supplies none |

**Metadata (never content).** File names, chunk names, `dataset_id`, previous aliases, manifest labels, the time
conversion declaration (segments, offsets, fold ranges, `evidence_ref`), clock attestation, source version,
instrument label and instrument facts, window ids, and any derived UTC time. Metadata is recorded in the register
entry, but changing it never changes the content identity. The instrument label is metadata on purpose, so that
relabelling cannot evade quarantine; a coincidental match across instruments is resolved by requalification (OPEN
QI-5).

**Canonicalization version.** `nexora.md6-tick-content/1`. **Hash algorithm.** SHA-256, lowercase hex.

**Canonical record bytes.** The JSON array `["<raw_msc>","<bid>","<ask>",<flags or null>]` with no whitespace,
ASCII only.

**`record_hash`.** `SHA-256("md6-record" 0x00 version 0x00 canonical_record_bytes)`.

**`content_identity`** (the name Track B and every other artifact cite). A record of five fields:

| Field | Definition |
|---|---|
| `canonicalization_version` | `nexora.md6-tick-content/1` |
| `hash_algorithm` | `sha256` |
| `ordered_content_hash` | `SHA-256("md6-ordered" 0x00 version 0x00, then for each record in stored order: record_hash + "\n")`. Stored order is significant (D10), so reordering changes it. |
| `multiset_fingerprint` | `SHA-256("md6-multiset" 0x00 version 0x00, then the sorted record hashes joined by "\n")`. Order-insensitive. Used only for re-entry detection, never as the dataset's identity. |
| `record_count` | number of records |

**`record_hashes`.** The sorted multiset of per-record hashes. Kept in the register so that a subset, superset
or partial overlap of quarantined content is detected (record containment).

**`shadow_record_hashes`.** Per-record hashes computed after a representation-only reduction (trailing fractional
zeros removed, `-0` read as `0`, `flags` left out entirely). Leaving `flags` out means a re-extraction that drops,
adds or rewrites flags while raw time and prices are unchanged is detected as representation drift (round-2 N6).
Used ONLY to detect that a candidate re-expresses quarantined values in another
representation. They never create a match, never clear a match and never normalize content.

**Lineage fields** (cited by Track B). `lineage_parents`: a list of `LineageRef(dataset_id, ordered_content_hash)`
that a dataset declares it was derived from (resampled, filtered, merged, re-extracted). `previous_aliases`: earlier
`dataset_id` values of the same content.

**Register.** An append-only list of entries, serialized as canonical JSON lines (sorted keys, no whitespace),
format `nexora.md6-quarantine-register/1`. Each entry carries `seq` (append position from 0), `prev_entry_hash`
(the previous entry's `entry_hash`, or 64 zeros for the first entry) and its own `entry_hash`
(`SHA-256("md6-register-entry" 0x00 canonical_json(entry without entry_hash))`).

**Register anchor.** `(entry_count, head_hash)`, stored independently of the register file (OPEN QI-3: where).
The anchor is what makes truncation detectable: a register cut after entry k still has a valid chain, but it does
not match the anchor.

## 3. Normative rules

- **QI-R1.** Every quarantine decision MUST be a register entry keyed by `content_identity`, `record_hashes` and
  `shadow_record_hashes`, computed under the canonicalization version above. Labels alone MUST NOT key an entry.
- **QI-R2.** Dataset-level rejections (D09 hard defect) MUST be registered with `scope = DATASET`. Window-level
  quarantines MUST be registered with `scope = WINDOW` and a `window_id`, and their content is the window's records.
  The re-entry check is applied to each evaluation window as its own candidate (A-24). A WINDOW entry therefore
  blocks any candidate that contains its records (for example the whole containing dataset, as SUPERSET) but not a
  sibling window checked on its own (round-2 N4; D09 option b). A WINDOW entry's `dataset_id` and aliases
  are labels and MAY name the containing dataset, so for a WINDOW entry a candidate's declared parent is a
  lineage hit only when the parent's `ordered_content_hash` equals the window's; a sibling that declares the
  containing dataset as parent is decided by its records like a sibling that declares nothing (R3-m4).
  DATASET entries keep the label and alias match.
- **QI-R3.** Every entry MUST carry all of: content identity (all five fields), `record_hashes`,
  `shadow_record_hashes`, `dataset_id`, `previous_aliases` (may be empty), `lineage_parents` (may be empty),
  `window_id` (null for DATASET), `instrument_id`, rejection reason (`defect_class` and preregistered
  `criteria_id`), at least one `evidence_refs` item, `submitter_id`, `strategy_author_ids`, metadata pairs,
  `decided_at` (a synthetic or real timestamp; ordering authority is `seq`, and `decided_at` MUST NOT decrease in
  append order), `post_evaluation` label (D13), status, decision authority (`person_id`, `role`), `seq`,
  `prev_entry_hash`, `entry_hash`. Requalification history is the set of later REQUALIFICATION entries that cite
  the entry.
- **QI-R4.** The canonical form uses the exact stored decimal string. It MUST NOT be normalized: `1.10` and `1.1`
  are different content. A token outside the grammar (for example `nan`, `inf`, `1e-3`, `+1.1`, `01.1`, `1.`,
  ` 1.1`) is non-canonicalizable: the check returns `CANONICALIZATION_CONFLICT` (eligibility UNVERIFIED). It is
  never silently repaired. Such a dataset cannot be registered under this version; it also cannot pass any check,
  so this does not open an evasion path.
- **QI-R5.** The register is append-only. Entries MUST NOT be edited or deleted. A requalification is a new
  entry; the original rejection stays.
- **QI-R6.** Only the independent data reviewer or the Quant approver MAY append an entry. The deciding person
  MUST NOT be the dataset's submitter or any strategy author named in the entry (D13 separation of duties).
  `verify_register` re-checks this for every entry, so a register containing a violating entry is invalid.
- **QI-R7.** A check MUST compute the candidate's content identity from its records. A declared identity that
  differs from the computed one makes the result UNVERIFIED.
- **QI-R8.** No outcome of this procedure grants TRUSTED or PASS. The best outcome lets the dataset proceed to the
  normal Part A criteria of [ACCEPTANCE-CRITERIA.md](ACCEPTANCE-CRITERIA.md).

## 4. Outcome vocabulary

| Outcome | Meaning | Eligibility |
|---|---|---|
| `UNVERIFIED` | Register or anchor missing, register corrupted, truncated, reordered, chain broken, entry rule violated, or the candidate's declared identity does not match its records | UNVERIFIED (fail closed) |
| `CANONICALIZATION_CONFLICT` | A register entry or the candidate's declared identity uses another canonicalization version or hash algorithm; or the candidate has non-canonicalizable content; or the candidate re-expresses quarantined values in another representation (representation drift) | UNVERIFIED (fail closed) |
| `QUARANTINED_MATCH` | Same `ordered_content_hash` or same `multiset_fingerprint` as a quarantined entry, whatever the id, file name or metadata | BLOCKED |
| `DERIVED_FROM_QUARANTINED` | Declared lineage from a quarantined entry (by parent content hash, `dataset_id` or alias), or record containment: the candidate is a subset (every record hash in the quarantined set), a superset, or a partial overlap of quarantined content | BLOCKED |
| `REQUALIFIED` | Exact match with a quarantined entry whose specific `ordered_content_hash` has a valid requalification | PART_A_REQUIRED (not trusted) |
| `NOT_KNOWN` | No relation to any entry | PART_A_REQUIRED (not trusted) |

Precedence, first that applies: UNVERIFIED (register) > CANONICALIZATION_CONFLICT (version, algorithm,
non-canonical content) > QUARANTINED_MATCH > DERIVED_FROM_QUARANTINED > CANONICALIZATION_CONFLICT (representation
drift) > REQUALIFIED > NOT_KNOWN. A later QUARANTINE entry for content that was requalified quarantines it again.

A superset or partial overlap is `DERIVED_FROM_QUARANTINED`, so the dataset as a whole is blocked. Whether its
non-overlapping remainder may be used once the quarantined windows are excluded is OPEN (QI-1). Until decided it
is not usable.

## 5. Requalification

A requalification is accepted only if all of the following hold. Otherwise it is refused, nothing is appended, and
the content stays quarantined.

1. The register verifies against its anchor (otherwise refused as UNVERIFIED).
2. The target is a QUARANTINE entry in this register.
3. The deciding authority has role `INDEPENDENT_DATA_REVIEWER` or `QUANT_APPROVER`, and is not the original
   submitter, not a strategy author named in the entry, and not a strategy author of the current evaluation context.
4. At least one new evidence reference is given (for example a new retrieval, calendar or offset evidence), and no
   given reference was already cited by the target entry or by an earlier requalification of it.
5. A rationale is recorded.
6. The requalified identity uses this canonicalization version and hash algorithm, and its
   `ordered_content_hash` is not already requalified under the target. It either has the target's
   `multiset_fingerprint`, or it is a subset: the request then carries the records, which must reproduce the
   requalified identity exactly and form a sub-multiset of the target's `record_hashes` (round-2 N5).
7. `decided_at` does not precede the register head.

The REQUALIFIED status applies only to the specific `ordered_content_hash` requalified. A reordering of the same
records, a subset or a child still match the original entry and stay blocked until they are requalified
themselves under rule 6; requalifying a subset never releases its sibling records. REQUALIFIED never grants TRUSTED: the dataset must still pass every Part A criterion.

## 6. Fail-closed behaviour

- Missing register, missing anchor, unparseable bytes, a line not in canonical form, a truncated line, a dropped or
  reordered entry, an edited entry, a broken back-link, an unknown register format, a decreasing `decided_at`, or an
  entry violating QI-R6: every eligibility check returns `UNVERIFIED`, and recording or requalifying is refused.
- Any entry under a different canonicalization version or hash algorithm: every check returns
  `CANONICALIZATION_CONFLICT` until the register is migrated (OPEN QI-6).
- An empty register is valid only with an anchor of `(0, 64 zeros)`.

## 7. Cross-track contract (for Track B, exposure ledger)

Track B cites these names exactly: `content_identity` (`canonicalization_version`, `hash_algorithm`,
`ordered_content_hash`, `multiset_fingerprint`, `record_count`), `record_hashes`, `lineage_parents`
(`LineageRef(dataset_id, ordered_content_hash)`), `previous_aliases`, and the register `entry_hash`. The exposure
key is `multiset_fingerprint` together with `canonicalization_version`, so a reordered or renamed copy does not
reset exposure; record containment gives lineage (EVALUATION-EXPOSURE-LINEAGE.md section 2, I1 to I5;
`tests/md6_cross_track_reference.py`).

## 8. What stays OPEN

| ID | Question | Owner |
|---|---|---|
| QI-1 | May a superset or partially overlapping dataset be used with the quarantined records' windows excluded? Default until decided: blocked. Sibling windows checked on their own are not affected (QI-R2) | Quant |
| QI-2 | Exact mapping from the tooling's stored chunk format to the canonical tokens (are prices stored as decimal strings or binary floats; how flags are stored). This mapping is part of canonicalization version 1 and must be fixed before use | Architect, Market data |
| QI-3 | Where the register anchor is stored and who holds custody (for example in the preregistration commit) | Rin, Architect |
| QI-4 | Must refused requalification attempts also be logged? This model does not append refused attempts | Quant, Rin |
| QI-5 | Procedure when two instruments or sources produce identical records (instrument is metadata) | Quant |
| QI-6 | Migration to a new canonicalization version: requires retaining quarantined bytes so they can be re-hashed by the independent reviewer and appended as new entries; retention policy | Rin, Operator |
| R2 (existing) | Who is the independent data reviewer | Rin, Architect |

## 9. Adversarial cases

All in `tests/test_md6_quarantine_identity.py`. Inputs are SYNTHETIC.

| Case | Input | Expected | Test |
|---|---|---|---|
| C1 | Rejected bytes under a new file name | QUARANTINED_MATCH | `test_c1_same_bytes_new_filename_is_quarantined_match` |
| C2 | Same content, new `dataset_id` | QUARANTINED_MATCH | `test_c2_same_content_new_dataset_id_is_quarantined_match` |
| C2-old | OLD D13 register (window, instrument, defect only), same content under new identity | old rule: not detected (failure); new rule: QUARANTINED_MATCH | `test_c2_old_rule_misses_same_content_under_new_identity` |
| C2-old | OLD register lookup is label-only (false positive and false negative; no dataset-level entry) | content-blind | `test_old_rule_is_content_blind` |
| C3 | Same content, changed conversion declaration, manifest label, instrument label, id | QUARANTINED_MATCH | `test_c3_modified_metadata_is_quarantined_match` |
| C4 | Records reordered | ordered hash differs, multiset equal, QUARANTINED_MATCH | `test_c4_reordered_records_match_by_multiset` |
| C4b | `1.10` stored, re-entered as `1.1` | not normalized; CANONICALIZATION_CONFLICT (drift), UNVERIFIED | `test_c4b_trailing_zero_representation_is_not_normalized_and_fails_closed` |
| C4b | Non-canonical tokens (`nan`, `inf`, `1e-3`, `+1.1`, `01.1`, whitespace, empty) | CANONICALIZATION_CONFLICT | `test_c4b_non_canonical_token_fails_closed` |
| C4b | Non-canonical flags (`06`) or boolean raw time | CANONICALIZATION_CONFLICT | `test_c4b_non_canonical_flags_and_raw_msc_fail_closed` |
| C4b | Canonical `NaN` token in a rejected dataset | registerable; re-entry QUARANTINED_MATCH | `test_c4b_declared_nonfinite_tokens_are_content_and_registerable` |
| C5 | Subset of quarantined records | DERIVED_FROM_QUARANTINED (SUBSET) | `test_c5_derived_subset_is_derived_from_quarantined` |
| C5 | Superset, partial overlap | DERIVED_FROM_QUARANTINED | `test_c5_superset_and_partial_overlap_are_derived` |
| C6 | New child declaring the rejected parent | DERIVED_FROM_QUARANTINED (DECLARED_LINEAGE) | `test_c6_child_with_declared_rejected_parent_is_derived` |
| C6 | Child declaring a previous alias of the parent | DERIVED_FROM_QUARANTINED | `test_c6_lineage_through_previous_alias_is_derived` |
| C7 | Missing register or anchor, edited entry, truncated (entry or mid-line), reordered, garbage, emptied, wrong anchor | UNVERIFIED for every candidate | `test_c7_missing_or_corrupted_register_is_unverified` |
| C7 | Edited entry with recomputed own hash | UNVERIFIED (next back-link breaks) | `test_c7_broken_hash_chain_with_recomputed_entry_hash_is_unverified` |
| C7 | Empty register without and with anchor | UNVERIFIED, then NOT_KNOWN | `test_c7_empty_register_needs_an_anchor` |
| C8 | Register entry under another canonicalization version or hash algorithm | CANONICALIZATION_CONFLICT | `test_c8_register_with_other_canonicalization_is_conflict` |
| C8 | Candidate identity declared under another version | CANONICALIZATION_CONFLICT | `test_c8_candidate_declared_under_other_version_is_conflict` |
| C8 | Declared identity does not match records | UNVERIFIED | `test_c8_declared_identity_not_matching_records_is_unverified` |
| C8 | Requalification across versions | refused | `test_c8_requalification_across_versions_is_refused` |
| C9 | Requalification by strategy author, by the submitter, by a context strategy author, by another role, without new evidence, with reused evidence, back-dated | refused; still QUARANTINED_MATCH | `test_c9_unauthorized_requalification_is_refused` |
| C9 | Requalification against a corrupted register | refused (UNVERIFIED) | `test_c9_requalification_against_corrupt_register_is_refused` |
| C10 | Independent Quant approver, new evidence | REQUALIFIED; history preserved; no trust; Part A still required | `test_c10_legitimate_requalification_preserves_history_and_grants_no_trust` |

Round-2 MINOR regressions, in `tests/test_md6_exposure_n1_remediation.py`:

| Case | Input | Expected | Test |
|---|---|---|---|
| N4 | WINDOW-scope entry for W2; whole dataset and sibling windows W1, W3 checked | dataset DERIVED_FROM_QUARANTINED (SUPERSET); siblings NOT_KNOWN | `test_n4_window_quarantine_blocks_containing_dataset_but_not_sibling_windows` |
| N5 | Subset of quarantined content requalified with its records; without records; foreign or mismatched records; sibling of a requalified subset | REQUALIFIED (history kept, no trust) / refused / refused / still DERIVED_FROM_QUARANTINED | `test_n5_*` (3 tests) |
| N6 | Re-extraction with `flags` absent, or with other flags | CANONICALIZATION_CONFLICT (REPRESENTATION_DRIFT) | `test_n6_*` (2 tests) |
| C10 | Reordering or subset of requalified content | still QUARANTINED_MATCH / DERIVED | `test_c10_requalification_is_specific_to_the_content_identity` |
| C10 | Second requalification of the same identity | refused | `test_c10_second_requalification_of_same_identity_is_refused` |
| C10 | Re-quarantine after requalification | QUARANTINED_MATCH | `test_c10_requarantine_after_requalification_wins` |
| extra | Dataset-level rejection registered with all fields; window-level quarantine | registered | `test_dataset_level_rejection_is_registered_with_all_required_fields`, `test_window_level_quarantine_is_registered` |
| extra | Strategy author or submitter records a quarantine | refused | `test_strategy_author_or_submitter_cannot_record_quarantine` |
| extra | Unrelated dataset | NOT_KNOWN, never trusted | `test_not_known_never_grants_trust` |
| extra | Record the same decision twice | byte-identical register | `test_recording_is_deterministic` |

## 10. Limitations

- Documentation and a test-only reference model. Nothing here is runtime enforcement. Implementing the register,
  the anchor custody and the canonical token mapping in tooling is an implementation dependency (D18 style) that
  this proposal does not authorize.
- The model only detects relations to content that was registered. Content never registered is `NOT_KNOWN`, which
  is not trust.
- Containment uses record hashes. A dataset whose every record was changed (for example by re-quantizing prices)
  is new content; this is detected only through declared lineage, representation drift, or the normal criteria.
  Undeclared transformations rely on D13 separation of duties and the provenance in the manifest.
- Coincidental identical records across instruments are flagged, not silently cleared (QI-5).
- Sign-off: **PENDING**.

## 11. Mapping to the PR #96 tick storage format (Q2) and provenance linkage (Q3)

Reference only. PR #96 (market_history V4, head `1c536d5acdef2c9fe47ef5a6ecfb127595ef42b8`) is unmerged and is NOT part of
the base of this proposal; nothing here asserts its behaviour beyond what was read from a read-only `git archive`
export. **End-to-end compatibility is NOT claimed.** The first table is the result of a one-off EXECUTED check
(`evidence/verify_pr96_mapping.py`, output in `evidence/verify_pr96_mapping.output.txt`, 14 checks, all PASS on
2026-10-29). It is not part of CI, because the PR #96 code is absent from this base; re-run it against any export.
The second table is DESIGN ONLY.

### 11.1 Verified by that executed check (PR #96 code run, QI-R4 grammar from `tests/md6_quarantine_reference.py`)

| ID | Fact | Consequence for QUARANTINE-IDENTITY.md |
|---|---|---|
| V1 | `decimal_str` (PR #96) emits no exponent, strips trailing zeros and returns `0` for any zero, so every finite output matches the QI-R4 grammar `-?(0\|[1-9][0-9]*)(\.[0-9]+)?` | Stored finite price tokens of PR #96 data can be used verbatim as QI-R4 tokens |
| V2, V3 | PR #96 normalizes before it hashes: `1.10` and `1.1` become one stored token `1.1` (`content_sha256` cannot tell them apart), whereas QI-R4 keeps `1.10` and `1.1` distinct (`record_hash` differs) | The two layers disagree on trailing zeros. For PR #96 data this is harmless (the extractor never emits `1.10`), but a candidate that reached the register through another extractor with `1.10` is drift here (REPRESENTATION_DRIFT) and identical there |
| V4a, V4b | PR #96 stores `str(Decimal)` of a non-finite value verbatim. `NaN`, `Infinity`, `-Infinity` are inside the QI-R4 closed set; `sNaN`, `-NaN`, `-sNaN` and `NaN<payload>` are outside it | A dataset holding such a spelling cannot be registered as quarantined (QI-R4 refuses it: CanonicalizationError). The provider converts floats with `Decimal(repr(float))`, which yields only `NaN` and `Infinity` spellings, so this is a latent gap, not an observed one (QI-8) |
| V5a to V5d | A stored row is `{"a":"<ask>","b":"<bid>","f":<int>,"t":<raw_msc>}`; `f` is a JSON integer and is omitted when the source gives none. The mapping `raw_msc = t`, `bid = b`, `ask = a`, `flags = str(f)` or `None` gives a `TickRecord`; a negative `f` is outside the QI flags grammar | This is the concrete answer to QI-2 for PR #96 data; it is not frozen until QI-2 is decided |
| V6a, V6b | PR #96 `content_sha256` is the SHA-256 of the concatenated row bytes in stored order: independent of chunk layout, sensitive to tick order | Same axis as `ordered_content_hash` |
| V6c, V6d | A reordered copy has an equal QI `multiset_fingerprint` but another `content_sha256`; the two hashes are different constructions with no derivable relation | A PR #96 manifest alone cannot yield a QI `content_identity`; the tick rows must be re-read and canonicalized under the QI version |
| V7 | PR #96 sources contain no reference to a quarantine register, `multiset_fingerprint`, `ordered_content_hash` or shadow hashes | There is no code-level linkage between the two layers (see 11.3) |

### 11.2 Design only (no executed evidence)

- Computing `content_identity` and `shadow_record_hashes` from a PR #96 chunk directory (a reader that maps rows as in V5)
  is a design; no such reader exists in this proposal.
- The quarantine `dataset_id` field is a free label in this register. PR #96's `dataset_id` is a SHA-256 over an identity
  object that also covers the time conversion, clock attestation, source and units (`_identity`), so it changes when
  the offset declaration changes while the QI content identity does not. Whether the register should cite the PR #96
  `dataset_id` in `previous_aliases` is a design question (QI-7).
- Offset and time: PR #96 `t` is the raw broker-clock value, not UTC. The bridge in `tests/md6_cross_track_reference.py` treats
  synthetic `raw_msc` as verified UTC only as a stated simplification; real data needs MD-2 first.

### 11.3 Missing provenance and alias linkage (Q3), reported, not solved

1. PR #96 custody approval and revocation are keyed by PR #96 `dataset_id`, `content_sha256`, manifest hash, symbol and window. A
   custody revocation (`revoke_dataset`) does not append a quarantine entry, and a quarantine entry does not
   reference a custody record. A dataset can be revoked in one place and still pass `check_reentry` here, and the
   reverse.
2. `dataset_id` in the register is whatever the submitter supplied; nothing checks it against a PR #96 `dataset_id`.
   `previous_aliases` and `lineage_parents` cite names, not custody-verified identities.
3. PR #96 revocation can only deny trust (the verifier honours any revocation); this register's requalification can
   restore PART_A eligibility. The two directions of authority are not reconciled.
4. No end-to-end test exists for any of this, and none is claimed. Open items: QI-2, QI-3, QI-7, QI-8 in
   [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md).
