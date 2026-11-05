"""Input validation for the closed operation registry.

Every operation's input is checked against its own schema before a connection is
hydrated or a request is built. The checker executes the same keyword subset the
platform executes (type, enum, const, required, properties,
additionalProperties, items, min/max length, min/max, min/max items) and refuses a
schema that uses anything else, so a rule written into a row cannot silently
never run. Formats the subset cannot express — timestamps, decimals — are
checked by the helpers below and called explicitly by the handlers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from . import errors
from .errors import ExtensionError

_EXECUTED = frozenset(
    {
        "type",
        "enum",
        "const",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
    }
)
_ANNOTATIONS = frozenset(
    {"title", "description", "default", "examples", "format", "deprecated", "readOnly", "writeOnly"}
)

_EXTERNAL_EFFECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_DECIMAL_RE = re.compile(r"^-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,6})?$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def assert_executable(schema: Any, path: str = "$") -> None:
    """Refuse a schema keyword the checker would not execute."""
    if not isinstance(schema, Mapping):
        return
    unknown = set(schema) - _EXECUTED - _ANNOTATIONS
    if unknown:
        raise ValueError(f"{path} uses unsupported schema keywords: {sorted(unknown)}")
    for name, child in (schema.get("properties") or {}).items():
        assert_executable(child, f"{path}.{name}")
    if isinstance(schema.get("items"), Mapping):
        assert_executable(schema["items"], f"{path}[]")
    if isinstance(schema.get("additionalProperties"), Mapping):
        assert_executable(schema["additionalProperties"], f"{path}.*")


def validate(value: Any, schema: Mapping[str, Any], path: str = "input") -> None:
    """Raise ``invalid_payload`` naming the first path that breaks ``schema``."""
    declared = schema.get("type")
    if declared is not None:
        types = [declared] if isinstance(declared, str) else list(declared)
        if not any(_is_type(value, name) for name in types):
            raise _invalid(path, f"must be {' or '.join(types)}")
    if value is None:
        return
    if "const" in schema and value != schema["const"]:
        raise _invalid(path, f"must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise _invalid(path, "must be one of " + ", ".join(str(item) for item in schema["enum"]))
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            raise _invalid(path, "is too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise _invalid(path, "is too long")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise _invalid(path, f"must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise _invalid(path, f"must be at most {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            raise _invalid(path, f"needs at least {schema.get('minItems')} items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise _invalid(path, f"allows at most {schema['maxItems']} items")
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                validate(item, items, f"{path}[{index}]")
    if isinstance(value, Mapping):
        for name in schema.get("required") or ():
            if name not in value:
                raise _invalid(f"{path}.{name}", "is required")
        properties = schema.get("properties") or {}
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                validate(item, properties[name], f"{path}.{name}")
            elif extra is False:
                raise _invalid(f"{path}.{name}", "is not a supported field")
            elif isinstance(extra, Mapping):
                validate(item, extra, f"{path}.{name}")


def _is_type(value: Any, name: str) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, Mapping)
    return False


def _invalid(path: str, message: str) -> ExtensionError:
    return ExtensionError(errors.INVALID_PAYLOAD, f"{path} {message}")


def timestamp(value: Any, name: str) -> str | None:
    """An ISO 8601 timestamp with an explicit offset, returned unchanged.

    A timestamp without a time zone is ambiguous by hours, which is enough to
    skip or repeat a day of orders, so it is refused rather than assumed UTC.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid(name, "must be an ISO 8601 timestamp")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _invalid(name, "must be an ISO 8601 timestamp such as 2026-11-05T10:00:00Z") from exc
    if parsed.tzinfo is None:
        raise _invalid(name, "must include a time zone, for example Z or +01:00")
    return text


def decimal_string(value: Any, name: str) -> str:
    """A decimal amount as a string; binary floats are refused, not rounded."""
    if isinstance(value, bool) or not isinstance(value, str) or _DECIMAL_RE.fullmatch(value) is None:
        raise _invalid(name, 'must be a decimal string such as "19.99"')
    try:
        Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - the pattern already excludes it
        raise _invalid(name, "must be a decimal string") from exc
    return value


def currency_code(value: Any, name: str) -> str:
    if not isinstance(value, str) or _CURRENCY_RE.fullmatch(value) is None:
        raise _invalid(name, "must be a three-letter ISO 4217 currency code")
    return value


def test_mode(payload: Mapping[str, Any]) -> bool:
    """Whether the host asked for a rehearsal rather than a real invocation."""
    runtime_context = payload.get("runtime_context")
    if not isinstance(runtime_context, Mapping):
        return False
    value = runtime_context.get("test_mode", False)
    if not isinstance(value, bool):
        raise ExtensionError(errors.INVALID_PAYLOAD, "runtime_context.test_mode must be boolean")
    return value


def external_effect_id(payload: Mapping[str, Any], *, action: str) -> str:
    """The host's external-effect id for a mutation.

    Amazon has no idempotency key for these calls, so the id cannot make a
    repeat safe on Amazon's side; it is required so that the platform's effect
    ledger owns the attempt and a repeat after an unknown outcome is a decision,
    not an accident.
    """
    runtime_context = payload.get("runtime_context")
    runtime_context = runtime_context if isinstance(runtime_context, Mapping) else {}
    value = runtime_context.get("external_effect_id")
    text = value.strip() if isinstance(value, str) else ""
    if not text or _EXTERNAL_EFFECT_ID_RE.fullmatch(text) is None:
        raise ExtensionError(
            errors.MISSING_IDEMPOTENCY_KEY,
            f"runtime_context.external_effect_id is required to {action}",
        )
    return text


def string_list(value: Sequence[Any] | None) -> list[str]:
    return [str(item) for item in value or ()]


__all__ = [
    "assert_executable",
    "currency_code",
    "decimal_string",
    "external_effect_id",
    "string_list",
    "test_mode",
    "timestamp",
    "validate",
]
