"""Experience journal backward compatibility (B1).

A journal committed by the pre-Trendline writer (9016004) must recover under current
code without rewriting history: replay re-freezes each Experience snapshot from the
original recorded output and must reproduce the committed row exactly (EX1, ADR-020
Decision 13, ADR-021 Decision 12, ADR-028). The golden rows are genuine old-writer bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from nexora.artifacts import canonical_hash, decode
from nexora.experience import engine as experience_engine
from nexora.experience.engine import freeze
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.storage import SQLiteJournal

from tests.experience_compat_fixture import (
    Row,
    dump_rows,
    fixture_events,
    load_fixture,
    load_rows,
)
from tests.test_recovery_checkpoint import exact_state, runtime_config, start, state, store_for

SNAPSHOTS = "experience:v1:snapshots"
ADDED_FIELDS = ("trendline", "entry_readiness")


def old_rows() -> list[Row]:
    return load_fixture()


def research_stream(rows: list[Row]) -> str:
    return next(r[1] for r in rows if r[1].startswith("research:") and r[1].endswith(":config"))[
        : -len(":config")
    ]


def fixture_config(rows: list[Row]) -> RuntimeConfig:
    """The exact committed runtime config; its hash must still name the same stream."""
    stream = research_stream(rows)
    payload = next(json.loads(r[4]) for r in rows if r[1] == stream + ":config")
    config = decode(RuntimeConfig, payload)
    assert "research:" + canonical_hash(config) == stream
    assert canonical_hash(config) == canonical_hash(runtime_config())
    return config


def old_journal(tmp_path: Path, rows: list[Row] | None = None) -> Path:
    path = tmp_path / "old.sqlite"
    load_rows(path, old_rows() if rows is None else rows)
    return path


def recover(path: Path, config: RuntimeConfig, tmp_path: Path, *, checkpoints: bool) -> Any:
    journal = SQLiteJournal(path)
    try:
        runtime = start(config, journal, store_for(tmp_path) if checkpoints else None)
        return state(runtime), exact_state(runtime), runtime.last_recovery
    finally:
        journal.close()


def snapshot_contexts(rows: list[Row]) -> dict[str, dict[str, Any]]:
    return {r[2]: json.loads(json.loads(r[4])["context_json"]) for r in rows if r[1] == SNAPSHOTS}


def recorded_rows(rows: list[Row]) -> list[dict[str, Any]]:
    stream = research_stream(rows)
    return [json.loads(r[4]) for r in rows if r[1] == stream]


def test_fixture_is_the_pre_trendline_contract() -> None:
    rows = old_rows()
    contexts = snapshot_contexts(rows)
    assert len(contexts) == 16
    assert all(not set(ADDED_FIELDS) & set(context) for context in contexts.values())
    assert all(not set(ADDED_FIELDS) & set(row["output"]) for row in recorded_rows(rows))
    assert any(r[1].endswith(":outcomes") for r in rows)


@pytest.mark.parametrize("checkpoints", [False, True], ids=["checkpoints-off", "checkpoints-on"])
def test_old_writer_journal_recovers_without_touching_history(
    tmp_path: Path, checkpoints: bool
) -> None:
    """C1, C2, C5, C6, C10: recovery succeeds and every committed row stays byte-identical."""
    rows = old_rows()
    path = old_journal(tmp_path)
    _, _, recovery = recover(path, fixture_config(rows), tmp_path, checkpoints=checkpoints)
    assert recovery["mode"] == "full"
    assert recovery["replayed"] == 16
    assert dump_rows(path) == rows


def test_current_freeze_reproduces_every_committed_old_snapshot() -> None:
    """C2, C8: re-freezing the original recorded output yields the committed row exactly."""
    rows = old_rows()
    config, stream = fixture_config(rows), research_stream(rows)
    committed = {r[2]: r[3] for r in rows if r[1] == SNAPSHOTS}
    matched = 0
    for row in recorded_rows(rows):
        event = decode(NormalizedPriceEvent, row["event"])
        candidate = freeze(
            config,
            stream,
            event,
            row["output"],
            str(row.get("completeness", "unknown")),
            row.get("observation_metadata"),
        )
        assert not set(ADDED_FIELDS) & set(candidate.context())
        if candidate.experience_id in committed:
            assert canonical_hash(candidate) == committed[candidate.experience_id]
            matched += 1
    assert matched == len(committed)


def test_additive_fields_follow_presence_not_value() -> None:
    """Omission and explicit null keep their distinct canonical meaning."""
    rows = old_rows()
    config, stream = fixture_config(rows), research_stream(rows)
    row = recorded_rows(rows)[-1]
    event = decode(NormalizedPriceEvent, row["event"])

    def frozen(output: dict[str, Any]) -> dict[str, Any]:
        return freeze(config, stream, event, output, "complete", None).context()

    absent = frozen(row["output"])
    explicit = frozen({**row["output"], **dict.fromkeys(ADDED_FIELDS)})
    assert not set(ADDED_FIELDS) & set(absent)
    assert {field: explicit[field] for field in ADDED_FIELDS} == dict.fromkeys(ADDED_FIELDS)
    # Only the recorded output itself differs, and provenance hashes it.
    ignored = {*ADDED_FIELDS, "provenance"}
    assert {k: v for k, v in explicit.items() if k not in ignored} == {
        k: v for k, v in absent.items() if k not in ignored
    }


def test_current_writer_freezes_new_analytical_fields(tmp_path: Path) -> None:
    """C7: records written by current code carry Trendline and Entry Readiness."""
    journal = SQLiteJournal(tmp_path / "new.sqlite")
    try:
        runtime = start(runtime_config(), journal, None)
        for event in fixture_events():
            runtime.ingest(event, completeness="complete")
        output = runtime.engine.snapshot()
        experiences = runtime.experience.repository.all()
    finally:
        journal.close()
    assert experiences
    for experience in experiences:
        context = experience.context()
        assert all(isinstance(context[field], dict) for field in ADDED_FIELDS)
    latest = max(experiences, key=lambda e: e.t0).context()
    assert {field: latest[field] for field in ADDED_FIELDS} == {
        field: output[field] for field in ADDED_FIELDS
    }


@pytest.mark.parametrize("checkpoints", [False, True], ids=["checkpoints-off", "checkpoints-on"])
def test_current_writer_journal_replays_deterministically(
    tmp_path: Path, checkpoints: bool
) -> None:
    """C3, C4: new-format journals recover, and repeated replay is byte-identical."""
    path = tmp_path / "new.sqlite"
    journal = SQLiteJournal(path)
    try:
        runtime = start(runtime_config(), journal, None)
        for event in fixture_events():
            runtime.ingest(event, completeness="complete")
        live = state(runtime)
    finally:
        journal.close()
    committed = dump_rows(path)
    first = recover(path, runtime_config(), tmp_path / "a", checkpoints=checkpoints)
    second = recover(path, runtime_config(), tmp_path / "b", checkpoints=checkpoints)
    assert first[0] == live
    assert first[:2] == second[:2]
    assert dump_rows(path) == committed


@pytest.mark.parametrize("field", ADDED_FIELDS)
@pytest.mark.parametrize("value", [None, {"forged": True}], ids=["null", "object"])
def test_committed_snapshot_that_claims_a_new_field_still_conflicts(
    tmp_path: Path, field: str, value: Any
) -> None:
    """C9: absence and explicit null stay distinct; a forged historical field is a conflict."""
    rows = old_rows()
    target = next(i for i, r in enumerate(rows) if r[1] == SNAPSHOTS)
    sequence, stream, key, _, payload = rows[target]
    snapshot = json.loads(payload)
    context = json.loads(snapshot["context_json"])
    context[field] = value
    snapshot["context_json"] = json.dumps(context, sort_keys=True, separators=(",", ":"))
    tampered = list(rows)
    tampered[target] = (sequence, stream, key, canonical_hash(snapshot), json.dumps(snapshot))
    path = old_journal(tmp_path, tampered)
    with pytest.raises(ValueError, match="journal_identity_conflict"):
        recover(path, fixture_config(rows), tmp_path, checkpoints=False)
    assert dump_rows(path) == tampered


def test_changed_historical_content_still_conflicts(tmp_path: Path) -> None:
    """C9: any other difference from the recorded output keeps failing visibly."""
    rows = old_rows()
    target = next(i for i, r in enumerate(rows) if r[1] == SNAPSHOTS)
    sequence, stream, key, _, payload = rows[target]
    snapshot = json.loads(payload)
    context = json.loads(snapshot["context_json"])
    context["completeness"] = "partial"
    snapshot["context_json"] = json.dumps(context, sort_keys=True, separators=(",", ":"))
    tampered = list(rows)
    tampered[target] = (sequence, stream, key, canonical_hash(snapshot), json.dumps(snapshot))
    with pytest.raises(ValueError, match="journal_identity_conflict"):
        recover(old_journal(tmp_path, tampered), fixture_config(rows), tmp_path, checkpoints=False)


def test_future_analytical_fields_cannot_mutate_frozen_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C11: registering another additive field later leaves every frozen record untouched."""
    rows = old_rows()
    new_path = tmp_path / "new.sqlite"
    journal = SQLiteJournal(new_path)
    try:
        runtime = start(runtime_config(), journal, None)
        for event in fixture_events():
            runtime.ingest(event, completeness="complete")
    finally:
        journal.close()
    new_rows = dump_rows(new_path)
    monkeypatch.setattr(
        experience_engine,
        "ADDITIVE_OUTPUT_CONTEXT",
        (*experience_engine.ADDITIVE_OUTPUT_CONTEXT, "future_analysis"),
    )
    old_path = old_journal(tmp_path)
    recover(old_path, fixture_config(rows), tmp_path / "old", checkpoints=False)
    recover(new_path, runtime_config(), tmp_path / "new", checkpoints=False)
    assert dump_rows(old_path) == rows
    assert dump_rows(new_path) == new_rows


def test_mixed_journal_cold_replay_and_checkpoint_restore_converge(tmp_path: Path) -> None:
    """C12: old history + new live events; full replay equals checkpoint + delta, exactly."""
    rows = old_rows()
    config = fixture_config(rows)
    path = old_journal(tmp_path)
    store = store_for(tmp_path)
    journal = SQLiteJournal(path)
    try:
        runtime: ResearchRuntime = start(config, journal, store)
        for event in fixture_events(8, first=17)[:4]:
            runtime.ingest(event, completeness="complete")
        runtime.checkpoint()
        for event in fixture_events(8, first=17)[4:]:
            runtime.ingest(event, completeness="complete")
        live = state(runtime), exact_state(runtime)
    finally:
        journal.close()
    mixed = dump_rows(path)
    assert mixed[: len(rows)] == rows
    contexts = snapshot_contexts(mixed)
    old_ids = {r[2] for r in rows if r[1] == SNAPSHOTS}
    assert all(not set(ADDED_FIELDS) & set(contexts[i]) for i in old_ids)
    assert any(set(ADDED_FIELDS) <= set(c) for i, c in contexts.items() if i not in old_ids)

    cold_state, cold_exact, cold = recover(path, config, tmp_path / "cold", checkpoints=False)
    warm_state, warm_exact, warm = recover(path, config, tmp_path, checkpoints=True)
    assert cold["mode"] == "full" and cold["replayed"] == 24
    assert warm["mode"] == "checkpoint" and warm["replayed"] == 4
    assert cold_state == warm_state == live[0]
    assert cold_exact == warm_exact == live[1]
    assert dump_rows(path) == mixed
