# EDGE-VALIDATION-1 (#77) / SYSTEM-READINESS-1 (#78) — Master readiness report, 2026-10-20

Status: **READY FOR RIN REVIEW. Nothing is frozen, merged or authorized.** Author: Claude MASTER session (orchestrator; independent of the authors of PR #79, #80, #81). This is review input, not an APPROVE; no review verdict is issued on any PR.

Base: `origin/main` = `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87` (verified with `git ls-remote origin HEAD refs/heads/main` on 2026-10-20; unchanged since the 2026-10-15 readiness wave). Branch `claude/edge-readiness-master-20261020-v1`, created from that SHA with a clean tree. Session: cloud (no MT5 terminal, no PROD, no `D:` access).

This report continues, and does not replace, the Oct 15 readiness set (PR #82 Track A, #83 Track B, #84 Track C, #85 Track D). It re-verifies their claims against source, closes the gap that Track D never reviewed PR #80, and adds a delta.

## 1. Verified repository state

| Item | Verified value |
|---|---|
| Open PRs (all draft, all `mergeable_state: clean`, CI `python` + `web` green, **0 reviews, 0 comments**) | #79 `196a962f…` EDGE engineering · #80 `618ce87f…` Audit + Data Quality · #81 `49bd2d6c…` EDGE-VP-1 protocol · #82 `1948a63e…` Track A · #83 `62a8c862…` Track B · #84 `5fe2d9aa…` Track C · #85 `fd70ce21…` Track D · #73, #51 unrelated |
| Ownership | `claude/edge-validation-v1` (#79), `claude/audit-data-quality-v1` (#80), `claude/edge-validation-protocol-v1` (#81), `codex/edge-readiness-{a,b,c,d}-20261015-v1` (#82–#85) are owned by earlier sessions. None was edited, pushed to or rebased by this session. |
| Existing Edge work | Commit `6060831` is an ancestor of PR #79 head (GitHub compare: merge base `6060831…`, `behind_by 0`). Nothing in it is lost. |
| Worktrees | One checkout of main plus two detached read-only review worktrees at the exact PR #79 and #80 heads, in scratch. No other worktree exists in this session. |
| Governance files | `AGENTS.md` (Protocol V2) and `CLAUDE.md` read in full. Phase 1 limits, §10 main-branch rule and §20 merge gates applied. |
| ADR status | ADR-036 (#80) and ADR-037 (#79) are **Proposed**. EDGE-VP-1 is **DRAFT**. None is an accepted freeze. |

## 2. Independent verification performed (commands and results)

| # | Command (cloud, Python 3.13.16, `uv sync --locked`) | Result |
|---|---|---|
| V1 | PR #79 head: `uv run --no-sync pytest -q tests/test_edge_*.py` (8 edge test files) | **pass: 312 passed** (Track D could not run pytest) |
| V2 | PR #79 head: `ruff check`, `ruff format --check`, `mypy` | **pass** (237 source files) |
| V3 | PR #79 D-1 and D-2 reproduction script (synthetic fixtures only) | **both reproduced exactly**, same `run_id` `71325d96…` as Track D |
| V4 | PR #80 head: 7 audit/DQ test files | **pass: 533 passed** |
| V5 | PR #80 head: full `uv run --no-sync pytest -q` | **pass: 2571 passed, 2 skipped** (2 skips = PostgreSQL tests, no disposable DSN). 2038 main baseline + 533 PR tests. The task record's "2446" is a stale round-1 figure. |
| V6 | PR #80 head: `ruff check`, `ruff format --check` (PR packages), `mypy` | **pass** (240 source files) |
| V7 | main: `ruff format --check .` | **fail: 24 files** (pre-existing, matches the PR #80 task record) |
| not_run | PR #79 full pytest; web test/lint/typecheck/build; `recovery_drill.py`; PostgreSQL tests | not run by this session. CI shows `python` and `web` jobs green on each head; that is CI evidence, not mine. |

## 3. Track A — P&F baseline audit and freeze proposal

The Oct 15 Track A report (#82) was spot-checked against main and is **accurate**: `SignalWeights` 20/25/20/20/15/10/15; `wait_score_max` 49, `action_score_min` 65, `action_gap_min` 8; geometry 0.8 / 0.5 / 1.5 / 2.5; `AdaptiveBoxConfig` defaults 14 / 1.0 / 0.1 / 50.0; `PnfConfig` has no defaults; runtime `research-pipeline-v2`; `pipeline.py` calls `signals.evaluate(structure, regime, matrix)` with no pattern input; the legacy `BacktestRunner` "baseline" mode is close-momentum, not P&F Baseline V1.

New artifact: `BASELINE-V1-FREEZE-PROPOSAL.json` (this directory). Every field is either a source default quoted with its path, or `UNRESOLVED`. **No production value was invented.** It stays `immutable: false` until Rin freezes it.

Freeze remains **BLOCKED**: A1 no operator attestation of the real observation config; A2 information cutoff unknown; A3 no signed trade-plan semantics; A4 ADR-036/037 and protocol not accepted, and PR #79 defects open.

## 4. Track B — MT5 historical data inventory

Cloud session; **no MT5 history was or could be read**. Verified facts:

| Fact | Evidence |
|---|---|
| Tracked data files in the whole repo | exactly two: the FIX3 CSVs below |
| Historical extraction capability | none. `grep` for `copy_rates*`, `copy_ticks*`, `history_deals` across `packages apps scripts` returns nothing. `scripts/mt5_discover_symbols.py` is discovery only. |
| Order path | no `order_send`/`order_check` in code; only web tests asserting absence |

| File | Bytes | Lines (data rows) | SHA-256 |
|---|---|---|---|
| `tasks/evidence/FIX3-realtime-samples.csv` | 13339 | 64 (63) | `9c32f0e603cc88f5e4097ff556345c370b5050d49938e9436746d84b263fb55b` |
| `tasks/evidence/FIX3-sequence-matches.csv` | 35958 | 243 (242) | `31ae223462e843e32dbaced826f78426757ccb8cebb7d34bc0b5522b414dc110` |

(Hashes at main `36c90c5`; new evidence, not in the Oct 15 Track B report.)

**Finding B-1 (time semantics).** In all 63 rows of `FIX3-realtime-samples.csv`, raw `mt5_time_msc` is ahead of the UTC capture `timestamp` by 10799.5–10800.1 s (median 10800.0 s); the backend `event_time` is corrected by the same amount. Raw MT5 time is therefore **not UTC** for this feed. This is consistent with FIX2's explicit feed-specific correction and proves nothing about broker timezone or DST in general. Any historical export must record native timestamps plus an operator-declared, effective-dated offset per segment; it must not be inferred. Capture window: 2026-09-21T08:20:03Z to 08:21:08Z (about 65 s), so these files are operational parity samples, never a historical dataset, and they are already-seen data (never "unseen").

Remaining Track B blockers are those of #83 and stand: no approved historical dataset, no authenticity receipt, no both-side OHLC, no capability/cost schedules, no authorized extractor run. Extraction would be a WINDOWS-INTEGRATION run, operator-started against a named SHA; not authorized in this wave.

## 5. Track C — Quant Decision Register (delta)

The Oct 15 register (#84) was checked against protocol PR #81: **all 21 OPEN-VP ids occur in the protocol**, and #84 maps all 21 plus Q-T01…Q-T11. It selects no value. This session adds the rows below that the register does not contain. All are **BLOCKED / UNRESOLVED**; none has a proposed value.

| ID | Decision required | Owner | Source |
|---|---|---|---|
| Q-T12 | Decimal arithmetic/rounding semantics for costs and net PnL, bound into run identity | Architect + Quant | Finding D-2 |
| Q-D01 | Feed time-offset and DST policy per segment (declared, effective-dated, never inferred) | Data + Architect (see `tasks/TS1-time-semantics-proposal.md`, not reviewed by this session) | Finding B-1 |
| Q-D02 | Exposure ledger: FIX3 samples (2026-09-21) count as already seen | Quant | Section 4 |
| Q-DQ01 | Data Quality Guard thresholds: quote age, gap, latency, future skew, spread caps, `min_events`, zero-spread, bid/ask requirement | Quant | ADR-036 open item 4 |
| Q-DQ02 | Skip engine on bad data, or audit-then-deny | Quant + Rin | ADR-036 open item 2 |
| Q-DQ03 | `SequenceHistoryProvider` source and runtime wiring | Architect | ADR-036 open items 1, 3 |
| Q-DQ04 | Replay never re-authorizes: downstream recovery story | Risk/Authority owner | ADR-036 open item 6 |
| Q-DQ05 | Actionable decision without positive evidence is denied: confirm | Quant | ADR-036 open item 5 |
| Q-DQ06 | Residual secret-shape risk in append-only journal: accept or require closure | Security + Rin | ADR-036 open item 11 |

## 6. Track D — Independent inspection

### 6.1 PR #79 (EDGE engineering) — Track D findings re-confirmed, still open

- **D-1 MAJOR confirmed.** `ExitPolicy.__post_init__` validates concurrency, interval, max-hold and delay but not `ambiguity`; `simulate_exit` takes the stop only for the exact string `"stop_first"`. Reproduced: `'stop_first'` exits 95 (stop); `'target_first'`, `'typo'`, `None` and `0` all exit 105 (target). A malformed config silently turns a loss into a win.
- **D-2 MAJOR confirmed.** `compute_costs` and `to_edge_trade` use the ambient `Decimal` context; `run_id` hashes inputs and exit objects, not computed costs or context. Reproduced: precision 6 gives net `4.87654`, precision 28 gives `4.876543211…`; **identical `run_id`**. A hash-only reproduction check can confirm different financial output.
- PR #79 head is unchanged since 2026-10-13, so neither finding has been addressed.

### 6.2 PR #80 (Decision Audit + Data Quality Guard) — first independent review

Depth, stated honestly: read in full ADR-036, the AUDIT-DQ-1 task record, `data_quality/guard.py`, `decision_audit/gate.py`, `decision_audit/store.py` and the signal-eligibility logic in `builder.py` (`_resolve_signal`, `_eligibility`); ran all tests, ruff and mypy (V4–V6). **Not line-reviewed:** `models.py`, `invariants.py`, `payload_policy.py`, `identity.py`, `blocked.py` and the remainder of `builder.py` (about 1.2k lines); Security review not performed.

Result of the pass: **no CRITICAL or MAJOR code defect found.** The code matches ADR-036: stateless Guard re-validates numeric types, OHLC, sequence contiguity and clock; the gate owns the Guard so no caller-supplied verdict is accepted; eligibility follows the D3a order; replay is never eligible; the store is append-only with environment-qualified streams. Findings are about readiness, not correctness:

- **M-1 (MAJOR for readiness; matches the PR's own disclosure).** No runtime code uses either package. `grep` over `packages apps scripts infra` finds no import outside the new packages and tests (the two textual hits, a TypeScript union member and a SQL migration table name, are unrelated). Issue #78 asks for an audit that is mandatory and a hard NO NEW TRADE on invalid quality. As built, nothing forces a real decision through the gate. Merging #80 alone does **not** deliver System Test readiness.
- **M-2.** `SequenceHistoryProvider` is a `Protocol` with no implementation. Because the Guard is stateless, `ok` on a short snapshot proves internal consistency only, not stream continuity. Contiguity is mandatory inside a snapshot, so a sampled or filtered stream blocks as `sequence_gap` (ADR-036 open item 12).
- **M-3 (review evidence).** PRs #79, #80 and #81 have zero GitHub reviews and zero comments, while their task records cite several independent review rounds. Those rounds leave no verifiable trace on GitHub, so merge gates 2, 7 and 8 cannot be established from the repository. The #80 record itself says re-review of the round-4 delta and Security review are pending.
- **M-4 (minor, governance).** `CLAUDE.md` section 5 lists `ruff format --check .` as mirroring `validate.yml`, but the workflow does not run it, and main fails it on 24 files (V7). CI green therefore does not cover formatting.
- **M-5 (architecture note, not a blocker).** Three overlapping quality mechanisms now exist or are proposed: DQ1 monitor (main), `edge/dataset` audit (#79), `data_quality` Guard (#80). ADR-036 item 9 already asks for coordination; Architect should decide reuse before either is wired.
- Scope checks: #80 changes only new paths; no overlap with #79's files; `signals/**`, `research/**`, `execution/**`, `apps/**` untouched, per the PR file list.

### 6.3 Merge blockers (AGENTS.md section 20.3)

| PR | Blockers (gate numbers) |
|---|---|
| #79 | D-1 and D-2 unresolved MAJOR (3); no independent APPROVE on an exact head (2, 7, 8); ADR-037 Proposed, Rin decision outstanding (9); Q-T/OPEN-VP decisions unresolved (9) |
| #80 | No independent APPROVE of head `618ce87f…`; round-4 delta re-review pending (2, 7, 8); Security review pending for persistence/secrets (4); ADR-036 Proposed (9); M-1/M-2 mean readiness is not achieved by merge alone |
| #81 | DRAFT protocol, 21 open Quant/Rin decisions (9); no independent APPROVE (2) |
| #82–#85 | Docs-only reports by self-review; independent review pending; no code risk found in scope |

Gates 5 (CI) and 6 (mergeable/clean) are satisfied for all of the above. **No merge was performed or attempted.**

## 7. Next actions (in order)

1. **Rin:** decide ADR-037 and ADR-036 acceptance order; request the non-secret A1 attestation from the operator; approve a Windows-node extraction design (separate authority).
2. **PR #79 owner (Claude session owning `claude/edge-validation-v1`):** reject any `ambiguity` value outside the closed vocabulary with regression tests; make arithmetic semantics explicit and bind them into the run identity (Q-T12). Then independent delta review at the exact new head.
3. **Independent reviewer + Security** on #80 head `618ce87f…` (round 4), covering the files this session did not line-review. Post the verdict on the PR so gates 2, 7, 8 become checkable.
4. **Architect:** choose the sequence-history source and runtime wiring (Q-DQ03); a wiring PR must not touch `research/runtime.py` without checkpoint-equivalence tests.
5. **Quant:** sign Q-T01…Q-T12, Q-DQ01/02/05 and OPEN-VP-1…21 **before** any result exists, with the information cutoff.
6. **Housekeeping (separate PR):** reconcile `CLAUDE.md` section 5 with `validate.yml` and the 24 unformatted files (M-4).

## 8. Changed files and checks for this branch

Changed (docs only, both new, one directory): `docs/research/edge-validation/readiness-20261020/MASTER-REPORT.md`, `docs/research/edge-validation/readiness-20261020/BASELINE-V1-FREEZE-PROPOSAL.json`. No application, config, test, ADR or task file changed.

Docs checks: `git diff --check`; JSON parses; every cited main path exists at the base SHA. Application tests: **not_run** for this branch (docs-only; impact none). V1–V7 above are verification of other PRs' heads, not of this diff.

## 9. Confirmations

NO MERGE · NO PROD START · NO LIVE/DEMO/PAPER/AUTO ORDER · NO AUTO UNLOCK · NO HOLDOUT ACCESS OR UNLOCK · NO STRATEGY TUNING · NO MT5 ACCESS · NO SECRETS · NO BROKER OR SYMBOL ASSUMPTION · NO PROFITABILITY CLAIM. Self-review only for this report; independent review pending.
