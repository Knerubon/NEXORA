import profiles from "../../api/nexora_api/environments.json" with { type: "json" };
export function environmentDisplay(environment: string | undefined) {
  if (environment !== undefined && environment !== "development" && environment !== "production") throw new Error("invalid_nexora_environment");
  const production = environment === "production";
  const profile = profiles[production ? "production" : "development"];
  return { label: profile.label, api: `http://${profile.api_host}:${profile.api_port}`, ws: `ws://${profile.api_host}:${profile.api_port}`, production };
}
