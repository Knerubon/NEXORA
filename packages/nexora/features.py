"""Feature Lifecycle V1 pure core (ADR-023).

Closed code-level registry, strict feature-config parsing, deterministic resolution
and ``feature_config_hash``. This module never reads the environment or the
filesystem: the API layer resolves ``NEXORA_FEATURES_CONFIG`` and passes the parsed
payload (or ``None``) here.

Fail-closed rules (frozen by ADR-023):

- no config => every registered feature is DISABLED;
- unknown features/units/keys, invalid literals or types => ``invalid_features_config``;
- a lifecycle above the registry ``max_lifecycle`` => ``feature_lifecycle_not_permitted``.
  A lifecycle is never silently downgraded.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from nexora.artifacts import canonical_hash

FeatureLifecycle = Literal["ACTIVE", "SHADOW", "DISABLED"]
FeatureClass = Literal["analytical", "decisional"]
FeatureHealth = Literal["ready", "warmup", "degraded", "unavailable", "not_evaluated"]

LIFECYCLES: tuple[FeatureLifecycle, ...] = ("DISABLED", "SHADOW", "ACTIVE")
_RANK: dict[str, int] = {name: rank for rank, name in enumerate(LIFECYCLES)}

FEATURES_CONFIG_SCHEMA_VERSION = 1
# Global Safe Mode ceiling (ADR-023 Decision 9). V1 fixes it to ACTIVE, a no-op.
V1_CEILING: FeatureLifecycle = "ACTIVE"

INVALID_FEATURES_CONFIG = "invalid_features_config"
FEATURE_LIFECYCLE_NOT_PERMITTED = "feature_lifecycle_not_permitted"


class FeatureConfigError(ValueError):
    """Startup must fail with ``code``; ``detail`` names the offending entry."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def lifecycle_min(*values: FeatureLifecycle) -> FeatureLifecycle:
    return min(values, key=_RANK.__getitem__)


@dataclass(frozen=True, slots=True)
class FeatureRegistration:
    """One registry entry, fixed in code by the feature's own accepted ADR."""

    feature_id: str
    feature_class: FeatureClass
    max_lifecycle: FeatureLifecycle
    units: tuple[str, ...]
    consumer_contract: str
    adr: str

    def __post_init__(self) -> None:
        if not self.feature_id or not self.units or len(set(self.units)) != len(self.units):
            raise ValueError("invalid_feature_registration")
        if self.feature_class not in ("analytical", "decisional"):
            raise ValueError("invalid_feature_registration")
        if self.max_lifecycle not in _RANK:
            raise ValueError("invalid_feature_registration")
        # ADR-023 Decision 7: this ADR never permits decisional ACTIVE.
        if self.feature_class == "decisional" and self.max_lifecycle == "ACTIVE":
            raise ValueError("decisional_active_not_permitted")


PATTERN_ENGINE_FEATURE_ID = "pattern_engine"
PATTERN_ENGINE_UNITS: tuple[str, ...] = (
    "legacy_pivot.double_bottom",
    "legacy_pivot.double_top",
    "legacy_pivot.head_and_shoulders",
    "legacy_pivot.inverse_head_and_shoulders",
    "legacy_pivot.triangle_breakdown",
    "legacy_pivot.triangle_breakout",
    "legacy_pivot.failed_breakout",
    "legacy_pivot.failed_breakdown",
)

PATTERN_ENGINE_REGISTRATION = FeatureRegistration(
    feature_id=PATTERN_ENGINE_FEATURE_ID,
    feature_class="analytical",
    max_lifecycle="SHADOW",
    units=PATTERN_ENGINE_UNITS,
    consumer_contract="evaluation and display only; no decision-chain consumer (ADR-024)",
    adr="ADR-024",
)

FEATURE_REGISTRY: tuple[FeatureRegistration, ...] = (PATTERN_ENGINE_REGISTRATION,)


def _registry_map(
    registry: Iterable[FeatureRegistration],
) -> dict[str, FeatureRegistration]:
    entries = tuple(registry)
    mapping = {entry.feature_id: entry for entry in entries}
    if len(mapping) != len(entries):
        raise ValueError("duplicate_feature_registration")
    return mapping


@dataclass(frozen=True, slots=True)
class ResolvedFeature:
    feature_id: str
    feature_class: FeatureClass
    lifecycle: FeatureLifecycle
    units: tuple[tuple[str, FeatureLifecycle], ...]  # every registered unit, registry order


@dataclass(frozen=True, slots=True)
class ResolvedFeatureConfig:
    """Fully resolved feature configuration: every registered feature and unit explicit."""

    schema_version: int
    version: str
    ceiling: FeatureLifecycle
    features: tuple[ResolvedFeature, ...]  # sorted by feature_id
    feature_config_hash: str

    def feature(self, feature_id: str) -> ResolvedFeature:
        for feature in self.features:
            if feature.feature_id == feature_id:
                return feature
        raise KeyError(feature_id)

    def effective_feature(self, feature_id: str) -> FeatureLifecycle:
        return lifecycle_min(self.ceiling, self.feature(feature_id).lifecycle)

    def configured_unit(self, feature_id: str, unit_id: str) -> FeatureLifecycle:
        return dict(self.feature(feature_id).units)[unit_id]

    def effective_unit(self, feature_id: str, unit_id: str) -> FeatureLifecycle:
        """``min(ceiling, feature_lifecycle, unit_lifecycle)`` (ADR-023 Decision 3)."""
        feature = self.feature(feature_id)
        return lifecycle_min(self.ceiling, feature.lifecycle, dict(feature.units)[unit_id])


def _hash_payload(
    schema_version: int,
    version: str,
    ceiling: FeatureLifecycle,
    features: tuple[ResolvedFeature, ...],
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "version": version,
        "ceiling": ceiling,
        "features": {
            feature.feature_id: {"lifecycle": feature.lifecycle, "units": dict(feature.units)}
            for feature in features
        },
    }


def _build(
    schema_version: int,
    version: str,
    features: tuple[ResolvedFeature, ...],
) -> ResolvedFeatureConfig:
    ordered = tuple(sorted(features, key=lambda item: item.feature_id))
    digest = canonical_hash(_hash_payload(schema_version, version, V1_CEILING, ordered))
    return ResolvedFeatureConfig(
        schema_version=schema_version,
        version=version,
        ceiling=V1_CEILING,
        features=ordered,
        feature_config_hash=digest,
    )


def default_features_config(
    registry: Iterable[FeatureRegistration] = FEATURE_REGISTRY,
) -> ResolvedFeatureConfig:
    """``NEXORA_FEATURES_CONFIG`` unset: every registered feature DISABLED."""
    features = tuple(
        ResolvedFeature(
            feature_id=entry.feature_id,
            feature_class=entry.feature_class,
            lifecycle="DISABLED",
            units=tuple((unit, "DISABLED") for unit in entry.units),
        )
        for entry in _registry_map(registry).values()
    )
    return _build(FEATURES_CONFIG_SCHEMA_VERSION, "", features)


def _invalid(detail: str) -> FeatureConfigError:
    return FeatureConfigError(INVALID_FEATURES_CONFIG, detail)


def _object(value: Any, where: str, required: set[str], optional: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _invalid(f"{where}: expected object")
    keys = set(value)
    if unknown := sorted(keys - required - optional):
        raise _invalid(f"{where}: unknown keys {unknown}")
    if missing := sorted(required - keys):
        raise _invalid(f"{where}: missing keys {missing}")
    return cast(dict[str, Any], value)


def _lifecycle(value: Any, where: str, entry: FeatureRegistration) -> FeatureLifecycle:
    if not isinstance(value, str) or value not in _RANK:
        raise _invalid(f"{where}: invalid lifecycle {value!r}")
    lifecycle = cast(FeatureLifecycle, value)
    if _RANK[lifecycle] > _RANK[entry.max_lifecycle]:
        raise FeatureConfigError(
            FEATURE_LIFECYCLE_NOT_PERMITTED,
            f"{where}: {lifecycle} exceeds max_lifecycle {entry.max_lifecycle}",
        )
    return lifecycle


def resolve_features_config(
    payload: Mapping[str, Any] | None,
    registry: Iterable[FeatureRegistration] = FEATURE_REGISTRY,
) -> ResolvedFeatureConfig:
    """Validate a decoded features-config object and resolve every default explicitly."""
    registered = _registry_map(registry)
    if payload is None:
        return default_features_config(registered.values())
    root = _object(dict(payload), "config", {"schema_version", "version", "features"}, set())
    schema_version = root["schema_version"]
    if type(schema_version) is not int or schema_version != FEATURES_CONFIG_SCHEMA_VERSION:
        raise _invalid(f"config: unsupported schema_version {schema_version!r}")
    version = root["version"]
    if not isinstance(version, str) or not version:
        raise _invalid("config: version must be a non-empty string")
    configured = _object(root["features"], "features", set(), set(registered))
    features: list[ResolvedFeature] = []
    for feature_id, entry in registered.items():
        if feature_id not in configured:
            lifecycle: FeatureLifecycle = "DISABLED"
            units_config: dict[str, Any] = {}
        else:
            where = f"features.{feature_id}"
            item = _object(configured[feature_id], where, {"lifecycle"}, {"units"})
            lifecycle = _lifecycle(item["lifecycle"], f"{where}.lifecycle", entry)
            units_config = _object(item.get("units", {}), f"{where}.units", set(), set(entry.units))
        units: list[tuple[str, FeatureLifecycle]] = []
        for unit in entry.units:
            if unit in units_config:
                units.append((unit, _lifecycle(units_config[unit], f"{feature_id}.{unit}", entry)))
            else:
                units.append((unit, lifecycle))
        features.append(
            ResolvedFeature(
                feature_id=feature_id,
                feature_class=entry.feature_class,
                lifecycle=lifecycle,
                units=tuple(units),
            )
        )
    return _build(schema_version, version, tuple(features))


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise _invalid(f"non-finite number {value}")


def parse_features_config(
    text: str,
    registry: Iterable[FeatureRegistration] = FEATURE_REGISTRY,
) -> ResolvedFeatureConfig:
    """Strictly decode features-config JSON text (duplicate keys and NaN rejected)."""
    try:
        payload = json.loads(
            text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant
        )
    except FeatureConfigError:
        raise
    except ValueError as error:
        raise _invalid(f"undecodable JSON: {error}") from None
    if not isinstance(payload, dict):
        raise _invalid("config: expected object")
    return resolve_features_config(payload, registry)


@dataclass(frozen=True, slots=True)
class FeatureUnitStatus:
    unit_id: str
    configured_lifecycle: FeatureLifecycle
    effective_lifecycle: FeatureLifecycle
    health: FeatureHealth
    reason_codes: tuple[str, ...]
    algorithm_version: str


@dataclass(frozen=True, slots=True)
class FeatureStatus:
    """Status block published beside a feature's results every step (ADR-023 Decision 5)."""

    schema_version: Literal[1]
    feature_id: str
    feature_class: FeatureClass
    configured_lifecycle: FeatureLifecycle
    effective_lifecycle: FeatureLifecycle
    health: FeatureHealth
    reason_codes: tuple[str, ...]
    engine_version: str
    feature_config_version: str
    feature_config_hash: str
    units: tuple[FeatureUnitStatus, ...]
