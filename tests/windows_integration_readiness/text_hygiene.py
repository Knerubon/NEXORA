"""Text normalisation shared by the evidence validator and the MT5 reference classifier.

Everything here is a DENY-LIST or a normalisation, and a deny-list over free text is inherently
incomplete: it cannot enumerate every spelling of "missing" or every mock marker. It is a
defence in depth. The positive checks (required evidence must match its expected structure:
hashes, signatures, enumerations, allow-lists) are what a record actually has to satisfy.

Pure Python, no I/O.
"""

from __future__ import annotations

import re
import unicodedata

# Lowercase look-alikes (Cyrillic, Greek) folded to ASCII before comparison. NFKC does not do this.
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
        "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ӏ": "l",
        "ԁ": "d", "ѡ": "w", "ё": "e",
        "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t",
        "υ": "u", "χ": "x", "η": "n", "γ": "y", "β": "b", "μ": "m", "ϲ": "c",
    }
)  # fmt: skip


_HOMOGLYPHS_BOTH = {ord(k): v for k, v in ((chr(c), v) for c, v in _HOMOGLYPHS.items())}
_HOMOGLYPHS_BOTH.update({ord(chr(c).upper()): v.upper() for c, v in _HOMOGLYPHS.items()})


def ascii_lookalikes(text: str) -> str:
    """Map Cyrillic/Greek look-alike letters (either case) to ASCII, keeping the case."""
    return text.translate(_HOMOGLYPHS_BOTH)


def strip_hidden(text: str) -> str:
    """Remove control and format characters (zero-width space/joiner, BOM, bidi marks, ...)."""
    return "".join(c for c in text if not unicodedata.category(c).startswith("C"))


def has_hidden(text: str) -> bool:
    return any(unicodedata.category(c).startswith("C") for c in text)


def fold(text: str) -> str:
    """NFKC, hidden characters removed, case-folded, look-alike letters mapped to ASCII."""
    return strip_hidden(unicodedata.normalize("NFKC", text)).casefold().translate(_HOMOGLYPHS)


def loose_key(text: str) -> str:
    """`fold` with every non-alphanumeric character removed: `Order-Send`, `order send`,
    `ORDER_SEND` and `orderSend` all become `ordersend`."""
    return re.sub(r"[\W_]+", "", fold(text))


def within_one_edit(a: str, b: str) -> bool:
    """Damerau-Levenshtein distance <= 1 (insert, delete, substitute, adjacent transposition)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (
            len(diff) == 2
            and diff[1] == diff[0] + 1
            and a[diff[0]] == b[diff[1]]
            and a[diff[1]] == b[diff[0]]
        )
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1 :]


_PAREN = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")


def without_parentheticals(text: str) -> str:
    """`N/A (see notes)` -> `N/A `: a parenthetical remark does not make a placeholder real."""
    return _PAREN.sub(" ", text)
