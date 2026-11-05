"""Stock: FBA quantities (read only) and the merchant-fulfilled quantity.

* ``get_fba_inventory`` reads FBA Inventory v1 at marketplace granularity with
  details. Each quantity category stays separate and is an integer exactly as
  Amazon sent it; a quantity Amazon did not send is ``null``, never 0.
* ``get_fbm_quantity`` / ``set_fbm_quantity`` use the listing's
  ``fulfillment_availability`` for the merchant channel ``DEFAULT``.

Physical FBA stock cannot be changed by this extension: Amazon counts it in its
fulfillment centers. ``set_fbm_quantity`` first reads the listing and changes
the quantity only of a ``DEFAULT`` channel that already exists. A SKU with only
Amazon fulfillment channels, or with no fulfillment availability at all, is
refused before anything is sent: adding a ``DEFAULT`` channel would turn an FBA
offer, or an offer whose fulfillment is not set up yet, into a merchant-fulfilled
one, which is a decision about how the SKU is fulfilled, not a stock update. It
then sends a single ``merge`` patch of ``fulfillment_availability`` for the
``DEFAULT`` channel; merge keeps the other channels and optional fields such as
lead time as they are, where ``replace`` would drop them.
"""

from __future__ import annotations

from typing import Any

from .. import errors, listings_core, pagination, validation
from ..errors import ExtensionError
from ..listings_core import (
    GET_CALL,
    ISSUE_LOCALE_INPUT,
    MERCHANT_CHANNEL,
    PATCH_CALL,
    ROLE_PRODUCT_LISTING,
    SKU_INPUT,
    SUBMISSION_NOTE,
    SUBMISSION_OUTPUTS,
)
from ..registry import (
    ARRAY_OF_OBJECTS,
    BOOLEAN,
    CONNECTION_REF,
    INTEGER,
    NEXT_TOKEN,
    NEXT_TOKEN_OUTPUT,
    OBJECT,
    TEXT,
    Context,
    Operation,
    integer,
    out,
    text_list,
    timestamp,
)
from ..values import as_list, dig

API_VERSION = "v1"
SUMMARIES_PATH = "/fba/inventory/v1/summaries"
MAX_QUANTITY = 99_999_999


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def normalize_summary(raw: Any) -> dict[str, Any]:
    details = dig(raw, "inventoryDetails")
    reserved = dig(details, "reservedQuantity")
    researching = dig(details, "researchingQuantity")
    unfulfillable = dig(details, "unfulfillableQuantity")
    breakdown = {
        dig(entry, "name"): _count(dig(entry, "quantity"))
        for entry in as_list(dig(researching, "researchingQuantityBreakdown"))
    }
    return {
        "seller_sku": dig(raw, "sellerSku"),
        "asin": dig(raw, "asin"),
        "fn_sku": dig(raw, "fnSku"),
        "condition": dig(raw, "condition"),
        "product_name": dig(raw, "productName"),
        "last_updated_time": dig(raw, "lastUpdatedTime"),
        "total_quantity": _count(dig(raw, "totalQuantity")),
        "fulfillable_quantity": _count(dig(details, "fulfillableQuantity")),
        "inbound_working_quantity": _count(dig(details, "inboundWorkingQuantity")),
        "inbound_shipped_quantity": _count(dig(details, "inboundShippedQuantity")),
        "inbound_receiving_quantity": _count(dig(details, "inboundReceivingQuantity")),
        "reserved": {
            "total": _count(dig(reserved, "totalReservedQuantity")),
            "pending_customer_order": _count(dig(reserved, "pendingCustomerOrderQuantity")),
            "pending_transshipment": _count(dig(reserved, "pendingTransshipmentQuantity")),
            "fc_processing": _count(dig(reserved, "fcProcessingQuantity")),
        },
        "researching": {
            "total": _count(dig(researching, "totalResearchingQuantity")),
            "short_term": breakdown.get("researchingQuantityInShortTerm"),
            "mid_term": breakdown.get("researchingQuantityInMidTerm"),
            "long_term": breakdown.get("researchingQuantityInLongTerm"),
        },
        "unfulfillable": {
            "total": _count(dig(unfulfillable, "totalUnfulfillableQuantity")),
            "customer_damaged": _count(dig(unfulfillable, "customerDamagedQuantity")),
            "warehouse_damaged": _count(dig(unfulfillable, "warehouseDamagedQuantity")),
            "distributor_damaged": _count(dig(unfulfillable, "distributorDamagedQuantity")),
            "carrier_damaged": _count(dig(unfulfillable, "carrierDamagedQuantity")),
            "defective": _count(dig(unfulfillable, "defectiveQuantity")),
            "expired": _count(dig(unfulfillable, "expiredQuantity")),
        },
        "stores": [str(item) for item in as_list(dig(raw, "stores"))],
        "amazon": raw,
    }


def _get_fba_inventory(context: Context) -> dict[str, Any]:
    data = context.input
    if data.get("seller_skus") is not None and data.get("start_date_time") is not None:
        raise ExtensionError(
            errors.UNSUPPORTED_FILTER,
            "Amazon ignores seller_skus when start_date_time is given; use one of them",
        )
    validation.timestamp(data.get("start_date_time"), "start_date_time")
    token = pagination.unwrap(context, API_VERSION)
    marketplace_id = context.connection.marketplace_id
    response = context.transport.call(
        "GET",
        SUMMARIES_PATH,
        query={
            "details": True,
            "granularityType": "Marketplace",
            "granularityId": marketplace_id,
            "marketplaceIds": [marketplace_id],
            "startDateTime": data.get("start_date_time"),
            "sellerSkus": data.get("seller_skus"),
            "nextToken": token,
        },
        mutating=False,
        purpose="Amazon FBA inventory",
    )
    summaries = as_list(dig(response.body, "payload", "inventorySummaries"))
    return {
        "summaries": [normalize_summary(item) for item in summaries],
        "count": len(summaries),
        "next_token": pagination.wrap(
            context, API_VERSION, dig(response.body, "pagination", "nextToken")
        ),
    }


def _channels(context: Context, sku: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = listings_core.get_listing_item(context, sku, ["fulfillmentAvailability"])
    channels = [
        listings_core.normalize_channel(item)
        for item in as_list(raw.get("fulfillmentAvailability"))
    ]
    return raw, channels


def _get_fbm_quantity(context: Context) -> dict[str, Any]:
    raw, channels = _channels(context, str(context.input["sku"]))
    merchant = next(
        (item for item in channels if item["fulfillment_channel_code"] == MERCHANT_CHANNEL), None
    )
    return {
        "sku": raw.get("sku"),
        "quantity": merchant["quantity"] if merchant else None,
        "merchant_channel_found": merchant is not None,
        "channels": channels,
        "amazon": {"fulfillmentAvailability": raw.get("fulfillmentAvailability") or []},
    }


def build_quantity_body(context: Context) -> dict[str, Any]:
    value = [{"fulfillment_channel_code": MERCHANT_CHANNEL, "quantity": context.input["quantity"]}]
    return listings_core.patch_body(
        context.input["product_type"], "fulfillment_availability", value
    )


def _set_fbm_quantity(context: Context) -> dict[str, Any]:
    body = build_quantity_body(context)
    _, channels = _channels(context, str(context.input["sku"]))
    codes = {item["fulfillment_channel_code"] for item in channels}
    if MERCHANT_CHANNEL not in codes:
        situation = (
            "is fulfilled by Amazon (no DEFAULT channel)"
            if codes
            else "has no fulfillment availability yet (no DEFAULT channel)"
        )
        raise ExtensionError(
            errors.INVALID_PAYLOAD,
            f"This SKU {situation}; set_fbm_quantity only changes the quantity of an existing "
            "merchant-fulfilled channel, so nothing was sent. To make the SKU merchant-fulfilled, set "
            "fulfillment_availability with update_listing_attributes.",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        )
    return listings_core.submit(context, "PATCH", body, preview=False)


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        operation_id="get_fba_inventory",
        display_name="Get FBA inventory",
        description=(
            "Returns FBA inventory summaries for the connection's marketplace with every quantity category "
            "kept separate: fulfillable, inbound (working, shipped, receiving), reserved, researching and "
            "unfulfillable, each broken down. A quantity Amazon does not send is null. Filter by up to 50 "
            "SKUs or by start_date_time (changes since then; inbound changes are not detected), not both. "
            "Amazon's page token expires 30 seconds after it is issued."
        ),
        section="inventory",
        properties={
            "connection_ref": CONNECTION_REF,
            "seller_skus": text_list("SKUs", "Up to 50 seller SKUs.", max_items=50, max_length=200),
            "start_date_time": timestamp(
                "Changed since", "Only summaries changed since this time (at most 18 months ago)."
            ),
            "next_token": NEXT_TOKEN,
        },
        required=("connection_ref",),
        outputs=(out("summaries", ARRAY_OF_OBJECTS), out("count", INTEGER), NEXT_TOKEN_OUTPUT),
        handler=_get_fba_inventory,
        amazon_calls=("FBA Inventory v1 getInventorySummaries",),
        roles=("Amazon Fulfillment",),
        extra_error_codes=(errors.UNSUPPORTED_FILTER, errors.CONTINUATION_TOKEN_INVALID),
    ),
    Operation(
        operation_id="get_fbm_quantity",
        display_name="Get merchant quantity",
        description=(
            "Returns the quantity the seller makes available on the merchant-fulfilled channel (DEFAULT) "
            "for a SKU, and every fulfillment channel of the listing."
        ),
        section="inventory",
        properties={"connection_ref": CONNECTION_REF, "sku": SKU_INPUT},
        required=("connection_ref", "sku"),
        outputs=(
            out("sku", TEXT),
            out("quantity", {"type": ["integer", "null"]}),
            out("merchant_channel_found", BOOLEAN),
            out("channels", ARRAY_OF_OBJECTS),
            out("amazon", OBJECT),
        ),
        handler=_get_fbm_quantity,
        amazon_calls=(GET_CALL + " (includedData fulfillmentAvailability)",),
        roles=(ROLE_PRODUCT_LISTING,),
    ),
    Operation(
        operation_id="set_fbm_quantity",
        display_name="Set merchant quantity",
        description=(
            "Sets the quantity available for a merchant-fulfilled (DEFAULT channel) SKU. Only the "
            "fulfillment_availability attribute for the DEFAULT channel is sent. The listing must already "
            "have a DEFAULT channel: a SKU fulfilled only by Amazon, or without fulfillment availability, "
            "is refused before anything is sent. FBA stock is not changed by this extension."
            + SUBMISSION_NOTE
        ),
        section="inventory",
        properties={
            "connection_ref": CONNECTION_REF,
            "sku": SKU_INPUT,
            "product_type": listings_core.PRODUCT_TYPE_INPUT,
            "quantity": integer(
                "Quantity", "Units available for sale, 0 or more.", minimum=0, maximum=MAX_QUANTITY
            ),
            "issue_locale": ISSUE_LOCALE_INPUT,
        },
        required=("connection_ref", "sku", "product_type", "quantity"),
        outputs=SUBMISSION_OUTPUTS,
        handler=_set_fbm_quantity,
        amazon_calls=(
            GET_CALL + " (includedData fulfillmentAvailability)",
            PATCH_CALL + " (fulfillment_availability only)",
        ),
        roles=(ROLE_PRODUCT_LISTING,),
        kind="action",
        mutating=True,
        effect_kind="amazon_fbm_quantity_update",
    ),
)
