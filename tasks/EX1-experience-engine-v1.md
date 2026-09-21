# Experience Engine V1

status: in_review
translation_needed: false
base_commit: 81854bed9c24f5bb989e7f7d36142fdeac8ad312

## Assignment / context
User-authorized research-only Observe -> Freeze -> Track -> Measure -> Store.
Sources: [requirements](../docs/requirements.md), [architecture](../docs/architecture.md),
[root workflow](../AGENTS.md), [roadmap](../docs/roadmap.md),
[task mapping](../docs/decisions/ADR-007-task-roadmap.md).

Additional context authorized before inspection:
- packages/nexora/research/{pipeline,runtime}.py, storage.py, artifacts.py: live observation and persistence boundaries.
- packages/nexora/{signals,matrix,pnf,structure,market_regime,market_data} models/engines/repositories: available causal evidence and versions.
- apps/api/nexora_api/{main,research,quotes}.py: internal service integration and authenticated reads.
- infra/migrations, tests, pyproject.toml, .github/workflows, apps/web/package.json: migration and required regression conventions.
- docs/development.md, docs/research-runtime.md, tasks/FIX1-system-readiness.md, tasks/P8B-signal-intelligence.md: commands and dependency limitations.
- skills/{testing,postgres,fastapi,security}/SKILL.md: relevant repository implementation/review instructions.

## Scope / boundaries
Add immutable T0 snapshots, meaningful-transition dedup, raw future observations,
5/15/30/60 minute outcomes, persisted research lifecycle, read APIs and tests.
No changes to P&F/Matrix/Signal decision semantics, risk/paper execution, broker orders,
ML/LLM prediction, optimization or Strength-as-probability. WAIT is first-class.
UI deferred unless minimal integration is clearly supported.

## Dependency evidence
PR #20 confirmed merged; latest origin/main is its merge commit. Previous working tree clean;
separate worktree and requested branch created. Existing implementation/contracts under inspection.
Historical review/release limitations remain; this task does not mark prior tasks done.

## Contract / acceptance
Locked contract and acceptance are documented below.

## Execution record
See validation/handoff below. Self-review; independent review pending.

## Locked V1 contract (implementation target)
- Schema/measurement policy `experience-v1`; additive journal streams using migration 007,
  no new tables or destructive migration. PostgreSQL primary; explicit SQLite fallback unchanged.
- T0 = accepted event.received_at (knowledge time). Freeze serialized original recorded output,
  normalized event, runtime config/hash, available versions, source identity/hash and metadata.
  Do not regenerate decisions from current engine on recovery. Null for missing bias,
  session/news context, dataset version and unavailable metadata; preserve multiple patterns.
- Fingerprint = canonical SHA-256 of scope (runtime config hash + source/symbol/price source/units),
  action, decision engine/config versions, sorted Matrix name/direction/readiness/column IDs,
  confirmed structure pivots/levels (excluding repeated update timestamps), regime label/reason/config,
  all confirmed pattern evidence, and distinct evidence component/code/polarity + future conditions.
  Exclude quote price/time, poll/engine sequence, score/strength changes alone, latest signal ID,
  P&F extensions within a column, plan price drift and repeated source-reference churn.
  Compare only previous fingerprint in the same scope: A->B->A is three episodes.
  ID = SHA-256(policy, scope, T0 event identity, fingerprint). Config changes isolate scope.
- Horizon due = T0 + 5/15/30/60m. Eligible labels require event_time > T0 and received_at > T0,
  same scope; raw normalized facts persisted before any derived result. First eligible event
  at/after due supplies endpoint change with explicit actual time and delay. Excursions use
  samples with event_time <= due and received_at <= due only, never the late endpoint. No bar high/low excursion
  inference because current bar contract lacks interval start; retain OHLC as raw evidence.
- Measurement reference E = midpoint of available valid entry zone; otherwise T0 selected
  price. R = E-stop only for BUY with stop < zone.low; SELL uses stop-E with stop > zone.high.
  BUY MFE=max(0, max(P-E)), MAE=max(0,max(E-P)); SELL reverses signs. Empty horizon
  windows have null excursions. R metrics divide by R only when positive valid plan exists.
  Endpoint price_change=P_endpoint-P_T0; directional_change uses side; no fees/P&L inference.
  WAIT records unsigned market upward/downward excursions from T0 and price_change only;
  directional MFE/MAE/R/hits remain null even if malformed upstream WAIT carries a plan.
- Initial lifecycle OBSERVED (WAIT/unavailable) or SIGNAL_CREATED (BUY/SELL research decision).
  Hypothetical ENTRY_TRIGGERED then ACTIVE only at a future sampled price inside original
  zone with valid directional invalidation. T0 never counts as entry. Subsequent sampled
  TP1/TP2/invalidation crossings apply only after entry and when levels exist, terminal
  INVALIDATED/TP2 stops plan tracking. At final horizon CLOSED if entered, EXPIRED otherwise;
  WAIT CLOSED. These are research labels, never broker fills. Individual threshold hit facts
  remain separate from lifecycle, with missing levels null and unobserved hits unknown/null.
- No interpolation, post-restart downtime fabrication, future rewrites, news fetch, session
  inference, execution, parameter optimization or experience feedback into signal generation.
  Horizons without an eligible endpoint remain pending; completion means all four labels exist,
  not that capture was complete. Preserve gaps/completeness/quality and report sample coverage.
- Recovery replays committed runtime rows in order into deterministic append-only Experience
  artifacts; incomplete derived writes retry under stable keys. Single runtime writer convention.
  APIs read all persisted scopes: one/recent (bounded limit + offset)/outcomes/summary.
- Acceptance tests: independent numeric expectations, transitions/dedup/config identities,
  nested T0 immutability, prefix/future-injection/restart invariance, partial/missing data,
  multi-pattern and WAIT, lifecycle, failure recovery, PG/SQLite persistence, API guards,
  existing Python/web regression suites. UI deferred to preserve chart-first scope.

## Persisted schema / migration and recovery notes

No migration 008 is needed: all new records use the existing additive migration 007
`research_journal(sequence, stream, event_key, content_hash, payload)` and existing
canonical Decimal strings / UTC ISO timestamps. Unique `(stream,event_key)` rejects
conflicting content; original snapshots are never updated. No historical tables are dropped.

| Stream | event_key | Payload / order |
|---|---|---|
| `experience:v1:snapshots` | experience_id | schema/policy, scope, fingerprint, T0, action, immutable context_json |
| `experience:v1:{scope}:observations` | normalized event identity | raw normalized event, original completeness, nullable observation metadata |
| `experience:v1:{id}:lifecycle` | source event identity + transition suffix | ordered hypothetical state, entry time, observed hit facts, event/knowledge times |
| `experience:v1:{id}:outcomes` | 5 / 15 / 30 / 60 | immutable horizon label, due/actual times, endpoint reference/price, sampled excursions, conditional R, quality/coverage and plan-hit facts |

The runtime source log remains authoritative. Its only additive envelope field is
`observation_metadata`; old rows decode with null metadata. Original committed output is
used on recovery, including legacy missing decision/strength fields, without inferring a
current decision from `signals.latest`. Missing decision is null/OBSERVED, not invented WAIT.
Feed callback passes available quote raw timestamp/correction and quality/status metadata;
recorded datasets retain supplied completeness and normalized gap fields. No GOOD default.
Data-version/dataset-version identifiers are not in the current runtime ingest contract:
`data_version` is null; normalized event/source identities and hashes remain available.
Config contains each independent resolution's box/reversal/sizing rules; actual effective
box sizes and column facts come from recorded transitions, not nominal config guesses.

Write order is runtime event/output commit -> existing Paper processing -> Experience raw
fact -> new snapshot/initial state if needed -> lifecycle changes -> horizon labels.
Each append is transactional, but the whole chain is not a new cross-stream transaction.
Crash/failure can leave projections partially materialized; deterministic replay fills only
missing keys and conflicting content fails visibly. Pending-horizon recovery was tested
in a fresh Python process. Runtime ingest failure remains visible through existing error
handling. Retrying a standalone `ExperienceService` after failure requires recreating it
and replaying committed runtime rows; public recording is the runtime's internal call.

One writer per configured runtime remains the supported operating model. Starting the new
code on an existing log materializes Experience from its original output artifacts. Config
changes create another scope; old inactive scopes stay readable and remain pending until
that original runtime/feed resumes. There is no wall-clock scheduler, invented downtime
price, automatic backfill or cross-config outcome matching. Snapshot/outcome hashes detect
accidental corruption, not malicious database administration. Recovery/downgrade: back up the
journal with the existing supported mechanism, retain all Experience streams, and use an
older application without deleting rows; old code ignores the new streams. Do not change V1
fingerprint/formula code in place after deployment: use a new policy namespace/version.

## Measurement clarifications

Excursions are T0 setup-relative observations, including samples before hypothetical entry
and after hypothetical terminal hits, not realized/mark-to-market trade P&L. Original engine
entry zone, TP values and R:R are stored unchanged. Midpoint reference E and R above are
explicit Experience measurement conventions, not a rewrite of Signal Engine's R:R formula.
Without a valid zone use T0 selected price for directional excursions, and no R normalization.
WAIT/unavailable decisions get market upward/downward excursions and endpoint price change;
no direction, MFE/MAE/R or trade result is fabricated.

A horizon endpoint may be late: price_change describes its actual timestamp, while excursion
and hit fields use only facts with event_time and received_at at/before the exact due time. The same late observation can be the endpoint of
several horizons. Empty in-window samples yield null excursions, zero sample_count and
`no_in_window_samples`; observed extrema are lower bounds on a continuously sampled path.
No extrapolation, intrabar extrema/path guessing, bid/ask fill assumptions, costs or P&L.
`entry_observed_by_horizon` separates setup movement from a sampled hypothetical entry.
A null hit means missing level or no observed post-entry hit (not proof the level was never
hit); the frozen plan distinguishes availability. Invalid/inverted stop gives no R/entry.
Targets must be on the favorable side of E. Entry and invalidation are not inferred at T0.
Raw outcome API pagination stops at the final horizon endpoint when completed.

## Read API contract

All endpoints use existing local-only client/Origin restrictions; no recording POST exists.
No remote-authentication claim. Schema version 1; missing Experience -> 404; invalid page
bounds -> 422. `/experiences?limit=20&offset=0` sorts by (T0, experience_id) descending,
limit 1..200. `/experiences/summary` returns total/completed/pending, BUY/SELL/WAIT counts
and unavailable_decision count. Completed = four persisted horizon labels, not complete
market-data coverage. `/experiences/{id}` includes frozen context plus separate lifecycle;
`/experiences/{id}/outcomes?limit=100&offset=0` includes the four available outcomes and
paginated shared raw observations. Reads span all saved scopes even without active runtime.

## Explicit exclusions / remaining operational limits

UI deferred; no chart/Matrix overlay/Full Analysis changes. No new pattern triggers,
Signal/P&F/Matrix/Regime/Structure rules, Strength/Score semantics, strategy optimization,
news API, session inference, predictive ML/LLM, broker orders, live trading, Risk expansion
or Paper execution changes. No probability or performance improvement claims.

Journal reads/recovery scan retained history; capacity, retention/indexing/checkpointing
and multi-writer operation remain uncertified, consistent with existing runtime limits.
PostgreSQL target-host backup/restore/crash and real MT5 lossless coverage are not certified.
Independent Rin/architecture/quant/security review remains pending; status is in_review
only after validation, never done/approved merely because a draft PR exists.

## Validation and handoff (local, 2026-09-20)

status: in_review
from: developer (self-review)
to: Rin / independent tester-reviewer
base_commit: 81854bed9c24f5bb989e7f7d36142fdeac8ad312
branch: codex/experience-engine-v1
commit: implementation commit containing this record; final hash in PR

- Targeted `.venv/Scripts/python -m pytest -q tests/test_experience.py tests/test_experience_api.py tests/test_experience_postgres.py`:
  initial 24 passed/1 skipped; expanded run 31 passed/1 skipped. Fresh-process recovery added
  afterward is included in the full-suite result below.
- `.venv/Scripts/python -m pytest -q`: 158 passed, 2 skipped (both require isolated PostgreSQL;
  no local test DSN configured), 2 existing Starlette/httpx deprecation warnings.
  Includes 32 new passing Experience tests plus PG integration wired to existing CI service.
- `.venv/Scripts/ruff check .`: PASS.
- `.venv/Scripts/mypy`: PASS, 95 source files.
- `.venv/Scripts/ruff format --check packages/nexora/experience packages/nexora/research/runtime.py apps/api/nexora_api/main.py apps/api/nexora_api/research.py tests/test_experience*.py`:
  PASS, 11 files. Whole-repository `ruff format --check .` reports the same 15 pre-existing
  unformatted files outside this diff; not a CI gate, unrelated formatting preserved.
- `npm --prefix apps/web test`: PASS, 23 tests, including unchanged chart render comparisons,
  independent strengths, WAIT, Matrix overlay and Full Analysis. Existing module-type/SVG-title warnings.
- `npm --prefix apps/web run lint`, `npm --prefix apps/web run typecheck`,
  `npm --prefix apps/web run build`: PASS.
- `.venv/Scripts/python scripts/recovery_drill.py`: PASS, existing SQLite backup/fresh-process
  paper/risk recovery. New Experience fresh-process pending-horizon test also passes.
- `.tools/Scripts/uv lock --check`, `.tools/Scripts/uv build`: PASS; no dependency/lockfile changes.
- `.venv/Scripts/python scripts/smoke_api.py`: PASS in isolated child environment, temporary
  journal, inherited NEXORA_* values removed, NO_PROXY=localhost,127.0.0.1. No host settings changed.
- `git diff --check`: PASS. PostgreSQL CI evidence to be recorded after push.

Self-review covered append-only identity/conflict protection, failure after runtime commit,
old schema/default handling, causal prefix replay and changed-engine restart, frozen plan
vs later plan drift, direction math/late endpoints, source/config isolation, API read/local
boundary and unchanged engine/paper/broker modules. No independent approval is claimed.

No unresolved local acceptance failures. Risks/deferred: local PostgreSQL evidence unavailable,
CI PG pending, UI deferred, historical/config scopes only advance when resumed, sampled-only
coverage and unbounded journal/replay scaling, independent review and target-host operations.
EXPIRED at +60m is the Experience tracking-window label, not a change to event-based signal
expiry. TP/stop labels apply after sampled entry only; terminal outcome does not stop market
measurement. Review exact fingerprint/formulas before deployment; no merge/tag/release authorized.

Changed scope: packages/nexora/experience (5 files), research/runtime.py, API main.py/research.py,
three Experience test modules, requirements/architecture additive notes, and this task.
Migration: reuse 007; no SQL schema change or data rewrite.
Next action: open draft PR, inspect CI, then Rin performs independent review. Do not mark done.

## PR #21 Rin review corrections (2026-09-21)

Review: https://github.com/Knerubon/NEXORA/pull/21#issuecomment-5754354837
Scope: horizon causal-prefix measurement and out-of-order lifecycle regression only.
Each horizon reconstructs lifecycle from the initial frozen plan using only observations
whose event_time AND received_at are <= due, preserving original journal receipt order.
The late endpoint supplies endpoint price/change only, never in-window excursions or hits.
A delayed market observation cannot backdate a plan hit before entry or a prior lifecycle
hit. Raw facts remain retained; completed labels are never revised by later arrivals.
This is a pre-approval correction to the draft V1 contract, not a new measurement feature.
Validation (Windows / Python 3.13, same branch):
- `.venv/Scripts/python -m pytest -q tests/test_experience.py tests/test_experience_api.py tests/test_experience_postgres.py`: 47 passed, 1 skipped (local PostgreSQL DSN unavailable).
- `.venv/Scripts/python -m pytest -q`: 173 passed, 2 skipped (PostgreSQL), 2 dependency deprecation warnings.
- `.venv/Scripts/ruff check .`: PASS.
- `.venv/Scripts/mypy`: PASS, 95 source files.
- `.venv/Scripts/ruff format --check packages/nexora/experience/engine.py packages/nexora/experience/service.py tests/test_experience.py`: PASS.
- `.venv/Scripts/ruff format --check .`: same 15 pre-existing unformatted files outside this fix; not modified, not a CI gate.
- `.venv/Scripts/python scripts/recovery_drill.py`: PASS (SQLite fresh-process recovery).
- `.tools/Scripts/uv lock --check` and `.tools/Scripts/uv build`: PASS.
- `git diff --check`: PASS.

15 added regression cases cover Rin's delayed-entry timeline, receipt/market-time separation,
backdated TP/stop rejection, exact-due inclusivity, causal-only lifecycle reconstruction,
and original runtime journal replay at every timeline interruption. Existing future-injection
and frozen-snapshot tests remain passing. No SQL/schema, Signal/P&F/Matrix, UI or API changes.
CI including PostgreSQL and web validation will be linked on the PR for the pushed fix commit.
Self-review; independent Rin re-review pending. No merge or tag operations performed.
Existing draft-V1 artifacts are never overwritten: any conflicting pre-fix projection still
fails visibly through the existing journal conflict guard; no silent historical migration.

## Authorized merge integration (2026-09-21)
User explicitly requests latest main including real-time fixes, then merge PR #21.
This supersedes this task's earlier draft-only/no-merge constraint.
Additional inputs: tasks/FIX2-api-startup-recovery.md, tasks/FIX3-realtime-quote.md,
tests/test_startup_recovery.py, tests/test_realtime_delivery.py, and main's changes to
storage.py/pipeline.py/quotes.py/main.py/research.py/runtime.py: preserve startup recovery,
quote/research decoupling and bounded snapshots while integrating Experience recording.
Base main fetched at f5f388f (merged PR #22). Clean worktree before merge; conflict in
research/runtime.py requires explicit integration. Self-review; no new independent approval claimed.

Integration resolution and evidence:
- Preserve main's iter_read + no-snapshot engine.replay + progress logging, alongside
  Experience recovery from original recorded output. No real-time publisher/reader rollback.
- Keep original local-client checks in HTTP/WS as well as main's origin handling; a
  nonlocal client spoofing localhost headers must not gain access to research history.
- Adapt the synthetic fake pipeline to both process/replay entry points. All no-lookahead
  assertions retained; real-engine replay parity and streaming recovery tests remain passing.
- Targeted Experience/API/startup/realtime tests: 56 passed.
- `.venv/Scripts/python -m pytest -q`: 182 passed, 2 PostgreSQL skips locally.
- `.venv/Scripts/ruff check .`, `.venv/Scripts/mypy`: PASS (97 source files).
- Changed-file Ruff format check: PASS (3 files).
- `npm --prefix apps/web test`: 25 passed; lint, typecheck, build: PASS.
- `.venv/Scripts/python scripts/recovery_drill.py`: PASS (SQLite).
- CI with PostgreSQL required on the integrated commit before user-authorized merge.
Self-review of integration completed; user merge authorization supersedes prior no-merge
instructions. No independent approval or target-host production certification invented.
