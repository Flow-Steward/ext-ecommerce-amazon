"""Orders, in both Amazon API versions.

Every orders operation takes ``api_version``: ``2026-01-01`` (the default) or
``v0``. The same method, the same input names and the same normalized output
work for both; what differs is stated, not hidden:

* Filters that exist in only one version are accepted only with that version.
  Sending one to the other version is an ``unsupported_filter`` error, never a
  silently dropped condition.
* Statuses are normalized to the 2026-01-01 names. ``INVOICE_UNCONFIRMED``
  exists only in ``v0``.
* Each order and item keeps Amazon's original object under ``amazon``, so a
  field this extension does not normalize is still there.
* Personal data: ``v0`` needs a restricted data token, minted per call;
  ``2026-01-01`` returns it according to the application's roles.

Amazon removes the six ``v0`` read operations on 2027-03-27. After that date the
``v0`` choice fails with Amazon's own error; nothing falls back to the other
version on its own.
"""

from __future__ import annotations

from typing import Any

from .. import errors, pagination, validation
from ..errors import ExtensionError
from ..rdt import restricted_data_token
from ..registry import (
    AMAZON_OPERATION_OUTPUT,
    API_VERSION_OUTPUT,
    ARRAY_OF_OBJECTS,
    CONNECTION_REF,
    NEXT_TOKEN,
    NEXT_TOKEN_OUTPUT,
    NULLABLE_OBJECT,
    OBJECT,
    Context,
    Operation,
    enum,
    enum_list,
    integer,
    out,
    text,
    text_list,
    timestamp,
)
from ..values import as_list, dig, first, money

V2026 = "2026-01-01"
V0 = "v0"
API_VERSIONS = (V2026, V0)

SEARCH_2026 = "/orders/2026-01-01/orders"
ORDER_2026 = "/orders/2026-01-01/orders/{orderId}"
SEARCH_V0 = "/orders/v0/orders"
ORDER_V0 = "/orders/v0/orders/{orderId}"
ITEMS_V0 = "/orders/v0/orders/{orderId}/orderItems"
BUYER_V0 = "/orders/v0/orders/{orderId}/buyerInfo"
ADDRESS_V0 = "/orders/v0/orders/{orderId}/address"

STATUSES = (
    "PENDING_AVAILABILITY",
    "PENDING",
    "UNSHIPPED",
    "PARTIALLY_SHIPPED",
    "SHIPPED",
    "CANCELLED",
    "UNFULFILLABLE",
    "INVOICE_UNCONFIRMED",
)
_V0_STATUS = {
    "PendingAvailability": "PENDING_AVAILABILITY",
    "Pending": "PENDING",
    "Unshipped": "UNSHIPPED",
    "PartiallyShipped": "PARTIALLY_SHIPPED",
    "Shipped": "SHIPPED",
    "Canceled": "CANCELLED",
    "Unfulfillable": "UNFULFILLABLE",
    "InvoiceUnconfirmed": "INVOICE_UNCONFIRMED",
}
_STATUS_TO_V0 = {value: key for key, value in _V0_STATUS.items()}
FULFILLED_BY = ("MERCHANT", "AMAZON")
_CHANNEL_TO_V0 = {"MERCHANT": "MFN", "AMAZON": "AFN"}
_V0_CHANNEL = {value: key for key, value in _CHANNEL_TO_V0.items()}
INCLUDED_DATA = (
    "BUYER",
    "RECIPIENT",
    "PROCEEDS",
    "EXPENSE",
    "PROMOTION",
    "CANCELLATION",
    "FULFILLMENT",
    "PACKAGES",
    "TAX",
    "PAYMENT",
    "FULFILLMENT_ORDERS",
)

API_VERSION_INPUT = enum(
    "Amazon API version",
    "2026-01-01 (current, default) or v0 (legacy; Amazon removes its read operations on 2027-03-27).",
    API_VERSIONS,
)
ORDER_ID_INPUT = text("Amazon order ID", "The Amazon order ID, for example 123-1234567-1234567.", max_length=64)

#: Filters that only one version understands.
_ONLY_2026 = ("included_data",)
_ONLY_V0 = ("amazon_order_ids", "seller_order_id")


def api_version(context: Context) -> str:
    return str(context.input.get("api_version") or V2026)


def _refuse_other_version_filters(context: Context, version: str) -> None:
    foreign = _ONLY_V0 if version == V2026 else _ONLY_2026
    used = [name for name in foreign if context.input.get(name) is not None]
    if used:
        raise ExtensionError(
            errors.UNSUPPORTED_FILTER,
            f"{', '.join(used)} is not available with Orders API {version}",
        )
    if version == V2026 and "INVOICE_UNCONFIRMED" in (context.input.get("statuses") or []):
        raise ExtensionError(
            errors.UNSUPPORTED_FILTER, "INVOICE_UNCONFIRMED exists only in Orders API v0"
        )


def _address(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    def pick(camel: str, pascal: str) -> Any:
        return first(value, camel, pascal)

    return {
        "name": pick("name", "Name"),
        "company_name": pick("companyName", "CompanyName"),
        "address_line1": pick("addressLine1", "AddressLine1"),
        "address_line2": pick("addressLine2", "AddressLine2"),
        "address_line3": pick("addressLine3", "AddressLine3"),
        "city": pick("city", "City"),
        "district_or_county": first(value, "districtOrCounty", "County", "District"),
        "state_or_region": pick("stateOrRegion", "StateOrRegion"),
        "postal_code": pick("postalCode", "PostalCode"),
        "country_code": pick("countryCode", "CountryCode"),
        "phone": pick("phone", "Phone"),
        "address_type": pick("addressType", "AddressType"),
    }


def normalize_order(raw: Any, version: str) -> dict[str, Any]:
    if version == V2026:
        return {
            "order_id": dig(raw, "orderId"),
            "status": dig(raw, "fulfillment", "fulfillmentStatus"),
            "purchase_date": dig(raw, "createdTime"),
            "last_updated": dig(raw, "lastUpdatedTime"),
            "marketplace_id": dig(raw, "salesChannel", "marketplaceId"),
            "fulfilled_by": dig(raw, "fulfillment", "fulfilledBy"),
            "order_total": money(dig(raw, "proceeds", "grandTotal")),
            "api_version": V2026,
            "amazon": raw,
        }
    return {
        "order_id": dig(raw, "AmazonOrderId"),
        "status": _V0_STATUS.get(str(dig(raw, "OrderStatus") or ""), dig(raw, "OrderStatus")),
        "purchase_date": dig(raw, "PurchaseDate"),
        "last_updated": dig(raw, "LastUpdateDate"),
        "marketplace_id": dig(raw, "MarketplaceId"),
        "fulfilled_by": _V0_CHANNEL.get(str(dig(raw, "FulfillmentChannel") or "")),
        "order_total": money(dig(raw, "OrderTotal")),
        "api_version": V0,
        "amazon": raw,
    }


def normalize_item(raw: Any, version: str) -> dict[str, Any]:
    if version == V2026:
        return {
            "order_item_id": dig(raw, "orderItemId"),
            "asin": dig(raw, "product", "asin"),
            "seller_sku": dig(raw, "product", "sellerSku"),
            "title": dig(raw, "product", "title"),
            "quantity_ordered": dig(raw, "quantityOrdered"),
            "quantity_shipped": dig(raw, "fulfillment", "quantityFulfilled"),
            "unit_price": money(dig(raw, "product", "price", "unitPrice")),
            "item_total": money(dig(raw, "proceeds", "proceedsTotal")),
            "api_version": V2026,
            "amazon": raw,
        }
    return {
        "order_item_id": dig(raw, "OrderItemId"),
        "asin": dig(raw, "ASIN"),
        "seller_sku": dig(raw, "SellerSKU"),
        "title": dig(raw, "Title"),
        "quantity_ordered": dig(raw, "QuantityOrdered"),
        "quantity_shipped": dig(raw, "QuantityShipped"),
        # v0 ItemPrice is the line total (unit price times quantity), not a unit price.
        "unit_price": None,
        "item_total": money(dig(raw, "ItemPrice")),
        "api_version": V0,
        "amazon": raw,
    }


# -- search ---------------------------------------------------------------


def _search_orders(context: Context) -> dict[str, Any]:
    version = api_version(context)
    _refuse_other_version_filters(context, version)
    data = context.input
    for name in ("created_after", "created_before", "last_updated_after", "last_updated_before"):
        validation.timestamp(data.get(name), name)
    token = pagination.unwrap(context, version)
    if token is None and not (data.get("created_after") or data.get("last_updated_after") or data.get("amazon_order_ids")):
        raise ExtensionError(
            errors.INVALID_PAYLOAD, "Give created_after or last_updated_after to search orders"
        )
    if data.get("created_after") and (data.get("last_updated_after") or data.get("last_updated_before")):
        raise ExtensionError(
            errors.INVALID_PAYLOAD,
            "Search by creation time or by update time, not both in one search",
        )
    statuses = data.get("statuses") or []
    fulfilled_by = data.get("fulfilled_by") or []
    if version == V2026:
        query = {
            "marketplaceIds": [context.connection.marketplace_id],
            "createdAfter": data.get("created_after"),
            "createdBefore": data.get("created_before"),
            "lastUpdatedAfter": data.get("last_updated_after"),
            "lastUpdatedBefore": data.get("last_updated_before"),
            "fulfillmentStatuses": statuses or None,
            "fulfilledBy": fulfilled_by or None,
            "maxResultsPerPage": data.get("max_results"),
            "includedData": data.get("included_data"),
            "paginationToken": token,
        }
        response = context.transport.call(
            "GET", SEARCH_2026, query=query, mutating=False, purpose="Amazon search orders"
        )
        orders = as_list(dig(response.body, "orders"))
        amazon_token = dig(response.body, "pagination", "nextToken")
        operation = "Orders 2026-01-01 searchOrders"
    else:
        query = {
            "MarketplaceIds": [context.connection.marketplace_id],
            "CreatedAfter": data.get("created_after"),
            "CreatedBefore": data.get("created_before"),
            "LastUpdatedAfter": data.get("last_updated_after"),
            "LastUpdatedBefore": data.get("last_updated_before"),
            "OrderStatuses": [_STATUS_TO_V0[item] for item in statuses] or None,
            "FulfillmentChannels": [_CHANNEL_TO_V0[item] for item in fulfilled_by] or None,
            "MaxResultsPerPage": data.get("max_results"),
            "AmazonOrderIds": data.get("amazon_order_ids"),
            "SellerOrderId": data.get("seller_order_id"),
            "NextToken": token,
        }
        response = context.transport.call(
            "GET", SEARCH_V0, query=query, mutating=False, purpose="Amazon search orders"
        )
        orders = as_list(dig(response.body, "payload", "Orders"))
        amazon_token = dig(response.body, "payload", "NextToken")
        operation = "Orders v0 getOrders"
    return {
        "orders": [normalize_order(order, version) for order in orders],
        "count": len(orders),
        "next_token": pagination.wrap(context, version, amazon_token),
        "api_version": version,
        "amazon_operation": operation,
    }


# -- one order ------------------------------------------------------------


def _get_order_raw(context: Context, version: str, included: list[str] | None) -> Any:
    order_id = str(context.input["order_id"])
    if version == V2026:
        response = context.transport.call(
            "GET",
            ORDER_2026,
            path={"orderId": order_id},
            query={"includedData": included or None},
            mutating=False,
            purpose="Amazon get order",
        )
        return dig(response.body, "order")
    response = context.transport.call(
        "GET", ORDER_V0, path={"orderId": order_id}, mutating=False, purpose="Amazon get order"
    )
    return dig(response.body, "payload")


def _get_order(context: Context) -> dict[str, Any]:
    version = api_version(context)
    _refuse_other_version_filters(context, version)
    raw = _get_order_raw(context, version, context.input.get("included_data"))
    if not isinstance(raw, dict):
        raise ExtensionError(errors.NOT_FOUND, "Amazon returned no order for that ID")
    return {
        "order": normalize_order(raw, version),
        "api_version": version,
        "amazon_operation": "Orders 2026-01-01 getOrder" if version == V2026 else "Orders v0 getOrder",
    }


def _get_order_items(context: Context) -> dict[str, Any]:
    version = api_version(context)
    if version == V2026:
        if context.input.get("next_token") is not None:
            raise ExtensionError(
                errors.UNSUPPORTED_FILTER,
                "Orders API 2026-01-01 returns all items of an order at once; next_token is v0 only",
            )
        raw = _get_order_raw(context, version, None)
        items = as_list(dig(raw, "orderItems"))
        amazon_token = None
        operation = "Orders 2026-01-01 getOrder"
    else:
        token = pagination.unwrap(context, version)
        response = context.transport.call(
            "GET",
            ITEMS_V0,
            path={"orderId": str(context.input["order_id"])},
            query={"NextToken": token},
            mutating=False,
            purpose="Amazon get order items",
        )
        items = as_list(dig(response.body, "payload", "OrderItems"))
        amazon_token = dig(response.body, "payload", "NextToken")
        operation = "Orders v0 getOrderItems"
    return {
        "order_id": context.input["order_id"],
        "items": [normalize_item(item, version) for item in items],
        "count": len(items),
        "next_token": pagination.wrap(context, version, amazon_token),
        "api_version": version,
        "amazon_operation": operation,
    }


def _get_buyer_info(context: Context) -> dict[str, Any]:
    version = api_version(context)
    order_id = str(context.input["order_id"])
    if version == V2026:
        raw = _get_order_raw(context, version, ["BUYER", "RECIPIENT"])
        buyer = dig(raw, "buyer")
        recipient = dig(raw, "recipient")
        return {
            "order_id": order_id,
            "buyer": {
                "name": dig(buyer, "buyerName"),
                "email": dig(buyer, "buyerEmail"),
                "company_name": dig(buyer, "buyerCompanyName"),
                "purchase_order_number": dig(buyer, "buyerPurchaseOrderNumber"),
            }
            if isinstance(buyer, dict)
            else None,
            "shipping_address": _address(dig(recipient, "deliveryAddress")),
            "api_version": version,
            "amazon_operation": "Orders 2026-01-01 getOrder (includedData BUYER, RECIPIENT)",
            "amazon": {"buyer": buyer, "recipient": recipient},
        }
    token = restricted_data_token(
        context,
        [
            ("GET", BUYER_V0, {"orderId": order_id}),
            ("GET", ADDRESS_V0, {"orderId": order_id}),
        ],
    )
    buyer_response = context.transport.call(
        "GET", BUYER_V0, path={"orderId": order_id}, mutating=False,
        purpose="Amazon order buyer info", access_token=token,
    )
    address_response = context.transport.call(
        "GET", ADDRESS_V0, path={"orderId": order_id}, mutating=False,
        purpose="Amazon order address", access_token=token,
    )
    buyer = dig(buyer_response.body, "payload")
    address = dig(address_response.body, "payload")
    return {
        "order_id": order_id,
        "buyer": {
            "name": dig(buyer, "BuyerName"),
            "email": dig(buyer, "BuyerEmail"),
            "company_name": dig(address, "BuyerCompanyName"),
            "purchase_order_number": dig(buyer, "PurchaseOrderNumber"),
        }
        if isinstance(buyer, dict)
        else None,
        "shipping_address": _address(dig(address, "ShippingAddress")),
        "api_version": version,
        "amazon_operation": "Orders v0 getOrderBuyerInfo + getOrderAddress (restricted data token)",
        "amazon": {"buyer": buyer, "address": address},
    }


# -- rows -----------------------------------------------------------------

_SEARCH_PROPERTIES: dict[str, dict[str, Any]] = {
    "connection_ref": CONNECTION_REF,
    "api_version": API_VERSION_INPUT,
    "created_after": timestamp("Created after", "Orders created at or after this time."),
    "created_before": timestamp("Created before", "Orders created before this time."),
    "last_updated_after": timestamp("Updated after", "Orders updated at or after this time."),
    "last_updated_before": timestamp("Updated before", "Orders updated before this time."),
    "statuses": enum_list("Statuses", "Only orders in these statuses. INVOICE_UNCONFIRMED is v0 only.", STATUSES),
    "fulfilled_by": enum_list("Fulfilled by", "MERCHANT (FBM) or AMAZON (FBA).", FULFILLED_BY),
    "max_results": integer("Page size", "Orders per page, 1 to 100.", minimum=1, maximum=100),
    "included_data": enum_list("Included data (2026-01-01)", "Extra order sections to return.", INCLUDED_DATA),
    "amazon_order_ids": text_list("Amazon order IDs (v0)", "Up to 50 order IDs.", max_items=50, max_length=64),
    "seller_order_id": text("Seller order ID (v0)", "Your own order identifier.", max_length=64),
    "next_token": NEXT_TOKEN,
}

_ROLE_ORDERS = "Inventory and Order Tracking"
_ROLE_PII = "Direct-to-Consumer Shipping (restricted) for buyer and address data"

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        operation_id="search_orders",
        display_name="Find orders",
        description=(
            "Returns one page of the connection's orders created or updated in a time window. "
            "Pass next_token from the result to get the next page."
        ),
        section="orders",
        properties=_SEARCH_PROPERTIES,
        required=("connection_ref",),
        outputs=(
            out("orders", ARRAY_OF_OBJECTS),
            out("count", {"type": "integer"}),
            NEXT_TOKEN_OUTPUT,
            API_VERSION_OUTPUT,
            AMAZON_OPERATION_OUTPUT,
        ),
        handler=_search_orders,
        amazon_calls=("Orders 2026-01-01 searchOrders", "Orders v0 getOrders"),
        roles=(_ROLE_ORDERS,),
        extra_error_codes=(errors.UNSUPPORTED_FILTER, errors.CONTINUATION_TOKEN_INVALID),
    ),
    Operation(
        operation_id="get_order",
        display_name="Get an order",
        description="Returns one order by its Amazon order ID.",
        section="orders",
        properties={
            "connection_ref": CONNECTION_REF,
            "order_id": ORDER_ID_INPUT,
            "api_version": API_VERSION_INPUT,
            "included_data": _SEARCH_PROPERTIES["included_data"],
        },
        required=("connection_ref", "order_id"),
        outputs=(out("order", OBJECT), API_VERSION_OUTPUT, AMAZON_OPERATION_OUTPUT),
        handler=_get_order,
        amazon_calls=("Orders 2026-01-01 getOrder", "Orders v0 getOrder"),
        roles=(_ROLE_ORDERS,),
        extra_error_codes=(errors.UNSUPPORTED_FILTER,),
    ),
    Operation(
        operation_id="get_order_items",
        display_name="Get order items",
        description="Returns the items of one order. v0 pages them; 2026-01-01 returns them all.",
        section="orders",
        properties={
            "connection_ref": CONNECTION_REF,
            "order_id": ORDER_ID_INPUT,
            "api_version": API_VERSION_INPUT,
            "next_token": NEXT_TOKEN,
        },
        required=("connection_ref", "order_id"),
        outputs=(
            out("order_id", {"type": "string"}),
            out("items", ARRAY_OF_OBJECTS),
            out("count", {"type": "integer"}),
            NEXT_TOKEN_OUTPUT,
            API_VERSION_OUTPUT,
            AMAZON_OPERATION_OUTPUT,
        ),
        handler=_get_order_items,
        amazon_calls=("Orders 2026-01-01 getOrder", "Orders v0 getOrderItems"),
        roles=(_ROLE_ORDERS,),
        extra_error_codes=(errors.UNSUPPORTED_FILTER, errors.CONTINUATION_TOKEN_INVALID),
    ),
    Operation(
        operation_id="get_order_buyer_info",
        display_name="Get buyer and shipping address",
        description=(
            "Returns the buyer and the shipping address of one order, as far as the "
            "application's Amazon roles allow. Contains personal data."
        ),
        section="orders",
        properties={
            "connection_ref": CONNECTION_REF,
            "order_id": ORDER_ID_INPUT,
            "api_version": API_VERSION_INPUT,
        },
        required=("connection_ref", "order_id"),
        outputs=(
            out("order_id", {"type": "string"}),
            out("buyer", NULLABLE_OBJECT),
            out("shipping_address", NULLABLE_OBJECT),
            API_VERSION_OUTPUT,
            AMAZON_OPERATION_OUTPUT,
            out("amazon", OBJECT),
        ),
        handler=_get_buyer_info,
        amazon_calls=(
            "Orders 2026-01-01 getOrder (includedData BUYER, RECIPIENT)",
            "Tokens 2021-03-01 createRestrictedDataToken + Orders v0 getOrderBuyerInfo, getOrderAddress",
        ),
        roles=(_ROLE_ORDERS, _ROLE_PII),
        pii=True,
    ),
)
