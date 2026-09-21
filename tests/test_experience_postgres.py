"""Real PostgreSQL journal/migration/restart acceptance in the isolated CI database."""

import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from nexora.experience import ExperienceRepository
from nexora.research.runtime import ResearchRuntime, RuntimeConfig
from nexora.storage import PostgresJournal

from tests.test_experience import event
from tests.test_readiness_regressions import pipeline_config


def test_postgres_experience_migration_restart_and_pending_horizons() -> None:
    conninfo = os.environ.get("NEXORA_TEST_POSTGRES_DSN")
    if not conninfo:
        pytest.skip("isolated PostgreSQL test service not configured")
    writer = PostgresJournal(conninfo)
    config = RuntimeConfig(
        replace(pipeline_config(), version=f"experience-test-{uuid4()}"), "USD/oz"
    )
    runtime = ResearchRuntime(config, writer)
    scope = ""
    ids: list[str] = []
    try:
        migration = Path("infra/migrations/007_research_journal.sql").read_text()
        writer.connection.execute(migration)
        writer.connection.execute(migration)  # Existing schema/data preserved on reapplication.
        runtime.ingest(event(0))
        own = [
            e
            for e in ExperienceRepository(writer).all()
            if e.context()["provenance"]["runtime_stream"] == runtime.stream
        ]
        assert len(own) == 1
        experience = own[0]
        scope, ids = experience.scope, [experience.experience_id]
        reader = PostgresJournal(conninfo)
        try:
            recovered = ResearchRuntime(config, reader)
            assert ExperienceRepository(reader).get(experience.experience_id) == experience
            recovered.ingest(event(60))
            outcomes = ExperienceRepository(reader).outcomes(experience.experience_id)
            assert len(outcomes) == 4
            again = ResearchRuntime(config, reader)
            again.ingest(event(60))
            assert ExperienceRepository(reader).outcomes(experience.experience_id) == outcomes
            assert ExperienceRepository(reader).get(experience.experience_id) == experience
            with pytest.raises(ValueError, match="identity_conflict"):
                ExperienceRepository(reader).save(replace(experience, context_json="{}"))
        finally:
            reader.close()
    finally:
        # Remove only uniquely identified synthetic rows owned by this test.
        writer.connection.execute(
            "DELETE FROM research_journal WHERE stream IN (%s,%s,%s)",
            (runtime.stream, runtime.stream + ":config", f"experience:v1:{scope}:observations"),
        )
        for eid in ids:
            writer.connection.execute(
                "DELETE FROM research_journal WHERE stream IN (%s,%s) "
                "OR (stream=%s AND event_key=%s)",
                (
                    f"experience:v1:{eid}:outcomes",
                    f"experience:v1:{eid}:lifecycle",
                    "experience:v1:snapshots",
                    eid,
                ),
            )
        writer.close()
