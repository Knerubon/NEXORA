# ADR-019 — Explicit feed timestamp correction

Status: local corrective decision; independent review pending

[FIX2](../../tasks/FIX2-mt5-feed-time.md), [requirements](../requirements.md), [architecture](../architecture.md).

The official [MT5 Python documentation](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksfrom_py) specifies UTC timestamps. Current conversion already uses UTC explicitly. The selected feed nevertheless supplies advancing tick epochs approximately three hours ahead of independently checked UTC. Broker timezone/DST semantics are not verified.

Add `NEXORA_MT5_TIME_OFFSET_SECONDS`, integer in [-50400, 50400], default 0. Subtract it from the raw epoch; never auto-detect or track clock differences. Positive 10800 is a local workaround for the observed feed only. Keep raw_event_time and time_offset_seconds in quote output, normalized identity and versioned source provenance. Freshness checks operate on corrected event_time and still reject stale/future data. Operators must revalidate or remove this setting when feed behavior/DST changes. Do not change the machine clock or terminal settings to mask the problem.

This exception is visible in API/UI and journal provenance; it is not a change to the UTC domain contract or certification of source timestamp semantics. Quote coverage remains unknown, paper remains disabled. Local chart parameters copied from the example are labelled preview assumptions, not approved trading parameters. Historical P1–P4 and source-of-truth documents remain unchanged.
