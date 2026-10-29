"""The ONE place that names the PR #96 revision this package is checked against.

`docs/research/market-history-windows-readiness/dependency-manifest.json` holds the pinned #96
CANDIDATE head and the contract read from it. Everything else (custody gate, execution contract,
evidence validator, docs tests) takes the revision from here, so a re-pin changes one value.

A candidate head is NOT a gate-approved baseline: `gate_approved_baseline_sha` stays null until an
independent review approves #96's custody design (DEP-01), and `head_is_gate_approved` is then the
only function that may say so. Head matching is exact: a full 40-character lowercase hex SHA equal
to the pin. Abbreviations, uppercase, whitespace, a stale head or any other head never match.

Fail closed: an unreadable or inconsistent manifest raises ManifestError at import time. Pure
Python; the only I/O is reading that one JSON file next to the docs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = "nexora.windows-readiness-dependency-manifest/1"
MANIFEST_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "research"
    / "market-history-windows-readiness"
    / "dependency-manifest.json"
)
_SHA40 = re.compile(r"[0-9a-f]{40}")
_PREFIX = re.compile(r"[0-9a-fA-F]{4,39}")

# classify_head results
PINNED = "PINNED_CANDIDATE"
STALE = "STALE"
ABBREVIATED = "ABBREVIATED"
MALFORMED = "MALFORMED"
MISMATCH = "NOT_THE_PINNED_CANDIDATE"


class ManifestError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"dependency_manifest_unreadable:{type(exc).__name__}") from None
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA:
        raise ManifestError("dependency_manifest_schema")
    cand = data.get("pr96_candidate")
    if not isinstance(cand, dict):
        raise ManifestError("dependency_manifest_candidate_missing")
    sha = cand.get("head_sha")
    if type(sha) is not str or not _SHA40.fullmatch(sha):
        raise ManifestError("dependency_manifest_candidate_not_full_sha")
    if cand.get("candidate_is_not_gate_approved_baseline") is not True:
        raise ManifestError("dependency_manifest_must_state_candidate_is_not_approved_baseline")
    approved = cand.get("gate_approved_baseline_sha")
    if approved is not None and (type(approved) is not str or not _SHA40.fullmatch(approved)):
        raise ManifestError("dependency_manifest_approved_baseline_malformed")
    stale = data.get("stale_references")
    if not isinstance(stale, list):
        raise ManifestError("dependency_manifest_stale_references_missing")
    stale_shas = []
    for item in stale:
        s = item.get("sha") if isinstance(item, dict) else None
        if type(s) is not str or not _SHA40.fullmatch(s):
            raise ManifestError("dependency_manifest_stale_reference_not_full_sha")
        stale_shas.append(s)
    if sha in stale_shas or len(set(stale_shas)) != len(stale_shas):
        raise ManifestError("dependency_manifest_candidate_listed_as_stale")
    if not isinstance(data.get("contract_at_candidate"), dict):
        raise ManifestError("dependency_manifest_contract_missing")
    return data


MANIFEST: dict[str, Any] = _load(MANIFEST_PATH)
CANDIDATE_SHA: str = MANIFEST["pr96_candidate"]["head_sha"]
GATE_APPROVED_BASELINE_SHA: str | None = MANIFEST["pr96_candidate"]["gate_approved_baseline_sha"]
CONTRACT: dict[str, Any] = MANIFEST["contract_at_candidate"]
STALE_SHAS: frozenset[str] = frozenset(i["sha"] for i in MANIFEST["stale_references"])


def classify_head(sha: object) -> str:
    """Exact-match classification of a claimed #96 head."""
    if type(sha) is not str:
        return MALFORMED
    if sha in STALE_SHAS:
        return STALE
    if _SHA40.fullmatch(sha):
        return PINNED if sha == CANDIDATE_SHA else MISMATCH
    if _PREFIX.fullmatch(sha):
        return ABBREVIATED
    return MALFORMED


def head_matches_candidate(sha: object) -> bool:
    return classify_head(sha) == PINNED


def head_is_gate_approved(sha: object) -> bool:
    """True only for the head an independent review approved. None exists: always False now."""
    return (
        GATE_APPROVED_BASELINE_SHA is not None
        and type(sha) is str
        and sha == GATE_APPROVED_BASELINE_SHA
        and bool(_SHA40.fullmatch(sha))
    )
