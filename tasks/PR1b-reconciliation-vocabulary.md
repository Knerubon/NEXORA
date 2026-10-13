---
id: PR1b-reconciliation-vocabulary
status: in_review
owner: DEV (developer role; self-review; independent review pending)
base_sha: 41c03866807c006643007357d804d0060398f67a
branch: claude/pr1b-reconciliation-vocabulary-v1
---

# PR-1b - ADR-035 reconciliation vocabulary / evidence foundation

Requirements: [requirements](../docs/requirements.md). Architecture: [architecture](../docs/architecture.md).
Contract: [ADR-035](../docs/decisions/ADR-035-execution-integration-safety-amendment.md) section 4.6 (OPEN-6 shape),
section 6 (target table). Builds on [RECON-1](RECON-1-reconciliation-algorithm.md).

## Scope
Evidence classification only. No position-state mutation, no recovery authorization, no OPEN-20 resolution, no timeout
or blind reset, no transmission, no I/O. Not touched: `dedup_store.py`, `broker_capabilities.py`, `guard.py`,
`preflight.py`, PR-3 recovery, pipeline.

## Delivered
- `BrokerSnapshot.complete: bool = False`; `BrokerPositionSnapshot.close_pending: bool = False` (kw-only, trailing
  defaults; existing constructors/consumers unchanged).
- `ReconciliationFinding.CLOSE_PENDING_CONFIRMED` (derived UNKNOWN) and `BROKER_FLAT_CONFIRMED` (derived
  UNSYNCHRONIZED); both require `position_ref`.
- Classifier rules (`reconciler.py`):
  - `CLOSE_PENDING_CONFIRMED`: complete snapshot + position attributed by `nexora_position_ref` with identical
    instrument and side + exactly one broker claimant of that ref + `close_pending` true. Replaces the MATCH/mismatch
    comparison for that position (quantity compare is meaningless while closing).
  - `BROKER_FLAT_CONFIRMED`: local non-CLOSED position, complete snapshot, no broker position claims the local ref
    (any instrument/side), and no unattributed broker position exists on the same instrument and side.
  - Everything else keeps previous behavior (`LOCAL_OPEN_BROKER_MISSING` / `BROKER_POSITION_LOCAL_MISSING` / MATCH ...).
- `derive_recovery_target(records, position_ref, *, snapshot_complete) -> TradeState | None` (pure helper, evidence ->
  target only): MATCH(local==broker>0)->MANAGING, CLOSE_PENDING_CONFIRMED->EXIT_PENDING, BROKER_FLAT_CONFIRMED->CLOSED,
  else None. Needs `snapshot_complete` True, one `observed_at`, exactly one distinct confirming finding for the ref, no
  other non-confirming record for the ref, and (OPEN-4 stricter reading) every other record in the run SYNCHRONIZED.

## Decisions used (ADR text) and decisions taken here (for Rin)
- ADR 4.6 fixes names/defaults/derived statuses; used verbatim.
- ADR 6 table and rules 1-3 used for the helper. The helper lives in `reconciler.py` (smallest form; ADR does not fix
  the location). It is not exported from `execution/__init__.py` (PR-4 owns exports).
- Helper takes `snapshot_complete` as an explicit argument because records do not carry completeness. MATCH also
  requires it (ADR 4.6/section 6 rule 1; open question section 12 #8 not decided).
- Stricter-than-ADR, fail-closed (NEEDS RIN CONFIRMATION): an unattributed broker position on the same
  instrument+side blocks `BROKER_FLAT_CONFIRMED` (it may be a NEXORA position the adapter failed to attribute);
  duplicate broker claims on one ref suppress `CLOSE_PENDING_CONFIRMED`. Symbol never confirms anything, it only
  blocks.
- `CLOSE_PENDING_CONFIRMED` supersedes quantity/protection comparison for that position (ADR rule 3 would otherwise
  make a closing position unrecoverable).

## Acceptance
- Defaults fail closed; each finding positive; negative/boundary (incomplete, no identity, symbol-only, duplicate
  claims, identity mismatch, naive `observed_at`); determinism; purity; no authorization/mutation surface; target
  table incl. all non-confirming -> None; existing reconciler tests unchanged and green.
  Tests: `tests/test_execution_reconciliation_vocabulary.py`.

## Known limits / OPEN items untouched
OPEN-1 freshness not evaluated (classifier has no clock policy); OPEN-4 scope (stricter reading applied); OPEN-6
adapter attestation of `complete`/`close_pending` (no adapter provides it yet; defaults keep recovery unavailable);
OPEN-13, OPEN-20 untouched; section 12 #8 undecided.

## Handoff
ROLE: developer (DEV; QA/self-review). WORKSTREAM: ADR-035 PR-1b. WORKTREE: D:\NEXORA\NEXORA-PR1B-RECON.
BRANCH: claude/pr1b-reconciliation-vocabulary-v1. BASE SHA: 41c03866807c006643007357d804d0060398f67a.
STATUS: READY FOR REVIEW (self-review; independent review pending). MERGE: NOT PERFORMED.
