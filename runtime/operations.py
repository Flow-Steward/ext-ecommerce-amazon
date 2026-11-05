"""Run one operation: validate, rehearse or hydrate, call the handler, shape the result.

Order of checks for every invocation:

1. The input is validated against the operation's own schema.
2. A mutation in test mode returns a neutral suppressed result here, before a
   connection is hydrated or a transport exists, so a rehearsal cannot reach
   Amazon even by accident.
3. A real mutation needs the host's external-effect id.
4. The connection is hydrated and must be the one ``connection_ref`` names.
5. The handler runs; its result is made JSON-safe (decimals become strings).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from flowsteward_extension_sdk import read_artifact_bytes, write_artifact_bytes

from . import errors, validation
from .catalog import OPERATIONS_BY_ID
from .connection import connection_from_payload
from .errors import ExtensionError, error_response
from .registry import Context, Operation
from .transport import SpApiTransport
from .values import json_safe

_UNSUPPORTED = "That operation is not part of this extension"


def handle_runtime(
    payload: Mapping[str, Any],
    *,
    transport_factory: Callable[..., SpApiTransport] = SpApiTransport,
    artifact_reader: Callable[..., bytes] = read_artifact_bytes,
    artifact_writer: Callable[..., Mapping[str, Any]] = write_artifact_bytes,
) -> dict[str, Any]:
    try:
        operation_id, raw_input = _envelope(payload)
        row = OPERATIONS_BY_ID.get(operation_id)
        if row is None:
            raise ExtensionError(errors.UNSUPPORTED_OPERATION, _UNSUPPORTED)
        return run_operation(
            payload,
            row,
            raw_input,
            transport_factory=transport_factory,
            artifact_reader=artifact_reader,
            artifact_writer=artifact_writer,
        )
    except ExtensionError as exc:
        response = error_response(
            exc.code, exc.message, retry_facts=exc.retry_facts(), amazon_errors=exc.amazon_errors
        )
        return response
    except Exception:  # pragma: no cover - last line of defence, never echoes detail
        return error_response(errors.INTERNAL_ERROR, "The Amazon extension failed unexpectedly")


def run_operation(
    payload: Mapping[str, Any],
    row: Operation,
    raw_input: Any,
    *,
    transport_factory: Callable[..., SpApiTransport],
    artifact_reader: Callable[..., bytes],
    artifact_writer: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(raw_input, Mapping):
        raise ExtensionError(errors.INVALID_PAYLOAD, "action.input must be an object")
    operation_input = dict(raw_input)
    validation.validate(operation_input, row.input_schema)
    effect_id = ""
    if row.mutating:
        if validation.test_mode(payload):
            return _ok(_suppressed_result(row))
        effect_id = validation.external_effect_id(payload, action=row.display_name.lower())
    connection = connection_from_payload(payload, connection_ref=_connection_ref(payload, row, operation_input))
    context = Context(
        operation=row,
        input=operation_input,
        connection=connection,
        transport=transport_factory(connection),
        payload=payload,
        effect_id=effect_id,
        artifact_reader=artifact_reader,
        artifact_writer=artifact_writer,
    )
    result = json_safe(row.handler(context))
    if row.mutating:
        result.setdefault("external_effect_status", "succeeded")
        result.setdefault("definitely_no_external_effect", False)
    return _ok(result)


def _connection_ref(payload: Mapping[str, Any], row: Operation, operation_input: Mapping[str, Any]) -> str:
    """The connection a row runs against.

    Workflow-visible rows name it in ``connection_ref``. The parameterless
    post-connect check has no input and runs against the connection the host
    hydrated for it, which is the only one it can see.
    """
    if "connection_ref" in row.properties:
        return str(operation_input["connection_ref"])
    action = payload.get("action")
    target = action.get("target") if isinstance(action, Mapping) else None
    value = target.get("connection_id") if isinstance(target, Mapping) else None
    return value if isinstance(value, str) else ""


def _suppressed_result(row: Operation) -> dict[str, Any]:
    result: dict[str, Any] = {output.name: None for output in row.outputs}
    result["external_effect_status"] = "suppressed"
    result["definitely_no_external_effect"] = True
    return result


def _envelope(payload: Mapping[str, Any]) -> tuple[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("mode") != "action":
        raise ExtensionError(errors.UNSUPPORTED_OPERATION, _UNSUPPORTED)
    action = payload.get("action")
    if not isinstance(action, Mapping):
        raise ExtensionError(errors.INVALID_PAYLOAD, "action must be an object")
    value = action.get("action_id") or action.get("operation_id")
    operation_id = value.strip() if isinstance(value, str) else ""
    raw_input = action.get("input")
    if raw_input is None:
        raw_input = action.get("parameters", {})
    return operation_id, raw_input


def _ok(result: Mapping[str, Any]) -> dict[str, Any]:
    return {"ok": True, "result": dict(result), "errors": []}


__all__ = ["handle_runtime", "run_operation"]
