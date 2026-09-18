# CI1 — Restore Python validation

Status: in_review
Branch: codex/fix-python-ci-import
Base: d8568849fbc2b0eb161d961d6910db9c6ad940c9

## Scope and inputs
Requirements: docs/requirements.md (determinism, replay, research boundary).
Architecture: docs/architecture.md (engine and API separation).
Inputs: pyproject.toml, .github/workflows/validate.yml, failing signal/feed tests,
API bootstrap and research configuration for the local test hang, signal engine
annotations and readiness regression fixture for downstream validation failures.
User authorized fixing the reported errors and merging after checks.
No calculation, threshold, feed configuration, UI or backup tag changes.

## Root causes and changes
- Console pytest could not import tests.signal_pattern_golden. Add tests package marker.
- New local .env bootstrap caused API tests to replay workstation data. Isolate NEXORA
  runtime variables and journal per test; retain NEXORA_TEST_* integration settings.
- Resolve 52 lint findings using formatting/import cleanup and shorten a docstring.
- Resolve 18 typing findings with precise test annotations and two type-only casts:
  MT5 symbol name (external API string) and newly constructed non-null signal decision.
- Existing causal-prefix fixture now yields WAIT under high-volatility protection.
  Assert that empty result, then explicitly raise only that test's volatility threshold
  to exercise non-empty causal prefixes. Keep production/default configuration unchanged.

## Validation / handoff
- .venv/Scripts/pytest.exe -q: 94 passed, 1 skipped (PostgreSQL requires CI service).
- .venv/Scripts/ruff.exe check .: PASS.
- .venv/Scripts/mypy.exe: PASS, 85 source files.
- .venv/Scripts/python.exe scripts/recovery_drill.py: PASS (SQLite fresh-process recovery).
- git diff --check: PASS.
- Self-review; independent review pending. User explicitly requests merge.
- Next: run GitHub CI including PostgreSQL and web; merge only after success.
