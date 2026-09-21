export type QuoteSnapshot = {
  stream_id: string;
  sequence: number;
  status: string;
  symbol?: string;
  quote: {
    symbol: string; bid: string; ask: string; event_time: string;
    raw_event_time?: string; time_offset_seconds?: number;
  } | null;
};

// Sequence is an observation counter, never evidence of a new market tick.
// Delayed REST/full-state responses must not roll the displayed quote back.
export function latestQuote(prior: QuoteSnapshot | null, incoming: QuoteSnapshot): QuoteSnapshot {
  if (prior?.stream_id === incoming.stream_id && prior.sequence >= incoming.sequence) return prior;
  return incoming;
}
