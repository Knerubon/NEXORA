"""Evidence template, schema, validator and documentation agree (finding B-M1, minor F11).

`evidence_validator.py` is the single field specification. The committed schema must equal the
schema generated from it, the committed template must be its unfilled form, and
EVIDENCE-TEMPLATE.md must name exactly the same fields and non-claims. Not tied to one matrix
case."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tests.windows_integration_readiness.evidence_validator import (
    CASE_FIELDS,
    MANDATORY_NOT_VERIFIED,
    NOT_VERIFIED,
    SECTIONS,
    build_schema,
    validate_record,
)

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "research" / "market-history-windows-readiness"
TESTS = Path(__file__).resolve().parent


def _load(name: str) -> Any:
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


def _md() -> str:
    return (DOCS / "EVIDENCE-TEMPLATE.md").read_text(encoding="utf-8")


def test_committed_schema_equals_generated_schema() -> None:
    assert _load("evidence-record.schema.json") == build_schema()


def test_template_is_the_unfilled_form_of_the_specification() -> None:
    tpl = _load("evidence-record.template.json")
    assert tpl["evidence_class"] == "TEMPLATE" and tpl["overall_result"] == "NOT_RUN"
    for section, fields in SECTIONS.items():
        assert tpl[section] == dict.fromkeys(fields, NOT_VERIFIED), section
    for n, fields in CASE_FIELDS.items():
        assert tpl["cases"][n] == {
            "result": "NOT_RUN",
            "evidence": dict.fromkeys(fields, NOT_VERIFIED),
            "evidence_refs": NOT_VERIFIED,
            "notes": "",
        }
    assert tpl["not_verified"] == list(MANDATORY_NOT_VERIFIED) and tpl["claim_evidence"] == {}
    assert validate_record(tpl).ok


def test_markdown_lists_exactly_the_mandatory_non_claims() -> None:
    listed = re.findall(r"^- `([a-z0-9_]+)`", _md(), flags=re.M)
    assert listed == list(MANDATORY_NOT_VERIFIED)


def test_markdown_section_table_names_every_field_by_its_json_path() -> None:
    md = _md()
    for section, fields in SECTIONS.items():
        row = re.search(rf"^\| `{section}` \|(.*)\|$", md, flags=re.M)
        assert row, section
        assert re.findall(r"`([a-z0-9_]+)`", row.group(1)) == list(fields), section


def test_markdown_case_table_names_every_case_evidence_field() -> None:
    md = _md()
    for n, fields in CASE_FIELDS.items():
        row = re.search(rf"^\| {n} \|(.*)\|$", md, flags=re.M)
        assert row, n
        assert re.findall(r"`([a-z0-9_]+)`", row.group(1)) == list(fields), n


def test_each_matrix_case_has_a_test_module_and_a_matrix_section() -> None:
    matrix = (DOCS / "INTEGRATION-MATRIX.md").read_text(encoding="utf-8")
    for n in range(1, 8):
        assert re.search(rf"^## Case {n} - ", matrix, flags=re.M), n
        assert list(TESTS.glob(f"test_case{n}_*.py")), n
