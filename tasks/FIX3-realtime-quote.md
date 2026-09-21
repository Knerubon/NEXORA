# FIX3 — Real-time quote delivery

status: in_review
base_commit: c902721
translation_needed: false

User-authorized scope: diagnose MT5 -> adapter -> live state -> publisher ->
/ws/events -> onmessage -> React -> Bid/Ask/event_time; fix delivery at <=1 second
while healthy. Preserve all engine/strategy/paper semantics. Only commit/push
codex/fix-realtime; no main merge, PR #21 changes, or tag operations.

Context paths and reasons (declared before loading):
- docs/requirements.md, docs/architecture.md: source-of-truth boundaries.
- docs/development.md, docs/research-runtime.md: validation and local runtime.
- docs/decisions/ADR-013-web-dashboard-realtime-contract.md: transport contract.
- skills/market-data/SKILL.md, skills/frontend/SKILL.md, skills/testing/SKILL.md:
  adapter, UI and regression workflow.
- apps/api/nexora_api, apps/web, packages/nexora/market_data,
  packages/nexora/research, tests, scripts: trace implementation, existing
  runtime launch and tests; inspect only relevant dependencies.
- pyproject.toml, package.json: available tooling.

Acceptance: >=60-second real-feed test with >=10 consecutive cross-layer
samples; latest quote displayed <=1 second, genuine advancing event_time,
one socket, bounded timers, unchanged P&F; existing tests/lint/typecheck.

## Execution record

Clean working tree on requested branch; existing reconnect/startup changes at
c902721 are baseline, not evidence of working real-time delivery.
Self-review; independent review pending.

### Root cause and implementation

Actual MT5 ticks advanced while `/quotes` remained at sequence 97 and `/state`
timed out after 15 seconds. `py-spy dump --pid 27156` found the `nexora-quotes`
thread in `observe_quote -> ingest -> _rebuild -> journal.iter_read -> _verify`;
the state endpoint waited for the research lock. One captured tick had
event_time `07:48:27.475Z` but received_at `07:48:27.363990Z`, violating the
existing pipeline chronology rule and triggering expensive journal recovery.
Even without recovery, the old reader waited for research work plus a one-second
sleep. `/ws/events` synchronously acquired the same state/engine path on the ASGI
loop; reconnecting could not remove either bottleneck.

- `quotes.py`: one 200-ms reader, one independently sampled research observer;
  idempotent start, bounded latest observation rather than a growing queue.
- `research.py`: reject future-at-receipt/out-of-order quotes before ingest's
  recovery path; retain raw/corrected timestamps and existing chronology rules.
- `main.py`: one shared sequential background research publisher; lightweight
  250-ms quote envelopes on the existing socket, independent of the engine lock;
  finish an in-flight publication before closing storage on shutdown.
- `page.tsx`, `live-quote.ts`: consume quote envelopes into independent React
  state; prevent delayed full state/REST from replacing a newer observation;
  expose the displayed observation sequence for DOM verification.
- Corrected a separate existing reconnect defect: onclose cleared socketRef
  before the scheduler compared it with the closed socket, so scheduling returned
  early. Generation checks now preserve one socket and one reconnect timer.
- No changes under `packages/nexora`, no strategy/config/formula changes, no
  fabricated prices/event times, no broker orders. Research remains sampled
  observation with unknown completeness. Reader observation sequence is not a
  market-tick count; repeated quotes do not create research events.

### Real-time evidence — 2026-09-21

Final production-build Chromium/MT5 run: **65.016 seconds**, all backend samples
live. The locally configured terminal, symbol, time correction, research config
and existing journal were retained. The API was restarted to load the fix;
recovery finished without clearing/replacing the journal.

Committed evidence:
- [63 consecutive approximately one-second cross-layer samples](evidence/FIX3-realtime-samples.csv)
- [242 sequence-matched DOM/backend observations](evidence/FIX3-sequence-matches.csv)
- [Summary and controlled reconnect counters](evidence/FIX3-realtime-summary.json)

The sampler directly read MT5, requested `/quotes`, intercepted real browser WS
messages before React onmessage, and recorded actual DOM mutations of the visible
Bid/Ask/event_time element. These reads are not atomic: raw CSV rows can show a
newer source/backend sequence than the last browser frame. Sequence-matched
comparison found **0 mismatches in 242 observations** (one of 243 DOM sequences
was not caught by the independent backend sampler and is not claimed verified); maximum observed backend
receipt-to-DOM latency was **302.530 ms**. Empty WS fields in the joined CSV mean
the DOM observation arrived via a full state frame, not an invented quote frame.

Observed distinct quotes: MT5 **389**, backend **239**, quote-only WS **200**;
**241** lightweight WS deliveries, **243** DOM observation updates and **202**
distinct displayed quotes (full state frames can also supply a newer quote).
These are sampled counts, not a lossless tick-capture claim.

Maximum intervals: WS **512 ms**, DOM **511 ms**, actual displayed quote/event-time
change **1168 ms**. The latter spans an actual MT5 quiet interval: corrected source
time stayed at 08:21:05.968Z until the next tick at 08:21:07.246Z. Heartbeat
delivery continued within 512 ms; no unchanged quote was fabricated as a new
market update. Healthy run: **1** socket opened, **0** closed, active/max active
**1**, **0** HTTP requests from the page during measurement, **0** pending
1–5-second timers, **0** browser errors. Afterwards an intentional close produced
exactly one 1000-ms reconnect timer and one replacement socket; maximum active
remained **1**, maximum pending reconnect timers **1**, pending timers after
recovery **0**.

Final run research events advanced **16096 -> 16125**, error remained null;
transitions stayed at 254 because no new transition was confirmed in this window.
The preceding 65.069-second live run observed **15808 -> 15823** events and
**236 -> 237** P&F transitions with error null, demonstrating real threshold-driven
P&F progress. Its maximum DOM interval was 334 ms. Final source differs from that
run only in safe publisher shutdown, the named reconnect callback and retry eligibility
for future-at-receipt quotes; engine
rules/configuration were unchanged throughout. Existing P&F/replay tests pass.

Local detailed capture and screenshot are retained (ignored) at
`data/runtime-repair/realtime-evidence.json` and `data/runtime-repair/realtime-ui.png`.
Capture command: `.tools/Scripts/python data/runtime-repair/validate-realtime.py`
(local diagnostic harness using Playwright Chromium and read-only MetaTrader5).
Its initial middle-dot encoding comparison bug was corrected; final results
above come from the rerun, not from changing any captured market values.

### Checks and handoff

| Exact command | Result |
|---|---|
| `.venv/Scripts/python -m pytest` | PASS: 135 passed; 1 PostgreSQL integration skipped because NEXORA_TEST_POSTGRES_DSN is not configured; 2 existing dependency deprecation warnings |
| `npm --prefix apps/web test` | PASS: 25 tests; existing module/title warnings |
| `.venv/Scripts/ruff check .` | PASS |
| `.venv/Scripts/mypy` | PASS: 89 files |
| `.venv/Scripts/ruff format --check apps/api/nexora_api tests/test_realtime_delivery.py tests/test_signal_intelligence.py` | PASS: 6 files |
| `.venv/Scripts/ruff format --check .` | FAIL: 15 untouched baseline files; no unrelated formatting changes |
| `npm --prefix apps/web run lint` | PASS |
| `npm --prefix apps/web run typecheck` | PASS |
| `npm --prefix apps/web run build` | PASS |
| `$env:NEXORA_JOURNAL_PATH = 'data/runtime-repair/smoke-realtime.sqlite'; .venv/Scripts/python scripts/smoke_api.py` | PASS: isolated loopback health; initial non-isolated attempt was stopped while waiting, not counted as pass |
| `git diff --check` | PASS |

New regressions cover blocked observer/publisher while subsecond delivery
continues, duplicate worker start, real chronology rejection, repeated/stale quote
handling (including retry after a future-at-receipt quote becomes eligible), delayed snapshot rollback and stream restart. The existing decision
serialization test now waits for the shared publisher while still asserting the
identical decision payload; its signal expectations are unchanged.

Self-review: transport ordering, shutdown/storage ownership, bounded threads and
timers, source identity/freshness, unchanged core diff, browser output and local
network boundary checked. No independent approval is claimed. Task remains
in_review, not done; next action is independent review of the draft PR. PostgreSQL
integration and the 15 baseline formatting failures remain explicitly unverified
or failing outside this fix's scope. No merge, PR #21 operation or tag operation.
