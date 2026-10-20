"""V4 / A3: independent anchor custody. A matching hash is not trust: TRUSTED needs a verifiable
approval chain in an external append-only store, from an authorized custodian who is independent of
the extractor and operator. SYNTHETIC fixtures only; no signatures, no secrets
(see THREAT-MODEL-V4)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history import custody
from nexora.market_history.custody import (
    GENESIS,
    ChainHead,
    CustodianRegistry,
    EvidenceStore,
    LocalAppendOnlyStore,
    approve_dataset,
    build_approval_body,
    candidate_problem,
    evaluate_custody,
    head_of,
    load_registry,
    make_candidate,
    normalize_identity,
    revoke_dataset,
    seal_entry,
    verify_chain,
)
from nexora.market_history.extract import (
    LOCK_NAME,
    dataset_status,
    make_anchor,
    require_trusted,
    verify_dataset,
)
from nexora.market_history.models import HistoryError

from tests.test_market_history_corrective import go, reseal
from tests.test_market_history_v3 import NOW, seal

ROOT = Path(__file__).resolve().parent.parent
LATER = NOW + timedelta(days=1)
CHECK = NOW + timedelta(days=30)
REGISTRY_JSON: dict[str, Any] = {
    "schema": custody.REGISTRY_SCHEMA,
    "custodians": [
        {"id": "carol", "aliases": ["carol@corp.example"]},
        {
            "id": "dave",
            "valid_from": "2026-01-01T00:00:00+00:00",
            "valid_until": "2026-12-31T00:00:00+00:00",
        },
        {"id": "erin", "aliases": ["ann"]},  # erin's alias collides with the operator on purpose
    ],
}


@dataclass
class Env:
    root: Path
    ds: Path
    manifest: dict[str, Any]
    candidate: dict[str, Any]
    store: LocalAppendOnlyStore
    registry: CustodianRegistry


def make_env(tmp: Path, *, operator: str | None = "ann", creator: str | None = None) -> Env:
    ds = tmp / "ds"
    m = go(ds, operator=operator)
    cand = make_candidate(m, created_by=creator or operator or "extractor", created_at=NOW)
    return Env(
        tmp,
        ds,
        m,
        cand,
        LocalAppendOnlyStore(tmp / "store"),
        CustodianRegistry.from_json(REGISTRY_JSON),
    )


def approve(e: Env, who: str = "carol", at: datetime = LATER, **kw: Any) -> dict[str, Any]:
    return approve_dataset(
        e.ds,
        kw.pop("candidate", e.candidate),
        store=kw.pop("store", e.store),
        registry=kw.pop("registry", e.registry),
        approver=who,
        evidence_ref="ticket-1",
        approved_at=at,
    )


def status(e: Env, **kw: Any) -> Any:
    return dataset_status(
        e.ds,
        kw.pop("anchor", None),
        store=kw.pop("store", e.store),
        registry=kw.pop("registry", e.registry),
        now=CHECK,
        **kw,
    )


def raw_append(e: Env, **over: Any) -> dict[str, Any]:
    """An attacker/rogue writer with APPEND access appends whatever it likes (no tool checks)."""
    body = build_approval_body(
        e.manifest, e.candidate, approver="carol", evidence_ref="forged", approved_at=LATER
    )
    body.update(over)
    return e.store.append(body)


# ======================= the policy: extraction never trusts =======================
def test_trusted_requires_the_full_custody_chain(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    assert e.manifest["development_status"] == "UNTRUSTED"
    assert not status(e).trusted  # empty store
    entry = approve(e)
    s = status(e)
    assert s.status == "TRUSTED" and s.reasons == () and entry["seq"] == 1
    assert require_trusted(e.ds, None, store=e.store, registry=e.registry, now=CHECK).trusted


def test_candidate_alone_or_a_hash_only_anchor_never_trusts(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    legacy = make_anchor(e.manifest, recorded_by="carol", storage_ref="PR", recorded_at=LATER)
    for kwargs in ({}, {"anchor": legacy}, {"store": None}, {"registry": None}):
        s = status(e, **kwargs)
        assert not s.trusted
    assert "legacy_anchor_is_not_custody_evidence" in dataset_status(e.ds, legacy).reasons
    with pytest.raises(HistoryError, match="dataset_untrusted"):
        require_trusted(e.ds, legacy)
    with pytest.raises(HistoryError, match="dataset_untrusted"):
        require_trusted(e.ds, None, store=e.store, registry=None)


def test_fail_closed_when_custody_cannot_be_independently_verified(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)

    class Broken:
        location = None

        def entries(self) -> list[dict[str, Any]]:
            raise HistoryError("store_unreadable", "offline")

        def append(self, body: Any) -> dict[str, Any]:
            raise AssertionError

    s = dataset_status(e.ds, None, store=Broken(), registry=e.registry, now=CHECK)
    assert not s.trusted and "custody:store_unreadable:store_unreadable" in s.reasons

    class Explodes(Broken):
        def entries(self) -> list[dict[str, Any]]:
            raise RuntimeError("bug")

    s = dataset_status(e.ds, None, store=Explodes(), registry=e.registry, now=CHECK)
    assert not s.trusted and s.reasons[0].startswith("status_internal_error")


# ======================= authorization and self-approval =======================
def test_unauthorized_approver_is_refused_by_the_tool_and_rejected_by_the_verifier(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    with pytest.raises(HistoryError, match="approver_not_authorized"):
        approve(e, "mallory")
    assert not (tmp_path / "store").exists()  # a refused approval wrote nothing
    raw_append(e, approver="mallory")  # a rogue writer bypasses the tool
    s = status(e)
    assert not s.trusted
    assert "custody:approval_1_rejected:approver_not_authorized" in s.reasons


@pytest.mark.parametrize("variant", ["ann", "ANN", "  Ann  ", "ａｎｎ", "\u2003ann\u2003"])
def test_self_approval_by_the_operator_is_refused_in_every_spelling(
    tmp_path: Path, variant: str
) -> None:
    """The operator is also a registered custodian under various spellings of the same name."""
    reg = CustodianRegistry.from_json(
        {"schema": custody.REGISTRY_SCHEMA, "custodians": [{"id": variant}]}
    )
    e = make_env(tmp_path, operator="ann")
    assert normalize_identity(variant) == "ann"
    with pytest.raises(HistoryError, match="self_approval_by_operator"):
        approve(e, variant, registry=reg)
    raw_append(e, approver=variant)
    s = dataset_status(e.ds, None, store=e.store, registry=reg, now=CHECK)
    assert not s.trusted and any("self_approval_by_operator" in r for r in s.reasons)


def test_alias_of_the_approver_matching_the_operator_is_self_approval(tmp_path: Path) -> None:
    e = make_env(tmp_path, operator="ann")  # registry: erin has alias "ann"
    with pytest.raises(HistoryError, match="self_approval_by_operator"):
        approve(e, "erin")
    raw_append(e, approver="erin")
    assert any("self_approval_by_operator" in r for r in status(e).reasons)


def test_candidate_creator_cannot_approve_even_if_not_the_operator(tmp_path: Path) -> None:
    e = make_env(tmp_path, operator="ann", creator="carol")  # carol made the candidate
    with pytest.raises(HistoryError, match="self_approval_by_candidate_creator"):
        approve(e, "carol")
    approve(e, "dave")  # a different custodian can
    assert status(e).trusted


def test_a_dataset_without_a_recorded_operator_can_never_be_approved_or_trusted(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path, operator=None, creator="extractor")
    with pytest.raises(HistoryError, match="operator_not_recorded"):
        approve(e)
    raw_append(e)
    s = status(e)
    assert not s.trusted and "custody:operator_not_recorded" in s.reasons


def test_custodian_validity_window_is_enforced(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    with pytest.raises(HistoryError, match="approver_not_valid_at_approval_time"):
        approve(e, "dave", at=datetime(2027, 6, 1, tzinfo=UTC))
    approve(e, "dave", at=LATER)
    assert status(e).trusted


@pytest.mark.parametrize(
    ("at", "reason"),
    [
        (NOW - timedelta(days=1), "approved_before_dataset_retrieved"),
        (NOW + timedelta(days=400), "approved_in_the_future"),
    ],
)
def test_approval_time_must_follow_retrieval_and_not_be_in_the_future(
    tmp_path: Path, at: datetime, reason: str
) -> None:
    e = make_env(tmp_path)
    raw_append(e, approved_at=at.isoformat())
    s = status(e)
    assert not s.trusted and any(reason in r or "before_candidate" in r for r in s.reasons)


def test_approval_before_the_candidate_existed_is_rejected(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    late = make_candidate(e.manifest, created_by="ann", created_at=LATER + timedelta(days=2))
    e.candidate = late
    raw_append(e, approved_at=LATER.isoformat())
    assert any("approved_before_candidate_created" in r for r in status(e).reasons)


# ======================= forged, replayed, reused =======================
def test_editing_a_stored_entry_breaks_the_chain(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    raw_append(e, approver="mallory")  # appended but unauthorized
    p = tmp_path / "store" / "entry-00000001.json"
    forged = json.loads(p.read_text())
    forged["approver"] = "carol"  # attacker upgrades the approver in place
    p.chmod(0o644)
    p.write_text(json.dumps(forged))
    s = status(e)
    assert not s.trusted and "custody:chain_invalid:entry_1_hash" in s.reasons


def test_resealing_a_replaced_first_entry_breaks_the_next_link(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    approve(e, "dave")
    p = tmp_path / "store" / "entry-00000001.json"
    first = json.loads(p.read_text())
    first["evidence_ref"] = "rewritten history"
    first["entry_sha256"] = custody._hash_of(first, "entry_sha256")  # perfectly self-consistent
    p.chmod(0o644)
    p.write_text(json.dumps(first))
    assert "custody:chain_invalid:entry_2_prev" in status(e).reasons


def test_whole_chain_replacement_and_tail_truncation_need_a_pinned_head(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    revoke_dataset(
        e.ds,
        store=e.store,
        registry=e.registry,
        revoker="carol",
        evidence_ref="t-2",
        revoked_at=LATER,
    )
    pinned = head_of(e.store.entries())
    assert pinned and pinned.seq == 2 and not status(e).trusted  # revoked
    (tmp_path / "store" / "entry-00000002.json").unlink()  # attacker truncates the revocation
    # DOCUMENTED RESIDUAL RISK: without an independently kept head the rollback is invisible.
    assert status(e).trusted
    s = status(e, expected_head=pinned)  # with the pin it is detected, fail closed
    assert not s.trusted and "custody:chain_invalid:truncated_before_pinned_head" in s.reasons
    wrong = ChainHead(1, "0" * 64)
    assert "custody:chain_invalid:pinned_head_mismatch" in status(e, expected_head=wrong).reasons


def test_approval_for_one_dataset_cannot_be_replayed_for_another(tmp_path: Path) -> None:
    a = make_env(tmp_path / "a")
    approve(a)
    b_ds = tmp_path / "b" / "ds"
    from nexora.market_history.clock import ClockAttestation

    mb = go(b_ds, operator="ann", clock_attestation=ClockAttestation("other", "me", 0, 10**9))
    assert mb["dataset_id"] != a.manifest["dataset_id"]
    s = dataset_status(b_ds, None, store=a.store, registry=a.registry, now=CHECK)
    assert not s.trusted and "custody:no_valid_approval" in s.reasons


def test_approval_is_bound_to_the_exact_manifest_so_a_resealed_forgery_loses_trust(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    approve(e)
    assert status(e).trusted
    reseal(e.ds, lambda m: m["provenance"].update(operator="somebody-else"))  # same dataset_id
    s = status(e)
    assert not s.trusted
    assert "custody:binding_mismatch:manifest_sha256" in s.reasons
    # a forger who also changes identity fields gets a new dataset_id: no approval exists at all
    reseal(e.ds, lambda m: m.update(symbol="OTHER"))
    s = status(e)
    assert not s.trusted and "custody:no_valid_approval" in s.reasons


def test_chunk_tampering_after_approval_is_an_integrity_failure(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    name = e.manifest["chunks"][0]["file"]
    (e.ds / name).chmod(0o644)
    (e.ds / name).write_bytes((e.ds / name).read_bytes() + b"x")
    s = status(e)
    assert not s.trusted and any(r.startswith("integrity:") for r in s.reasons)


def test_a_candidate_for_another_dataset_cannot_be_smuggled_into_an_approval(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    foreign = make_candidate(
        {**e.manifest, "manifest_sha256": "a" * 64}, created_by="ann", created_at=NOW
    )
    with pytest.raises(HistoryError, match="candidate_binding_mismatch"):
        approve(e, candidate=foreign)
    raw_append(e, candidate=foreign, candidate_sha256=foreign["candidate_sha256"])
    assert any("candidate_binding_mismatch:manifest_sha256" in r for r in status(e).reasons)


def test_tampered_candidate_and_unbound_candidate_hash_are_rejected(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    bad = deepcopy(e.candidate)
    bad["created_by"] = "someone"  # self hash no longer matches
    with pytest.raises(HistoryError, match="candidate_malformed:candidate_sha256"):
        approve(e, candidate=bad)
    raw_append(e, candidate_sha256="f" * 64)
    assert any("candidate_hash_not_bound" in r for r in status(e).reasons)
    for mutate, field in (
        (lambda c: c.update(nonce="short"), "nonce"),
        (lambda c: c.update(schema="x"), "schema"),
        (lambda c: c.update(window_raw_msc=[1.5, 2]), "window_raw_msc"),
        (lambda c: c.update(created_by="  "), "created_by_or_created_at"),
    ):
        c = deepcopy(e.candidate)
        mutate(c)  # type: ignore[no-untyped-call]
        assert candidate_problem(c) == field
    assert candidate_problem("x") == "schema" and candidate_problem(None) == "schema"


def test_candidates_carry_a_fresh_nonce_and_bind_the_dataset(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    again = make_candidate(e.manifest, created_by="ann", created_at=NOW)
    assert (
        again["nonce"] != e.candidate["nonce"]
        and again["manifest_sha256"] == e.manifest["manifest_sha256"]
    )
    assert candidate_problem(again) == ""


def test_unapprovable_datasets_are_refused_at_the_door(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    name = e.manifest["chunks"][0]["file"]
    (e.ds / name).chmod(0o644)
    (e.ds / name).write_bytes(b"garbage\n")
    with pytest.raises(HistoryError, match="dataset_not_approvable"):
        approve(e)


# ======================= revocation =======================
def test_revocation_withdraws_trust_and_even_a_rogue_revocation_fails_closed(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    approve(e)
    assert status(e).trusted
    with pytest.raises(HistoryError, match="approver_not_authorized"):
        revoke_dataset(
            e.ds,
            store=e.store,
            registry=e.registry,
            revoker="mallory",
            evidence_ref="x",
            revoked_at=LATER,
        )
    raw_append(e, decision="revoke", candidate=None, candidate_sha256=None, approver="mallory")
    s = status(e)
    assert not s.trusted and any(r.startswith("custody:revoked_at_seq_2") for r in s.reasons)
    approve(e, "dave")  # a later approval supersedes the revocation
    assert status(e).trusted


# ======================= registry =======================
@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {"schema": "x", "custodians": [{"id": "a"}]},
        {"schema": custody.REGISTRY_SCHEMA, "custodians": []},
        {"schema": custody.REGISTRY_SCHEMA, "custodians": [{"id": ""}]},
        {"schema": custody.REGISTRY_SCHEMA, "custodians": [{"id": "a"}, {"id": "A "}]},
        {
            "schema": custody.REGISTRY_SCHEMA,
            "custodians": [{"id": "a", "aliases": ["B"]}, {"id": "b"}],
        },
        {"schema": custody.REGISTRY_SCHEMA, "custodians": [{"id": "a", "role": "root"}]},
        {"schema": custody.REGISTRY_SCHEMA, "custodians": [{"id": "a", "aliases": "x"}]},
        {"schema": custody.REGISTRY_SCHEMA, "custodians": [{"id": "a", "valid_from": "yesterday"}]},
        {
            "schema": custody.REGISTRY_SCHEMA,
            "custodians": [{"id": "a", "valid_from": "2026-01-01T00:00:00"}],
        },
        {"schema": custody.REGISTRY_SCHEMA, "custodians": [{"id": "a\x00b"}]},
    ],
)
def test_malformed_or_ambiguous_registries_are_refused(bad: Any) -> None:
    with pytest.raises(HistoryError, match="registry_malformed"):
        CustodianRegistry.from_json(bad)


def test_registry_must_live_outside_the_dataset_and_not_be_a_symlink(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    inside = e.ds / "registry.json"
    inside.write_text(json.dumps(REGISTRY_JSON))
    with pytest.raises(HistoryError, match="registry_inside_dataset"):
        load_registry(inside, dataset_dir=e.ds)
    ok = tmp_path / "registry.json"
    ok.write_text(json.dumps(REGISTRY_JSON))
    assert load_registry(ok, dataset_dir=e.ds).find("CAROL@corp.example") is not None
    link = tmp_path / "link.json"
    try:
        os.symlink(ok, link)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(HistoryError, match="registry_unreadable"):
        load_registry(link)
    assert load_registry(ok).find("mallory") is None and load_registry(ok).find(5) is None  # type: ignore[arg-type]


def test_a_store_inside_the_dataset_folder_is_refused(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    inside = LocalAppendOnlyStore(e.ds / "evidence")
    s = dataset_status(e.ds, None, store=inside, registry=e.registry, now=CHECK)
    assert not s.trusted and "custody:store_inside_dataset" in s.reasons
    with pytest.raises(HistoryError, match="store_inside_dataset"):
        approve(e, store=inside)


# ======================= the store =======================
def test_store_entries_are_read_only_hash_chained_and_never_overwritten(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    one, two = approve(e), approve(e, "dave")
    assert (one["seq"], two["seq"]) == (1, 2) and one["prev_sha256"] == GENESIS
    assert two["prev_sha256"] == one["entry_sha256"]
    assert verify_chain(e.store.entries()) == ""
    for p in (tmp_path / "store").iterdir():
        assert not p.stat().st_mode & 0o222
    from nexora.market_history.extract import write_once

    with pytest.raises(FileExistsError):  # replacing an existing sequence number is refused
        write_once(tmp_path / "store" / "entry-00000001.json", b"{}")


def test_store_rejects_stray_files_gaps_and_links(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    (tmp_path / "store" / "notes.txt").write_text("x")
    with pytest.raises(HistoryError, match="store_unexpected_entry"):
        e.store.entries()
    assert "custody:store_unreadable:store_unexpected_entry" in status(e).reasons
    (tmp_path / "store" / "notes.txt").unlink()
    os.link(tmp_path / "store" / "entry-00000001.json", tmp_path / "alias")
    with pytest.raises(HistoryError, match="store_unexpected_entry"):
        e.store.entries()
    os.unlink(tmp_path / "alias")
    os.rename(
        tmp_path / "store" / "entry-00000001.json", tmp_path / "store" / "entry-00000003.json"
    )
    with pytest.raises(HistoryError, match="store_unexpected_entry"):
        e.store.entries()


def test_symlinked_store_directory_is_refused(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    approve(e)
    os.symlink(tmp_path / "store", tmp_path / "store-link")
    with pytest.raises(HistoryError, match="store_unreadable"):
        LocalAppendOnlyStore(tmp_path / "store-link").entries()


def test_concurrent_appenders_get_distinct_sequence_numbers(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    results: list[int] = []
    guard = threading.Lock()

    def worker() -> None:
        entry = approve(e, "carol")
        with guard:
            results.append(entry["seq"])

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(results) == [1, 2, 3, 4, 5]
    assert verify_chain(e.store.entries()) == "" and status(e).trusted


def test_the_store_interface_is_provider_neutral(tmp_path: Path) -> None:
    """Any object with append()/entries() works: no filesystem, no cloud SDK."""

    class MemoryStore:
        def __init__(self) -> None:
            self.log: list[dict[str, Any]] = []

        def append(self, body: Any) -> dict[str, Any]:
            head = head_of(self.log)
            entry = seal_entry(body, len(self.log) + 1, head.entry_sha256 if head else GENESIS)
            self.log.append(entry)
            return entry

        def entries(self) -> list[dict[str, Any]]:
            return deepcopy(self.log)

    mem = MemoryStore()
    assert isinstance(mem, EvidenceStore)
    e = make_env(tmp_path)
    approve(e, store=mem)
    assert dataset_status(e.ds, None, store=mem, registry=e.registry, now=CHECK).trusted


@pytest.mark.parametrize(
    "bad_entries",
    [
        None,
        "x",
        [None],
        [{}],
        [{"schema": custody.APPROVAL_SCHEMA, "seq": 2}],
        [{"schema": custody.APPROVAL_SCHEMA, "seq": True, "prev_sha256": GENESIS}],
    ],
)
def test_malformed_chains_never_verify(bad_entries: Any) -> None:
    assert verify_chain(bad_entries) != ""


def test_identity_normalisation() -> None:
    assert normalize_identity("  Ｃarol   Smith ") == "carol smith"
    for bad in ("", "  ", "a\x00", "a\u200bb", "\x07", None, 5, "x" * 300):
        with pytest.raises(HistoryError, match="invalid_identity"):
            normalize_identity(bad)


# ======================= V3 compatibility / migration =======================
def downgrade_to_v3(ds: Path) -> None:
    m = json.loads((ds / "manifest.json").read_text())
    m["schema"] = "nexora.market-history/3"
    m.pop("filesystem_capabilities")
    m["provenance"].pop("operator")
    seal(ds, m, identity=True)


def test_v3_datasets_still_verify_but_can_never_be_trusted(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    downgrade_to_v3(e.ds)
    assert verify_dataset(e.ds) == []  # integrity still checks out
    m = json.loads((e.ds / "manifest.json").read_text())
    legacy_anchor = make_anchor(m, recorded_by="carol", storage_ref="PR #92", recorded_at=LATER)
    s = dataset_status(e.ds, legacy_anchor)
    assert not s.trusted
    assert "legacy_anchor_is_not_custody_evidence" in s.reasons
    assert "legacy_schema_cannot_be_trusted:nexora.market-history/3" in s.reasons
    # even a complete forged approval chain cannot lift a V3 dataset: migration = re-extract
    forged = build_approval_body(
        m, e.candidate, approver="carol", evidence_ref="x", approved_at=LATER
    )
    forged.update({k: m[k] for k in ("dataset_id", "content_sha256", "manifest_sha256")})
    e.store.append(forged)
    s = status(e)
    assert not s.trusted and "capability:filesystem_capabilities_absent" in s.reasons
    assert "custody:operator_not_recorded" in s.reasons


def test_a_v4_manifest_missing_v4_fields_is_malformed_not_legacy(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    m = json.loads((e.ds / "manifest.json").read_text())
    m.pop("filesystem_capabilities")
    seal(e.ds, m, identity=False)
    assert "manifest_malformed:missing_filesystem_capabilities" in verify_dataset(e.ds)


def test_capability_record_in_the_manifest_gates_trust_even_with_valid_custody(
    tmp_path: Path,
) -> None:
    e = make_env(tmp_path)
    # extractor ran on an unproven configuration; honest manifest says so; custody can't override
    reseal(
        e.ds, lambda m: m["filesystem_capabilities"].update(fs_class="network", platform="win32")
    )
    m = json.loads((e.ds / "manifest.json").read_text())
    e.manifest = m
    e.candidate = make_candidate(m, created_by="ann", created_at=NOW)
    raw_append(e)
    s = status(e)
    assert not s.trusted
    assert "capability:platform_unproven:win32" in s.reasons
    assert "capability:filesystem_unproven:network" in s.reasons


def test_hard_defects_still_block_trust_with_valid_custody(tmp_path: Path) -> None:
    from decimal import Decimal

    from nexora.market_history.models import RawTick

    from tests.test_market_history import FakeSource

    ds = tmp_path / "ds"
    crossed = [RawTick(1000 + i * 100, Decimal("101"), Decimal("100")) for i in range(5)]
    m = go(ds, FakeSource(crossed), operator="ann")
    e = Env(
        tmp_path,
        ds,
        m,
        make_candidate(m, created_by="ann", created_at=NOW),
        LocalAppendOnlyStore(tmp_path / "store"),
        CustodianRegistry.from_json(REGISTRY_JSON),
    )
    with pytest.raises(HistoryError, match="dataset_not_approvable"):
        approve(e)
    raw_append(e)
    assert any(r.startswith("hard_defect:") for r in status(e).reasons)


def test_evaluate_custody_is_pure_and_reports_every_rejected_approval(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    raw_append(e, approver="mallory")
    raw_append(e, evidence_ref=" ")
    v = evaluate_custody(e.manifest, e.store.entries(), e.registry, now=CHECK)
    assert not v.approved and v.head is not None and v.head.seq == 2
    assert "custody:approval_1_rejected:approver_not_authorized" in v.reasons
    assert "custody:approval_2_rejected:evidence_ref_missing" in v.reasons
    good = approve(e)
    v = evaluate_custody(e.manifest, e.store.entries(), e.registry, now=CHECK)
    assert v.approved and v.approval_seq == good["seq"]


# ======================= CLIs =======================
def run_cli(script: str, *args: str) -> tuple[int, dict[str, Any]]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "packages"), str(ROOT)])}
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        check=False,
    )
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_cli_custody_and_verify_round_trip(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    (tmp_path / "registry.json").write_text(json.dumps(REGISTRY_JSON))
    (tmp_path / "candidate.json").write_text(json.dumps(e.candidate))
    base = ["--dataset", str(e.ds), "--store", str(tmp_path / "store")]
    reg = ["--registry", str(tmp_path / "registry.json")]
    code, out = run_cli(
        "custody_market_history.py",
        "approve",
        *base,
        *reg,
        "--custodian",
        "ann",
        "--evidence-ref",
        "t",
        "--candidate",
        str(tmp_path / "candidate.json"),
    )
    assert code == 2 and out["refused"] == "approval_refused"  # self-approval / not a custodian
    code, out = run_cli(
        "custody_market_history.py",
        "approve",
        *base,
        *reg,
        "--custodian",
        "carol",
        "--evidence-ref",
        "t",
        "--candidate",
        str(tmp_path / "candidate.json"),
    )
    assert code == 0 and out["seq"] == 1
    code, out = run_cli("custody_market_history.py", "head", "--store", str(tmp_path / "store"))
    assert code == 0 and out["chain"] == "ok"
    head = out["head"]
    vargs = ["--dataset", str(e.ds), "--store", str(tmp_path / "store"), *reg]
    code, out = run_cli("verify_market_history.py", *vargs, "--require-trusted")
    assert (code, out["status"], out["status_reasons"]) == (0, "TRUSTED", [])
    code, out = run_cli(
        "verify_market_history.py", *vargs, "--store-head", head, "--require-trusted"
    )
    assert code == 0
    code, out = run_cli("verify_market_history.py", *vargs, "--store-head", f"1:{'0' * 64}")
    assert code == 1 and "custody:chain_invalid:pinned_head_mismatch" in out["status_reasons"]
    code, out = run_cli("verify_market_history.py", *vargs, "--store-head", "garbage")
    assert code == 2
    code, out = run_cli(
        "custody_market_history.py",
        "revoke",
        *base,
        *reg,
        "--custodian",
        "carol",
        "--evidence-ref",
        "t2",
    )
    assert code == 0
    code, out = run_cli("verify_market_history.py", *vargs, "--require-trusted")
    assert code == 1 and any("revoked" in r for r in out["status_reasons"])


def test_cli_registry_inside_dataset_is_refused(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    (e.ds / "registry.json").write_text(json.dumps(REGISTRY_JSON))
    code, out = run_cli(
        "verify_market_history.py",
        "--dataset",
        str(e.ds),
        "--store",
        str(tmp_path / "store"),
        "--registry",
        str(e.ds / "registry.json"),
    )
    assert code == 2 and out["status_reasons"] == ["registry_inside_dataset"]


def test_extract_cli_candidate_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    from tests.test_market_history_corrective import _cli_files

    spec = importlib.util.spec_from_file_location(
        "x", ROOT / "scripts" / "extract_market_history.py"
    )
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    class Boom:
        def __init__(self) -> None:
            raise AssertionError("terminal attached before validation")

    monkeypatch.setattr(cli, "Mt5ReadOnlyHistorySource", Boom)
    argv = _cli_files(tmp_path)
    monkeypatch.setattr(sys, "argv", [*argv, "--candidate-out", str(tmp_path / "c.json")])
    with pytest.raises(SystemExit):  # a candidate needs the operator identity
        cli.main()
    monkeypatch.setattr(
        sys,
        "argv",
        [*argv, "--operator", "ann", "--candidate-out", str(tmp_path / "out" / "c.json")],
    )
    with pytest.raises(HistoryError, match="candidate_inside_dataset"):
        cli.main()
    (tmp_path / "c.json").write_text("{}")
    monkeypatch.setattr(
        sys, "argv", [*argv, "--operator", "ann", "--candidate-out", str(tmp_path / "c.json")]
    )
    with pytest.raises(HistoryError, match="candidate_file_exists"):
        cli.main()


def test_lock_name_is_not_a_trust_input(tmp_path: Path) -> None:
    """Sanity: a leftover lock in an approved dataset is an integrity problem, not trust."""
    e = make_env(tmp_path)
    approve(e)
    (e.ds / LOCK_NAME).write_text("{}")
    s = status(e)
    assert not s.trusted and f"integrity:writer_lock_present:{LOCK_NAME}" in s.reasons
