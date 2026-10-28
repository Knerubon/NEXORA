"""V4 / A2: filesystem hardening. Cloud-tested on Linux (ext4); the no-`dir_fd` fallback is
exercised on Linux by switching the capability off, which is NOT a Windows test: Windows is
UNVERIFIED."""

from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history import fsutil
from nexora.market_history.extract import (
    dataset_status,
    extract,
    verify_dataset,
    verify_dataset_report,
    write_once,
)
from nexora.market_history.fsutil import (
    FS_SCHEMA,
    Dir,
    capability_blockers,
    classify_fs,
    filesystem_type,
    probe_filesystem,
    verifier_blockers,
)
from nexora.market_history.lock import LOCK_NAME
from nexora.market_history.models import HistoryError
from nexora.market_history.quality import QualityConfig

from tests.test_market_history import FakeSource, att, conv
from tests.test_market_history_corrective import REQ, TICKS, codes, go, reseal
from tests.test_market_history_v3 import NOW

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX semantics (Linux-tested)")


def need_symlinks(tmp: Path) -> None:
    try:
        os.symlink(tmp, tmp.parent / (tmp.name + "-probe"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")


# ======================= symlinked parents of --out =======================
@posix_only
def test_symlinked_parent_of_out_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    need_symlinks(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(real, tmp_path / "link")
    with pytest.raises(HistoryError, match="out_dir_ancestor_is_symlink"):
        go(tmp_path / "link" / "sub" / "ds")
    assert os.listdir(real) == []


@posix_only
def test_symlinked_grandparent_and_final_component_are_refused(tmp_path: Path) -> None:
    need_symlinks(tmp_path)
    (tmp_path / "real" / "mid").mkdir(parents=True)
    os.symlink(tmp_path / "real", tmp_path / "link")
    with pytest.raises(HistoryError, match="out_dir_ancestor_is_symlink"):
        go(tmp_path / "link" / "mid" / "ds")
    os.symlink(tmp_path / "real" / "mid", tmp_path / "leaf")
    with pytest.raises(HistoryError, match="out_dir_is_symlink"):
        go(tmp_path / "leaf")
    assert os.listdir(tmp_path / "real" / "mid") == []


@posix_only
def test_verify_refuses_a_dataset_reached_through_a_symlink(tmp_path: Path) -> None:
    need_symlinks(tmp_path)
    go(tmp_path / "real" / "ds")
    os.symlink(tmp_path / "real", tmp_path / "link")
    os.symlink(tmp_path / "real" / "ds", tmp_path / "leaf")
    assert codes(verify_dataset_report(tmp_path / "link" / "ds")) == ["dataset_dir_unsafe"]
    assert codes(verify_dataset_report(tmp_path / "leaf")) == ["dataset_dir_unsafe"]
    assert not dataset_status(tmp_path / "leaf").trusted
    assert verify_dataset(tmp_path / "real" / "ds") == []


@posix_only
def test_swapping_the_out_path_after_validation_cannot_redirect_writes(tmp_path: Path) -> None:
    """TOCTOU: after the directory was pinned, the attacker moves it away and plants a symlink to
    a directory of their choosing at the same path. Every write still lands in the original."""
    need_symlinks(tmp_path)
    out, moved, attacker = tmp_path / "ds", tmp_path / "moved", tmp_path / "attacker"
    attacker.mkdir()

    class Swapper(FakeSource):
        swapped = False

        def ticks(self, *a: Any, **k: Any) -> Any:
            if not Swapper.swapped:
                Swapper.swapped = True
                os.rename(out, moved)
                os.symlink(attacker, out)
            return super().ticks(*a, **k)

    m = extract(
        Swapper(TICKS),
        REQ,
        conv(),
        QualityConfig(5000),
        out,
        retrieved_at=NOW,
        clock_attestation=att(),
        protected_utc_ranges=(),
        sleep=lambda _s: None,
    )
    assert Swapper.swapped
    assert os.listdir(attacker) == []  # nothing escaped through the planted link
    assert (moved / "manifest.json").exists()
    assert verify_dataset(moved) == []
    assert m["dataset_id"]


@posix_only
def test_pinned_reads_follow_the_descriptor_not_the_path(tmp_path: Path) -> None:
    need_symlinks(tmp_path)
    go(tmp_path / "ds")
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / "manifest.json").write_text("{}")
    with Dir.open(tmp_path / "ds") as d:
        os.rename(tmp_path / "ds", tmp_path / "moved")
        os.symlink(attacker, tmp_path / "ds")
        assert not d.same_inode_as_path()  # diagnostic: the path no longer names this directory
        with d.open_regular("manifest.json") as fh:
            assert json.loads(fh.read())["symbol"] == "SYN"  # still the original manifest


# ======================= hard links, special files, regular-file checks =======================
@posix_only
def test_hard_linked_chunk_or_manifest_is_a_problem(tmp_path: Path) -> None:
    go(tmp_path / "a")
    name = json.loads((tmp_path / "a" / "manifest.json").read_text())["chunks"][0]["file"]
    os.link(tmp_path / "a" / name, tmp_path / "alias")  # a second name for the evidence
    assert f"chunk_multiple_links:{name}" in verify_dataset(tmp_path / "a")
    assert not dataset_status(tmp_path / "a").trusted
    go(tmp_path / "b")
    os.link(tmp_path / "b" / "manifest.json", tmp_path / "alias-m")
    assert codes(verify_dataset_report(tmp_path / "b")) == ["manifest_multiple_links"]


def test_write_once_never_overwrites_and_leaves_no_temp(tmp_path: Path) -> None:
    write_once(tmp_path / "f.bin", b"first")
    with pytest.raises(FileExistsError):
        write_once(tmp_path / "f.bin", b"second")
    assert (tmp_path / "f.bin").read_bytes() == b"first"
    assert sorted(os.listdir(tmp_path)) == ["f.bin"]
    assert not (tmp_path / "f.bin").stat().st_mode & 0o222


@posix_only
def test_write_once_refuses_a_symlinked_parent(tmp_path: Path) -> None:
    need_symlinks(tmp_path)
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "link")
    with pytest.raises(HistoryError, match="out_dir_is_symlink"):
        write_once(tmp_path / "link" / "f.bin", b"x")
    assert os.listdir(tmp_path / "real") == []


@posix_only
def test_planted_symlink_at_a_temp_or_final_name_is_not_followed(tmp_path: Path) -> None:
    need_symlinks(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("precious")
    out = tmp_path / "ds"
    out.mkdir()
    os.symlink(victim, out / "chunk-1000-3000.jsonl")  # final name pre-planted
    with pytest.raises(HistoryError):
        go(out)
    assert victim.read_text() == "precious"


# ======================= filesystems without hard links / unsupported configs =================
@posix_only
def test_filesystem_without_hard_links_fails_closed_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_links(*_a: Any, **_k: Any) -> None:
        raise OSError(errno.EPERM, "hard links not supported")  # FAT/exFAT/some shares

    monkeypatch.setattr(os, "link", no_links)
    with pytest.raises(HistoryError, match="filesystem_cannot_hardlink"):
        go(tmp_path / "ds")
    assert os.listdir(tmp_path / "ds") == []  # no probe leftovers, lock released
    assert not (tmp_path / "ds" / LOCK_NAME).exists()


def test_manifest_records_the_probe_and_it_is_part_of_the_manifest_hash(tmp_path: Path) -> None:
    m = go(tmp_path / "a")
    caps = m["filesystem_capabilities"]
    assert caps["schema"] == FS_SCHEMA and caps["platform"] == sys.platform
    assert set(caps["probes"]) == set(fsutil.REQUIRED_PROBES)
    reseal(tmp_path / "a", lambda x: x["filesystem_capabilities"]["probes"].update(dir_fd=False))
    assert not verify_dataset(tmp_path / "a")  # a re-sealed claim is self-consistent ...
    assert "capability:probe_not_proven:dir_fd" in dataset_status(tmp_path / "a").reasons


@posix_only
def test_probe_exercises_real_guarantees_on_this_filesystem(tmp_path: Path) -> None:
    need_symlinks(tmp_path)
    with Dir.open(tmp_path) as d:
        caps = probe_filesystem(d)
    assert capability_blockers(caps) == [] and caps["fs_class"] == "local"
    assert caps["probes"] == dict.fromkeys(fsutil.REQUIRED_PROBES, True)
    assert os.listdir(tmp_path) == []  # the probe cleans up after itself


def good_caps() -> dict[str, Any]:
    return {
        "schema": FS_SCHEMA,
        "platform": "linux",
        "fs_class": "local",
        "probes": dict.fromkeys(fsutil.REQUIRED_PROBES, True),
    }


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda c: c.update(platform="win32"), "platform_unproven:win32"),
        (lambda c: c.update(platform="darwin"), "platform_unproven:darwin"),
        (lambda c: c.update(fs_class="network"), "filesystem_unproven:network"),
        (lambda c: c.update(fs_class="unknown"), "filesystem_unproven:unknown"),
        (lambda c: c["probes"].update(nofollow_open=None), "probe_not_proven:nofollow_open"),
        (
            lambda c: c["probes"].update(hardlink_no_overwrite=False),
            "probe_not_proven:hardlink_no_overwrite",
        ),
        (
            lambda c: c["probes"].pop("advisory_lock_exclusion"),
            "probe_not_proven:advisory_lock_exclusion",
        ),
        (lambda c: c["probes"].update(dir_fd=1), "probe_not_proven:dir_fd"),  # truthy is not True
        (lambda c: c.update(probes=[]), "filesystem_probes_malformed"),
        (lambda c: c.update(schema="other"), "filesystem_capabilities_absent"),
    ],
)
def test_capability_blockers_rederive_the_verdict_from_raw_probes(mutate: Any, reason: str) -> None:
    caps = good_caps()
    caps["proven"] = True  # a stored verdict is never believed
    mutate(caps)
    assert reason in capability_blockers(caps)
    assert capability_blockers(good_caps()) == []
    junk_values: list[Any] = [None, [], "x", 5]
    for junk in junk_values:
        assert capability_blockers(junk) == ["filesystem_capabilities_absent"]


def test_filesystem_classification() -> None:
    assert classify_fs("ext4") == "local" and classify_fs("tmpfs") == "local"
    for net in ("nfs4", "cifs", "smb3", "fuse.sshfs", "9p", "virtiofs"):
        assert classify_fs(net) == "network"
    assert classify_fs("exfat") == "unknown" and classify_fs(None) == "unknown"


@posix_only
def test_filesystem_type_picks_the_longest_mount_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = (
        "1 0 8:1 / / rw - ext4 /dev/sda1 rw\n"
        "2 1 0:5 / /data rw - nfs4 srv:/x rw\n"
        "3 2 0:6 / /data/with\\040space rw - tmpfs tmpfs rw\n"
    )
    real_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **k: (
            info if str(self) == "/proc/self/mountinfo" else real_read(self, *a, **k)
        ),
    )
    assert filesystem_type(Path("/data/x/y")) == "nfs4"
    assert filesystem_type(Path("/data/with space/z")) == "tmpfs"
    assert filesystem_type(Path("/datax")) == "ext4"  # "/data" must not match "/datax"


def test_verifier_platform_and_dir_fd_gate_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        assert verifier_blockers() == []
    monkeypatch.setattr(fsutil, "PROVEN_PLATFORMS", frozenset())
    assert any(b.startswith("verifier_platform_unproven") for b in verifier_blockers())
    monkeypatch.setattr(fsutil, "DIR_FD_SUPPORTED", False)
    assert "verifier_lacks_dir_fd_nofollow" in verifier_blockers()


@posix_only
def test_fallback_mode_without_dir_fd_works_but_can_never_be_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates the Windows-style code path on Linux (path-based, lstat checks). It is not a
    Windows test; it proves the fallback is functional, still refuses symlinked ancestors, and is
    blocked from TRUSTED by the recorded probe."""
    need_symlinks(tmp_path)
    monkeypatch.setattr(fsutil, "DIR_FD_SUPPORTED", False)
    m = go(tmp_path / "ds")
    assert verify_dataset(tmp_path / "ds") == []
    assert m["filesystem_capabilities"]["probes"]["dir_fd"] is False
    reasons = dataset_status(tmp_path / "ds").reasons
    assert "capability:probe_not_proven:dir_fd" in reasons
    assert "capability:verifier_lacks_dir_fd_nofollow" in reasons
    os.symlink(tmp_path / "ds", tmp_path / "link")
    with pytest.raises(HistoryError, match="out_dir_is_symlink"):
        go(tmp_path / "link")
    (tmp_path / "ds2").mkdir()
    os.symlink(tmp_path / "ds2", tmp_path / "l2")
    with pytest.raises(HistoryError, match="out_dir_ancestor_is_symlink"):
        go(tmp_path / "l2" / "x")


def test_reparse_point_attribute_counts_as_a_symlink(monkeypatch: pytest.MonkeyPatch) -> None:
    class St:
        st_mode = 0o040755
        st_file_attributes = 0x400  # junction / mount point

    assert fsutil._kind_of(St()) == "symlink"  # type: ignore[arg-type]
