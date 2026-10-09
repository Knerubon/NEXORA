"""Deterministic, fail-closed market-data quality checks (ADR-036).

The Guard only observes. It never repairs, interpolates, reorders or drops data, and it
never reads a clock. Anything it cannot positively verify yields NO NEW TRADE.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from nexora.artifacts import canonical_hash
from nexora.data_quality import models as m
from nexora.data_quality.models import (
    MarketDataSnapshot,
    QualityExpectation,
    QualityFinding,
    QualityGuardConfig,
    QualityVerdict,
)
from nexora.market_data.models import NormalizedPriceEvent


class DataQualityGuard:
    def __init__(self, config: QualityGuardConfig, expectation: QualityExpectation) -> None:
        self._config = config
        self._expectation = expectation

    def evaluate(
        self, snapshot: MarketDataSnapshot, *, evaluated_at: datetime | None
    ) -> QualityVerdict:
        try:
            return self._evaluate(snapshot, evaluated_at)
        except Exception:
            # Fail closed; the message is deliberately dropped so nothing sensitive leaks.
            return self._verdict(
                [QualityFinding(m.GUARD_INTERNAL_ERROR, "unknown", None, ())],
                evaluated_at=None,
                snapshot=None,
            )

    def _evaluate(
        self, snapshot: MarketDataSnapshot, evaluated_at: datetime | None
    ) -> QualityVerdict:
        cfg, findings = self._config, []
        if evaluated_at is None or evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            findings.append(QualityFinding(m.UNSYNCHRONIZED_CLOCK, "unknown", None, ()))
            return self._verdict(findings, evaluated_at=None, snapshot=snapshot)
        now = evaluated_at.astimezone(UTC)

        events = snapshot.events
        if not events:
            findings.append(QualityFinding(m.MISSING_MARKET_DATA, "unknown", None, ()))
            return self._verdict(findings, evaluated_at=now, snapshot=snapshot)
        if len(events) < cfg.min_events:
            findings.append(
                QualityFinding(
                    m.INCOMPLETE_SNAPSHOT,
                    "unknown",
                    None,
                    _ev(have=len(events), need=cfg.min_events),
                )
            )

        seen: dict[str, str] = {}
        previous: NormalizedPriceEvent | None = None
        for event in events:
            key = event.identity_key
            findings.extend(self._identity(event))
            findings.extend(self._numeric(event))
            findings.extend(self._quote(event))
            findings.extend(self._clock(event, now))
            if key in seen:
                same = seen[key] == _fingerprint(event)
                findings.append(
                    QualityFinding(
                        m.DUPLICATE_RECORD if same else m.IDENTITY_CONFLICT,
                        "blocking",
                        key,
                        (),
                    )
                )
            else:
                seen[key] = _fingerprint(event)
            if event.is_duplicate:
                findings.append(QualityFinding(m.DUPLICATE_RECORD, "blocking", key, _ev(flag="1")))
            if event.is_out_of_order:
                findings.append(
                    QualityFinding(m.OUT_OF_ORDER_SEQUENCE, "blocking", key, _ev(flag="1"))
                )
            if event.is_gap:
                findings.append(QualityFinding(m.SEQUENCE_GAP, "blocking", key, _ev(flag="1")))
            if previous is not None:
                findings.extend(self._ordering(previous, event))
            previous = event

        latest = max(events, key=lambda e: e.event_time)
        age = (now - latest.event_time).total_seconds()
        if age > cfg.max_quote_age_seconds:
            findings.append(
                QualityFinding(
                    m.STALE_QUOTE,
                    "blocking",
                    latest.identity_key,
                    _ev(age_seconds=age, max_seconds=cfg.max_quote_age_seconds),
                )
            )
        return self._verdict(findings, evaluated_at=now, snapshot=snapshot)

    def _identity(self, event: NormalizedPriceEvent) -> list[QualityFinding]:
        exp = self._expectation
        if (event.source, event.symbol, event.units) == (exp.source, exp.symbol, exp.units):
            return []
        return [
            QualityFinding(
                m.IDENTITY_MISMATCH,
                "blocking",
                event.identity_key,
                _ev(
                    got_source=event.source,
                    got_symbol=event.symbol,
                    got_units=event.units,
                    want_source=exp.source,
                    want_symbol=exp.symbol,
                    want_units=exp.units,
                ),
            )
        ]

    def _numeric(self, event: NormalizedPriceEvent) -> list[QualityFinding]:
        out: list[QualityFinding] = []
        fields = {
            "price": event.price,
            "bid": event.bid,
            "ask": event.ask,
            "last": event.last,
            "open": event.open_price,
            "high": event.high,
            "low": event.low,
            "close": event.close,
        }
        for name, value in fields.items():
            if value is None:
                continue
            if isinstance(value, Decimal):
                finite = value.is_finite()
            elif isinstance(value, float):
                finite = math.isfinite(value)
            else:
                out.append(
                    QualityFinding(
                        m.INVALID_NUMERIC_TYPE, "blocking", event.identity_key, _ev(field=name)
                    )
                )
                continue
            if not finite:
                out.append(
                    QualityFinding(
                        m.NON_FINITE_VALUE, "blocking", event.identity_key, _ev(field=name)
                    )
                )
        return out

    def _quote(self, event: NormalizedPriceEvent) -> list[QualityFinding]:
        cfg, key = self._config, event.identity_key
        bid, ask = event.bid, event.ask
        if bid is None or ask is None:
            if cfg.require_bid_ask:
                return [QualityFinding(m.MISSING_BID_ASK, "unknown", key, _ev(bid=bid, ask=ask))]
            return []
        if not (_finite(bid) and _finite(ask)):
            return []  # already reported as non-finite; spread is undefined
        if bid <= 0 or ask <= 0:
            return [QualityFinding(m.INVALID_BID_ASK, "blocking", key, _ev(bid=bid, ask=ask))]
        spread = ask - bid
        if spread < 0:
            return [QualityFinding(m.NEGATIVE_SPREAD, "blocking", key, _ev(bid=bid, ask=ask))]
        out: list[QualityFinding] = []
        if spread == 0 and not cfg.allow_zero_spread:
            out.append(QualityFinding(m.ZERO_SPREAD, "blocking", key, _ev(bid=bid, ask=ask)))
        if cfg.max_spread is not None and spread > cfg.max_spread:
            out.append(
                QualityFinding(
                    m.EXCESSIVE_SPREAD,
                    "blocking",
                    key,
                    _ev(spread=spread, max_spread=cfg.max_spread),
                )
            )
        if (
            cfg.max_spread_to_price is not None
            and ask > 0
            and spread / ask > cfg.max_spread_to_price
        ):
            out.append(
                QualityFinding(
                    m.EXCESSIVE_SPREAD,
                    "blocking",
                    key,
                    _ev(spread=spread, ask=ask, max_ratio=cfg.max_spread_to_price),
                )
            )
        return out

    def _clock(self, event: NormalizedPriceEvent, now: datetime) -> list[QualityFinding]:
        cfg, key, out = self._config, event.identity_key, []
        if event.received_at < event.event_time:
            out.append(
                QualityFinding(
                    m.CLOCK_ANOMALY_RECEIVED_BEFORE_EVENT,
                    "blocking",
                    key,
                    _ev(
                        event_time=event.event_time.isoformat(),
                        received_at=event.received_at.isoformat(),
                    ),
                )
            )
        elif (event.received_at - event.event_time) > timedelta(milliseconds=cfg.max_latency_ms):
            latency_ms = (event.received_at - event.event_time).total_seconds() * 1000
            out.append(
                QualityFinding(
                    m.HIGH_LATENCY,
                    "blocking",
                    key,
                    _ev(latency_ms=latency_ms, max_ms=cfg.max_latency_ms),
                )
            )
        if event.event_time > now + timedelta(seconds=cfg.max_future_skew_seconds):
            out.append(
                QualityFinding(
                    m.CLOCK_ANOMALY_FUTURE_EVENT,
                    "blocking",
                    key,
                    _ev(event_time=event.event_time.isoformat(), evaluated_at=now.isoformat()),
                )
            )
        return out

    def _ordering(
        self, prev: NormalizedPriceEvent, cur: NormalizedPriceEvent
    ) -> list[QualityFinding]:
        out: list[QualityFinding] = []
        key = cur.identity_key
        if cur.event_time < prev.event_time:
            out.append(
                QualityFinding(
                    m.OUT_OF_ORDER_TIMESTAMP,
                    "blocking",
                    key,
                    _ev(prev=prev.event_time.isoformat(), cur=cur.event_time.isoformat()),
                )
            )
        if cur.source_sequence <= prev.source_sequence and cur.identity_key != prev.identity_key:
            out.append(
                QualityFinding(
                    m.OUT_OF_ORDER_SEQUENCE,
                    "blocking",
                    key,
                    _ev(prev=prev.source_sequence, cur=cur.source_sequence),
                )
            )
        gap = (cur.event_time - prev.event_time).total_seconds()
        if gap > self._config.max_gap_seconds:
            out.append(
                QualityFinding(
                    m.TIMESTAMP_GAP,
                    "blocking",
                    key,
                    _ev(gap_seconds=gap, max_seconds=self._config.max_gap_seconds),
                )
            )
        return out

    def _verdict(
        self,
        findings: list[QualityFinding],
        *,
        evaluated_at: datetime | None,
        snapshot: MarketDataSnapshot | None,
    ) -> QualityVerdict:
        ordered = tuple(sorted(findings, key=lambda f: (f.code, f.event_key or "", f.evidence)))
        if any(f.severity == "blocking" for f in ordered):
            state: m.QualityState = "blocked"
        elif ordered:
            state = "unknown"
        else:
            state = "ok"
        keys: tuple[str, ...] = ()
        digest: str | None = None
        if snapshot is not None:
            keys = tuple(e.identity_key for e in snapshot.events)
            try:
                digest = canonical_hash(snapshot.events)
            except Exception:
                digest = None  # non-hashable evidence is itself unverifiable
                if state == "ok":
                    state = "unknown"
                    ordered = (
                        *ordered,
                        QualityFinding(m.GUARD_INTERNAL_ERROR, "unknown", None, ()),
                    )
        return QualityVerdict(
            schema_version=1,
            state=state,
            new_trade_permitted=state == "ok",
            findings=ordered,
            evaluated_at=evaluated_at,
            event_keys=keys,
            snapshot_hash=digest,
            config_version=self._config.version,
        )


def _fingerprint(event: NormalizedPriceEvent) -> str:
    """Deterministic identity of an event, even one canonical hashing rejects (e.g. NaN).

    Such an event is reported by its real cause (non_finite_value), not as a guard error.
    """
    try:
        return canonical_hash(event)
    except Exception:
        return "repr:" + repr(event)


def _finite(value: Decimal | float) -> bool:
    return value.is_finite() if isinstance(value, Decimal) else math.isfinite(value)


def _ev(**values: object) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((name, _text(value)) for name, value in values.items()))


def _text(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f") if value.is_finite() else str(value)
    return str(value)
