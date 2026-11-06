"""Sending inventory to Amazon's fulfillment centres: Fulfillment Inbound 2024-03-20.

Every Amazon operation of the inbound workflow is its own method, named
``fba_`` + the operation in snake_case. The workflow decides which ones to call
and in what order; a method never calls the next step, never waits, never picks a
packing, placement, carrier or delivery window, and never loops over pages,
shipments or marketplaces.

Identifiers are kept apart because Amazon uses several for one shipment:

* ``inbound_plan_id`` — the plan (``wf…``), from ``fba_create_inbound_plan``;
* ``shipment_id`` — a shipment inside the plan (``sh…``), used by the 2024-03-20
  operations;
* ``shipment_confirmation_id`` — the shipment's ``FBA…`` ID, shown on labels and
  used by ``fba_get_labels`` and ``fba_get_bill_of_lading`` (Inbound v0);
* ``operation_id`` — an asynchronous request; ``fba_get_inbound_operation_status``
  checks it once;
* ``packing_option_id``, ``packing_group_id``, ``placement_option_id``,
  ``transportation_option_id``, ``delivery_window_option_id``, ``slot_id`` —
  options Amazon offered, each confirmed by its own method.

Asynchronous requests (``generate_*``, ``confirm_*``, ``create``, ``set``,
``cancel``, most ``update_*``) answer as soon as Amazon accepted them, with the
``operation_id``. Accepted is not completed: check the operation before using its
result.

Where it works: Amazon supports inbound shipment creation in every Amazon store
except Brazil. Amazon-partnered carriers are offered in the contiguous United
States (and, through the Partnered Carrier Program, in India); in the EU the seller
must first accept the carrier's and Amazon's terms in Seller Central. Pack Later is
for pallet (LTL/FTL) shipments only. The India-only steps (compliance details and
the delivery challan) live in ``fba_inbound_india``.
"""

from __future__ import annotations

from typing import Any

from .. import documents, errors
from ..amazon_api import Method, operations
from ..errors import ExtensionError
from ..marketplaces import MARKETPLACE_CODES
from ..registry import TEXT, ArtifactBinding, Context, out
from ..transport import ApiResponse

NAMESPACE = "inbound_2024_03_20"
SECTION = "fba_inbound"
ROLES = ("Amazon Fulfillment",)

#: Every marketplace of the connection catalog but Brazil.
INBOUND_MARKETPLACES = tuple(code for code in MARKETPLACE_CODES if code != "BR")
INBOUND_AVAILABILITY = (
    "Every Amazon marketplace except Brazil, where Amazon does not support inbound shipment "
    "creation through the API."
)
PARTNERED_NOTE = (
    "Amazon-partnered carriers are offered in the contiguous United States (and through the "
    "Partnered Carrier Program in India); in the EU the seller must first accept the carrier's "
    "and Amazon's terms in Seller Central."
)

ASYNC_NOTE = (
    "Amazon accepted the request and processes it asynchronously. Accepted is not completed: "
    "check operation_id once with fba_get_inbound_operation_status before the next step."
)

SYNC_NOTE = "Amazon applied the change (HTTP 204); read the plan or shipment to see it."

#: Amazon's PDF label and bill-of-lading documents are a few megabytes at most.
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024

FBA_DOCUMENT = ArtifactBinding(
    binding_key="amazon_fba_document",
    field="document_file",
    filename="amazon-fba-document",
    content_type="application/octet-stream",
    max_size_bytes=MAX_DOCUMENT_BYTES,
    presentation_claim="document_file",
)

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
DOCUMENT_OUTPUTS = (
    out("document_file", DOCUMENT_CLAIM),
    out("document_format", TEXT),
    out("expires_at", {"type": ["string", "null"]}),
)

PLAN_ID = {
    "title": "Inbound plan ID",
    "description": "inbound_plan_id from fba_create_inbound_plan or fba_list_inbound_plans (wf…).",
}
SHIPMENT_ID = {
    "title": "Shipment ID",
    "description": (
        "The 2024-03-20 shipment_id (sh…) from fba_get_inbound_plan or a placement option. Not the "
        "FBA… shipment confirmation ID."
    ),
}
PAGE_SIZE = {"title": "Page size", "description": "How many rows one page returns."}
PLACEMENT_OPTION_ID = {"title": "Placement option ID"}

_ADDRESS_EXAMPLE = {
    "name": "Warehouse 1",
    "company_name": "Example Ltd",
    "address_line1": "1 Example Street",
    "city": "Seattle",
    "state_or_province_code": "WA",
    "postal_code": "98101",
    "country_code": "US",
    "phone_number": "+12065550100",
    "email": "warehouse@example.com",
}
_ITEMS_EXAMPLE = [
    {"msku": "MUG-BLUE-01", "quantity": 24, "prep_owner": "SELLER", "label_owner": "SELLER"}
]
_PACKAGE_GROUPINGS_EXAMPLE = [
    {
        "packing_group_id": "pg1234abcd-1234-abcd-5678-1234abcd5678",
        "boxes": [
            {
                "content_information_source": "BOX_CONTENT_PROVIDED",
                "quantity": 2,
                "dimensions": {
                    "length": 40,
                    "width": 30,
                    "height": 30,
                    "unit_of_measurement": "CM",
                },
                "weight": {"value": 9.5, "unit": "KG"},
                "items": [
                    {
                        "msku": "MUG-BLUE-01",
                        "quantity": 12,
                        "prep_owner": "SELLER",
                        "label_owner": "SELLER",
                    }
                ],
            }
        ],
    }
]


def _confirm_transport_result(
    context: Context, converted: dict[str, Any], _response: ApiResponse
) -> dict[str, Any]:
    selections = [
        {
            "shipment_id": row.get("shipment_id"),
            "transportation_option_id": row.get("transportation_option_id"),
        }
        for row in context.input.get("transportation_selections") or []
    ]
    return {
        "operation_id": converted.get("operation_id"),
        "inbound_plan_id": context.input["inbound_plan_id"],
        "transportation_selections": selections,
        "charge_notice": CHARGE_NOTICE,
        "note": ASYNC_NOTE,
    }


CHARGE_NOTICE = (
    "For an Amazon-partnered carrier this authorizes Amazon to charge the seller's account the "
    "transportation option's quote (cost and currency in fba_list_transportation_options). "
    "Amazon lets a small parcel request be voided within 24 hours and an LTL/FTL request within "
    "one hour of confirmation; after that the shipping cost is charged. No new transportation "
    "options can be generated or confirmed for the plan afterwards."
)
PLACEMENT_NOTICE = (
    "Confirming a placement option cannot be undone for the plan. The option's fees (for example "
    "the placement service fee) are listed in fba_list_placement_options."
)
CANCEL_NOTICE = (
    "Amazon may charge for a cancellation outside the void window of a partnered carrier (24 "
    "hours for small parcel, one hour for LTL)."
)


def download_document(
    context: Context, *, url: Any, allowed: tuple[str, ...], stem: str, expires_at: Any = None
) -> dict[str, Any]:
    body = context.transport.download_document(
        str(url or ""), max_bytes=MAX_DOCUMENT_BYTES, purpose="Amazon FBA document"
    )
    document_format = documents.detect_format(body, allowed=allowed)
    claim = documents.save_document(
        context, FBA_DOCUMENT, body, document_format=document_format, filename_stem=stem
    )
    return {
        "document_file": claim,
        "document_format": document_format,
        "expires_at": expires_at if isinstance(expires_at, str) else None,
    }


def _item_labels_result(
    context: Context, converted: dict[str, Any], _response: ApiResponse
) -> dict[str, Any]:
    downloads = converted.get("document_downloads") or []
    if len(downloads) != 1:
        raise ExtensionError(
            errors.DOCUMENT_UNAVAILABLE,
            f"Amazon returned {len(downloads)} label documents; this method stores exactly one. "
            "Request labels for fewer MSKUs per call.",
        )
    download = downloads[0]
    return download_document(
        context,
        url=download.get("uri"),
        allowed=("PDF",),
        stem="amazon-item-labels",
        expires_at=download.get("expiration"),
    )


def _async(
    operation_id: str,
    display_name: str,
    description: str,
    amazon_operation: str,
    *,
    effect_kind: str,
    read_back: tuple[str, ...],
    inputs: dict[str, dict[str, Any]] | None = None,
    marketplaces: tuple[str, ...] = INBOUND_MARKETPLACES,
    availability: str = INBOUND_AVAILABILITY,
    section: str = SECTION,
    note: str = ASYNC_NOTE,
) -> Method:
    return Method(
        operation_id=operation_id,
        display_name=display_name,
        description=description,
        section=section,
        namespace=NAMESPACE,
        amazon_operation=amazon_operation,
        roles=ROLES,
        mutating=True,
        effect_kind=effect_kind,
        inputs={
            "inbound_plan_id": PLAN_ID,
            "shipment_id": SHIPMENT_ID,
            **(inputs or {}),
        },
        marketplaces=marketplaces,
        availability=availability,
        read_back=read_back,
        note=note,
    )


def _read(
    operation_id: str,
    display_name: str,
    description: str,
    amazon_operation: str,
    *,
    inputs: dict[str, dict[str, Any]] | None = None,
    marketplaces: tuple[str, ...] = INBOUND_MARKETPLACES,
    availability: str = INBOUND_AVAILABILITY,
    section: str = SECTION,
) -> Method:
    return Method(
        operation_id=operation_id,
        display_name=display_name,
        description=description,
        section=section,
        namespace=NAMESPACE,
        amazon_operation=amazon_operation,
        roles=ROLES,
        inputs={
            "inbound_plan_id": PLAN_ID,
            "shipment_id": SHIPMENT_ID,
            "page_size": PAGE_SIZE,
            **(inputs or {}),
        },
        marketplaces=marketplaces,
        availability=availability,
    )


_LIST_NOTE = " Returns one page and next_token; nothing loops over the remaining pages."

METHODS: tuple[Method, ...] = (
    # -- Items: preparation and labels ------------------------------------
    _read(
        "fba_list_prep_details",
        "FBA: list prep details",
        "Reads the prep category and prep types set through the API (fba_set_prep_details) for up "
        "to 100 MSKUs in the connection's marketplace. Prep details entered in Seller Central are "
        "not returned here; set them with fba_set_prep_details as a separate step.",
        "listPrepDetails",
        inputs={
            "mskus": {
                "title": "MSKUs",
                "description": "Up to 100 seller SKUs.",
                "examples": [["MUG-BLUE-01"]],
            }
        },
    ),
    _async(
        "fba_set_prep_details",
        "FBA: set prep details",
        "Sets the prep category and prep types of MSKUs in the connection's marketplace, which Amazon "
        "needs before those MSKUs can be sent. A separate, explicit step: fba_list_prep_details "
        "shows only what was set this way, never Seller Central's prep settings.",
        "setPrepDetails",
        effect_kind="amazon_fba_prep_details_update",
        read_back=("fba_list_prep_details",),
        inputs={
            "msku_prep_details": {
                "title": "MSKU prep details",
                "examples": [
                    [
                        {
                            "msku": "MUG-BLUE-01",
                            "prep_category": "FRAGILE",
                            "prep_types": ["ITEM_BUBBLEWRAP"],
                        }
                    ]
                ],
            }
        },
    ),
    Method(
        operation_id="fba_create_marketplace_item_labels",
        display_name="FBA: create item labels",
        description=(
            "Creates the FNSKU item labels for MSKUs and quantities and stores Amazon's PDF as a file. "
            "Changes nothing on the plan."
        ),
        section=SECTION,
        namespace=NAMESPACE,
        amazon_operation="createMarketplaceItemLabels",
        roles=ROLES,
        inputs={
            "msku_quantities": {
                "title": "MSKUs and quantities",
                "description": "1 to 100 MSKUs, 1 to 10,000 labels each.",
                "examples": [[{"msku": "MUG-BLUE-01", "quantity": 24}]],
            },
            "height": {
                "title": "Label height (mm)",
                "description": "Thermal labels: 25 to 100 millimetres.",
            },
            "width": {
                "title": "Label width (mm)",
                "description": "Thermal labels: 25 to 100 millimetres.",
            },
        },
        marketplaces=INBOUND_MARKETPLACES,
        availability=INBOUND_AVAILABILITY,
        result=_item_labels_result,
        outputs=DOCUMENT_OUTPUTS,
        artifact_output=FBA_DOCUMENT,
        pii=False,
    ),
    # -- Plans ----------------------------------------------------------------
    Method(
        operation_id="fba_create_inbound_plan",
        display_name="FBA: create inbound plan",
        description=(
            "Creates an inbound plan for the connection's marketplace from the MSKUs and quantities to "
            "send and the address they ship from. Returns inbound_plan_id and operation_id at once; "
            "the plan is usable after the operation succeeded."
        ),
        section=SECTION,
        namespace=NAMESPACE,
        amazon_operation="createInboundPlan",
        roles=ROLES,
        mutating=True,
        effect_kind="amazon_fba_inbound_plan_creation",
        inputs={
            "items": {
                "title": "Items",
                "description": "MSKUs, quantities and who preps and labels them.",
                "examples": [_ITEMS_EXAMPLE],
            },
            "source_address": {"title": "Ship-from address", "examples": [_ADDRESS_EXAMPLE]},
            "name": {"title": "Plan name"},
        },
        marketplaces=INBOUND_MARKETPLACES,
        availability=INBOUND_AVAILABILITY,
        read_back=("fba_list_inbound_plans",),
        note=ASYNC_NOTE,
    ),
    _read(
        "fba_list_inbound_plans",
        "FBA: list inbound plans",
        "Lists inbound plans, optionally by status, sorted as asked." + _LIST_NOTE,
        "listInboundPlans",
    ),
    _read(
        "fba_get_inbound_plan",
        "FBA: get inbound plan",
        "Reads one inbound plan: status, ship-from address, its shipments and the packing and "
        "placement options with their status.",
        "getInboundPlan",
    ),
    _async(
        "fba_cancel_inbound_plan",
        "FBA: cancel inbound plan",
        "Cancels an inbound plan and its shipments. " + CANCEL_NOTICE,
        "cancelInboundPlan",
        effect_kind="amazon_fba_inbound_plan_cancellation",
        read_back=("fba_get_inbound_plan",),
    ),
    Method(
        operation_id="fba_get_inbound_operation_status",
        display_name="FBA: get operation status",
        description=(
            "Checks one asynchronous Inbound request once: IN_PROGRESS, SUCCESS or FAILED, with "
            "Amazon's problems (code, message, severity). Does not wait; call it again later from the "
            "workflow if the status is IN_PROGRESS."
        ),
        section=SECTION,
        namespace=NAMESPACE,
        amazon_operation="getInboundOperationStatus",
        roles=ROLES,
        inputs={
            "operation_id": {
                "title": "Operation ID",
                "description": "operation_id returned by an asynchronous FBA method.",
            }
        },
        marketplaces=INBOUND_MARKETPLACES,
        availability=INBOUND_AVAILABILITY,
    ),
    _read(
        "fba_list_inbound_plan_items",
        "FBA: list plan items",
        "Lists the items of an inbound plan." + _LIST_NOTE,
        "listInboundPlanItems",
    ),
    _read(
        "fba_list_inbound_plan_boxes",
        "FBA: list plan boxes",
        "Lists the boxes of an inbound plan." + _LIST_NOTE,
        "listInboundPlanBoxes",
    ),
    _read(
        "fba_list_inbound_plan_pallets",
        "FBA: list plan pallets",
        "Lists the pallets of an inbound plan." + _LIST_NOTE,
        "listInboundPlanPallets",
    ),
    _async(
        "fba_update_inbound_plan_name",
        "FBA: rename inbound plan",
        "Changes the name of an inbound plan.",
        "updateInboundPlanName",
        effect_kind="amazon_fba_inbound_plan_rename",
        read_back=("fba_get_inbound_plan",),
        note=SYNC_NOTE,
    ),
    # -- Packing ----------------------------------------------------------------
    _async(
        "fba_generate_packing_options",
        "FBA: generate packing options",
        "Asks Amazon to work out the ways the plan's items can be packed into packing groups.",
        "generatePackingOptions",
        effect_kind="amazon_fba_packing_options_generation",
        read_back=("fba_list_packing_options",),
    ),
    _read(
        "fba_list_packing_options",
        "FBA: list packing options",
        "Lists the packing options of a plan with their packing groups, fees, discounts and "
        "expiration." + _LIST_NOTE,
        "listPackingOptions",
    ),
    _async(
        "fba_confirm_packing_option",
        "FBA: confirm packing option",
        "Confirms the packing option the workflow chose. Choose it from fba_list_packing_options; "
        "nothing is chosen here.",
        "confirmPackingOption",
        effect_kind="amazon_fba_packing_option_confirmation",
        read_back=("fba_get_inbound_plan",),
        inputs={"packing_option_id": {"title": "Packing option ID"}},
    ),
    _read(
        "fba_list_packing_group_items",
        "FBA: list packing group items",
        "Lists the items of one packing group." + _LIST_NOTE,
        "listPackingGroupItems",
        inputs={"packing_group_id": {"title": "Packing group ID"}},
    ),
    _read(
        "fba_list_packing_group_boxes",
        "FBA: list packing group boxes",
        "Lists the boxes of one packing group." + _LIST_NOTE,
        "listPackingGroupBoxes",
        inputs={"packing_group_id": {"title": "Packing group ID"}},
    ),
    _async(
        "fba_set_packing_information",
        "FBA: set packing information",
        "Sends box contents, dimensions and weights per packing group (Pack Now: after confirming the "
        "packing option and before confirming placement) or per shipment (Pack Later, pallets only: "
        "after confirming placement). Dimensions in IN or CM, weight in LB or KG.",
        "setPackingInformation",
        effect_kind="amazon_fba_packing_information_update",
        read_back=("fba_list_inbound_plan_boxes",),
        inputs={
            "package_groupings": {
                "title": "Package groupings",
                "examples": [_PACKAGE_GROUPINGS_EXAMPLE],
            }
        },
    ),
    # -- Placement --------------------------------------------------------------
    _async(
        "fba_generate_placement_options",
        "FBA: generate placement options",
        "Asks Amazon for the ways the plan's shipments can be split across fulfillment centres. "
        "custom_placement names fulfillment centres where Amazon allows it (for example India).",
        "generatePlacementOptions",
        effect_kind="amazon_fba_placement_options_generation",
        read_back=("fba_list_placement_options",),
    ),
    _read(
        "fba_list_placement_options",
        "FBA: list placement options",
        "Lists the placement options with their shipment IDs, fees, discounts, status and "
        "expiration. An expired option must be generated again." + _LIST_NOTE,
        "listPlacementOptions",
    ),
    _async(
        "fba_confirm_placement_option",
        "FBA: confirm placement option",
        "Confirms the placement option the workflow chose. " + PLACEMENT_NOTICE,
        "confirmPlacementOption",
        effect_kind="amazon_fba_placement_option_confirmation",
        read_back=("fba_get_inbound_plan",),
        inputs={"placement_option_id": PLACEMENT_OPTION_ID},
    ),
    # -- Transportation -------------------------------------------------------
    _async(
        "fba_generate_transportation_options",
        "FBA: generate transportation options",
        "Asks Amazon for carrier options per shipment: small parcel or LTL/FTL, Amazon-partnered or "
        "the seller's own carrier, from the ready-to-ship window, contact and, for pallets, pallet "
        "and freight details. " + PARTNERED_NOTE,
        "generateTransportationOptions",
        effect_kind="amazon_fba_transportation_options_generation",
        read_back=("fba_list_transportation_options",),
        inputs={
            "placement_option_id": PLACEMENT_OPTION_ID,
            "shipment_transportation_configurations": {
                "title": "Shipment transportation configurations",
                "examples": [
                    [
                        {
                            "shipment_id": "sh1234abcd-1234-abcd-5678-1234abcd5678",
                            "ready_to_ship_window": {"start": "2026-11-12T00:00:00Z"},
                            "contact_information": {
                                "name": "Jane Doe",
                                "phone_number": "+12065550100",
                                "email": "jane@example.com",
                            },
                        }
                    ]
                ],
            },
        },
    ),
    _read(
        "fba_list_transportation_options",
        "FBA: list transportation options",
        "Lists transportation options for a placement option or a shipment: shipping mode, solution "
        "(Amazon-partnered or own carrier), carrier and, for partnered carriers, the quote with cost, "
        "currency and expiration." + _LIST_NOTE + " Read every page (next_token) before concluding "
        "that no Amazon-partnered option is offered.",
        "listTransportationOptions",
        inputs={"placement_option_id": PLACEMENT_OPTION_ID},
    ),
    Method(
        operation_id="fba_confirm_transportation_options",
        display_name="FBA: confirm transportation options",
        description=(
            "Confirms one transportation option per shipment, as the workflow chose them. "
            + CHARGE_NOTICE
        ),
        section=SECTION,
        namespace=NAMESPACE,
        amazon_operation="confirmTransportationOptions",
        roles=ROLES,
        mutating=True,
        effect_kind="amazon_fba_transportation_confirmation",
        inputs={
            "inbound_plan_id": PLAN_ID,
            "transportation_selections": {
                "title": "Transportation selections",
                "description": "One shipment_id with its chosen transportation_option_id per shipment.",
                "examples": [
                    [
                        {
                            "shipment_id": "sh1234abcd-1234-abcd-5678-1234abcd5678",
                            "transportation_option_id": "to1234abcd-1234-abcd-5678-1234abcd5678",
                        }
                    ]
                ],
            },
        },
        marketplaces=INBOUND_MARKETPLACES,
        availability=INBOUND_AVAILABILITY + " " + PARTNERED_NOTE,
        read_back=("fba_get_shipment", "fba_list_transportation_options"),
        result=_confirm_transport_result,
        outputs=(
            out("operation_id", {"type": ["string", "null"]}),
            out("inbound_plan_id", TEXT),
            out(
                "transportation_selections",
                {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "shipment_id": {"type": ["string", "null"]},
                            "transportation_option_id": {"type": ["string", "null"]},
                        },
                    },
                },
            ),
            out("charge_notice", TEXT),
            out("note", TEXT),
        ),
        pii=False,
    ),
    # -- Delivery windows (own carrier) ---------------------------------------
    _async(
        "fba_generate_delivery_window_options",
        "FBA: generate delivery window options",
        "Asks Amazon for delivery windows of a shipment sent with the seller's own carrier.",
        "generateDeliveryWindowOptions",
        effect_kind="amazon_fba_delivery_windows_generation",
        read_back=("fba_list_delivery_window_options",),
    ),
    _read(
        "fba_list_delivery_window_options",
        "FBA: list delivery window options",
        "Lists a shipment's delivery windows with their availability and valid_until; a window not "
        "confirmed by then must be generated again." + _LIST_NOTE,
        "listDeliveryWindowOptions",
    ),
    _async(
        "fba_confirm_delivery_window_options",
        "FBA: confirm delivery window",
        "Confirms the delivery window the workflow chose for a shipment with the seller's own carrier.",
        "confirmDeliveryWindowOptions",
        effect_kind="amazon_fba_delivery_window_confirmation",
        read_back=("fba_get_shipment",),
        inputs={"delivery_window_option_id": {"title": "Delivery window option ID"}},
    ),
    # -- Shipments ----------------------------------------------------------------
    _read(
        "fba_get_shipment",
        "FBA: get shipment",
        "Reads one shipment: status, shipment_confirmation_id (FBA…), destination fulfillment centre, "
        "source address, contact, selected transportation and delivery window, tracking and the "
        "Amazon reference ID needed for appointments.",
        "getShipment",
    ),
    _read(
        "fba_list_shipment_items",
        "FBA: list shipment items",
        "Lists the items of a shipment." + _LIST_NOTE,
        "listShipmentItems",
    ),
    _read(
        "fba_list_shipment_boxes",
        "FBA: list shipment boxes",
        "Lists the boxes of a shipment with their box IDs." + _LIST_NOTE,
        "listShipmentBoxes",
    ),
    _read(
        "fba_list_shipment_pallets",
        "FBA: list shipment pallets",
        "Lists the pallets of a shipment." + _LIST_NOTE,
        "listShipmentPallets",
    ),
    _async(
        "fba_update_shipment_name",
        "FBA: rename shipment",
        "Changes the name of a shipment.",
        "updateShipmentName",
        effect_kind="amazon_fba_shipment_rename",
        read_back=("fba_get_shipment",),
        note=SYNC_NOTE,
    ),
    _async(
        "fba_update_shipment_source_address",
        "FBA: change shipment source address",
        "Changes the address a shipment leaves from, where Amazon still allows it.",
        "updateShipmentSourceAddress",
        effect_kind="amazon_fba_shipment_source_address_update",
        read_back=("fba_get_shipment",),
        inputs={"address": {"title": "Ship-from address", "examples": [_ADDRESS_EXAMPLE]}},
    ),
    _async(
        "fba_update_shipment_tracking_details",
        "FBA: set shipment tracking",
        "Sends the tracking of a shipment sent with the seller's own carrier: per box tracking IDs for "
        "small parcel, or the bill of lading and freight bill numbers for LTL/FTL.",
        "updateShipmentTrackingDetails",
        effect_kind="amazon_fba_shipment_tracking_update",
        read_back=("fba_get_shipment",),
    ),
)

OPERATIONS = operations(METHODS)

__all__ = [
    "ASYNC_NOTE",
    "SYNC_NOTE",
    "CHARGE_NOTICE",
    "DOCUMENT_OUTPUTS",
    "FBA_DOCUMENT",
    "INBOUND_AVAILABILITY",
    "INBOUND_MARKETPLACES",
    "MAX_DOCUMENT_BYTES",
    "PLAN_ID",
    "ROLES",
    "SHIPMENT_ID",
    "download_document",
    "METHODS",
    "OPERATIONS",
]
