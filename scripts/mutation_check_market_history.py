# ruff: noqa: E501
"""Manual mutation check for the V4 market-history safeguards (developer tool; not run by CI).

Applies each deliberate regression to the source, runs the V4 tests, and requires them to FAIL
(mutant killed). The original file is always restored. Usage (repo root):
    uv run --no-sync python scripts/mutation_check_market_history.py
Exit code 0 = every mutant killed; 1 = at least one survived (a test gap).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "packages/nexora/market_history/"
TESTS = ["tests/test_market_history_v4_lock.py", "tests/test_market_history_v4_fs.py",
         "tests/test_market_history_v4_custody.py"]  # fmt: skip

# (label, file, exact old text, replacement)
MUTANTS: list[tuple[str, str, str, str]] = [
    ("lock: no inode re-check after flock (release/acquire race)", "lock.py",
     "            if not _same_file(directory, st):\n                _unlock(fd)",
     "            if False:\n                _unlock(fd)"),
    ("lock: unverifiable record treated as free (reaping)", "lock.py",
     'if verdict not in ("empty", "v4-this-host"):', 'if verdict not in ("empty", "v4-this-host", "unverifiable", "v4-foreign-host"):'),
    ("lock: release unlinks without inode check", "lock.py",
     "if now is not None and (now.st_dev, now.st_ino) == (self.dev, self.ino):", "if now is not None:"),
    ("lock: flock never reports contention", "lock.py",
     "        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):\n            return False",
     "        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):\n            return True"),
    ("fsutil: ancestors opened following symlinks", "fsutil.py",
     "    flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW\n    try:\n        return os.open(part",
     "    flags = os.O_RDONLY | _DIRECTORY\n    try:\n        return os.open(part"),
    ("fsutil: capability verdict ignores the filesystem class", "fsutil.py",
     'if caps.get("fs_class") != "local":', 'if False:'),
    ("fsutil: unproven probe values accepted", "fsutil.py",
     "        if probes.get(key) is not True:", "        if probes.get(key) is False:"),
    ("custody: self-approval check disabled", "custody.py",
     "        if normalize_identity(ident) in mine:", "        if False:"),
    ("custody: registry membership not required", "custody.py",
     '    if custodian is None:\n        return "approver_not_authorized"', "    if False:\n        return \"approver_not_authorized\""),
    ("custody: chain prev-link not checked", "custody.py",
     "        if e.get(\"prev_sha256\") != prev:", "        if False:"),
    ("custody: approval need not bind the manifest hash", "custody.py",
     '"content_sha256", "manifest_sha256", "symbol")\n    return {k: manifest[k] for k in keys}', '"content_sha256", "symbol")\n    return {k: manifest[k] for k in keys}'),
    ("custody: later revocation ignored", "custody.py",
     "    if revoked:\n        return CustodyVerdict(False", "    if False:\n        return CustodyVerdict(False"),
    ("extract: custody evidence not consulted for TRUSTED", "extract.py",
     "    reasons += _custody_reasons(directory, manifest, store, registry, expected_head, now)\n", ""),
    ("extract: hard-link count not checked on chunks", "extract.py",
     "            if directory.stat(name).st_nlink != 1:", "            if False:"),
    ("extract: capability blockers ignored by status", "extract.py",
     '    reasons += [\n        f"capability:{c}" for c in capability_blockers(manifest.get("filesystem_capabilities"))\n    ]\n', ""),
]  # fmt: skip


def run_tests() -> int:
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False).returncode


def main() -> int:
    if run_tests() != 0:
        print("baseline tests do not pass; refusing to mutate")
        return 2
    survived = 0
    for label, name, old, new in MUTANTS:
        path = ROOT / PKG / name
        original = path.read_text()
        if original.count(old) != 1:
            print(f"BAD MUTANT (pattern found {original.count(old)}x): {label}")
            survived += 1
            continue
        try:
            path.write_text(original.replace(old, new))
            killed = run_tests() != 0
        finally:
            path.write_text(original)
        print(f"{'KILLED  ' if killed else 'SURVIVED'} {label}")
        survived += 0 if killed else 1
    print(f"{len(MUTANTS) - survived}/{len(MUTANTS)} mutants killed")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
