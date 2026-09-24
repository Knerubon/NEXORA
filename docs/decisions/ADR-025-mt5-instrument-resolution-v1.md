# ADR-025 — MT5 multi-broker instrument resolution V1

Status: **DRAFT — NOT ACCEPTED.** Architect (Rin) review required; Security review required
(broker identity reads); Quant review required (price-grid validation rule, stream continuity).
No implementation may start until accepted.

Base: `origin/main` 4f69e9c. Branch `claude/mt5-multi-broker-v1`.
Related: [ADR-003](ADR-003-local-price-preview.md) (read-only MT5 preview boundary),
[ADR-004](ADR-004-market-data.md) (market-data contract),
[ADR-019](ADR-019-explicit-feed-time-correction.md) (feed time offset),
[ADR-022](ADR-022-startup-recovery-checkpoint-v1.md) (checkpoint recovery),
[environment isolation](../environment-isolation.md).
ADR-023/024 are reserved by the P&F Pattern Engine track (draft, unmerged).

## 1. Context — current state (observed in code, 4f69e9c)

Two MT5 read paths exist:

| Path | Used by | Symbol handling |
|---|---|---|
| `apps/api/nexora_api/quotes.py` `Mt5Source` | live runtime (quotes, research observation) | `NEXORA_MT5_SYMBOL` optional; auto-selects a symbol when unset or when the configured one becomes invisible |
| `packages/nexora/market_data/adapters.py` `Mt5ReadOnlyMarketDataAdapter` | P2 adapter (tests; not wired to runtime) | exact configured symbol, fails closed |

Findings:

1. **Auto symbol selection contradicts accepted ADR-003.** ADR-003 says the runtime must set
   `NEXORA_MT5_PATH` and `NEXORA_MT5_SYMBOL` and never search and re-select terminal/symbol.
   `Mt5Source._select_symbol` (added in a450123) returns the **first visible symbol** from
   `symbols_get()` when no symbol is configured, and also when the configured symbol becomes
   invisible mid-session, then overwrites `self.symbol`. Pinned by
   `tests/test_feed_time.py::test_dynamic_mt5_symbol_selection_uses_visible_symbol`.
   A Market Watch change can therefore silently switch the live instrument. Research is
   partly protected (`pipeline_symbol_mismatch` → `research_processing_failed`), but the
   quote/dashboard stream is not, and with no symbol configured research follows whatever
   was picked.
2. **No broker/server identity is read or recorded.** Only `terminal_info().connected` is read.
   Provenance is `source="MT5"` / `"MT5-quote-observation:time-offset=N"`;
   `source_event_id = "mt5:{symbol}:{time_msc}"`; quote identity hash =
   `(symbol, event_time, bid, ask, raw_event_time, offset)`. Two brokers that use the same
   symbol name produce indistinguishable provenance.
3. **Symbol name is the only instrument identity.** `NormalizedPriceEvent.symbol`,
   `PnfConfig.symbol`, `SignalConfig.symbol`, matrix/structure/trendline/risk/paper all key on
   the broker's raw symbol string. The research stream id is `research:` + hash(config), so
   the broker symbol is part of stream identity.
4. **Only `digits` and `visible` are consumed from symbol metadata.** `point`,
   `trade_tick_size`, `trade_contract_size`, currencies and `trade_calc_mode` are ignored.
   P&F `box_size`/`price_precision` are absolute price units configured independently of the
   broker's price grid.
5. **Time offset is global, but feed-specific.** ADR-019's `NEXORA_MT5_TIME_OFFSET_SECONDS`
   (observed 10800) describes one broker feed. Switching broker keeps applying it silently.
6. **Terminal path**: exact `NEXORA_MT5_PATH` exe match via psutil before `initialize`; no
   cross-check with `terminal_info().path`.
7. **Sessions**: the MT5 Python API (MetaTrader5 5.0.6180, inspected statically without
   `initialize`) exposes no trading-session schedule; `SymbolInfo.session_*` fields are
   session statistics (open/close price, volume), not hours.
8. **Broker server name** is only exposed by `account_info()`, which also returns login,
   balance and personal data. ADR-003 forbids reading account details.
   `terminal_info().company` is available without account data.
9. Hard-coded names appear only in tests/fixtures (`XAUUSD`, `XAUUSD-STD`, `EURUSD`,
   risk fixtures). No production code hard-codes a symbol, server or suffix convention.
10. Web renders the raw symbol from quote/event; it computes nothing symbol-specific.

## 2. Decision (proposed)

Separate **what the instrument is** from **where its prices come from**.

```text
MT5 terminal (NEXORA_MT5_PATH)
  │  read-only: terminal_info, [account_info allowlist — §4], symbol_info, symbol_info_tick
  ▼
Broker identity  ── verified against config ── mismatch → fail closed
  ▼
Feed binding (broker + exact broker symbol + expected metadata)
  │  exact lookup only; metadata validated; fingerprint computed
  ▼
Canonical instrument (operator-declared instrument_id + semantic invariants)
  ▼
Market data pipeline → P&F / research / future M30 Bias / Trade Journal
```

### 2.1 Canonical instrument (semantic identity)

Operator-declared; NEXORA never derives it from a broker symbol name.

```text
InstrumentDefinition
  schema_version: 1
  instrument_id: str            # opaque NEXORA slug, operator-chosen, immutable once used
  semantic:                     # invariants that define "same instrument"
    currency_base: str
    currency_profit: str
    trade_calc_mode: int        # MT5 SYMBOL_CALC_MODE_* enum value
    trade_contract_size: Decimal
    chart_mode: int             # bid vs last price basis
  description: str | None       # informational only; never used for matching
```

Rule: two feeds represent the same `instrument_id` only if **every** semantic field matches
exactly. Similar names with a different contract size or calc mode are different instruments.

### 2.2 Broker identity

```text
BrokerIdentity
  broker_id: str                # operator label, used in config/provenance
  company: str                  # must equal terminal_info().company exactly
  server: str | None            # must equal account_info().server if §4 option B accepted
```

`terminal_info().path` must match `NEXORA_MT5_PATH` (normalized) in addition to the existing
psutil process check. No login, password, account number or balance is read, stored or logged.

### 2.3 Feed binding

```text
FeedBinding
  instrument_id: str
  broker_id: str
  symbol: str                   # exact broker symbol, case-sensitive, no pattern matching
  price_grid:                   # expected; must equal observed
    digits: int
    point: Decimal
    trade_tick_size: Decimal
  time_offset_seconds: int      # replaces the global ADR-019 variable in binding mode
  feed_id = hash(broker_id, company, server?, symbol, semantic, price_grid, time_offset)
```

`feed_id` is the provenance key. It is written into quote output, `source`,
`source_event_id` and quote identity so that events from different brokers can never collide.

### 2.4 Resolution algorithm (runtime, deterministic)

1. Load instrument config (§5). Validate: unique `instrument_id`; unique
   `(broker_id, symbol)`; at most one binding per `(instrument_id, broker_id)`; every binding
   references a defined instrument and broker. Any violation → startup error
   `instrument_config_invalid`.
2. Connect (existing process check + `initialize`). Read broker identity.
3. Select the binding whose `broker_id` matches observed identity. None → `broker_not_bound`;
   more than one → `broker_ambiguous`.
4. `symbol_info(binding.symbol)` — exact name only. `None` → `symbol_not_found`;
   `visible == False` → `symbol_not_visible`. **Never** call `symbol_select`
   (it changes Market Watch; ADR-003) and **never** fall back to another symbol.
5. Compare observed semantic + price-grid fields with the config. Any difference, or any
   field missing/unreadable → `instrument_metadata_mismatch` (status `error`), no quotes
   delivered, research not fed.
6. Compute `feed_id`, pin it for the process session. Only then read ticks.

No fuzzy, prefix, suffix, or similarity matching is used at runtime. Ever.

### 2.5 Symbol discovery (operator tool, offline from the runtime)

A separate read-only CLI (e.g. `scripts/mt5_discover_symbols.py`) lists
`symbols_get()` with the §2.1/§2.3 metadata fields and, given an `InstrumentDefinition`,
ranks candidates whose **semantic fields match exactly**. Name similarity may only order
equally-matching candidates in the report. The tool prints a proposed binding for the
operator to copy; it never writes config, never calls `symbol_select`, never reads
`account_info` beyond the §4 allowlist, and is not invoked by the runtime.

### 2.6 Fail-closed rules

| Condition | Result |
|---|---|
| no binding for observed broker | `broker_not_bound` |
| observed company/server differs from binding | `broker_identity_mismatch` |
| two instruments claim one broker symbol, or two bindings for one instrument+broker | `instrument_config_invalid` (startup) |
| symbol missing / not visible | `symbol_not_found` / `symbol_not_visible`; no alternative symbol |
| semantic or price-grid mismatch | `instrument_metadata_mismatch` |
| metadata or broker changes after reconnect | `feed_identity_changed`; stop delivery until operator restarts with reviewed config |
| legacy offset env var **and** binding offset both set | `time_offset_conflict` (startup) |
| research config symbol ≠ bound `instrument_id` (binding mode) | `research_instrument_mismatch` (startup) |

Errors are sanitized codes only (existing `FeedError` style); no raw terminal/account strings.

### 2.7 Reconnect and sessions

- Every (re)`initialize` re-reads broker identity and binding metadata and compares against
  the pinned `feed_id`. A terminal restart that logs into another account/server or
  another broker cannot silently continue the stream.
- Session schedules are unavailable through the Python API; V1 does not infer market hours.
  Freshness stays time-based (`stale` at >10 s), so market-closed periods remain `stale`.
- Timezone: ADR-019 offset moves into the binding because it is a property of one broker's
  feed. Offset is still explicit, never auto-detected. Bar alignment for future M30 work
  depends on broker server time; any bar-based component must use the binding's offset and
  record `feed_id`.

## 3. Stream continuity (requires Architect + Quant decision)

Changing broker changes the price series (spread, quote source, gaps) even for the same
`instrument_id`. Proposed default: **research stream identity includes `feed_id`**, so a broker
change starts a new stream; P&F/structure history is not merged across feeds. Merging feeds
into one stream would require a separate, explicit decision.

Price-grid compatibility with P&F config (e.g. whether `box_size` must be a multiple of
`trade_tick_size`, or `price_precision` must equal `digits`) is a formula decision for Quant;
V1 proposes only to **report** the relationship at startup, not to enforce or change it.

## 4. Broker identity source (Security decision)

- **Option A (recommended for V1)**: `terminal_info().company` + terminal path only. No change
  to ADR-003. Weakness: one company's demo and live servers, or two servers with different
  contract specs, are indistinguishable by company alone — mitigated because semantic and
  price-grid fields are still validated per symbol.
- **Option B**: additionally read `account_info().server` with an in-memory allowlist
  (`server` only), discarding all other fields immediately; never persisted except as part
  of `feed_id` hash and a `server` string. Requires an explicit ADR-003 amendment and
  Security review.

## 5. Configuration and DEV/PROD

- New optional `NEXORA_INSTRUMENTS_CONFIG`: JSON path, validated by the existing environment
  rule (inside the owning worktree or its runtime root). Contains instruments, brokers and
  bindings; contains **no credentials** and no account numbers.
- DEV and PROD each own their file; bindings may be identical because MT5 is a shared
  read-only input (environment-isolation.md). No shared writable state is introduced.
- Example config in `config/instruments.example.json` uses placeholder names only
  (e.g. `EXAMPLE-INSTRUMENT`), never real broker names.

## 6. Backward compatibility

| Configuration | Behavior |
|---|---|
| `NEXORA_INSTRUMENTS_CONFIG` set | binding mode (§2); `instrument_id` is the research symbol |
| unset, `NEXORA_MT5_SYMBOL` set | **legacy mode**: exact symbol, global offset, `feed_id` = `legacy:` + hash(company, symbol, digits, offset); event `symbol` unchanged so existing streams/checkpoints keep their identity; no auto-selection |
| unset, `NEXORA_MT5_SYMBOL` unset | `not_configured` — **behavior change**: today this auto-selects the first visible symbol |

Before implementation, the operator must confirm PROD/DEV `.env` files set
`NEXORA_MT5_SYMBOL` explicitly (this investigation did not read `.env` files). If PROD relies on
auto-selection, removing it is a visible runtime change that needs a release note.

Legacy-mode events keep today's `source`/`source_event_id` format so historical journals and
ADR-022 checkpoints stay valid; `feed_id` is added only to observation metadata in legacy mode.
Binding mode is a new stream by construction (config hash changes).

## 7. Downstream contract

- **P&F / research pipeline**: consumes `NormalizedPriceEvent.symbol == instrument_id` in
  binding mode; no engine change. Pattern Engine (ADR-024 draft) inherits the same symbol.
- **M30 Bias** (not in repo yet): must key on `instrument_id`, record `feed_id`, and use the
  binding offset for bar alignment.
- **Trade Journal** (not in repo yet; Experience engine is the nearest existing consumer):
  records `instrument_id`, `feed_id` and broker symbol as provenance. Contract-size/lot
  conversion is out of scope; paper remains linear units (research-runtime.md).
- **Risk / paper**: symbol equality checks keep working on `instrument_id`; no change.
- **API/Web**: quote snapshot adds `instrument_id`, `broker_id`, `feed_id` (additive; schema
  version bump decided at implementation review). UI displays but does not interpret them.

## 8. Consequences

- Removes an ADR-003 violation and the silent instrument-switch path.
- Adds one config file and one read-only discovery tool; no DB schema migration expected.
- Broker change becomes an explicit operator action that starts a new stream.
- Out of scope: order paths, `symbol_select`, automatic login, session calendars,
  contract/lot conversion, refactoring engines to a new identity type.

## 9. Expected implementation files (after acceptance)

- `packages/nexora/market_data/instruments.py` — contract dataclasses, config validation,
  pure resolver (no MT5 import; testable offline).
- `apps/api/nexora_api/quotes.py` — `Mt5Source`: remove `_select_symbol`, add identity
  verification, `feed_id` pinning, reconnect re-verification.
- `apps/api/nexora_api/research.py` — `feed_id` into provenance (binding mode only).
- `apps/api/nexora_api/environment.py` — path validation for `NEXORA_INSTRUMENTS_CONFIG`.
- `packages/nexora/market_data/adapters.py` — reuse resolver (P2 adapter parity).
- `scripts/mt5_discover_symbols.py` — operator discovery report.
- `config/instruments.example.json`, `config/*.env.example`, `docs/research-runtime.md`,
  `docs/environment-isolation.md`.
- Tests: `tests/test_instruments.py` (new), `tests/test_feed_time.py` (replace the
  auto-selection test with a fail-closed test).

## 10. Test strategy

All offline with fake MT5 modules; no terminal, no broker login, no PROD storage.

- Config validation: duplicates, dangling references, conflicting offsets.
- Resolution: exact match; missing/invisible symbol never falls back; broker unbound /
  ambiguous / mismatched.
- Metadata: each semantic and price-grid field mismatch fails; missing attribute fails.
- Same-name symbol at two brokers with different contract size → different outcome, never
  equivalent.
- Reconnect: identity or metadata change after reconnect → `feed_identity_changed`.
- Legacy mode: event identity, `source_event_id`, stream hash and ADR-022 checkpoint
  compatibility byte-for-byte unchanged versus 4f69e9c fixtures.
- Discovery tool: ranking uses semantic fields only; never calls `symbol_select`; output
  contains no account fields.
- Security: no `account_info` call in Option A; allowlist test in Option B.

## 11. Open decisions

1. Broker identity source: Option A or B (§4).
2. Stream continuity across brokers (§3).
3. Whether removing auto-selection with no symbol configured is acceptable as-is (§6).
4. Price-grid vs P&F config relationship: report only, or enforce (Quant).
5. Quote snapshot schema version bump for additive fields.
