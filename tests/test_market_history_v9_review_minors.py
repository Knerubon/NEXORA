"""PR #99 Reviewer A minors M-1..M-3 (hostile-input hardening of `jsonio` and the writer lock).

M-1: `jsonio.loads` scanned raw bytes for brackets but let `json.loads(bytes)` auto-detect UTF-16
     and UTF-32. A string character whose code units contain byte 0x22 (U+2200 is `00 22`)
     desynchronised the byte-level string stripper, so nesting far past `MAX_JSON_DEPTH` was
     accepted. Bytes are now decoded strictly as UTF-8 and anything else is `json_invalid`.
M-2: the N-01 fix in `lock._classify` (typed `jsonio.loads` instead of `json.loads`) had no test:
     reverting it let a deep lock record raise `RecursionError` out of `acquire`.
M-3: the exact depth bound was only tested through the constant itself, so loosening it to 64
     went unnoticed. The bound is pinned here as a literal.

Synthetic in-memory and tmp-dir fixtures only. Every test here fails on d2e8ee9 or under the
matching mutant in `scripts/mutation_check_market_history.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from nexora.market_history import jsonio
from nexora.market_history import lock as lockmod
from nexora.market_history.lock import LOCK_NAME, acquire_writer_lock, release_writer_lock
from nexora.market_history.models import HistoryError

from tests.test_market_history_v4_lock import record

# Reviewer A's M-1 probe: depth 201 hidden behind a U+2200 string, bound is 32.
SMUGGLED = '["∀",' + "[" * 200 + "]" * 200 + ',"x"]'
WIDE = ["utf-16-le", "utf-16-be", "utf-16", "utf-32-le", "utf-32-be", "utf-32"]


# --- M-1: strict UTF-8 ----------------------------------------------------------------------------
@pytest.mark.parametrize("encoding", WIDE)
def test_wide_encodings_cannot_smuggle_nesting_past_the_depth_bound(encoding: str) -> None:
    with pytest.raises(HistoryError, match="json_invalid"):
        jsonio.loads(SMUGGLED.encode(encoding), "pin")


@pytest.mark.parametrize("encoding", WIDE)
def test_evidence_bytes_are_utf8_only_even_when_shallow(encoding: str) -> None:
    doc = '{"a": ["∀", 1]}'
    assert jsonio.loads(doc.encode("utf-8")) == {"a": ["∀", 1]}  # positive control
    with pytest.raises(HistoryError, match="json_invalid"):
        jsonio.loads(doc.encode(encoding))


def test_utf8_bom_and_invalid_utf8_are_json_invalid() -> None:
    for bad in (b"\xef\xbb\xbf{}", b'{"a": "\xc0\xaf"}', b'{"a": "\xed\xa0\x80"}'):
        with pytest.raises(HistoryError, match="json_invalid"):
            jsonio.loads(bad)


def test_smuggled_depth_is_still_too_deep_when_the_text_is_utf8() -> None:
    """Same document as the M-1 probe, honestly encoded: the scan sees its true depth."""
    with pytest.raises(HistoryError, match="json_too_deep"):
        jsonio.loads(SMUGGLED.encode("utf-8"))
    with pytest.raises(HistoryError, match="json_too_deep"):
        jsonio.loads(SMUGGLED)


def test_wide_encoded_pin_file_is_refused_by_the_verifier_cli(tmp_path: Path) -> None:
    from tests.test_market_history_v4_custody import run_cli
    from tests.test_market_history_v9_hostile_input import _cli_inputs

    _e, base = _cli_inputs(tmp_path)
    (tmp_path / "pin.json").write_bytes(SMUGGLED.encode("utf-16"))
    code, out = run_cli("verify_market_history.py", *base, "--max-pin-age-seconds", "3600")
    assert code == 2 and out["status_reasons"] == ["pin_unreadable"]


# --- M-3: the exact bound -------------------------------------------------------------------------
def test_depth_bound_is_exactly_32() -> None:
    assert jsonio.MAX_JSON_DEPTH == 32
    at_bound = b"[" * 32 + b"]" * 32
    assert jsonio.loads(at_bound) is not None
    with pytest.raises(HistoryError, match="json_too_deep"):
        jsonio.loads(b"[" * 33 + b"]" * 33)
    nested_objects = '{"k":' * 32 + "0" + "}" * 32
    assert jsonio.loads(nested_objects) is not None
    with pytest.raises(HistoryError, match="json_too_deep"):
        jsonio.loads('{"k":' * 33 + "0" + "}" * 33)


# --- M-2: deep writer-lock record -----------------------------------------------------------------
DEEP_LOCK = b"[" * 2048 + b"]" * 2048  # fills the 4096-byte record read; deeper than recursion


def test_classify_deep_lock_record_is_unverifiable_not_recursion_error() -> None:
    assert len(DEEP_LOCK) <= lockmod._MAX_RECORD
    assert lockmod._classify(DEEP_LOCK) == "unverifiable"
    assert lockmod._classify(SMUGGLED.encode("utf-16")) == "unverifiable"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock tests (Linux)")
@pytest.mark.parametrize(
    "content",
    [DEEP_LOCK, record().encode("utf-16"), record().encode("utf-32-le")],
    ids=["deep", "utf16-this-host", "utf32-this-host"],
)
def test_hostile_lock_record_fails_closed_and_is_left_in_place(
    tmp_path: Path, content: bytes
) -> None:
    path = tmp_path / LOCK_NAME
    path.write_bytes(content)
    with pytest.raises(HistoryError, match="writer_lock_held"):
        acquire_writer_lock(tmp_path)
    assert path.read_bytes() == content
    # positive control: the same this-host record, UTF-8 as the writer produces it, is reclaimable
    path.write_text(record())
    token = acquire_writer_lock(tmp_path)
    release_writer_lock(tmp_path, token)
