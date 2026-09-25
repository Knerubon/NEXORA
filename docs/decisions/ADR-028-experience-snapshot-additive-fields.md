# ADR-028 — Experience snapshot additive context fields (backward compatibility)

Status: **accepted** (Rin, 2026-09-25 19:51 +07:00, for EXC1 — Experience Journal Compatibility V1). Proposed earlier the same day; see *Acceptance* for the sequence.
Date: 2026-09-25
Related: [EX1](../../tasks/EX1-experience-engine-v1.md) (Experience V1 contract), [ADR-020](./ADR-020-pnf-trendline-v1.md) Decision 13, [ADR-021](./ADR-021-entry-readiness-v1.md) Decision 12, [ADR-022](./ADR-022-startup-recovery-checkpoint-v1.md)
Task: [EXC1](../../tasks/EXC1-experience-journal-compat.md)

## Context

EX1 freezes Experience snapshots from the *original recorded output*. It never regenerates them from current engines, and it never updates a committed snapshot. Recovery re-freezes each snapshot from the journaled research row and appends it idempotently. `(stream, event_key)` is unique, so a different payload under the same `experience_id` fails with `journal_identity_conflict`.

ADR-020 D13 and ADR-021 D12 added `trendline` and `entry_readiness` to the frozen `context` "additively", outside `fingerprint()`. The implementation used `output.get(...)`, which writes an explicit `null` when the recorded output predates the field. As a result:

- `experience_id` is unchanged, because it derives from the policy, the scope, the event and a fingerprint of the output.
- The context, and so the snapshot's content hash, changes for every snapshot committed before those fields existed.
- Recovering a journal written by the pre-Trendline writer (9016004) fails at the first snapshot during startup, whether checkpoints are on or off. This is PROD migration blocker B1.

Canonical serialization distinguishes an absent key from an explicit `null`, so the two are not equivalent.

## Decision

A context field added to `freeze()` after experience-v1 was first committed is frozen **only when the recorded output carries that key**. Presence decides, not value: a recorded `null` is frozen as `null`. The fields are listed in `ADDITIVE_OUTPUT_CONTEXT` in `packages/nexora/experience/engine.py`. They are currently `trendline` and `entry_readiness`, and any future additive field must be added to that list.

Fields that were part of the original V1 context (`matrix`, `structure`, `regime`, and the rest) keep their existing `output.get` behavior, so snapshots committed under V1 are unchanged.

## Consequences

- **Historical rows are untouched.** Replaying old recorded output reproduces the committed snapshot byte for byte. There is no migration, rewrite, deletion or relaxed conflict check.
- **`experience_id`, `fingerprint()` and the policy are unchanged.**
- **New records are unchanged.** The current pipeline always records both fields, so its snapshots are byte-identical to what 4f69e9c writes, and journals written since ADR-020/021 still recover.
- **Genuine conflicts still fail.** A committed snapshot that differs from its re-freeze, including one that claims a field its recorded output lacks, still raises `journal_identity_conflict`.
- **Future additive fields** cannot change already-frozen history, as long as they are listed in `ADDITIVE_OUTPUT_CONTEXT`. Changing the meaning of an existing field still needs a new policy or version, as EX1 requires.
- **Cost:** a bounded membership check per field, per freeze. There is no I/O, clock or lookup.
- **Not provided: reverse compatibility.** Code older than ADR-020 (for example 9016004) cannot replay journals that newer code has written, because it omits fields those snapshots carry. Rollback must use a store preserved from before the newer code wrote to it, per the [migration checklist](../environment-migration-checklist.md) §6.

## Alternatives rejected

| Option | Why not |
|---|---|
| A. Version the snapshot schema or policy | Changes `experience_id` or the content of new records. Journals already written since ADR-020 would conflict. Needs a policy migration. |
| B. Freeze only the original V1 fields | Drops fields that ADR-020/021 require on new records. Breaks journals written since then. |
| C (generic). Omit every null field | Would change V1 snapshots whose recorded `matrix`/`structure`/`regime` was null. |
| D. Normalize or strip nulls before hashing | Weakens identity checks for every stream and treats different content as equal. |
| E. Skip `save()` when a snapshot already exists | Needs a database lookup per record, and hides genuine historical conflicts. |

## Acceptance (2026-09-25 19:51 +07:00)

Rin accepted this ADR for EXC1 (Experience Journal Compatibility V1) after reviewing the EXC1 completion handoff at `ab97e62`, based on `f2d51ad`. The acceptance is **prospective**: it is recorded now and is not backdated. The implementation existed before the acceptance; the sequence is recorded in the [EXC1 task](../../tasks/EXC1-experience-journal-compat.md).

**Accepted architecture:**

- Compatibility is decided by field **presence**, not by field value.
- Additive compatibility applies only to `trendline` and `entry_readiness` (`ADDITIVE_OUTPUT_CONTEXT`).
- Those fields are frozen into the Experience context only when the recorded output contains them. A recorded `null` stays `null`.
- The original V1 fields keep their existing behavior.
- Journal history is never rewritten.
- Journal identity and conflict detection stay enabled and unchanged.
- `experience_id`, `fingerprint()` and policy semantics are unchanged.
- Trading and decision semantics are unchanged.

**Accepted deployment consequences:**

- Reverse compatibility with writer `9016004` is **not** guaranteed. Rollback requires the pre-cutover store preserved as the [migration checklist](../environment-migration-checklist.md) §6 describes.
- Deploying this code change alters the checkpoint code fingerprint (ADR-022), so every existing checkpoint is rejected and recovery performs **one intentional full replay**. Experience replay is still expensive (ADR-031), so this replay must be scheduled operationally.
