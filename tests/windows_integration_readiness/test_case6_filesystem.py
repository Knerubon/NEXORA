"""MATRIX CASE 6 - file lock, hard link, symlink/reparse point, crash recovery (CURRENT platform).

Runs on the cloud Linux container: it proves the PROBES work and records this platform's answers.
It proves nothing about Windows/NTFS; see UNSUPPORTED-GUARANTEES.md."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.windows_integration_readiness.filesystem_probes import PROBES, run_probes

TMP = ".tmp-"


def _write_once(path: Path, data: bytes, *, crash_before_link: bool = False) -> None:
    """Harness-local model of temp -> fsync -> hard-link -> unlink-temp (NOT the extractor)."""
    tmp = path.with_name(f"{path.name}{TMP}{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    if crash_before_link:
        raise KeyboardInterrupt("simulated crash")
    try:
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def test_case6_every_probe_runs_and_reports_a_structured_outcome(tmp_path: Path) -> None:
    result = run_probes(tmp_path)
    assert set(result["probes"]) == set(PROBES)
    for name, r in result["probes"].items():
        assert r["outcome"] in {"observed", "unsupported"}, (name, r)
    assert result["verified_on_windows_ntfs"] in (False, "operator_must_confirm_volume")
    json.dumps(result)  # evidence must be JSON-serialisable


def test_case6_probe_refuses_non_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "x").write_bytes(b"")
    with pytest.raises(ValueError):
        run_probes(tmp_path)


def test_case6_exclusive_lock_second_acquire_refused(tmp_path: Path) -> None:
    lock = tmp_path / ".writer.lock"
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    os.close(fd)
    with pytest.raises(FileExistsError):
        os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)


def test_case6_write_once_never_overwrites(tmp_path: Path) -> None:
    p = tmp_path / "chunk"
    _write_once(p, b"one")
    with pytest.raises(FileExistsError):
        _write_once(p, b"two")
    assert p.read_bytes() == b"one"
    assert [x.name for x in tmp_path.iterdir()] == ["chunk"]


def test_case6_crash_before_link_leaves_only_temp_and_no_final(tmp_path: Path) -> None:
    p = tmp_path / "chunk"
    with pytest.raises(KeyboardInterrupt):
        _write_once(p, b"data", crash_before_link=True)
    names = [x.name for x in tmp_path.iterdir()]
    assert not p.exists()
    assert len(names) == 1 and TMP in names[0]


def test_case6_rerun_after_crash_succeeds_once_stale_temp_removed(tmp_path: Path) -> None:
    p = tmp_path / "chunk"
    with pytest.raises(KeyboardInterrupt):
        _write_once(p, b"data", crash_before_link=True)
    for stale in tmp_path.glob(f"*{TMP}*"):
        stale.unlink()
    _write_once(p, b"data")
    assert p.read_bytes() == b"data"


def test_case6_symlink_is_distinguishable_without_following(tmp_path: Path) -> None:
    target, link = tmp_path / "t", tmp_path / "l"
    target.write_bytes(b"x")
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink not creatable on this platform/user: {type(exc).__name__}")
    assert link.is_symlink() and not target.is_symlink()
    assert os.lstat(link).st_ino != os.lstat(target).st_ino


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"),
    reason="os.O_NOFOLLOW absent on this platform (always the case on Windows)",
)
def test_case6_o_nofollow_refuses_symlink_where_available(tmp_path: Path) -> None:
    target, link = tmp_path / "t", tmp_path / "l"
    target.write_bytes(b"x")
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink not creatable: {type(exc).__name__}")
    with pytest.raises(OSError):
        os.open(link, os.O_RDONLY | os.O_NOFOLLOW)


def test_case6_records_whether_o_nofollow_exists_on_this_platform() -> None:
    """Informational: on Windows this is False and symlink refusal must not rely on the flag."""
    assert isinstance(hasattr(os, "O_NOFOLLOW"), bool)
