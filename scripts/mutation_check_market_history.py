# ruff: noqa: E501
"""Manual mutation check for the V4 + round-3 + V9 market-history safeguards (developer tool; not run by CI).

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
         "tests/test_market_history_v4_custody.py", "tests/test_market_history_r3_adversarial.py",
         "tests/test_market_history_v9_mutation_regressions.py",
         "tests/test_market_history_v9_revocation_latency.py",
         "tests/test_market_history_v9_hostile_input.py",
         "tests/test_market_history_v9_review_minors.py"]  # fmt: skip

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
     "    if approver.id == cand[\"operator\"]:\n        return \"self_approval_by_operator\"", "    if False:\n        return \"self_approval_by_operator\""),
    ("custody: registry membership/role not required for approver", "custody.py",
     "    if approver is None or akey is None:\n        return f\"approver_{why}\"", "    if False:\n        return f\"approver_{why}\""),
    ("custody: chain prev-link not checked", "custody.py",
     "        if e.get(\"prev_sha256\") != prev:", "        if False:"),
    ("custody: approval need not bind the manifest hash", "custody.py",
     '"content_sha256", "manifest_sha256", "symbol")\n    return {k: manifest[k] for k in keys}', '"content_sha256", "symbol")\n    return {k: manifest[k] for k in keys}'),
    ("custody: later revocation ignored", "custody.py",
     "    if later:\n        revoked = ", "    if False:\n        revoked = "),
    ("extract: custody evidence not consulted for TRUSTED", "extract.py",
     "    reasons += _custody_reasons(\n        directory, manifest, store, registry, expected_head, now, pin, max_pin_age\n    )\n", ""),
    ("extract: hard-link count not checked on chunks", "extract.py",
     "        if before.st_nlink != 1:\n            raise HistoryError(\"multiple_links\", name)", "        if False:\n            raise HistoryError(\"multiple_links\", name)"),
    ("extract: capability blockers ignored by status", "extract.py",
     '    reasons += [\n        f"capability:{c}" for c in capability_blockers(manifest.get("filesystem_capabilities"))\n    ]\n', ""),
    # ---- round 3 (F1-F6, N1-N6) ----
    ("R3 F1: approval signature not verified", "custody.py",
     "        return \"approval_signature_invalid\"", "        pass"),
    ("R3 F1: operator attestation signature not verified", "custody.py",
     "        return \"operator_signature_invalid\"", "        pass"),
    ("R3 F2: replayed approval_id counted", "custody.py",
     "        if not why and replay:\n            why = \"replayed_approval_id\"", "        if False:\n            why = \"replayed_approval_id\""),
    ("R3 F2: candidate reuse allowed", "custody.py",
     "            if e[\"candidate_sha256\"] in candidates:\n                why = \"candidate_reused\"", "            if False:\n                why = \"candidate_reused\""),
    ("R3 F2: lineage (acknowledged revocations) not checked", "custody.py",
     "            why = _lineage_problem(e, revocations, blocking)", "            why = \"\""),
    ("R3 F2: unidentifiable revocation does not block", "custody.py",
     "                blocking.append(f\"custody:revocation_{e['seq']}_unidentifiable\")", "                pass"),
    ("R3 F5: missing head pin accepted", "custody.py",
     "        return [\"custody:independent_pin_missing\"], None", "        return [], None"),
    ("R3 F5: pin signature not verified", "custody.py",
     "        return [\"custody:pin_signature_invalid\"], None", "        pass"),
    ("R3 F5: stale pin accepted", "custody.py",
     "        out.append(\"custody:pin_stale\")", "        pass"),
    ("R3 F5: pin head mismatch ignored", "custody.py",
     "        out.append(\"custody:pin_head_mismatch\")", "        pass"),
    ("R3 F5/N4: pin authority need not be independent", "custody.py",
     "        blocking.append(\"custody:pin_not_independent\")", "        pass"),
    ("R3 F4: approvals after the pin counted", "custody.py",
     "    if e[\"seq\"] > pinned.seq:\n        return \"approval_not_covered_by_pin\"", "    if False:\n        return \"approval_not_covered_by_pin\""),
    ("R3 F3: path replacement during status not detected", "extract.py",
     "        reasons.append(\"integrity:dataset_path_replaced_during_check\")", "        pass"),
    ("R3 F3/N3: snapshot change during read not detected", "extract.py",
     "        raise HistoryError(\"changed_during_read\", name)\n    return data", "        pass\n    return data"),
    ("R3 N2: approval path replacement not detected", "custody.py",
     "            raise HistoryError(\"dataset_not_approvable\", \"dataset_path_replaced_during_approval\")", "            pass"),
    ("R3 F6: directory lock never contended", "lock.py",
     "        if not _try_lock(fd):\n            raise HistoryError(\"writer_lock_held\", \"directory lock held by another writer\")", "        if False:\n            raise HistoryError(\"writer_lock_held\", \"directory lock held by another writer\")"),
    ("R3 F6: lost lock record not detected", "lock.py",
     "        if self.released or not _same_file_ids(self.directory, self.dev, self.ino):", "        if self.released:"),
    ("R3 N6: duplicate identity ignores flags", "quality.py",
     "        ident = (t.raw_msc, bid, ask, flags)", "        ident = (t.raw_msc, bid, ask)"),
    # ---- Wave V9 (N-04): the seven valid mutants the V8 review found surviving, plus boundaries ----
    ("V9 D3: gap bound >= instead of >", "quality.py",
     "            elif t.utc_msc - prev.utc_msc > self.config.max_gap_ms:", "            elif t.utc_msc - prev.utc_msc >= self.config.max_gap_ms:"),
    ("V9 C25: key valid_from ignored", "custody.py",
     "            if (key.valid_from and t < key.valid_from) or (key.valid_until and t > key.valid_until):", "            if key.valid_until and t > key.valid_until:"),
    ("V9 C25b: key valid_until exclusive", "custody.py",
     "(key.valid_until and t > key.valid_until)", "(key.valid_until and t >= key.valid_until)"),
    ("V9 T8: chunk contiguity not checked", "extract.py",
     "        if c[\"from_raw_msc\"] != cursor:\n            problems.append(Problem(\"chunk_not_contiguous\", name))", "        if False:\n            problems.append(Problem(\"chunk_not_contiguous\", name))"),
    ("V9 C21: approval before dataset retrieval allowed", "custody.py",
     "    if approved_at < retrieved:", "    if False:"),
    ("V9 T9: orders_submitted may be non-zero", "extract.py",
     "        or orders != 0\n", ""),
    ("V9 C12: reapproval need not acknowledge a revocation", "custody.py",
     "    if prior - ack:\n        return \"revocation_not_acknowledged\"", "    if False:\n        return \"revocation_not_acknowledged\""),
    ("V9 OPM: candidate operator may differ from the recorded operator", "custody.py",
     "    if c.get(\"operator\") != recorded:\n        return \"candidate_operator_mismatch\"", "    if False:\n        return \"candidate_operator_mismatch\""),
    ("V9 X1: approval before candidate creation allowed", "custody.py",
     "    if approved_at < created_at:", "    if False:"),
    ("V9 X2: pin age bound exclusive", "custody.py",
     "    elif now - captured > max_pin_age:", "    elif now - captured >= max_pin_age:"),
    ("V9 X3: approval future-skew doubled", "custody.py",
     "    if approved_at > now + MAX_SKEW:", "    if approved_at > now + MAX_SKEW + MAX_SKEW:"),
    ("V9 X4: pin future-skew doubled", "custody.py",
     "    if captured > now + MAX_SKEW:\n", "    if captured > now + MAX_SKEW + MAX_SKEW:\n"),
    ("V9 X5: approval dated after pin capture counted", "custody.py",
     "    if _aware(e[\"approved_at\"], \"approved_at\") > pinned.captured_at + MAX_SKEW:", "    if False:"),
    ("V9 N01: timestamp OverflowError not caught", "jsonio.py",
     "    except (ValueError, OverflowError):\n        raise HistoryError(\"invalid_timestamp\", what) from None", "    except ValueError:\n        raise HistoryError(\"invalid_timestamp\", what) from None"),
    ("V9 N01: JSON depth bound not enforced", "jsonio.py",
     "    if check_depth and json_depth(raw, MAX_JSON_DEPTH) > MAX_JSON_DEPTH:", "    if False:"),
    ("V9 N01: platform type not checked", "fsutil.py",
     "    if type(platform) is not str or platform not in PROVEN_PLATFORMS:", "    if platform not in PROVEN_PLATFORMS:"),
    ("PR99 M-1: bytes parsed with encoding auto-detection", "jsonio.py",
     "        return json.loads(text)", "        return json.loads(data)"),
    ("PR99 M-2: lock record parsed without the typed reader", "lock.py",
     '        data = jsonio.loads(raw, "writer_lock")', "        data = json.loads(raw)"),
    ("PR99 M-3: depth bound loosened to 64", "jsonio.py",
     "MAX_JSON_DEPTH = 32", "MAX_JSON_DEPTH = 64"),
    ("PR99 M-3: depth bound off by one", "jsonio.py",
     "json_depth(raw, MAX_JSON_DEPTH) > MAX_JSON_DEPTH:", "json_depth(raw, MAX_JSON_DEPTH) > MAX_JSON_DEPTH + 1:"),
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
