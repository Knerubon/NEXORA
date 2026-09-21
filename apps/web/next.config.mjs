import { randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const web = path.dirname(fileURLToPath(import.meta.url));
const code = path.resolve(web, "../..");
const profiles = JSON.parse(fs.readFileSync(path.join(code, "apps/api/nexora_api/environments.json"), "utf8").replace(/^\uFEFF/, ""));
export default function config(phase) {
  const environment = process.env.NEXORA_ENV ?? "development";
  if (!["development", "production"].includes(environment)) throw new Error("invalid_nexora_environment");
  const expectedApi = `http://${profiles[environment].api_host}:${profiles[environment].api_port}`;
  if (process.env.NEXT_PUBLIC_API_BASE_URL && process.env.NEXT_PUBLIC_API_BASE_URL !== expectedApi) throw new Error("frontend_api_environment_mismatch");
  if (process.env.NEXT_PUBLIC_NEXORA_ENV && process.env.NEXT_PUBLIC_NEXORA_ENV !== environment) throw new Error("frontend_environment_mismatch");
  const marker = path.join(code, ".nexora-environment.json");
  if (fs.existsSync(marker) && JSON.parse(fs.readFileSync(marker, "utf8")).environment !== environment) throw new Error("worktree_environment_mismatch");
  if (environment === "production" && phase === "phase-development-server") throw new Error("production_hot_reload_forbidden");
  if (environment === "production" && !fs.existsSync(marker)) throw new Error("production_worktree_unclaimed_use_launcher");
  if (phase === "phase-production-server") {
    const built = fs.readFileSync(path.join(web, ".next", "BUILD_ID"), "utf8");
    if (!built.startsWith(`${environment}-`)) throw new Error("build_environment_mismatch");
  }
  return { turbopack: { root: code }, generateBuildId: async () => `${environment}-${randomUUID()}`, env: { NEXT_PUBLIC_NEXORA_ENV: environment, NEXT_PUBLIC_API_BASE_URL: expectedApi } };
}
