"""Wave V9 / N-05: characterise the "create-then-write" window of the writer lock.

Between `open(O_CREAT)` and the record write, `.writer.lock` exists and is EMPTY. In the V3 scheme
that empty file was ambiguous (live owner about to write, or dead owner) and wedged or was reaped.
In V4 ownership is the OS lock, not the file content, so the window is not an exclusion gap:

  * a live owner holds the directory lock and the record lock BEFORE the file is written, so a
    second acquirer is refused (`writer_lock_held`) even though the file is empty;
  * an empty file with no holder is free by OS state (crash recovery, `test_crash_between_...`);
  * the only way to get two writers is one that does not take the OS lock at all (a V3 writer),
    which is a documented process control (THREAT-MODEL-V4 row L10), not something code can fix.

A grace period ("refuse an empty file younger than N seconds") would REINTRODUCE the V3 wedge and
needs a clock policy; it is deliberately NOT added. Synthetic temp dirs, POSIX flock (Linux).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history import lock as lockmod
from nexora.market_history.fsutil import Dir
from nexora.market_history.lock import LOCK_NAME, _acquire_record, acquire
from nexora.market_history.models import HistoryError

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock tests (Linux)")


def _in_window(monkeypatch: pytest.MonkeyPatch, tmp: Path, probe: Any) -> list[Any]:
    """Run `probe()` at the exact moment A holds the locks but has not yet written its record."""
    seen: list[Any] = []

    def hook(stage: str) -> None:
        if stage == "after_flock" and not seen:
            seen.append((tmp / LOCK_NAME).read_bytes())  # the empty file the window exposes
            seen.append(probe())

    monkeypatch.setattr(lockmod, "_hook", hook)
    return seen


def _attempt(tmp: Path, *, via_record_only: bool) -> str:
    d = Dir.open(tmp)
    try:
        lock = _acquire_record(d) if via_record_only else acquire(d)
    except HistoryError as exc:
        d.close()
        return exc.code
    lock.release()
    d.close()
    return "ACQUIRED"


@pytest.mark.parametrize("via_record_only", [False, True])
def test_second_acquirer_inside_the_window_is_refused_although_the_file_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, via_record_only: bool
) -> None:
    seen = _in_window(
        monkeypatch, tmp_path, lambda: _attempt(tmp_path, via_record_only=via_record_only)
    )
    with Dir.open(tmp_path) as d:
        owner = acquire(d)
        assert seen[0] == b"", "the window exists: the file is empty here"
        assert seen[1] == "writer_lock_held", seen
        assert (tmp_path / LOCK_NAME).read_bytes() != b""  # the record is written afterwards
        owner.release()
    # positive control: once released, the next writer acquires normally
    assert _attempt(tmp_path, via_record_only=False) == "ACQUIRED"


def test_an_empty_file_with_no_holder_is_free_so_no_grace_period_is_needed(tmp_path: Path) -> None:
    (tmp_path / LOCK_NAME).write_bytes(b"")  # what a crash inside the window leaves behind
    assert _attempt(tmp_path, via_record_only=False) == "ACQUIRED"
