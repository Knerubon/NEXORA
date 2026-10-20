"""Evidence template self-consistency (docs/research/market-history-windows-readiness/).

Structural checks only (jsonschema is not in the locked env). Not tied to one matrix case."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "research" / "market-history-windows-readiness"
TESTS = Path(__file__).resolve().parent


def _load(name: str) -> Any:
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


def test_template_has_every_schema_required_key_and_no_extras() -> None:
    schema, tpl = _load("evidence-record.schema.json"), _load("evidence-record.template.json")
    assert set(schema["required"]) == set(tpl)
    assert set(schema["properties"]) == set(tpl)


def test_template_is_unfilled_every_leaf_is_not_verified_or_not_run() -> None:
    tpl = _load("evidence-record.template.json")

    def leaves(node: Any, path: str = "") -> list[tuple[str, Any]]:
        if isinstance(node, dict):
            return [x for k, v in node.items() for x in leaves(v, f"{path}/{k}")]
        return [(path, node)]

    for path, value in leaves({k: v for k, v in tpl.items() if k != "not_verified"}):
        if path.endswith("/notes"):
            assert value == ""
        else:
            assert value in ("NOT VERIFIED", "NOT_RUN"), (path, value)


def test_template_covers_seven_cases_and_overall_not_run() -> None:
    tpl = _load("evidence-record.template.json")
    assert sorted(tpl["cases"]) == ["1", "2", "3", "4", "5", "6", "7"]
    assert tpl["overall_result"] == "NOT_RUN"


def test_not_verified_list_matches_the_markdown_and_is_nonempty() -> None:
    tpl = _load("evidence-record.template.json")
    md = (DOCS / "EVIDENCE-TEMPLATE.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^- `([a-z0-9_]+)`", md, flags=re.M))
    assert tpl["not_verified"] and set(tpl["not_verified"]) == listed


def test_each_matrix_case_has_a_test_module_and_a_matrix_section() -> None:
    matrix = (DOCS / "INTEGRATION-MATRIX.md").read_text(encoding="utf-8")
    for n in range(1, 8):
        assert re.search(rf"^## Case {n} - ", matrix, flags=re.M), n
        assert list(TESTS.glob(f"test_case{n}_*.py")), n
