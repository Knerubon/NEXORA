"""Allow-list classification of an observed MT5 module reference (finding V8B-2).

The Case 7 evidence lists the module attributes the extractor was observed touching. A DENY-list
that compares exact strings (`order_send`) misses `mt5.order_send`, `order_send()`, `ORDER_SEND`,
`order-send` and `orderSend`. This module therefore works the other way round:

    ALLOWED    the reference, after removal of the supported wrappers (surrounding whitespace, call
               syntax `name(...)`, a receiver in RECEIVERS such as `mt5.` / `MetaTrader5.`), equals
               a name in ALLOWED_MT5_CALLS EXACTLY (case-sensitive), and contains no hidden
               characters.
    FORBIDDEN  anything else whose loose spelling (NFKC, hidden characters removed, case-folded,
               look-alike letters mapped, separators removed) contains a known trade/account/book
               name; reported with the alias it matched.
    UNKNOWN    anything else. UNKNOWN counts exactly like FORBIDDEN: an observed name that is not
               on the allow-list is not evidence of read-only behaviour.

Supported representations: plain strings, qualified names (`mod.attr`, any depth), call syntax,
whitespace, separators, case, Unicode compatibility forms, zero-width/format characters, structured
references (`{"module": ..., "name": ...}` or a sequence of name parts).

Not claimed: this does not detect arbitrary code execution, dynamic attribute access performed
without a recorded name, or a recorder that lies. It classifies the NAMES a recording proxy
reported; the proxy and the real run remain Windows-only evidence (DEP-03).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from tests.windows_integration_readiness.text_hygiene import has_hidden, loose_key, strip_hidden
from tests.windows_integration_readiness.vectors import ALLOWED_MT5_CALLS, FORBIDDEN_MT5_NAMES

ALLOWED = "ALLOWED"
FORBIDDEN = "FORBIDDEN"
UNKNOWN = "UNKNOWN"
# Receivers a recorded reference may carry in front of the attribute (compared case-insensitively).
RECEIVERS = frozenset({"mt5", "metatrader5"})
_FORBIDDEN_LOOSE = {loose_key(n): n for n in FORBIDDEN_MT5_NAMES}
_CALL_SUFFIX = re.compile(r"\s*\(.*\)\s*$", re.DOTALL)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STRUCT_KEYS = frozenset({"module", "name", "attr", "attribute"})


@dataclass(frozen=True)
class Classification:
    status: str  # ALLOWED | FORBIDDEN | UNKNOWN
    canonical: str  # the attribute name after removing the supported wrappers ("" if none)
    key: str  # loose key, the unit used to count distinct non-allowed references
    matched_alias: str | None  # the known forbidden name this spelling matched, if any
    problems: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return self.status == ALLOWED


def _flatten(ref: object) -> tuple[str, tuple[str, ...]] | None:
    """Structured reference -> dotted text. None when the shape is not supported."""
    if isinstance(ref, str):
        return ref, ()
    if isinstance(ref, Mapping):
        if not ref or set(ref) - _STRUCT_KEYS or not all(isinstance(v, str) for v in ref.values()):
            return None
        name = ref.get("name", ref.get("attr", ref.get("attribute")))
        if not isinstance(name, str):
            return None
        module = ref.get("module")
        return (f"{module}.{name}" if module is not None else name), ("structured_reference",)
    if isinstance(ref, Sequence) and not isinstance(ref, bytes | bytearray):
        if ref and all(isinstance(p, str) for p in ref):
            return ".".join(ref), ("structured_reference",)
    return None


def classify_reference(ref: object) -> Classification:
    flat = _flatten(ref)
    if flat is None:
        return Classification(UNKNOWN, "", "", None, ("unsupported_reference_type",))
    raw, problems_t = flat
    problems = list(problems_t)
    text = raw.strip()
    if has_hidden(text):
        problems.append("hidden_characters")
    visible = strip_hidden(text)
    call = _CALL_SUFFIX.search(visible)
    body = visible[: call.start()] if call else visible
    parts = [p.strip() for p in body.split(".")]
    name = parts[-1]
    receivers = parts[:-1]
    canonical = name if _IDENT.fullmatch(name) else ""
    key = loose_key(raw)
    whole = loose_key(body) or key

    for loose, original in _FORBIDDEN_LOOSE.items():
        if loose in key:
            # one operation = one key, however it is spelled or qualified
            return Classification(FORBIDDEN, canonical, loose, original, tuple(problems))

    receiver_ok = all(r.casefold() in RECEIVERS for r in receivers) and len(receivers) <= 1
    if canonical in ALLOWED_MT5_CALLS and receiver_ok and not has_hidden(text):
        return Classification(ALLOWED, canonical, canonical, None, tuple(problems))
    if canonical in ALLOWED_MT5_CALLS and not receiver_ok:
        problems.append("receiver_not_mt5")
    elif not canonical:
        problems.append("not_a_plain_attribute_name")
    else:
        problems.append("not_on_allow_list")
    return Classification(UNKNOWN, canonical, whole, None, tuple(problems))


def non_allowed_keys(refs: Iterable[object]) -> frozenset[str]:
    """Distinct loose keys of every reference that is not ALLOWED (FORBIDDEN and UNKNOWN alike)."""
    return frozenset(c.key or "?" for r in refs if not (c := classify_reference(r)).allowed)


def mentions_forbidden_name(text: str) -> str | None:
    """The known trade/account/book name whose loose spelling occurs inside `text`, if any."""
    key = loose_key(text)
    return next((orig for loose, orig in _FORBIDDEN_LOOSE.items() if loose in key), None)
