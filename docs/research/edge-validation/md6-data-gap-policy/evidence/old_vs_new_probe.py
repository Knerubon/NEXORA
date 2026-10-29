"""Behavioural probe for the Wave V9 findings, run unchanged against two checkouts.

Usage: python old_vs_new_probe.py <checkout root containing tests/>  (prints JSON rows).
Uses only API that exists at BOTH PR #94 head 930af2aef829fa14064d0ad0be0cfa4bda12fbec and the V9
head, so the same file measures both. SYNTHETIC data only; no market data, no network.
"""

import json
import sys
from dataclasses import replace
from itertools import permutations

root = sys.argv[1]
sys.path.insert(0, root)

from tests import md6_cross_track_reference as X  # noqa: E402
from tests import md6_quarantine_reference as C  # noqa: E402
from tests.md6_exposure_reference import (  # noqa: E402
    MultiplicityDeclaration,
    append,
    classify_evaluation,
    classify_multiplicity_set,
)
from tests.test_md6_exposure_lineage import LINEAGE, Book, tgt  # noqa: E402
from tests.test_md6_exposure_n1_remediation import (  # noqa: E402
    BASE,
    SYN_ALIAS,
    SYN_INST,
    _book_for,
    _target,
    _ticks,
)

rows = []


def add(rid, finding, what, outcome):
    rows.append(
        {
            "id": rid,
            "finding": finding,
            "scenario": what,
            "outcome": getattr(outcome, "value", outcome),
        }
    )


def cls(book, eid, lineage=LINEAGE):
    return classify_evaluation((book.ledger,), book.anchors(), lineage, eid).outcome


def declare(book, did, members, quant, corr):
    ts = book._tick()
    book.ledger = append(book.ledger, MultiplicityDeclaration(did, members, quant, corr, ts), ts)


def disjoint_book(quant="QA-SYN", corr="CORR-SYN", declared=True, members=("a", "b", "c")):
    book = Book()
    win = {"a": (0, 100), "b": (100, 200), "c": (200, 300)}
    for v in members:
        book.register(f"P-{v}", (tgt(*win[v]),), policy=f"POL-{v}", family=f"FAM-{v}")
    if declared:
        declare(book, "D-1", tuple(f"P-{v}" for v in members), quant, corr)
    for v in members:
        book.evaluate(f"E-{v}", f"P-{v}", (tgt(*win[v]),), policy=f"POL-{v}", family=f"FAM-{v}")
    return book


def setcls(book):
    return classify_multiplicity_set((book.ledger,), book.anchors(), LINEAGE, "D-1").outcome


# --- F-2
for label, q, c in (
    ("whitespace", " ", " "),
    ("x", "x", "x"),
    ("NONE", "NONE", "NONE"),
    ("tab", "\t", "\t"),
    ("well-formed free text", "QA-SYN", "CORR-SYN"),
):
    add(
        f"F2-{label}",
        "F-2",
        f"declared set, authority/correction refs = {label!r}, default (no verifier)",
        setcls(disjoint_book(q, c)),
    )
# --- F-3
b = disjoint_book()
add(
    "F3-member",
    "F-3",
    "declared member, disjoint footprint, cited alone (declaration before runs)",
    cls(b, "E-a"),
)
b = disjoint_book(declared=False)
add("F3-ctrl", "CONTROL", "undeclared disjoint registration cited alone", cls(b, "E-a"))
# F-3 permutations: every eval order
res = set()
for order in permutations("abc"):
    bk = Book()
    win = {"a": (0, 100), "b": (100, 200), "c": (200, 300)}
    for v in "abc":
        bk.register(f"P-{v}", (tgt(*win[v]),), policy=f"POL-{v}", family=f"FAM-{v}")
    declare(bk, "D-1", ("P-a", "P-b", "P-c"), "QA-SYN", "CORR-SYN")
    for v in order:
        bk.evaluate(f"E-{v}", f"P-{v}", (tgt(*win[v]),), policy=f"POL-{v}", family=f"FAM-{v}")
    res.add(tuple(sorted((v, cls(bk, f"E-{v}").value) for v in "abc")))
add(
    "F3-perm",
    "F-3",
    "6 evaluation orders, members cited alone: distinct outcome vectors",
    "; ".join(str(r) for r in sorted(res)),
)


# --- F-4
def relabel(copy, rev=False):
    known = [X.KnownDataset(SYN_INST, BASE), X.KnownDataset(SYN_ALIAS, copy)]
    if rev:
        known.reverse()
    bk = _book_for(_target(SYN_INST, BASE, "DS-1"), _target(SYN_ALIAS, copy, "DS-2"))
    return bk, X.lineage_registry(known)


changes = {
    "flags dropped": tuple(replace(r, flags=None) for r in BASE),
    "flags rewritten": tuple(replace(r, flags="2") for r in BASE),
    "trailing zeros padded": tuple(replace(r, bid=r.bid + "00", ask=r.ask + "00") for r in BASE),
    "flags dropped+padded+reordered": tuple(
        reversed(tuple(replace(r, flags=None, bid=r.bid + "00", ask=r.ask + "00") for r in BASE))
    ),
    "subset with flags dropped": tuple(replace(r, flags=None) for r in BASE[2:6]),
}
for name, copy in changes.items():
    bk, lin = relabel(copy)
    add(
        f"F4-{name}",
        "F-4",
        f"E0 on original (A), copy ({name}) relabelled (B): copy's run",
        cls(bk, "E2", lin),
    )
other = tuple(C.TickRecord(r.raw_msc, "2" + r.bid[1:], "2" + r.ask[1:], r.flags) for r in BASE)
bk, lin = relabel(other)
add(
    "F4-ctrl-prices",
    "CONTROL",
    "relabelled instrument, same time grid, different prices",
    cls(bk, "E2", lin),
)
bk, lin = relabel(_ticks(8, start=10_000))
add("F4-ctrl-fresh", "CONTROL", "relabelled instrument, later disjoint period", cls(bk, "E2", lin))
bk, lin = relabel(tuple(reversed(BASE)))
bk2, lin2 = relabel(tuple(reversed(BASE)), rev=True)
add(
    "F4-registry-order",
    "F-4",
    "exact reordered relabelled copy: first run E1 under registry order AB / BA",
    f"{cls(bk, 'E1', lin).value} / {cls(bk2, 'E1', lin2).value}",
)
# --- F-1 default
b = Book()
b.register("P1", (tgt(0, 100),))
b.evaluate("E1", "P1", (tgt(0, 100),))
before = cls(b, "E1")
b.register("P1", (tgt(500, 600),))
add(
    "F1-default",
    "F-1",
    "E1 INDEPENDENT, then a duplicate preregistration id is appended: E1 now",
    cls(b, "E1"),
)
add("F1-before", "CONTROL", "same book before the invalid append", before)
# --- N1 regression
W = (tgt(0, 100),)
outs = set()
for ro in permutations("abc"):
    for eo in permutations("abc"):
        bk = Book()
        for v in ro:
            bk.register(f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
        for v in eo:
            bk.evaluate(f"E-{v}", f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
        outs.add(tuple(sorted((v, cls(bk, f"E-{v}").value) for v in "abc")))
add(
    "N1-36",
    "REGRESSION",
    "three overlapping variants, 36 registration x evaluation orders: distinct outcome vectors",
    "; ".join(str(o) for o in sorted(outs)),
)
print(json.dumps(rows, indent=1))
