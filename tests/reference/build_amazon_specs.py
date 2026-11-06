"""Compile Amazon's official OpenAPI models into ``runtime/amazon_specs.py``.

The logistics and listing-maintenance methods are thin, one-call wrappers. Their
request and response shapes are taken from Amazon's own models
(``tests/reference/models``, copied from github.com/amzn/selling-partner-api-models)
instead of being retyped, so a field name, an enum, a length limit or a required
flag cannot drift from what Amazon documents.

For every operation named in ``SPEC_OPERATIONS`` the compiler writes:

* the HTTP method, the path template and the success status;
* every path, query and header parameter as a snake_case JSON schema;
* the request body as one snake_case JSON schema (``additionalProperties: false``
  wherever Amazon names the properties);
* the success response as one snake_case JSON schema.

``$ref`` is inlined (the models have no cycles; the compiler refuses one), and
only the keywords the platform's validator executes are kept. ``pattern`` is not
executable there, so it is written into the description instead and left to
Amazon to enforce. Enum value tables become part of the description, so the form
shows what each value means. Each object with named properties also carries
``x-amazon-names`` (snake_case name -> Amazon's name): the runtime converts keys
through that map, never by guessing, and strips every ``x-`` key before a schema
is published.

Run ``python3 tests/reference/refresh_contracts.py`` from the bundle root after
changing ``SPEC_OPERATIONS`` or a model; ``tests/test_manifest_parity.py`` fails
while ``runtime/amazon_specs.py`` is stale. The module holds the compiled JSON as
one string (extension bundles carry no JSON data files).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

BUNDLE_ROOT = Path(__file__).resolve().parents[2]
MODELS = BUNDLE_ROOT / "tests" / "reference" / "models"
SPECS_PATH = "runtime/amazon_specs.py"

#: model file -> (spec namespace, API version label, operations used).
SPEC_OPERATIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "shippingV2.json": (
        "shipping_v2",
        "v2",
        (
            "getRates",
            "getAdditionalInputs",
            "purchaseShipment",
            "getShipmentDocuments",
            "getTracking",
            "cancelShipment",
        ),
    ),
    "fulfillmentInbound_2024-03-20.json": (
        "inbound_2024_03_20",
        "2024-03-20",
        (
            "listPrepDetails",
            "setPrepDetails",
            "createMarketplaceItemLabels",
            "createInboundPlan",
            "listInboundPlans",
            "getInboundPlan",
            "cancelInboundPlan",
            "getInboundOperationStatus",
            "listInboundPlanItems",
            "listInboundPlanBoxes",
            "listInboundPlanPallets",
            "generatePackingOptions",
            "listPackingOptions",
            "confirmPackingOption",
            "listPackingGroupItems",
            "listPackingGroupBoxes",
            "setPackingInformation",
            "generatePlacementOptions",
            "listPlacementOptions",
            "confirmPlacementOption",
            "generateTransportationOptions",
            "listTransportationOptions",
            "confirmTransportationOptions",
            "generateDeliveryWindowOptions",
            "listDeliveryWindowOptions",
            "confirmDeliveryWindowOptions",
            "getShipment",
            "listShipmentItems",
            "listShipmentBoxes",
            "listShipmentPallets",
            "updateShipmentName",
            "updateShipmentSourceAddress",
            "updateShipmentTrackingDetails",
            "updateInboundPlanName",
            "listItemComplianceDetails",
            "updateItemComplianceDetails",
            "generateSelfShipAppointmentSlots",
            "getSelfShipAppointmentSlots",
            "scheduleSelfShipAppointment",
            "cancelSelfShipAppointment",
            "getDeliveryChallanDocument",
        ),
    ),
    "fulfillmentInboundV0.json": ("inbound_v0", "v0", ("getLabels", "getBillOfLading")),
    "fbaInboundEligibility.json": ("inbound_eligibility_v1", "v1", ("getItemEligibilityPreview",)),
    "listingsItems_2021-08-01.json": ("listings_2021_08_01", "2021-08-01", ("deleteListingsItem",)),
    "feeds_2021-06-30.json": ("feeds_2021_06_30", "2021-06-30", ("cancelFeed",)),
    "reports_2021-06-30.json": ("reports_2021_06_30", "2021-06-30", ("cancelReport",)),
}

_SCALAR_KEYS = (
    "type",
    "enum",
    "format",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
)
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_MAX_DESCRIPTION = 1200


def snake(name: str) -> str:
    """``shipmentId`` -> ``shipment_id``, ``DownloadURL`` -> ``download_url``,
    ``supportedDPIs`` -> ``supported_dpis``."""
    name = re.sub(r"([A-Z]{2,})s(?=[A-Z]|$)", lambda match: match.group(1).capitalize() + "s", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", spaced)
    return spaced.replace("-", "_").lower()


def _names(raw: dict[str, Any], where: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for name in raw:
        converted = snake(name)
        if converted in names:
            raise ValueError(f"{where}: {name!r} and {names[converted]!r} collide as {converted!r}")
        names[converted] = name
    return names


def _snake_value(value: Any) -> Any:
    """An Amazon example with its object keys in snake_case."""
    if isinstance(value, dict):
        return {snake(str(key)): _snake_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_snake_value(item) for item in value]
    return value


def _description(schema: dict[str, Any], inherited: str = "") -> str:
    parts = [str(schema.get("description") or inherited or "").strip()]
    table = schema.get("x-docgen-enum-table-extension")
    if isinstance(table, list) and table:
        values = "; ".join(
            f"{row.get('value')}: {str(row.get('description') or '').strip().rstrip('.')}"
            for row in table
            if isinstance(row, dict)
        )
        parts.append(f"Values: {values}.")
    if schema.get("pattern"):
        parts.append(f"Format (checked by Amazon): {schema['pattern']}")
    text = " ".join(part for part in parts if part)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _MAX_DESCRIPTION:
        text = text[: _MAX_DESCRIPTION - 1].rstrip() + "…"
    return text


class Compiler:
    def __init__(self, model: dict[str, Any], label: str) -> None:
        self.model = model
        self.label = label
        self.definitions = model.get("definitions") or {}

    def schema(self, raw: Any, *, where: str, stack: tuple[str, ...] = ()) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        if "$ref" in raw:
            name = str(raw["$ref"]).rsplit("/", 1)[-1]
            if name in stack:
                raise ValueError(f"{where}: recursive definition {name}")
            resolved = self.schema(
                self.definitions[name], where=f"{where}->{name}", stack=(*stack, name)
            )
            own = _description(raw)
            if own:
                resolved["description"] = own
            return resolved
        if "allOf" in raw or "oneOf" in raw or "anyOf" in raw:
            raise ValueError(f"{where}: composition keywords are not supported")
        compiled: dict[str, Any] = {}
        for key in _SCALAR_KEYS:
            if key in raw:
                compiled[key] = copy.deepcopy(raw[key])
        if "properties" in raw and "type" not in compiled:
            compiled["type"] = "object"
        description = _description(raw)
        if description:
            compiled["description"] = description
        if "example" in raw:
            compiled["examples"] = [_snake_value(raw["example"])]
        if isinstance(raw.get("properties"), dict):
            names = _names(raw["properties"], where)
            compiled["properties"] = {
                converted: self.schema(
                    raw["properties"][name], where=f"{where}.{name}", stack=stack
                )
                for converted, name in names.items()
            }
            compiled["required"] = [snake(name) for name in raw.get("required") or ()]
            compiled["x-amazon-names"] = names
            additional = raw.get("additionalProperties", False)
            compiled["additionalProperties"] = (
                self.schema(additional, where=f"{where}.*", stack=stack)
                if isinstance(additional, dict)
                else bool(additional)
            )
        elif compiled.get("type") == "object" and "additionalProperties" in raw:
            additional = raw["additionalProperties"]
            if isinstance(additional, dict):
                compiled["additionalProperties"] = self.schema(
                    additional, where=f"{where}.*", stack=stack
                )
        if "items" in raw:
            compiled["items"] = self.schema(raw["items"], where=f"{where}[]", stack=stack)
        return compiled

    def operation(self, operation_id: str) -> dict[str, Any]:
        for path, methods in (self.model.get("paths") or {}).items():
            shared = methods.get("parameters") or []
            for method in _HTTP_METHODS:
                row = methods.get(method)
                if isinstance(row, dict) and row.get("operationId") == operation_id:
                    return self._compile(path, method, row, shared)
        raise ValueError(f"{self.label}: operation {operation_id} not found")

    def _compile(
        self, path: str, method: str, row: dict[str, Any], shared: list[Any]
    ) -> dict[str, Any]:
        where = f"{self.label}.{row['operationId']}"
        parameters: list[dict[str, Any]] = []
        body: dict[str, Any] | None = None
        body_required = False
        for raw in [*shared, *(row.get("parameters") or [])]:
            if "$ref" in raw:
                raw = self.model["parameters"][str(raw["$ref"]).rsplit("/", 1)[-1]]
            location = raw.get("in")
            if location == "body":
                body = self.schema(raw.get("schema"), where=f"{where}.body")
                body_required = bool(raw.get("required"))
                continue
            parameter_schema = self.schema(
                {key: value for key, value in raw.items() if key not in {"in", "name", "required"}},
                where=f"{where}.{raw['name']}",
            )
            if location == "query" and parameter_schema.get("type") == "array":
                parameter_schema.setdefault("minItems", 1)
            row_out = {
                "name": raw["name"],
                "input": snake(raw["name"]),
                "in": location,
                "required": bool(raw.get("required")),
                "schema": parameter_schema,
            }
            if location == "query" and raw.get("collectionFormat") == "multi":
                # ``multi``: one ``name=value`` pair per item. Otherwise Amazon
                # takes a list as one comma-separated value (``csv``).
                row_out["repeated"] = True
            parameters.append(row_out)
        success = sorted(
            (status, item)
            for status, item in (row.get("responses") or {}).items()
            if str(status).startswith("2")
        )
        if not success:
            raise ValueError(f"{where}: no success response")
        status, response = success[0]
        response_schema = self.schema(response.get("schema"), where=f"{where}.response")
        return {
            "amazon_operation": row["operationId"],
            "method": method.upper(),
            "path": path,
            "success_status": int(status),
            "parameters": parameters,
            "body": body,
            "body_required": body_required,
            "response": response_schema,
        }


def compile_specs() -> dict[str, Any]:
    specs: dict[str, Any] = {}
    for filename, (namespace, version, operations) in SPEC_OPERATIONS.items():
        model = json.loads((MODELS / filename).read_text(encoding="utf-8"))
        compiler = Compiler(model, namespace)
        specs[namespace] = {
            "api_version": version,
            "source_model": filename,
            "operations": {name: compiler.operation(name) for name in operations},
        }
    return specs


_MODULE_HEADER = '''# ruff: noqa: E501
"""Generated by tests/reference/build_amazon_specs.py from Amazon's official models.

Do not edit: run ``python3 tests/reference/refresh_contracts.py``. The parity test
fails while this file differs from what the models compile to.
"""

from __future__ import annotations

import json
from typing import Any

SPECS: dict[str, Any] = json.loads(
    r"""
'''

_MODULE_FOOTER = '''"""
)

__all__ = ["SPECS"]
'''


def render_specs() -> str:
    data = json.dumps(compile_specs(), indent=1, sort_keys=True, ensure_ascii=True)
    if '"""' in data:
        raise ValueError("compiled specs contain a triple quote")
    return _MODULE_HEADER + data + "\n" + _MODULE_FOOTER


__all__ = ["SPECS_PATH", "SPEC_OPERATIONS", "compile_specs", "render_specs", "snake"]
