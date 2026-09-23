# REMOTE1 — Secure Remote Access / External 5G Access V1

status: implemented; awaiting external 5G verification and tunnel provisioning
translation_needed: false
base_commit: 33fc471829905cba962aab415a63ca57e927af4d (origin/main, includes PR #23
Environment Isolation V1 and PR #24 Decision Clarity + Bias V1; tag 1.2.0)

## Assignment / context

User-authorized: make the NEXORA dashboard securely reachable from outside the home
network (4G/5G, office Wi-Fi, another network) through one stable HTTPS URL with
authentication, without exposing FastAPI, PostgreSQL, SQLite or MT5 to the public
Internet, and without breaking local access or Environment Isolation.

Sources: [requirements](../docs/requirements.md), [architecture](../docs/architecture.md),
[root workflow](../AGENTS.md), [environment isolation](../docs/environment-isolation.md).

Additional context inspected before implementation: `apps/api/nexora_api/main.py`
(security boundary), `apps/api/nexora_api/{environment,launch}.py` and
`environments.json` (DEV/PROD contract), `apps/web/app/{page.tsx,environment.ts,
next.config.mjs}`, existing web tests, and the pre-existing uncommitted Gateway work
in the protected worktree `D:\NEXORA\NEXORA`.

## Worktree

Implemented in a new isolated worktree/branch, never in the protected dirty worktree:

- Protected worktree `D:\NEXORA\NEXORA`: inspected read-only only. Never staged,
  committed, stashed, reset, or reverted. Verified unchanged (`git status`) before,
  during, and after this task.
- Implementation worktree: `D:\NEXORA\NEXORA-REMOTE`, branch `claude/remote-access-v1`,
  created from `origin/main` at `33fc471` (current, includes PR #23/#24 and tag 1.2.0).

## Phase A — Audit of the pre-existing dirty Gateway work

### What it contained

- `apps/api/nexora_api/main.py`: relaxed `_assert_local_http`/`_is_local_ws` to also
  accept the literal strings `"localhost"`, `"127.0.0.1"`, `"::1"`, `"testserver"` as
  `client_host` (in addition to the existing `LOCAL_CLIENTS` set).
- `apps/web/app/page.tsx`: `const api = "/api/nexora"` (same-origin, no more
  `NEXT_PUBLIC_API_BASE_URL`/hard-coded `127.0.0.1:8000`); REST calls via template
  literals; WebSocket via `wss`/`ws` derived from `window.location.protocol` +
  `window.location.host` + `/api/nexora/ws/events`.
- `apps/web/package.json`: `dev`/`start` scripts changed from `--hostname 127.0.0.1`
  to `--hostname 0.0.0.0`.
- `apps/web/next.config.mjs` (untracked): `rewrites()` forwarding `/api/nexora` and
  `/api/nexora/:path*` to a **hard-coded** `http://127.0.0.1:8000`.
- `apps/web/tests/gateway.test.mjs` (untracked): source-pattern assertions matching
  the above.

A verbatim patch and file copies of this original work were reviewed during the
audit but are intentionally **not** committed into the repository — they were a
local, uncommitted experiment in the protected worktree, not something this PR
should carry forward as tracked history. This section, and the rest of this
document, records what it contained, what was reused, what was rejected, and why.

### Baseline vs current main

`git status` on the protected worktree showed local `main` at `130f260`
("Merge pull request #21... Experience Engine V1"), **7 commits behind** `origin/main`
(`33fc471`). The dirty work predates:

- **PR #23 Environment Isolation V1**: `Environment.resolve()`, `environments.json`
  (DEV 8000/PROD 8100), `_local_origins()` (already dynamic, port-aware, with a
  Host-header fallback), a per-environment `apps/web/app/environment.ts` and its own
  `next.config.mjs` (identity/build guards, no rewrites). The dirty work's
  hard-coded `http://127.0.0.1:8000` rewrite destination would have been **silently
  wrong in PROD** (needs 8100), and its `next.config.mjs` would have **entirely
  discarded** PR #23's environment-identity guard function it never saw.
- **PR #24 Decision Clarity + Bias V1**: unrelated to transport; no conflict.

### Security review of the dirty approach

- `--hostname 0.0.0.0` for `next dev`/`next start`: **unjustified LAN exposure**.
  Not needed with a tunnel architecture (the tunnel makes an outbound-only
  connection to `127.0.0.1`); every currently-supported launch path
  (`apps/api/nexora_api/launch.py`) already hard-codes `--hostname 127.0.0.1`
  regardless of `package.json`, so this change had no effect through the supported
  launcher and only weakened the raw `npm run dev`/`npm start` scripts. **Rejected.**
- `_assert_local_http`/`_is_local_ws` client-host relaxation: `request.client.host`
  is a TCP peer address (e.g. `127.0.0.1`), never the literal string `"localhost"`
  or `"testserver"` — most of the added strings could never match a real peer
  address, so this patch was largely a no-op for its stated purpose. It also never
  touched `_is_local_origin`, which is the check that **actually** rejects a
  gateway-proxied remote request (see below) — so even fully applied, it would not
  have made remote access work. **Rejected**; the real fix is in `_local_origins()`
  (see "REST gateway").
- Hard-coded rewrite destination `http://127.0.0.1:8000`: wrong for PROD. **Rejected**
  in favor of `Environment.resolve()`-derived `expectedApi`, which
  `next.config.mjs` already computes.

### What was reused

- The same-origin relative API base (`/api/nexora`) in `page.tsx` — kept, re-applied
  onto the current file (which now resolves `api` via `environment.ts` instead of a
  raw `127.0.0.1:8000` fallback; that hard-coded-loopback pattern is exactly what
  this task needs to remove).
- The `wss`/`ws`-from-`window.location` WebSocket construction — kept verbatim; it
  is correct and directly satisfies "no `ws://` from an `https://` page."
  The dirty version already pointed the socket at the gateway path; only the file it
  was based on had moved on.
- The `/api/nexora` + `/api/nexora/:path*` Next.js rewrite shape — kept, but the
  destination is now `expectedApi` (environment-derived), and it is **merged into**
  PR #23's existing `next.config.mjs` function rather than replacing it, so the
  identity/build guards are preserved.
- The general gateway-test intent (source-pattern assertions) — kept, rewritten
  against the current file contents and extended.

### Runtime/debug files noted in the audit

`tmp_signal_debug.py` is tracked (pre-existing, unrelated, unmodified — not part of
this diff). `logs/` and `data/research.sqlite*` are already `.gitignore`d. None of
these were touched, copied, or included in this branch.

## Phase A — the critical open question: does Next.js's rewrite proxy WebSocket?

This was resolved **empirically**, not by assumption, since it is the single most
important technical fact this design depends on. A minimal isolated reproduction
(separate from NEXORA, in a scratch worktree, cleaned up afterward — never in the
protected worktree) was built: a Python `websockets` echo server behind a Next.js
`rewrites()` rule, tested with a real WebSocket client (Node's built-in `WebSocket`).

**Result: Next.js 16.3.5's `rewrites()` correctly proxies a full WebSocket upgrade
handshake and bidirectional messages, in both `next dev` and `next start`
(production) mode.** A second reproduction confirmed the exact headers the backend
receives through the rewrite: **`Host` is rewritten to the destination**
(`127.0.0.1:<port>`, so `TrustedHostMiddleware` already passes unmodified), while
**`Origin` is forwarded unchanged** from the original browser request (so a remote
browser's `https://nexora.example.com` Origin reaches FastAPI verbatim, and
`_is_local_origin()`'s existing origin-set check is the only place that needed a
change).

This is why the actual code change is small: no custom Node/Express WS-proxy server,
no tunnel-level path splitting between web and API, and no change to
`_assert_local_http`/`_is_local_ws`/`LOCAL_CLIENTS`/`TrustedHostMiddleware` was
necessary — only `_local_origins()` needed an opt-in addition.

## Scope / boundaries

Only secure remote access. Did **not** touch P&F, Adaptive Box, Structure, Matrix,
Market Regime, Signal engine/scoring, Risk, Paper, Backtest, Experience, MT5 adapter,
`apps/api/nexora_api/quotes.py`, `apps/api/nexora_api/environment.py`,
`apps/api/nexora_api/launch.py`, `environments.json`, or `apps/web/app/environment.ts`.
Confirmed by diff: only `apps/api/nexora_api/main.py`, `apps/web/app/page.tsx`,
`apps/web/next.config.mjs`, `tests/test_environment.py`, and new
`apps/web/tests/gateway.test.mjs` changed (4 files modified, 1 new test file, plus
this doc and the preserved evidence patch).

## Target architecture

```text
Phone/laptop (4G/5G, office Wi-Fi, any network)
        │
        ▼
  https://<external-hostname>            (Cloudflare Access: authentication gate)
        │
        ▼
  Cloudflare Tunnel (cloudflared, outbound-only from the home PC — no inbound
  router port forwarding, no public IP exposure)
        │
        ▼
  Home Windows PC
        │
        ▼
  NEXORA Web (Next.js, 127.0.0.1:3100 PROD / :3000 DEV)
        │
   same-origin rewrite: /api/nexora/* and /api/nexora/ws/events
        │
        ▼
  NEXORA API (FastAPI, 127.0.0.1:8100 PROD / :8000 DEV — never bound to 0.0.0.0,
  never reachable except via the loopback rewrite above)
        │
   ┌────┴────┐
   ▼         ▼
WebSocket   REST
   │
   ▼
Research Runtime → MT5 (read-only quote source, unchanged)
```

Both the browser and the tunnel only ever see **one origin and one port** (the web
app's). The backend's own host/port is never present in the browser bundle, in a
URL, or in any script the remote user runs.

## Chosen secure tunnel: Cloudflare Tunnel + Cloudflare Access

**Not installed or provisioned by this task** — it requires the user's own
Cloudflare account and a domain, and per the assignment credentials/accounts must
never be invented or committed. This section documents the choice and the exact
steps for the user (or Rin) to perform.

| Requirement | Cloudflare Tunnel + Access | Tailscale (alternative considered) |
|---|---|---|
| One stable HTTPS URL for phone/office/laptop | Yes — a real public hostname on the user's domain | Needs the Tailscale client installed on every remote device (harder for "any office PC/phone") |
| No inbound router port forwarding | Yes — `cloudflared` makes an outbound-only connection | Yes — also outbound-only (WireGuard) |
| Built-in authentication layer | Yes — Cloudflare Access (email OTP / SSO), no code in NEXORA | No built-in web auth gate; would need a custom check |
| WebSocket support | Yes, natively | Yes (it's just IP-level) |
| Cost | Free tier covers Cloudflare Tunnel + Access for a handful of users | Free tier available too |
| Works from an unmanaged office PC without installing anything | Yes — just a browser | No — requires installing the Tailscale client, often blocked by corporate IT |

**Cloudflare Tunnel + Access is selected** because the target UX explicitly includes
"office computer" and "phone over 5G" as ordinary browsers with no software
installed — Tailscale would require installing its client on every such device,
which is not guaranteed to be possible on a managed office machine. Tailscale
remains a reasonable fallback if the user already has it deployed; the code changes
in this PR do not depend on which tunnel is used, since both terminate as an
ordinary HTTPS/WSS reverse proxy into `127.0.0.1:3100`.

### Operator setup (to be performed by the user/Rin, not by this task)

1. Cloudflare account + a domain added to Cloudflare (can be a subdomain of an
   existing domain).
2. `Zero Trust` dashboard → `Networks` → `Tunnels` → create a tunnel, name it (e.g.
   `nexora-home`). Install `cloudflared` on the home Windows PC.
3. `cloudflared service install <token>` — installs `cloudflared` as a **Windows
   Service** (survives reboot, starts automatically; this is the built-in mechanism,
   no custom PowerShell loop needed).
4. Public hostname: map `nexora.<yourdomain>` → `http://127.0.0.1:3100` (the PROD
   web port). One rule; the Next.js rewrite handles `/api/nexora/*` and
   `/api/nexora/ws/events` internally, so the tunnel itself needs no path-based
   routing.
5. `Zero Trust` → `Access` → `Applications` → add an application for
   `nexora.<yourdomain>`, choose an identity provider (email OTP is the simplest —
   no separate account needed, Cloudflare emails a one-time code) and an access
   policy (e.g. "allow this exact email address").
6. Set `NEXORA_EXTERNAL_ORIGIN=https://nexora.<yourdomain>` in `.env.production`
   (never committed — see `config/production.env.example`).
7. Restart the PROD NEXORA API (`scripts/nexora.ps1 -Environment production
   -Action start`, or `stop` then `start`) so it picks up the new environment
   variable — see "Rollout" below for how to do this without disturbing the
   currently-running stable screen.

No credentials, tokens, or certificates are created, invented, or committed by this
task.

## Authentication model

Handled entirely by **Cloudflare Access**, in front of the tunnel — the assignment's
explicit preference over a home-grown password system. Nothing in NEXORA itself
implements login, sessions, or password storage. A request never reaches
`cloudflared`, let alone Next.js or FastAPI, until Cloudflare Access has
authenticated the user. This also means NEXORA's application logs never see
authentication secrets — there simply aren't any at that layer.

`_local_origins()`'s `NEXORA_EXTERNAL_ORIGIN` check (below) is **defense in depth**,
not the primary authentication boundary: it only prevents an arbitrary Origin
header from being accepted even if it somehow reached FastAPI directly (e.g. a
misconfiguration that exposed the loopback port, or another local process). Two
independent layers, not a substitute for each other.

## REST gateway

`apps/web/app/next.config.mjs`'s existing `config(phase)` function (owned by
Environment Isolation) gained a `rewrites()` entry, added alongside its existing
identity/build guards rather than replacing them:

```js
async rewrites() {
  return [
    { source: "/api/nexora", destination: expectedApi },
    { source: "/api/nexora/:path*", destination: `${expectedApi}/:path*` },
  ];
},
```

`expectedApi` was already computed by this function from `environments.json`
(`http://127.0.0.1:8000` DEV, `http://127.0.0.1:8100` PROD) — so the destination is
correct in both environments without any new logic, and the dirty work's
hard-coded-8000 bug does not exist here.

`apps/web/app/page.tsx`'s `const api = "/api/nexora"` (previously
`process.env.NEXT_PUBLIC_API_BASE_URL ?? environment.api`, where `environment.api`
was `http://127.0.0.1:<port>` — a browser-facing loopback URL, exactly the pattern
this task must remove). All REST call sites (`/state`, `/backtest/runs`,
`/paper/replay`, `/operations/readiness`, `/config`, `/paper/control` POST) are
built from this one `api` constant, so no other file needed a route-by-route change.
Full endpoint inventory checked against `apps/api/nexora_api/main.py`: `/config`,
`/state`, `/quotes`, `/quality`, `/history`, `/backtest/runs` (GET+POST),
`/experiences*`, `/backtest/compare`, `/risk/replay`, `/paper/replay`,
`/paper/control` (POST), `/operations/readiness`, `/operations/alerts` — all behind
`_assert_local_http`, all reachable through the same wildcard rewrite. `/health` is
the one route with no `_assert_local_http` call (pre-existing, unchanged); it stays
unreachable publicly anyway because FastAPI is never bound outside loopback.

## WebSocket / WSS gateway

`page.tsx`:

```js
const protocol = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss" : "ws";
const socket = new WebSocket(`${protocol}://${window.location.host}/api/nexora/ws/events`);
```

Same-origin, protocol-matched to the page itself — `wss://nexora.<domain>/...` when
opened through the tunnel (`https://`), `ws://127.0.0.1:3100/...` when opened at
home (`http://`). No mixed-content error is possible: the socket protocol always
matches the page protocol. Proxied by the **same** `next.config.mjs` rewrite as REST
(empirically verified above to support the WS upgrade in both dev and prod Next.js
modes) — no separate WS-specific infrastructure needed.

Existing PR #22 realtime semantics (reconnect with capped exponential backoff,
single logical socket via `socketRef`/`socketGenerationRef`, authoritative REST
resync on reconnect) are in `page.tsx`'s `connect()`/`useEffect` and were **not**
touched by this change — only the URL construction changed, not the connection
lifecycle logic.

## Local vs remote URL behavior

| | Local (home) | Remote (tunnel) |
|---|---|---|
| Page origin | `http://127.0.0.1:3100` | `https://nexora.<domain>` |
| REST | `http://127.0.0.1:3100/api/nexora/state` → rewritten to `http://127.0.0.1:8100/state` | `https://nexora.<domain>/api/nexora/state` → tunnel → `http://127.0.0.1:3100/api/nexora/state` → rewritten to `http://127.0.0.1:8100/state` |
| WebSocket | `ws://127.0.0.1:3100/api/nexora/ws/events` | `wss://nexora.<domain>/api/nexora/ws/events` |
| Auth | none (local-only guard) | Cloudflare Access, then the same local-only guard (now allow-listing the configured external Origin) |

Same frontend code path both ways — no "am I remote" branch in the browser.

## Environment behavior (DEV vs PROD)

Unchanged DEV/PROD port contract (`.runtime/development` 3000/8000,
`.runtime/production` 3100/8100) — `environments.json`, `Environment.resolve()`,
`apps/api/nexora_api/launch.py`'s ownership/PID guards are untouched. Per the
assignment's explicit preference, **remote access targets PROD** — the tunnel's
public hostname points at `127.0.0.1:3100`, and `NEXORA_EXTERNAL_ORIGIN` is
documented only in `config/production.env.example`, not the DEV example. DEV
remains local-only and unaffected; no migration of the currently-running
stable/legacy screen was performed or required by this task (see "Rollout").

## Startup / resilience

- NEXORA PROD: existing supported launcher (`scripts/nexora.ps1 -Environment
  production -Action start`), unchanged by this task.
- Tunnel: `cloudflared service install` registers a genuine Windows Service —
  starts on boot, restarts automatically, no fragile PowerShell polling loop.
- Order after a reboot: Windows → (existing) NEXORA PROD services start → tunnel
  service starts and reconnects outbound to Cloudflare → `/health` reachable
  through the tunnel once both are up. No new orchestration code was added; this is
  the natural effect of two independent Windows services.
- Temporary Internet outage: `cloudflared` reconnects automatically when
  connectivity returns (its own built-in retry/backoff); NEXORA itself keeps
  running locally and is unaffected — local access never depended on the tunnel.

## Recovery / failure-state behavior

The frontend already distinguishes these states from existing PR #22/Environment
Isolation work, unchanged by this task:
`connectionStatus` (`live`/`reconnecting`/`offline`) for the WebSocket,
`research_mode` (`live_observation` vs `recorded_or_unavailable`) plus
`quote.status` for feed/research freshness, and `error` for a surfaced
connection-interrupted message. A tunnel outage shows up to the frontend exactly
like any other network interruption (the browser's `fetch`/`WebSocket` simply fail),
so the **existing** reconnect/backoff and "Recorded / last received calculation"
messaging already covers it — no new frontend failure-state code was needed. Stale
data is never presented as newly live: the existing `fresh`/`research_mode` logic in
`apps/api/nexora_api/main.py`'s `state_payload()` is unchanged.

## Health check

`/health` (unauthenticated by design, pre-existing) reports `status`, `mode`,
`readiness` pointer, and `environment` — reachable through the tunnel exactly like
any other route (`https://nexora.<domain>/api/nexora/health`), for use as the
tunnel's own health probe if desired. It exposes no secrets and no research state.
`/operations/readiness` and `/operations/alerts` (behind the local-only guard, so
behind Cloudflare Access remotely) give the fuller research/feed/recovery picture.

## Security checklist

- [x] HTTPS externally (Cloudflare Tunnel terminates TLS; `cloudflared` origin
      connection to `127.0.0.1` is a private outbound tunnel, not a public HTTP
      listener).
- [x] WSS externally (same tunnel, verified to proxy the WS upgrade).
- [x] Authentication required (Cloudflare Access in front of the tunnel).
- [x] No anonymous public dashboard (Access gate; NEXORA itself still enforces its
      own local-only/allow-listed-origin check as a second layer).
- [x] No public FastAPI port (bound to `127.0.0.1` by the existing launcher;
      never changed to `0.0.0.0`; never targeted by the tunnel's public hostname).
- [x] No public PostgreSQL/SQLite/MT5 (untouched; not exposed by this task).
- [x] No committed credentials (none created; tunnel token and Access
      configuration live in the user's Cloudflare account, not in this repo).
- [x] No secrets in the browser bundle (`NEXORA_EXTERNAL_ORIGIN` is a public
      hostname, not a secret, and is only used server-side in `main.py`; the
      existing `NEXT_PUBLIC_API_BASE_URL` injection in `next.config.mjs` is
      unchanged and unrelated).
- [x] No secrets in `git diff` — confirmed by review of this branch's diff.
- [x] No router port forwarding (tunnel is outbound-only).
- [x] Origin handling reviewed (empirically: `Origin` forwarded unchanged by the
      Next.js rewrite; `_local_origins()` now allow-lists it only when configured).
- [x] Host handling reviewed (empirically: rewritten to the loopback destination
      by Next.js; `TrustedHostMiddleware` unaffected).
- [x] Proxy headers reviewed (`X-Forwarded-Host` carries the original host but is
      not currently consumed by the backend; nothing security-relevant depends on
      it).
- [x] Direct API bypass reviewed: FastAPI is never bound to a publicly-reachable
      address, so there is no path that bypasses the Next.js gateway from outside
      the home PC.
- [x] WebSocket authentication reviewed: `_is_local_ws` calls the same
      `_is_local_origin` check as HTTP, now covering the configured external
      origin; unauthenticated WS connections are rejected exactly like HTTP ones.
- [x] External URL contains no secret token (`https://nexora.<domain>`, a plain
      hostname; auth is a Cloudflare Access session, not a URL parameter).
- [x] Logs do not expose authentication secrets (NEXORA has no authentication
      secrets of its own; Cloudflare Access logs live in Cloudflare's dashboard).

## Manual external 5G acceptance test (must be performed by the user)

Automated CI cannot prove the home PC is reachable from a real external network.
Perform this **after** completing the operator setup above:

1. Confirm the home PROD NEXORA server is running and reachable at
   `http://127.0.0.1:3100` on the home PC itself.
2. On a phone: turn Wi-Fi **off**.
3. Connect the phone to 4G/5G (confirm no Wi-Fi icon, confirm cellular data icon).
4. Open `https://nexora.<domain>` in the phone's browser.
5. Confirm a Cloudflare Access authentication page appears (email OTP or configured
   identity provider) — **not** the NEXORA dashboard directly.
6. Authenticate.
7. Confirm the NEXORA dashboard loads (DEV/PROD badge should read PROD).
8. Confirm a live MT5 quote appears and is not `"No live quote available"`.
9. Confirm the P&F chart renders confirmed boxes (not empty/placeholder).
10. Confirm the Matrix panel shows FAST/MEDIUM/SLOW resolution state.
11. Confirm the Decision Context panel (Bias/State/Alignment/Why-WAIT) renders.
12. Confirm the WebSocket is connected (`Feed: LIVE` in the header, not
    `RECONNECTING`/`OFFLINE`).
13. Leave the page open for several minutes; confirm the quote/timestamp advances
    with real data and the connection does not repeatedly drop/reconnect
    (no reconnect storm).
14. On the phone, switch from 4G/5G to a different Wi-Fi network; confirm the page
    detects the interruption (`RECONNECTING`), then recovers (`LIVE`) once the new
    network is up, without a page reload.

Only after a user performs this and confirms every step should this task be called
**"EXTERNAL 5G VERIFIED."** Until then, the correct status is:

**"Remote access implementation complete — awaiting external 5G verification and
tunnel provisioning."**

Provisioning (Cloudflare account/tunnel/Access setup) has **not** been performed by
this task — see "Operator setup" above.

## Rollback procedure

Remote-access failure must never make local NEXORA unusable; it does not depend on
the tunnel at all.

- **Disable remote access only**: unset `NEXORA_EXTERNAL_ORIGIN` (or remove it from
  `.env.production`) and restart PROD. `_local_origins()` reverts to loopback-only;
  any request bearing the external Origin is rejected again, exactly like before
  this task. Local access (`http://127.0.0.1:3100`) is unaffected throughout.
- **Disable the tunnel only**: stop the `cloudflared` Windows service
  (`Stop-Service cloudflared` or via `services.msc`), or delete the public hostname
  route in the Cloudflare dashboard. NEXORA itself keeps running; only the external
  path goes away.
- **Full revert of this code**: `git revert` this branch's merge commit (or simply
  don't merge it) — `next.config.mjs` loses its `rewrites()` entry,
  `_local_origins()` loses the `NEXORA_EXTERNAL_ORIGIN` branch, `page.tsx` reverts
  to `environment.api`-based URLs. None of this touches persisted data, journal
  schema, or any other subsystem.
- At no point does any rollback step require touching PostgreSQL, the SQLite
  journal, MT5 configuration, or Environment Isolation's own files.

## Tests

- `tests/test_environment.py`: three new tests — `NEXORA_EXTERNAL_ORIGIN` unset ⇒
  an external Origin is still rejected (regression guard against accidentally
  becoming permissive); set ⇒ that exact origin is accepted, an arbitrary other
  origin is still rejected, and local access still works; the same behavior over a
  real `TestClient` WebSocket connection (`/ws/events` accepted with the configured
  origin, rejected — `WebSocketDisconnect` — with an arbitrary one).
- `apps/web/tests/gateway.test.mjs` (new): same-origin API base with no hard-coded
  backend host/port in `page.tsx`; `wss`/`ws` derived from `window.location`, never
  a literal `ws://`; the DEV rewrite resolves to the exact DEV API port by directly
  invoking `next.config.mjs`'s exported `config()` function (matching the existing
  `environment.test.mjs` convention — no live server needed for this deterministic
  part); a regression guard that the rewrite destination is never a hard-coded
  literal port (guards against reintroducing the dirty work's PROD-breaking bug);
  a regression guard that the gateway config doesn't loosen `allowed_hosts` or
  introduce a new origin override inside the Next.js layer.
- The WebSocket-through-Next.js-rewrite behavior itself (the empirical question this
  whole design depends on) was verified with a live, isolated, non-NEXORA
  reproduction in both `next dev` and `next start` modes — documented above under
  "the critical open question" rather than checked into the automated suite, since
  spinning up live Next servers on ephemeral ports is not how any existing test in
  this repo verifies Next.js behavior (`environment.test.mjs` calls `config()`
  directly instead), and would add CI flakiness for a fact that does not change
  between runs.

## Full regression / validation

Run in `D:\NEXORA\NEXORA-REMOTE`:

- `.venv\Scripts\python -m pytest -q` (targeted `-k external_origin` first, then
  full suite): **217 passed, 3 skipped** (214 prior baseline + 3 new; same
  pre-existing skips — no local PostgreSQL DSN, Windows symlink privilege).
- `.venv\Scripts\ruff check .`: pass. `.venv\Scripts\mypy`: pass, 102 source files.
- `.venv\Scripts\python scripts/recovery_drill.py`: `PASS: SQLite backup and
  fresh-process paper/risk recovery ...` (PostgreSQL/host-crash/RPO-RTO/remote-access
  remain NOT VERIFIED, as before — unrelated to this task's transport change).
- `npm --prefix apps/web test`: **34 passed** (29 prior baseline + 5 new gateway
  tests, including the existing realtime `live-quote.test.mjs` reconnect tests,
  unchanged and still passing).
- `npm run lint`: pass. `npm run typecheck`: pass. `npm run build`: pass.
- `git diff --check`: pass.

## Known limitations

- Cloudflare account, domain, tunnel, and Access application are **not
  provisioned** by this task — see "Operator setup." This PR is code-only.
- `NEXORA_EXTERNAL_ORIGIN` supports exactly one origin (matches the "ONE stable
  HTTPS URL" requirement); a future need for multiple external hostnames would
  require extending it to a comma-separated list.
- `X-Forwarded-Host`/`X-Forwarded-For` are not currently consumed by
  `apps/api/nexora_api/main.py` for logging or rate-limiting — out of scope here,
  since nothing in the current security boundary depends on them.
- No rate-limiting or brute-force protection is added at the FastAPI layer; this is
  intentionally left to Cloudflare Access, which already gates all traffic before
  it reaches the tunnel.
- The manual 5G acceptance test (above) has not yet been performed by the user as
  of this PR; status is "implemented, awaiting external verification," not
  "5G access verified."

## Execution record

- `git fetch origin`; confirmed `origin/main` at `33fc471` includes PR #23/#24.
- Protected worktree audited read-only (its diff and untracked files reviewed
  in place, not copied into the repository); confirmed unchanged via `git status`
  throughout and at the end of this task.
- New worktree `D:\NEXORA\NEXORA-REMOTE` created from `origin/main`.
- Empirical WebSocket-through-Next.js-rewrite and Host/Origin-header reproductions
  run in a separate scratch location, cleaned up completely (processes stopped,
  temporary config reverted with `git checkout`) before any real implementation
  began.
- Implementation: `apps/api/nexora_api/main.py` (`_local_origins()` +
  `NEXORA_EXTERNAL_ORIGIN`), `apps/web/next.config.mjs` (rewrites, merged with the
  existing environment guard), `apps/web/app/page.tsx` (same-origin `api`, WSS/WS
  construction), `config/production.env.example` (documented opt-in variable, no
  value), tests, this document.
- All commands above run and their exact output recorded.

Self-review; independent Rin review pending. No merge, deploy, tunnel provisioning,
or PROD data migration performed. No currently-running NEXORA screen/API was
restarted or otherwise disturbed by this task.

## Addendum — PR #26 (peer-IP trust) disproved; replaced with identity-header trust

After this PR, live production testing with Tailscale Serve (`fon.tail39afa4.ts.net`
→ `127.0.0.1:3100`) surfaced a real gap: guarded WebSocket connections proxied
through Tailscale Serve were rejected. **PR #26** attempted a fix: trust exactly
one configured local peer address, `NEXORA_LOCAL_TAILSCALE_IP`, added only to the
WebSocket check. It validated successfully from the home PC itself.

**It was wrong.** Real-device testing (an actual iPhone, Wi-Fi off, over 5G, through
the real `fon.tail39afa4.ts.net` → Tailscale Serve → Next.js → FastAPI path)
disproved its core assumption: Tailscale Serve does not source proxied connections
from the home machine's own tailnet address. It preserves **the connecting
device's own** tailnet identity end-to-end. The iPhone presented as its own address
(`100.79.209.92`), not the home machine's (`100.94.248.31`); a second device (an
iPad) presented as a third, different address. A single hardcoded peer can never
generalize to this — and the same real-device test showed **guarded REST endpoints
were equally broken** through the external path, something PR #26 never touched or
tested (its own validation only ever exercised the unguarded `/health` route and
loopback-simulated REST calls, never a guarded route through the real external URL).

### Root cause, confirmed against Tailscale's own source

Fetched `ipn/ipnlocal/serve.go` directly from `github.com/tailscale/tailscale`.
Tailscale Serve is a standard Go `httputil.ReverseProxy`. Its `Rewrite` hook runs
two functions, uniformly for HTTP and WebSocket upgrades alike (no special-casing
exists for WS in this code path):

```go
func addProxyForwardedHeaders(r *httputil.ProxyRequest) {
	r.Out.Header.Set("X-Forwarded-Host", r.In.Host)
	if r.In.TLS != nil { r.Out.Header.Set("X-Forwarded-Proto", "https") }
	if c, ok := serveHTTPContextKey.ValueOk(r.Out.Context()); ok {
		r.Out.Header.Set("X-Forwarded-For", c.SrcAddr.Addr().String())
	}
}

func (b *LocalBackend) addTailscaleIdentityHeaders(r *httputil.ProxyRequest) {
	r.Out.Header.Del("Tailscale-User-Login") // clear any client-supplied value first
	r.Out.Header.Del("Tailscale-User-Name")
	// ...
	node, user, ok := b.WhoIs("tcp", c.SrcAddr)
	if !ok { return }
	if node.IsTagged() { return }
	r.Out.Header.Set("Tailscale-User-Login", encTailscaleHeaderValue(user.LoginName))
	// ...
}
```

`X-Forwarded-For` explains the observed peer-address behavior (this app's
`ws.client.host`/`request.client.host` is, and always was, `X-Forwarded-For` — not
raw socket info — because uvicorn's `ProxyHeadersMiddleware` defaults to
`proxy_headers=True`, `forwarded_allow_ips="127.0.0.1,::1"`, and the immediate hop
from Next.js's rewrite to FastAPI is always loopback). `Tailscale-User-Login` is
**deleted from any client-supplied value, then set from Tailscale's own
cryptographic `WhoIs` resolution** — not spoofable by the connecting client.

### Empirical verification (real iPhone, real spoof test)

A standalone, throwaway diagnostic server (outside any NEXORA worktree, never
committed) was mounted at a temporary, additional Tailscale Serve path (`/diag`,
alongside — never replacing — the production `/` → `:3100` mapping) to capture
exactly the headers needed, then torn down completely afterward.

- **Spoof test**: `curl -H "Tailscale-User-Login: attacker@example.invalid"`
  through the real external URL. The diagnostic server received the *real*
  authenticated login instead — the forged value never survived.
- **Real iPhone, HTTP** (Wi-Fi off, 5G, Tailscale connected): `Tailscale-User-Login`
  present, `Tailscale-Headers-Info` present, `X-Forwarded-For` matched the phone's
  own tailnet address exactly, `X-Forwarded-Proto: https`. (`Origin` absent — this
  was a plain Safari navigation, not a `fetch()` call; NEXORA's real REST calls in
  `page.tsx` are all `fetch()`, which does send `Origin`.)
- **Real iPhone, WebSocket**: identical identity headers present, plus `Origin`
  present (the browser `WebSocket` API always sends it) — upgrade succeeded,
  closed normally (code 1000) after the diagnostic reply.

This diagnostic intentionally bypassed Next.js (it was a separate, parallel Serve
path straight to the throwaway server) to isolate Tailscale Serve's own behavior
from Next.js's. Header survival through the *additional* Next.js rewrite hop that
the real `/api/nexora/*` path uses was therefore a mandatory **live pre-merge
validation gate** for the replacement PR, not something inferred from this
diagnostic alone — see that PR's own validation record.

### Replacement design (implemented in the PR that supersedes #26)

- `NEXORA_LOCAL_TAILSCALE_IP` removed entirely — no per-device IP is configured or
  enumerated anywhere.
- A new shared helper, `_is_trusted_transport(peer, headers)`, used by **both**
  `_assert_local_http` and `_is_local_ws` (fixing REST, which PR #26 never did):
  trusted if the peer is in the existing `LOCAL_CLIENTS` (unchanged local
  behavior), **or** if `NEXORA_EXTERNAL_ORIGIN` is configured and a non-empty
  `Tailscale-User-Login` header is present. Whitespace-only or absent is treated
  as no identity — fails closed. No CIDR/range trust of any kind.
  `NEXORA_EXTERNAL_ORIGIN` is still independently required for Origin, exactly as
  before — transport trust and Origin validation remain two separate, both-required
  checks, for REST and WebSocket alike.
- `NEXORA_EXTERNAL_ORIGIN` is the only configuration remote access needs — no new
  variable was introduced, and none is required per-device.

PR #26 was closed without merging (branch preserved as audit history, not
deleted) once this diagnosis was confirmed.
