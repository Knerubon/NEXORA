---
task: "AUDIT-DQ-1"
status: "in_review"
depends_on: ["tasks/DQ1-market-data-quality.md", "tasks/P8B-signal-intelligence.md"]
agents: ["agents/architect/AGENT.md", "agents/developer/AGENT.md", "agents/tester/AGENT.md", "agents/reviewer/AGENT.md", "agents/security/AGENT.md"]
skills: ["skills/testing/SKILL.md", "skills/security/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-036-decision-audit-trail-data-quality-guard-v1.md", "docs/environment-isolation.md"]
translation_needed: false
---

# AUDIT-DQ-1 — Decision Audit Trail V1 + Data Quality Guard V1 (Issue #78)

Priority HIGH, Wave 2A Track B. Owner: Claude cloud session for branch
`claude/audit-data-quality-v1`. Coordinates with Track A (Issue #77); touches none of its files.

## Architecture report (Phase A)

| Question | Finding |
|---|---|
| Base | `origin/main` `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87` |
| Ownership | Branch did not exist on origin; no open PR for #78; #77 branch `claude/edge-validation-v1` is a stale historical branch with no overlap on target paths |
| BUY/SELL/WAIT origin | `SignalEngine.evaluate` (`signals/engine.py`) via `ResearchPipeline._process` (`research/pipeline.py`); WAIT decisions have no `signal_id` |
| Market-data ingestion | `market_data/*` (`NormalizedPriceEvent`), `ResearchRuntime.ingest` journals event + output |
| Replay / persistence | `storage.Journal` (append-only, idempotent by key, `expected_count` guard); checkpoints in `research/checkpoint*.py` |
| Existing quality | `market_data/quality.py` (DQ1/ADR-012): observability sidecar, wall-clock default, no bid/ask/finite/identity/completeness checks |
| Logging | module logging only; no decision audit exists |
| DEV/PROD | `NEXORA_ENV`, per-env runtime roots, journal stream names; audit stream is env-qualified |
| Execution boundary | `execution/**` serialized safety lane; not touched. `ExecutionPreflight` DENY-ONLY |

## Scope / deliverables
New paths only: `packages/nexora/decision_audit/`, `packages/nexora/data_quality/`,
`tests/test_decision_audit*.py`, `tests/test_data_quality_guard.py`, ADR-036, this record.
Unmodified by design: `signals/**`, `research/**`, `market_data/quality*.py`, `execution/**`.

## Acceptance
- [x] BUY/SELL/**WAIT** audit completeness (`test_every_decision_is_audited_including_wait`, `test_each_action_has_a_reconstructable_record`)
- [x] Deterministic replay + stable correlation/decision IDs (`test_replay_produces_identical_records_and_stable_ids`, idempotent re-replay)
- [x] Missing/invalid evidence recorded explicitly, never fabricated; unreconstructable decisions denied
- [x] Audit write failure => NO NEW TRADE (fail closed); no disable toggle exists (signature/surface tests)
- [x] Duplicate/out-of-order/stale/invalid bid-ask/spread/non-finite/gap/clock/identity/incomplete cases (36 original Guard tests, all still passing)
- [x] DEV/PROD stream isolation and cross-environment record rejection
- [x] Existing SignalEngine output unchanged (pinned action counts on the fixture; hash-identical with and without audit)
- [x] No order submission / execution activation (AST import-boundary tests; no execution/risk/paper/MT5/network/env imports)
- [x] ruff, mypy, full pytest, recovery_drill, git diff --check (see Checks)
- [x] Hardening round 1 (CHANGES REQUESTED): F1, F2, F3 and the security items below are fixed with regression tests; see "Hardening round 1"
- [ ] Independent re-review of the hardening delta, Security review (persistence + secrets boundary), Rin approval: pending

## Execution record
- Implementation: additive packages `nexora.data_quality` (pure verdict: `ok|blocked|unknown`, stable reason codes, explicit thresholds, caller-supplied `evaluated_at`) and `nexora.decision_audit` (frozen schema v1, deterministic `decision_id`/`correlation_id`, append-only store on `Journal`, mandatory `DecisionAuditGate`).
- Context additions: [ADR-036](../docs/decisions/ADR-036-decision-audit-trail-data-quality-guard-v1.md) (Proposed)
- Decisions made here, pending Rin/Architect confirmation: WAIT carries `signal_id=None` and an audit-only `decision_id`; `degraded` state not defined; engine output is audited unchanged under bad quality and BUY/SELL is marked `denied_data_quality`.
- Base SHA: `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`
- Changed files: new paths only (12 files); no change to `signals/**`, `research/**`, `market_data/quality*.py`, `execution/**`, `apps/**`, `infra/**`.
- Checks (cloud session, CLOUD-SAFE/SIMULATION only):
  - baseline before changes `uv run --no-sync pytest -q`: pass: 2038 passed, 2 skipped
  - `uv run --no-sync pytest -q`: pass: 2112 passed, 2 skipped (baseline + 74 new; skips are the PostgreSQL tests, no disposable DSN)
  - `uv run --no-sync ruff check .`: pass
  - `uv run --no-sync ruff format --check` on the 10 changed Python files: pass. Repo-wide `ruff format --check .`: fail, 24 files, **pre-existing on `origin/main`** (not touched here; same 24 before and after)
  - `uv run --no-sync mypy`: pass: 231 source files
  - `uv run --no-sync python scripts/recovery_drill.py`: pass (PostgreSQL/host-crash/RPO-RTO reported NOT VERIFIED by the script itself)
  - `git diff --check`: pass
  - `npm --prefix apps/web test|lint|typecheck|build`: not_run: no `apps/web` changes; impact none for this diff, left to PR CI
- Review: self-review only; independent review pending
- Limitations (not claimed as done):
  - **Contracts and a standalone gate only. No end-to-end enforcement.** `ResearchRuntime.ingest`, `ResearchPipeline`, paper trading and execution are not wired to the Guard or gate, so today nothing forces a real runtime decision through them.
  - `risk_authority_outcome` / `lifecycle_ref` are optional opaque references supplied by the caller; V1 derives neither.
  - The store trusts the `Journal.append` contract and does no separate read-back.
  - No live-feed integration, no scheduling, no polling or reconnect logic was added.
- Blockers / unblock conditions:
  - Runtime wiring touches `research/runtime.py` (shared with recovery/checkpoint, ADR-022/029): needs Architect review and checkpoint-equivalence tests before ingest semantics change.
  - Whether the SignalEngine should be skipped (rather than audited then denied) on bad data is a trading-semantics decision for Quant/Rin (AGENTS.md section 9).
  - Edge Validation (#77) may consume audit records for backtest provenance: coordinate before sharing any file.
- Next action: independent reviewer session (+ Security) on the draft PR; then Rin decision on runtime wiring.

## Hardening round 1 (response to Independent Review: CHANGES REQUESTED)

Starting HEAD `017b5a6aa19503bcb8b29f11b3cfd401616ea1b3` (verified). The exact new HEAD is reported
in the PR description and handoff (a record cannot contain its own commit SHA). Same branch
`claude/audit-data-quality-v1`; PR #80 stays Draft/HOLD. Self-review only; independent re-review pending.

### Reproduction on the original code (`017b5a6`)

A throwaway script (not committed) ran against a detached worktree of the original HEAD:

| Finding | Observed on `017b5a6` |
|---|---|
| F1 | A hand-built `QualityVerdict(ok, permitted, no event keys, no hash, no time)` -> `recorded_eligible`, `new_trade_eligible=True`. An `ok` verdict for event `t:9999` on a crossed-book event -> `recorded_eligible` |
| F2 | `max_quote_age_seconds=float("inf")` and `10**12` constructed; a 30-day-old quote -> `ok`, `new_trade_permitted=True`. `True` and `"false"` also constructed |
| F3 | BUY whose `latest` was the earlier signal -> `recorded_eligible`, `signal_id=None`, `signal_emitted=False`, `trade_eligibility=eligible_for_downstream_gates` |

### F1-F3 resolution matrix

| ID | Fix | Where | Regression / adversarial tests |
|---|---|---|---|
| F1 | No public API accepts a verdict. The gate/builder/recorder take the snapshot + `evaluated_at` and obtain the verdict from a Guard they own. `DataQualityGuard` is final. The verdict is verified bound to the exact event (snapshot must end in it), `event_keys`, the recomputed snapshot hash and `evaluated_at`; the public `verify_quality_binding` also re-derives it via the Guard. `QualityVerdict` invariants enforced at construction. Store re-checks record invariants | `data_quality/models.py`, `data_quality/guard.py`, `decision_audit/invariants.py`, `builder.py`, `gate.py`, `store.py` | `test_decision_audit_hardening.py` F1 block (forged/mismatched/reused/hash-less verdicts, relabelled-ok, flipped bit, wrong time/config, non-verdict objects, lying/raising Guard, 14 forged-record cases); `test_data_quality_guard_hardening.py` verdict-construction block |
| F2 | Strict validation of every field: `type(x) is int/bool`, finite positive `Decimal` caps, ratio <= 1, version grammar, zero/negative/ceiling rejection, contradiction rules (`unbounded_spread`, `contradictory_spread_cap_without_bid_ask`); Guard type-checks and re-validates inputs | `data_quality/models.py`, `guard.py` | `test_data_quality_guard_hardening.py` (NaN/Inf/bool/str/float/Decimal/None/negative/zero/ceiling x each field, missing fields, contradictions, constructor-bypass re-validation) |
| F3 | Eligibility requires a genuinely emitted, consistent signal. New denials `denied_no_emitted_signal`, `denied_inconsistent_output`; WAIT never reads `latest` and never gets an id; no fabricated score; record invariants forbid eligible-without-signal | `builder.py` (`_resolve_signal`, `_eligibility`), `invariants.py`, `models.py`, `gate.py` | `test_decision_audit_hardening.py` F3 block (suppressed-duplicate state, absent/stale/naive/garbage `latest`, 12 inconsistent-identity cases, empty evidence, WAIT with injected `latest`, 14 score cases, 300-event run: every authorization maps to a unique real engine signal) |

### Security findings resolution matrix

| # | Item | Resolution | Tests |
|---|---|---|---|
| 1 | Structured allowlist, not only regex | `payload_policy.py`: every persisted field declared with a kind, exact field-set equality, per-kind grammar, stricter grammar for caller references; sensitive keys rejected in every section the audit reads; regex only as last layer | sensitive-key (9), free-text secret (8), caller-reference (7), version/identifier, schema-completeness and undeclared-field tests |
| 2 | Unambiguous stream identity | `identity.py`: validated components, injective percent-escape; separate prefix for blocked records; structured `correlation_id` | exhaustive injectivity over a hostile alphabet, old-collision regression, component validation |
| 3 | Duplicate / replay | `AppendOutcome`; `RECORDED_REPLAY` is never eligible; same decision with different evidence -> `audit_identity_conflict` | replay, whole-run replay, restart, conflicting-evidence tests |
| 4 | Price / OHLC at the correct boundary | Guard re-validates kind, precision, positivity, OHLC consistency, price-vs-source (new additive codes) | Guard hardening price/OHLC tests |
| 5 | Stateless Guard + sequence-history provider | ADR-036 D2a; `SequenceHistoryProvider` contract (no implementation) | ADR drift test |
| 6 | ADR-036 drift | ADR rewritten to match the code (gate name, replay, read-back claim removed, id derivations, new modules) | `test_adr_036_describes_the_code_that_actually_exists` |

### EVALUATION_BLOCKED contract

Implemented as an isolated, additive contract (`decision_audit/blocked.py`): own record kind, `blocked:` id
namespace and `audit-blocked` stream prefix; **no** action/score/signal_id/eligibility field exists on the
type; carries event reference, verified quality verdict, reason code and timestamp; durable, append-only,
idempotent. It imports nothing from signals/research/execution/risk (AST-tested) and is not wired into any
runtime. **Integration is BLOCKED** (not the contract): emitting it instead of an audited-then-denied decision
needs the skip-engine-on-bad-data decision (Quant/Rin) and runtime wiring (ADR-036 open items 2 and 3).

### Checks (cloud session, CLOUD-SAFE/SIMULATION only)

- `uv run --no-sync pytest -q`: pass: 2446 passed, 2 skipped (2038 baseline on `origin/main` + 408 PR tests = 36 + 38 original, 184 + 150 new; skips are the PostgreSQL tests, no disposable DSN)
- `uv run --no-sync ruff check .`: pass
- `uv run --no-sync ruff format --check` on the 16 changed Python files: pass. Repo-wide `ruff format --check .`: fail, 24 files, **pre-existing on `origin/main`**, none PR-owned
- `uv run --no-sync mypy`: pass: 237 source files
- `uv run --no-sync python scripts/recovery_drill.py`: pass (PostgreSQL/host-crash/RPO-RTO reported NOT VERIFIED by the script itself)
- `git diff --check`: pass
- Engines untouched: `git status` shows 0 changes under `packages/nexora/{signals,research,risk,execution,autonomous,position,paper,market_data}`, `apps/**`, `infra/**`; the pinned fixture counts (WAIT 226 / BUY 39 / SELL 35) and the with/without-audit hash-identity test still pass
- PR #79 file ownership: PR #79 changes only `packages/nexora/edge/**`, `docs/research/edge-validation/**`, `tests/edge_fixtures.py`, `tests/test_edge_*.py`; no overlap
- `npm --prefix apps/web test|lint|typecheck|build`: not_run: no `apps/web` changes; impact none for this diff, left to PR CI
- CI: not claimed green; see the PR for current check status

### Remaining decisions / blockers

See ADR-036 "Open items / blockers": sequence-history provider implementation (1), skip-engine-on-bad-data
and EVALUATION_BLOCKED integration (2, BLOCKED), runtime wiring (3), Quant confirmation of configuration-sanity
ceilings (4), actionable-without-evidence denial (5), replay-never-re-authorizes recovery story (6), journal
read-back (7).
