"""Case 6 filesystem probes. Plain `os` calls in a caller-supplied scratch directory; no MT5, no
nexora imports, nothing outside `root`. Safe to run on the CURRENT platform; the operator runs the
same module on the Windows node (`python -m tests.windows_integration_readiness.filesystem_probes
<empty scratch dir on the target volume>`) and attaches the JSON to the evidence record.

Each probe returns {"outcome": <observed|unsupported|manual_required|error>, "detail": str}.
A probe never aborts the run: the point is to RECORD what this platform/volume does. But the run's
`stop_required` is true when any probe is `error` or `manual_required`, or the hard-link probe is
`unsupported`: the checklist (B2) STOPS on it until the operator attaches evidence that resolves
each one. `verified_on_windows_ntfs` is always the boolean False: no probe here verifies NTFS.
"""

from __future__ import annotations

import json
import os
import platform
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _r(outcome: str, detail: str = "") -> dict[str, str]:
    return {"outcome": outcome, "detail": detail}


def probe_hard_link(root: Path) -> dict[str, str]:
    a, b = root / "hl-src", root / "hl-dst"
    a.write_bytes(b"x")
    try:
        os.link(a, b)
    except OSError as exc:
        return _r("unsupported", type(exc).__name__)
    sa, sb = os.stat(a), os.stat(b)
    if sa.st_nlink != 2 or sb.st_nlink != 2 or (sa.st_dev, sa.st_ino) != (sb.st_dev, sb.st_ino):
        # e.g. a share or sync layer that "links" by copying: the no-overwrite design is void
        return _r("error", f"link not a true hard link (nlink={sa.st_nlink}/{sb.st_nlink})")
    try:
        os.link(a, b)  # a second link onto the same name must refuse to overwrite
    except FileExistsError:
        return _r("observed", "true hard link (nlink=2, same inode); second link refused")
    except OSError as exc:
        return _r("observed", f"true hard link; second link raised {type(exc).__name__}")
    return _r("error", "second os.link overwrote or silently succeeded")


def probe_symlink(root: Path) -> dict[str, str]:
    target, link = root / "sl-target", root / "sl-link"
    target.write_bytes(b"x")
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        return _r("unsupported", f"{type(exc).__name__} (Windows needs privilege/Developer Mode)")
    kind = "symlink" if stat.S_ISLNK(os.lstat(link).st_mode) else "not-symlink"
    return _r("observed", f"created; lstat reports {kind}")


def probe_nofollow_flag(root: Path) -> dict[str, str]:
    has = hasattr(os, "O_NOFOLLOW")
    if not has:
        return _r("unsupported", "os.O_NOFOLLOW absent: symlink refusal must use lstat/fstat only")
    target, link = root / "nf-target", root / "nf-link"
    target.write_bytes(b"x")
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        return _r("observed", f"flag present; symlink not creatable ({type(exc).__name__})")
    try:
        fd = os.open(link, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return _r("observed", "open through symlink refused")
    os.close(fd)
    return _r("error", "open through symlink succeeded despite O_NOFOLLOW")


def probe_exclusive_create(root: Path) -> dict[str, str]:
    p = root / "excl"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _r("observed", "second O_EXCL create refused")
    return _r("error", "second O_EXCL create succeeded")


def probe_rename_over_existing(root: Path) -> dict[str, str]:
    a, b = root / "rn-a", root / "rn-b"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    try:
        os.rename(a, b)
    except FileExistsError:
        return _r("observed", "os.rename onto existing refused (Windows semantics)")
    except OSError as exc:
        return _r("observed", f"os.rename onto existing raised {type(exc).__name__}")
    return _r("observed", "os.rename onto existing REPLACED target (POSIX semantics)")


def probe_unlink_open_file(root: Path) -> dict[str, str]:
    p = root / "open-unlink"
    p.write_bytes(b"x")
    with open(p, "rb"):
        try:
            os.unlink(p)
        except OSError as exc:
            return _r("observed", f"unlink of open file refused: {type(exc).__name__}")
    return _r("observed", "unlink of open file allowed (POSIX semantics)")


def probe_readonly_attribute(root: Path) -> dict[str, str]:
    p = root / "ro"
    p.write_bytes(b"x")
    os.chmod(p, 0o444)
    try:
        with open(p, "ab"):
            return _r("observed", "read-only bit did NOT stop append (running as privileged user?)")
    except PermissionError:
        pass
    finally:
        os.chmod(p, 0o644)
    return _r("observed", "append to 0o444 file refused")


def probe_fsync_directory(root: Path) -> dict[str, str]:
    try:
        fd = os.open(root, os.O_RDONLY)
    except OSError as exc:
        return _r(
            "unsupported", f"cannot open directory: {type(exc).__name__}; dir fsync is a no-op"
        )
    try:
        os.fsync(fd)
    except OSError as exc:
        return _r("unsupported", f"fsync(dir) raised {type(exc).__name__}")
    finally:
        os.close(fd)
    return _r("observed", "fsync(dir) ok (durability of the entry still unproven)")


def probe_case_sensitivity(root: Path) -> dict[str, str]:
    (root / "Case").write_bytes(b"1")
    try:
        (root / "case").write_bytes(b"2")
    except OSError as exc:
        return _r("observed", type(exc).__name__)
    same = len([p for p in root.iterdir() if p.name.lower() == "case"]) == 1
    return _r("observed", "case-INSENSITIVE volume" if same else "case-sensitive volume")


def probe_trailing_dot_space(root: Path) -> dict[str, str]:
    """Win32 path normalization strips a trailing dot or space, so `x.` and `x ` can alias `x`."""
    (root / "x").write_bytes(b"0")
    aliased = []
    for name in ("x.", "x "):
        try:
            (root / name).write_bytes(b"1")
        except OSError as exc:
            aliased.append(f"{name!r}:{type(exc).__name__}")
            continue
        if (root / "x").read_bytes() == b"1":
            aliased.append(f"{name!r}:aliases x")
            (root / "x").write_bytes(b"0")
    if aliased:
        return _r("observed", "trailing dot/space NOT preserved: " + ", ".join(aliased))
    return _r("observed", "trailing dot/space preserved as distinct names")


def probe_reparse_point_note(root: Path) -> dict[str, str]:
    if os.name != "nt":
        return _r("unsupported", "not Windows: junction/reparse behaviour cannot be probed here")
    return _r(
        "manual_required",
        "NOT PROBED: create a junction with `mklink /J` per INTEGRATION-MATRIX case 6 step 3 and "
        "attach the output; os.lstat does not report junctions as symlinks on all Python builds",
    )


def probe_filesystem_type(root: Path) -> dict[str, str]:
    """Not detectable portably from Python without platform calls this harness does not make."""
    return _r(
        "manual_required",
        "NOT PROBED: attach `fsutil fsinfo volumeinfo <drive>:` (Windows) or `findmnt -T <dir>` "
        "(Linux); network shares and synced folders are STOP (checklist B2)",
    )


PROBES: dict[str, Callable[[Path], dict[str, str]]] = {
    "hard_link": probe_hard_link,
    "symlink": probe_symlink,
    "o_nofollow_flag": probe_nofollow_flag,
    "exclusive_create": probe_exclusive_create,
    "rename_over_existing": probe_rename_over_existing,
    "unlink_open_file": probe_unlink_open_file,
    "readonly_attribute": probe_readonly_attribute,
    "fsync_directory": probe_fsync_directory,
    "case_sensitivity": probe_case_sensitivity,
    "trailing_dot_space": probe_trailing_dot_space,
    "reparse_point_junction": probe_reparse_point_note,
    "filesystem_type": probe_filesystem_type,
}
OUTCOMES = ("observed", "unsupported", "manual_required", "error")


def run_probes(root: Path) -> dict[str, Any]:
    if any(root.iterdir()):
        raise ValueError("probe directory must be empty")
    results: dict[str, Any] = {}
    for name, fn in PROBES.items():
        sub = root / name
        sub.mkdir()
        try:
            results[name] = fn(sub)
        except Exception as exc:  # recorded, never hidden
            results[name] = _r("error", f"{type(exc).__name__}: {exc}")
    count = {o: sum(r["outcome"] == o for r in results.values()) for o in OUTCOMES}
    stop = (
        count["error"] > 0
        or count["manual_required"] > 0
        or results["hard_link"]["outcome"] == "unsupported"
    )
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "os_name": os.name,
        "probes": results,
        "outcome_counts": count,
        "stop_required": stop,
        "verified_on_windows_ntfs": False,
    }


if __name__ == "__main__":
    out = Path(sys.argv[1])
    print(json.dumps(run_probes(out), indent=2, sort_keys=True))
