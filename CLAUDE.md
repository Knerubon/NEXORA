# NEXORA — Claude session instructions

@AGENTS.md

[AGENTS.md](AGENTS.md) (Multi-Agent Development Protocol V2) is authoritative for governance,
workflow, safety and merge authority. This file only adds the cloud operating model and a
quick reference. If anything here conflicts with AGENTS.md, AGENTS.md wins; record the
conflict and stop only the affected work (AGENTS.md section 0).

Product scope, system design and frozen contracts stay in [docs/requirements.md](docs/requirements.md),
[docs/architecture.md](docs/architecture.md) and the accepted ADRs in [docs/decisions](docs/decisions).

## 1. Cloud-first operating model

| Place | Role |
|---|---|
| GitHub `Knerubon/nexora` | Authoritative source of truth: code, docs, ADRs, task records, handoffs, review evidence |
| Claude Code cloud session | Primary development environment (implement, test, review, open PRs) |
| Windows machine | MT5 / broker integration node only (AGENTS.md section 5.2) |
| Local SSD | Disposable runtime/cache/integration checkout; never the only copy of anything |
| Important non-Git data | Backed up separately (research config JSON, instrument bindings, recorded datasets, journals) |

- Never assume `D:\NEXORA` exists or is reachable. It is a recovery source only, not a
  development environment; do not plan, read or write work there.
- Chat history is not project memory. State that a future session needs lives in the repo:
  this file, AGENTS.md, ADRs, `tasks/*` Execution records and PR descriptions/handoffs.
- Do not recreate local-only or not-yet-recovered work from memory or chat
  (for example `claude/edge-validation-v1`, EDGE-2/EDGE-3). Recovered work is integrated explicitly.

## 2. Unit of work

**One task = one cloud session = one branch = one PR.** This is the cloud equivalent of the
worktree model; details in AGENTS.md section 5.2.

1. `git fetch origin main` and record the base `origin/main` SHA.
2. Check the branch does not exist (`git ls-remote --heads origin <branch>`) and no open PR
   or other session owns it or your target files. If one does: **STOP — WORKSTREAM ALREADY IN USE.**
3. Create `claude/<scope>-<feature>-vN` from that SHA (AGENTS.md section 6).
4. Implement only the assigned scope; touch only the paths your task owns (AGENTS.md section 7).
5. Run the cloud validation (section 5 below), push, open a **draft** PR with the section 16 handoff.

## 3. Hard safety boundaries (never, without separate explicit Rin authority)

- No merge to `main` by any worker or reviewer; MASTER merges only under a Rin token and all
  merge gates (AGENTS.md sections 10 and 20). Governance PRs need per-PR `RIN_MERGE_AUTHORIZED`.
- No direct commit/push/force-push to `main`, no history rewrite, no tag create/move/delete.
- No PROD start/stop/build/deploy (`scripts/nexora.ps1 -Environment production ...`), and no
  read/write of PROD journals, checkpoints, databases or DSNs.
- No real-order transmission: no live, demo or paper-unlock orders, no MT5 `order_send`,
  no real broker adapter. `ExecutionPreflight` stays DENY-ONLY; production composition keeps
  `transmission=None`.
- No `TradingMode.AUTO` unlock; AI output never authorizes or overrides deterministic state.
- No secrets in code, docs, fixtures, logs, PRs or config examples. No public DB/MT5 exposure.
- Do not bypass Auto-mode or any permission/safety control; if blocked, STOP and report.
- Do not change production strategy, thresholds or formulas from backtest results alone, and do
  not invent trading semantics (AGENTS.md section 9).

`packages/nexora/execution/**`, `packages/nexora/autonomous/**` and `packages/nexora/position/**`
are a serialized, safety-critical lane: changes there need independent review plus Security
(and, where useful, Codex adversarial) review per AGENTS.md section 20.5.

## 4. Architecture rules to keep in mind

- **Pattern Engine single source.** Pattern/structure logic is computed only in the P&F Pattern
  Engine. Chart renders it, Entry Readiness consumes it, API/AI never recompute it
  (AGENTS.md section 2). Pattern detection is evidence, never trade permission.
- **Broker-agnostic.** Core, execution and risk code depend on adapter ports and adapter-supplied
  capabilities, never on MT5 or a specific broker. MT5 code stays behind the read-only market-data
  adapter and the Windows-only `mt5` extra; instruments resolve through canonical `instrument_id`
  ([ADR-025](docs/decisions/ADR-025-mt5-instrument-resolution-v1.md)).
- Frozen shared contracts change only through the AGENTS.md section 3 procedure.

## 5. Cloud validation

Once it lands from the cloud-environment track, `scripts/validate_cloud.sh` is the single entry
point. Until then, run the same commands directly from the repo root (they mirror
`.github/workflows/validate.yml`; full guide in [docs/development.md](docs/development.md)):

```bash
python -m pip install uv==0.12.15          # Python 3.13
uv sync --locked --extra api --extra postgres
uv lock --check
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync python scripts/recovery_drill.py
git diff --check
npm --prefix apps/web ci                   # Node 24, npm 11
npm --prefix apps/web test
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build            # needs full git history (chart regression baseline)
```

The two PostgreSQL tests skip unless `NEXORA_TEST_POSTGRES_DSN` points at a **disposable**
PostgreSQL 18 test database. Never set `NEXORA_MT5_*`, `NEXORA_POSTGRES_DSN` or any production
value in a cloud session. Report exact commands and results; `not_run` needs a reason and is
never a pass. Docs-only PRs follow the AGENTS.md section 14 docs checks instead of claiming
application tests.

## 6. Test classes

| Class | What | Who runs it |
|---|---|---|
| **CLOUD-SAFE** | Full `pytest` suite (MT5/psutil faked), ruff, mypy, `recovery_drill.py` (temp SQLite), web test/lint/typecheck/build, `smoke_api.py` on a loopback port in a disposable checkout, synthetic benchmarks | Any agent, automatically |
| **SIMULATION** | Execution pipeline/guard/preflight/dedup/reconciler with `SimulatedBrokerAdapter`, paper/risk replay, backtests on fixtures or recorded datasets, PostgreSQL tests against a disposable PG 18 | Any agent, automatically |
| **WINDOWS-INTEGRATION** | Live MT5 terminal (read-only adapter, live quotes, symbol discovery, feed time offset), `scripts/nexora.ps1` / `launch.py` process ownership, PG backup/restore on the Windows target, Tailscale/remote verification | Operator-started on the Windows node only, against a named SHA |
| **PRODUCTION-ONLY** | PROD start/stop/build, anything touching PROD journal/checkpoints/DB, any future real adapter or PAPER/DEMO/REAL/AUTO unlock | Never automatic; each needs separate Rin authority (AGENTS.md section 20.7) |

Agents run only CLOUD-SAFE and SIMULATION on their own.

## 7. Handoff

Every session ends with the AGENTS.md section 16 handoff, in the PR description and the task's
Execution record. In the cloud, `WORKTREE` is reported as `cloud session <id>` (or `n/a`), and
`BASE SHA` is the recorded `origin/main` SHA. Include enough that a new session with no chat
history can continue: what is done, what is not, open findings, blockers with unblock conditions,
and the next recommended action. Do not start the next phase or Wave on your own.

## 8. Language

Thai prose kept short; technical terms, code, identifiers, commit messages and errors in English
(AGENTS.md section 18). Commits use `docs:` / `feat:` / `fix:` style.
