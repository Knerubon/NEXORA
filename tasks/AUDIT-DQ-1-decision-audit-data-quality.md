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
- [x] Duplicate/out-of-order/stale/invalid bid-ask/spread/non-finite/gap/clock/identity/incomplete cases (36 Guard tests)
- [x] DEV/PROD stream isolation and cross-environment record rejection
- [x] Existing SignalEngine output unchanged (pinned action counts on the fixture; hash-identical with and without audit)
- [x] No order submission / execution activation (AST import-boundary tests; no execution/risk/paper/MT5/network/env imports)
- [x] ruff, mypy, full pytest, recovery_drill, git diff --check (see Checks)
- [ ] Independent review, Security review (persistence + secrets boundary), Rin approval: pending

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
