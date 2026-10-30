"""Wave V9 (V8 reviewer findings V8B-1, V8B-2 and the N1 residual) for the evidence validator.

Every assertion states the REMEDIATED behaviour. The same attacks were run against the previous
head (60de064) with `repro_old.py`-style records and were all ACCEPTED there (recorded in the PR
description). Scope notes, so nothing here over-claims:

* The forbidden-operation check is an ALLOW-list over recorded NAMES. It does not detect arbitrary
  code execution, dynamic attribute access that was never recorded, or a recorder that lies.
* Placeholder and mock-marker detection are deny-lists and therefore incomplete by nature; the
  positive checks (hashes, signatures, enumerations, allow-lists, probe outcomes) carry the weight.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.windows_integration_readiness import evidence_validator as ev
from tests.windows_integration_readiness import mt5_reference as m5
from tests.windows_integration_readiness.test_evidence_validator import (
    DOCS,
    Rec,
    check,
    real_shaped_pass,
    synthetic,
    template,
    with_gate,
)
from tests.windows_integration_readiness.vectors import ALLOWED_MT5_CALLS, FORBIDDEN_MT5_NAMES

SENTINEL = ev.NOT_VERIFIED


def _errors(mutate: Callable[[Rec], None], *, regate: bool = False) -> list[str]:
    r = real_shaped_pass()
    mutate(r)
    if regate:
        with_gate(r)
    report = check(r)
    assert not report.ok
    return report.errors


def _set(path: list[Any], value: Any) -> Callable[[Rec], None]:
    def mutate(r: Rec) -> None:
        node: Any = r
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


# ---- V8B-2: forbidden operations ---------------------------------------------------------------
ALIASES = [
    "mt5.order_send",
    "MetaTrader5.order_send",
    "metatrader5.ORDER_SEND",
    "order_send()",
    "order_send ",
    " order_send",
    "mt5.order_send(request)",
    "ORDER_SEND",
    "order-send",
    "order send",
    "order.send",
    "orderSend",
    "Order_Send",
    "ｏｒｄｅｒ＿ｓｅｎｄ",  # fullwidth
    "order_s​end",  # zero-width space
    "оrder_send",  # Cyrillic o
    "getattr(mt5, 'order_send')",
    "evil.module.order_send",
]


@pytest.mark.parametrize("alias", ALIASES)
def test_v8b2_every_spelling_of_order_send_is_counted(alias: str) -> None:
    def mutate(r: Rec) -> None:
        r["cases"]["7"]["evidence"]["observed_mt5_names"] = ["initialize", alias]
        r["cases"]["7"]["evidence"]["forbidden_names_observed"] = 0  # the old, wrong, count

    errors = _errors(mutate)
    assert "derived_flag_contradicts_evidence:cases.7.forbidden_names_observed" in errors
    assert "pass_contradicted_by_evidence:cases.7.forbidden_name_observed" in errors


@pytest.mark.parametrize("alias", ALIASES)
def test_v8b2_classifier_reports_the_matched_alias(alias: str) -> None:
    got = m5.classify_reference(alias)
    assert got.status == m5.FORBIDDEN and got.matched_alias == "order_send", got


@pytest.mark.parametrize("name", FORBIDDEN_MT5_NAMES)
def test_v8b2_every_known_forbidden_name_in_every_representation(name: str) -> None:
    for spelling in (
        name,
        f"mt5.{name}",
        f"MetaTrader5.{name}()",
        name.upper(),
        name.replace("_", "-"),
        f"{name} ",
    ):
        assert m5.classify_reference(spelling).status == m5.FORBIDDEN, spelling


@pytest.mark.parametrize("name", sorted(ALLOWED_MT5_CALLS))
def test_v8b2_allowed_names_pass_in_supported_representations(name: str) -> None:
    for spelling in (name, f" {name} ", f"mt5.{name}", f"MetaTrader5.{name}()"):
        got = m5.classify_reference(spelling)
        assert got.status == m5.ALLOWED and got.canonical == name, (spelling, got)


@pytest.mark.parametrize(
    "ref",
    [
        "Initialize",  # case differs: not the allowed name
        "INITIALIZE",
        "copy_ticks_from",  # a read call, but not on the allow-list
        "symbols_get",
        "evil.initialize",  # unknown receiver
        "mt5.sub.initialize",
        "init",
        "SYNTHETIC",
        "i​nitialize",  # hidden character
        "initialise",
        "",
        "()",
    ],
)
def test_v8b2_anything_off_the_allow_list_counts_as_unknown(ref: str) -> None:
    got = m5.classify_reference(ref)
    assert not got.allowed and got.status in (m5.UNKNOWN, m5.FORBIDDEN), got


@pytest.mark.parametrize(
    "ref",
    [
        {"module": "MetaTrader5", "name": "order_send"},
        {"module": "mt5", "attr": "OrderSend"},
        ("MetaTrader5", "order_send"),
        ["mt5", "order", "send"],
    ],
)
def test_v8b2_structured_references_are_classified_too(ref: Any) -> None:
    got = m5.classify_reference(ref)
    assert got.status == m5.FORBIDDEN
    assert "structured_reference" in got.problems


def test_v8b2_structured_allowed_reference_and_unsupported_shapes() -> None:
    assert m5.classify_reference({"module": "MetaTrader5", "name": "initialize"}).allowed
    assert m5.classify_reference(("mt5", "copy_ticks_range")).allowed
    for bad in (None, 5, {"module": 1, "name": "initialize"}, {"name": "initialize", "x": "y"}):
        got = m5.classify_reference(bad)
        assert got.status == m5.UNKNOWN and "unsupported_reference_type" in got.problems


def test_v8b2_distinct_spellings_of_one_operation_count_once_and_two_operations_twice() -> None:
    assert len(m5.non_allowed_keys(["mt5.order_send", "ORDER_SEND", "order_send()"])) == 1
    assert len(m5.non_allowed_keys(["order_send", "positions_get"])) == 2
    assert m5.non_allowed_keys(["initialize", "mt5.shutdown()"]) == frozenset()


def test_v8b2_recorded_count_must_equal_the_derived_count_and_zero_is_required_for_pass() -> None:
    def two(r: Rec) -> None:
        ev7 = r["cases"]["7"]["evidence"]
        ev7["observed_mt5_names"] = ["initialize", "mt5.order_send", "positions_get()"]
        ev7["forbidden_names_observed"] = 2

    errors = _errors(two)
    assert "derived_flag_contradicts_evidence:cases.7.forbidden_names_observed" not in errors
    assert "pass_contradicted_by_evidence:cases.7.forbidden_name_observed" in errors
    assert "pass_contradicted_by_evidence:cases.7.evidence.forbidden_names_observed" in errors


def test_v8b2_forbidden_name_hidden_in_the_json_evidence_is_refused() -> None:
    def hidden(r: Rec) -> None:
        r["cases"]["7"]["evidence"]["initialize_args"] = {"calls": ["MetaTrader5.order-send"]}

    assert "case7_forbidden_reference_in_evidence:initialize_args" in _errors(hidden)
    r = real_shaped_pass()
    r["cases"]["7"]["evidence"]["shutdown_paths"] = {"MT5.OrderSend": "x"}
    assert "case7_forbidden_reference_in_evidence:shutdown_paths" in check(r).errors


def test_v8b2_a_clean_allowed_list_is_accepted_and_negative_evidence_is_preserved() -> None:
    r = real_shaped_pass()
    r["cases"]["7"]["evidence"]["observed_mt5_names"] = [
        "mt5.initialize()",
        "MetaTrader5.copy_ticks_range",
        "COPY_TICKS_ALL",
        "shutdown",
    ]
    assert check(r).ok  # forbidden_names_observed stays 0: explicit negative evidence


# ---- V8B-1: the sentinel is not a value ----------------------------------------------------------
NESTED: list[tuple[str, list[Any], Any]] = [
    ("commands_run", ["execution", "commands_run"], [SENTINEL]),
    ("case1.names", ["cases", "1", "evidence", "names"], [SENTINEL]),
    ("case3.refusals_observed", ["cases", "3", "evidence", "refusals_observed"], [SENTINEL]),
    ("case4.refusals_observed", ["cases", "4", "evidence", "refusals_observed"], [SENTINEL]),
    ("case7.observed_mt5_names", ["cases", "7", "evidence", "observed_mt5_names"], [SENTINEL]),
    ("registry_identities", ["custody", "registry_identities"], [SENTINEL]),
    ("approver_identities", ["custody", "approver_identities"], [SENTINEL]),
    ("verifier_argv", ["custody", "verifier_argv"], [SENTINEL]),
    ("verifier_status_reasons", ["custody", "verifier_status_reasons"], [SENTINEL]),
    ("gate_reasons", ["custody", "gate_reasons"], [SENTINEL]),
    ("status_ref.path", ["checkout", "status_ref", "path"], SENTINEL),
    ("registry_ref.path", ["custody", "registry_ref", "path"], SENTINEL),
    ("pin_ref.path", ["custody", "pin_ref", "path"], SENTINEL),
    ("verifier_attestation_ref.path", ["people", "verifier_attestation_ref", "path"], SENTINEL),
    ("evidence_refs[0].path", ["cases", "1", "evidence_refs", 0, "path"], SENTINEL),
    ("raw_output_refs[0].path", ["execution", "raw_output_refs", 0, "path"], SENTINEL),
    ("json leaf", ["cases", "2", "evidence", "quality_counts"], {"ok": SENTINEL}),
    ("json nested", ["cases", "2", "evidence", "vector_results"], {"a": [{"b": SENTINEL}]}),
    ("json list", ["cases", "4", "evidence", "transitions"], [SENTINEL]),
]


@pytest.mark.parametrize(("name", "path", "value"), NESTED, ids=[n[0] for n in NESTED])
def test_v8b1_nested_sentinel_is_refused(name: str, path: list[Any], value: Any) -> None:
    r = real_shaped_pass()
    _set(path, copy.deepcopy(value))(r)
    report = ev.validate_record(r, _test_only_dependencies=None)
    assert not report.ok, name
    report = check(r)
    assert not report.ok, (name, report.errors)


@pytest.mark.parametrize(("name", "path", "value"), NESTED[:-3], ids=[n[0] for n in NESTED[:-3]])
def test_v8b1_nested_sentinel_is_refused_even_when_the_case_result_is_not_pass(
    name: str, path: list[Any], value: Any
) -> None:
    r = synthetic()
    _set(path, copy.deepcopy(value))(r)
    if path[0] == "cases":
        r["cases"][path[1]]["result"] = "NOT_RUN"
    report = ev.validate_record(r)
    assert any(
        e.startswith(("placeholder_value", "invalid_value", "claim_evidence_invalid"))
        for e in report.errors
    ), (name, report.errors)


def test_v8b1_sentinel_as_a_claim_evidence_reference_path_is_refused() -> None:
    def mutate(r: Rec) -> None:
        r["not_verified"].remove("broker_clock_rule_truth")
        r["claim_evidence"]["broker_clock_rule_truth"] = {"path": SENTINEL, "sha256": "a" * 64}

    assert "claim_evidence_invalid:broker_clock_rule_truth" in _errors(mutate)


def test_v8b1_the_sentinel_still_marks_a_whole_unfilled_field_and_blocks_pass() -> None:
    r = real_shaped_pass()
    r["node"]["antivirus_indexer_state"] = SENTINEL
    report = check(r)
    assert any(
        e.startswith("pass_with_unverified_field:node.antivirus_indexer_state")
        for e in report.errors
    )
    r = synthetic()
    r["node"]["antivirus_indexer_state"] = SENTINEL
    assert ev.validate_record(r).ok  # unfilled is fine outside a PASS
    assert ev.is_missing_marker(SENTINEL) and not ev.is_placeholder(SENTINEL)


def test_v8b1_empty_lists_remain_valid_negative_evidence_where_the_schema_allows_them() -> None:
    r = real_shaped_pass()
    assert r["custody"]["verifier_status_reasons"] == []
    assert r["custody"]["revocation_seqs"] == [] and r["custody"]["revocation_ids"] == []
    assert check(r).ok
    r["cases"]["3"]["evidence"]["refusals_observed"] = []  # minItems 1: positive evidence needed
    assert not check(r).ok


PLACEHOLDER_VARIANTS = [
    "none",
    "None",
    "NONE",
    "undefined",
    "not verified yet",
    "Not Verified Yet",
    "N/A (see notes)",
    "n/a [see below]",
    "TBD (later)",
    "unknown",
    "unkоnwn",  # Cyrillic o and a transposition (reviewer's literal)
    "unknоwn",  # Cyrillic o
    "ТBD",  # Cyrillic Te
    "unkown",  # one edit
    "NOT  VERIFIED ",
    "nil",
    "missing",
    "-",
    "?",
    "not set",
    "unspecified",
]


@pytest.mark.parametrize("value", PLACEHOLDER_VARIANTS)
def test_v8b1_placeholder_variants_are_refused_in_text_lists_and_paths(value: str) -> None:
    assert "placeholder_value:node.antivirus_indexer_state" in _errors(
        _set(["node", "antivirus_indexer_state"], value)
    )
    assert "placeholder_value:cases.1.evidence.names" in _errors(
        _set(["cases", "1", "evidence", "names"], ["ok-name", value])
    )
    assert "placeholder_value:checkout.status_ref" in _errors(
        _set(["checkout", "status_ref", "path"], value)
    )


@pytest.mark.parametrize(
    "value",
    [
        "(1, 'Success')",  # MetaTrader5.last_error() text
        "copy_ticks_range returned None for the empty window",
        "none installed",
        "NA-1 server",
        "Windows 11 Pro 23H2 (build 22631.4317)",
        "partially not verified by vendor; see attached volume info",
        "pending review by the second person",
        "empty window: zero rows",
    ],
)
def test_v8b1_legitimate_text_is_not_mistaken_for_a_placeholder(value: str) -> None:
    r = real_shaped_pass()
    r["node"]["antivirus_indexer_state"] = value
    r["cases"]["1"]["evidence"]["last_error"] = value
    assert check(r).ok, check(r).errors


MARKERS = [
    "SyntheticRun1",
    "SYNTHETIC1",
    "S​YNTHETIC-1",  # zero-width split
    "MockSource",
    "FixtureSource",
    "mock_source",
    "ＭＯＣＫ terminal",
    "synthetic_mock",
    "TEST-ONLY-PASS-SHAPE",
    "test only",
    "Dummy-node",
    "fakeBroker",
    "Ѕynthetic-1",  # Cyrillic S
    "syntheticrun",
]


@pytest.mark.parametrize("value", MARKERS)
def test_v8b1_mock_markers_are_refused_in_real_records(value: str) -> None:
    assert "real_record_carries_mock_marker:identity.record_id" in _errors(
        _set(["identity", "record_id"], value)
    )
    assert "real_record_carries_mock_marker:source_provenance.source_descriptor" in _errors(
        _set(["source_provenance", "source_descriptor"], value)
    )
    assert "real_record_carries_mock_marker:execution.commands_run" in _errors(
        _set(["execution", "commands_run"], [["run", value]])
    )


@pytest.mark.parametrize(
    "value",
    ["MetaQuotes-Demo", "Hammock Capital Server", "node-class-a", "Fixed-Income-Desk", "Faker"[:4]],
)
def test_v8b1_real_looking_names_are_not_mistaken_for_markers(value: str) -> None:
    r = real_shaped_pass()
    r["terminal"]["server_name"] = value
    r["node"]["hostname_class"] = value
    # "Fake" is itself a marker prefix (over-blocking, documented); the others must pass
    report = check(r)
    if ev.has_mock_marker(value):
        assert value == "Fake"
    else:
        assert report.ok, report.errors


def test_v8b1_synthetic_class_may_carry_markers() -> None:
    assert ev.validate_record(synthetic()).ok  # the fixture is full of them, by design


def test_v8b1_real_record_id_and_extractor_head_are_positively_validated() -> None:
    assert "real_record_id_malformed" in _errors(_set(["identity", "record_id"], "short"))
    assert "real_record_id_malformed" in _errors(_set(["identity", "record_id"], "REAL ID 0001"))
    errors = _errors(_set(["identity", "extractor_sha"], "a" * 40))
    assert "real_record_extractor_sha_not_pinned_pr96_candidate:NOT_THE_PINNED_CANDIDATE" in errors


# ---- validator versus JSON Schema ----------------------------------------------------------------
def _jsonschema() -> Any:
    return pytest.importorskip(
        "jsonschema",
        reason="jsonschema is not in the locked env: the schema half is NOT run here (a skip is "
        "not a pass); see the PR description for the run that installed it outside the repo",
    )


def _schema_validator() -> Any:
    js = _jsonschema()
    schema = json.loads((DOCS / "evidence-record.schema.json").read_text(encoding="utf-8"))
    return js.Draft202012Validator(schema)


# one-edit typos are validator-only (the schema pattern lists exact spellings)
VALIDATOR_ONLY = {"unkown"}


def _ascii_corpus() -> list[tuple[str, Callable[[Rec], None]]]:
    out: list[tuple[str, Callable[[Rec], None]]] = []
    for name, path, value in NESTED:
        out.append((f"sentinel:{name}", _set(path, copy.deepcopy(value))))
    for v in PLACEHOLDER_VARIANTS:
        if v.isascii() and v not in VALIDATOR_ONLY:
            out.append((f"placeholder:{v!r}", _set(["node", "antivirus_indexer_state"], v)))
            out.append((f"placeholder-list:{v!r}", _set(["cases", "1", "evidence", "names"], [v])))
            out.append((f"placeholder-path:{v!r}", _set(["checkout", "status_ref", "path"], v)))
    return out


def test_agreement_the_schema_accepts_every_valid_record_the_validator_accepts() -> None:
    v = _schema_validator()
    for rec in (template(), synthetic(), real_shaped_pass(), real_shaped_pass("BLOCKED")):
        assert not list(v.iter_errors(rec))
        assert ev.validate_record(rec).ok or rec["overall_result"] == "PASS"


def test_agreement_both_reject_every_ascii_missing_evidence_variant() -> None:
    v = _schema_validator()
    disagreements = []
    for name, mutate in _ascii_corpus():
        r = real_shaped_pass("BLOCKED")
        mutate(r)
        validator_rejects = not ev.validate_record(r).ok
        schema_rejects = bool(list(v.iter_errors(r)))
        if not (validator_rejects and schema_rejects):
            disagreements.append((name, validator_rejects, schema_rejects))
    assert disagreements == []


def test_agreement_no_ascii_text_the_validator_accepts_is_rejected_by_the_schema() -> None:
    v = _schema_validator()
    for value in (
        "(1, 'Success')",
        "none installed",
        "NA-1 server",
        "Windows 11 Pro 23H2 (build 22631.4317)",
        "empty window: zero rows",
    ):
        r = real_shaped_pass("BLOCKED")
        r["node"]["antivirus_indexer_state"] = value
        assert ev.validate_record(r).ok
        assert not list(v.iter_errors(r)), value


def test_agreement_the_schema_is_a_documented_subset_for_non_ascii_variants() -> None:
    """Homoglyph, one-edit and bracket-after-text variants are validator-only (authoritative)."""
    v = _schema_validator()
    for value in ("ТBD", "unkown", "unkоnwn"):
        r = real_shaped_pass("BLOCKED")
        r["node"]["antivirus_indexer_state"] = value
        assert not ev.validate_record(r).ok
        assert not list(v.iter_errors(r)), value  # the schema cannot express the fold/one edit


# ---- N1 residual: case 6 flags derive from the probe outcomes; the rest is declared -----------
def _c6(r: Rec) -> dict[str, Any]:
    evidence: dict[str, Any] = r["cases"]["6"]["evidence"]
    return evidence


def test_n1_probe_error_count_must_equal_the_errors_in_the_results() -> None:
    def mutate(r: Rec) -> None:
        _c6(r)["crash_results"] = {"kill": {"outcome": "error", "detail": "x"}}

    errors = _errors(mutate)
    assert "derived_flag_contradicts_evidence:cases.6.evidence.probe_error_count" in errors


def test_n1_probe_manual_unresolved_count_must_equal_the_manual_probes() -> None:
    def mutate(r: Rec) -> None:
        _c6(r)["reparse_results"] = {"junction": {"outcome": "manual_required", "detail": "x"}}

    errors = _errors(mutate)
    assert "derived_flag_contradicts_evidence:cases.6.evidence.probe_manual_unresolved_count" in (
        errors
    )


def test_n1_consistent_counts_are_accepted_and_a_failed_probe_cannot_pass() -> None:
    def mutate(r: Rec) -> None:
        c6 = _c6(r)
        c6["crash_results"] = {"kill": {"outcome": "error", "detail": "x"}}
        c6["probe_error_count"] = 1

    errors = _errors(mutate)
    assert "derived_flag_contradicts_evidence:cases.6.evidence.probe_error_count" not in errors
    assert "pass_contradicted_by_evidence:cases.6.evidence.probe_error_count" in errors


def test_n1_unknown_probe_outcome_is_refused() -> None:
    def mutate(r: Rec) -> None:
        _c6(r)["lock_results"] = {"w": {"outcome": "ok-ish"}}

    assert "invalid_value:cases.6.evidence:probe_outcome_unknown" in _errors(mutate)


def test_n1_real_pass_needs_probe_outcomes_in_the_results() -> None:
    def mutate(r: Rec) -> None:
        c6 = _c6(r)
        c6["reparse_results"] = {"note": "operator says fine"}
        c6["crash_results"] = {"note": "operator says fine"}
        c6["lock_results"] = {"note": "operator says fine"}

    assert "pass_requires_probe_outcomes_in_case6_results" in _errors(mutate)


def test_n1_flags_without_underlying_evidence_are_reported_as_self_declared() -> None:
    report = ev.validate_record(real_shaped_pass("BLOCKED"))
    declared = set(report.as_json()["self_declared_flags"])
    assert {
        "cases.1.evidence.first_last_time_msc_in_window",
        "cases.2.evidence.hostile_context_identical",
        "cases.3.evidence.terminal_contacted_before_validation",
    } <= declared
    assert "cases.6.evidence.probe_error_count" not in declared  # derived, not declared
    syn = ev.validate_record(synthetic()).as_json()["self_declared_flags"]
    assert "cases.6.evidence.probe_error_count" not in syn  # V10: fixture carries probe nodes


def test_n1_the_filesystem_probe_output_shape_is_what_the_derivation_reads(
    tmp_path: Path,
) -> None:
    from tests.windows_integration_readiness import filesystem_probes as fp

    run = fp.run_probes(tmp_path)
    nodes = ev._outcomes(run["probes"])
    assert nodes and set(nodes) <= set(ev.PROBE_OUTCOMES) == set(fp.OUTCOMES)
    assert sum(o == "error" for o in nodes) == run["outcome_counts"]["error"]
    assert sum(o == "manual_required" for o in nodes) == run["outcome_counts"]["manual_required"]
