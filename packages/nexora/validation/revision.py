"""Source revision probe for the (future) CLI shell; never called by the pure core.

Q-V7: a dirty or unidentifiable tree is development-only evidence. Any probe failure
yields an unknown revision, which can never qualify.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from nexora.validation.models import SourceRevision


def revision_from_git_output(head: str, porcelain: str) -> SourceRevision:
    commit = head.strip().lower()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        return SourceRevision(commit=None, dirty=None)
    return SourceRevision(commit=commit, dirty=bool(porcelain.strip()))


def read_source_revision(repository: Path) -> SourceRevision:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return SourceRevision(commit=None, dirty=None)
    return revision_from_git_output(head, status)
