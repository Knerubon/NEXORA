"use client";

import type { NewsScannerDisplay, SocialScannerDisplay } from "./control-center-types";

// Both scanners have zero backend implementation today (confirmed: no
// News/Social scanning code anywhere in the repo). Status is hardcoded to
// NOT_IMPLEMENTED by the caller; toggles here are inert — nothing reads them.
export function ScannersPanel({ news, social }: { news: NewsScannerDisplay; social: SocialScannerDisplay }) {
  return (
    <section className="cc-panel cc-scanners" aria-label="Scanners">
      <header><h3>Scanners</h3></header>
      <div className="cc-scanner-row" data-cc-scanner-status={news.status}>
        <strong>News Scanner</strong>
        <span className="cc-scanner-status">{news.status === "NOT_IMPLEMENTED" ? "Not implemented" : news.status}</span>
        <label className="cc-inert-toggle" title="Not wired to any engine — display only">
          <input type="checkbox" checked={news.tradeDuringNews} disabled readOnly />
          Trade During News
        </label>
        <span>{news.nextBlackoutWindow ? `Next blackout: ${news.nextBlackoutWindow}` : "No blackout window data"}</span>
      </div>
      <div className="cc-scanner-row" data-cc-scanner-status={social.status}>
        <strong>Social Scanner</strong>
        <span className="cc-scanner-status">{social.status === "NOT_IMPLEMENTED" ? "Not implemented" : social.status}</span>
        <span>{social.lastSignal ?? "No signal data"}</span>
      </div>
    </section>
  );
}
