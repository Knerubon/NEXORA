"""Backup a real local journal and verify recovery in a fresh Python process.

Without arguments, run an isolated synthetic paper-session recovery rehearsal.
This does not certify PostgreSQL recovery or remote deployment.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from nexora.artifacts import canonical_hash, decode
from nexora.paper.session import PaperSession, PaperSessionConfig
from nexora.risk import proposal_fixture, risk_policy_fixture
from nexora.storage import SQLiteJournal


def recovered_hash(path: Path, namespace: str) -> str:
    journal = SQLiteJournal(path)
    try:
        rows = journal.read(f"paper:{namespace}:config")
        if len(rows) != 1:
            raise ValueError("paper_config_missing")
        config = decode(PaperSessionConfig, rows[0])
        session = PaperSession(config, journal)
        return canonical_hash(session.snapshot())
    finally:
        journal.close()


def drill(source: Path, destination: Path, namespace: str) -> None:
    before = recovered_hash(source, namespace)
    journal = SQLiteJournal(source)
    try:
        journal.backup(destination)
    finally:
        journal.close()
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--verify",
            str(destination),
            "--namespace",
            namespace,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    after = json.loads(result.stdout)["state_hash"]
    if before != after:
        raise RuntimeError("restore_state_mismatch")
    print(f"PASS: SQLite backup and fresh-process paper/risk recovery {after}")
    print("NOT VERIFIED: PostgreSQL backup/restore, host crash, RPO/RTO, remote access")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--namespace", default="paper-recovery")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps({"state_hash": recovered_hash(args.verify, args.namespace)}))
        return
    if args.source:
        if not args.source.is_file() or args.backup is None:
            parser.error("existing --source and a new --backup path are required")
        drill(args.source, args.backup, args.namespace)
        return
    with tempfile.TemporaryDirectory(prefix="nexora-recovery-") as temporary:
        source, backup = Path(temporary) / "source.sqlite", Path(temporary) / "backup.sqlite"
        journal = SQLiteJournal(source)
        config = PaperSessionConfig(
            args.namespace,
            "paper-account",
            Decimal("10000"),
            Decimal("0.05"),
            Decimal("0.1"),
            risk_policy_fixture(),
            Decimal("1"),
            Decimal("1"),
        )
        session = PaperSession(config, journal)
        session.submit(proposal_fixture().signal, price=Decimal("100"), quality="complete")
        journal.close()
        drill(source, backup, args.namespace)


if __name__ == "__main__":
    main()
