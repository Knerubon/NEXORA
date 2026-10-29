"""MD-6 cross-track bridge: Track C content relations feeding Track B exposure (reference model).

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING). Test-only reference
model; not runtime enforcement.

Closes the gap reported as N3: the exposure model compared ``content_identity`` as an opaque key,
so a reordered copy, a subset or a copy under another canonicalization version could escape it.
This module derives the exposure model's inputs from QUARANTINE-IDENTITY.md content profiles:

* ``exposure_identity``: the exposure key is Track C's ``multiset_fingerprint`` under Track C's
  canonicalization version, so a reordered copy is the same exposure content (Track C MULTISET).
* ``content_relation``: Track C's record-hash relation (EXACT_ORDERED, MULTISET, SUBSET, SUPERSET,
  PARTIAL_OVERLAP, NONE) plus REPRESENTATION_DRIFT (V9 F-4): the two datasets share more records
  under Track C's flags-insensitive, trailing-zero-insensitive shadow key than under exact record
  hashes, so one re-expresses the other's values (flags dropped or rewritten, trailing zeros).
* ``derive_lineage``: a dataset that is a SUBSET of a known dataset is DERIVED from it on its own
  time span (the superset side needs no edge: exposure flows both ways through the subset's edge).
  PARTIAL_OVERLAP with any known dataset cannot be mapped to a parent interval here, so its lineage
  is UNKNOWN and every exposure outcome touching it is UNVERIFIED.
  REPRESENTATION_DRIFT under a DIFFERENT instrument label is treated the same way (UNKNOWN): a
  trivial representation change must not let a relabelled copy escape exposure. Whether two
  instrument labels denote the same instrument is NOT defined by any approved contract (ADR-025
  fixes canonical ids, not equivalence), so the bridge reports the unresolved mapping as UNKNOWN
  lineage instead of guessing. Under the SAME label the instrument key already carries exposure.
  Copies changed by anything outside the shadow key (time shift, price scaling) are NOT detected.

Time is the synthetic tick ``raw_msc`` treated as verified UTC ms. Real data would need MD-2
(offset) first; nothing here asserts that mapping.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from tests import md6_exposure_reference as B
from tests import md6_quarantine_reference as C

ContentRelation = Literal[
    "EXACT_ORDERED",
    "MULTISET",
    "SUBSET",
    "SUPERSET",
    "PARTIAL_OVERLAP",
    "REPRESENTATION_DRIFT",
    "NONE",
]


@dataclass(frozen=True)
class KnownDataset:
    """A dataset already known to the lineage registry (records plus its canonical instrument)."""

    instrument_id: str
    records: tuple[C.TickRecord, ...]


def exposure_identity(
    records: Sequence[C.TickRecord], canonicalization_version: str = C.CANONICALIZATION_VERSION
) -> B.ContentIdentity:
    """Exposure key of ``records``. A version other than Track C's current one is kept as declared
    so that the exposure model sees a CANONICALIZATION_CONFLICT and fails closed."""
    profile = C.content_profile(records)
    return B.ContentIdentity(profile.identity.multiset_fingerprint, canonicalization_version)


def span(records: Sequence[C.TickRecord]) -> tuple[int, int]:
    """Half-open [first raw_msc, last raw_msc + 1) of a non-empty dataset."""
    if not records:
        raise B.LineageUnresolvable("empty dataset has no span")
    times = [r.raw_msc for r in records]
    return min(times), max(times) + 1


def content_relation(a: Sequence[C.TickRecord], b: Sequence[C.TickRecord]) -> ContentRelation:
    """Relation of ``a`` to ``b`` by Track C per-record hashes (multiset semantics)."""
    pa, pb = C.content_profile(a), C.content_profile(b)
    if pa.identity.ordered_content_hash == pb.identity.ordered_content_hash:
        return "EXACT_ORDERED"
    if pa.identity.multiset_fingerprint == pb.identity.multiset_fingerprint:
        return "MULTISET"
    ca, cb = Counter(pa.record_hashes), Counter(pb.record_hashes)
    exact_shared = sum((ca & cb).values())
    shadow_shared = sum(
        (Counter(pa.shadow_record_hashes) & Counter(pb.shadow_record_hashes)).values()
    )
    if shadow_shared > exact_shared:
        # Some records of one dataset re-express values of the other without being equal to them.
        return "REPRESENTATION_DRIFT"
    if not ca & cb:
        return "NONE"
    if not ca - cb:
        return "SUBSET"
    if not cb - ca:
        return "SUPERSET"
    return "PARTIAL_OVERLAP"


def derive_lineage(
    instrument_id: str, records: Sequence[C.TickRecord], known: Sequence[KnownDataset]
) -> B.DatasetLineage:
    """Lineage record of ``records`` relative to every dataset in ``known``."""
    ident = exposure_identity(records)
    start, end = span(records)
    parents: list[B.LineageEdge] = []
    unknown = False
    for k in known:
        relation = content_relation(records, k.records)
        if relation in ("EXACT_ORDERED", "MULTISET"):
            # Same exposure content: the identity itself carries it. A different instrument label
            # for the same content is caught by the exposure model as an identity conflict.
            continue
        if relation == "SUBSET":
            parents.append(B.LineageEdge(exposure_identity(k.records), k.instrument_id, start, end))
        elif relation == "PARTIAL_OVERLAP":
            unknown = True
        elif relation == "REPRESENTATION_DRIFT" and k.instrument_id != instrument_id:
            unknown = True
        # SUPERSET: ``k`` is the subset and carries the edge to this dataset; exposure then flows
        # both ways through that edge (X3).
    if unknown:
        return B.DatasetLineage(ident, instrument_id, start, end, "UNKNOWN")
    if parents:
        return B.DatasetLineage(ident, instrument_id, start, end, "DERIVED", tuple(parents))
    return B.DatasetLineage(ident, instrument_id, start, end, "ROOT")


def lineage_registry(
    datasets: Sequence[KnownDataset],
) -> dict[B.ContentIdentity, B.DatasetLineage]:
    """Registry over ``datasets``; each one is related to every other one.

    The result does not depend on the order of ``datasets`` (V9 F-4 permutation tests). Identical
    content under two instrument labels is ambiguous: BOTH sides become UNKNOWN (instrument
    equivalence is not defined by any approved contract), so every outcome touching it is
    UNVERIFIED whichever dataset was listed first.
    """
    registry: dict[B.ContentIdentity, B.DatasetLineage] = {}
    labels: dict[B.ContentIdentity, set[str]] = {}
    for index, d in enumerate(datasets):
        others = [o for j, o in enumerate(datasets) if j != index]
        node = derive_lineage(d.instrument_id, d.records, others)
        labels.setdefault(node.content_identity, set()).add(node.instrument_id)
        registry[node.content_identity] = node
    for identity, seen in labels.items():
        if len(seen) > 1:
            node = registry[identity]
            registry[identity] = B.DatasetLineage(
                identity, min(seen), node.start_ms, node.end_ms, "UNKNOWN"
            )
    return registry
