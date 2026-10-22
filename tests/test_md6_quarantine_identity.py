"""Adversarial tests for the MD-6 content-addressed quarantine register.

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING).
Test-only reference model; not runtime enforcement.

All tick values, identifiers and timestamps below are SYNTHETIC. No MD-6 threshold is used.
Policy text: docs/research/edge-validation/md6-data-gap-policy/QUARANTINE-IDENTITY.md
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tests.md6_quarantine_reference import (
    CANONICALIZATION_VERSION,
    HASH_ALGORITHM,
    Authority,
    CandidateDataset,
    CanonicalizationError,
    ContentIdentity,
    LineageRef,
    OldRegisterEntry,
    QuarantineDecision,
    QuarantineRequest,
    Refused,
    RegisterAnchor,
    RegisterUpdated,
    RequalificationRequest,
    TickRecord,
    _append,
    anchor_for,
    canonical_record_bytes,
    check_reentry,
    content_identity,
    content_profile,
    old_rule_is_quarantined,
    record_quarantine,
    register_bytes,
    requalification_history,
    requalify,
    verify_register,
)

# ---- SYNTHETIC fixtures -------------------------------------------------------------

REVIEWER = Authority("reviewer-1", "INDEPENDENT_DATA_REVIEWER")
QUANT = Authority("quant-1", "QUANT_APPROVER")
AUTHOR = Authority("author-1", "STRATEGY_AUTHOR")
SUBMITTER_ID = "submitter-1"
EMPTY_REGISTER = b""
EMPTY_ANCHOR = anchor_for(())


def _ticks(n: int, start: int = 1_000) -> tuple[TickRecord, ...]:
    """SYNTHETIC ticks: integer raw ms, stored decimal strings, stored flags."""
    return tuple(
        TickRecord(raw_msc=start + 250 * i, bid=f"1.1{i:03d}0", ask=f"1.1{i:03d}5", flags="6")
        for i in range(n)
    )


RECORDS = _ticks(8)
REJECTED = CandidateDataset(
    dataset_id="ds-synthetic-A",
    records=RECORDS,
    metadata=(
        ("filename", "SYN_A_chunk_0001.jsonl"),
        ("manifest_label", "synthetic extraction A"),
        ("conversion_declaration", "segment-set-1 (evidence_ref EV-SYN-1)"),
        ("instrument_label", "SYN.PAIR"),
    ),
)


def _quarantine(
    dataset: CandidateDataset = REJECTED,
    register: bytes = EMPTY_REGISTER,
    anchor: RegisterAnchor = EMPTY_ANCHOR,
    *,
    scope: str = "DATASET",
    window_id: str | None = None,
    decided_at: int = 10,
    aliases: tuple[str, ...] = (),
) -> RegisterUpdated:
    request = QuarantineRequest(
        candidate=dataset,
        scope="WINDOW" if scope == "WINDOW" else "DATASET",
        window_id=window_id,
        instrument_id="SYN.PAIR",
        defect_class="hard:crossed_quote",
        criteria_id="PREREG-SYN-CRIT-3",
        evidence_refs=("EV-SYN-quality-report-1",),
        submitter_id=SUBMITTER_ID,
        strategy_author_ids=(AUTHOR.person_id,),
        previous_aliases=aliases,
        authority=REVIEWER,
        decided_at=decided_at,
        post_evaluation=False,
    )
    result = record_quarantine(register, anchor, request)
    assert isinstance(result, RegisterUpdated), result
    return result


QREG = _quarantine()


def _requal_request(
    identity: ContentIdentity | None = None,
    *,
    authority: Authority = QUANT,
    evidence: tuple[str, ...] = ("EV-SYN-new-retrieval-2",),
    context_authors: tuple[str, ...] = (AUTHOR.person_id,),
    decided_at: int = 20,
) -> RequalificationRequest:
    return RequalificationRequest(
        target_entry_hash=QREG.entry.entry_hash,
        requalified_identity=identity or content_identity(RECORDS),
        new_evidence_refs=evidence,
        authority=authority,
        context_strategy_author_ids=context_authors,
        rationale="SYNTHETIC: new retrieval shows the defect was a transport artifact",
        decided_at=decided_at,
        post_evaluation=False,
    )


def _check(candidate: CandidateDataset, reg: RegisterUpdated = QREG) -> str:
    return check_reentry(candidate, reg.register, reg.anchor).outcome


# ---- C1 to C4: re-entry of rejected content ------------------------------------------


def test_c1_same_bytes_new_filename_is_quarantined_match() -> None:
    candidate = replace(REJECTED, metadata=(("filename", "renamed_chunk_9999.jsonl"),))
    result = check_reentry(candidate, QREG.register, QREG.anchor)
    assert result.outcome == "QUARANTINED_MATCH"
    assert result.relation == "EXACT_ORDERED"
    assert result.matched_entry_hashes == (QREG.entry.entry_hash,)
    assert result.eligibility == "BLOCKED"


def test_c2_same_content_new_dataset_id_is_quarantined_match() -> None:
    candidate = replace(REJECTED, dataset_id="ds-synthetic-B-reextracted")
    assert _check(candidate) == "QUARANTINED_MATCH"


def test_c2_old_rule_misses_same_content_under_new_identity() -> None:
    """OLD D13 as written: keyed by window id / instrument only, no content field."""
    old_register = [
        OldRegisterEntry(
            window_id="ds-synthetic-A/w01",
            instrument="SYN.PAIR",
            defect_class="hard:crossed_quote",
            evidence_ids=("EV-SYN-quality-report-1",),
            decision_date=10,
            decider=REVIEWER.person_id,
        )
    ]
    # Same bytes re-extracted get a new dataset_id, hence a new manifest-issued window id.
    old_detects = old_rule_is_quarantined(old_register, "ds-synthetic-B/w01", "SYN.PAIR")
    assert old_detects is False  # old-policy failure: re-entry undetected
    new_result = _check(replace(REJECTED, dataset_id="ds-synthetic-B"))
    assert new_result == "QUARANTINED_MATCH"  # corrected behaviour


def test_old_rule_is_content_blind() -> None:
    """The old lookup cannot consult content: its answer depends only on labels."""
    old_register = [
        OldRegisterEntry("w01", "SYN.PAIR", "hard:crossed_quote", ("EV-1",), 10, "reviewer-1")
    ]
    # Unrelated clean content under the same labels: blocked (false positive).
    assert old_rule_is_quarantined(old_register, "w01", "SYN.PAIR") is True
    # Same rejected content under a relabelled instrument: released (false negative).
    assert old_rule_is_quarantined(old_register, "w01", "SYN.PAIR.v2") is False
    # Old fields have no place for a dataset-level (D09 hard-defect) rejection at all.
    assert "dataset_id" not in OldRegisterEntry.__dataclass_fields__
    assert "content_hash" not in OldRegisterEntry.__dataclass_fields__


def test_c3_modified_metadata_is_quarantined_match() -> None:
    candidate = replace(
        REJECTED,
        dataset_id="ds-synthetic-A-redeclared",
        metadata=(
            ("filename", "SYN_A_chunk_0001.jsonl"),
            ("manifest_label", "relabelled manifest"),
            ("conversion_declaration", "segment-set-2 (evidence_ref EV-SYN-9)"),
            ("instrument_label", "SYN.PAIR.alt"),
        ),
    )
    assert content_identity(candidate.records) == QREG.entry.quarantine.content_identity  # type: ignore[union-attr]
    assert _check(candidate) == "QUARANTINED_MATCH"


def test_c4_reordered_records_match_by_multiset() -> None:
    reordered = tuple(reversed(RECORDS))
    original = content_identity(RECORDS)
    shuffled = content_identity(reordered)
    assert shuffled.ordered_content_hash != original.ordered_content_hash  # D10: order matters
    assert shuffled.multiset_fingerprint == original.multiset_fingerprint
    result = check_reentry(replace(REJECTED, records=reordered), QREG.register, QREG.anchor)
    assert result.outcome == "QUARANTINED_MATCH"
    assert result.relation == "MULTISET"


# ---- canonicalization ambiguity ------------------------------------------------------


def test_c4b_trailing_zero_representation_is_not_normalized_and_fails_closed() -> None:
    stored = TickRecord(raw_msc=5_000, bid="1.10", ask="1.20", flags="6")
    restated = TickRecord(raw_msc=5_000, bid="1.1", ask="1.2", flags="6")
    assert canonical_record_bytes(stored) != canonical_record_bytes(restated)  # exact string
    reg = _quarantine(replace(REJECTED, records=(stored,)))
    result = check_reentry(replace(REJECTED, records=(restated,)), reg.register, reg.anchor)
    assert result.outcome == "CANONICALIZATION_CONFLICT"
    assert result.relation == "REPRESENTATION_DRIFT"
    assert result.eligibility == "UNVERIFIED"
    assert not result.may_proceed_to_part_a


@pytest.mark.parametrize(
    "token", ["nan", "inf", "-inf", "1e-3", "1.1E0", " 1.1", "1.1 ", "+1.1", "01.1", "1.", ".5", ""]
)
def test_c4b_non_canonical_token_fails_closed(token: str) -> None:
    bad = TickRecord(raw_msc=1_000, bid=token, ask="1.2", flags="6")
    with pytest.raises(CanonicalizationError):
        content_profile((bad,))
    result = check_reentry(replace(REJECTED, records=(*RECORDS, bad)), QREG.register, QREG.anchor)
    assert result.outcome == "CANONICALIZATION_CONFLICT"
    assert result.relation == "NON_CANONICAL_CONTENT"
    assert result.eligibility == "UNVERIFIED"


def test_c4b_non_canonical_flags_and_raw_msc_fail_closed() -> None:
    for bad in (
        TickRecord(raw_msc=1_000, bid="1.1", ask="1.2", flags="06"),
        TickRecord(raw_msc=True, bid="1.1", ask="1.2", flags=None),
    ):
        result = check_reentry(replace(REJECTED, records=(bad,)), QREG.register, QREG.anchor)
        assert result.outcome == "CANONICALIZATION_CONFLICT"


def test_c4b_declared_nonfinite_tokens_are_content_and_registerable() -> None:
    """A dataset rejected for a non-finite hard defect can still be registered."""
    nan_tick = TickRecord(raw_msc=7_000, bid="NaN", ask="1.2", flags="6")
    reg = _quarantine(replace(REJECTED, records=(*RECORDS, nan_tick)))
    again = replace(REJECTED, dataset_id="ds-nan-again", records=(*RECORDS, nan_tick))
    assert _check(again, reg) == "QUARANTINED_MATCH"


# ---- C5, C6: derived data ------------------------------------------------------------


def test_c5_derived_subset_is_derived_from_quarantined() -> None:
    subset = replace(REJECTED, dataset_id="ds-subset", records=RECORDS[2:6])
    result = check_reentry(subset, QREG.register, QREG.anchor)
    assert result.outcome == "DERIVED_FROM_QUARANTINED"
    assert result.relation == "SUBSET"


@pytest.mark.parametrize(
    ("records", "relation"),
    [
        ((*RECORDS, *_ticks(2, start=90_000)), "SUPERSET"),
        ((*RECORDS[:3], *_ticks(2, start=90_000)), "PARTIAL_OVERLAP"),
    ],
)
def test_c5_superset_and_partial_overlap_are_derived(
    records: tuple[TickRecord, ...], relation: str
) -> None:
    result = check_reentry(replace(REJECTED, records=records), QREG.register, QREG.anchor)
    assert result.outcome == "DERIVED_FROM_QUARANTINED"
    assert result.relation == relation


def test_c6_child_with_declared_rejected_parent_is_derived() -> None:
    child = CandidateDataset(
        dataset_id="ds-child-resampled",
        records=_ticks(4, start=500_000),  # no shared records
        declared_parents=(
            LineageRef("ds-synthetic-A", content_identity(RECORDS).ordered_content_hash),
        ),
    )
    result = check_reentry(child, QREG.register, QREG.anchor)
    assert result.outcome == "DERIVED_FROM_QUARANTINED"
    assert result.relation == "DECLARED_LINEAGE"


def test_c6_lineage_through_previous_alias_is_derived() -> None:
    reg = _quarantine(aliases=("ds-old-alias",))
    child = CandidateDataset(
        dataset_id="ds-child-2",
        records=_ticks(3, start=700_000),
        declared_parents=(LineageRef("ds-old-alias", "f" * 64),),
    )
    assert _check(child, reg) == "DERIVED_FROM_QUARANTINED"


# ---- C7: register integrity ----------------------------------------------------------


def _lines(blob: bytes) -> list[bytes]:
    return blob.split(b"\n")[:-1]


def _two_entry_register() -> RegisterUpdated:
    other = replace(REJECTED, dataset_id="ds-other", records=_ticks(3, start=300_000))
    return _quarantine(other, QREG.register, QREG.anchor, decided_at=11)


def _corruptions() -> dict[str, tuple[bytes | None, RegisterAnchor | None]]:
    two = _two_entry_register()
    lines = _lines(two.register)
    tampered = json.loads(lines[0])
    tampered["quarantine"]["defect_class"] = "soft:zero_spread"
    tampered_line = json.dumps(tampered, sort_keys=True, separators=(",", ":")).encode()
    return {
        "missing_register": (None, two.anchor),
        "missing_anchor": (two.register, None),
        "edited_entry_hash_mismatch": (tampered_line + b"\n" + lines[1] + b"\n", two.anchor),
        "truncated_last_entry": (lines[0] + b"\n", two.anchor),
        "truncated_mid_line": (two.register[:-40], two.anchor),
        "reordered_entries": (lines[1] + b"\n" + lines[0] + b"\n", two.anchor),
        "garbage_bytes": (b"\x00\xffnot json\n", two.anchor),
        "emptied_register": (b"", two.anchor),
        "anchor_from_other_register": (two.register, QREG.anchor),
    }


@pytest.mark.parametrize("case", sorted(_corruptions()))
def test_c7_missing_or_corrupted_register_is_unverified(case: str) -> None:
    blob, anchor = _corruptions()[case]
    for candidate in (
        REJECTED,
        replace(REJECTED, dataset_id="ds-unrelated", records=_ticks(5, start=900_000)),
    ):
        result = check_reentry(candidate, blob, anchor)
        assert result.outcome == "UNVERIFIED", case
        assert result.eligibility == "UNVERIFIED"
        assert not result.may_proceed_to_part_a


def test_c7_broken_hash_chain_with_recomputed_entry_hash_is_unverified() -> None:
    """An attacker recomputing one entry hash still breaks the next entry's back-link."""
    from tests.md6_quarantine_reference import _entry_hash  # private: tamper simulation only

    two = _two_entry_register()
    lines = _lines(two.register)
    first = json.loads(lines[0])
    first["quarantine"]["evidence_refs"] = ["EV-forged"]
    del first["entry_hash"]
    first["entry_hash"] = _entry_hash(first)
    forged = json.dumps(first, sort_keys=True, separators=(",", ":")).encode()
    blob = forged + b"\n" + lines[1] + b"\n"
    assert check_reentry(REJECTED, blob, two.anchor).outcome == "UNVERIFIED"


def test_c7_empty_register_needs_an_anchor() -> None:
    unrelated = replace(REJECTED, records=_ticks(2, start=800_000))
    assert check_reentry(unrelated, b"", None).outcome == "UNVERIFIED"
    assert check_reentry(unrelated, b"", EMPTY_ANCHOR).outcome == "NOT_KNOWN"


# ---- C8: conflicting canonicalization / hash versions ---------------------------------


def _register_with_identity(identity: ContentIdentity) -> RegisterUpdated:
    profile = content_profile(RECORDS)
    q = QuarantineDecision(
        scope="DATASET",
        content_identity=identity,
        record_hashes=profile.record_hashes,
        shadow_record_hashes=profile.shadow_record_hashes,
        dataset_id="ds-legacy",
        previous_aliases=(),
        lineage_parents=(),
        window_id=None,
        instrument_id="SYN.PAIR",
        defect_class="hard:crossed_quote",
        criteria_id="PREREG-SYN-CRIT-3",
        evidence_refs=("EV-SYN-1",),
        submitter_id=SUBMITTER_ID,
        strategy_author_ids=(),
        metadata=(),
    )
    entry = _append((), "QUARANTINE", "QUARANTINED", 1, False, REVIEWER, q, None)
    return RegisterUpdated(register_bytes((entry,)), anchor_for((entry,)), entry)


@pytest.mark.parametrize(
    ("version", "algorithm"),
    [("nexora.md6-tick-content/0", HASH_ALGORITHM), (CANONICALIZATION_VERSION, "sha1")],
)
def test_c8_register_with_other_canonicalization_is_conflict(version: str, algorithm: str) -> None:
    legacy = replace(
        content_identity(RECORDS), canonicalization_version=version, hash_algorithm=algorithm
    )
    reg = _register_with_identity(legacy)
    verify_register(reg.register, reg.anchor)  # the register itself is intact
    for candidate in (REJECTED, replace(REJECTED, records=_ticks(2, start=600_000))):
        result = check_reentry(candidate, reg.register, reg.anchor)
        assert result.outcome == "CANONICALIZATION_CONFLICT"
        assert result.relation == "VERSION_MISMATCH"
        assert result.eligibility == "UNVERIFIED"


def test_c8_candidate_declared_under_other_version_is_conflict() -> None:
    declared = replace(content_identity(RECORDS), canonicalization_version="other/9")
    candidate = replace(REJECTED, declared_identity=declared)
    assert _check(candidate) == "CANONICALIZATION_CONFLICT"


def test_c8_declared_identity_not_matching_records_is_unverified() -> None:
    declared = content_identity(_ticks(3, start=111_000))
    candidate = replace(REJECTED, declared_identity=declared)
    assert _check(candidate) == "UNVERIFIED"


def test_c8_requalification_across_versions_is_refused() -> None:
    reg = _register_with_identity(replace(content_identity(RECORDS), hash_algorithm="sha1"))
    request = replace(_requal_request(), target_entry_hash=reg.entry.entry_hash)
    result = requalify(reg.register, reg.anchor, request)
    assert isinstance(result, Refused)
    assert result.reason.startswith("CANONICALIZATION_CONFLICT")


# ---- C9, C10: requalification --------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "request_"),
    [
        ("strategy_author_role", _requal_request(authority=AUTHOR)),
        (
            "reviewer_is_original_submitter",
            _requal_request(authority=Authority(SUBMITTER_ID, "INDEPENDENT_DATA_REVIEWER")),
        ),
        (
            "quant_is_context_strategy_author",
            _requal_request(context_authors=("quant-1",)),
        ),
        ("missing_new_evidence", _requal_request(evidence=())),
        ("evidence_already_cited", _requal_request(evidence=("EV-SYN-quality-report-1",))),
        ("other_role", _requal_request(authority=Authority("ops-1", "OTHER"))),
        ("decided_before_head", _requal_request(decided_at=1)),
    ],
)
def test_c9_unauthorized_requalification_is_refused(
    label: str, request_: RequalificationRequest
) -> None:
    result = requalify(QREG.register, QREG.anchor, request_)
    assert isinstance(result, Refused), label
    assert _check(REJECTED) == "QUARANTINED_MATCH"  # register unchanged, still quarantined


def test_c9_requalification_against_corrupt_register_is_refused() -> None:
    result = requalify(QREG.register[:-5], QREG.anchor, _requal_request())
    assert isinstance(result, Refused)
    assert result.reason.startswith("UNVERIFIED")


def test_c10_legitimate_requalification_preserves_history_and_grants_no_trust() -> None:
    result = requalify(QREG.register, QREG.anchor, _requal_request())
    assert isinstance(result, RegisterUpdated)
    assert result.register.startswith(QREG.register)  # append-only: original bytes intact
    check = check_reentry(REJECTED, result.register, result.anchor)
    assert check.outcome == "REQUALIFIED"
    assert check.trust_granted is False
    assert check.eligibility == "PART_A_REQUIRED"  # normal criteria still decide
    history = requalification_history(result.register, result.anchor, QREG.entry.entry_hash)
    assert [e.kind for e in history] == ["QUARANTINE", "REQUALIFICATION"]
    assert history[0] == QREG.entry  # original rejection preserved, never deleted
    assert history[1].authority == QUANT
    assert history[1].requalification is not None
    assert history[1].requalification.new_evidence_refs == ("EV-SYN-new-retrieval-2",)


def test_c10_requalification_is_specific_to_the_content_identity() -> None:
    result = requalify(QREG.register, QREG.anchor, _requal_request())
    assert isinstance(result, RegisterUpdated)
    reordered = replace(REJECTED, records=tuple(reversed(RECORDS)))
    assert _check(reordered, result) == "QUARANTINED_MATCH"
    subset = replace(REJECTED, records=RECORDS[:4])
    assert _check(subset, result) == "DERIVED_FROM_QUARANTINED"


def test_c10_second_requalification_of_same_identity_is_refused() -> None:
    first = requalify(QREG.register, QREG.anchor, _requal_request())
    assert isinstance(first, RegisterUpdated)
    again = requalify(
        first.register, first.anchor, _requal_request(evidence=("EV-SYN-other-3",), decided_at=30)
    )
    assert isinstance(again, Refused)


def test_c10_requarantine_after_requalification_wins() -> None:
    first = requalify(QREG.register, QREG.anchor, _requal_request())
    assert isinstance(first, RegisterUpdated)
    again = _quarantine(REJECTED, first.register, first.anchor, decided_at=40)
    assert _check(REJECTED, again) == "QUARANTINED_MATCH"


# ---- registration rules and baseline behaviour ---------------------------------------


def test_dataset_level_rejection_is_registered_with_all_required_fields() -> None:
    q = QREG.entry.quarantine
    assert q is not None
    assert q.scope == "DATASET" and q.window_id is None
    assert q.content_identity.canonicalization_version == CANONICALIZATION_VERSION
    assert q.content_identity.hash_algorithm == HASH_ALGORITHM
    assert q.dataset_id == "ds-synthetic-A"
    assert q.criteria_id and q.defect_class and q.evidence_refs
    assert len(q.record_hashes) == len(RECORDS)
    assert QREG.entry.prev_entry_hash == "0" * 64
    assert QREG.entry.authority == REVIEWER
    assert ("conversion_declaration", "segment-set-1 (evidence_ref EV-SYN-1)") in q.metadata


def test_window_level_quarantine_is_registered() -> None:
    reg = _quarantine(replace(REJECTED, records=RECORDS[:3]), scope="WINDOW", window_id="w01")
    assert reg.entry.quarantine is not None and reg.entry.quarantine.window_id == "w01"
    assert _check(replace(REJECTED, records=RECORDS[:3]), reg) == "QUARANTINED_MATCH"


@pytest.mark.parametrize(
    "authority",
    [AUTHOR, Authority(SUBMITTER_ID, "QUANT_APPROVER"), Authority("author-1", "QUANT_APPROVER")],
)
def test_strategy_author_or_submitter_cannot_record_quarantine(authority: Authority) -> None:
    request = QuarantineRequest(
        candidate=REJECTED,
        scope="DATASET",
        window_id=None,
        instrument_id="SYN.PAIR",
        defect_class="hard:crossed_quote",
        criteria_id="PREREG-SYN-CRIT-3",
        evidence_refs=("EV-1",),
        submitter_id=SUBMITTER_ID,
        strategy_author_ids=("author-1",),
        previous_aliases=(),
        authority=authority,
        decided_at=1,
        post_evaluation=False,
    )
    assert isinstance(record_quarantine(EMPTY_REGISTER, EMPTY_ANCHOR, request), Refused)


def test_not_known_never_grants_trust() -> None:
    unrelated = CandidateDataset("ds-new", _ticks(6, start=2_000_000))
    result = check_reentry(unrelated, QREG.register, QREG.anchor)
    assert result.outcome == "NOT_KNOWN"
    assert result.trust_granted is False
    assert result.eligibility == "PART_A_REQUIRED"


def test_recording_is_deterministic() -> None:
    assert _quarantine().register == QREG.register
    assert _quarantine().anchor == QREG.anchor
