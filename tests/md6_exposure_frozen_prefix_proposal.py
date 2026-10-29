"""MD-6 exposure lineage: FROZEN-PREFIX ALTERNATIVE (semantics A). PROPOSAL, NOT THE DEFAULT.

STATUS: BLOCKED pending a human Quant / governance decision (QE-9 in
``docs/research/edge-validation/md6-data-gap-policy/EVALUATION-EXPOSURE-LINEAGE.md`` section 5.5.1).
Test-only reference model; not runtime enforcement. Production runtime enforcement: NOT VERIFIED.

The default model (``tests/md6_exposure_reference.py``) is "monotone non-improving": whole-ledger
validity and the lineage registry are ledger-wide, fail-closed inputs, so a later invalid append
or a later partially overlapping dataset turns an earlier INDEPENDENT into UNVERIFIED (finding
F-1).

This module shows what the alternative would look like, so that the Quant can compare the two with
executable evidence and decide. It does NOT decide anything and no document may cite it as policy.

Semantics A (frozen prefix):

* an evaluation's outcome is computed from the ledger prefix ending at the evaluation only;
* whole-ledger problems (a later invalid append, replica divergence after the evaluation) are
  reported next to the outcome (``ledger_problems``) as an integrity alarm and never change it;
* lineage is taken as captured at the evaluation's position (``lineage_at``). A real implementation
  would have to record a lineage-snapshot digest inside the ledger entry. That is a schema change
  to a shared contract (AGENTS.md section 3) and depends on QE-1 / QE-6, so it is NOT made here;
* later declarations do not downgrade an earlier standalone outcome (the declaration is not in the
  prefix); the set outcome is still EXPLORATORY ("declaration recorded after a member evaluation").

What A gives up: a previously issued INDEPENDENT stands even after tampering with later entries
has been detected (it is only reported in ``ledger_problems``), whereas the default voids it.
Whether previously issued outcomes may ever be revised is exactly the decision requested from the
Quant.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from tests import md6_exposure_reference as B


@dataclass(frozen=True)
class FrozenAssessment:
    assessment: B.Assessment
    ledger_problems: tuple[str, ...]  # integrity alarm over the whole ledger; never alters outcome


def _links_ok(ledger: B.Ledger, upto: int) -> bool:
    """Hash links and positions of entries 0..upto only (no id-uniqueness or semantic checks)."""
    prev = B.GENESIS_PREV_HASH
    for index, entry in enumerate(ledger[: upto + 1]):
        if entry.position != index or entry.prev_hash != prev:
            return False
        if entry.entry_hash != B.entry_hash(
            entry.position, entry.prev_hash, entry.recorded_at, entry.record
        ):
            return False
        prev = entry.entry_hash
    return len(ledger) > upto


def _unverified(*reasons: str, problems: tuple[str, ...] = ()) -> FrozenAssessment:
    return FrozenAssessment(B.Assessment(B.Outcome.UNVERIFIED, tuple(reasons)), problems)


def classify_evaluation_prefix_frozen(
    replicas: Sequence[B.Ledger],
    anchors: Sequence[B.LedgerAnchor],
    lineage: B.LineageRegistry,
    evaluation_id: str,
    lineage_at: Callable[[int], B.LineageRegistry] | None = None,
) -> FrozenAssessment:
    """Outcome of one evaluation under semantics A. See the module docstring."""
    if not replicas or not any(replicas):
        return _unverified("no ledger replica supplied: exposure history missing")
    alarm = B.reconcile(replicas)[1]
    first: dict[int, tuple[int, str]] = {}
    for number, replica in enumerate(replicas):
        for entry in replica:
            if (
                isinstance(entry.record, B.Evaluation)
                and entry.record.evaluation_id == evaluation_id
            ):
                first[number] = (entry.position, entry.entry_hash)
                break
    if not first or len(set(first.values())) != 1:
        return _unverified(
            f"evaluation {evaluation_id} not found, or replicas disagree on it", problems=alarm
        )
    position = next(iter(first.values()))[0]
    holder = replicas[next(iter(first))]
    prefix = holder[: position + 1]
    problems = B.verify_chain(prefix)
    if problems:
        return _unverified(*problems, problems=alarm)
    for replica in replicas:
        for entry in replica[: position + 1]:
            if entry.entry_hash != prefix[entry.position].entry_hash:
                return _unverified(
                    f"replica disagrees with the prefix at position {entry.position}",
                    problems=alarm,
                )
    covering = False
    for anchor in anchors:
        verified = any(
            len(r) > anchor.position
            and _links_ok(r, anchor.position)
            and r[anchor.position].entry_hash == anchor.entry_hash
            for r in replicas
        )
        if anchor.position <= position and not verified:
            return _unverified(
                f"anchor at position {anchor.position} disagrees with the ledger", problems=alarm
            )
        covering = covering or (anchor.position >= position and verified)
    if not covering:
        return _unverified(f"no independent anchor covers position {position}", problems=alarm)
    snapshot = lineage_at(position) if lineage_at is not None else lineage
    assessment = B._assess(prefix, snapshot, prefix[position])
    if assessment.outcome is B.Outcome.UNVERIFIED and any(
        "not in the ledger" in r for r in assessment.reasons
    ):
        record = prefix[position].record
        assert isinstance(record, B.Evaluation)
        longest = max(replicas, key=len)
        later = [
            e
            for e in longest[position + 1 :]
            if isinstance(e.record, B.Registration)
            and e.record.preregistration_id == record.preregistration_id
        ]
        if later:
            assessment = B.Assessment(
                B.Outcome.REJECTED_RETROSPECTIVE,
                (
                    f"registration at position {later[0].position} is after the evaluation it "
                    f"covers at position {position}",
                ),
            )
    return FrozenAssessment(assessment, alarm)
