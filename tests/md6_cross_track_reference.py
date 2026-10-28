"""MD-6 cross-track bridge: Track C content relations feeding Track B exposure (reference model).

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING). Test-only reference
model; not runtime enforcement.

Closes the gap reported as N3: the exposure model compared ``content_identity`` as an opaque key,
so a reordered copy, a subset or a copy under another canonicalization version could escape it.
This module derives the exposure model's inputs from QUARANTINE-IDENTITY.md content profiles:

* ``exposure_identity``: the exposure key is Track C's ``multiset_fingerprint`` under Track C's
  canonicalization version, so a reordered copy is the same exposure content (Track C MULTISET).
* ``content_relation``: Track C's record-hash relation (EXACT_ORDERED, MULTISET, SUBSET, SUPERSET,
  PARTIAL_OVERLAP, NONE).
* ``derive_lineage``: a dataset that is a SUBSET of a known dataset is DERIVED from it on its own
  time span (the superset side needs no edge: exposure flows both ways through the subset's edge).
  PARTIAL_OVERLAP with any known dataset cannot be mapped to a parent interval here, so its lineage
  is UNKNOWN and every exposure outcome touching it is UNVERIFIED.

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
    "EXACT_ORDERED", "MULTISET", "SUBSET", "SUPERSET", "PARTIAL_OVERLAP", "NONE"
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
    """Registry over ``datasets``; each one is related to every other one."""
    registry: dict[B.ContentIdentity, B.DatasetLineage] = {}
    for index, d in enumerate(datasets):
        others = [o for j, o in enumerate(datasets) if j != index]
        node = derive_lineage(d.instrument_id, d.records, others)
        existing = registry.get(node.content_identity)
        if existing is not None and existing.instrument_id != node.instrument_id:
            # Same content under two instrument labels: keep a conflicting record so that the
            # exposure model's instrument check fails closed for the relabelled one.
            continue
        registry[node.content_identity] = node
    return registry
