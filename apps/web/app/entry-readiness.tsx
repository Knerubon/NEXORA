"use client";

import { useState } from "react";

// UI-READINESS-1: presentation of the backend EntryReadinessSnapshot (ADR-021) carried in
// research.output.entry_readiness. The browser never calculates, derives, upgrades or
// repairs readiness; any payload outside the frozen schema renders Unavailable.

export const ENTRY_READINESS_STATES = ["READY", "DEVELOPING", "NOT_READY", "BLOCKED"] as const;
const SIGNAL_ACTIONS = ["BUY", "SELL", "WAIT"] as const;

export type EntryReadinessItem = { code: string; side: string; trendline_kind: string; line_id: string; reason: string };
export type EntryReadinessSnapshot = {
  schema_version: 1;
  symbol: string;
  state: (typeof ENTRY_READINESS_STATES)[number];
  signal_action: (typeof SIGNAL_ACTIONS)[number];
  blockers: EntryReadinessItem[];
  pending_confirmations: EntryReadinessItem[];
  config_version: string;
};
export type ParsedEntryReadiness =
  | { status: "valid"; snapshot: EntryReadinessSnapshot }
  | { status: "absent" }
  | { status: "unsupported" };

const isText = (value: unknown): value is string => typeof value === "string";
const isItem = (value: unknown): value is EntryReadinessItem => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return ["code", "side", "trendline_kind", "line_id", "reason"].every((key) => isText(item[key]));
};
const isItems = (value: unknown): value is EntryReadinessItem[] => Array.isArray(value) && value.every(isItem);

// Shape check only. Accepts the schema_version 1 contract as-is or nothing at all.
export function parseEntryReadiness(value: unknown): ParsedEntryReadiness {
  if (value === undefined || value === null) return { status: "absent" };
  if (typeof value !== "object" || Array.isArray(value)) return { status: "unsupported" };
  const r = value as Record<string, unknown>;
  const valid = r.schema_version === 1
    && (ENTRY_READINESS_STATES as readonly unknown[]).includes(r.state)
    && (SIGNAL_ACTIONS as readonly unknown[]).includes(r.signal_action)
    && isText(r.symbol) && isText(r.config_version)
    && isItems(r.blockers) && isItems(r.pending_confirmations);
  return valid ? { status: "valid", snapshot: value as EntryReadinessSnapshot } : { status: "unsupported" };
}

function Items({ label, items }: { label: string; items: EntryReadinessItem[] }) {
  if (!items.length) return null;
  return <ul className="entry-readiness-items">{items.map((item, i) => <li key={i}>
    <strong>{label}</strong> {item.reason} <small title={`${item.trendline_kind} · ${item.line_id}`}>{item.code}</small>
  </li>)}</ul>;
}

function EntryReadinessBody({ readiness, stale }: { readiness: unknown; stale?: boolean }) {
  const parsed = parseEntryReadiness(readiness);
  if (parsed.status !== "valid") {
    return <p className="entry-readiness-state" data-entry-readiness-state="UNAVAILABLE">
      State <strong>Unavailable</strong>
      <small>{parsed.status === "absent" ? "Not reported by backend" : "Unsupported backend payload — not interpreted"}</small>
    </p>;
  }
  const r = parsed.snapshot;
  return <>
    <p className="entry-readiness-state" data-entry-readiness-state={r.state}>
      State <strong className={`entry-state-${r.state.toLowerCase()}`}>{r.state}</strong> · Signal <strong>{r.signal_action}</strong>
      {stale && <span className="entry-readiness-stale">Last received</span>}
    </p>
    <Items label="Blocker" items={r.blockers} />
    <Items label="Pending" items={r.pending_confirmations} />
    <small>Backend filter on the Signal · not an order · config {r.config_version}</small>
  </>;
}

export function EntryReadinessView({ readiness, stale, enabled, onToggle }: {
  readiness: unknown; stale?: boolean; enabled: boolean; onToggle?: () => void;
}) {
  return <div className="entry-readiness" data-entry-readiness={enabled ? "on" : "off"}>
    <div className="entry-readiness-head">
      <h3 title="Backend Entry Readiness (ADR-021): narrows an existing BUY/SELL Signal; never creates one. WAIT is always NOT_READY.">Entry Readiness ⓘ</h3>
      <button type="button" aria-label="Entry Readiness display" aria-pressed={enabled} onClick={onToggle}>{enabled ? "ON" : "OFF"}</button>
    </div>
    {enabled && <EntryReadinessBody readiness={readiness} stale={stale} />}
  </div>;
}

// Display toggle only, session-scoped React state: never persisted and never sent anywhere.
export function EntryReadiness({ readiness, stale, initialEnabled = true }: {
  readiness: unknown; stale?: boolean; initialEnabled?: boolean;
}) {
  const [enabled, setEnabled] = useState(initialEnabled);
  return <EntryReadinessView readiness={readiness} stale={stale} enabled={enabled} onToggle={() => setEnabled((value) => !value)} />;
}
