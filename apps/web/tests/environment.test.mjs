import test from "node:test";
import assert from "node:assert/strict";
import { environmentDisplay } from "../app/environment.ts";
import config from "../next.config.mjs";

test("DEV and PROD badge/endpoints are explicit and independent", () => {
  assert.deepEqual(environmentDisplay("development"), { label: "DEV", api: "http://127.0.0.1:8000", ws: "ws://127.0.0.1:8000", production: false });
  assert.deepEqual(environmentDisplay("production"), { label: "PROD", api: "http://127.0.0.1:8100", ws: "ws://127.0.0.1:8100", production: true });
  assert.throws(() => environmentDisplay("typo"));
});
test("frontend rejects accidental cross-environment API configuration", () => {
  const saved = { ...process.env };
  try {
    process.env.NEXORA_ENV = "development";
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8100";
    assert.throws(() => config("phase-development-server"), /frontend_api_environment_mismatch/);
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    delete process.env.NEXT_PUBLIC_NEXORA_ENV;
    process.env.NEXORA_ENV = "production";
    assert.throws(() => config("phase-development-server"), /production_hot_reload_forbidden|worktree_environment_mismatch/);
  } finally {
    for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key];
    Object.assign(process.env, saved);
  }
});
