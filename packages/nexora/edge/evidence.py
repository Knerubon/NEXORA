"""Evidence gates: four separate facts that must never be treated as one.

1. dataset integrity verified      - hashes, structure and the acceptance policy re-applied.
2. provenance independently verified - the data really came from the declared market source.
3. holdout evaluation authorized   - someone with authority approved reading the holdout.
4. statistical evidence eligible   - all of the above, so a result may support a claim.

Only (1) can be established inside this library. (2) and (3) need a mechanism that does not
exist yet (an independently reviewable provenance record and an authorization service). The
caller-supplied `Provenance.data_class`, adapter references, `HoldoutUnlock` objects and the
in-memory `HoldoutAccessLog` are declarations, not proof, so they cannot satisfy those gates.
Until such a mechanism is designed and reviewed, (2), (3) and therefore (4) are False.

Do not add a code path that sets these to True from caller-supplied data. When a real
mechanism exists, extend `evaluate_gates` only, and change its tests in the same reviewed PR.
"""

from __future__ import annotations

from dataclasses import dataclass

NO_PROVENANCE_MECHANISM = "no_independent_provenance_verification_mechanism"
NO_AUTHORIZATION_MECHANISM = "no_independent_holdout_authorization_mechanism"
INTEGRITY_NOT_VERIFIED = "dataset_integrity_not_verified"


class EvidenceGateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceGates:
    dataset_integrity_verified: bool
    provenance_independently_verified: bool
    holdout_evaluation_authorized: bool
    statistical_evidence_eligible: bool
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        required = (
            self.dataset_integrity_verified
            and self.provenance_independently_verified
            and self.holdout_evaluation_authorized
        )
        if self.statistical_evidence_eligible != required:
            raise EvidenceGateError("eligibility_must_equal_all_gates")
        if self.statistical_evidence_eligible and self.blockers:
            raise EvidenceGateError("eligible_with_blockers")


def evaluate_gates(*, dataset_integrity_verified: bool) -> EvidenceGates:
    """The only place gate values are decided. Provenance and holdout authorization are
    constants (False) because no independent mechanism exists to establish them."""
    blockers: list[str] = []
    if not dataset_integrity_verified:
        blockers.append(INTEGRITY_NOT_VERIFIED)
    blockers.extend((NO_PROVENANCE_MECHANISM, NO_AUTHORIZATION_MECHANISM))
    return EvidenceGates(
        dataset_integrity_verified=dataset_integrity_verified,
        provenance_independently_verified=False,
        holdout_evaluation_authorized=False,
        statistical_evidence_eligible=False,
        blockers=tuple(blockers),
    )
