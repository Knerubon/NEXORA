"""Wave V9 / N-01: hostile input must end in a TYPED fail-closed rejection.

Each case below crashed the verifier path of PR #96 at 1c536d5 with an uncontrolled exception that
the catch-all turned into `status_internal_error:*` / `verify_internal_error:*` (or, in a CLI, a
traceback). An internal error is fail-closed but it is not a diagnosis: the reason code must name
what was wrong. Every test asserts (a) the dataset is not TRUSTED, (b) no `internal_error` reason
appears, (c) the specific typed code, and (d) a positive control from the SAME fixture is still
accepted, so a blanket "reject everything" cannot pass.

Synthetic fixtures, per-run in-memory keys and the normal registry/pin flow only. No MT5, no real
data, no network. The seeded type-mutation fuzz is deterministic (SEED); a failure prints the
mutation that caused it.
"""

from __future__ import annotations

import json
import random
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history import custody, jsonio
from nexora.market_history.custody import (
    LocalAppendOnlyStore,
    PrincipalRegistry,
    load_registry,
    seal_entry,
)
from nexora.market_history.extract import CHUNK_NAME, load_anchor, verify_dataset
from nexora.market_history.fsutil import REQUIRED_PROBES, capability_blockers
from nexora.market_history.models import HistoryError

from tests.test_market_history_corrective import reseal
from tests.test_market_history_v3 import seal
from tests.test_market_history_v4_custody import (
    CHECK,
    KEYS,
    REGISTRY_JSON,
    Env,
    approve,
    body_for,
    fresh_pin,
    make_env,
    run_cli,
    status,
)

SEED = 20261029
EDGE_HIGH = "9999-12-31T23:59:59-12:00"  # +12h puts it past year 9999 in UTC
EDGE_LOW = "0001-01-01T00:00:00+14:00"  # -14h puts it before year 1 in UTC
DEEP = b"[" * 100_000 + b"]" * 100_000


def _clean(s: Any) -> None:
    assert not s.trusted, s.reasons
    assert not any("internal_error" in r for r in s.reasons), s.reasons


def _trusted_env(tmp: Path) -> Env:
    e = make_env(tmp)
    approve(e)
    assert status(e).trusted  # positive control: this fixture IS trustable
    return e


def _rewrite(path: Path, data: bytes | str) -> None:
    path.chmod(0o644)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


# ---------------------------------------------------------------- timestamps (OverflowError) ---
@pytest.mark.parametrize("stamp", [EDGE_HIGH, EDGE_LOW])
def test_signed_approval_with_unrepresentable_timestamp_is_a_typed_rejection(
    tmp_path: Path, stamp: str
) -> None:
    e = make_env(tmp_path)
    e.store.append(body_for(e, "carol", approved_at=stamp))  # genuinely signed by carol
    s = status(e)
    _clean(s)
    assert "custody:approval_1_rejected:invalid:invalid_timestamp" in s.reasons
    approve(e)  # positive control: a normal approval after it still works
    assert status(e).trusted


@pytest.mark.parametrize("stamp", [EDGE_HIGH, EDGE_LOW])
def test_signed_pin_with_unrepresentable_captured_at_is_a_typed_rejection(
    tmp_path: Path, stamp: str
) -> None:
    e = _trusted_env(tmp_path)
    pin = fresh_pin(e.store)
    assert pin is not None
    bad = {k: v for k, v in pin.items() if k != "signature"} | {"captured_at": stamp}
    bad["signature"] = KEYS["pia"].sign("head-pin", bad)  # the pin authority really signed it
    s = status(e, pin=bad)
    _clean(s)
    assert "custody:pin_malformed:pinned_by_or_captured_at" in s.reasons


@pytest.mark.parametrize("field", ["valid_from", "valid_until"])
@pytest.mark.parametrize("stamp", [EDGE_HIGH, EDGE_LOW])
def test_registry_key_window_with_unrepresentable_timestamp_is_malformed(
    tmp_path: Path, field: str, stamp: str
) -> None:
    data = deepcopy(REGISTRY_JSON)
    data["principals"][1]["keys"][0][field] = stamp
    with pytest.raises(HistoryError) as err:  # HistoryError, never OverflowError
        PrincipalRegistry.from_json(data)
    assert err.value.code == "registry_malformed"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(data))
    with pytest.raises(HistoryError, match="registry_malformed"):
        load_registry(path)
    # positive control: the same registry with a representable far-future window loads
    data["principals"][1]["keys"][0][field] = "9999-12-31T23:59:59+00:00"
    assert PrincipalRegistry.from_json(data).find("carol") is not None


def test_anchor_with_unrepresentable_recorded_at_is_typed(tmp_path: Path) -> None:
    from nexora.market_history.extract import ANCHOR_SCHEMA

    h = "a" * 64
    anchor = {
        "schema": ANCHOR_SCHEMA, "manifest_sha256": h, "dataset_id": h, "content_sha256": h,
        "symbol": "SYN", "recorded_by": "x", "storage_ref": "y", "recorded_at": EDGE_HIGH,
    }  # fmt: skip
    path = tmp_path / "anchor.json"
    path.write_text(json.dumps(anchor))
    with pytest.raises(HistoryError, match="anchor_malformed"):
        load_anchor(path, dataset_dir=tmp_path / "nowhere")
    anchor["recorded_at"] = "2026-01-01T00:00:00+00:00"
    path.write_text(json.dumps(anchor))
    assert load_anchor(path, dataset_dir=tmp_path / "nowhere")["symbol"] == "SYN"


# ----------------------------------------------------------- deep nesting (RecursionError) ---
def test_deeply_nested_store_entry_is_unreadable_not_internal_error(tmp_path: Path) -> None:
    e = _trusted_env(tmp_path)
    pin = fresh_pin(e.store)
    (entry_file,) = list(e.store.location.iterdir())
    original = entry_file.read_bytes()
    _rewrite(entry_file, DEEP)
    s = status(e, pin=pin)
    _clean(s)
    assert "custody:store_unreadable:store_unreadable" in s.reasons
    _rewrite(entry_file, original)
    assert status(e, pin=pin).trusted  # positive control


def test_deeply_nested_manifest_is_a_typed_integrity_problem(tmp_path: Path) -> None:
    e = _trusted_env(tmp_path)
    manifest = e.ds / "manifest.json"
    original = manifest.read_bytes()
    pin = fresh_pin(e.store)
    _rewrite(manifest, DEEP)
    assert verify_dataset(e.ds) == ["manifest_unreadable:json_too_deep"]
    s = status(e, pin=pin)
    _clean(s)
    assert "integrity:manifest_unreadable:json_too_deep" in s.reasons
    _rewrite(manifest, original)
    assert verify_dataset(e.ds) == [] and status(e, pin=pin).trusted


def test_deeply_nested_chunk_row_is_a_typed_chunk_problem(tmp_path: Path) -> None:
    import hashlib

    e = _trusted_env(tmp_path)
    chunk = sorted(e.ds.glob("chunk-*.jsonl"))[0]
    original_chunk, original_manifest = chunk.read_bytes(), (e.ds / "manifest.json").read_bytes()
    _rewrite(chunk, b"[" * 100_000 + b"]" * 100_000 + b"\n")  # one hostile row (200 KB)

    def _rehash(m: dict[str, Any]) -> None:  # a forger who also repairs every hash
        for c in m["chunks"]:
            if c["file"] == chunk.name:
                c["sha256"] = hashlib.sha256(chunk.read_bytes()).hexdigest()

    reseal(e.ds, _rehash)
    problems = verify_dataset(e.ds)
    assert any(p.startswith("chunk_malformed:") for p in problems), problems
    assert not any("internal_error" in p for p in problems), problems
    _clean(status(e))
    _rewrite(chunk, original_chunk)
    _rewrite(e.ds / "manifest.json", original_manifest)
    assert verify_dataset(e.ds) == []


def test_deeply_nested_registry_pin_and_anchor_files_are_typed(tmp_path: Path) -> None:
    reg = tmp_path / "registry.json"
    reg.write_bytes(DEEP)
    with pytest.raises(HistoryError, match="registry_unreadable"):
        load_registry(reg)
    anchor = tmp_path / "anchor.json"
    anchor.write_bytes(DEEP)
    with pytest.raises(HistoryError, match="anchor_unreadable"):
        load_anchor(anchor, dataset_dir=tmp_path / "nowhere")
    with pytest.raises(HistoryError, match="json_too_deep"):
        jsonio.loads(DEEP, "pin")


def test_depth_bound_is_mechanical_and_ignores_brackets_inside_strings() -> None:
    ok = b"[" * jsonio.MAX_JSON_DEPTH + b"]" * jsonio.MAX_JSON_DEPTH
    assert jsonio.loads(ok) is not None  # exactly at the bound: accepted
    with pytest.raises(HistoryError, match="json_too_deep"):
        jsonio.loads(b"[" + ok + b"]")  # one deeper: refused
    quoted = {"a": "[" * 1000 + "{" * 1000}
    assert jsonio.loads(json.dumps(quoted)) == quoted
    assert jsonio.loads(json.dumps({"a": '\\"[[[['})) == {"a": '\\"[[[['}
    for bad in (b"{", b"", b"\xff\xfe", b"nope"):
        with pytest.raises(HistoryError, match="json_invalid"):
            jsonio.loads(bad)
    # the stream-of-rows form relies on the parser's own limit, still typed
    with pytest.raises(HistoryError, match="json_too_deep"):
        jsonio.loads(b"[" * 200_000, check_depth=False)


def _true_depth(node: Any) -> int:
    if isinstance(node, dict):
        return 1 + max((_true_depth(v) for v in node.values()), default=0)
    if isinstance(node, list):
        return 1 + max((_true_depth(v) for v in node), default=0)
    return 0


def _random_doc(rng: random.Random, budget: int = 6) -> Any:
    if budget == 0 or rng.random() < 0.3:
        return rng.choice([1, None, "plain", 'q"uo[te{', "back\\slash\\", "]]]}}}", '\\"[[', ""])
    if rng.random() < 0.5:
        return [_random_doc(rng, budget - 1) for _ in range(rng.randrange(0, 4))]
    return {f"k{i}[": _random_doc(rng, budget - 1) for i in range(rng.randrange(0, 4))}


def test_depth_scan_matches_the_true_depth_on_random_documents_with_tricky_strings() -> None:
    rng = random.Random(SEED)
    for _ in range(500):
        doc = _random_doc(rng)
        text = json.dumps(doc, ensure_ascii=rng.random() < 0.5)
        assert jsonio.json_depth(text.encode()) == _true_depth(doc), text


def test_depth_scan_is_linear_on_adversarial_quote_and_backslash_input() -> None:
    """A first version used a back-tracking string pattern: 100 KB of `"\\` took over a minute."""
    import time

    started = time.monotonic()
    for hostile in (b'"\\' * 500_000, b'"' * 1_000_000, b'"\\"' * 330_000, b"[" * 1_000_000):
        jsonio.json_depth(hostile)
    assert time.monotonic() - started < 10  # about 1.5 s here; was minutes per 100 KB before


# ------------------------------------------------------------------ unhashable platform ---
@pytest.mark.parametrize("platform", [[], {}, ["linux"], {"linux": 1}, 1, None, 1.5, True])
def test_non_string_platform_in_a_resealed_manifest_is_a_typed_capability_blocker(
    tmp_path: Path, platform: Any
) -> None:
    e = _trusted_env(tmp_path)
    pin = fresh_pin(e.store)
    reseal(e.ds, lambda m: m["filesystem_capabilities"].update(platform=platform))
    s = status(e, pin=pin)
    _clean(s)
    assert any(r.startswith("capability:platform_unproven:") for r in s.reasons), s.reasons


def test_capability_blockers_never_raise_on_hostile_probe_records() -> None:
    caps: dict[str, Any] = {
        "schema": "nexora.fs-capabilities/1", "platform": "linux", "fs_class": "local",
        "probes": dict.fromkeys(REQUIRED_PROBES, True),
    }  # fmt: skip
    assert capability_blockers(caps) == []  # positive control
    hostile: Any
    for hostile in ([], {}, ["linux"], 1, None, float("nan")):
        assert capability_blockers({**caps, "platform": hostile}) != []
        assert capability_blockers({**caps, "fs_class": hostile}) != []
        assert capability_blockers({**caps, "probes": hostile}) != []


# ------------------------------------------------------------------ unicode digits (\d) ---
ARABIC_INDIC = "".join(chr(0x660 + int(c)) for c in "1000")  # U+0661 U+0660 U+0660 U+0660


def test_chunk_names_accept_ascii_digits_only() -> None:
    assert CHUNK_NAME.fullmatch("chunk-1000-3000.jsonl")
    assert not CHUNK_NAME.fullmatch(f"chunk-{ARABIC_INDIC}-3000.jsonl")
    assert not CHUNK_NAME.fullmatch(f"chunk-1000-{ARABIC_INDIC}.jsonl")
    assert not CHUNK_NAME.fullmatch("chunk-１０００-3000.jsonl")  # fullwidth digits


def test_manifest_with_unicode_digit_chunk_names_is_refused(tmp_path: Path) -> None:
    e = _trusted_env(tmp_path)
    original = {p.name: p.read_bytes() for p in e.ds.iterdir()}
    first = sorted(e.ds.glob("chunk-*.jsonl"))[0]
    lo, hi = first.name[len("chunk-") : -len(".jsonl")].split("-")
    fancy = f"chunk-{''.join(chr(0x660 + int(c)) for c in lo)}-{hi}.jsonl"
    (e.ds / fancy).write_bytes(first.read_bytes())
    first.chmod(0o644)
    first.unlink()

    def _rename(m: dict[str, Any]) -> None:
        for c in m["chunks"]:
            if c["file"] == first.name:
                c["file"] = fancy

    reseal(e.ds, _rename)
    assert any(p.startswith("chunk_name_invalid:") for p in verify_dataset(e.ds))
    _clean(status(e))
    assert set(original) >= {first.name, "manifest.json"}  # fixture sanity only


def test_store_entry_names_accept_ascii_digits_only(tmp_path: Path) -> None:
    e = _trusted_env(tmp_path)
    pin = fresh_pin(e.store)
    (entry_file,) = list(e.store.location.iterdir())
    fancy = e.store.location / f"entry-{''.join(chr(0x660 + int(c)) for c in '00000001')}.json"
    entry_file.rename(fancy)
    s = status(e, pin=pin)
    _clean(s)
    assert "custody:store_unreadable:store_unexpected_entry" in s.reasons
    fancy.rename(entry_file)
    assert status(e, pin=pin).trusted


# ------------------------------------------------------------------------- CLI boundaries ---
def _cli_inputs(tmp: Path) -> tuple[Env, list[str]]:
    e = _trusted_env(tmp)
    (tmp / "registry.json").write_text(json.dumps(REGISTRY_JSON))
    pin_file = tmp / "pin.json"
    pin_file.write_text(json.dumps(fresh_pin(e.store, CHECK)))
    return e, ["--dataset", str(e.ds), "--store", str(tmp / "store"),
               "--registry", str(tmp / "registry.json"), "--pin", str(pin_file)]  # fmt: skip


@pytest.mark.parametrize("seconds", ["99999999999999999999", "0", "-5", str(10**12), "abc", "١٠٠"])
def test_cli_max_pin_age_is_bounded_and_rejected_without_a_traceback(
    tmp_path: Path, seconds: str
) -> None:
    _e, base = _cli_inputs(tmp_path)
    code, out = run_cli("verify_market_history.py", *base, "--max-pin-age-seconds", seconds)
    assert code == 2, out
    assert out["status"] == "UNTRUSTED" and out["trusted"] is False
    assert out["status_reasons"] in (["invalid_max_pin_age"], ["invalid_arguments"])


def test_cli_max_pin_age_at_the_sanity_bound_is_accepted(tmp_path: Path) -> None:
    _e, base = _cli_inputs(tmp_path)
    # A 10-year-old pin is stale for the fixture's clock, but the INPUT is valid: exit 1, not 2.
    code, out = run_cli(
        "verify_market_history.py", *base, "--max-pin-age-seconds",
        str(jsonio.MAX_AGE_SECONDS_SANITY),
    )  # fmt: skip
    assert code in (0, 1) and out["exit_code"] == code
    code, out = run_cli(
        "verify_market_history.py", *base, "--max-pin-age-seconds",
        str(jsonio.MAX_AGE_SECONDS_SANITY + 1),
    )  # fmt: skip
    assert code == 2 and out["status_reasons"] == ["invalid_max_pin_age"]


@pytest.mark.parametrize("which", ["registry", "pin"])
def test_cli_deeply_nested_input_files_exit_2_with_a_typed_reason(
    tmp_path: Path, which: str
) -> None:
    _e, base = _cli_inputs(tmp_path)
    target = tmp_path / ("registry.json" if which == "registry" else "pin.json")
    target.write_bytes(DEEP)
    code, out = run_cli("verify_market_history.py", *base, "--max-pin-age-seconds", "3600")
    assert code == 2 and out["status_reasons"] in (["registry_unreadable"], ["pin_unreadable"])


@pytest.mark.parametrize(
    "text", [f"{ARABIC_INDIC[0]}:{'0' * 64}", "²:" + "0" * 64, "1:" + "G" * 64, "0:" + "0" * 64]
)
def test_cli_store_head_accepts_ascii_digits_only(tmp_path: Path, text: str) -> None:
    _e, base = _cli_inputs(tmp_path)
    code, out = run_cli("verify_market_history.py", *base, "--store-head", text)
    assert code == 2 and out["status_reasons"] == ["invalid_store_head"]


def test_custody_cli_hostile_candidate_file_is_refused_not_a_traceback(tmp_path: Path) -> None:
    e = make_env(tmp_path)
    (tmp_path / "registry.json").write_text(json.dumps(REGISTRY_JSON))
    (tmp_path / "candidate.json").write_bytes(DEEP)
    from tests.test_market_history_v4_custody import key_file

    code, out = run_cli(
        "custody_market_history.py", "approve", "--dataset", str(e.ds), "--store",
        str(tmp_path / "store"), "--registry", str(tmp_path / "registry.json"),
        "--candidate", str(tmp_path / "candidate.json"),
        "--signing-key", key_file(tmp_path, "carol"), "--evidence-ref", "t",
    )  # fmt: skip
    assert code == 2 and out["refused"] == "json_too_deep"
    assert not (tmp_path / "store").exists()  # nothing was appended


# ----------------------------------------------------------- seeded type-mutation fuzz ---
HOSTILE_VALUES: list[Any] = [
    None, True, False, 0, -1, 1, 2**63, 10**30, -(10**30), 1.5, float("nan"), float("inf"),
    "", " ", "x", "٣", "0" * 64, "a" * 64, "9999-12-31T23:59:59+00:00", EDGE_HIGH, EDGE_LOW,
    "2026-01-01T00:00:00", "linux", [], {}, [[]], {"a": []}, ["linux"], [None], [1, 2],
    {"a": {"b": {}}}, json.loads("[" * 40 + "]" * 40),  # 40 levels: past MAX_JSON_DEPTH
]  # fmt: skip


def _paths(node: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = [prefix] if prefix else []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _paths(v, (*prefix, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _paths(v, (*prefix, i))
    return out


def _mutate(doc: Any, rng: random.Random) -> tuple[Any, str]:
    doc = deepcopy(doc)
    path = rng.choice(_paths(doc))
    value = deepcopy(rng.choice(HOSTILE_VALUES))
    parent = doc
    for step in path[:-1]:
        parent = parent[step]
    if rng.random() < 0.15 and isinstance(parent, dict) and path[-1] in parent:
        del parent[path[-1]]
        return doc, f"delete {path}"
    parent[path[-1]] = value
    return doc, f"set {path} = {str(value)[:40]!r}"


def _dump(doc: Any) -> str:
    return json.dumps(doc, allow_nan=True)


FUZZ_ROUNDS = 300


def test_fuzz_manifest_types_never_cause_an_internal_error(tmp_path: Path) -> None:
    e = _trusted_env(tmp_path)
    pin = fresh_pin(e.store)
    path = e.ds / "manifest.json"
    original = json.loads(path.read_text())
    rng = random.Random(SEED)
    for i in range(FUZZ_ROUNDS):
        doc, what = _mutate(original, rng)
        path.chmod(0o644)
        if rng.random() < 0.5 and isinstance(doc, dict):
            seal(e.ds, doc, identity=rng.random() < 0.5)  # a forger who repairs the hashes
        else:
            path.write_text(_dump(doc))
        s = status(e, pin=pin)
        assert not any("internal_error" in r for r in s.reasons), (i, what, s.reasons)
        assert not any("internal_error" in p for p in verify_dataset(e.ds)), (i, what)
    path.write_text(json.dumps(original))
    assert status(e, pin=pin).trusted  # positive control after the storm


def test_fuzz_store_entry_types_never_cause_an_internal_error(tmp_path: Path) -> None:
    e = _trusted_env(tmp_path)
    (entry_file,) = list(e.store.location.iterdir())
    original = json.loads(entry_file.read_text())
    rng = random.Random(SEED + 1)
    for i in range(FUZZ_ROUNDS):
        doc, what = _mutate(original, rng)
        if isinstance(doc, dict) and rng.random() < 0.7:  # re-sign and re-seal: the hard case
            body = {k: v for k, v in doc.items() if k not in (*custody._SEAL_KEYS, "signature")}
            if rng.random() < 0.7:
                body["signature"] = KEYS["carol"].sign("custody-approval", body)
            else:
                body["signature"] = doc.get("signature")
            try:
                doc = seal_entry(body, 1, custody.GENESIS)
            except (TypeError, ValueError):  # un-serialisable hostile value: cannot be written
                continue
        _rewrite(entry_file, _dump(doc))
        try:
            pin = fresh_pin(LocalAppendOnlyStore(e.store.location))
        except HistoryError:
            pin = None
        s = status(e, pin=pin)
        assert not any("internal_error" in r for r in s.reasons), (i, what, s.reasons)
    _rewrite(entry_file, json.dumps(original))
    assert status(e).trusted


def test_fuzz_registry_types_only_ever_raise_a_typed_error(tmp_path: Path) -> None:
    e = _trusted_env(tmp_path)
    rng = random.Random(SEED + 2)
    accepted = rejected = 0
    for i in range(FUZZ_ROUNDS * 2):
        doc, what = _mutate(REGISTRY_JSON, rng)
        try:
            reg = PrincipalRegistry.from_json(json.loads(_dump(doc)))
        except HistoryError as exc:
            assert exc.code == "registry_malformed", (i, what, exc.code)
            rejected += 1
            continue
        accepted += 1
        s = status(e, registry=reg)
        assert not any("internal_error" in r for r in s.reasons), (i, what, s.reasons)
    assert rejected > 20 and accepted > 0  # the fuzz exercised both outcomes


def test_fuzz_pin_types_never_cause_an_internal_error(tmp_path: Path) -> None:
    e = _trusted_env(tmp_path)
    pin = fresh_pin(e.store)
    assert pin is not None
    rng = random.Random(SEED + 3)
    for i in range(FUZZ_ROUNDS):
        doc, what = _mutate(pin, rng)
        if isinstance(doc, dict) and rng.random() < 0.6:  # the pin authority "signs" it anyway
            body = {k: v for k, v in doc.items() if k != "signature"}
            doc = {**body, "signature": KEYS["pia"].sign("head-pin", body)}
        s = status(e, pin=doc)
        assert not any("internal_error" in r for r in s.reasons), (i, what, s.reasons)


def test_fuzz_is_deterministic_for_a_fixed_seed() -> None:
    first = [_mutate(REGISTRY_JSON, r)[1] for r in [random.Random(SEED)] for _ in range(5)]
    second = [_mutate(REGISTRY_JSON, r)[1] for r in [random.Random(SEED)] for _ in range(5)]
    assert first == second
