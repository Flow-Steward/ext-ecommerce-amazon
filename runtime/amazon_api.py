"""One Amazon operation, one method: methods built from Amazon's compiled specs.

``runtime/amazon_specs.py`` holds the request and response shapes of the
logistics and listing-maintenance operations, compiled from Amazon's official
models. This module turns one spec into one ``Operation``:

* **Inputs** are the operation's path and query parameters and the properties of
  its request body, side by side, in snake_case, with Amazon's own types, enums,
  limits and required flags. Parameters that identify the seller or the
  marketplace (``marketplaceId``, ``marketplaceIds``, ``destinationMarketplaces``,
  ``sellerId``) and the two technical headers are never inputs: they come from
  the connection or the platform, so a workflow cannot point a call at another
  seller or store.
* **Outputs** are the top-level properties of Amazon's response (unwrapped from
  ``payload`` where Amazon wraps it), in snake_case, plus ``amazon_operation``,
  ``api_version`` and ``amazon_request_id``. A list returns one page and a
  ``next_token`` bound to this connection and these filters.
* **Keys** are converted through each object's ``x-amazon-names`` map. Values in
  an object Amazon leaves open (for example the additional inputs of a shipping
  rate, whose keys Amazon's own schema names) are passed through unchanged.

Every method makes exactly one Amazon call. Nothing here loops over pages,
waits for an asynchronous operation, picks an option or chains a second step.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import errors, pagination
from .amazon_specs import SPECS
from .errors import ExtensionError
from .registry import (
    CONNECTION_REF,
    NEXT_TOKEN,
    TEXT,
    ArtifactBinding,
    Context,
    Operation,
    Output,
    out,
)
from .transport import ApiResponse, Repeated

#: Amazon parameter / body names filled from the connection, never from input.
FROM_CONNECTION = {
    "marketplaceId": "marketplace_id",
    "MarketplaceId": "marketplace_id",
    "marketplaceIds": "marketplace_ids",
    "destinationMarketplaces": "marketplace_ids",
    "sellerId": "seller_id",
}
#: Headers the runtime sets itself.
PLATFORM_HEADERS = {"x-amzn-shipping-business-id", "x-amzn-IdempotencyKey"}
PAGE_TOKEN_NAMES = {"paginationToken", "nextToken", "NextToken"}

_PII_KEYS = frozenset({"address_line1", "email", "phone_number", "phone"})


def spec(namespace: str, amazon_operation: str) -> dict[str, Any]:
    return SPECS[namespace]["operations"][amazon_operation]


def api_version(namespace: str) -> str:
    return str(SPECS[namespace]["api_version"])


# -- Schemas -----------------------------------------------------------------


def public_schema(schema: Any) -> Any:
    """``schema`` without the runtime-only ``x-`` keys."""
    if isinstance(schema, Mapping):
        return {
            key: public_schema(value)
            for key, value in schema.items()
            if not str(key).startswith("x-") and key != "examples"
        } | ({"examples": copy.deepcopy(schema["examples"])} if "examples" in schema else {})
    if isinstance(schema, list):
        return [public_schema(item) for item in schema]
    return schema


def output_schema(schema: Any) -> Any:
    """An output schema: open objects, decimal strings for numbers, nullable at the top.

    Amazon adds response fields over time, so an output object stays open (a new
    field must not fail a run); numbers with a fraction arrive as exact decimal
    strings, the way every amount of this extension does. Field-level ``required``
    is kept: it is what Amazon promises to send.
    """
    converted = _output(public_schema(schema))
    return _nullable(converted)


def _output(schema: Any) -> Any:
    """Describe what Amazon sends without rejecting what it may add.

    Limits, ``additionalProperties`` and enums are dropped (Amazon adds event codes
    and reason codes over time; the values stay in the description), and every
    type admits null, because Amazon sends ``null`` for absent nested values.
    """
    if not isinstance(schema, Mapping):
        return schema
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {"minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"}:
            continue
        if key in {"additionalProperties", "enum", "examples"}:
            continue
        if key == "properties":
            result[key] = {name: _output(child) for name, child in value.items()}
        elif key == "items":
            result[key] = _output(value)
        elif key == "type":
            types = [value] if isinstance(value, str) else list(value)
            if "number" in types:
                types.append("string")
            result[key] = list(dict.fromkeys([*types, "null"]))
        else:
            result[key] = copy.deepcopy(value)
    return result


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    declared = schema.get("type")
    if isinstance(declared, str):
        schema["type"] = [declared, "null"]
    elif isinstance(declared, list) and "null" not in declared:
        schema["type"] = [*declared, "null"]
    if "enum" in schema and None not in schema["enum"]:
        schema["enum"] = [*schema["enum"], None]
    return schema


def contains_personal_data(schema: Any) -> bool:
    """Whether a response shape carries an address or contact details."""
    if isinstance(schema, Mapping):
        properties = schema.get("properties")
        if isinstance(properties, Mapping) and _PII_KEYS & set(properties):
            return True
        return any(contains_personal_data(value) for value in schema.values())
    if isinstance(schema, list):
        return any(contains_personal_data(item) for item in schema)
    return False


# -- Key conversion -----------------------------------------------------------


def to_amazon(value: Any, schema: Any) -> Any:
    """``value`` with every declared snake_case key renamed to Amazon's name."""
    if isinstance(value, Mapping) and isinstance(schema, Mapping):
        names = schema.get("x-amazon-names") or {}
        properties = schema.get("properties") or {}
        extra = schema.get("additionalProperties")
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if key in names:
                converted[names[key]] = to_amazon(item, properties.get(key))
            else:
                # Only an open object reaches here (validation refused anything
                # else): its keys are the caller's own, e.g. Amazon's additional
                # input names, and stay exactly as given.
                converted[key] = to_amazon(item, extra if isinstance(extra, Mapping) else None)
        return converted
    if isinstance(value, list) and isinstance(schema, Mapping):
        return [to_amazon(item, schema.get("items")) for item in value]
    return value


def from_amazon(value: Any, schema: Any) -> Any:
    """``value`` with Amazon's keys in snake_case, through the declared map.

    A key Amazon added after the model was compiled is converted the same way the
    compiler converts names. An object Amazon leaves open (no declared
    properties) is passed through unchanged: its keys are data, not field names.
    """
    if isinstance(value, Mapping):
        if not isinstance(schema, Mapping) or not schema.get("properties"):
            return copy.deepcopy(dict(value))
        names = {amazon: snake for snake, amazon in (schema.get("x-amazon-names") or {}).items()}
        properties = schema.get("properties") or {}
        converted: dict[str, Any] = {}
        for key, item in value.items():
            name = names.get(key) or _snake(str(key))
            converted[name] = from_amazon(item, properties.get(name))
        return converted
    if isinstance(value, list):
        items = schema.get("items") if isinstance(schema, Mapping) else None
        return [from_amazon(item, items) for item in value]
    return value


def _snake(name: str) -> str:
    name = re.sub(r"([A-Z]{2,})s(?=[A-Z]|$)", lambda match: match.group(1).capitalize() + "s", name)
    out_chars: list[str] = []
    for index, char in enumerate(name):
        if (
            char.isupper()
            and index
            and (
                not name[index - 1].isupper()
                or (index + 1 < len(name) and name[index + 1].islower())
            )
        ):
            out_chars.append("_")
        out_chars.append(char.lower())
    return "".join(out_chars).replace("-", "_")


# -- Method definition --------------------------------------------------------


@dataclass(frozen=True)
class Method:
    """The hand-written part of one method; everything else comes from the spec."""

    operation_id: str
    display_name: str
    description: str
    section: str
    namespace: str
    amazon_operation: str
    roles: tuple[str, ...]
    mutating: bool = False
    effect_kind: str | None = None
    #: Input name -> schema keys to merge (title, description, examples, limits).
    inputs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Inputs the method adds that Amazon does not take directly (the handler's
    #: ``prepare`` turns them into request parts), as name -> full schema.
    extra_inputs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Spec input name -> the name this method uses, where Amazon's name would be
    #: ambiguous (Inbound v0 calls a shipment confirmation ID ``shipmentId``).
    renamed: Mapping[str, str] = field(default_factory=dict)
    #: Inputs the spec marks optional that this method requires.
    required: tuple[str, ...] = ()
    #: Inputs left out of the method entirely.
    hidden: tuple[str, ...] = ()
    marketplaces: tuple[str, ...] = ()
    availability: str = ""
    read_back: tuple[str, ...] = ()
    pii: bool | None = None
    #: Builds the result from the converted response; defaults to the top-level fields.
    result: Callable[[Context, dict[str, Any], ApiResponse], dict[str, Any]] | None = None
    #: Replaces the declared outputs (name, schema) when ``result`` shapes its own.
    outputs: tuple[Output, ...] | None = None
    #: Extra checks or derived request parts before the call.
    prepare: Callable[[Context, dict[str, Any]], None] | None = None
    #: Builds request headers (Amazon Shipping business, idempotency key).
    headers: Callable[[Context], dict[str, str]] | None = None
    artifact_output: ArtifactBinding | None = None
    extra_artifact_outputs: tuple[ArtifactBinding, ...] = ()
    extra_error_codes: tuple[str, ...] = ()
    #: Output fields that hold free-form Amazon objects (e.g. a JSON schema).
    verbatim_outputs: tuple[str, ...] = ()
    #: Response fields that never become outputs (e.g. inline document bytes).
    dropped_outputs: tuple[str, ...] = ()
    #: Shown with the result of an asynchronous or deferred change.
    note: str = ""
    #: ``registry.SCOPES`` key; empty derives it from the request (a marketplace
    #: field from the connection means "marketplace", none means "seller_region").
    scope: str = ""


@dataclass(frozen=True)
class _Plan:
    """The resolved request layout of one method."""

    path_inputs: tuple[tuple[str, str], ...]
    query_inputs: tuple[tuple[str, str, dict[str, Any]], ...]
    repeated_query: frozenset[str]
    connection_query: tuple[tuple[str, str], ...]
    body_schema: dict[str, Any] | None
    body_inputs: tuple[str, ...]
    connection_body: tuple[tuple[str, str], ...]
    page_token: str | None
    response: dict[str, Any]
    payload_wrapped: bool


def _plan(method: Method) -> _Plan:
    row = spec(method.namespace, method.amazon_operation)
    path_inputs: list[tuple[str, str]] = []
    query_inputs: list[tuple[str, str, dict[str, Any]]] = []
    connection_query: list[tuple[str, str]] = []
    repeated: set[str] = set()
    page_token = None
    for parameter in row["parameters"]:
        name, location = parameter["name"], parameter["in"]
        if location == "header":
            if name not in PLATFORM_HEADERS:
                raise ValueError(f"{method.operation_id}: unexpected header {name}")
            continue
        if name in FROM_CONNECTION:
            if location == "path":
                path_inputs.append((f"@{FROM_CONNECTION[name]}", name))
            else:
                connection_query.append((name, FROM_CONNECTION[name]))
            continue
        if name in PAGE_TOKEN_NAMES:
            page_token = name
            continue
        input_name = method.renamed.get(parameter["input"], parameter["input"])
        if parameter.get("repeated"):
            repeated.add(name)
        if location == "path":
            path_inputs.append((input_name, name))
        else:
            query_inputs.append((input_name, name, parameter["schema"]))
    body = row.get("body")
    connection_body: list[tuple[str, str]] = []
    body_inputs: list[str] = []
    if isinstance(body, Mapping):
        names = body.get("x-amazon-names") or {}
        for snake_name, amazon_name in names.items():
            if amazon_name in FROM_CONNECTION:
                connection_body.append((snake_name, FROM_CONNECTION[amazon_name]))
            else:
                body_inputs.append(snake_name)
    response = row["response"]
    properties = response.get("properties") or {}
    wrapped = set(properties) <= {"payload", "errors"} and "payload" in properties
    return _Plan(
        path_inputs=tuple(path_inputs),
        query_inputs=tuple(query_inputs),
        repeated_query=frozenset(repeated),
        connection_query=tuple(connection_query),
        body_schema=dict(body) if isinstance(body, Mapping) else None,
        body_inputs=tuple(body_inputs),
        connection_body=tuple(connection_body),
        page_token=page_token,
        response=properties["payload"] if wrapped else response,
        payload_wrapped=wrapped,
    )


def _title(name: str) -> str:
    text = name.replace("_", " ").strip()
    replacements = {"id": "ID", "ids": "IDs", "msku": "MSKU", "mskus": "MSKUs", "asin": "ASIN"}
    words = [replacements.get(word, word) for word in text.split()]
    return " ".join(words)[:1].upper() + " ".join(words)[1:]


def _input_schema(name: str, schema: Mapping[str, Any], method: Method) -> dict[str, Any]:
    built = public_schema(schema)
    built.setdefault("title", _title(name))
    built.update(copy.deepcopy(dict(method.inputs.get(name) or {})))
    return built


def build(method: Method) -> Operation:
    """The ``Operation`` row for one method."""
    layout = _plan(method)
    row = spec(method.namespace, method.amazon_operation)
    properties: dict[str, dict[str, Any]] = {"connection_ref": CONNECTION_REF}
    required: list[str] = ["connection_ref"]
    parameters = {
        method.renamed.get(parameter["input"], parameter["input"]): parameter
        for parameter in row["parameters"]
    }
    for input_name, _amazon in layout.path_inputs:
        if input_name.startswith("@"):
            continue
        properties[input_name] = _input_schema(input_name, parameters[input_name]["schema"], method)
        required.append(input_name)
    for input_name, _amazon, schema in layout.query_inputs:
        if input_name in method.hidden:
            continue
        properties[input_name] = _input_schema(input_name, schema, method)
        if parameters[input_name]["required"] or input_name in method.required:
            required.append(input_name)
    if layout.body_schema is not None:
        body_properties = layout.body_schema.get("properties") or {}
        body_required = set(layout.body_schema.get("required") or ())
        for input_name in layout.body_inputs:
            if input_name in method.hidden:
                continue
            if input_name in properties:
                raise ValueError(f"{method.operation_id}: input {input_name} declared twice")
            properties[input_name] = _input_schema(input_name, body_properties[input_name], method)
            if (row["body_required"] and input_name in body_required) or (
                input_name in method.required
            ):
                required.append(input_name)
    for input_name, extra_schema in method.extra_inputs.items():
        if input_name in properties:
            raise ValueError(f"{method.operation_id}: input {input_name} declared twice")
        properties[input_name] = copy.deepcopy(dict(extra_schema))
        if input_name in method.required:
            required.append(input_name)
    if layout.page_token:
        properties["next_token"] = NEXT_TOKEN
    outputs = method.outputs if method.outputs is not None else _outputs(method, layout)
    pii = method.pii if method.pii is not None else contains_personal_data(layout.response)
    scope = method.scope or (
        "marketplace"
        if layout.connection_query
        or layout.connection_body
        or any(name.startswith("@marketplace") for name, _amazon in layout.path_inputs)
        else "seller_region"
    )
    kind = "action" if method.mutating else "source"
    return Operation(
        operation_id=method.operation_id,
        display_name=method.display_name,
        description=method.description,
        section=method.section,
        properties=properties,
        required=tuple(dict.fromkeys(required)),
        outputs=(
            *outputs,
            out("amazon_operation", TEXT),
            out("api_version", TEXT),
            out("amazon_request_id", {"type": ["string", "null"]}),
        ),
        handler=lambda context: _run(method, layout, context),
        amazon_calls=(f"{_api_label(method.namespace)} {method.amazon_operation}",),
        roles=method.roles,
        kind=kind,
        mutating=method.mutating,
        effect_kind=method.effect_kind,
        extra_error_codes=method.extra_error_codes,
        artifact_output=method.artifact_output,
        extra_artifact_outputs=method.extra_artifact_outputs,
        pii=pii,
        read_back=method.read_back,
        marketplaces=method.marketplaces,
        availability=method.availability,
        api_version=api_version(method.namespace),
        scope=scope,
    )


_API_LABELS = {
    "shipping_v2": "Shipping v2",
    "inbound_2024_03_20": "Fulfillment Inbound 2024-03-20",
    "inbound_v0": "Fulfillment Inbound v0",
    "inbound_eligibility_v1": "FBA Inbound Eligibility v1",
    "listings_2021_08_01": "Listings Items 2021-08-01",
    "feeds_2021_06_30": "Feeds 2021-06-30",
    "reports_2021_06_30": "Reports 2021-06-30",
}


def _api_label(namespace: str) -> str:
    return _API_LABELS[namespace]


def _outputs(method: Method, layout: _Plan) -> tuple[Output, ...]:
    built: list[Output] = []
    for name, schema in (layout.response.get("properties") or {}).items():
        if name in {"pagination", "errors"} or name in method.dropped_outputs:
            continue
        if name in method.verbatim_outputs:
            built.append(out(name, {"type": ["object", "null"]}))
            continue
        built.append(out(name, output_schema(schema)))
    if layout.page_token:
        built.append(out("next_token", {"type": ["string", "null"]}))
    if spec(method.namespace, method.amazon_operation)["success_status"] == 204:
        built.append(out("accepted", {"type": "boolean"}))
    if method.note:
        built.append(out("note", TEXT))
    return tuple(built)


# -- Execution ------------------------------------------------------------------


def connection_value(context: Context, source: str) -> Any:
    if source == "marketplace_id":
        return context.connection.marketplace_id
    if source == "marketplace_ids":
        return [context.connection.marketplace_id]
    if source == "seller_id":
        return context.connection.seller_id
    raise ExtensionError(errors.INTERNAL_ERROR, "An unknown connection value was requested")


def _run(method: Method, layout: _Plan, context: Context) -> dict[str, Any]:
    data = dict(context.input)
    if method.prepare is not None:
        method.prepare(context, data)
    path: dict[str, str] = {}
    for input_name, amazon_name in layout.path_inputs:
        value = (
            connection_value(context, input_name[1:])
            if input_name.startswith("@")
            else data[input_name]
        )
        path[amazon_name] = str(value)
    query: dict[str, Any] = {}
    for input_name, amazon_name, _schema in layout.query_inputs:
        if data.get(input_name) is not None:
            value = data[input_name]
            if amazon_name in layout.repeated_query and isinstance(value, list):
                value = Repeated(value)
            query[amazon_name] = value
    for amazon_name, source in layout.connection_query:
        query[amazon_name] = connection_value(context, source)
    version = api_version(method.namespace)
    if layout.page_token:
        query[layout.page_token] = pagination.unwrap(context, version)
    body = None
    if layout.body_schema is not None:
        submitted = {name: data[name] for name in layout.body_inputs if name in data}
        for snake_name, source in layout.connection_body:
            submitted[snake_name] = connection_value(context, source)
        body = to_amazon(submitted, layout.body_schema)
    row = spec(method.namespace, method.amazon_operation)
    response = context.transport.call(
        row["method"],
        row["path"],
        path=path,
        query=query,
        body=body,
        mutating=method.mutating,
        purpose=f"Amazon {method.amazon_operation}",
        headers=method.headers(context) if method.headers is not None else None,
    )
    raw: Mapping[str, Any] = response.body if isinstance(response.body, Mapping) else {}
    if layout.payload_wrapped:
        payload = raw.get("payload")
        raw = payload if isinstance(payload, Mapping) else {}
    converted = from_amazon(raw, layout.response)
    if method.result is not None:
        result = method.result(context, converted, response)
    else:
        result = default_result(method, layout, context, converted, raw)
    result["amazon_operation"] = method.amazon_operation
    result["api_version"] = version
    result["amazon_request_id"] = response.headers.get("x-amzn-requestid")
    return result


def default_result(
    method: Method,
    layout: _Plan,
    context: Context,
    converted: dict[str, Any],
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in layout.response.get("properties") or {}:
        if name in {"pagination", "errors"} or name in method.dropped_outputs:
            continue
        if name in method.verbatim_outputs:
            amazon_name = (layout.response.get("x-amazon-names") or {}).get(name, name)
            result[name] = copy.deepcopy(raw.get(amazon_name))
            continue
        result[name] = converted.get(name)
    if layout.page_token:
        token = None
        page = converted.get("pagination")
        if isinstance(page, Mapping):
            token = page.get("next_token")
        result["next_token"] = pagination.wrap(context, api_version(method.namespace), token)
    if spec(method.namespace, method.amazon_operation)["success_status"] == 204:
        result["accepted"] = True
    if method.note:
        result["note"] = method.note
    return result


def operations(methods: tuple[Method, ...]) -> tuple[Operation, ...]:
    return tuple(build(method) for method in methods)


__all__ = [
    "FROM_CONNECTION",
    "Method",
    "api_version",
    "build",
    "connection_value",
    "contains_personal_data",
    "default_result",
    "from_amazon",
    "operations",
    "output_schema",
    "public_schema",
    "spec",
    "to_amazon",
]
