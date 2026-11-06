"""FBA inbound: eligibility, the two Inbound v0 documents, and the India-only steps.

* ``fba_get_item_eligibility_preview`` (FBA Inbound Eligibility v1) says whether
  one ASIN can be sent to Amazon's fulfillment centres and why not.
* ``fba_get_labels`` and ``fba_get_bill_of_lading`` are Inbound v0 operations that
  Amazon keeps for shipments created with 2024-03-20: box and pallet labels and the
  bill of lading of a partnered LTL/FTL shipment. They take the shipment
  *confirmation* ID (``FBA…``), never the 2024-03-20 ``shipment_id``. Amazon's
  download URL lives for 15 seconds, so the document is fetched at once and
  stored as a file; the URL itself is never an output. Creating shipments through
  Inbound v0 is not offered.
* India (Amazon IN store) requires compliance details per item (HSN code, declared
  value, tax rates), self-ship appointments at the fulfillment centre and, for the
  Partnered Carrier Program, a delivery challan that travels with the goods.
"""

from __future__ import annotations

from typing import Any

from ..amazon_api import Method, operations
from ..registry import Context
from ..transport import ApiResponse
from .fba_inbound import (
    ASYNC_NOTE,
    DOCUMENT_OUTPUTS,
    FBA_DOCUMENT,
    INBOUND_AVAILABILITY,
    INBOUND_MARKETPLACES,
    PLAN_ID,
    ROLES,
    SHIPMENT_ID,
    download_document,
)

SECTION = "fba_inbound"
INDIA_SECTION = "fba_inbound_india"
INDIA = ("IN",)
INDIA_AVAILABILITY = "India (Amazon IN store) only."
APPOINTMENT_AVAILABILITY = (
    "Self-ship shipments where fulfillment-centre appointments are mandatory, such as India. "
    "Amazon does not offer appointments for partnered-carrier shipments."
)
INDIA_ROLES = ("Amazon Fulfillment", "Product Listing")

CONFIRMATION_ID = {
    "title": "Shipment confirmation ID",
    "description": (
        "The shipment's FBA… ID: shipment_confirmation_id from fba_get_shipment. Not the 2024-03-20 "
        "shipment_id (sh…)."
    ),
    "examples": ["FBA1234ABCD"],
}


def _labels_result(
    context: Context, converted: dict[str, Any], _response: ApiResponse
) -> dict[str, Any]:
    return download_document(
        context,
        url=converted.get("download_url"),
        allowed=("PDF", "ZIP"),
        stem=f"amazon-fba-labels-{context.input['shipment_confirmation_id']}",
    )


def _bill_of_lading_result(
    context: Context, converted: dict[str, Any], _response: ApiResponse
) -> dict[str, Any]:
    return download_document(
        context,
        url=converted.get("download_url"),
        allowed=("PDF",),
        stem=f"amazon-bill-of-lading-{context.input['shipment_confirmation_id']}",
    )


def _challan_result(
    context: Context, converted: dict[str, Any], _response: ApiResponse
) -> dict[str, Any]:
    download = converted.get("document_download") or {}
    return download_document(
        context,
        url=download.get("uri"),
        allowed=("PDF",),
        stem=f"amazon-delivery-challan-{context.input['shipment_id']}",
        expires_at=download.get("expiration"),
    )


METHODS: tuple[Method, ...] = (
    Method(
        operation_id="fba_get_item_eligibility_preview",
        display_name="FBA: check item eligibility",
        description=(
            "Says whether one ASIN can be sent to Amazon's fulfillment centres (program INBOUND, in the "
            "connection's marketplace) or commingled (COMMINGLING), with Amazon's ineligibility reasons."
        ),
        section=SECTION,
        namespace="inbound_eligibility_v1",
        amazon_operation="getItemEligibilityPreview",
        roles=ROLES,
        inputs={"asin": {"title": "ASIN", "examples": ["B000000000"]}},
        marketplaces=INBOUND_MARKETPLACES,
        availability=INBOUND_AVAILABILITY,
    ),
    Method(
        operation_id="fba_get_labels",
        display_name="FBA: get box or pallet labels",
        description=(
            "Stores the box labels (BARCODE_2D or UNIQUE) or pallet labels (PALLET) of a shipment as a "
            "file, for the page type chosen. For UNIQUE labels name the box IDs from "
            "fba_list_shipment_boxes; for a non-partnered LTL shipment give the page size and start "
            "index. Getting labels does not change the shipment's status."
        ),
        section=SECTION,
        namespace="inbound_v0",
        amazon_operation="getLabels",
        roles=ROLES,
        renamed={"shipment_id": "shipment_confirmation_id"},
        inputs={
            "shipment_confirmation_id": CONFIRMATION_ID,
            "page_type": {"title": "Page type"},
            "label_type": {"title": "Label type"},
            "number_of_packages": {"title": "Number of packages"},
            "package_labels_to_print": {
                "title": "Box IDs to print",
                "description": "UNIQUE labels: the box IDs from fba_list_shipment_boxes.",
            },
            "number_of_pallets": {"title": "Number of pallets"},
            "page_size": {
                "title": "Labels per page request",
                "description": "Non-partnered LTL: how many labels to return.",
            },
            "page_start_index": {
                "title": "Start index",
                "description": "Non-partnered LTL: the first label to return.",
            },
        },
        marketplaces=INBOUND_MARKETPLACES,
        availability=INBOUND_AVAILABILITY,
        result=_labels_result,
        outputs=DOCUMENT_OUTPUTS,
        artifact_output=FBA_DOCUMENT,
        pii=False,
    ),
    Method(
        operation_id="fba_get_bill_of_lading",
        display_name="FBA: get bill of lading",
        description=(
            "Stores the bill of lading of a partnered-carrier LTL/FTL shipment as a PDF file, once Amazon "
            "has issued it."
        ),
        section=SECTION,
        namespace="inbound_v0",
        amazon_operation="getBillOfLading",
        roles=ROLES,
        renamed={"shipment_id": "shipment_confirmation_id"},
        inputs={"shipment_confirmation_id": CONFIRMATION_ID},
        marketplaces=INBOUND_MARKETPLACES,
        availability=INBOUND_AVAILABILITY,
        result=_bill_of_lading_result,
        outputs=DOCUMENT_OUTPUTS,
        artifact_output=FBA_DOCUMENT,
        pii=False,
    ),
    # -- India ------------------------------------------------------------------
    Method(
        operation_id="fba_list_item_compliance_details",
        display_name="FBA India: list compliance details",
        description="Reads the compliance details (HSN code, declared value, tax rates) of up to 100 MSKUs.",
        section=INDIA_SECTION,
        namespace="inbound_2024_03_20",
        amazon_operation="listItemComplianceDetails",
        roles=INDIA_ROLES,
        inputs={"mskus": {"title": "MSKUs", "examples": [["MUG-BLUE-01"]]}},
        marketplaces=INDIA,
        availability=INDIA_AVAILABILITY,
    ),
    Method(
        operation_id="fba_update_item_compliance_details",
        display_name="FBA India: update compliance details",
        description=(
            "Sets the compliance details of one MSKU: HSN code, declared value and GST/cess rates. India "
            "requires them once per item before it is included in an inbound plan."
        ),
        section=INDIA_SECTION,
        namespace="inbound_2024_03_20",
        amazon_operation="updateItemComplianceDetails",
        roles=INDIA_ROLES,
        mutating=True,
        effect_kind="amazon_fba_compliance_details_update",
        inputs={
            "msku": {"title": "MSKU"},
            "tax_details": {
                "title": "Tax details",
                "examples": [
                    {
                        "hsn_code": "69120010",
                        "declared_value": {"amount": 499.0, "code": "INR"},
                        "tax_rates": [{"tax_type": "GST", "gst_rate": 18, "cess_rate": 0}],
                    }
                ],
            },
        },
        marketplaces=INDIA,
        availability=INDIA_AVAILABILITY,
        read_back=("fba_list_item_compliance_details",),
        note=ASYNC_NOTE,
    ),
    Method(
        operation_id="fba_generate_self_ship_appointment_slots",
        display_name="FBA: generate appointment slots",
        description=(
            "Asks Amazon for fulfillment-centre appointment slots of a self-ship shipment, by default for "
            "the next 42 days. The shipment needs its Amazon reference ID first (fba_get_shipment)."
        ),
        section=INDIA_SECTION,
        namespace="inbound_2024_03_20",
        amazon_operation="generateSelfShipAppointmentSlots",
        roles=ROLES,
        mutating=True,
        effect_kind="amazon_fba_appointment_slots_generation",
        inputs={"inbound_plan_id": PLAN_ID, "shipment_id": SHIPMENT_ID},
        marketplaces=INBOUND_MARKETPLACES,
        availability=APPOINTMENT_AVAILABILITY,
        read_back=("fba_get_self_ship_appointment_slots",),
        note=ASYNC_NOTE,
    ),
    Method(
        operation_id="fba_get_self_ship_appointment_slots",
        display_name="FBA: list appointment slots",
        description=(
            "Lists the appointment slots Amazon generated for a self-ship shipment. Returns one page and "
            "next_token."
        ),
        section=INDIA_SECTION,
        namespace="inbound_2024_03_20",
        amazon_operation="getSelfShipAppointmentSlots",
        roles=ROLES,
        inputs={
            "inbound_plan_id": PLAN_ID,
            "shipment_id": SHIPMENT_ID,
            "page_size": {"title": "Page size"},
        },
        marketplaces=INBOUND_MARKETPLACES,
        availability=APPOINTMENT_AVAILABILITY,
    ),
    Method(
        operation_id="fba_schedule_self_ship_appointment",
        display_name="FBA: schedule appointment",
        description=(
            "Books the appointment slot the workflow chose for a self-ship shipment. Rescheduling needs a "
            "reason comment."
        ),
        section=INDIA_SECTION,
        namespace="inbound_2024_03_20",
        amazon_operation="scheduleSelfShipAppointment",
        roles=ROLES,
        mutating=True,
        effect_kind="amazon_fba_appointment_scheduling",
        inputs={
            "inbound_plan_id": PLAN_ID,
            "shipment_id": SHIPMENT_ID,
            "slot_id": {"title": "Slot ID"},
        },
        marketplaces=INBOUND_MARKETPLACES,
        availability=APPOINTMENT_AVAILABILITY,
        read_back=("fba_get_shipment",),
    ),
    Method(
        operation_id="fba_cancel_self_ship_appointment",
        display_name="FBA: cancel appointment",
        description="Cancels the booked appointment of a self-ship shipment, with a reason comment.",
        section=INDIA_SECTION,
        namespace="inbound_2024_03_20",
        amazon_operation="cancelSelfShipAppointment",
        roles=ROLES,
        mutating=True,
        effect_kind="amazon_fba_appointment_cancellation",
        inputs={"inbound_plan_id": PLAN_ID, "shipment_id": SHIPMENT_ID},
        marketplaces=INBOUND_MARKETPLACES,
        availability=APPOINTMENT_AVAILABILITY,
        read_back=("fba_get_shipment",),
        note=ASYNC_NOTE,
    ),
    Method(
        operation_id="fba_get_delivery_challan_document",
        display_name="FBA India: get delivery challan",
        description=(
            "Stores the delivery challan of a Partnered Carrier Program shipment in India as a PDF file. "
            "The carrier must carry it with the goods."
        ),
        section=INDIA_SECTION,
        namespace="inbound_2024_03_20",
        amazon_operation="getDeliveryChallanDocument",
        roles=INDIA_ROLES,
        inputs={"inbound_plan_id": PLAN_ID, "shipment_id": SHIPMENT_ID},
        marketplaces=INDIA,
        availability=INDIA_AVAILABILITY + " Partnered Carrier Program shipments.",
        result=_challan_result,
        outputs=DOCUMENT_OUTPUTS,
        artifact_output=FBA_DOCUMENT,
        pii=False,
    ),
)

OPERATIONS = operations(METHODS)

__all__ = ["METHODS", "OPERATIONS"]
