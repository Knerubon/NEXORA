"""Cross-document consistency of the readiness package (orchestrator checks, remediation V1).

Not tied to one matrix case: the matrix's evidence fields equal the validator's, no step can reach
TRUSTED through a V3 anchor, every relative link resolves, and every dependency row is complete."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.windows_integration_readiness.evidence_validator import CASE_FIELDS

DOCS = (
    Path(__file__).resolve().parents[2] / "docs" / "research" / "market-history-windows-readiness"
)
PAGES = sorted(DOCS.glob("*.md"))


def _text(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def _case_sections(matrix: str) -> dict[str, str]:
    parts = re.split(r"^## Case (\d) - ", matrix, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


@pytest.mark.parametrize("n", sorted(CASE_FIELDS))
def test_matrix_evidence_fields_equal_the_validator_fields(n: str) -> None:
    section = _case_sections(_text("INTEGRATION-MATRIX.md"))[n]
    block = section.split("Evidence fields:", 1)[1].split("\n\n", 1)[0]
    assert re.findall(rf"`case{n}\.([a-z0-9_]+)`", block) == list(CASE_FIELDS[n])


@pytest.mark.parametrize("name", ["INTEGRATION-MATRIX.md", "WINDOWS-EXECUTION-CHECKLIST.md"])
def test_no_step_claims_trust_from_a_v3_anchor(name: str) -> None:
    """B1: every mention of `--anchor` must say it never grants trust."""
    for para in _text(name).split("\n\n"):
        if "--anchor" in para:
            assert "never grants trust" in para, para[:200]


def test_case7_and_checklist_record_trusted_as_blocked() -> None:
    case7 = _case_sections(_text("INTEGRATION-MATRIX.md"))["7"]
    assert "TRUSTED is BLOCKED (DEP-01)" in case7
    assert "**BLOCKED (DEP-01):**" in _text("WINDOWS-EXECUTION-CHECKLIST.md")


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_relative_links_resolve(page: Path) -> None:
    for target in re.findall(r"\]\(([^)#:]+)(?:#[^)]*)?\)", page.read_text(encoding="utf-8")):
        assert (page.parent / target).exists(), (page.name, target)


def test_dependency_register_rows_are_complete() -> None:
    text = _text("DEPENDENCY-REGISTER.md")
    for i in range(1, 6):
        row = re.search(rf"^\| DEP-0{i} [^|]+\|(.*)\|$", text, flags=re.M)
        assert row, i
        cells = [c.strip() for c in row.group(1).split("|")]
        assert len(cells) == 7 and all(cells), (i, cells)
