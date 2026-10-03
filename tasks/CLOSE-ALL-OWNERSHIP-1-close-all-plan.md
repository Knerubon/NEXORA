# CLOSE-ALL-OWNERSHIP-1 — Pure CLOSE ALL plan layer

status: in_review (self-review; independent review pending)
translation_needed: false
base_commit: bc4cae2abb6539176c8d3cf3b7f0765ad1a6d1ec (origin/main, ADR-034 accepted)
branch: claude/close-all-ownership-v1
worktree: D:\NEXORA\NEXORA-CLOSE-ALL-OWNERSHIP
role: developer (Track F)

## Scope

Sources: [AGENTS.md](../AGENTS.md); [ADR-034](../docs/decisions/ADR-034-execution-contracts-v1.md)
sections 1, 3, 8, 9; [MANUAL-EXEC-1](MANUAL-EXEC-1-manual-execution-ui.md).

New pure module `packages/nexora/execution/close_all_plan.py` on top of the frozen
`owned_open_positions` / `build_manual_close_all_intents` (unmodified). `build_close_all_plan`
returns a `CloseAllPlan`: per-position entries (CLOSE intent, idempotency key, `AuthorityDecision`
from the existing `authorize_trade_intent`), `excluded_not_owned` (broker-only/external refs,
report-only), `excluded_not_open`, `excluded_out_of_scope`, and counts. NOT_TRANSMITTABLE/denied
entries stay listed and are never reported transmittable. No ExecutionRequest is created, no I/O,
no broker call. `execution/__init__.py` is intentionally not edited (import from the module).

## Invariants tested

External refs never become intents; non-OPEN/MANAGING excluded; symbol scope never widens;
retry byte-identical (plan + keys); empty owned set -> empty plan; duplicate position_ids or
external refs fail closed; unknown health override fails closed; frozen plan; no broker/DB imports.

## Execution record

- Tests: `tests/test_execution_close_all_plan.py` plus existing contract/autonomous/supervisor suites.
- Note: the venv editable install resolves `nexora` to a different worktree; tests were run with
  `PYTHONPATH=<worktree>/packages:<worktree>/apps/api`.
- Frozen-contract changes: none. Review: self-review; independent review pending.
