"""Amazon Shipping API v2 for merchant-fulfilled Amazon orders (channelType AMAZON).

Six methods, one Amazon call each, in the order a workflow uses them:

1. ``get_shipping_rates`` (getRates): the offers for one package of one order.
   It returns a ``request_token`` and, per offer, a ``rate_id``, the carrier and
   service, the total charge, the promised windows and the label formats.
2. ``get_shipping_additional_inputs`` (getAdditionalInputs): only when the chosen
   offer says ``requires_additional_inputs``; returns Amazon's JSON schema for the
   values ``purchase_shipping_shipment`` must then carry.
3. ``purchase_shipping_shipment`` (purchaseShipment): buys the chosen offer. This
   is the paid, external step. The workflow names the offer explicitly with its
   ``request_token`` and ``rate_id``; nothing is picked automatically, an expired
   offer is reported as ``offer_expired`` (never re-quoted), and a failed or
   unknown purchase is never repeated here.
4. ``get_shipping_documents`` (getShipmentDocuments): stores one document of one
   package (the label, by default) as a file.
5. ``get_shipping_tracking`` (getTracking): reads tracking; it changes nothing.
6. ``cancel_shipping_shipment`` (cancelShipment): cancels a shipment that has not
   been picked up.

``request_token`` is Amazon Shipping's identifier of one getRates answer. It is not
the connection's access token and grants nothing on its own.

Where it works: Amazon documents Shipping v2 labels for Amazon-marketplace orders
in the United States, the United Kingdom, Italy, France and Spain (Shipping API
FAQ, service IDs for ON-Amazon labels). The ``x-amzn-shipping-business-id`` header
is derived from the connection's marketplace for every call, never left to
Amazon's default (``AmazonShipping_UK``) and never taken from input. The package
must ship from the marketplace's own country.

The Amazon order: Amazon's Shipping v2 pages do not say whether buying a label
confirms the shipment of the Amazon order. ``confirm_shipment`` therefore stays a
separate method; see the "How the methods behave" page before using both.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from .. import documents, errors
from ..amazon_api import Method, operations
from ..errors import ExtensionError
from ..registry import (
    BOOLEAN,
    TEXT,
    ArtifactBinding,
    Context,
    enum,
    out,
    text,
)
from ..transport import ApiResponse

NAMESPACE = "shipping_v2"
SECTION = "shipping"
ROLES = ("Direct-to-Consumer Shipping (restricted)",)

#: Marketplace code -> (Amazon Shipping business, ISO 3166 country of origin).
SHIPPING_BUSINESSES: dict[str, tuple[str, str]] = {
    "US": ("AmazonShipping_US", "US"),
    "UK": ("AmazonShipping_UK", "GB"),
    "IT": ("AmazonShipping_IT", "IT"),
    "FR": ("AmazonShipping_FR", "FR"),
    "ES": ("AmazonShipping_ES", "ES"),
}
MARKETPLACES = tuple(SHIPPING_BUSINESSES)
AVAILABILITY = (
    "Amazon Shipping v2 labels for Amazon orders: United States, United Kingdom, Italy, "
    "France and Spain only. The package ships from the marketplace's own country."
)

DOCUMENT_TYPES = ("LABEL", "PACKSLIP", "RECEIPT", "CUSTOM_FORM")
DOCUMENT_FORMATS = ("PDF", "PNG", "ZPL")
#: A label or customs form is a few hundred kilobytes; this bounds a hostile answer.
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024

SHIPPING_DOCUMENT = ArtifactBinding(
    binding_key="amazon_shipping_document",
    field="document_file",
    filename="amazon-shipping-document",
    content_type="application/octet-stream",
    max_size_bytes=MAX_DOCUMENT_BYTES,
    presentation_claim="document_file",
)

_TOKEN_NOTE = (
    "request_token identifies this getRates answer for Amazon Shipping; it is not the "
    "connection's access token. Pass it unchanged, with one rate_id from rates, to "
    "purchase_shipping_shipment. Amazon documents that a purchase must follow within "
    "10 minutes of the rate; an older offer is refused with offer_expired."
)
_PURCHASE_NOTE = (
    "Amazon charged the seller's account for this label at the price of the chosen rate "
    "(total_charge in get_shipping_rates). The label is not stored by this step: call "
    "get_shipping_documents with shipment_id and package_client_reference_id. Amazon's "
    "Shipping v2 documentation does not say whether the Amazon order is confirmed as "
    "shipped; read the order with get_order before calling confirm_shipment."
)
_CANCEL_NOTE = (
    "Amazon accepted the cancellation of a shipment that had not been picked up. Whether "
    "and when the label charge is refunded is the carrier's and Amazon's rule; read the "
    "order with get_order to see its state."
)

ORDER_ID = text(
    "Amazon order ID",
    "The Amazon order this package fulfils, for example 123-1234567-1234567.",
    max_length=64,
)
ORDER_ID["examples"] = ["123-1234567-1234567"]

_ADDRESS_EXAMPLE = {
    "name": "Warehouse 1",
    "company_name": "Example Ltd",
    "address_line1": "1 Example Street",
    "city": "London",
    "state_or_region": "London",
    "postal_code": "EC1A 1BB",
    "country_code": "GB",
    "phone_number": "+442000000000",
}
_PACKAGE_EXAMPLE = [
    {
        "package_client_reference_id": "order-123-pkg-1",
        "dimensions": {"length": 30, "width": 20, "height": 10, "unit": "CENTIMETER"},
        "weight": {"value": 1.2, "unit": "KILOGRAM"},
        "insured_value": {"value": 25.0, "unit": "GBP"},
        "items": [
            {
                "item_identifier": "12345678901234",
                "quantity": 1,
                "item_value": {"value": 25.0, "unit": "GBP"},
                "description": "Blue mug",
                "weight": {"value": 1.0, "unit": "KILOGRAM"},
            }
        ],
    }
]
_DOCUMENT_SPEC_EXAMPLE = {
    "format": "PDF",
    "size": {"width": 4, "length": 6, "unit": "INCH"},
    "need_file_joining": False,
    "requested_document_types": ["LABEL"],
}


def business_headers(context: Context) -> dict[str, str]:
    business, _country = SHIPPING_BUSINESSES[context.connection.marketplace.code]
    return {"x-amzn-shipping-business-id": business}


def _purchase_headers(context: Context) -> dict[str, str]:
    headers = business_headers(context)
    # Amazon's model lists this header; its documentation does not describe the
    # semantics, so the platform's effect id is sent without promising that Amazon
    # deduplicates a repeated purchase.
    headers["x-amzn-IdempotencyKey"] = context.effect_id
    return headers


def _prepare_rates(context: Context, data: dict[str, Any]) -> None:
    _business, country = SHIPPING_BUSINESSES[context.connection.marketplace.code]
    origin = str((data.get("ship_from") or {}).get("country_code") or "").upper()
    if origin != country:
        raise ExtensionError(
            errors.UNSUPPORTED_MARKETPLACE,
            f"ship_from.country_code must be {country}: Amazon Shipping for the "
            f"{context.connection.marketplace.country} marketplace ships from that country",
            definitely_no_external_effect=True,
        )
    for package_index, package in enumerate(data.get("packages") or []):
        for item_index, item in enumerate(package.get("items") or []):
            where = f"input.packages[{package_index}].items[{item_index}]"
            if not item.get("item_identifier"):
                raise ExtensionError(
                    errors.INVALID_PAYLOAD,
                    f"{where}.item_identifier is required for an Amazon order: use the "
                    "order_item_id from get_order_items",
                )
            if not item.get("item_value"):
                raise ExtensionError(
                    errors.INVALID_PAYLOAD,
                    f"{where}.item_value is required: Amazon needs the item value to buy a label",
                )
    data["channel_details"] = {
        "channel_type": "AMAZON",
        "amazon_order_details": {"order_id": data.pop("amazon_order_id")},
    }


def _rates_result(
    context: Context, converted: dict[str, Any], _response: ApiResponse
) -> dict[str, Any]:
    return {
        "request_token": converted.get("request_token"),
        "rates": converted.get("rates") or [],
        "ineligible_rates": converted.get("ineligible_rates") or [],
        "note": _TOKEN_NOTE,
    }


def _additional_inputs_result(
    _context: Context, converted: dict[str, Any], _response: ApiResponse
) -> dict[str, Any]:
    # The payload is Amazon's JSON schema for the additional inputs; its keys are
    # data the purchase must repeat exactly, so it is returned unchanged.
    return {"additional_inputs_schema": converted or None}


def _purchase_result(
    context: Context, converted: dict[str, Any], _response: ApiResponse
) -> dict[str, Any]:
    packages: list[dict[str, Any]] = []
    for detail in converted.get("package_document_details") or []:
        packages.append(
            {
                "package_client_reference_id": detail.get("package_client_reference_id"),
                "tracking_id": detail.get("tracking_id"),
                # Only what each document is; the bytes are never put into a step
                # output, a log or a model context.
                "documents": [
                    {"type": document.get("type"), "format": document.get("format")}
                    for document in detail.get("package_documents") or []
                ],
            }
        )
    return {
        "shipment_id": converted.get("shipment_id"),
        "rate_id": context.input["rate_id"],
        "packages": packages,
        "promise": converted.get("promise"),
        "benefits": converted.get("benefits"),
        "note": _PURCHASE_NOTE,
    }


def _documents_result(
    context: Context, converted: dict[str, Any], _response: ApiResponse
) -> dict[str, Any]:
    wanted_type = context.input.get("document_type") or "LABEL"
    detail = converted.get("package_document_detail") or {}
    available = detail.get("package_documents") or []
    chosen = next((row for row in available if row.get("type") == wanted_type), None)
    if chosen is None:
        offered = sorted({str(row.get("type")) for row in available if row.get("type")})
        raise ExtensionError(
            errors.NOT_FOUND,
            f"Amazon returned no {wanted_type} for this package"
            + (f"; it returned {', '.join(offered)}" if offered else ""),
        )
    document_format = str(chosen.get("format") or "")
    if document_format not in DOCUMENT_FORMATS:
        raise ExtensionError(
            errors.DOCUMENT_UNAVAILABLE, "Amazon returned an unknown document format"
        )
    body = _decode(chosen.get("contents"))
    if documents.detect_format(body, allowed=(document_format,)) != document_format:
        raise ExtensionError(errors.DOCUMENT_UNAVAILABLE, "The document does not match its format")
    reference = str(
        detail.get("package_client_reference_id") or context.input["package_client_reference_id"]
    )
    claim = documents.save_document(
        context,
        SHIPPING_DOCUMENT,
        body,
        document_format=document_format,
        filename_stem=f"amazon-{wanted_type.lower()}-{reference}",
    )
    return {
        "document_file": claim,
        "document_type": wanted_type,
        "document_format": document_format,
        "shipment_id": converted.get("shipment_id"),
        "package_client_reference_id": reference,
        "tracking_id": detail.get("tracking_id"),
        "other_document_types": sorted(
            {str(row.get("type")) for row in available if row.get("type") != wanted_type}
        ),
    }


def _decode(contents: Any) -> bytes:
    if not isinstance(contents, str) or not contents:
        raise ExtensionError(errors.DOCUMENT_UNAVAILABLE, "Amazon returned an empty document")
    if len(contents) > (MAX_DOCUMENT_BYTES * 4) // 3 + 4:
        raise ExtensionError(
            errors.RESPONSE_TOO_LARGE, "The document is larger than this step's limit"
        )
    try:
        return base64.b64decode(contents, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ExtensionError(
            errors.DOCUMENT_UNAVAILABLE, "Amazon returned a damaged document"
        ) from exc


def _cancel_result(
    context: Context, _converted: dict[str, Any], _response: ApiResponse
) -> dict[str, Any]:
    return {"shipment_id": context.input["shipment_id"], "cancelled": True, "note": _CANCEL_NOTE}


DOCUMENT_CLAIM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["artifact_id", "binding_key", "filename", "content_type", "size_bytes", "sha256"],
    "properties": {
        "artifact_id": TEXT,
        "binding_key": TEXT,
        "filename": TEXT,
        "content_type": TEXT,
        "size_bytes": {"type": "integer"},
        "sha256": TEXT,
    },
}

METHODS: tuple[Method, ...] = (
    Method(
        operation_id="get_shipping_rates",
        display_name="Get shipping rates",
        description=(
            "Asks Amazon Shipping for the offers to ship one package of one Amazon order: per offer a "
            "rate_id, carrier, service, total charge, promised pickup and delivery windows, label "
            "formats and whether more inputs are needed. Returns request_token for the purchase. "
            "Chooses nothing and buys nothing."
        ),
        section=SECTION,
        namespace=NAMESPACE,
        amazon_operation="getRates",
        roles=ROLES,
        extra_inputs={"amazon_order_id": ORDER_ID},
        required=("amazon_order_id",),
        hidden=(
            "ship_to",
            "channel_details",
            "client_reference_details",
            "destination_access_point_details",
            "carrier_accounts",
            "shipment_type",
        ),
        inputs={
            "ship_from": {
                "title": "Ship from",
                "description": "The address the package leaves from, in the marketplace's country.",
                "examples": [_ADDRESS_EXAMPLE],
            },
            "return_to": {"title": "Return to", "examples": [_ADDRESS_EXAMPLE]},
            "packages": {
                "title": "Package",
                "description": (
                    "Exactly one package: Amazon Shipping quotes one package per request. Dimensions "
                    "in INCH or CENTIMETER, weight in GRAM, KILOGRAM, OUNCE or POUND, values in the "
                    "marketplace currency. item_identifier is the order_item_id from get_order_items."
                ),
                "maxItems": 1,
                "examples": [_PACKAGE_EXAMPLE],
            },
            "ship_date": {
                "title": "Ship date",
                "description": (
                    "Only for a future pickup: ISO 8601 with a time zone, for example "
                    "2026-11-10T09:00:00Z. Leave empty to ship today."
                ),
            },
        },
        marketplaces=MARKETPLACES,
        availability=AVAILABILITY,
        prepare=_prepare_rates,
        headers=business_headers,
        result=_rates_result,
        outputs=None,
        pii=False,
        extra_error_codes=(errors.UNSUPPORTED_MARKETPLACE,),
        note=_TOKEN_NOTE,
        dropped_outputs=(),
    ),
    Method(
        operation_id="get_shipping_additional_inputs",
        display_name="Get shipping additional inputs",
        description=(
            "For an offer whose requires_additional_inputs is true: returns Amazon's JSON schema of the "
            "extra values the purchase must carry. Fill them as that schema says and pass them as "
            "additional_inputs to purchase_shipping_shipment."
        ),
        section=SECTION,
        namespace=NAMESPACE,
        amazon_operation="getAdditionalInputs",
        roles=ROLES,
        inputs={
            "request_token": {
                "title": "Request token",
                "description": "request_token from get_shipping_rates (not an access token).",
            },
            "rate_id": {"title": "Rate ID", "description": "rate_id of the chosen offer."},
        },
        marketplaces=MARKETPLACES,
        availability=AVAILABILITY,
        headers=business_headers,
        result=_additional_inputs_result,
        outputs=(out("additional_inputs_schema", {"type": ["object", "null"]}),),
        pii=False,
    ),
    Method(
        operation_id="purchase_shipping_shipment",
        display_name="Purchase a shipping label",
        description=(
            "Buys the offer named by request_token and rate_id. Amazon charges the seller's account the "
            "offer's total_charge. Returns shipment_id and, per package, the tracking ID and which "
            "documents exist; store a document with get_shipping_documents. Never re-quotes an expired "
            "offer and never repeats a purchase."
        ),
        section=SECTION,
        namespace=NAMESPACE,
        amazon_operation="purchaseShipment",
        roles=ROLES,
        mutating=True,
        effect_kind="amazon_shipping_label_purchase",
        inputs={
            "request_token": {
                "title": "Request token",
                "description": "request_token from get_shipping_rates (not an access token).",
            },
            "rate_id": {"title": "Rate ID", "description": "rate_id of the chosen offer."},
            "requested_document_specification": {
                "title": "Label format",
                "description": (
                    "One of the supported_document_specifications of the chosen rate: format PDF, PNG "
                    "or ZPL, size, dpi (ZPL only: 203 or 300), page layout, file joining and the "
                    "document types. A specification the rate does not offer is refused by Amazon."
                ),
                "examples": [_DOCUMENT_SPEC_EXAMPLE],
            },
            "requested_value_added_services": {"title": "Value-added services"},
            "additional_inputs": {
                "title": "Additional inputs",
                "description": (
                    "Only when the rate requires them: the values get_shipping_additional_inputs "
                    "asks for, with exactly the keys of its schema."
                ),
            },
        },
        marketplaces=MARKETPLACES,
        availability=AVAILABILITY,
        read_back=("get_order",),
        headers=_purchase_headers,
        result=_purchase_result,
        outputs=(
            out("shipment_id", TEXT),
            out("rate_id", TEXT),
            out(
                "packages",
                {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "package_client_reference_id": {"type": ["string", "null"]},
                            "tracking_id": {"type": ["string", "null"]},
                            "documents": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"type": ["string", "null"]},
                                        "format": {"type": ["string", "null"]},
                                    },
                                },
                            },
                        },
                    },
                },
            ),
            out("promise", {"type": ["object", "null"]}),
            out("benefits", {"type": ["object", "null"]}),
            out("note", TEXT),
        ),
        pii=False,
    ),
    Method(
        operation_id="get_shipping_documents",
        display_name="Get a shipping document",
        description=(
            "Stores one document of one package of a purchased shipment as a file: the label by "
            "default, or the packing slip, receipt or customs form. The file holds the buyer's name and "
            "address. Bytes are stored unchanged."
        ),
        section=SECTION,
        namespace=NAMESPACE,
        amazon_operation="getShipmentDocuments",
        roles=ROLES,
        extra_inputs={
            "document_type": enum(
                "Document type",
                "Which document of the package to store. Default LABEL.",
                DOCUMENT_TYPES,
            )
        },
        inputs={
            "shipment_id": {
                "title": "Shipment ID",
                "description": "shipment_id from purchase_shipping_shipment.",
            },
            "package_client_reference_id": {
                "title": "Package reference",
                "description": "package_client_reference_id of the package, as sent to get_shipping_rates.",
            },
            "format": {
                "title": "Format",
                "description": "PDF, PNG or ZPL; one the rate supported. Empty: the purchased format.",
            },
            "dpi": {"title": "DPI", "description": "ZPL only: 203 or 300."},
        },
        marketplaces=MARKETPLACES,
        availability=AVAILABILITY,
        headers=business_headers,
        result=_documents_result,
        outputs=(
            out("document_file", DOCUMENT_CLAIM),
            out("document_type", TEXT),
            out("document_format", TEXT),
            out("shipment_id", {"type": ["string", "null"]}),
            out("package_client_reference_id", TEXT),
            out("tracking_id", {"type": ["string", "null"]}),
            out("other_document_types", {"type": "array", "items": {"type": "string"}}),
        ),
        artifact_output=SHIPPING_DOCUMENT,
        pii=True,
    ),
    Method(
        operation_id="get_shipping_tracking",
        display_name="Get shipment tracking",
        description=(
            "Reads the tracking of one package: status summary, promised delivery date and the event "
            "history. Changes nothing on the order or the shipment."
        ),
        section=SECTION,
        namespace=NAMESPACE,
        amazon_operation="getTracking",
        roles=ROLES,
        inputs={
            "tracking_id": {
                "title": "Tracking ID",
                "description": "tracking_id from the purchase.",
            },
            "carrier_id": {
                "title": "Carrier ID",
                "description": "carrier_id of the purchased rate.",
            },
        },
        marketplaces=MARKETPLACES,
        availability=AVAILABILITY,
        headers=business_headers,
        pii=False,
    ),
    Method(
        operation_id="cancel_shipping_shipment",
        display_name="Cancel a shipping label",
        description=(
            "Cancels a purchased shipment that the carrier has not picked up. A picked-up shipment is "
            "refused with invalid_state. Cancellation windows depend on the carrier."
        ),
        section=SECTION,
        namespace=NAMESPACE,
        amazon_operation="cancelShipment",
        roles=ROLES,
        mutating=True,
        effect_kind="amazon_shipping_label_cancellation",
        inputs={
            "shipment_id": {"title": "Shipment ID", "description": "shipment_id from the purchase."}
        },
        marketplaces=MARKETPLACES,
        availability=AVAILABILITY,
        read_back=("get_shipping_tracking", "get_order"),
        headers=business_headers,
        result=_cancel_result,
        outputs=(out("shipment_id", TEXT), out("cancelled", BOOLEAN), out("note", TEXT)),
        pii=False,
    ),
)

OPERATIONS = operations(METHODS)

__all__ = ["MARKETPLACES", "METHODS", "OPERATIONS", "SHIPPING_BUSINESSES"]
