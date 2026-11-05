"""Shipping updates for merchant-fulfilled orders, through Orders API v0.

``confirmShipment`` and ``updateShipmentStatus`` exist only in Orders v0. Orders
2026-01-01 has no equivalent, and these two operations are not part of Amazon's
v0 deprecation schedule (which removes the six v0 *read* operations on
2027-03-27), so there is no ``api_version`` choice here.

Both calls answer HTTP 204 with no body. A 204 means Amazon took the update; it
does not return the order's new state, so the result tells the workflow to read
the order (``get_order``) to see it. Neither call has an idempotency key on
Amazon's side: the platform's external-effect id owns each attempt, and a 5xx or
a timeout after the request was sent is reported as ``outcome_unknown``.
"""

from __future__ import annotations

import re
from typing import Any

from .. import errors, validation
from ..errors import ExtensionError
from ..registry import (
    BOOLEAN,
    CONNECTION_REF,
    TEXT,
    Context,
    Operation,
    enum,
    out,
    text,
    text_list,
    timestamp,
)

CONFIRM_PATH = "/orders/v0/orders/{orderId}/shipmentConfirmation"
STATUS_PATH = "/orders/v0/orders/{orderId}/shipment"

SHIPMENT_STATUSES = ("ReadyForPickup", "PickedUp", "RefusedPickup")
COD_METHODS = ("DirectPayment",)
#: Amazon supports a COD collection method only in Japan.
COD_MARKETPLACES = frozenset({"JP"})

_PACKAGE_REFERENCE_RE = re.compile(r"^[1-9][0-9]{0,18}$")
_NOT_DEPRECATED = (
    " Orders v0 only: Orders 2026-01-01 has no equivalent, and this call is not in Amazon's v0 "
    "deprecation schedule."
)
_READ_BACK = "Amazon accepted the update and returns no order state; read the order with get_order to see its status."

ORDER_ID_INPUT = text(
    "Amazon order ID", "The Amazon order ID, for example 123-1234567-1234567.", max_length=64
)


def _items_input(description: str, *, transparency: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "order_item_id": text(
            "Order item ID", "The order item ID from get_order_items.", max_length=64
        ),
        "quantity": {"type": "integer", "minimum": 1, "maximum": 100000},
    }
    if transparency:
        properties["transparency_codes"] = text_list(
            "Transparency codes",
            "Amazon Transparency codes for this item, if it needs them.",
            max_items=100,
            max_length=100,
        )
    return {
        "type": "array",
        "title": "Order items",
        "description": description,
        "minItems": 1,
        "maxItems": 100,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["order_item_id", "quantity"],
            "properties": properties,
        },
    }


def _order_items(items: Any) -> list[dict[str, Any]]:
    built: list[dict[str, Any]] = []
    for item in items or []:
        entry: dict[str, Any] = {"orderItemId": item["order_item_id"], "quantity": item["quantity"]}
        if item.get("transparency_codes") is not None:
            entry["transparencyCodes"] = list(item["transparency_codes"])
        built.append(entry)
    return built


def _confirm_shipment(context: Context) -> dict[str, Any]:
    data = context.input
    package_reference_id = str(data["package_reference_id"])
    if _PACKAGE_REFERENCE_RE.fullmatch(package_reference_id) is None:
        raise ExtensionError(
            errors.INVALID_PAYLOAD,
            "input.package_reference_id must be a positive number, for example 1",
        )
    ship_date = validation.timestamp(data["ship_date"], "ship_date")
    if str(data["carrier_code"]).lower() == "other" and not data.get("carrier_name"):
        raise ExtensionError(
            errors.INVALID_PAYLOAD, "input.carrier_name is required when carrier_code is Other"
        )
    if (
        data.get("cod_collection_method")
        and context.connection.marketplace.code not in COD_MARKETPLACES
    ):
        raise ExtensionError(
            errors.UNSUPPORTED_FILTER,
            "cod_collection_method is supported only in the Japan marketplace",
        )
    package: dict[str, Any] = {
        "packageReferenceId": package_reference_id,
        "carrierCode": data["carrier_code"],
        "trackingNumber": data["tracking_number"],
        "shipDate": ship_date,
        "orderItems": _order_items(data["items"]),
    }
    for source, target in (
        ("carrier_name", "carrierName"),
        ("shipping_method", "shippingMethod"),
        ("ship_from_supply_source_id", "shipFromSupplySourceId"),
    ):
        if data.get(source) is not None:
            package[target] = data[source]
    body: dict[str, Any] = {
        "marketplaceId": context.connection.marketplace_id,
        "packageDetail": package,
    }
    if data.get("cod_collection_method"):
        body["codCollectionMethod"] = data["cod_collection_method"]
    context.transport.call(
        "POST",
        CONFIRM_PATH,
        path={"orderId": str(data["order_id"])},
        body=body,
        mutating=True,
        purpose="Amazon confirm shipment",
    )
    return {
        "order_id": data["order_id"],
        "confirmed": True,
        "package_reference_id": package_reference_id,
        "note": _READ_BACK,
    }


def _update_shipment_status(context: Context) -> dict[str, Any]:
    data = context.input
    body: dict[str, Any] = {
        "marketplaceId": context.connection.marketplace_id,
        "shipmentStatus": data["shipment_status"],
    }
    if data.get("items"):
        body["orderItems"] = _order_items(data["items"])
    context.transport.call(
        "POST",
        STATUS_PATH,
        path={"orderId": str(data["order_id"])},
        body=body,
        mutating=True,
        purpose="Amazon update shipment status",
    )
    return {
        "order_id": data["order_id"],
        "shipment_status": data["shipment_status"],
        "updated": True,
        "note": _READ_BACK,
    }


_ROLES = ("Inventory and Order Tracking",)

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        operation_id="confirm_shipment",
        display_name="Confirm a shipment",
        description=(
            "Confirms that a package of a merchant-fulfilled order was shipped: carrier, tracking number, "
            "ship date and the items with quantities in the package. Amazon answers without the new order "
            "state; read the order afterwards." + _NOT_DEPRECATED
        ),
        section="shipping",
        properties={
            "connection_ref": CONNECTION_REF,
            "order_id": ORDER_ID_INPUT,
            "package_reference_id": text(
                "Package reference ID",
                "Your positive numeric identifier of this package within the order, for example 1.",
                max_length=19,
            ),
            "carrier_code": text(
                "Carrier code",
                "Amazon's carrier code, for example UPS, DHL, Royal Mail, or Other with carrier_name.",
                max_length=100,
            ),
            "carrier_name": text(
                "Carrier name", "Required when carrier_code is Other.", max_length=100
            ),
            "shipping_method": text(
                "Shipping method", "The ship method, for example Next Day.", max_length=100
            ),
            "tracking_number": text(
                "Tracking number", "The carrier's tracking number.", max_length=100
            ),
            "ship_date": timestamp("Ship date", "When the package was shipped."),
            "ship_from_supply_source_id": text(
                "Supply source ID",
                "The supply source the package ships from, if you use supply sources.",
                max_length=128,
            ),
            "items": _items_input(
                "The order items and quantities in this package.", transparency=True
            ),
            "cod_collection_method": enum(
                "COD collection method",
                "Cash-on-delivery collection method (Japan only).",
                COD_METHODS,
            ),
        },
        required=(
            "connection_ref",
            "order_id",
            "package_reference_id",
            "carrier_code",
            "tracking_number",
            "ship_date",
            "items",
        ),
        outputs=(
            out("order_id", TEXT),
            out("confirmed", BOOLEAN),
            out("package_reference_id", TEXT),
            out("note", TEXT),
        ),
        handler=_confirm_shipment,
        amazon_calls=("Orders v0 confirmShipment",),
        roles=_ROLES,
        kind="action",
        mutating=True,
        effect_kind="amazon_shipment_confirmation",
        extra_error_codes=(errors.UNSUPPORTED_FILTER,),
    ),
    Operation(
        operation_id="update_shipment_status",
        display_name="Update shipment status",
        description=(
            "Sets the shipment status of a merchant-fulfilled order to ReadyForPickup, PickedUp or "
            "RefusedPickup, for the whole order or for the listed items. Amazon answers without the new "
            "order state; read the order afterwards." + _NOT_DEPRECATED
        ),
        section="shipping",
        properties={
            "connection_ref": CONNECTION_REF,
            "order_id": ORDER_ID_INPUT,
            "shipment_status": enum("Shipment status", "The status to apply.", SHIPMENT_STATUSES),
            "items": _items_input(
                "For a partial update: the order items and quantities the status applies to.",
                transparency=False,
            ),
        },
        required=("connection_ref", "order_id", "shipment_status"),
        outputs=(
            out("order_id", TEXT),
            out("shipment_status", TEXT),
            out("updated", BOOLEAN),
            out("note", TEXT),
        ),
        handler=_update_shipment_status,
        amazon_calls=("Orders v0 updateShipmentStatus",),
        roles=_ROLES,
        kind="action",
        mutating=True,
        effect_kind="amazon_shipment_status_update",
    ),
)
