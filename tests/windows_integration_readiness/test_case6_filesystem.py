"""MATRIX CASE 6 - file lock, hard link, symlink/reparse point, crash recovery (CURRENT platform).

Runs on the cloud Linux container: it proves the PROBES work and records this platform's answers.
It proves nothing about Windows/NTFS; see UNSUPPORTED-GUARANTEES.md."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.windows_integration_readiness import filesystem_probes as fp
from tests.windows_integration_readiness.filesystem_probes import OUTCOMES, PROBES, run_probes

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
        assert r["outcome"] in OUTCOMES, (name, r)
        assert r["outcome"] != "error", (name, r)
    assert result["verified_on_windows_ntfs"] is False
    # filesystem type is never auto-detected: the run always asks for manual evidence
    assert result["probes"]["filesystem_type"]["outcome"] == "manual_required"
    assert result["stop_required"] is True
    assert sum(result["outcome_counts"].values()) == len(PROBES)
    json.dumps(result)  # evidence must be JSON-serialisable


def test_case6_verified_on_windows_ntfs_is_never_truthy_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    result = run_probes(tmp_path)
    assert result["verified_on_windows_ntfs"] is False
    assert result["probes"]["reparse_point_junction"]["outcome"] == "manual_required"
    assert result["stop_required"] is True


def test_case6_emulated_hard_link_is_an_error_not_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    def copy_not_link(src: Path, dst: Path) -> None:
        if Path(dst).exists():
            raise FileExistsError(dst)
        shutil.copyfile(src, dst)

    monkeypatch.setattr(os, "link", copy_not_link)
    r = fp.probe_hard_link(tmp_path)
    assert r["outcome"] == "error" and "not a true hard link" in r["detail"]


def test_case6_probe_error_forces_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = os.open

    def deny(path: object, flags: int, *a: int) -> int:
        if str(path).endswith("excl"):
            raise PermissionError("synthetic")
        return real_open(path, flags, *a)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", deny)
    result = run_probes(tmp_path)
    assert result["probes"]["exclusive_create"]["outcome"] == "error"
    assert result["outcome_counts"]["error"] == 1 and result["stop_required"] is True


def test_case6_unsupported_hard_link_forces_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_link(*_a: object) -> None:
        raise OSError("synthetic: filesystem cannot hard link")

    monkeypatch.setattr(os, "link", no_link)
    result = run_probes(tmp_path)
    assert result["probes"]["hard_link"]["outcome"] == "unsupported"
    assert result["stop_required"] is True


def test_case6_trailing_dot_and_space_probe_reports_this_platform(tmp_path: Path) -> None:
    r = fp.probe_trailing_dot_space(tmp_path)
    assert r["outcome"] == "observed"
    if os.name != "nt":
        assert r["detail"] == "trailing dot/space preserved as distinct names"


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
