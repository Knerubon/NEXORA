"""Readiness-checklist gate for V4 custody evidence (finding B-M2).

This is NOT a reimplementation of PR #96's custody code and it grants nothing. It encodes what the
PR #95 checklist (phase D) must refuse before it may even RECORD a verifier's TRUSTED outcome, using
only interfaces read from PR #96 at `V4_REF_SHA` (scripts/verify_market_history.py,
scripts/custody_market_history.py, custody.py, fsutil.py, lock.py). Every constant below is quoted
from that SHA and must be re-verified at the SHA that actually runs (checklist A3).

The gate never returns "TRUSTED". Its best outcome is RECORDABLE_PENDING_REVIEW, and while the V4
custody baseline itself is not independently approved (DEP-01: PR #96 Reviewer A, 3 MAJOR findings)
every bundle is additionally BLOCKED.

Pure Python, no I/O, no `nexora` imports.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

V4_REF_SHA = "d6a7cda72a88600e2ccf6554605f1c50478893ea"
# scripts/verify_market_history.py argparse flags, as read at V4_REF_SHA.
V4_VERIFY_FLAGS = frozenset(
    {"--dataset", "--anchor", "--store", "--registry", "--store-head", "--require-trusted"}
)
# scripts/custody_market_history.py subcommands, as read at V4_REF_SHA.
V4_CUSTODY_SUBCOMMANDS = ("approve", "revoke", "head")
V4_EXTRACTOR_VERSION = "nexora.market-history-extractor/5"  # extract.EXTRACTOR_VERSION
V4_MANIFEST_SCHEMA = "nexora.market-history/4"  # extract.SCHEMA_VERSION
V4_PROVEN_PLATFORMS = frozenset({"linux"})  # fsutil.PROVEN_PLATFORMS
# Set to True only by an independent review that approves PR #96's custody design (DEP-01).
V4_BASELINE_APPROVED = False

# Windows filesystem/lock behaviours nobody has observed (UNSUPPORTED-GUARANTEES.md). A bundle that
# claims any of them as verified without attached evidence is refused.
WINDOWS_UNVERIFIED = frozenset(
    {
        "windows_msvcrt_lock_exclusivity",
        "windows_ntfs_atomicity_and_hardlink_behaviour",
        "reparse_point_and_junction_handling",
        "windows_directory_fsync",
        "windows_delete_sharing_semantics",
    }
)

RECORDABLE = "RECORDABLE_PENDING_REVIEW"
BLOCKED = "BLOCKED"
STOP = "STOP"


@dataclass(frozen=True)
class CustodyBundle:
    """What the operator and verifier recorded. Field names mirror the evidence record."""

    verify_argv: tuple[str, ...]
    verifier_status: str  # printed by verify_market_history.py: TRUSTED | UNTRUSTED
    verifier_exit_code: int
    operator: str | None
    candidate_creator: str | None
    approver: str | None  # None = no approval entry for this dataset in the store
    approval_seq: int | None
    registry_ids: frozenset[str]  # every id and alias in the verifier-supplied registry
    revocation_seqs: tuple[int, ...]
    store_chain: str  # `custody_market_history.py head` -> "chain": "ok" or a problem
    store_head_seq_at_verification: int | None  # from `head`, run at verification time
    pinned_head_seq: int | None  # the SEQ of `--store-head SEQ:SHA256`, None if absent
    legacy_v3_anchor_only: bool  # custody evidence offered is a V3 anchor file
    manifest_schema: str
    extractor_version: str
    extraction_platform: str  # manifest filesystem_capabilities.platform
    writer_lock: str  # "acquired" or the refusal code observed
    claimed_verified_guarantees: frozenset[str] = frozenset()
    checklist_extractor_sha: str = ""
    run_extractor_sha: str = ""
    checklist_extractor_version: str = V4_EXTRACTOR_VERSION


@dataclass(frozen=True)
class GateDecision:
    outcome: str
    reasons: tuple[str, ...] = field(default=())


def _norm(identity: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", identity).split()).casefold()


def _flags(argv: Sequence[str]) -> set[str]:
    return {a.split("=", 1)[0] for a in argv if a.startswith("--")}


def trust_gate(b: CustodyBundle) -> GateDecision:
    stop: list[str] = []
    blocked: list[str] = []

    # B8 writer lock: fail closed, never remove a lock to proceed (PR #96 Reviewer A F6)
    if b.writer_lock != "acquired":
        stop.append(f"writer_lock_not_owned:{b.writer_lock}")

    # B10 the extractor must be the one the checklist was prepared and reviewed for
    if b.checklist_extractor_sha != b.run_extractor_sha or not b.run_extractor_sha:
        stop.append("extractor_sha_changed_since_checklist")
    if b.extractor_version != b.checklist_extractor_version:
        stop.append(f"extractor_version_changed:{b.extractor_version}")

    # B1/B7 trust needs the V4 verifier inputs, never a V3 anchor alone
    flags = _flags(b.verify_argv)
    unknown = sorted(flags - V4_VERIFY_FLAGS)
    if unknown:
        stop.append(f"unknown_verifier_flag:{','.join(unknown)}")
    if not {"--store", "--registry"} <= flags:
        blocked.append("anchor_only_or_no_custody_inputs_never_trusted")
    if "--require-trusted" not in flags:
        blocked.append("verifier_run_without_require_trusted")
    if b.legacy_v3_anchor_only:
        blocked.append("legacy_v3_anchor_is_not_custody_evidence")
    if b.manifest_schema != V4_MANIFEST_SCHEMA:
        blocked.append(f"legacy_or_unknown_manifest_schema:{b.manifest_schema}")

    # B2/B3 independent, authorized approval
    if b.approver is None or b.approval_seq is None:
        blocked.append("no_custody_approval")
    else:
        if _norm(b.approver) not in {_norm(x) for x in b.registry_ids}:
            blocked.append("approver_not_in_verifier_registry")
        for role, who in (("operator", b.operator), ("candidate_creator", b.candidate_creator)):
            if who is None:
                blocked.append(f"{role}_not_recorded")
            elif _norm(who) == _norm(b.approver):
                blocked.append(f"self_approval_by_{role}")
        # B4 any later revocation wins
        if any(seq > b.approval_seq for seq in b.revocation_seqs):
            blocked.append("approval_revoked")

    # B5 chain integrity
    if b.store_chain != "ok":
        blocked.append(f"evidence_store_chain_invalid:{b.store_chain}")

    # B6 head pin: mandatory here (stricter than the V4 CLI, see Reviewer A F5) and fresh
    if b.pinned_head_seq is None:
        blocked.append("store_head_not_pinned")
    elif b.store_head_seq_at_verification is None:
        blocked.append("store_head_at_verification_unknown")
    elif b.pinned_head_seq != b.store_head_seq_at_verification:
        blocked.append("stale_pinned_head")

    # B9 Windows guarantees stay UNVERIFIED; V4 cannot trust a non-proven platform
    for g in sorted(b.claimed_verified_guarantees & WINDOWS_UNVERIFIED):
        blocked.append(f"windows_guarantee_unverified:{g}")
    if b.extraction_platform not in V4_PROVEN_PLATFORMS:
        blocked.append(f"dep01_platform_unproven_at_v4:{b.extraction_platform}")

    # a verifier TRUSTED that contradicts the recorded evidence is itself a finding
    if b.verifier_status == "TRUSTED" and (blocked or b.verifier_exit_code != 0):
        blocked.append("verifier_trusted_contradicts_recorded_evidence")
    if b.verifier_status != "TRUSTED" or b.verifier_exit_code != 0:
        blocked.append("verifier_did_not_report_trusted")

    if not V4_BASELINE_APPROVED:
        blocked.append("dep01_v4_custody_baseline_not_independently_approved")

    if stop:
        return GateDecision(STOP, tuple(stop + blocked))
    if blocked:
        return GateDecision(BLOCKED, tuple(blocked))
    return GateDecision(RECORDABLE)
