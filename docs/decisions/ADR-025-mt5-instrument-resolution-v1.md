# ADR-025 — MT5 multi-broker instrument resolution V1

Status: **DRAFT rev 2 — NOT ACCEPTED.** Decisions D1–D5 are resolved per Rin's Phase 1
resolution. Remaining architect questions (§14) and one Quant decision (§7) are open.
Awaiting Rin's final architecture review. No implementation may start until accepted.

Workstream: MT5 Multi-Broker V1 — branch `claude/mt5-multi-broker-v1`,
worktree `D:\NEXORA\NEXORA-MT5-BROKER`, base `origin/main` 4f69e9c (unchanged since rev 1).

Related: [ADR-003](ADR-003-local-price-preview.md) (read-only MT5 boundary — **authoritative,
not amended**), [ADR-004](ADR-004-market-data.md), [ADR-013](ADR-013-web-dashboard-realtime-contract.md),
[ADR-019](ADR-019-explicit-feed-time-correction.md) (legacy feed offset),
[ADR-022](ADR-022-startup-recovery-checkpoint-v1.md) (checkpoints),
[environment isolation](../environment-isolation.md).
Drafts on other branches (not on main, not modified by this ADR): ADR-023/024
(`claude/pnf-pattern-engine-v1`), ADR-026 (`claude/m30-next-candle-bias-v1`),
ADR-027 (`claude/research-journal-payload-v2`).

## 1. Context — current state (verified at 4f69e9c)

Two MT5 read paths exist:

| Path | Used by | Symbol handling |
|---|---|---|
| `Mt5Source` in `apps/api/nexora_api/quotes.py` | live runtime (quotes, research observation) | `NEXORA_MT5_SYMBOL` optional; `_select_symbol` picks the first visible symbol when unset, and switches symbol when the configured one becomes invisible |
| `Mt5ReadOnlyMarketDataAdapter` in `packages/nexora/market_data/adapters.py` | P2 adapter, tests only | exact configured symbol, fails closed |

Findings that constrain this ADR:

1. `_select_symbol` (introduced in a450123, pinned by
   `tests/test_feed_time.py::test_dynamic_mt5_symbol_selection_uses_visible_symbol`) violates
   accepted ADR-003 ("runtime must set `NEXORA_MT5_PATH` and `NEXORA_MT5_SYMBOL`; never search
   and re-select terminal/symbol").
2. No broker identity is read. Provenance is `source="MT5-quote-observation:time-offset=N"`,
   `source_event_id="{identity}:raw-time=…:offset=N"`, identity key
   `"quote:" + canonical_hash((symbol, event_time, bid, ask, raw_event_time, offset))`
   (`observe_quote` in `apps/api/nexora_api/research.py`). Same-name symbols from two brokers
   are indistinguishable.
3. The broker symbol string is the only instrument identity across P&F, signals, matrix,
   structure, trendline, risk and paper. Research stream id = `"research:" + canonical_hash(RuntimeConfig)`
   (`packages/nexora/research/runtime.py`), so any `RuntimeConfig` field change creates a new
   stream (also noted by ADR-026 and ADR-027).
4. `observe_quote` journals `observation_metadata = {"quote": Quote.model_dump(), "feed":
   {"quality": …, "status": Snapshot.model_dump()}}` (wired in `apps/api/nexora_api/main.py`).
   **Any new field on `Quote` or `Snapshot` changes new journal rows.** ADR-027 classifies
   `observation_metadata` as authoritative fact.
5. Only `symbol_info().digits/visible` and `terminal_info().connected` are read.
6. `NEXORA_MT5_TIME_OFFSET_SECONDS` (ADR-019) is global but describes one broker feed.
7. MetaTrader5 5.0.6180, inspected statically (import only, no `initialize`): `SymbolInfo`
   exposes `currency_base`, `currency_profit`, `currency_margin`, `trade_calc_mode`,
   `trade_contract_size`, `chart_mode`, `digits`, `point`, `trade_tick_size`, `custom`,
   `visible`, plus volatile/statistical fields; `TerminalInfo` exposes `company`, `name`,
   `path`, `build`, `connected` and also `community_account`, `community_balance`, `mqid`,
   `data_path`, `commondata_path`. No session-schedule API exists; `session_*` fields are
   statistics. The trade server name exists only in `account_info()`.
8. Only tests/fixtures hard-code symbols (`XAUUSD`, `XAUUSD-STD`, `EURUSD`). No production code
   assumes a symbol, server or suffix convention.

## 2. Architecture

```text
MT5 terminal (NEXORA_MT5_PATH; existing psutil "already running" check; never launched)
  │ read-only allowlist (§4.3): terminal_info.{connected,company,path},
  │                              symbol_info(<exact symbol>), symbol_info_tick, shutdown
  ▼
BrokerIdentity   declared in config ── validated against observed ── else fail closed
  ▼
FeedBinding      instrument + broker + exact symbol + price grid + time contract
  │ exact lookup; every identity field validated; feed_id pinned per process
  ▼
Canonical instrument_id  →  NormalizedPriceEvent.symbol  →  pipeline / P&F / consumers
```

The resolver **validates declarations; it never infers identity.** It is a pure module with no
MT5 import (the terminal read is injected), so it is testable offline.

## 3. D1 — Canonical instrument contract

`InstrumentDefinition` is operator-declared. NEXORA never derives an instrument from broker
symbol spelling.

| Field | Source | Verified read-only? | Identity-bearing |
|---|---|---|---|
| `instrument_id` | operator | no — declaration | yes |
| `currency_base` | `SymbolInfo.currency_base` | yes, exact string | yes |
| `currency_profit` | `SymbolInfo.currency_profit` | yes, exact string | yes |
| `trade_calc_mode` | `SymbolInfo.trade_calc_mode` | yes, exact int (`SYMBOL_CALC_MODE_*`) | yes |
| `trade_contract_size` | `SymbolInfo.trade_contract_size` | yes, exact Decimal (§6.2) | yes |
| `chart_mode` | `SymbolInfo.chart_mode` | yes, exact int (`SYMBOL_CHART_MODE_*`) | yes |
| `description` | operator | no | no — informational, never matched |

Considered and excluded from identity in V1:
- `currency_margin`, `margin_*`, `volume_*`, `swap_*`, `trade_tick_value*`, `spread*`, `bid/ask/last*`,
  `session_*`: account/trading/volatile values that change harmlessly or vary with the account.
- `description`, `path`, `exchange`, `category`, `basis`, `isin`: broker-authored text. They are
  shown by discovery and never used for identity. (`isin` as an optional declared exact-match
  field is a possible later extension, not V1.)

Rules:
- `instrument_id`: non-empty, `[A-Za-z0-9._-]`, max 64 chars, immutable once used by a stream.
  It is a NEXORA name. It is not required to equal, or differ from, any broker symbol.
- Two feeds are the same canonical instrument only if every identity-bearing semantic field
  matches. Same symbol name + different contract size (or calc mode, or currencies, or chart
  mode) → `instrument_metadata_mismatch`. Nothing is specific to XAUUSD or any suffix style.

## 4. D1 — Broker identity (Option A; no `account_info`)

### 4.1 Contract

```text
BrokerIdentity (operator-declared)
  broker_id: str          # operator label, same charset as instrument_id; used in provenance
  company: str            # expected exact value of terminal_info().company
  terminal_path: str      # expected terminal64.exe path; compared normalized (normcase+abspath)
```

Observed (read-only): `terminal_info().company`, `terminal_info().path` and the running
process exe path (existing psutil check).

Resolution: select the broker whose `terminal_path` equals `NEXORA_MT5_PATH` (normalized).
None → `broker_not_bound`; more than one → `broker_ambiguous`. Then observed
`terminal_info().path` must match the terminal directory and observed `company` must equal
the declared `company` exactly. Otherwise → `broker_identity_mismatch`. Config validation
rejects two brokers with the same `(company, terminal_path)` as `instrument_config_invalid`,
because they could not be told apart.

### 4.2 What V1 can and cannot verify

| Claim | V1 |
|---|---|
| Terminal installation used | verified (process exe + `terminal_info().path`) |
| Terminal company string | verified (`terminal_info().company`) |
| Symbol semantic contract + price grid | verified per symbol (§3, §6) |
| Connected trade server / account | **not verifiable** without `account_info()`; not read |
| Whether `company` reflects the connected server rather than the terminal's distributor | **not verified**; needs a DEV read-only probe before acceptance of implementation (§12) |
| Market hours / server timezone | not available; offset is declared (§8) |

Residual risk: one terminal installation re-logged into a different server of the same company,
with identical symbol metadata, is undetectable in V1. Operational rule: **one dedicated
terminal installation per `broker_id`**. Any detectable metadata change still fails closed.
Accepting this residual risk is open question A2 (§14).

### 4.3 ADR-003 compatibility

- No `account_info`, `login`, `positions_*`, `orders_*`, `history_*`, `order_*`, `symbol_select`,
  `market_book_*` or `copy_*` call is introduced. No credential, login, account number, balance,
  equity, position or order is read.
- `terminal_info()` fields read: `connected`, `company`, `path` only. `community_*`, `mqid`,
  `data_path`, `commondata_path` and all other fields are never read or copied.
- ADR-003's data line says "terminal_info (read connected only)". Reading `company` and `path`
  is terminal metadata, not account data, but it is a literal extension of that line.
  Rev 2 treats ADR-003 as authoritative and does **not** amend it. Rin must rule whether this
  read is within ADR-003's intent (open question A1). If it is not, binding mode can only
  verify the terminal path, broker identity cannot be established, and binding mode stays
  unavailable (fail closed). Legacy mode then keeps reading `connected` only.

## 5. D3 — Symbol resolution (exact binding only)

Runtime resolution is exact. The resolver:

1. uses exactly the bound broker symbol (binding mode) or `NEXORA_MT5_SYMBOL` (legacy mode);
   case-sensitive; no trimming, prefix, suffix, fuzzy or similarity logic;
2. calls `symbol_info(symbol)`: `None` → `symbol_not_found`; `visible` false →
   `symbol_not_visible`; `custom` true → `instrument_metadata_mismatch` (custom symbols can be
   fabricated locally);
3. never calls `symbols_get()` at runtime, never calls `symbol_select()`, never switches
   symbols, never picks the first visible symbol;
4. with no symbol/binding configured → `not_configured`.

`Mt5Source._select_symbol` and its test are technical debt from an accepted-ADR violation. The
implementation removes them and replaces the test with fail-closed tests (§13).

**Operator migration gate (before implementation merges):** the operator confirms, without
sharing values, that the DEV and PROD environments set `NEXORA_MT5_SYMBOL` (legacy) or a
binding (binding mode). Today an unset symbol auto-selects; after the change it reports
`not_configured`. This architecture phase did not read any `.env` file.

## 6. D2 — Feed binding and `feed_id`

### 6.1 Contract (frozen by this ADR on acceptance)

```text
FeedBinding (operator-declared)
  instrument_id: str
  broker_id: str
  symbol: str                        # exact broker symbol
  price_grid:
    digits: int                      # 0..10
    point: Decimal string
    trade_tick_size: Decimal string | null   # null = observed value must be 0 (not provided)
  time_offset_seconds: int           # [-50400, 50400], same range as ADR-019
```

Validation on every resolve: observed `digits`, `point` and `trade_tick_size` must equal the
declared ones (§6.2). Any difference or unreadable attribute → `instrument_metadata_mismatch`.

### 6.2 Deterministic comparison and serialization

- Observed MT5 floats are converted with `Decimal(str(value))` (shortest round-trip repr). Config
  values are decimal strings. They are compared with Decimal equality (`"0.01" == "0.010"`).
- Ints are compared exactly; strings exactly, case-sensitive, no normalization.
- `feed_id` uses the repo's `canonical_hash` (`packages/nexora/artifacts.py`): SHA-256 of
  sorted-key compact JSON, with Decimal serialized by `canonical_serialize` (trailing zeros
  stripped).

### 6.3 `feed_id`

```text
feed_id = "mt5f1-" + canonical_hash({
  "contract": "adr025-feed-v1",
  "instrument": {instrument_id, currency_base, currency_profit,
                 trade_calc_mode, trade_contract_size, chart_mode},
  "broker": {broker_id, company},
  "symbol": <exact broker symbol>,
  "price_grid": {digits, point, trade_tick_size},
  "time_contract": {"version": "adr025-binding-v1", "offset_seconds": time_offset_seconds}
})
```

- Computed from **declared** values. Those values are validated equal to the observed values
  before use, so `feed_id` is reproducible offline from config alone.
- Identity-bearing: exactly the fields above.
- Not identity-bearing (verification only, or harmless variation): `terminal_path` (host
  install location), terminal `build`/`name`, `description`, `visible`, all volatile fields,
  environment name. DEV and PROD reading the same broker feed have the same `feed_id`.
- Never included: credentials, login, account number, server name, balance, local data paths.
- Any change to broker, symbol, semantic contract, price grid or offset produces a new `feed_id`.

### 6.4 Reconnect contract

- On process start and after **every** successful `initialize` (reconnect), the full resolution
  (§4.1, §5, §6.1) runs again and the result is compared with the pinned identity:
  binding mode compares `feed_id`; legacy mode compares an in-memory
  `legacy_session_fingerprint = canonical_hash((company, symbol, semantic fields, price grid,
  offset))` pinned at the first successful resolve. The fingerprint is never journaled or exposed.
- Any difference → `feed_identity_changed`: quote status `error`, no quotes delivered, research
  not fed. It is latched for the process. Recovery is an operator restart after config review.
  The old stream never silently continues.
- `terminal_info().connected == False` stays `terminal_disconnected` (existing behavior).
  A reconnect with unchanged identity resumes normally.

### 6.5 Research stream effect

- **Binding mode:** `NormalizedPriceEvent.symbol = instrument_id`, and the research config's
  `signals.symbol` must equal the selected `instrument_id` (`research_instrument_mismatch`).
  The stream is pinned to one feed: at startup the API composition layer appends
  `(stream=runtime.stream + ":feed", key="feed", value={"mode": "binding", "feed_id": …})` using
  the existing idempotent `Journal.append`. A different `feed_id` on an existing pin raises the
  journal's identity conflict, which is surfaced as `research_feed_mismatch`: research is not
  fed; quotes still display. No `RuntimeConfig` field is added, so this needs no change to
  `runtime.py` or `checkpoint_state.py`.
- **Starting a new stream after a broker change** requires a distinct `RuntimeConfig` hash. The
  mechanism for that is open question A3. Until A3 is decided, a broker change in binding mode
  fails closed (`research_feed_mismatch`). That is safe but requires operator action.
- **Mode crossing is never silent:** binding mode on a stream that already has research rows
  but no feed pin → `feed_mode_conflict`. Legacy mode on a stream that has a feed pin →
  `feed_mode_conflict`. This matters because an operator may choose an `instrument_id` equal to a
  legacy broker symbol, which would otherwise produce the same config hash.

## 7. D4 — Price grid vs P&F configuration (QUANT boundary)

- Architecture captures and validates `digits`, `point` and `trade_tick_size` (price grid) and
  `trade_contract_size` (semantic contract). A mismatch fails closed.
- The price grid is provenance and a validation input. At startup the resolver **reports**, as
  informational diagnostics (not errors), how `PnfConfig.box_size` and `price_precision`
  relate to `trade_tick_size`/`point`/`digits`.
- ADR-025 does **not** alter, round or rewrite `box_size`, `price_precision` or any P&F config.
  No existing accepted ADR defines a mapping from broker tick size/digits to box size. ADR-005
  and ADR-006 define box rules in configured absolute units, and `observe_quote` sets event
  `precision = quote.digits`, which is existing behavior and remains unchanged.
- **QUANT DECISION REQUIRED (Q-Q1):** whether a compatibility rule is enforced (for example, box
  size a multiple of tick size, or precision equal to digits) and what it is. Until then:
  report only.

## 8. Time contract

**Legacy mode:** ADR-019 unchanged, byte for byte. `event_time = raw epoch − NEXORA_MT5_TIME_OFFSET_SECONDS`,
raw time and offset retained, identical `source`/`source_event_id`/identity-key formats. ADR-026
identifies this as `time_contract = "legacy-adr019"` with its own `feed_key`.

**Binding mode:** the binding owns the time contract, version string **`adr025-binding-v1`**:
- `event_time = datetime.fromtimestamp(time_msc/1000, UTC) − binding.time_offset_seconds`
  (the same arithmetic as ADR-019, with the offset taken from the binding);
- `raw_event_time` and `time_offset_seconds` are retained in the quote;
- the offset is declared, never auto-detected; freshness (`stale` > 10 s, `clock_skew` < −5 s)
  operates on corrected `event_time`, as today;
- the time contract (version + offset) is part of `feed_id`;
- `NEXORA_MT5_TIME_OFFSET_SECONDS` present in binding mode → `time_offset_conflict`.

Downstream consumers (for example M30 Bias per ADR-026 Q-M7) receive `event_time` already
defined by this contract. They never read, compute or re-apply a broker offset or timezone.
Market hours are never inferred from MT5 `session_*` statistics. Closed markets appear as
`stale`.

## 9. Modes and backward compatibility

Mode selection happens at startup and is fixed for the process:

| `NEXORA_INSTRUMENTS_CONFIG` | `NEXORA_MT5_INSTRUMENT` | `NEXORA_MT5_SYMBOL` | Result |
|---|---|---|---|
| set | set | unset | **binding mode** |
| set | unset | — | `not_configured` |
| set | — | set | `feed_mode_conflict` |
| unset | set | — | `feed_mode_conflict` |
| unset | unset | set | **legacy mode** |
| unset | unset | unset | `not_configured` (was: auto-select) |

### 9.1 Legacy mode — promises

Byte-identical to 4f69e9c:
- `NormalizedPriceEvent` `identity_key`, `source`, `source_event_id`, `symbol`, `precision`;
- `Quote.model_dump()` and `Snapshot.model_dump()`, including the journaled
  `observation_metadata`: new fields are **absent** (not `null`) in legacy mode;
- research stream id (no `RuntimeConfig` change), no new journal stream, no feed pin;
- ADR-019 offset semantics and the ADR-022 checkpoint format.

Changed in legacy mode (intentional; fail closed only):
- no auto-selection (`not_configured`), no mid-session switch (`symbol_not_visible`);
- reconnect revalidation against the in-memory legacy fingerprint (`feed_identity_changed`).

Honest caveat: ADR-022's `code_fingerprint` covers all `nexora` package code, so deploying the
implementation invalidates existing checkpoints and causes **one full replay** by design. The
checkpoint *format* is unchanged.

### 9.2 Binding mode

- Canonical `instrument_id` as event symbol; broker identity verified; exact broker symbol;
  `feed_id` pinned; binding time contract.
- Provenance: `source = "mt5-binding-v1:" + feed_id`;
  `identity_key = "quote:" + canonical_hash((feed_id, event_time, bid, ask, raw_event_time,
  time_offset_seconds))`; `source_event_id = identity_key + ":raw-time=…:offset=…"`.
- A new research stream by construction when `instrument_id` ≠ the legacy symbol. The feed pin
  (§6.5) guards the equal-name case.

There is no automatic migration in either direction.

## 10. D5 — Quote snapshot schema version: **A. NO VERSION BUMP REQUIRED**

Repository evidence:
- `Snapshot.schema_version: Literal[1]` (`quotes.py`) implements the ADR-003 envelope. ADR-003
  lists its fields but defines no rule that additive fields require a new version.
- ADR-013 describes `ws/quotes` as "backward-compatible quote snapshots". The `/ws/events`
  envelope is `schema_version: 2` (`main.py`). The web client dispatches on
  `schema_version === 2 && event_type` (`apps/web/app/page.tsx`) and reads only named fields
  (`apps/web/app/live-quote.ts`). Unknown fields are ignored.
- There are accepted additive precedents without version bumps: ADR-020 and ADR-021 (Experience context keys).
- No repo document defines a breaking-change policy for the quote envelope.

Decision: add one **optional** `feed` object to `Snapshot`, present in binding mode only and
absent (not serialized) in legacy mode:

```text
feed: { mode: "binding", instrument_id, broker_id, broker_symbol, feed_id,
        price_grid: {digits, point, trade_tick_size},
        time_contract: {version: "adr025-binding-v1", offset_seconds} }
```

`Quote` itself is unchanged. `broker_id` and `instrument_id` are operator labels. Terminal path,
company string and any account data are never exposed through the API. Existing field meanings
are unchanged. If Rin prefers identity on `/config` only, that is an equally additive
alternative.

## 11. Fail-closed reason codes (frozen by this ADR on acceptance)

| Code | Layer | When |
|---|---|---|
| `not_configured` | startup / feed | no symbol and no binding (§9) |
| `instrument_config_invalid` | startup | schema error; duplicate `instrument_id`/`broker_id`; duplicate `(broker_id, symbol)` or `(instrument_id, broker_id)`; dangling reference; two brokers with the same `(company, terminal_path)`; path outside the environment |
| `feed_mode_conflict` | startup / research | mixed legacy and binding settings; mode crossing on an existing stream (§6.5) |
| `time_offset_conflict` | startup | legacy offset variable set in binding mode |
| `broker_not_bound` | feed | no broker for the terminal path; no binding for `(instrument_id, broker_id)` |
| `broker_ambiguous` | feed | more than one broker/binding candidate (defensive; config validation should prevent it) |
| `broker_identity_mismatch` | feed | observed company/terminal path ≠ declared |
| `symbol_not_found` | feed | `symbol_info(exact)` is `None` |
| `symbol_not_visible` | feed | exact symbol not visible; never repaired |
| `instrument_metadata_mismatch` | feed | any semantic or price-grid mismatch, unreadable field, or `custom` symbol |
| `feed_identity_changed` | feed | identity differs from the process pin after reconnect (latched) |
| `research_instrument_mismatch` | startup / research | research `signals.symbol` ≠ selected `instrument_id` |
| `research_feed_mismatch` | research | stream feed pin ≠ current `feed_id` |

Existing codes (`terminal_not_running`, `connection_failed`, `terminal_disconnected`,
`quote_unavailable`, `adapter_not_installed`, `invalid_quote`, `invalid_time_offset`) are
unchanged.

Sanitization: API-facing values are the code only (the existing `FeedError` style). No symbol
lists, terminal paths, company strings, account data or raw exception text appear in API
responses, WebSocket envelopes, journal rows or logs. Detailed mismatch diagnosis is available
only through the local discovery tool (§12).

## 12. Discovery (operator tool; never runtime)

`scripts/mt5_discover_symbols.py`, a future read-only CLI:
- requires the terminal to be already running (the same psutil check; it never launches a
  terminal), calls `initialize(path)`, `terminal_info()` (allowlist §4.3), `symbols_get()` and
  `symbol_info(name)`, then `shutdown()`;
- prints to stdout the §3 and §6.1 fields for each symbol, plus informational text fields;
- given a declared `InstrumentDefinition`, groups symbols into "exact semantic match" and
  "other". Name similarity may only order rows within a group, labelled presentation-only;
- prints a proposed binding for the operator to copy by hand;
- never writes files or config, never calls `symbol_select`, `account_info`, `login` or any
  order/position/history API, and never chooses a runtime symbol.

It also serves as the DEV read-only probe for §4.2's unverified `company` semantics. Running
it is an operator action, outside this ADR's phase.

## 13. Configuration and isolation

- `NEXORA_INSTRUMENTS_CONFIG`: JSON file path. It is validated by the same rule `Environment.resolve`
  (`apps/api/nexora_api/environment.py`) applies to `NEXORA_RESEARCH_CONFIG`: it must resolve
  inside the owning worktree or its runtime root, else `configuration_outside_environment`.
- `NEXORA_MT5_INSTRUMENT`: the `instrument_id` this process observes.
- Content: `schema_version: 1`, `instruments[]`, `brokers[]`, `bindings[]` as in §3, §4.1 and
  §6.1, decoded strictly (the repo's typed `decode`; unknown keys rejected). No credentials,
  login, account number, server password or secret fields exist in the schema.
- DEV and PROD own separate files. They may declare identical bindings because MT5 is a shared
  read-only input (environment-isolation.md). No shared writable state is introduced.
- `config/instruments.example.json` uses placeholders only (`EXAMPLE-INSTRUMENT`,
  `example-broker`, `EXAMPLE_SYMBOL`, `Example Company`, `C:\\Path\\To\\terminal64.exe`).

## 14. Open questions

Architect (Rin):
- **A1.** Is reading `terminal_info().company` and `.path` within ADR-003's intent (§4.3), given
  ADR-003's "connected only" wording? If not, binding mode stays unavailable in V1.
- **A2.** Accept the residual risk that a same-company server/account switch in one terminal
  installation is undetectable without `account_info` (§4.2), mitigated by the rule of one
  terminal installation per `broker_id`?
- **A3.** The mechanism for a new research stream after a broker change in binding mode (§6.5).
  Any `RuntimeConfig` change touches `runtime.py`, which is shared with Startup Recovery, M30
  Phase 2B and Journal V2, so it requires their coordination. Default until decided: fail
  closed with `research_feed_mismatch`.

Quant:
- **Q-Q1.** The price-grid ↔ P&F `box_size`/`price_precision` compatibility rule (§7).

## 15. Downstream contract

| Consumer | V1 effect | Eventually required | Out of scope now |
|---|---|---|---|
| P&F / research pipeline | binding mode: event `symbol = instrument_id`; no engine change | Q-Q1 rule, if enforced | engine changes |
| Pattern Engine (Track D, ADR-024) | inherits `symbol`; `pattern_id` includes symbol, so binding streams get distinct ids | none | any Track D change |
| M30 Bias (ADR-026) | legacy: `"legacy-adr019"` + its own `feed_key`; binding: `time_contract = "adr025-binding-v1"`, `feed_key = feed_id` verbatim, from quote/event provenance | M30 Phase 2B reads `feed_id` from binding-mode provenance | any ADR-026 change |
| Experience | scope follows the research stream; binding mode = new scope | none | Experience changes |
| Risk / Paper | symbol equality on `instrument_id`; paper stays linear units, disabled for quote observation | lot/contract conversion using `trade_contract_size` (future ADR) | sizing |
| Future Trade Journal | records `instrument_id`, `feed_id`, `broker_symbol` | design at its own ADR | — |
| API | additive `Snapshot.feed` (binding only); `/config` may show `instrument_id`/`broker_id` | — | version bump |
| Web | displays broker symbol today; may show `instrument_id`/`broker_id` | display only | any interpretation |

## 16. Coordination with active tracks

- **Track D** (`claude/pnf-pattern-engine-v1`): no shared files. ADR-024 is unaffected.
- **M30** (`claude/m30-next-candle-bias-v1`): ADR-026 Q-M7 already defers to this ADR's
  binding time contract. This rev defines the string `adr025-binding-v1` and the `feed_id`
  format. ADR-026 line 52 quotes rev 1's legacy `feed_id` formula, which rev 2 removes. ADR-026's
  own legacy `feed_key` does not depend on it, so this is informational only; the M30 owner is to
  be notified. ADR-026 is not edited.
- **Journal V2** (ADR-027): the legacy promise (§9.1) keeps `observation_metadata` bytes
  unchanged. Binding mode adds `Snapshot.feed` to new streams only. The feed pin is a separate
  small stream. Both are compatible with ADR-027's authoritative-fact classification.
- **Shared files for Phase 2:** `apps/api/nexora_api/quotes.py`, `research.py`, `main.py`
  (feed-pin wiring), `environment.py`. `main.py` currently has **uncommitted changes in the main
  worktree**, so Phase 2 must rebase on whatever lands and coordinate with its owner.
  `packages/nexora/research/runtime.py` and `checkpoint_state.py` are **not touched** unless
  A3 selects a `RuntimeConfig` mechanism. That would need Startup Recovery / M30 Phase 2B /
  Journal V2 coordination first.

## 17. Implementation files (after acceptance)

- `packages/nexora/market_data/instruments.py`: contracts, strict config decode/validation,
  pure resolver, `feed_id`, comparison rules (no MT5 import).
- `apps/api/nexora_api/quotes.py`: remove `_select_symbol`; exact resolution; broker check;
  pinning; reconnect revalidation; optional `Snapshot.feed`.
- `apps/api/nexora_api/research.py`: binding-mode provenance formats; legacy formats unchanged.
- `apps/api/nexora_api/main.py`: mode selection; feed pin; `research_*` checks.
- `apps/api/nexora_api/environment.py`: path rule for `NEXORA_INSTRUMENTS_CONFIG`.
- `packages/nexora/market_data/adapters.py`: use the same resolver (P2 parity).
- `scripts/mt5_discover_symbols.py`, `config/instruments.example.json`, the `config/*.env.example`
  files, `docs/research-runtime.md`, `docs/environment-isolation.md`.
- `tests/test_instruments.py` (new), `tests/test_mt5_discovery.py` (new), and
  `tests/test_feed_time.py` (auto-selection test replaced).

## 18. Test strategy

Fake MT5 and fake psutil modules only; no terminal, broker login or PROD storage. The fake
module **raises on access** to `account_info`, `symbol_select`, `login`, `order_*`,
`positions_*`, `orders_*` and `history_*`, and on `terminal_info` fields outside the allowlist.

1. Config: strict decode; unknown keys; every `instrument_config_invalid` case; mode matrix (§9);
   `time_offset_conflict`; path outside the environment; the example config contains
   placeholders only and no credential-like keys.
2. `feed_id`: golden value; stable across runs and key order; `"0.01"` ≡ `"0.010"`; changes when
   each identity-bearing field changes; unchanged when `terminal_path`, `description` or
   volatile fields change.
3. Resolution: exact symbol; case-sensitive; no fuzzy/prefix/suffix fallback;
   `symbols_get` is never called by the runtime; no first-visible fallback (the fake exposes other
   visible symbols); `symbol_not_found`; `symbol_not_visible` with no switch; custom symbol rejected;
   `not_configured`.
4. Broker: `broker_not_bound`, `broker_ambiguous`, `broker_identity_mismatch` (company, path).
5. Metadata: one test per semantic field and per price-grid field; unreadable attribute; same
   name at two fake brokers with different `trade_contract_size` → mismatch, never equivalent.
6. Reconnect: unchanged identity resumes; changed company/symbol metadata/grid →
   `feed_identity_changed`, latched, no delivery; legacy fingerprint reconnect behaves the same.
7. Time: binding offset applied; the legacy variable in binding mode fails; the time-contract change
   changes `feed_id`; raw time retained.
8. Streams: binding mode event symbol = `instrument_id`; `research_instrument_mismatch`; feed pin
   written once, idempotent on restart; `research_feed_mismatch`; `feed_mode_conflict` in both
   directions.
9. Legacy byte compatibility: golden `NormalizedPriceEvent`, identity key, `source_event_id`,
   `Quote`/`Snapshot` dumps, `observation_metadata` and stream id versus fixtures captured at
   4f69e9c.
10. Discovery: never calls `symbol_select`/`account_info`/`login`/order APIs; writes nothing;
    ranking uses semantic fields only; no account fields in output.
11. Sanitization: every error surface contains only codes; no path/company/account strings in
    API, journal or log output.
12. Regression: the full Python suite plus the web tests per the development guide.

## 19. Consequences

- Removes an accepted-ADR violation and the silent instrument switch.
- Broker identity is verified as far as the ADR-003-safe metadata allows; the limits are explicit.
- A broker change can never silently continue a stream.
- Legacy deployments keep their journal/stream bytes; one checkpoint-invalidating full replay
  follows the code deploy (ADR-022 design).
- Out of scope: orders, `symbol_select`, automatic login, account reads, session calendars,
  lot conversion, engine refactors, ADR-003 amendment.
