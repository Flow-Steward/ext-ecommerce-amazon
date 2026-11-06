"""Building blocks of the closed operation registry.

Each section module (``runtime/sections/*.py``) declares its operations as literal
``Operation`` rows: the input schema, the outputs, the Amazon calls it makes, the
roles those calls need and the handler. ``runtime/catalog.py`` collects the rows;
the published contracts are generated from them by ``tests/reference``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import errors
from .connection import Connection
from .transport import SpApiTransport

MAX_CONNECTION_REF_LENGTH = 256
MAX_NEXT_TOKEN_LENGTH = 4096
MAX_ID_LENGTH = 128
MAX_SKU_LENGTH = 200
MAX_TIMESTAMP_LENGTH = 40


@dataclass(frozen=True)
class Output:
    name: str
    value_type: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class ArtifactBinding:
    """One artifact the operation reads or writes through the platform."""

    binding_key: str
    field: str
    filename: str = ""
    content_type: str = ""
    max_size_bytes: int = 104_857_600
    required: bool = False
    #: The result field that carries this file's presentation claim (ADR 0045):
    #: its real name and MIME, known only once the document has been read.
    presentation_claim: str = ""


@dataclass
class Context:
    """What one handler gets: its validated input and the hydrated connection."""

    operation: Operation
    input: dict[str, Any]
    connection: Connection
    transport: SpApiTransport
    payload: Mapping[str, Any]
    effect_id: str = ""
    artifact_reader: Callable[..., bytes] | None = None
    artifact_writer: Callable[..., Mapping[str, Any]] | None = None


@dataclass(frozen=True)
class Operation:
    operation_id: str
    display_name: str
    description: str
    section: str
    properties: dict[str, dict[str, Any]]
    required: tuple[str, ...]
    outputs: tuple[Output, ...]
    handler: Callable[[Context], dict[str, Any]]
    amazon_calls: tuple[str, ...]
    roles: tuple[str, ...]
    labels: dict[str, str] = field(default_factory=dict)
    kind: str = "source"
    mutating: bool = False
    effect_kind: str | None = None
    workflow_visible: bool = True
    contacts_amazon: bool = True
    extra_error_codes: tuple[str, ...] = ()
    artifact_input: ArtifactBinding | None = None
    artifact_output: ArtifactBinding | None = None
    #: Further output files, one per content type, when the file Amazon returns
    #: has a format the caller chooses (a label as PDF, PNG or ZPL). The handler
    #: writes exactly one binding per call.
    extra_artifact_outputs: tuple[ArtifactBinding, ...] = ()
    pii: bool = False
    #: The method(s) that read back the state this change affects, named in the
    #: error result when its outcome is unknown.
    read_back: tuple[str, ...] = ()
    #: Marketplace codes where Amazon offers this method; empty means every
    #: marketplace of the connection catalog.
    marketplaces: tuple[str, ...] = ()
    #: Shown in the operation matrix and the form: where and for which program
    #: the method works.
    availability: str = ""
    #: Amazon's API version this method calls, for the matrix.
    api_version: str = ""
    #: Inputs besides the common ones (next_token, page size, connection_ref)
    #: that may change between pages of one search without invalidating its
    #: continuation token; see ``runtime/pagination.py``.
    page_only_inputs: tuple[str, ...] = ()

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(self.required),
            "properties": self.properties,
        }

    @property
    def artifact_outputs(self) -> tuple[ArtifactBinding, ...]:
        first = (self.artifact_output,) if self.artifact_output is not None else ()
        return first + self.extra_artifact_outputs

    @property
    def result_fields(self) -> tuple[str, ...]:
        return tuple(output.name for output in self.outputs)

    @property
    def error_codes(self) -> tuple[str, ...]:
        codes = list(errors.TRANSPORT_ERROR_CODES)
        if self.mutating:
            codes += [errors.MISSING_IDEMPOTENCY_KEY, errors.OUTCOME_UNKNOWN]
        if self.marketplaces:
            codes.append(errors.UNSUPPORTED_MARKETPLACE)
        codes += list(self.extra_error_codes)
        return tuple(dict.fromkeys(codes))


# -- Reusable input fragments ---------------------------------------------

CONNECTION_REF: dict[str, Any] = {
    "type": "string",
    "title": "Connection",
    "description": "The Amazon connection (one seller account in one marketplace) this step uses.",
    "minLength": 1,
    "maxLength": MAX_CONNECTION_REF_LENGTH,
}

NEXT_TOKEN: dict[str, Any] = {
    "type": "string",
    "title": "Continuation token",
    "description": (
        "next_token from the previous page of the same search, with the same filters "
        "and connection. Leave empty for the first page."
    ),
    "minLength": 1,
    "maxLength": MAX_NEXT_TOKEN_LENGTH,
}


def text(
    title: str, description: str, *, max_length: int = MAX_ID_LENGTH, min_length: int = 1
) -> dict[str, Any]:
    return {
        "type": "string",
        "title": title,
        "description": description,
        "minLength": min_length,
        "maxLength": max_length,
    }


def timestamp(title: str, description: str) -> dict[str, Any]:
    return text(
        title,
        description + " ISO 8601 with a time zone, for example 2026-11-01T00:00:00Z.",
        max_length=MAX_TIMESTAMP_LENGTH,
    )


def integer(title: str, description: str, *, minimum: int, maximum: int) -> dict[str, Any]:
    return {
        "type": "integer",
        "title": title,
        "description": description,
        "minimum": minimum,
        "maximum": maximum,
    }


def boolean(title: str, description: str) -> dict[str, Any]:
    return {"type": "boolean", "title": title, "description": description}


def enum(title: str, description: str, values: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "string", "title": title, "description": description, "enum": list(values)}


def enum_list(
    title: str, description: str, values: tuple[str, ...], *, max_items: int | None = None
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "array",
        "title": title,
        "description": description,
        "minItems": 1,
        "items": {"type": "string", "enum": list(values)},
    }
    schema["maxItems"] = max_items or len(values)
    return schema


def text_list(
    title: str, description: str, *, max_items: int, max_length: int = MAX_ID_LENGTH
) -> dict[str, Any]:
    return {
        "type": "array",
        "title": title,
        "description": description,
        "minItems": 1,
        "maxItems": max_items,
        "items": {"type": "string", "minLength": 1, "maxLength": max_length},
    }


def json_object(title: str, description: str) -> dict[str, Any]:
    return {"type": "object", "title": title, "description": description}


# -- Reusable output fragments --------------------------------------------

OBJECT = {"type": "object"}
NULLABLE_OBJECT = {"type": ["object", "null"]}
ARRAY_OF_OBJECTS = {"type": "array", "items": {"type": "object"}}
NULLABLE_TEXT = {"type": ["string", "null"]}
TEXT = {"type": "string"}
BOOLEAN = {"type": "boolean"}
INTEGER = {"type": "integer"}
MONEY = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["amount", "currency_code"],
    "properties": {"amount": {"type": "string"}, "currency_code": {"type": "string"}},
}


def out(name: str, schema: dict[str, Any]) -> Output:
    declared = schema.get("type")
    types = [declared] if isinstance(declared, str) else list(declared or [])
    value_type = "text"
    for candidate, mapped in (
        ("object", "object"),
        ("array", "array"),
        ("boolean", "boolean"),
        ("integer", "integer"),
        ("number", "number"),
    ):
        if candidate in types:
            value_type = mapped
            break
    return Output(name=name, value_type=value_type, schema=schema)


NEXT_TOKEN_OUTPUT = out("next_token", NULLABLE_TEXT)
API_VERSION_OUTPUT = out("api_version", TEXT)
AMAZON_OPERATION_OUTPUT = out("amazon_operation", TEXT)


__all__ = [
    "AMAZON_OPERATION_OUTPUT",
    "API_VERSION_OUTPUT",
    "ARRAY_OF_OBJECTS",
    "BOOLEAN",
    "CONNECTION_REF",
    "INTEGER",
    "MONEY",
    "NEXT_TOKEN",
    "NEXT_TOKEN_OUTPUT",
    "NULLABLE_OBJECT",
    "NULLABLE_TEXT",
    "OBJECT",
    "TEXT",
    "ArtifactBinding",
    "Context",
    "Operation",
    "Output",
    "boolean",
    "enum",
    "enum_list",
    "integer",
    "json_object",
    "out",
    "text",
    "text_list",
    "timestamp",
]
