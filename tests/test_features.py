"""Feature Lifecycle V1 pure core (ADR-023)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from nexora.features import (
    FEATURE_LIFECYCLE_NOT_PERMITTED,
    FEATURE_REGISTRY,
    INVALID_FEATURES_CONFIG,
    PATTERN_ENGINE_FEATURE_ID,
    PATTERN_ENGINE_UNITS,
    FeatureConfigError,
    FeatureRegistration,
    default_features_config,
    parse_features_config,
    resolve_features_config,
)

ANALYTICAL = FeatureRegistration(
    feature_id="test_analytical",
    feature_class="analytical",
    max_lifecycle="ACTIVE",
    units=("test.a", "test.b"),
    consumer_contract="test: production-visible analytical output only",
    adr="ADR-TEST",
)
TEST_REGISTRY = (*FEATURE_REGISTRY, ANALYTICAL)


def _config(features: dict[str, Any], version: str = "dev-1") -> dict[str, Any]:
    return {"schema_version": 1, "version": version, "features": features}


def _error(payload: Any, registry: tuple[FeatureRegistration, ...] = FEATURE_REGISTRY) -> str:
    with pytest.raises(FeatureConfigError) as caught:
        if isinstance(payload, str):
            parse_features_config(payload, registry)
        else:
            resolve_features_config(payload, registry)
    return caught.value.code


def test_pattern_engine_registration_is_analytical_shadow_only() -> None:
    (entry,) = FEATURE_REGISTRY
    assert entry.feature_id == PATTERN_ENGINE_FEATURE_ID
    assert entry.feature_class == "analytical"
    assert entry.max_lifecycle == "SHADOW"
    assert entry.units == PATTERN_ENGINE_UNITS
    assert entry.adr == "ADR-024"
    assert entry.consumer_contract


def test_no_config_resolves_every_feature_disabled() -> None:
    resolved = resolve_features_config(None, TEST_REGISTRY)
    assert resolved == default_features_config(TEST_REGISTRY)
    assert resolved.version == ""
    for feature in resolved.features:
        assert feature.lifecycle == "DISABLED"
        assert {lifecycle for _, lifecycle in feature.units} == {"DISABLED"}
        for unit, _ in feature.units:
            assert resolved.effective_unit(feature.feature_id, unit) == "DISABLED"


def test_valid_disabled_and_shadow() -> None:
    disabled = resolve_features_config(_config({"pattern_engine": {"lifecycle": "DISABLED"}}))
    assert disabled.effective_feature("pattern_engine") == "DISABLED"
    shadow = resolve_features_config(_config({"pattern_engine": {"lifecycle": "SHADOW"}}))
    assert shadow.effective_feature("pattern_engine") == "SHADOW"
    assert all(
        shadow.effective_unit("pattern_engine", unit) == "SHADOW" for unit in PATTERN_ENGINE_UNITS
    )


def test_unit_override_and_effective_minimum() -> None:
    resolved = resolve_features_config(
        _config(
            {
                "pattern_engine": {
                    "lifecycle": "SHADOW",
                    "units": {"legacy_pivot.double_top": "DISABLED"},
                }
            }
        )
    )
    assert resolved.effective_unit("pattern_engine", "legacy_pivot.double_top") == "DISABLED"
    assert resolved.effective_unit("pattern_engine", "legacy_pivot.double_bottom") == "SHADOW"
    engine_off = resolve_features_config(
        _config(
            {
                "pattern_engine": {
                    "lifecycle": "DISABLED",
                    "units": {"legacy_pivot.double_top": "SHADOW"},
                }
            }
        )
    )
    # Engine DISABLED => every unit DISABLED (ADR-023 Decision 3).
    assert engine_off.effective_unit("pattern_engine", "legacy_pivot.double_top") == "DISABLED"


def test_analytical_active_where_registry_permits_it() -> None:
    resolved = resolve_features_config(
        _config({"test_analytical": {"lifecycle": "ACTIVE", "units": {"test.b": "SHADOW"}}}),
        TEST_REGISTRY,
    )
    assert resolved.effective_feature("test_analytical") == "ACTIVE"
    assert resolved.effective_unit("test_analytical", "test.a") == "ACTIVE"
    assert resolved.effective_unit("test_analytical", "test.b") == "SHADOW"
    assert resolved.feature("test_analytical").feature_class == "analytical"
    assert resolved.effective_feature("pattern_engine") == "DISABLED"


def test_pattern_engine_active_is_not_permitted_and_never_downgraded() -> None:
    assert (
        _error(_config({"pattern_engine": {"lifecycle": "ACTIVE"}}))
        == FEATURE_LIFECYCLE_NOT_PERMITTED
    )
    unit_active = _config(
        {"pattern_engine": {"lifecycle": "SHADOW", "units": {"legacy_pivot.double_top": "ACTIVE"}}}
    )
    assert _error(unit_active) == FEATURE_LIFECYCLE_NOT_PERMITTED


def test_lifecycle_above_max_is_a_hard_failure() -> None:
    shadow_only = FeatureRegistration(
        feature_id="test_shadow_only",
        feature_class="analytical",
        max_lifecycle="DISABLED",
        units=("x",),
        consumer_contract="none",
        adr="ADR-TEST",
    )
    registry = (*FEATURE_REGISTRY, shadow_only)
    payload = _config({"test_shadow_only": {"lifecycle": "SHADOW"}})
    assert _error(payload, registry) == FEATURE_LIFECYCLE_NOT_PERMITTED


def test_decisional_active_cannot_be_registered() -> None:
    with pytest.raises(ValueError, match="decisional_active_not_permitted"):
        FeatureRegistration(
            feature_id="test_decisional",
            feature_class="decisional",
            max_lifecycle="ACTIVE",
            units=("x",),
            consumer_contract="none",
            adr="ADR-TEST",
        )


@pytest.mark.parametrize(
    "payload",
    [
        _config({"unknown_feature": {"lifecycle": "SHADOW"}}),
        _config(
            {"pattern_engine": {"lifecycle": "SHADOW", "units": {"legacy_pivot.nope": "SHADOW"}}}
        ),
        _config({"pattern_engine": {"lifecycle": "shadow"}}),
        _config({"pattern_engine": {"lifecycle": 1}}),
        _config({"pattern_engine": {"lifecycle": "SHADOW", "extra": True}}),
        _config({"pattern_engine": {}}),
        _config({"pattern_engine": "SHADOW"}),
        _config({"pattern_engine": {"lifecycle": "SHADOW", "units": ["x"]}}),
        {"schema_version": 2, "version": "v", "features": {}},
        {"schema_version": True, "version": "v", "features": {}},
        {"schema_version": 1, "version": "", "features": {}},
        {"schema_version": 1, "version": 3, "features": {}},
        {"schema_version": 1, "features": {}},
        {"schema_version": 1, "version": "v", "features": {}, "ceiling": "ACTIVE"},
    ],
)
def test_invalid_config_fails_closed(payload: dict[str, Any]) -> None:
    assert _error(payload) == INVALID_FEATURES_CONFIG


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        '{"schema_version": 1, "version": "v", "features": {}, "version": "w"}',
        '{"schema_version": NaN, "version": "v", "features": {}}',
    ],
)
def test_invalid_json_text_fails_closed(text: str) -> None:
    assert _error(text) == INVALID_FEATURES_CONFIG


def test_feature_config_hash_is_deterministic_and_order_independent() -> None:
    first = parse_features_config(
        '{"schema_version": 1, "version": "dev-1", "features": {"pattern_engine": '
        '{"units": {"legacy_pivot.double_top": "DISABLED", '
        '"legacy_pivot.double_bottom": "SHADOW"}, "lifecycle": "SHADOW"}}}'
    )
    second = parse_features_config(
        json.dumps(
            {
                "features": {
                    "pattern_engine": {
                        "lifecycle": "SHADOW",
                        "units": {
                            "legacy_pivot.double_bottom": "SHADOW",
                            "legacy_pivot.double_top": "DISABLED",
                        },
                    }
                },
                "version": "dev-1",
                "schema_version": 1,
            },
            indent=2,
        )
    )
    assert first == second
    assert first.feature_config_hash == second.feature_config_hash
    assert len(first.feature_config_hash) == 64


def test_resolved_defaults_are_included_in_hash() -> None:
    # Listing an inherited unit explicitly resolves to the same config and hash.
    implicit = resolve_features_config(_config({"pattern_engine": {"lifecycle": "SHADOW"}}))
    explicit = resolve_features_config(
        _config(
            {
                "pattern_engine": {
                    "lifecycle": "SHADOW",
                    "units": {unit: "SHADOW" for unit in PATTERN_ENGINE_UNITS},
                }
            }
        )
    )
    assert implicit.feature_config_hash == explicit.feature_config_hash
    # A registered-but-unlisted feature resolves to DISABLED and still enters the hash.
    unlisted = resolve_features_config(_config({}), TEST_REGISTRY)
    listed = resolve_features_config(
        _config({"test_analytical": {"lifecycle": "DISABLED"}}), TEST_REGISTRY
    )
    assert unlisted.feature_config_hash == listed.feature_config_hash
    assert unlisted.feature_config_hash != resolve_features_config(_config({})).feature_config_hash
    # Any lifecycle or label change changes the hash.
    hashes = {
        implicit.feature_config_hash,
        resolve_features_config(_config({})).feature_config_hash,
        resolve_features_config(
            _config({"pattern_engine": {"lifecycle": "SHADOW"}}, version="dev-2")
        ).feature_config_hash,
        resolve_features_config(
            _config(
                {
                    "pattern_engine": {
                        "lifecycle": "SHADOW",
                        "units": {"legacy_pivot.double_top": "DISABLED"},
                    }
                }
            )
        ).feature_config_hash,
        default_features_config().feature_config_hash,
    }
    assert len(hashes) == 5


def test_feature_config_hash_golden_vectors() -> None:
    # Changing these requires an explicit decision: checkpoints bind to this hash.
    assert default_features_config().feature_config_hash == (
        "56a716a4fe1999e06bc390f029f5239ac92e31b9b38b2121d2bc8c7f81e8da95"
    )
    shadow = resolve_features_config(_config({"pattern_engine": {"lifecycle": "SHADOW"}}))
    assert shadow.feature_config_hash == (
        "44785f59197f1ef47046404a9a19d0757c5384ac064964295454cdd9ca27f92c"
    )
