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

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from runtime.catalog import CATEGORY, CHANNEL, OPERATIONS
from runtime.marketplaces import MARKETPLACE_CODES
from runtime.connection import CONNECTION_TYPE_ID
from runtime.registry import SCOPES, Operation

BUNDLE_ROOT = Path(__file__).resolve().parents[2]

GENERATED_HEADER = (
    "# Generated from the closed runtime registry in runtime/catalog.py.\n"
    "# Extension-owned parity tests fail if this file and the registry disagree.\n"
)

REQUIRED_SCOPES: list[str] = ["extension:invoke"]
ARTIFACT_RETENTION = "run_window"
#: The platform requires this exact ceiling on an output that names max_size_env;
#: the runtime enforces its own, smaller caps before writing.
CANONICAL_ARTIFACT_MAX_BYTES = 209_715_200


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
            "description": described(row),
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


def described(row: Operation) -> str:
    """The method's description with its marketplace/program limits and its data scope."""
    text = row.description
    if row.availability and row.availability not in text:
        text = f"{text} Availability: {row.availability}"
    if row.contacts_amazon and row.scope != "marketplace":
        text = f"{text} Scope: {SCOPES[row.scope]}"
    return text


def _widget(spec: Mapping[str, Any]) -> str:
    declared = spec.get("type")
    types = [declared] if isinstance(declared, str) else list(declared or [])
    if "enum" in spec:
        return "select"
    if "array" in types:
        items = spec.get("items") if isinstance(spec.get("items"), Mapping) else {}
        return "multi_select" if "enum" in items else "json"
    if "object" in types:
        return "json"
    if "boolean" in types:
        return "boolean"
    if "integer" in types:
        return "integer"
    if "number" in types:
        return "number"
    return "text"


#: A placeholder is a hint, not documentation: long JSON examples stay in the schema.
MAX_PLACEHOLDER = 160


def _placeholder(spec: Mapping[str, Any]) -> str:
    examples = spec.get("examples")
    if not isinstance(examples, list) or not examples:
        return ""
    example = examples[0]
    text = example if isinstance(example, str) else json.dumps(example, ensure_ascii=False)
    return text if len(text) <= MAX_PLACEHOLDER else ""


def _help(spec: Mapping[str, Any]) -> str:
    parts = [str(spec.get("description") or "").strip()]
    limits = []
    if "minimum" in spec or "maximum" in spec:
        limits.append(f"from {spec.get('minimum', '…')} to {spec.get('maximum', '…')}")
    if "maxLength" in spec:
        limits.append(f"up to {spec['maxLength']} characters")
    if "maxItems" in spec:
        limits.append(f"up to {spec['maxItems']} entries")
    if limits:
        parts.append("Allowed: " + ", ".join(limits) + ".")
    examples = spec.get("examples")
    if isinstance(examples, list) and examples and not _placeholder(spec):
        parts.append("Example: " + json.dumps(examples[0], ensure_ascii=False))
    return " ".join(part for part in parts if part)


def step_ui_manifest() -> dict[str, Any]:
    forms: list[dict[str, Any]] = []
    for row in OPERATIONS:
        fields = []
        for name, spec in row.properties.items():
            if name == "connection_ref":
                continue
            field: dict[str, Any] = {
                "name": name,
                "label": str(spec.get("title") or name.replace("_", " ").capitalize()),
                "widget_type": _widget(spec),
                "required": name in row.required,
            }
            help_text = _help(spec)
            if help_text:
                field["help_text"] = help_text
            placeholder = _placeholder(spec)
            if placeholder:
                field["placeholder"] = placeholder
            fields.append(field)
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
        if row.artifact_outputs:
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
        if row.artifact_outputs:
            outputs: list[dict[str, Any]] = []
            for binding in row.artifact_outputs:
                item: dict[str, Any] = {
                    "kind": "artifact",
                    "value_type": "artifact_handle",
                    "binding_key": binding.binding_key,
                    "filename": binding.filename,
                    "content_type": binding.content_type,
                    "max_size_bytes": CANONICAL_ARTIFACT_MAX_BYTES,
                    "max_size_env": "FS_EXTENSION_ARTIFACT_MAX_BYTES",
                    "retention_class": ARTIFACT_RETENTION,
                }
                if binding.presentation_claim:
                    # ADR 0045: the real file name and MIME arrive as a claim in
                    # this result field once the document has been read.
                    item["presentation"] = {"claim_output": binding.presentation_claim}
                outputs.append(item)
            entry["outputs"] = outputs
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


def _markets(row: Operation) -> str:
    if not row.contacts_amazon:
        return "n/a"
    if not row.marketplaces:
        return "all 23"
    if len(row.marketplaces) > 12:
        missing = [code for code in MARKETPLACE_CODES if code not in row.marketplaces]
        return "all except " + ", ".join(missing)
    return ", ".join(row.marketplaces)


def _file(row: Operation) -> str:
    if row.artifact_input is not None:
        return "reads a file"
    if row.artifact_outputs:
        return "writes a file"
    return "no"


def _scope_words(row: Operation) -> str:
    return {
        "marketplace": "this connection's marketplace",
        "seller_region": "the seller's data in this connection's region (by Amazon ID)",
        "shipping_business": "the seller's labels in this country's Amazon Shipping business",
    }[row.scope]


def operation_matrix() -> str:
    """The method → Amazon operation → version → roles → markets → effect table."""
    lines = [
        "# Operation matrix",
        "",
        "Generated from `runtime/catalog.py` by `tests/reference/refresh_contracts.py`; "
        "`tests/test_manifest_parity.py` fails when it is stale.",
        "",
        "Every operation runs against one connection, i.e. one seller account in one "
        "marketplace; the marketplace and the Seller ID always come from the connection.",
        "",
        "Scope: `marketplace` = the request names the connection's marketplace; "
        "`seller_region` = addressed by Amazon ID or seller-wide, so Amazon answers for any "
        "marketplace of the seller in the connection's region; `shipping_business` = the "
        "seller's labels in the Amazon Shipping business of the connection's country.",
        "",
        "| Method | Section | Amazon operations | Roles | Marketplaces | Scope | External effect "
        "| Personal data | File |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in OPERATIONS:
        calls = "<br>".join(row.amazon_calls)
        roles = "<br>".join(row.roles)
        effect = f"`{row.effect_kind}`" if row.mutating else "none (read)"
        visibility = "" if row.workflow_visible else " (platform only)"
        lines.append(
            f"| `{row.operation_id}`{visibility} | {row.section} | {calls} | {roles} | "
            f"{_markets(row)} | {row.scope if row.contacts_amazon else 'n/a'} | {effect} | "
            f"{'yes' if row.pii else 'no'} | {_file(row)} |"
        )
    return "\n".join(lines) + "\n"


SECTION_TITLES = {
    "account": "Account and connection",
    "orders": "Orders",
    "shipping": "FBM shipping",
    "catalog_items": "Catalog",
    "listings": "Listings",
    "requirements": "Listing requirements",
    "pricing": "Pricing",
    "inventory": "Inventory",
    "fba_inbound": "FBA inbound shipments",
    "fba_inbound_india": "FBA inbound: India and appointments",
    "feeds": "Feeds",
    "finances": "Finances",
    "reports": "Reports",
}


def matrix_page() -> dict[str, Any]:
    """The Operation matrix UI page: one list per section (the page renderer draws no tables)."""
    components: list[dict[str, Any]] = [
        {
            "component_id": "intro",
            "type": "markdown",
            "title": "How to read this page",
            "body": (
                "Every method is one Amazon call on one connection (one seller account in one "
                "marketplace). Scope says what the call can reach: searches and creations name "
                "the connection's marketplace; a method that addresses an order, plan, shipment, "
                "feed or report by its Amazon ID reaches it in any marketplace of the same seller "
                "in the connection's region, because Amazon's IDs are region-wide. Per method: the "
                "Amazon operation and API version, the roles the "
                "application needs, where it works, whether it changes something on Amazon (an "
                "external effect, suppressed in test runs), whether its result holds personal "
                "data, and whether it reads or writes a file.\n"
            ),
        }
    ]
    for section, title in SECTION_TITLES.items():
        rows = [row for row in OPERATIONS if row.section == section and row.workflow_visible]
        if not rows:
            continue
        lines = []
        for row in rows:
            effect = f"Changes Amazon ({row.effect_kind})" if row.mutating else "Read only"
            lines.append(
                f"- **{row.display_name}** (`{row.operation_id}`): "
                f"{'; '.join(row.amazon_calls)}. Roles: {', '.join(row.roles)}. "
                f"Marketplaces: {_markets(row)}. Scope: {_scope_words(row)}. {effect}. "
                f"Personal data: {'yes' if row.pii else 'no'}. File: {_file(row)}."
            )
        components.append(
            {
                "component_id": f"matrix_{section}",
                "type": "markdown",
                "title": title,
                "body": "\n".join(lines) + "\n",
            }
        )
    return {
        "page_id": "operation-matrix",
        "title": "Operation matrix",
        "description": "Every method with its Amazon operation, roles, marketplaces, effect, personal data and files.",
        "components": components,
    }


def dump(payload: Any) -> str:
    return GENERATED_HEADER + yaml.safe_dump(
        payload, sort_keys=False, default_flow_style=False, allow_unicode=True, width=4096
    )


GENERATED_FILES: dict[str, Any] = {
    "contracts/operation_manifest.yaml": operation_manifest,
    "contracts/step_ui_manifest.yaml": step_ui_manifest,
    "contracts/artifact_policies.yaml": artifact_policies,
    "ui/actions/actions.yaml": action_manifest,
    "ui/pages/operation-matrix.yaml": matrix_page,
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
