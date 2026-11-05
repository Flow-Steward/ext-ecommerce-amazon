"""Generate every published contract from the closed runtime registry.

``runtime/catalog.py`` is the single source of truth. The operation manifest, the
step UI manifest, the action manifest, the artifact policy file, the external-effect
rows in ``extension.yaml`` and the operation matrix in ``docs/operation-matrix.md``
are derived here, and ``tests/test_manifest_parity.py`` fails if a shipped file and
this generator disagree.

Run ``python3 tests/reference/refresh_contracts.py`` from the bundle root after
changing the registry.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from runtime.catalog import CATEGORY, CHANNEL, OPERATIONS
from runtime.connection import CONNECTION_TYPE_ID
from runtime.registry import Operation

BUNDLE_ROOT = Path(__file__).resolve().parents[2]

GENERATED_HEADER = (
    "# Generated from the closed runtime registry in runtime/catalog.py.\n"
    "# Extension-owned parity tests fail if this file and the registry disagree.\n"
)

REQUIRED_SCOPES: list[str] = ["extension:invoke"]
ARTIFACT_RETENTION = "run_window"


def _schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _schema(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_schema(item) for item in value]
    return value


def _value_type(name: str, spec: Mapping[str, Any]) -> str:
    if name == "connection_ref":
        return "connection_ref"
    declared = spec.get("type")
    types = [declared] if isinstance(declared, str) else list(declared or [])
    for candidate, value_type in (
        ("object", "object"),
        ("array", "array"),
        ("boolean", "boolean"),
        ("integer", "integer"),
        ("number", "number"),
    ):
        if candidate in types:
            return value_type
    return "text"


def _inputs(row: Operation) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, spec in row.properties.items():
        entry: dict[str, Any] = {
            "name": name,
            "value_type": _value_type(name, spec),
            "required": name in row.required,
        }
        if name != "connection_ref":
            entry["schema"] = _schema(spec)
        rows.append(entry)
    return rows


def _outputs(row: Operation) -> list[dict[str, Any]]:
    return [
        {"name": output.name, "value_type": output.value_type, "schema": _schema(output.schema)}
        for output in row.outputs
    ]


def connection_type_for(row: Operation) -> str:
    """Every row that contacts Amazon runs against one connection."""
    return CONNECTION_TYPE_ID if row.contacts_amazon else ""


def operation_manifest() -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    for row in OPERATIONS:
        entry: dict[str, Any] = {
            "operation_id": row.operation_id,
            "display_name": row.display_name,
            "description": row.description,
            "operation_kind": "action" if row.mutating else row.kind,
            "category": CATEGORY,
        }
        connection_type = connection_type_for(row)
        if connection_type:
            entry["connection_type_ids"] = [connection_type]
        entry["workflow_visible"] = row.workflow_visible
        entry["required_oauth_scopes"] = []
        if row.effect_kind:
            entry["effect_kind"] = row.effect_kind
        entry["inputs"] = _inputs(row)
        entry["outputs"] = _outputs(row)
        entry["result_fields"] = [f"result.{name}" for name in row.result_fields]
        entry["error_codes"] = list(row.error_codes)
        operations.append(entry)
    return {"operations": operations}


def _widget(spec: Mapping[str, Any]) -> str:
    declared = spec.get("type")
    types = [declared] if isinstance(declared, str) else list(declared or [])
    if "array" in types or "object" in types:
        return "json"
    if "boolean" in types:
        return "boolean"
    if "integer" in types or "number" in types:
        return "number"
    return "text"


def step_ui_manifest() -> dict[str, Any]:
    forms: list[dict[str, Any]] = []
    for row in OPERATIONS:
        fields = [
            {
                "name": name,
                "label": str(spec.get("title") or name.replace("_", " ").capitalize()),
                "widget_type": _widget(spec),
                "required": name in row.required,
            }
            for name, spec in row.properties.items()
            if name != "connection_ref"
        ]
        forms.append(
            {"operation_id": row.operation_id, "title": row.display_name, "fields": fields}
        )
    return {"forms": forms}


def _parameter_type(value_type: str) -> str:
    return {"text": "string"}.get(value_type, value_type)


def action_manifest() -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for row in OPERATIONS:
        required_scopes = list(REQUIRED_SCOPES)
        if row.artifact_input is not None:
            required_scopes.append("artifact:read")
        if row.artifact_output is not None:
            required_scopes.append("artifact:write")
        entry: dict[str, Any] = {
            "action_id": row.operation_id,
            "title": row.display_name,
            "description": row.description,
            "required_scopes": required_scopes,
            "handler": {"mode": "extension"},
        }
        if row.mutating:
            entry["command_type"] = "invoke_extension_action"
        entry["mutates_platform"] = row.mutating
        entry["workflow_visible"] = row.workflow_visible
        entry["parameters"] = [
            {
                "name": item["name"],
                "type": _parameter_type(item["value_type"]),
                "required": item["required"],
            }
            for item in _inputs(row)
        ]
        entry["result_fields"] = list(row.result_fields)
        entry["error_codes"] = list(row.error_codes)
        actions.append(entry)
    return {"actions": actions}


def artifact_policies() -> dict[str, Any]:
    policies: list[dict[str, Any]] = []
    for row in OPERATIONS:
        entry: dict[str, Any] = {"operation_id": row.operation_id}
        if row.artifact_input is not None:
            binding = row.artifact_input
            entry["inputs"] = [
                {
                    "kind": "artifact",
                    "value_type": "artifact_handle",
                    "binding_key": binding.binding_key,
                    "field": binding.field,
                    "required": binding.required,
                    "max_size_bytes": binding.max_size_bytes,
                }
            ]
        if row.artifact_output is not None:
            binding = row.artifact_output
            entry["outputs"] = [
                {
                    "kind": "artifact",
                    "value_type": "artifact_handle",
                    "binding_key": binding.binding_key,
                    "filename": binding.filename,
                    "content_type": binding.content_type,
                    "max_size_bytes": binding.max_size_bytes,
                    "max_size_env": "FS_EXTENSION_ARTIFACT_MAX_BYTES",
                    "retention_class": ARTIFACT_RETENTION,
                }
            ]
        if len(entry) > 1:
            policies.append(entry)
    return {"artifact_policies": policies}


def external_effects() -> list[dict[str, Any]]:
    return [
        {
            "operation_id": row.operation_id,
            "effect_kind": row.effect_kind,
            "channel": CHANNEL,
            "idempotency": "required",
            "test_mode_behavior": "suppress",
        }
        for row in OPERATIONS
        if row.mutating
    ]


EXTERNAL_EFFECTS_KEY = "external_effects:\n"


def external_effects_block() -> str:
    effects = external_effects()
    if not effects:
        return "external_effects: []\n"
    return EXTERNAL_EFFECTS_KEY + "".join(
        "  - {" + ", ".join(f"{key}: {value}" for key, value in effect.items()) + "}\n"
        for effect in effects
    )


def manifest_with_external_effects(current: str) -> str:
    start = current.index("external_effects:")
    end = current.index("runtime:\n", start)
    return current[:start] + external_effects_block() + current[end:]


def artifact_policies_block() -> str:
    """The top-level ``artifact_policies`` block of ``extension.yaml``."""
    policies = artifact_policies()["artifact_policies"]
    if not policies:
        return "artifact_policies: []\n"
    return yaml.safe_dump(
        {"artifact_policies": policies}, sort_keys=False, default_flow_style=None, width=4096
    )


def manifest_with_artifact_policies(current: str) -> str:
    start = current.index("\nartifact_policies:") + 1
    end = current.index("entrypoint:", start)
    return current[:start] + artifact_policies_block() + current[end:]


def operation_matrix() -> str:
    """The method → Amazon operation → version → roles → effect table."""
    lines = [
        "# Operation matrix",
        "",
        "Generated from `runtime/catalog.py` by `tests/reference/refresh_contracts.py`; "
        "`tests/test_manifest_parity.py` fails when it is stale.",
        "",
        "Every operation runs against one connection, i.e. one seller account in one "
        "marketplace; the marketplace and the Seller ID always come from the connection.",
        "",
        "| Method | Section | Amazon operations | Roles | External effect | Personal data |",
        "|---|---|---|---|---|---|",
    ]
    for row in OPERATIONS:
        calls = "<br>".join(row.amazon_calls)
        roles = "<br>".join(row.roles)
        effect = f"`{row.effect_kind}`" if row.mutating else "none (read)"
        visibility = "" if row.workflow_visible else " (platform only)"
        lines.append(
            f"| `{row.operation_id}`{visibility} | {row.section} | {calls} | {roles} | {effect} | "
            f"{'yes' if row.pii else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def dump(payload: Any) -> str:
    return GENERATED_HEADER + yaml.safe_dump(
        payload, sort_keys=False, default_flow_style=False, allow_unicode=True, width=4096
    )


GENERATED_FILES: dict[str, Any] = {
    "contracts/operation_manifest.yaml": operation_manifest,
    "contracts/step_ui_manifest.yaml": step_ui_manifest,
    "contracts/artifact_policies.yaml": artifact_policies,
    "ui/actions/actions.yaml": action_manifest,
}

GENERATED_TEXT: dict[str, Any] = {"docs/operation-matrix.md": operation_matrix}

__all__ = [
    "BUNDLE_ROOT",
    "GENERATED_FILES",
    "GENERATED_HEADER",
    "GENERATED_TEXT",
    "action_manifest",
    "artifact_policies",
    "dump",
    "external_effects",
    "manifest_with_artifact_policies",
    "manifest_with_external_effects",
    "operation_manifest",
    "operation_matrix",
    "step_ui_manifest",
]
