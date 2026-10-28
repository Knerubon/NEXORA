"""V4 / A1: writer-lock races. Each test reproduces a race that the V3 lock (read pid, check
liveness, rename by name) lost or could not express, using injected hooks and real subprocesses.
SYNTHETIC fixtures only. Platform: POSIX flock (Linux cloud-tested); Windows is UNVERIFIED."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history import lock as lockmod
from nexora.market_history.extract import verify_dataset
from nexora.market_history.fsutil import Dir
from nexora.market_history.lock import (
    LOCK_FORMAT,
    LOCK_NAME,
    _acquire_record,
    acquire,
    acquire_writer_lock,
    release_writer_lock,
)
from nexora.market_history.models import HistoryError

from tests.test_market_history_corrective import go

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock tests (Linux)")


def record(**over: Any) -> str:
    base = {"format": LOCK_FORMAT, "pid": 4_000_000, "host": socket.gethostname(), "token": "old"}
    return json.dumps({**base, **over})


def hook_once(monkeypatch: pytest.MonkeyPatch, stage: str, action: Callable[[], None]) -> None:
    fired: list[int] = []

    def hook(name: str) -> None:
        if name == stage and not fired:
            fired.append(1)
            action()

    monkeypatch.setattr(lockmod, "_hook", hook)


def token_in(path: Path) -> str:
    return str(json.loads(path.read_text())["token"])


def holder(tmp: Path, *, exit_at: str | None = None) -> subprocess.Popen[str]:
    """A real process that takes the lock, prints READY and waits (or dies at a given stage)."""
    script = textwrap.dedent(
        """
        import os, sys, time
        from pathlib import Path
        from nexora.market_history import lock
        from nexora.market_history.fsutil import Dir
        stage = sys.argv[2]
        if stage != "-":
            lock._hook = lambda s: os._exit(7) if s == stage else None
        d = Dir.open(Path(sys.argv[1]))
        h = lock.acquire(d)
        print("READY", flush=True)
        time.sleep(120)
        """
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "packages"), str(ROOT)])}
    return subprocess.Popen(
        [sys.executable, "-c", script, str(tmp), exit_at or "-"],
        stdout=subprocess.PIPE,
        text=True,
        cwd=ROOT,
        env=env,
    )


def ready(p: subprocess.Popen[str]) -> None:
    assert p.stdout is not None
    assert p.stdout.readline().strip() == "READY"


# --- R1: stale lock seen, then a live owner appears BEFORE we act (the V3 rename-by-name bug) ----
def test_live_owner_appearing_after_we_looked_is_never_displaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / LOCK_NAME).write_text(record())  # a dead owner's leftover: free by OS state
    winner: list[Any] = []

    def live_owner_takes_it() -> None:
        # B acquires between A's open and A's flock. Since round 3, A already holds the directory
        # lock at that point, so only a writer that skips it (a round-2 V4 writer: the record
        # sub-protocol alone) can get here; the record protocol must still never displace it.
        winner.append(_acquire_record(Dir.open(tmp_path)))

    hook_once(monkeypatch, "after_open", live_owner_takes_it)
    with Dir.open(tmp_path) as d, pytest.raises(HistoryError, match="writer_lock_held"):
        acquire(d)  # A loses cleanly; it never renames or unlinks anything
    assert token_in(tmp_path / LOCK_NAME) == winner[0].token
    assert sorted(os.listdir(tmp_path)) == [LOCK_NAME]
    winner[0].release()
    winner[0].directory.close()


# --- R2: two processes (threads) race on the same stale file --------------------------------------
def test_simultaneous_acquirers_on_a_stale_file_exactly_one_wins(tmp_path: Path) -> None:
    (tmp_path / LOCK_NAME).write_text(record())
    n = 8
    start = threading.Barrier(n)
    all_tried = threading.Event()
    results: list[Any] = []
    guard = threading.Lock()

    def worker() -> None:
        with Dir.open(tmp_path) as d:
            start.wait()
            try:
                lock = acquire(d)
            except HistoryError as exc:
                outcome: Any = exc.code
            else:
                outcome = lock
            with guard:
                results.append(outcome)
                if len(results) == n:
                    all_tried.set()
            if not isinstance(outcome, str):
                all_tried.wait(30)  # the winner keeps holding until every racer has tried
                outcome.release()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    winners = [r for r in results if not isinstance(r, str)]
    assert len(winners) == 1 and results.count("writer_lock_held") == n - 1


# --- R3: release racing acquisition ---------------------------------------------------------------
def test_acquirer_that_locked_a_just_released_inode_retries_on_the_new_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Dir.open(tmp_path) as da:
        a = acquire(da)

        # B opens the file A holds; A releases (unlink + unlock) before B's flock; B's flock then
        # succeeds on an inode that no longer has a name: B must notice and retry, not "own" it.
        hook_once(monkeypatch, "after_open", a.release)
        with Dir.open(tmp_path) as db:
            b = _acquire_record(db)  # record sub-protocol (a full acquire stops at the dir lock)
            assert token_in(tmp_path / LOCK_NAME) == b.token != a.token
            with Dir.open(tmp_path) as dc, pytest.raises(HistoryError, match="writer_lock_held"):
                acquire(dc)  # and B really owns the file that now carries its name
            b.release()
    assert os.listdir(tmp_path) == []


def test_release_while_another_process_probes_does_not_lose_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Dir.open(tmp_path) as da:
        a = acquire(da)
        outcomes: list[str] = []

        def probe() -> None:
            with Dir.open(tmp_path) as db:
                try:
                    acquire(db)
                except HistoryError as exc:
                    outcomes.append(exc.code)

        hook_once(monkeypatch, "before_release_unlink", probe)
        a.release()  # still holds the OS lock while deciding what to unlink
        assert outcomes == ["writer_lock_held"]
    assert os.listdir(tmp_path) == []


def test_release_never_unlinks_a_file_it_does_not_hold(tmp_path: Path) -> None:
    with Dir.open(tmp_path) as d:
        a = acquire(d)
        (tmp_path / LOCK_NAME).unlink()
        (tmp_path / LOCK_NAME).write_text(record(token="someone-else"))  # different inode
        a.release()
    assert token_in(tmp_path / LOCK_NAME) == "someone-else"


def test_release_is_idempotent_and_tokens_are_checked(tmp_path: Path) -> None:
    token = acquire_writer_lock(tmp_path)
    release_writer_lock(tmp_path, "not-my-token")
    assert (tmp_path / LOCK_NAME).exists()
    release_writer_lock(tmp_path, token)
    release_writer_lock(tmp_path, token)
    assert not (tmp_path / LOCK_NAME).exists()


# --- real processes: liveness is decided by the kernel -------------------------------------------
def test_lock_of_a_live_process_is_untouchable_then_free_after_kill(tmp_path: Path) -> None:
    p = holder(tmp_path)
    try:
        ready(p)
        before = (tmp_path / LOCK_NAME).stat()
        body = (tmp_path / LOCK_NAME).read_bytes()
        for _ in range(3):
            with pytest.raises(HistoryError, match="writer_lock_held"):
                acquire_writer_lock(tmp_path)
            with pytest.raises(HistoryError, match="writer_lock_held"):
                go(tmp_path)
        after = (tmp_path / LOCK_NAME).stat()
        assert (before.st_ino, before.st_dev) == (after.st_ino, after.st_dev)
        assert (tmp_path / LOCK_NAME).read_bytes() == body
        assert sorted(os.listdir(tmp_path)) == [LOCK_NAME]  # go() wrote nothing
    finally:
        p.send_signal(signal.SIGKILL)  # crash: no cleanup runs
        p.wait()
    # Crash recovery: the leftover record is free because the kernel dropped the lock.
    assert (tmp_path / LOCK_NAME).exists()
    go(tmp_path)
    assert verify_dataset(tmp_path) == []


def test_verify_reports_a_held_lock_on_a_finished_dataset(tmp_path: Path) -> None:
    go(tmp_path)
    assert verify_dataset(tmp_path) == []
    p = holder(tmp_path)
    try:
        ready(p)
        assert f"writer_lock_present:{LOCK_NAME}" in verify_dataset(tmp_path)
    finally:
        p.kill()
        p.wait()


def test_editing_the_record_of_a_live_owner_does_not_free_it(tmp_path: Path) -> None:
    """PID reuse / forged content: the record is informational. A record naming a dead pid, or
    this very process, changes nothing while another process holds the OS lock."""
    p = holder(tmp_path)
    try:
        ready(p)
        for content in (record(pid=4_000_000), record(pid=os.getpid()), record(host="elsewhere")):
            (tmp_path / LOCK_NAME).write_text(content)
            with pytest.raises(HistoryError, match="writer_lock_held"):
                acquire_writer_lock(tmp_path)
    finally:
        p.kill()
        p.wait()


def test_crash_between_create_and_write_leaves_a_free_empty_lock(tmp_path: Path) -> None:
    p = holder(tmp_path, exit_at="after_flock")  # os._exit after create+flock, before the record
    assert p.wait(timeout=30) == 7
    assert (tmp_path / LOCK_NAME).read_bytes() == b""  # what a V3 reader saw and wedged on
    token = acquire_writer_lock(tmp_path)
    release_writer_lock(tmp_path, token)
    assert os.listdir(tmp_path) == []


def test_crash_after_the_record_was_written_leaves_a_free_lock(tmp_path: Path) -> None:
    p = holder(tmp_path)
    ready(p)
    p.send_signal(signal.SIGKILL)
    p.wait()
    assert json.loads((tmp_path / LOCK_NAME).read_text())["format"] == LOCK_FORMAT
    release_writer_lock(tmp_path, acquire_writer_lock(tmp_path))
    assert os.listdir(tmp_path) == []


def test_pid_of_a_live_unrelated_process_in_a_free_record_does_not_block(tmp_path: Path) -> None:
    (tmp_path / LOCK_NAME).write_text(record(pid=1))  # pid 1 is alive; nobody holds the lock
    release_writer_lock(tmp_path, acquire_writer_lock(tmp_path))


# --- unreadable / hostile / partial content: fail closed, never reap -----------------------------
@pytest.mark.parametrize(
    "content",
    [
        record(host="some-other-host"),  # foreign host: the OS lock may not be shared
        record()[:30],  # partially written record
        "{garbage",
        "[]",
        "\x00\xff\xfe",
        json.dumps({"pid": 1, "host": socket.gethostname(), "token": "t"}),  # V3 record
        record(pid="x"),
        record(format="nexora.writer-lock/999"),
    ],
)
def test_unverifiable_records_fail_closed_and_are_left_in_place(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / LOCK_NAME
    path.write_text(content, errors="surrogateescape")
    before = path.read_bytes()
    with pytest.raises(HistoryError, match="writer_lock_held"):
        acquire_writer_lock(tmp_path)
    with pytest.raises(HistoryError, match="writer_lock_held"):
        go(tmp_path)
    assert path.read_bytes() == before and os.listdir(tmp_path) == [LOCK_NAME]


def test_zero_length_lock_file_means_crash_before_write_and_is_free(tmp_path: Path) -> None:
    (tmp_path / LOCK_NAME).write_bytes(b"")
    token = acquire_writer_lock(tmp_path)
    assert token_in(tmp_path / LOCK_NAME) == token
    release_writer_lock(tmp_path, token)


def test_emptying_the_record_of_a_held_lock_does_not_free_it(tmp_path: Path) -> None:
    """A reader can observe an empty file while an acquirer is mid-protocol: content is never what
    decides ownership, the OS lock is."""
    p = holder(tmp_path, exit_at=None)
    ready(p)
    (tmp_path / LOCK_NAME).write_bytes(b"")  # reader observes an empty file mid-acquire
    try:
        with pytest.raises(HistoryError, match="writer_lock_held"):
            acquire_writer_lock(tmp_path)
    finally:
        p.kill()
        p.wait()


def test_symlink_directory_and_fifo_at_the_lock_name_fail_closed(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    d1, d2, d3 = (tmp_path / n for n in ("a", "b", "c"))
    for d in (d1, d2, d3):
        d.mkdir()
    os.symlink(victim, d1 / LOCK_NAME)
    (d2 / LOCK_NAME).mkdir()
    os.mkfifo(d3 / LOCK_NAME)
    for d in (d1, d2, d3):
        with pytest.raises(HistoryError, match="writer_lock_held"):
            acquire_writer_lock(d)
    assert victim.read_text() == "precious"


def test_no_code_path_renames_or_reaps_another_lock() -> None:
    """Structural guard: the reaper is gone and the module never moves files by name."""
    src = Path(lockmod.__file__).read_text()
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith(("#", '"', "`"))
    )
    assert "os.rename" not in code and "os.replace" not in code and ".dead-" not in code
    assert not hasattr(lockmod, "_reap_stale_lock") and not hasattr(lockmod, "_pid_alive")
