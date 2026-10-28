"""Filesystem hardening for market-history datasets (V4).

`Dir` pins a directory by file descriptor. The path is walked ONCE, component by component, with
`O_NOFOLLOW|O_DIRECTORY` (so a symlinked parent anywhere above `--out` is refused and cannot be
swapped in later); every later operation is relative to that descriptor (`dir_fd=`), so a rename or
symlink swap of the path after validation cannot redirect a read, write, link or unlink.

Where the platform has no `dir_fd`/`O_NOFOLLOW` (Windows) `Dir` falls back to `lstat` checks
(symlink AND reparse point/junction) followed by path-based operations. That fallback has a
check-then-use window and is reported as such by `probe_filesystem`; datasets written or verified
in that mode can never be TRUSTED (see `capability_blockers`).

Tested on Linux only (cloud). Windows behaviour is UNVERIFIED.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import sys
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO

from nexora.market_history.models import HistoryError

TMP_MARKER = ".tmp-"
FS_SCHEMA = "nexora.fs-capabilities/1"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)  # never hang on a FIFO swapped in after the check
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_BINARY = getattr(os, "O_BINARY", 0)  # Windows: never translate newlines in evidence files
_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT (junctions, symlinks, mount points)

DIR_FD_SUPPORTED = bool(
    _NOFOLLOW
    and _DIRECTORY
    and all(
        fn in os.supports_dir_fd
        for fn in (os.open, os.stat, os.unlink, os.link, os.mkdir, os.chmod)
    )
)

# Platform/filesystem combinations for which the guarantees below were actually exercised by the
# test-suite. Everything else is "unproven" and cannot yield TRUSTED. Add an entry only together
# with a recorded run of the probes and tests on that configuration.
PROVEN_PLATFORMS = frozenset({"linux"})
LOCAL_FS_TYPES = frozenset(
    {"ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs", "overlay", "f2fs", "zfs", "jfs", "reiserfs"}
)
NETWORK_FS_PREFIXES = ("nfs", "cifs", "smb", "fuse", "9p", "virtiofs", "afs", "ceph", "gluster")
REQUIRED_PROBES = (
    "dir_fd",
    "nofollow_open",
    "hardlink_no_overwrite",
    "nlink_reported",
    "advisory_lock_exclusion",
    "directory_lock_exclusion",
    "fsync_dir",
)


def _is_reparse(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _kind_of(st: os.stat_result) -> str:
    if stat.S_ISLNK(st.st_mode) or _is_reparse(st):
        return "symlink"
    return "regular" if stat.S_ISREG(st.st_mode) else "other"


class Dir:
    """A directory pinned for the lifetime of the object. Use as a context manager."""

    def __init__(self, path: Path, fd: int | None) -> None:
        self.path = path
        self.fd = fd

    # ---- opening -----------------------------------------------------------------------
    @classmethod
    def open(cls, path: Path, *, create: bool = False) -> Dir:
        """Pin `path`. Refuses a symlink/reparse point at the final component
        (`out_dir_is_symlink`) or at any ancestor (`out_dir_ancestor_is_symlink`) and a
        non-directory (`out_dir_not_a_directory`). `create` makes missing components."""
        absolute = Path(os.path.abspath(path))
        if DIR_FD_SUPPORTED:
            return cls._open_posix(absolute, create)
        return cls._open_fallback(absolute, create)

    @classmethod
    def _open_posix(cls, absolute: Path, create: bool) -> Dir:
        parts = absolute.parts
        fd = os.open(parts[0], os.O_RDONLY | _DIRECTORY)
        try:
            for i, part in enumerate(parts[1:], 1):
                last = i == len(parts) - 1
                nfd = _step(fd, part, create, last)
                os.close(fd)
                fd = nfd
        except BaseException:
            os.close(fd)
            raise
        return cls(absolute, fd)

    @classmethod
    def _open_fallback(cls, absolute: Path, create: bool) -> Dir:
        current = Path(absolute.anchor)
        parts = absolute.parts[1:]
        for i, part in enumerate(parts):
            current = current / part
            last = i == len(parts) - 1
            try:
                st = os.lstat(current)
            except FileNotFoundError:
                if not create:
                    raise HistoryError("out_dir_missing", part) from None
                os.mkdir(current)
                st = os.lstat(current)
            if stat.S_ISLNK(st.st_mode) or _is_reparse(st):
                raise HistoryError(
                    "out_dir_is_symlink" if last else "out_dir_ancestor_is_symlink", part
                )
            if not stat.S_ISDIR(st.st_mode):
                raise HistoryError("out_dir_not_a_directory", part)
        return cls(absolute, None)

    def __enter__(self) -> Dir:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None

    @property
    def race_free(self) -> bool:
        return self.fd is not None

    # ---- operations relative to the pinned directory -----------------------------------
    def _p(self, name: str) -> Path:
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise HistoryError("invalid_entry_name", name[:40])
        return self.path / name

    def stat(self, name: str) -> os.stat_result:
        """lstat of an entry (never follows a link). Raises FileNotFoundError if absent."""
        self._p(name)
        if self.fd is not None:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        return os.lstat(self.path / name)

    def kind(self, name: str) -> str:
        """'missing' | 'symlink' | 'regular' | 'other' without following links."""
        try:
            return _kind_of(self.stat(name))
        except FileNotFoundError:
            return "missing"

    def lexists(self, name: str) -> bool:
        return self.kind(name) != "missing"

    def open_fd(self, name: str, flags: int, mode: int = 0o600) -> int:
        self._p(name)
        flags |= _NOFOLLOW | _BINARY
        if self.fd is not None:
            return os.open(name, flags, mode, dir_fd=self.fd)
        return os.open(self.path / name, flags, mode)

    def open_regular(self, name: str) -> BinaryIO:
        """Open a regular file for reading, never through a link (O_NOFOLLOW + fstat, relative to
        the pinned directory). Raises HistoryError otherwise."""
        try:
            fd = self.open_fd(name, os.O_RDONLY | _NONBLOCK)
        except OSError as exc:
            raise HistoryError("file_unreadable", f"{name}: {type(exc).__name__}") from None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or _kind_of(st) != "regular":
                raise HistoryError("not_a_regular_file", name)
            if self.fd is None and self.kind(name) != "regular":  # fallback: best effort only
                raise HistoryError("not_a_regular_file", name)
        except BaseException:
            os.close(fd)
            raise
        return os.fdopen(fd, "rb")

    def listdir(self) -> list[str]:
        return os.listdir(self.fd) if self.fd is not None else os.listdir(self.path)

    def link(self, src: str, dst: str) -> None:
        """Hard link `src` to `dst` (atomic, never overwrites: FileExistsError if `dst` exists)."""
        self._p(src), self._p(dst)
        if self.fd is not None:
            os.link(src, dst, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
        else:
            os.link(self.path / src, self.path / dst)

    def unlink(self, name: str, *, missing_ok: bool = False) -> None:
        self._p(name)
        try:
            if self.fd is not None:
                os.unlink(name, dir_fd=self.fd)
            else:
                os.unlink(self.path / name)
        except FileNotFoundError:
            if not missing_ok:
                raise

    def chmod_readonly(self, name: str) -> None:
        self._p(name)
        if self.fd is not None:
            os.chmod(name, 0o444, dir_fd=self.fd)
        else:
            os.chmod(self.path / name, 0o444)

    def fsync(self) -> None:
        if self.fd is None:
            return  # Windows cannot fsync a directory handle
        try:
            os.fsync(self.fd)
        except OSError:
            pass

    def same_inode_as_path(self) -> bool:
        """True while the pinned directory is still what the path names (diagnostic only; all
        operations already go through the descriptor)."""
        try:
            now = os.lstat(self.path)
        except OSError:
            return False
        if self.fd is None:
            return True
        mine = os.fstat(self.fd)
        return (now.st_dev, now.st_ino) == (mine.st_dev, mine.st_ino)


def _step(fd: int, part: str, create: bool, last: bool) -> int:
    """Open child directory `part` of `fd` without following a link, creating it when asked."""
    flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW
    try:
        return os.open(part, flags, dir_fd=fd)
    except FileNotFoundError:
        if not create:
            raise HistoryError("out_dir_missing", part) from None
        try:
            os.mkdir(part, 0o777, dir_fd=fd)
        except FileExistsError:
            pass
        return _step(fd, part, False, last)
    except OSError as exc:
        if exc.errno not in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise
        try:
            st = os.stat(part, dir_fd=fd, follow_symlinks=False)
        except OSError:
            raise HistoryError("out_dir_not_a_directory", part) from None
        if stat.S_ISLNK(st.st_mode):
            raise HistoryError(
                "out_dir_is_symlink" if last else "out_dir_ancestor_is_symlink", part
            ) from None
        raise HistoryError("out_dir_not_a_directory", part) from None


# ---- capability probe -----------------------------------------------------------------------
def _unescape_mount(text: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def filesystem_type(path: Path) -> str | None:
    """Filesystem type of the mount holding `path` (Linux /proc/self/mountinfo), else None."""
    try:
        text = Path("/proc/self/mountinfo").read_text()
    except OSError:
        return None
    best: tuple[int, str] | None = None
    target = str(path)
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        fields = left.split(" ")
        if not sep or len(fields) < 5 or not right:
            continue
        mount = _unescape_mount(fields[4])
        within = target == mount or target.startswith(mount.rstrip("/") + "/")
        if within and (best is None or len(mount) > best[0]):
            best = (len(mount), right.split(" ")[0])
    return best[1] if best else None


def classify_fs(fs_type: str | None) -> str:
    if fs_type is None:
        return "unknown"
    if fs_type in LOCAL_FS_TYPES:
        return "local"
    if fs_type.startswith(NETWORK_FS_PREFIXES):
        return "network"
    return "unknown"


def probe_filesystem(d: Dir) -> dict[str, Any]:
    """Exercise, in `d` (caller holds the writer lock), the guarantees dataset integrity relies on.

    Returns a JSON-safe record stored in the manifest. It is a CLAIM by the extracting machine:
    `capability_blockers` re-derives the verdict from the raw probes (never from a stored verdict),
    but a hostile extractor can still lie; the custodian approval is the control for that."""
    from nexora.market_history.lock import (  # local: avoid import cycle
        directory_lock_exclusion_works,
        lock_exclusion_works,
    )

    suffix = f"{TMP_MARKER}probe-{os.getpid()}-{os.urandom(4).hex()}"
    a, b, s = f".a{suffix}", f".b{suffix}", f".s{suffix}"
    probes: dict[str, bool | None] = {k: None for k in REQUIRED_PROBES}
    probes["dir_fd"] = d.race_free
    created: list[str] = []
    try:
        fd = d.open_fd(a, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created.append(a)
        os.write(fd, b"x")
        os.close(fd)
        try:
            d.link(a, b)
            created.append(b)
            try:
                d.link(a, b)
                probes["hardlink_no_overwrite"] = False
            except FileExistsError:
                probes["hardlink_no_overwrite"] = True
            probes["nlink_reported"] = d.stat(a).st_nlink == 2
        except OSError:
            probes["hardlink_no_overwrite"] = False
        probes["nofollow_open"] = _probe_nofollow(d, a, s, created)
        probes["advisory_lock_exclusion"] = lock_exclusion_works(d, a)
        probes["directory_lock_exclusion"] = directory_lock_exclusion_works(d)
        probes["fsync_dir"] = d.fd is not None and _fsync_ok(d.fd)
    finally:
        for name in created:
            d.unlink(name, missing_ok=True)
    fs_type = filesystem_type(d.path)
    return {
        "schema": FS_SCHEMA,
        "platform": sys.platform,
        "fs_class": classify_fs(fs_type),
        "probes": probes,
    }


def _fsync_ok(fd: int) -> bool:
    try:
        os.fsync(fd)
    except OSError:
        return False
    return True


def _probe_nofollow(d: Dir, target: str, name: str, created: list[str]) -> bool | None:
    """True when opening a symlink with the no-follow flag is refused; None when a symlink cannot
    be created here (cannot be proven)."""
    try:
        if d.fd is not None:
            os.symlink(target, name, dir_fd=d.fd)
        else:
            os.symlink(target, d.path / name)
    except (OSError, NotImplementedError):
        return None
    created.append(name)
    try:
        os.close(d.open_fd(name, os.O_RDONLY | _NONBLOCK))
    except OSError:
        return True
    return False


def capability_blockers(caps: Any) -> list[str]:
    """Reasons a recorded probe cannot support TRUSTED. Empty list = proven configuration.

    Re-derived from the raw probes and the platform/fs class: any stored verdict is ignored."""
    if not isinstance(caps, dict) or caps.get("schema") != FS_SCHEMA:
        return ["filesystem_capabilities_absent"]
    out: list[str] = []
    if caps.get("platform") not in PROVEN_PLATFORMS:
        out.append(f"platform_unproven:{str(caps.get('platform'))[:20]}")
    if caps.get("fs_class") != "local":
        out.append(f"filesystem_unproven:{str(caps.get('fs_class'))[:20]}")
    probes = caps.get("probes")
    if not isinstance(probes, dict):
        return [*out, "filesystem_probes_malformed"]
    for key in REQUIRED_PROBES:
        if probes.get(key) is not True:
            out.append(f"probe_not_proven:{key}")
    return out


def verifier_blockers() -> list[str]:
    """The verifying machine must itself be able to read without following links, relative to a
    pinned directory, and be a proven platform."""
    out: list[str] = []
    if sys.platform not in PROVEN_PLATFORMS:
        out.append(f"verifier_platform_unproven:{sys.platform}")
    if not DIR_FD_SUPPORTED:
        out.append("verifier_lacks_dir_fd_nofollow")
    return out
