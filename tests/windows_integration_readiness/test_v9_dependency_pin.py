"""Wave V9: one machine-readable pin for the PR #96 candidate, exact matching, no stale references.

The candidate is NOT the gate-approved baseline. These tests make a re-pin a one-value change:
they derive every expectation from the manifest, never from a second copy of the SHA, except the
literal assertion that documents the CURRENT pin (updated together with the manifest).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.windows_integration_readiness import custody_gate as cg
from tests.windows_integration_readiness import dependency_pin as dp
from tests.windows_integration_readiness import execution_contract as ec

HERE = Path(__file__).resolve().parent
DOCS = dp.MANIFEST_PATH.parent
RAW: dict[str, Any] = json.loads(dp.MANIFEST_PATH.read_text(encoding="utf-8"))
SHA = dp.CANDIDATE_SHA


def test_the_current_pin_and_its_status() -> None:
    assert SHA == "d2e8ee9ef4f6eb438dc79c5e04ad43ed46de764d"  # update with the manifest on re-pin
    assert re.fullmatch(r"[0-9a-f]{40}", SHA)
    cand = RAW["pr96_candidate"]
    assert cand["status"] == "CANDIDATE_NOT_APPROVED"
    assert cand["candidate_is_not_gate_approved_baseline"] is True
    assert cand["gate_approved_baseline_sha"] is None
    assert not dp.head_is_gate_approved(SHA)  # a candidate is never an approved baseline


def test_provenance_fields_are_present() -> None:
    prov = RAW["pr96_candidate"]["provenance"]
    for key in ("recorded_for_branch", "method", "recorded_in_wave", "pending_update"):
        assert isinstance(prov[key], str) and prov[key].strip()
    assert prov["independent_review_of_this_pin"] is False
    assert RAW["schema"] == dp.MANIFEST_SCHEMA


def test_every_module_takes_the_pin_from_the_manifest() -> None:
    assert cg.V4_REF_SHA == ec.CONTRACT_REF_SHA == SHA
    assert cg.V4_EXTRACTOR_VERSION == ec.EXTRACTOR_VERSION == dp.CONTRACT["extractor_version"]
    assert cg.V4_MANIFEST_SCHEMA == ec.MANIFEST_SCHEMA == dp.CONTRACT["manifest_schema"]
    assert set(ec.EXTRACT_REQUIRED) == set(dp.CONTRACT["extract_required"])
    assert set(cg.V4_VERIFY_FLAGS) == set(dp.CONTRACT["verify_flags"])
    assert cg.REQUIRED_VERIFY_FLAGS <= cg.V4_VERIFY_FLAGS
    assert dp.CONTRACT["verify_script"] == cg.V4_VERIFY_SCRIPT
    # no module keeps its own copy of the SHA literal
    for name in ("custody_gate.py", "execution_contract.py", "evidence_validator.py"):
        text = (HERE / name).read_text(encoding="utf-8")
        assert not re.search(r"\b[0-9a-f]{40}\b", text), name


def test_exact_full_sha_matching() -> None:
    assert dp.classify_head(SHA) == dp.PINNED and dp.head_matches_candidate(SHA)
    assert dp.classify_head(SHA[:7]) == dp.ABBREVIATED
    assert dp.classify_head(SHA[:39]) == dp.ABBREVIATED
    assert dp.classify_head(SHA[:4]) == dp.ABBREVIATED
    for bad in (SHA.upper(), f" {SHA}", f"{SHA}\n", SHA + "0", "", "g" * 40, "abc", None, 5, b"x"):
        assert dp.classify_head(bad) == dp.MALFORMED, bad
        assert not dp.head_matches_candidate(bad)
    other = ("0" if SHA[0] != "0" else "1") + SHA[1:]
    assert dp.classify_head(other) == dp.MISMATCH  # one character off: an unapproved head
    for stale in dp.STALE_SHAS:
        assert dp.classify_head(stale) == dp.STALE and not dp.head_matches_candidate(stale)


def test_manifest_loader_fails_closed(tmp_path: Path) -> None:
    def load(mutate: Any) -> None:
        doc = copy.deepcopy(RAW)
        mutate(doc)
        p = tmp_path / "m.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        dp._load(p)

    cases = {
        "schema": lambda d: d.update(schema="x"),
        "abbreviated": lambda d: d["pr96_candidate"].update(head_sha=SHA[:7]),
        "uppercase": lambda d: d["pr96_candidate"].update(head_sha=SHA.upper()),
        "approved_flag": lambda d: d["pr96_candidate"].update(
            candidate_is_not_gate_approved_baseline=False
        ),
        "approved_baseline_malformed": lambda d: d["pr96_candidate"].update(
            gate_approved_baseline_sha="abc"
        ),
        "candidate_is_stale": lambda d: d["stale_references"].append({"sha": SHA}),
        "stale_abbreviated": lambda d: d["stale_references"].append({"sha": "abc1234"}),
        "no_contract": lambda d: d.pop("contract_at_candidate"),
        "no_candidate": lambda d: d.pop("pr96_candidate"),
    }
    for name, mutate in cases.items():
        with pytest.raises(dp.ManifestError):
            load(mutate)
        assert name
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(dp.ManifestError):
        dp._load(tmp_path / "bad.json")
    with pytest.raises(dp.ManifestError):
        dp._load(tmp_path / "missing.json")
    load(lambda d: None)  # the unmodified manifest loads


def test_a_stale_head_is_listed_with_what_broke() -> None:
    stale = {i["sha"]: i for i in RAW["stale_references"]}
    old = stale["d6a7cda72a88600e2ccf6554605f1c50478893ea"]
    assert old["status"] == "STALE_OBSOLETE"
    text = " ".join(old["incompatible_with_candidate"])
    for needle in ("--operator-key", "/6", "--pin", "--max-pin-age-seconds", "--signing-key"):
        assert needle in text, needle


# ---- stale references must not survive anywhere else -----
def _scan_files() -> list[Path]:
    out = [
        p
        for p in DOCS.iterdir()
        if p.suffix in (".md", ".json") and p.name != dp.MANIFEST_PATH.name
    ]
    out += [
        p
        for p in HERE.rglob("*")
        if p.is_file() and p.suffix in (".py", ".json", ".md") and "__pycache__" not in p.parts
    ]
    return out


def test_no_stale_pr96_reference_outside_the_manifest_and_the_historical_section() -> None:
    allowed = {
        "V4-CUSTODY-COMPATIBILITY.md",
        "test_v9_dependency_pin.py",
        "test_v9_custody_signed_pin.py",
        "test_v9_repin_exit_codes.py",
    }
    offenders: list[str] = []
    for path in _scan_files():
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for sha in dp.STALE_SHAS:
            for n in (7, 40):
                if re.search(rf"(?<![0-9a-f]){re.escape(sha[:n])}", text):
                    offenders.append(f"{path.name}:{sha[:n]}")
    assert offenders == [], offenders


def test_the_historical_section_labels_every_stale_reference_as_stale() -> None:
    text = (DOCS / "V4-CUSTODY-COMPATIBILITY.md").read_text(encoding="utf-8")
    for sha in dp.STALE_SHAS:
        assert sha[:7] in text
        for m in re.finditer(re.escape(sha[:7]), text):
            window = text[max(0, m.start() - 400) : m.end() + 400].lower()
            assert "stale" in window or "obsolete" in window or "superseded" in window, sha[:7]


def test_docs_state_the_candidate_is_not_the_approved_baseline() -> None:
    readme = (DOCS / "README.md").read_text(encoding="utf-8")
    assert "dependency-manifest.json" in readme
    assert "candidate" in readme.lower() and "not" in readme.lower()
    register = (DOCS / "DEPENDENCY-REGISTER.md").read_text(encoding="utf-8")
    assert SHA[:7] in register or "dependency-manifest.json" in register
    assert "not a gate-approved baseline" in register.lower() or "not the gate-approved" in (
        register.lower()
    )


def test_plan_invocation_rejects_unpinned_heads_for_a_real_run(tmp_path: Path) -> None:
    from tests.windows_integration_readiness.test_execution_contract import (
        SYMBOL,
        argv,
        custody_args,
        plan,
    )

    out = tmp_path / "out"
    args = argv(out, **custody_args(tmp_path))
    for head, kind in (
        (SHA[:7], "ABBREVIATED"),
        ("f" * 40, "NOT_THE_PINNED_CANDIDATE"),
        ("d6a7cda72a88600e2ccf6554605f1c50478893ea", "STALE"),
        (SHA.upper(), "MALFORMED"),
    ):
        with pytest.raises(ec.ContractError) as e:
            plan(
                out, args, provider="mt5", offline=False, extractor_sha=head,
                expected_extractor_sha=head, preregistered_symbol=SYMBOL,
            )  # fmt: skip
        assert e.value.code == f"extractor_sha_not_pinned_pr96_candidate:{kind}"
    ok = plan(out, args, provider="mt5", offline=False)
    assert ok.provider == "mt5"
