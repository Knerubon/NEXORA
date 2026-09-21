import test from "node:test";
import assert from "node:assert/strict";
import { latestQuote } from "../app/live-quote.ts";

const snapshot = (sequence, eventTime = "2026-09-21T07:00:00Z") => ({
  stream_id: "feed", sequence, status: "live",
  quote: { symbol: "XAUUSD", bid: "2000", ask: "2001", event_time: eventTime },
});

test("fast quotes are not rolled back by delayed REST or full research snapshots", () => {
  let displayed = null;
  for (let sequence = 1; sequence <= 10; sequence++) {
    const next = snapshot(sequence, `2026-09-21T07:00:${String(sequence).padStart(2, "0")}Z`);
    displayed = latestQuote(displayed, next);
    assert.equal(displayed, next);
    assert.equal(latestQuote(displayed, snapshot(sequence - 1)), displayed);
  }
});

test("heartbeats retain the real timestamp, and reconnect accepts a new stream", () => {
  const first = snapshot(1);
  const repeated = latestQuote(first, snapshot(2));
  assert.deepEqual(repeated.quote, first.quote);
  assert.equal(latestQuote(repeated, repeated), repeated);
  const restarted = { ...snapshot(0), stream_id: "restarted" };
  assert.equal(latestQuote(repeated, restarted), restarted);
});
