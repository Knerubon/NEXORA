"""Readiness-checklist gate for V4 custody evidence (findings B-M2, N3, R3-2).

This is NOT a reimplementation of PR #96's custody code and it grants nothing. It encodes what the
PR #95 checklist (phase D) must refuse before it may even RECORD a verifier's TRUSTED outcome, using
only interfaces read from PR #96 at `V4_REF_SHA` (scripts/verify_market_history.py,
scripts/custody_market_history.py, custody.py, fsutil.py, lock.py). Every constant below is quoted
from that SHA and must be re-verified at the SHA that actually runs (checklist A3).

The gate never returns "TRUSTED". Its best outcome is RECORDABLE_PENDING_REVIEW, and while the V4
custody baseline itself is not independently approved (DEP-01: PR #96 Reviewer A, 4 MAJOR findings
F1, F2, F3 and N1) every bundle is additionally BLOCKED. Identities are DECLARED strings: the
normalisation below catches spelling disguises and refuses ambiguous characters, it proves nothing
about who a person is, and while PR #96 offers no principal authentication every bundle is also
BLOCKED on that.

Pure Python, no I/O, no `nexora` imports.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

V4_REF_SHA = "d6a7cda72a88600e2ccf6554605f1c50478893ea"
# scripts/verify_market_history.py argparse flags, as read at V4_REF_SHA.
V4_VERIFY_FLAGS = frozenset(
    {"--dataset", "--anchor", "--store", "--registry", "--store-head", "--require-trusted"}
)
V4_VERIFY_VALUELESS = frozenset({"--require-trusted"})
V4_VERIFY_SCRIPT = "scripts/verify_market_history.py"
# Launchers accepted in front of the script; anything else is not the reviewed verifier.
V4_VERIFY_LAUNCHERS: tuple[tuple[str, ...], ...] = (
    (),
    ("python",),
    ("uv", "run", "python"),
    ("uv", "run", "--no-sync", "python"),
)
# What this package requires of a verifier run that may be recorded as TRUSTED (stricter than the
# V4 CLI, where `--store-head` is optional: Reviewer A F5).
REQUIRED_VERIFY_FLAGS = frozenset(
    {"--dataset", "--store", "--registry", "--store-head", "--require-trusted"}
)
# scripts/custody_market_history.py subcommands, as read at V4_REF_SHA.
V4_CUSTODY_SUBCOMMANDS = ("approve", "revoke", "head")
V4_EXTRACTOR_VERSION = "nexora.market-history-extractor/5"  # extract.EXTRACTOR_VERSION
V4_MANIFEST_SCHEMA = "nexora.market-history/4"  # extract.SCHEMA_VERSION
V4_PROVEN_PLATFORMS = frozenset({"linux"})  # fsutil.PROVEN_PLATFORMS
# Set to True only by an independent review that approves PR #96's custody design (DEP-01).
V4_BASELINE_APPROVED = False
# Set to True only when PR #96 authenticates operators and custodians (key identities, not
# declared strings; PR #96 Reviewer A F1 and N1) and an independent review approves it.
V4_PRINCIPAL_AUTHENTICATION_AVAILABLE = False
# Set to True only when real Windows evidence resolves DEP-02. Until then nothing may be claimed.
DEP02_WINDOWS_GUARANTEES_RESOLVED = False
# Fail-closed ALLOW-list of guarantees a bundle may claim as verified. Empty: no Windows or
# filesystem guarantee has been observed (UNSUPPORTED-GUARANTEES.md). Any other name BLOCKS.
VERIFIABLE_GUARANTEES: frozenset[str] = frozenset()

# Names known to be unverified (kept for reporting; the allow-list above is what decides).
WINDOWS_UNVERIFIED = frozenset(
    {
        "windows_msvcrt_lock_exclusivity",
        "windows_ntfs_atomicity_and_hardlink_behaviour",
        "reparse_point_and_junction_handling",
        "windows_directory_fsync",
        "windows_delete_sharing_semantics",
    }
)

# Where the `--store-head` pin came from. Only a pin published through a channel independent of the
# store, BEFORE verification, can reveal a re-sealed store (PR #96 Reviewer A N4, cross-track X3).
PIN_INDEPENDENT = "independent_prior_channel"
PIN_SAME_STORE = "same_store_at_verification"
PIN_CHANNELS = (PIN_INDEPENDENT, PIN_SAME_STORE)

RECORDABLE = "RECORDABLE_PENDING_REVIEW"
BLOCKED = "BLOCKED"
STOP = "STOP"

_HEAD_PIN = re.compile(r"^([1-9][0-9]*):([0-9a-f]{64})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# Conservative identity alphabet after NFKC + case-fold. Anything outside it (homoglyphs from other
# scripts, format or control characters, symbols) is AMBIGUOUS and refused, not compared.
_IDENTITY = re.compile(r"^[a-z0-9][a-z0-9._@+-]{0,199}$")


@dataclass(frozen=True)
class CustodyBundle:
    """What the operator and verifier recorded. Field names mirror the evidence record."""

    verify_argv: tuple[str, ...]  # the verifier invocation, as an argv list (not free text)
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
    pinned_head_seq: int | None  # the SEQ recorded for `--store-head SEQ:SHA256`, None if absent
    legacy_v3_anchor_only: bool  # custody evidence offered is a V3 anchor file
    manifest_schema: str
    extractor_version: str
    extraction_platform: str  # manifest filesystem_capabilities.platform
    writer_lock: str  # "acquired" or the refusal code observed
    claimed_verified_guarantees: frozenset[str] = frozenset()
    checklist_extractor_sha: str = ""
    run_extractor_sha: str = ""
    checklist_extractor_version: str = V4_EXTRACTOR_VERSION
    # Round 4 (N3, R3-2, X2, X3). Defaults fail closed.
    approver_identities: frozenset[str] = frozenset()  # approver's registry id and every alias
    operator_authenticated: bool = False
    approver_authenticated: bool = False
    pinned_head_sha256: str | None = None  # the SHA256 recorded for the pin
    store_head_sha256_at_verification: str | None = None
    approval_entry_sha256: str | None = None
    candidate_approval_seqs: tuple[int, ...] = ()  # every approval entry for this candidate
    pin_channel: str = ""
    same_store_pin_claimed_independent: bool = False


@dataclass(frozen=True)
class GateDecision:
    outcome: str
    reasons: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class VerifyInvocation:
    flags: dict[str, str]
    problems: tuple[str, ...]  # BLOCKED reasons
    unknown: tuple[str, ...]  # STOP: flags the reviewed CLI does not have

    @property
    def store_head(self) -> tuple[int, str] | None:
        m = _HEAD_PIN.match(self.flags.get("--store-head", ""))
        return (int(m.group(1)), m.group(2)) if m else None


def identity_problem(identity: object) -> str:
    """'' when `identity` is an acceptable declared identity, else a reason code.

    Mirrors PR #96 `custody.normalize_identity` (string, NFKC, no control or format characters,
    1-200 characters) and is stricter: after NFKC and case-folding only `[a-z0-9._@+-]` is accepted,
    so confusable letters from other scripts and zero-width characters are refused as ambiguous
    rather than compared. This is string hygiene, not authentication."""
    if type(identity) is not str:
        return "identity_not_a_string"
    text = unicodedata.normalize("NFKC", identity)
    if any(unicodedata.category(c).startswith("C") for c in text):
        return "identity_control_or_format_character"
    text = " ".join(text.split()).casefold()
    if not text:
        return "identity_empty"
    if not _IDENTITY.match(text):
        return "identity_ambiguous"
    return ""


def norm_identity(identity: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", identity).split()).casefold()


def parse_verify_argv(argv: Sequence[str]) -> VerifyInvocation:
    """Structured check of a recorded verifier invocation against the reviewed V4 CLI."""
    argv = tuple(argv)
    problems: list[str] = []
    unknown: list[str] = []
    rest: tuple[str, ...] | None = None
    for launcher in sorted(V4_VERIFY_LAUNCHERS, key=len, reverse=True):
        n = len(launcher)
        if argv[:n] == launcher and argv[n : n + 1] == (V4_VERIFY_SCRIPT,):
            rest = argv[n + 1 :]
            break
    if rest is None:
        return VerifyInvocation({}, ("verifier_argv_not_v4_verifier",), ())
    flags: dict[str, str] = {}
    i = 0
    while i < len(rest):
        tok = rest[i]
        if not tok.startswith("--") or "=" in tok:
            problems.append(f"verifier_argv_malformed:{tok}")
            i += 1
            continue
        if tok not in V4_VERIFY_FLAGS:
            unknown.append(tok)
            i += 1
            continue
        if tok in flags:
            problems.append(f"verifier_argv_duplicate:{tok}")
        if tok in V4_VERIFY_VALUELESS:
            flags[tok] = ""
            i += 1
            continue
        if i + 1 >= len(rest) or rest[i + 1].startswith("--") or not rest[i + 1].strip():
            problems.append(f"verifier_argv_missing_value:{tok}")
            i += 1
            continue
        flags[tok] = rest[i + 1]
        i += 2
    for f in sorted(REQUIRED_VERIFY_FLAGS - set(flags)):
        problems.append(f"verifier_argv_missing:{f}")
    if "--store-head" in flags and not _HEAD_PIN.match(flags["--store-head"]):
        problems.append("verifier_argv_store_head_malformed")
    return VerifyInvocation(flags, tuple(problems), tuple(unknown))


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

    # B1/B7 trust needs the structured V4 verifier invocation, never a V3 anchor alone (R3-1/R3-2)
    inv = parse_verify_argv(b.verify_argv)
    if inv.unknown:
        stop.append(f"unknown_verifier_flag:{','.join(sorted(inv.unknown))}")
    blocked += inv.problems
    if not {"--store", "--registry"} <= set(inv.flags):
        blocked.append("anchor_only_or_no_custody_inputs_never_trusted")
    if "--require-trusted" not in inv.flags:
        blocked.append("verifier_run_without_require_trusted")
    if b.legacy_v3_anchor_only:
        blocked.append("legacy_v3_anchor_is_not_custody_evidence")
    if b.manifest_schema != V4_MANIFEST_SCHEMA:
        blocked.append(f"legacy_or_unknown_manifest_schema:{b.manifest_schema}")

    # B2/B3 independent, authorized approval (N3: compared over the approver's id AND aliases)
    if b.approver is None or b.approval_seq is None:
        blocked.append("no_custody_approval")
    else:
        roles = (
            ("approver", b.approver),
            ("operator", b.operator),
            ("candidate_creator", b.candidate_creator),
        )
        for role, who in roles:
            if who is None:
                blocked.append(f"{role}_not_recorded")
            elif problem := identity_problem(who):
                blocked.append(f"{problem}:{role}")
        for alias in sorted(b.approver_identities):
            if problem := identity_problem(alias):
                blocked.append(f"{problem}:approver_alias")
        if not b.approver_identities:
            blocked.append("approver_alias_set_not_recorded")
        mine = {norm_identity(x) for x in b.approver_identities} | {norm_identity(b.approver)}
        registry = {norm_identity(x) for x in b.registry_ids}
        if norm_identity(b.approver) not in registry:
            blocked.append("approver_not_in_verifier_registry")
        if not {norm_identity(x) for x in b.approver_identities} <= registry:
            blocked.append("approver_alias_not_in_verifier_registry")
        for role, who in roles[1:]:
            if who is not None and norm_identity(who) in mine:
                blocked.append(f"self_approval_by_{role}")
        # B4 any later revocation wins; X2: a revocation after the FIRST approval of this
        # candidate also wins, so re-appending a revoked approval cannot restore it.
        if any(seq > b.approval_seq for seq in b.revocation_seqs):
            blocked.append("approval_revoked")
        first = min((*b.candidate_approval_seqs, b.approval_seq))
        if b.approval_seq not in b.candidate_approval_seqs and b.candidate_approval_seqs:
            blocked.append("approval_not_among_candidate_approvals")
        if any(seq > first for seq in b.revocation_seqs) and "approval_revoked" not in blocked:
            blocked.append("approval_replayed_after_revocation")

    # B5 chain integrity
    if b.store_chain != "ok":
        blocked.append(f"evidence_store_chain_invalid:{b.store_chain}")

    # B6 head pin: mandatory here (stricter than the V4 CLI, Reviewer A F5), taken from the actual
    # invocation, fresh in seq AND digest, and covering the approval (R3-2)
    pin = inv.store_head
    if b.pinned_head_seq is None:
        blocked.append("store_head_not_pinned")
    elif pin is None:
        blocked.append("recorded_pin_absent_from_verifier_invocation")
    elif (b.pinned_head_seq, b.pinned_head_sha256) != pin:
        blocked.append("recorded_pin_differs_from_verifier_invocation")
    if pin is not None:
        seq, digest = pin
        if b.store_head_seq_at_verification is None or b.store_head_sha256_at_verification is None:
            blocked.append("store_head_at_verification_unknown")
        elif seq != b.store_head_seq_at_verification:
            blocked.append("stale_pinned_head")
        elif digest != b.store_head_sha256_at_verification:
            blocked.append("pinned_head_digest_mismatch")
        if b.approval_seq is not None:
            if b.approval_seq > seq:
                blocked.append("approval_outside_pinned_chain")
            if b.approval_entry_sha256 is None or not _SHA256.match(b.approval_entry_sha256):
                blocked.append("approval_entry_sha256_not_recorded")
            elif b.approval_seq == seq and b.approval_entry_sha256 != digest:
                blocked.append("approval_entry_differs_from_pinned_head")
        if any(r > seq for r in b.revocation_seqs):
            blocked.append("revocation_outside_pinned_chain")
    # X3 a pin read from the same store at verification time cannot reveal a re-sealed store
    if b.pin_channel not in PIN_CHANNELS:
        blocked.append("store_head_pin_channel_unknown")
    elif b.pin_channel != PIN_INDEPENDENT:
        blocked.append("store_head_pin_not_from_independent_channel")
    if b.same_store_pin_claimed_independent:
        blocked.append("same_store_pin_presented_as_independent")

    # B9 guarantees: fail-closed allow-list (R3-2); V4 cannot trust a non-proven platform
    for g in sorted(b.claimed_verified_guarantees):
        if g in WINDOWS_UNVERIFIED:
            blocked.append(f"windows_guarantee_unverified:{g}")
        if g not in VERIFIABLE_GUARANTEES:
            blocked.append(f"guarantee_not_on_allow_list:{g}")
    if b.claimed_verified_guarantees and not DEP02_WINDOWS_GUARANTEES_RESOLVED:
        blocked.append("dep02_windows_guarantees_unresolved")
    if b.extraction_platform not in V4_PROVEN_PLATFORMS:
        blocked.append(f"dep01_platform_unproven_at_v4:{b.extraction_platform}")

    # a verifier TRUSTED that contradicts the recorded evidence is itself a finding; the
    # dependency blocks below are not evidence, so they are added after this check
    if b.verifier_status == "TRUSTED" and (blocked or b.verifier_exit_code != 0):
        blocked.append("verifier_trusted_contradicts_recorded_evidence")
    if b.verifier_status != "TRUSTED" or b.verifier_exit_code != 0:
        blocked.append("verifier_did_not_report_trusted")

    # N3/X6 declared identities are not authenticated and PR #96 offers no principal
    # authentication. Policy, not verifier evidence, so it comes after the contradiction check.
    if not b.operator_authenticated:
        blocked.append("operator_identity_not_authenticated")
    if not b.approver_authenticated:
        blocked.append("approver_identity_not_authenticated")
    if not V4_PRINCIPAL_AUTHENTICATION_AVAILABLE:
        blocked.append("dep01_principal_authentication_unavailable")

    if not V4_BASELINE_APPROVED:
        blocked.append("dep01_v4_custody_baseline_not_independently_approved")

    if stop:
        return GateDecision(STOP, tuple(stop + blocked))
    if blocked:
        return GateDecision(BLOCKED, tuple(blocked))
    return GateDecision(RECORDABLE)
