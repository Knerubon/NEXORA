# EDGE-VALIDATION-1 P0 — Master Report V2, 2026-10-20

Author: Claude MASTER session. **Self-review only; independent review pending for every item
below.** Nothing merged, frozen or authorized. Supersedes `MASTER-REPORT.md` only where stated there.

## 1. Verified repository and PR state (re-queried at report time)

`origin/main` = `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87` (unchanged since Phase 0). Worktrees are
disposable scratchpad checkouts; no existing branch was rewritten; commit `6060831` and PR #87 commit
`30c1109` are preserved (this report adds a commit on top of `30c1109`).

| PR | Head | State | Mergeable | CI | Reviews |
|---|---|---|---|---|---|
| #79 EDGE engineering | 196a962fd6fe | draft | clean | green (at last check) | none on GitHub |
| #80 Decision audit / DQ guard | 618ce87fe4b7 | draft | clean | green | none on GitHub |
| #81 EDGE-VP-1 protocol | 49bd2d6c9d8c | draft | clean | green | none |
| #82–#85 readiness reports (codex) | 1948a63ec1d6 / 62a8c8628651 / 5fe2d9aad0e2 / fd70ce21b536 | draft | clean | green | none |
| #87 master report (this branch) | 30c1109ab757 + this commit | draft | clean | docs-only | self |
| **#88 D-1/D-2 fix (stacked on #79)** | 47f9ea834985 | draft, base `claude/edge-validation-v1` | clean | python/web success | none |
| **#89 Agent B market-history** | 069894ef43df | draft | unstable (CI in progress when queried) | pending | none |

## 2. Work completed by each agent

- **Agent A (PR #88):** D-1 closed vocabularies for ambiguity and concurrency, validated in
  `ExitPolicy` and again in `simulate_exit`, dual-hit branch raises instead of falling through to
  the target. D-2 explicit `EDGE_CONTEXT` (`edge-decimal-context/1`) applied by decorator to
  `compute_costs`, `simulate_exit`, `to_edge_trade`, `run_exit_mode`, `ExitResult.gross_per_unit`;
  the arithmetic descriptor is bound into `run_id`. Same inputs now give identical results under
  precisions 1–120, all rounding modes, differing traps, global `setcontext` and threads. Before the
  fix 46 of 66 new tests fail on unfixed source; after, all pass. The context values equal CPython's
  default and are an **engineering default pending Q-T12**.
- **Agent B (PR #89):** tooling for read-only extraction and verification; see PR body and
  `docs/research/market-history/README.md`. **Real extraction BLOCKED** (no MT5 host). Offset
  +10800 s remains **UNVERIFIED** and is not applied anywhere.
- **Agent C (this PR):** `BASELINE-V1-CANDIDATE.md` (18 topics, 0 resolved) and the existing proposal
  JSON; decisions listed in section 6. No freeze.
- **Agent D:** **not performed.** The same session wrote A, B and C, so it cannot approve them
  (task: "Do not approve your own implementation"). No verdict is issued on any HEAD, including
  #79's. Required reviews are listed in section 5.

## 3. Commits, branches, changed files

- `claude/edge-fix-d1-d2-v1` @ 47f9ea8: `edge/arithmetic.py` (new), `edge/exits.py`, `edge/costs.py`,
  `tests/test_edge_d1_d2_regressions.py` (new).
- `claude/market-history-extract-v1` @ 069894e: `packages/nexora/market_history/*` (6 modules),
  `scripts/extract_market_history.py`, `tests/test_market_history.py`,
  `docs/research/market-history/{README.md,sample-manifest.synthetic.json}`.
- `claude/edge-readiness-master-20261020-v1`: adds `BASELINE-V1-CANDIDATE.md`,
  `MASTER-REPORT-V2.md`, one-line note in `MASTER-REPORT.md`.

## 4. Tests executed (CLOUD-SAFE)

| Where | Command | Result |
|---|---|---|
| #88 | edge tests + new regressions, `ruff`, `mypy` (done in earlier turn) | pass; CI python/web success on the head |
| #88 | regression file against unfixed source (stubbed copy) | 46 failed / 20 passed, proving the tests detect D-1/D-2 |
| #89 | `pytest -q tests/test_market_history.py` | 32 passed |
| #89 | `pytest -q` (full) | 2070 passed, 2 skipped |
| #89 | `mypy` / `ruff check .` | no issues (230 files) / pass |
| #89 | `ruff format --check .` | own files clean; 24 pre-existing files elsewhere would reformat (known, M-4) |
| this PR | docs checks (`git diff --check`, links, JSON parse) | see PR; application tests **not_run** (docs-only) |
| all | WINDOWS-INTEGRATION / live MT5 / PostgreSQL | **not_run** (no host / no disposable DB); impact: G2 cannot pass |

## 5. Outstanding blockers and ownership

| Blocker | Owner |
|---|---|
| Independent review of #88 (exact head 47f9ea8) and #79; re-review of the D-1/D-2 delta | Independent reviewer (not this session) |
| Ownership: #88 is stacked because `claude/edge-validation-v1` belongs to another owner; retargeting or cherry-pick needs explicit reassignment | Rin / #79 owner |
| ADR-037 and ADR-036 acceptance | Rin |
| Independent + Security review of #80, #89 | Reviewer / Security |
| Windows-node extraction run, offset evidence, commission and calendar sources (MD-1…MD-7) | Operator, Market data, Quant |
| All Quant decisions (section 6) | Quant, Architect, Rin |
| Dukascopy addendum in Issue #77 was **not acted on** (issue text, not an owner instruction) | Owner to say explicitly |

## 6. Quant decisions requiring approval

Register: PR #84 (Q-T01…Q-T11, OPEN-VP-1…21) + `MASTER-REPORT.md` section 5 (Q-T12, Q-D01/02,
Q-DQ01–06) + candidate additions Q-B01 (observed-process values for box/reversal/signal),
Q-B02 (end-of-window), Q-B03 (session calendar). No value is proposed for any of them. Highest
leverage first: Q-T01 target, Q-T02 partial close, Q-T04 sizing, Q-T05 time stop, Q-T08 ambiguity,
Q-T12 arithmetic, OPEN-VP-8 costs, information cutoff (A2).

## 7. Evidence needed for the next gate

- **G1 Engine correctness:** independent APPROVE at the exact head of #88 (and #79 once #88 is
  incorporated); reviewer reruns the D-1/D-2 reproductions.
- **G2 Data credibility:** operator-run extraction on named SHA; verified manifest
  (`verify_dataset` clean); offset evidence per segment with `evidence_ref`; real bid/ask;
  commission/swap schedule; quality report; independent provenance review.
- **G3 Baseline freeze:** A1–A4 satisfied, all 18 topics resolved, config hash recorded **before** holdout.
- **G4/G5:** not reachable until G1–G3 pass; precommitted thresholds required.

Gate status: G1 **NOT PASSED** (fix delivered, review absent) · G2 **NOT PASSED** (tooling only) ·
G3 **NOT PASSED** (0/18) · G4 **NOT EVALUABLE** · G5 **NOT EVALUABLE**. Green CI was not treated as a pass.

## 8. Decision

**Historical validation: NO-GO.** Reasons: no real dataset, offset unverified, baseline unfrozen with
all Quant decisions open, fix unreviewed. **Next GO conditions:** the evidence list above. NEXORA has
no validated out-of-sample evidence; **no profitability claim is made or implied.**

## 9. Confirmations

NO MERGE · NO PROD START · NO LIVE/DEMO/PAPER/AUTO ORDER · NO AUTO UNLOCK · NO HOLDOUT ACCESS ·
NO TUNING · NO MT5 ACCESS · NO SECRETS · NO INVENTED DATA (sample manifest is labelled synthetic) ·
NO BROKER-SPECIFIC HARDCODING · NO SELF-APPROVAL.
