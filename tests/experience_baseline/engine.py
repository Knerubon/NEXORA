"""Pure, deterministic freezing and measurement. No persistence or decision feedback."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from nexora.artifacts import canonical_hash, canonical_serialize
from nexora.experience.models import POLICY, Experience, frozen_json
from nexora.market_data.models import NormalizedPriceEvent

# Output fields added to the frozen context after experience-v1 was first committed
# (ADR-020 Decision 13, ADR-021 Decision 12). Each is frozen only when the recorded
# output carries it, so replaying output recorded before the field existed reproduces
# the committed snapshot exactly (ADR-028). Append future additive fields here.
ADDITIVE_OUTPUT_CONTEXT = ("trendline", "entry_readiness")


def scope_for(config: Any, event: NormalizedPriceEvent) -> str:
    return canonical_hash((config, event.source, event.symbol, event.price_source, event.units))


def fingerprint(scope: str, output: dict[str, Any]) -> str:
    decision = output.get("signals", {}).get("decision") or {}
    matrix = output.get("matrix") or {}
    structure = output.get("structure") or {}
    regime = output.get("regime", {}).get("state") or {}
    resolutions = sorted(
        (
            {
                "name": r.get("name"),
                "direction": r.get("direction"),
                "status": r.get("status"),
                "column_id": (r.get("latest_transition") or {}).get("column_id"),
            }
            for r in matrix.get("resolutions", [])
        ),
        key=lambda r: str(r["name"]),
    )
    # All confirmed patterns survive, including simultaneous/conflicting evidence.
    patterns = sorted(decision.get("patterns") or [], key=frozen_json)
    evidence = {
        (e.get("component"), e.get("code"), e.get("polarity"))
        for e in (
            *(decision.get("positive_evidence") or []),
            *(decision.get("negative_evidence") or []),
        )
    }
    return canonical_hash(
        {
            "policy": POLICY,
            "scope": scope,
            "action": decision.get("action"),
            "engine_version": decision.get("engine_version"),
            "config_version": decision.get("config_version"),
            "matrix": resolutions,
            "pivots": structure.get("pivots"),
            "levels": [
                {k: v for k, v in level.items() if k != "updated_at"}
                for level in structure.get("levels", [])
            ],
            "regime": {k: regime.get(k) for k in ("label", "reason", "config_version")},
            "patterns": patterns,
            "evidence": sorted(evidence, key=frozen_json),
            "future_conditions": sorted(decision.get("future_conditions") or []),
        }
    )


def freeze(
    config: Any,
    runtime_stream: str,
    event: NormalizedPriceEvent,
    output: dict[str, Any],
    completeness: str,
    metadata: dict[str, Any] | None,
) -> Experience:
    config = canonical_serialize(config)
    output = canonical_serialize(output)
    scope = scope_for(config, event)
    digest = fingerprint(scope, output)
    decision = output.get("signals", {}).get("decision")
    latest = output.get("signals", {}).get("latest")
    # A previous signal is historical context, not the current WAIT decision.
    signal = (
        latest
        if latest and latest.get("decision_time") == canonical_serialize(event.received_at)
        else None
    )
    bid, ask = event.bid, event.ask
    context = {
        "event": event,
        "market": {
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2 if bid is not None and ask is not None else None,
            "spread": ask - bid if bid is not None and ask is not None else None,
            "feed_age_seconds": (event.received_at - event.event_time).total_seconds(),
        },
        "decision": decision,
        "signal": signal,
        "latest_signal_context": latest,
        "matrix": output.get("matrix"),
        "structure": output.get("structure"),
        "regime": output.get("regime"),
        "pnf": {"columns": output.get("columns"), "transitions": output.get("transitions")},
        "runtime_config": config,
        "provenance": {
            "runtime_stream": runtime_stream,
            "config_hash": canonical_hash(config),
            "pipeline_version": config.get("pipeline", {}).get("version"),
            "implementation_version": config.get("implementation_version"),
            "signal_engine_version": (decision or {}).get("engine_version"),
            "strategy_version": (decision or {}).get("config_version"),
            "event_hash": canonical_hash(event),
            "output_hash": canonical_hash(output),
            "data_version": None,
        },
        "completeness": completeness,
        "metadata": metadata,
        "bias": None,
        "session_context": None,
        "news_context": None,
    }
    # Presence, not value: an explicitly recorded null is frozen as null.
    context.update({key: output[key] for key in ADDITIVE_OUTPUT_CONTEXT if key in output})
    return Experience(
        1,
        POLICY,
        canonical_hash((POLICY, scope, event.identity_key, digest)),
        scope,
        digest,
        event.received_at,
        (decision or {}).get("action"),
        frozen_json(context),
    )


def plan(experience: Experience) -> tuple[Decimal | None, Decimal | None, dict[str, Any]]:
    decision = experience.context().get("decision") or {}
    if experience.action not in {"BUY", "SELL"}:
        return None, None, {}
    zone = decision.get("entry_zone")
    if zone is None:
        return None, None, decision
    low, high = Decimal(zone["low"]), Decimal(zone["high"])
    if not low.is_finite() or not high.is_finite() or low <= 0 or high < low:
        return None, None, decision
    entry = (low + high) / 2
    stop = decision.get("invalidation_price")
    if stop is None:
        return entry, None, decision
    stop = Decimal(stop)
    if not stop.is_finite() or stop <= 0:
        return entry, None, decision
    risk = entry - stop if experience.action == "BUY" else stop - entry
    valid = stop < low if experience.action == "BUY" else stop > high
    return entry, risk if valid else None, decision


def eligible(experience: Experience, event: NormalizedPriceEvent) -> bool:
    return event.event_time > experience.t0 and event.received_at > experience.t0


def measure(
    experience: Experience,
    minutes: int,
    samples: list[dict[str, Any]],
    endpoint: NormalizedPriceEvent,
) -> dict[str, Any]:
    due = experience.t0 + timedelta(minutes=minutes)
    window = [
        r
        for r in samples
        if eligible(experience, r["event"])
        and r["event"].event_time <= due
        and r["event"].received_at <= due
    ]
    # Rebuild only from facts known within this horizon, in original receipt order.
    # The late endpoint and the live lifecycle cannot supply historical plan facts.
    lifecycle = initial_lifecycle(experience)
    for row in window:
        for state in advance(experience, lifecycle, row["event"]):
            lifecycle = state
    prices = [r["event"].price for r in window]
    t0_price = Decimal(experience.context()["event"]["price"])
    entry, risk, _ = plan(experience)
    reference = entry if entry is not None else t0_price
    sign = Decimal(1) if experience.action == "BUY" else Decimal(-1)
    directional = experience.action in {"BUY", "SELL"}
    moves = [sign * (p - reference) for p in prices]
    mfe = max(Decimal(0), max(moves)) if moves and directional else None
    mae = max(Decimal(0), -min(moves)) if moves and directional else None
    return {
        "schema_version": 1,
        "policy_version": POLICY,
        "experience_id": experience.experience_id,
        "horizon_minutes": minutes,
        "due_at": due,
        "endpoint_event_id": endpoint.identity_key,
        "endpoint_event_time": endpoint.event_time,
        "endpoint_received_at": endpoint.received_at,
        "endpoint_delay_seconds": (endpoint.event_time - due).total_seconds(),
        "endpoint_price": endpoint.price,
        "price_change": endpoint.price - t0_price,
        "directional_change": sign * (endpoint.price - t0_price) if directional else None,
        "measurement_reference": reference if directional else t0_price,
        "reference_kind": "entry_zone_midpoint" if entry is not None else "t0_price",
        "mfe": mfe,
        "mae": mae,
        "mfe_r": mfe / risk if mfe is not None and risk is not None else None,
        "mae_r": mae / risk if mae is not None and risk is not None else None,
        "risk_distance": risk,
        "market_upward_excursion": max(Decimal(0), max(prices) - t0_price) if prices else None,
        "market_downward_excursion": max(Decimal(0), t0_price - min(prices)) if prices else None,
        "sample_count": len(window),
        "sample_event_ids": [r["event"].identity_key for r in window],
        "gap_observed": any(r["event"].is_gap for r in window),
        "completeness_values": sorted({r["completeness"] for r in window}),
        "coverage": "sampled_only" if window else "no_in_window_samples",
        "plan_hits": lifecycle["hits"],
        "entry_observed_by_horizon": lifecycle["entered_at"] is not None,
        "metric_scope": "t0_setup_observations_not_trade_pnl",
        "hypothetical_only": True,
    }


def initial_lifecycle(experience: Experience) -> dict[str, Any]:
    return {
        "state": "SIGNAL_CREATED" if experience.action in {"BUY", "SELL"} else "OBSERVED",
        "entered_at": None,
        "hits": {"TP1": None, "TP2": None, "invalidation": None},
    }


def advance(
    experience: Experience,
    previous: dict[str, Any],
    event: NormalizedPriceEvent,
) -> list[dict[str, Any]]:
    """Only sampled prices establish hypothetical entry/hits; no intrabar path guessing."""
    state = {**previous, "hits": dict(previous["hits"])}
    entry, risk, decision = plan(experience)
    if (
        risk is None
        or entry is None
        or state["state"]
        in {
            "TP2",
            "INVALIDATED",
            "CLOSED",
            "EXPIRED",
        }
    ):
        return []
    if event.event_time > experience.t0 + timedelta(minutes=60):
        return []
    zone = decision["entry_zone"]
    if state["entered_at"] is None:
        if Decimal(zone["low"]) <= event.price <= Decimal(zone["high"]):
            state["entered_at"] = event.event_time
            return [{**state, "state": "ENTRY_TRIGGERED"}, {**state, "state": "ACTIVE"}]
        return []
    # A late market timestamp cannot attach a hit before entry or a prior transition.
    latest_fact = max(
        [state["entered_at"]]
        + [hit["event_time"] for hit in state["hits"].values() if hit is not None]
    )
    if event.event_time < latest_fact:
        return []
    is_buy = experience.action == "BUY"
    stop = Decimal(decision["invalidation_price"])
    hit = {"event_id": event.identity_key, "event_time": event.event_time, "price": event.price}
    if (is_buy and event.price <= stop) or (not is_buy and event.price >= stop):
        state["hits"]["invalidation"] = hit
        state["state"] = "INVALIDATED"
        return [state]
    changes = []
    for name in ("TP1", "TP2"):
        target = next((t for t in decision.get("targets", []) if t["name"] == name), None)
        if target is None or state["hits"][name] is not None:
            continue
        price = Decimal(target["price"])
        if not price.is_finite() or (price <= entry if is_buy else price >= entry):
            continue
        if (is_buy and event.price >= price) or (not is_buy and event.price <= price):
            state["hits"][name] = hit
            state["state"] = name
            changes.append({**state, "hits": dict(state["hits"])})
    return changes
