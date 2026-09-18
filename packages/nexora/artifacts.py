"""Canonical encoding and caller-selected typed reconstruction."""

from __future__ import annotations

import hashlib
import json
import types
from dataclasses import asdict, fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Union, cast, get_args, get_origin, get_type_hints


def canonical_serialize(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return canonical_serialize(asdict(value))
    if isinstance(value, dict):
        return {key: canonical_serialize(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [canonical_serialize(item) for item in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non_finite_decimal")
        encoded = format(value, "f")
        if "." in encoded:
            encoded = encoded.rstrip("0").rstrip(".")
        return "0" if value == 0 else encoded
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone_required")
        return value.astimezone(UTC).isoformat()
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(canonical_serialize(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def decode[T](model: type[T], payload: Any) -> T:
    return cast(T, _decode(model, payload))


def _decode(model: Any, value: Any) -> Any:
    origin, args = get_origin(model), get_args(model)
    if origin in (Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        for candidate in args:
            if candidate is type(None):
                continue
            try:
                return _decode(candidate, value)
            except (ValueError, TypeError, KeyError):
                continue
        raise ValueError("invalid_union")
    if origin is Literal:
        if value not in args:
            raise ValueError("invalid_literal")
        return value
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise ValueError("invalid_tuple")
        return tuple(_decode(args[0], item) for item in value)
    if model is Decimal:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("non_finite_decimal")
        return result
    if model is datetime:
        result_time = datetime.fromisoformat(value)
        if result_time.tzinfo is None:
            raise ValueError("timezone_required")
        return result_time.astimezone(UTC)
    if is_dataclass(model):
        hints = get_type_hints(model)
        if not isinstance(value, dict) or set(value) - {f.name for f in fields(model)}:
            raise ValueError("unknown_schema_fields")
        return cast(Any, model)(**{k: _decode(hints[k], v) for k, v in value.items()})
    if model in (str, int, bool, float):
        if type(value) is not model:
            raise ValueError("invalid_scalar")
        return value
    raise ValueError("unsupported_schema")
