"""Single-writer ownership of a dataset folder (V4).

Ownership is an OS advisory lock held on an open file descriptor (`flock` on POSIX, a one-byte
`msvcrt.locking` range on Windows). The kernel drops it when the holder exits or crashes, so there
is no stale-lock state to judge and NOTHING IS EVER REAPED: no code path removes or renames another
process's lock file. The V3 scheme (read pid, check liveness, rename by name) had a race in which a
reaper could delete a live owner's freshly created lock; that class of bug cannot occur here because
no decision is derived from a previously read file.

Protocol (POSIX):
  0. take `flock(LOCK_EX|LOCK_NB)` on a descriptor of the PINNED DIRECTORY ITSELF (round 3, F6):
     a directory inode has no name inside itself that anyone could unlink or replace, so removing
     `.writer.lock` from outside can no longer admit a second writer; failure -> `writer_lock_held`;
  1. open `.writer.lock` (O_CREAT, no O_EXCL, O_NOFOLLOW, relative to the pinned directory);
  2. `flock(LOCK_EX|LOCK_NB)`; failure -> `writer_lock_held` (fail closed, never reap);
  3. confirm the locked inode is still the one at the name (the previous holder unlinks the file
     while still holding the lock; a waiter that locked the dead inode retries);
  4. inspect the content: empty = free (crash between create and write); a complete V4 record from
     THIS host = free (previous holder died; the kernel proved it); anything else (V3 record,
     garbage, partial write, foreign host) = `writer_lock_held` (owner unverifiable): fail
     closed, an operator must act;
  5. write the V4 record (pid/host/token are informational only, never used for ownership).
Release unlinks the file only while still holding the lock and only if the name still resolves to
the inode we hold, then closes the descriptors. `assert_held()` lets a writer confirm, before each
publication, that the record name still resolves to its inode: if someone removed or replaced it
the writer stops with `writer_lock_lost` (fail closed, auditable) instead of carrying on.

Supported: POSIX local filesystems where `flock` is exclusive (probed: `lock_exclusion_works`;
Linux ext4 cloud-tested). Not supported for TRUSTED: network/FUSE filesystems (flock may be local
only or emulated) and Windows (msvcrt path implemented, UNVERIFIED). Do not run V3 and V4 writers on
the same folder: V3 never took an OS lock.
"""

from __future__ import annotations

import errno
import json
import os
import socket
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from nexora.market_history import jsonio
from nexora.market_history.fsutil import Dir
from nexora.market_history.models import HistoryError

LOCK_NAME = ".writer.lock"
LOCK_FORMAT = "nexora.writer-lock/2"
_MAX_RECORD = 4096
_WINDOWS = sys.platform == "win32"
if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
    import msvcrt
else:
    import fcntl


def _hook(_stage: str) -> None:
    """Test seam: called with a stage name; a test replaces it (monkeypatch) to block, switch actors
    or raise at an exact point of the protocol. Production leaves it a no-op."""


def _try_lock(fd: int) -> bool:
    """True when this descriptor now holds the exclusive lock, False when someone else does.
    Raises HistoryError when the platform offers no usable lock."""
    if sys.platform == "win32":  # pragma: no cover - Windows only, UNVERIFIED
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            return False
        raise HistoryError("writer_lock_unsupported", f"errno {exc.errno}") from None
    return True


def _unlock(fd: int) -> None:
    try:
        if sys.platform == "win32":  # pragma: no cover
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def lock_exclusion_works(d: Dir, name: str) -> bool:
    """Probe: of two independent descriptors on `name`, exactly one can hold the lock."""
    try:
        first = d.open_fd(name, os.O_RDWR)
    except OSError:
        return False
    second = -1
    try:
        if not _try_lock(first):
            return False
        second = d.open_fd(name, os.O_RDWR)
        return not _try_lock(second)
    except (OSError, HistoryError):
        return False
    finally:
        for fd in (second, first):
            if fd >= 0:
                _unlock(fd)
                os.close(fd)


@dataclass
class WriterLock:
    """A held lock. `release()` is idempotent."""

    directory: Dir
    fd: int
    token: str
    dev: int
    ino: int
    released: bool = False
    dir_fd: int = -1  # descriptor holding the directory lock (POSIX); -1 where unsupported

    def assert_held(self) -> None:
        """Raise `writer_lock_lost` unless `.writer.lock` still names the inode we hold."""
        if self.released or not _same_file_ids(self.directory, self.dev, self.ino):
            raise HistoryError("writer_lock_lost", f"{LOCK_NAME} was removed or replaced")

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            _hook("before_release_unlink")
            try:
                now = self.directory.stat(LOCK_NAME)
            except FileNotFoundError:
                now = None
            # Unlink only while we still hold the lock and only the inode we hold.
            if now is not None and (now.st_dev, now.st_ino) == (self.dev, self.ino):
                if _WINDOWS:  # pragma: no cover - Windows cannot unlink an open file
                    os.ftruncate(self.fd, 0)
                else:
                    self.directory.unlink(LOCK_NAME, missing_ok=True)
        finally:
            _unlock(self.fd)
            os.close(self.fd)
            _release_dir_lock(self.dir_fd)


def _classify(raw: bytes) -> str:
    """'empty' | 'v4-this-host' | 'v4-foreign-host' | 'unverifiable'."""
    if not raw.strip():
        return "empty"
    try:
        data = jsonio.loads(raw, "writer_lock")
    except (ValueError, UnicodeDecodeError):  # HistoryError (deep/invalid JSON) is a ValueError
        return "unverifiable"
    if not isinstance(data, dict) or data.get("format") != LOCK_FORMAT:
        return "unverifiable"  # V3 record (never took an OS lock), garbage or a partial write
    if type(data.get("pid")) is not int or type(data.get("token")) is not str:
        return "unverifiable"
    return "v4-this-host" if data.get("host") == socket.gethostname() else "v4-foreign-host"


def acquire(directory: Dir) -> WriterLock:
    """Take exclusive ownership of `directory` or raise `writer_lock_held` (this includes an owner
    that cannot be verified: fail closed) or `writer_lock_unsupported`. Never removes anyone
    else's file."""
    dir_fd = _acquire_dir_lock(directory)
    try:
        lock = _acquire_record(directory)
    except BaseException:
        _release_dir_lock(dir_fd)
        raise
    lock.dir_fd = dir_fd
    return lock


def _open_self(directory: Dir) -> int:
    """A new open file description of the pinned directory (POSIX only)."""
    assert directory.fd is not None
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(".", flags, dir_fd=directory.fd)


def _acquire_dir_lock(directory: Dir) -> int:
    if _WINDOWS or directory.fd is None:  # pragma: no cover - fallback is never TRUSTED
        return -1
    fd = _open_self(directory)
    try:
        if not _try_lock(fd):
            raise HistoryError("writer_lock_held", "directory lock held by another writer")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _release_dir_lock(fd: int) -> None:
    if fd >= 0:
        _unlock(fd)
        os.close(fd)


def directory_lock_exclusion_works(directory: Dir) -> bool:
    """Probe: of two independent descriptors on the pinned directory, at most one can hold the
    lock, and a holder blocks the other (works whether or not the caller already holds it)."""
    if _WINDOWS or directory.fd is None:
        return False
    first = second = -1
    try:
        first, second = _open_self(directory), _open_self(directory)
        _try_lock(first)  # now held by `first`, or already held by the caller / another writer
        return not _try_lock(second)
    except (OSError, HistoryError):
        return False
    finally:
        for fd in (second, first):
            if fd >= 0:
                _unlock(fd)
                os.close(fd)


def _acquire_record(directory: Dir) -> WriterLock:
    flags = os.O_RDWR | os.O_CREAT
    for _attempt in range(4):
        try:
            fd = directory.open_fd(LOCK_NAME, flags, 0o644)
        except OSError as exc:  # symlink (ELOOP), directory (EISDIR), permissions...
            raise HistoryError(
                "writer_lock_held", f"{LOCK_NAME}: cannot open ({type(exc).__name__})"
            ) from None
        _hook("after_open")
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise HistoryError("writer_lock_held", f"{LOCK_NAME}: not a regular file")
            if not _try_lock(fd):
                if _same_file(directory, st):
                    raise HistoryError("writer_lock_held", LOCK_NAME)
                os.close(fd)  # we raced a releaser that unlinked this inode: look again
                fd = -1
                continue
            _hook("after_flock")
            if not _same_file(directory, st):
                _unlock(fd)
                os.close(fd)
                fd = -1
                continue
            os.lseek(fd, 0, os.SEEK_SET)
            verdict = _classify(os.read(fd, _MAX_RECORD))
            if verdict not in ("empty", "v4-this-host"):
                raise HistoryError(
                    "writer_lock_held", f"{LOCK_NAME}: owner unverifiable ({verdict})"
                )
            token = os.urandom(8).hex()
            body = json.dumps(
                {"format": LOCK_FORMAT, "pid": os.getpid(), "host": socket.gethostname(),
                 "token": token}
            ).encode()  # fmt: skip
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, body)
            os.fsync(fd)
            return WriterLock(directory, fd, token, st.st_dev, st.st_ino)
        except BaseException:
            if fd >= 0:
                _unlock(fd)
                os.close(fd)
            raise
    raise HistoryError("writer_lock_held", LOCK_NAME)


def _same_file(directory: Dir, st: os.stat_result) -> bool:
    return _same_file_ids(directory, st.st_dev, st.st_ino)


def _same_file_ids(directory: Dir, dev: int, ino: int) -> bool:
    try:
        now = directory.stat(LOCK_NAME)
    except FileNotFoundError:
        return False
    return (now.st_dev, now.st_ino) == (dev, ino)


# ---- path/token compatibility API (V3 signatures) -------------------------------------------
_HELD: dict[str, WriterLock] = {}


def acquire_writer_lock(directory: Path) -> str:
    """Take the lock on `directory` and return a token for `release_writer_lock`. The held
    descriptor is kept by this module until released."""
    d = Dir.open(directory)
    try:
        lock = acquire(d)
    except BaseException:
        d.close()
        raise
    _HELD[lock.token] = lock
    return lock.token


def release_writer_lock(directory: Path, token: str) -> None:
    """Release only a lock this process holds under `token`; an unknown token is a no-op."""
    lock = _HELD.pop(token, None)
    if lock is None:
        return
    try:
        lock.release()
    finally:
        lock.directory.close()
