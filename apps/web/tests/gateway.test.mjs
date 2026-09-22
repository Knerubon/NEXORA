import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import config from "../next.config.mjs";

const pageSource = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const configSource = readFileSync(new URL("../next.config.mjs", import.meta.url), "utf8");

test("frontend requests stay on the same origin and never hard-code a backend host/port", () => {
  assert.match(pageSource, /const api = "\/api\/nexora";/);
  assert.doesNotMatch(pageSource, /127\.0\.0\.1:8000|127\.0\.0\.1:8100|localhost:8000|localhost:8100/);
  assert.match(pageSource, /fetch\(api\+p,/);
  assert.match(pageSource, /fetch\(api\+path,/);
});

test("WebSocket URL is derived from the page's own origin: wss for https, ws otherwise", () => {
  assert.match(pageSource, /window\.location\.protocol === "https:" \? "wss" : "ws"/);
  assert.match(
    pageSource,
    /new WebSocket\(`\$\{protocol\}:\/\/\$\{window\.location\.host\}\/api\/nexora\/ws\/events`\)/,
  );
  assert.doesNotMatch(pageSource, /new WebSocket\(["']ws:\/\//);
});

test("DEV rewrite forwards /api/nexora/* to the DEV API port without exposing it in the browser bundle", async () => {
  const saved = { ...process.env };
  try {
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    delete process.env.NEXT_PUBLIC_NEXORA_ENV;
    process.env.NEXORA_ENV = "development";
    const { rewrites } = config("phase-development-server");
    const rules = await rewrites();
    assert.deepEqual(rules, [
      { source: "/api/nexora", destination: "http://127.0.0.1:8000" },
      { source: "/api/nexora/:path*", destination: "http://127.0.0.1:8000/:path*" },
    ]);
  } finally {
    for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key];
    Object.assign(process.env, saved);
  }
});

test("rewrite destination is derived from the environment profile, never a hard-coded literal port", () => {
  // Guards against re-introducing the fixed :8000 destination that would be
  // wrong for PROD (:8100); the destination must come from expectedApi.
  assert.doesNotMatch(configSource, /destination:\s*"http:\/\/127\.0\.0\.1:8000"/);
  assert.match(configSource, /destination:\s*expectedApi/);
  assert.match(configSource, /destination:\s*`\$\{expectedApi\}\/:path\*`/);
});

test("gateway config exposes no allowed-origin/host override that would widen the local-only boundary", () => {
  assert.doesNotMatch(configSource, /allowed_hosts|allowedOrigins|NEXORA_EXTERNAL_ORIGIN/);
});
