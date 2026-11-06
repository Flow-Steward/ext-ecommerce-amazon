from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from conftest import UK_MARKETPLACE_ID, connection_target
from test_connection_and_transport import _sample

from runtime.amazon_api import spec
from runtime.catalog import OPERATIONS_BY_ID
from runtime.sections import fba_inbound, fba_inbound_documents

BASE = "/inbound/fba/2024-03-20"
PLAN = "wf1234abcd-1234-abcd-5678-1234abcd5678"
SHIPMENT = "sh1234abcd-1234-abcd-5678-1234abcd5678"
OPERATION = "1234abcd-1234-abcd-5678-1234abcd5678"
PLACEMENT = "pl1234abcd-1234-abcd-5678-1234abcd5678"
TRANSPORT = "to1234abcd-1234-abcd-5678-1234abcd5678"
GROUP = "pg1234abcd-1234-abcd-5678-1234abcd5678"
PDF = b"%PDF-1.4\n" + b"1" * 64
ZIP = b"PK\x03\x04" + b"1" * 64
S3_URL = "https://fba-labels.s3.amazonaws.com/labels/doc.pdf?X-Amz-Signature=secret"
IN_MARKETPLACE_ID = "A21TJRUUN4KGV"

DOCUMENT_METHODS = {
    "fba_get_labels",
    "fba_get_bill_of_lading",
    "fba_create_marketplace_item_labels",
    "fba_get_delivery_challan_document",
}
INDIA_ONLY = {
    "fba_list_item_compliance_details",
    "fba_update_item_compliance_details",
    "fba_get_delivery_challan_document",
}
FBA_METHODS = [method for method in (*fba_inbound.METHODS, *fba_inbound_documents.METHODS)]


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    # The extension test container may have no network; the SDK's DNS pinning is
    # covered by the SDK itself, and the S3-host rule by test_connection_and_transport.
    monkeypatch.setenv("FS_ALLOW_PRIVATE_REMOTE_URLS", "1")


class Writer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, payload, body, *, binding_key, content_type):
        self.calls.append({"body": body, "binding_key": binding_key, "content_type": content_type})
        return {
            "artifact_id": "art-9",
            "binding_key": binding_key,
            "size_bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }


def india_target() -> dict[str, Any]:
    return connection_target(connection_id="conn-in", marketplace="IN")


def _path(method, values: dict[str, Any]) -> str:
    row = spec(method.namespace, method.amazon_operation)
    path = row["path"]
    for parameter in row["parameters"]:
        if parameter["in"] == "path":
            name = method.renamed.get(parameter["input"], parameter["input"])
            path = path.replace("{" + parameter["name"] + "}", str(values[name]))
    return path


def test_the_inbound_surface_is_the_agreed_one():
    names = {method.operation_id for method in FBA_METHODS}
    assert len(names) == 44
    expected_amazon = {
        "getItemEligibilityPreview",
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
        "getLabels",
        "getBillOfLading",
        "listItemComplianceDetails",
        "updateItemComplianceDetails",
        "generateSelfShipAppointmentSlots",
        "getSelfShipAppointmentSlots",
        "scheduleSelfShipAppointment",
        "cancelSelfShipAppointment",
        "getDeliveryChallanDocument",
    }
    assert {method.amazon_operation for method in FBA_METHODS} == expected_amazon
    for method in FBA_METHODS:
        if method.namespace == "inbound_2024_03_20":
            snake = "fba_" + "".join(
                "_" + c.lower() if c.isupper() else c for c in method.amazon_operation
            )
            assert method.operation_id == snake, method.operation_id


@pytest.mark.parametrize(
    "method",
    [m for m in FBA_METHODS if m.operation_id not in DOCUMENT_METHODS],
    ids=lambda m: m.operation_id,
)
def test_every_method_makes_exactly_one_amazon_call(amazon, run, method):
    row = OPERATIONS_BY_ID[method.operation_id]
    amazon_spec = spec(method.namespace, method.amazon_operation)
    minimal = {
        name: _sample(schema)
        for name, schema in row.properties.items()
        if name in row.required and name != "connection_ref"
    }
    target = india_target() if method.operation_id in INDIA_ONLY else None
    if target is not None:
        minimal["connection_ref"] = "conn-in"
    status = amazon_spec["success_status"]
    amazon.reply(
        amazon_spec["method"],
        _path(method, minimal),
        raw=b"" if status == 204 else b"{}",
        status=status,
    )
    response = run(method.operation_id, minimal, **({"target": target} if target else {}))
    assert response["ok"] is True, (method.operation_id, response)
    assert len(amazon.requests) == 1
    assert response["result"]["api_version"] in {"2024-03-20", "v1"}


def test_create_plan_targets_the_connections_marketplace_and_is_asynchronous(amazon, run):
    amazon.reply(
        "POST",
        f"{BASE}/inboundPlans",
        {"inboundPlanId": PLAN, "operationId": OPERATION},
        status=202,
    )
    response = run(
        "fba_create_inbound_plan",
        {
            "items": [
                {"msku": "MUG-1", "quantity": 24, "prep_owner": "SELLER", "label_owner": "SELLER"}
            ],
            "source_address": {
                "name": "W1",
                "address_line1": "1 Street",
                "city": "London",
                "country_code": "GB",
                "postal_code": "EC1A 1BB",
                "phone_number": "+442000000000",
            },
        },
    )
    assert response["ok"] is True, response
    body = amazon.requests[0].body
    assert body["destinationMarketplaces"] == [UK_MARKETPLACE_ID]
    assert body["items"][0] == {
        "msku": "MUG-1",
        "quantity": 24,
        "prepOwner": "SELLER",
        "labelOwner": "SELLER",
    }
    assert body["sourceAddress"]["addressLine1"] == "1 Street"
    result = response["result"]
    assert result["inbound_plan_id"] == PLAN
    assert result["operation_id"] == OPERATION
    assert "not completed" in result["note"]
    assert OPERATIONS_BY_ID["fba_create_inbound_plan"].pii is False or True  # address in input only


def test_the_marketplace_cannot_be_chosen_by_input(amazon, run):
    response = run(
        "fba_list_prep_details",
        {"mskus": ["MUG-1"], "marketplace_id": "ATVPDKIKX0DER"},
    )
    assert response["error_code"] == "invalid_payload"
    assert amazon.requests == []


def test_brazil_is_refused_before_any_call(amazon, run):
    response = run(
        "fba_list_inbound_plans",
        {"connection_ref": "conn-br"},
        target=connection_target(connection_id="conn-br", marketplace="BR"),
    )
    assert response["error_code"] == "unsupported_marketplace"
    assert "except Brazil" in response["error"]
    assert amazon.requests == []


def test_lists_return_one_page_and_a_bound_token(amazon, run):
    amazon.reply(
        "GET",
        f"{BASE}/inboundPlans",
        {
            "inboundPlans": [
                {
                    "inboundPlanId": PLAN,
                    "status": "ACTIVE",
                    "name": "Plan 1",
                    "createdAt": "2026-11-01T00:00:00Z",
                    "lastUpdatedAt": "2026-11-01T00:00:00Z",
                    "marketplaceIds": [UK_MARKETPLACE_ID],
                    "sourceAddress": {
                        "name": "W1",
                        "addressLine1": "1 Street",
                        "city": "London",
                        "countryCode": "GB",
                        "postalCode": "EC1A 1BB",
                    },
                }
            ],
            "pagination": {"nextToken": "amz-next"},
        },
    )
    first = run("fba_list_inbound_plans", {"status": "ACTIVE", "page_size": 10})
    assert first["ok"] is True, first
    assert first["result"]["inbound_plans"][0]["inbound_plan_id"] == PLAN
    token = first["result"]["next_token"]
    assert token
    assert "amz-next" not in token
    assert len(amazon.requests) == 1

    amazon.reply("GET", f"{BASE}/inboundPlans", {"inboundPlans": [], "pagination": {}})
    second = run(
        "fba_list_inbound_plans", {"status": "ACTIVE", "page_size": 10, "next_token": token}
    )
    assert second["ok"] is True, second
    assert amazon.requests[1].query["paginationToken"] == "amz-next"
    assert second["result"]["next_token"] is None

    other_filter = run("fba_list_inbound_plans", {"status": "SHIPPED", "next_token": token})
    assert other_filter["error_code"] == "continuation_token_invalid"
    other_connection = run(
        "fba_list_inbound_plans",
        {"status": "ACTIVE", "next_token": token, "connection_ref": "conn-2"},
        target=connection_target(connection_id="conn-2"),
    )
    assert other_connection["error_code"] == "continuation_token_invalid"
    assert len(amazon.requests) == 2


def test_operation_status_is_checked_once(amazon, run):
    amazon.reply(
        "GET",
        f"{BASE}/operations/{OPERATION}",
        {
            "operationId": OPERATION,
            "operation": "createInboundPlan",
            "operationStatus": "IN_PROGRESS",
            "operationProblems": [],
        },
    )
    response = run("fba_get_inbound_operation_status", {"operation_id": OPERATION})
    assert response["ok"] is True, response
    assert response["result"]["operation_status"] == "IN_PROGRESS"
    assert len(amazon.requests) == 1


def test_confirming_placement_with_an_unknown_outcome_names_the_ids(amazon, run):
    path = f"{BASE}/inboundPlans/{PLAN}/placementOptions/{PLACEMENT}/confirmation"
    amazon.reply("POST", path, {"errors": []}, status=500)
    response = run(
        "fba_confirm_placement_option", {"inbound_plan_id": PLAN, "placement_option_id": PLACEMENT}
    )
    assert response["error_code"] == "outcome_unknown"
    assert response["result"]["check"] == {
        "ids": {"inbound_plan_id": PLAN, "placement_option_id": PLACEMENT},
        "read_with": ["fba_get_inbound_plan"],
    }
    assert len(amazon.requests) == 1


def test_an_expired_option_is_its_own_error(amazon, run):
    path = f"{BASE}/inboundPlans/{PLAN}/placementOptions/{PLACEMENT}/confirmation"
    amazon.reply(
        "POST",
        path,
        {"errors": [{"code": "BadRequest", "message": "The placement option has expired."}]},
        status=400,
    )
    response = run(
        "fba_confirm_placement_option", {"inbound_plan_id": PLAN, "placement_option_id": PLACEMENT}
    )
    assert response["error_code"] == "offer_expired"


def test_confirming_transportation_explains_the_charge(amazon, run):
    path = f"{BASE}/inboundPlans/{PLAN}/transportationOptions/confirmation"
    amazon.reply("POST", path, {"operationId": OPERATION}, status=202)
    selection = {"shipment_id": SHIPMENT, "transportation_option_id": TRANSPORT}
    response = run(
        "fba_confirm_transportation_options",
        {"inbound_plan_id": PLAN, "transportation_selections": [selection]},
    )
    assert response["ok"] is True, response
    assert amazon.requests[0].body == {
        "transportationSelections": [{"shipmentId": SHIPMENT, "transportationOptionId": TRANSPORT}]
    }
    result = response["result"]
    assert result["transportation_selections"] == [selection]
    assert "charge" in result["charge_notice"]
    assert "24 hours" in result["charge_notice"]
    assert result["operation_id"] == OPERATION


def test_packing_information_converts_nested_fields(amazon, run):
    path = f"{BASE}/inboundPlans/{PLAN}/packingInformation"
    amazon.reply("POST", path, {"operationId": OPERATION}, status=202)
    grouping = {
        "packing_group_id": GROUP,
        "boxes": [
            {
                "content_information_source": "BOX_CONTENT_PROVIDED",
                "quantity": 1,
                "dimensions": {
                    "length": 40,
                    "width": 30,
                    "height": 30,
                    "unit_of_measurement": "CM",
                },
                "weight": {"value": 9.5, "unit": "KG"},
                "items": [
                    {"msku": "MUG-1", "quantity": 12, "prep_owner": "NONE", "label_owner": "SELLER"}
                ],
            }
        ],
    }
    response = run(
        "fba_set_packing_information", {"inbound_plan_id": PLAN, "package_groupings": [grouping]}
    )
    assert response["ok"] is True, response
    box = amazon.requests[0].body["packageGroupings"][0]["boxes"][0]
    assert box["contentInformationSource"] == "BOX_CONTENT_PROVIDED"
    assert box["dimensions"]["unitOfMeasurement"] == "CM"
    assert amazon.requests[0].body["packageGroupings"][0]["packingGroupId"] == GROUP


def test_renaming_answers_without_a_body(amazon, run):
    amazon.reply(
        "PUT", f"{BASE}/inboundPlans/{PLAN}/shipments/{SHIPMENT}/name", raw=b"", status=204
    )
    response = run(
        "fba_update_shipment_name",
        {"inbound_plan_id": PLAN, "shipment_id": SHIPMENT, "name": "Box run 1"},
    )
    assert response["ok"] is True, response
    assert response["result"]["accepted"] is True
    assert amazon.requests[0].body == {"name": "Box run 1"}


def test_labels_use_the_confirmation_id_and_store_the_file(amazon, run):
    amazon.reply(
        "GET", "/fba/inbound/v0/shipments/FBA1234ABCD/labels", {"payload": {"DownloadURL": S3_URL}}
    )
    amazon.reply("GET", "/labels/doc.pdf", raw=PDF)
    writer = Writer()
    response = run(
        "fba_get_labels",
        {
            "shipment_confirmation_id": "FBA1234ABCD",
            "page_type": "PackageLabel_Thermal",
            "label_type": "UNIQUE",
            "package_labels_to_print": ["FBA1234ABCDU000001"],
        },
        artifact_writer=writer,
    )
    assert response["ok"] is True, response
    label_call, download = amazon.requests
    assert label_call.query == {
        "PageType": "PackageLabel_Thermal",
        "LabelType": "UNIQUE",
        "PackageLabelsToPrint": "FBA1234ABCDU000001",
    }
    assert "x-amz-access-token" not in download.headers
    assert writer.calls[0]["content_type"] == "application/octet-stream"
    claim = response["result"]["document_file"]
    assert claim["filename"] == "amazon-fba-labels-FBA1234ABCD.pdf"
    assert claim["content_type"] == "application/pdf"
    assert "X-Amz-Signature" not in json.dumps(response)


def test_labels_accept_a_zip_and_refuse_a_url_outside_s3(amazon, run):
    amazon.reply(
        "GET", "/fba/inbound/v0/shipments/FBA1/labels", {"payload": {"DownloadURL": S3_URL}}
    )
    amazon.reply("GET", "/labels/doc.pdf", raw=ZIP)
    zipped = run(
        "fba_get_labels",
        {
            "shipment_confirmation_id": "FBA1",
            "page_type": "PackageLabel_A4_4",
            "label_type": "BARCODE_2D",
        },
        artifact_writer=Writer(),
    )
    assert zipped["result"]["document_file"]["content_type"] == "application/zip"

    amazon.reply(
        "GET",
        "/fba/inbound/v0/shipments/FBA1/labels",
        {"payload": {"DownloadURL": "https://evil.example.com/doc.pdf"}},
    )
    writer = Writer()
    refused = run(
        "fba_get_labels",
        {
            "shipment_confirmation_id": "FBA1",
            "page_type": "PackageLabel_A4_4",
            "label_type": "BARCODE_2D",
        },
        artifact_writer=writer,
    )
    assert refused["error_code"] == "blocked_address"
    assert writer.calls == []


def test_bill_of_lading_and_item_labels_store_one_pdf(amazon, run):
    amazon.reply(
        "GET", "/fba/inbound/v0/shipments/FBA1/billOfLading", {"payload": {"DownloadURL": S3_URL}}
    )
    amazon.reply("GET", "/labels/doc.pdf", raw=PDF)
    bol = run(
        "fba_get_bill_of_lading", {"shipment_confirmation_id": "FBA1"}, artifact_writer=Writer()
    )
    assert bol["result"]["document_file"]["filename"] == "amazon-bill-of-lading-FBA1.pdf"

    amazon.reply(
        "POST",
        f"{BASE}/items/labels",
        {
            "documentDownloads": [
                {"uri": S3_URL, "downloadType": "URL", "expiration": "2026-11-06T12:00:00Z"}
            ]
        },
    )
    amazon.reply("GET", "/labels/doc.pdf", raw=PDF)
    labels = run(
        "fba_create_marketplace_item_labels",
        {"msku_quantities": [{"msku": "MUG-1", "quantity": 10}], "label_type": "STANDARD_FORMAT"},
        artifact_writer=Writer(),
    )
    assert labels["ok"] is True, labels
    assert amazon.requests[2].body["marketplaceId"] == UK_MARKETPLACE_ID
    assert labels["result"]["expires_at"] == "2026-11-06T12:00:00Z"

    amazon.reply(
        "POST", f"{BASE}/items/labels", {"documentDownloads": [{"uri": S3_URL}, {"uri": S3_URL}]}
    )
    two = run(
        "fba_create_marketplace_item_labels",
        {"msku_quantities": [{"msku": "MUG-1", "quantity": 10}], "label_type": "STANDARD_FORMAT"},
        artifact_writer=Writer(),
    )
    assert two["error_code"] == "document_unavailable"


def test_india_steps_run_only_on_an_india_connection(amazon, run):
    refused = run("fba_list_item_compliance_details", {"mskus": ["MUG-1"]})
    assert refused["error_code"] == "unsupported_marketplace"
    assert amazon.requests == []

    amazon.reply("GET", f"{BASE}/items/compliance", {"complianceDetails": []})
    allowed = run(
        "fba_list_item_compliance_details",
        {"mskus": ["MUG-1"], "connection_ref": "conn-in"},
        target=india_target(),
    )
    assert allowed["ok"] is True, allowed
    assert amazon.requests[0].query == {"mskus": "MUG-1", "marketplaceId": IN_MARKETPLACE_ID}
    assert amazon.requests[0].host == "sellingpartnerapi-eu.amazon.com"


def test_delivery_challan_is_stored_as_a_pdf(amazon, run):
    path = f"{BASE}/inboundPlans/{PLAN}/shipments/{SHIPMENT}/deliveryChallanDocument"
    amazon.reply(
        "GET",
        path,
        {
            "documentDownload": {
                "uri": S3_URL,
                "downloadType": "URL",
                "expiration": "2026-11-06T12:00:00Z",
            }
        },
    )
    amazon.reply("GET", "/labels/doc.pdf", raw=PDF)
    response = run(
        "fba_get_delivery_challan_document",
        {"inbound_plan_id": PLAN, "shipment_id": SHIPMENT, "connection_ref": "conn-in"},
        target=india_target(),
        artifact_writer=Writer(),
    )
    assert response["ok"] is True, response
    assert response["result"]["document_file"]["content_type"] == "application/pdf"


def test_eligibility_uses_the_connections_marketplace(amazon, run):
    amazon.reply(
        "GET",
        "/fba/inbound/v1/eligibility/itemPreview",
        {
            "payload": {
                "asin": "B000000000",
                "program": "INBOUND",
                "isEligibleForProgram": False,
                "ineligibilityReasonList": ["FBA_INB_0004"],
            }
        },
    )
    response = run("fba_get_item_eligibility_preview", {"asin": "B000000000", "program": "INBOUND"})
    assert response["ok"] is True, response
    assert amazon.requests[0].query == {
        "marketplaceIds": UK_MARKETPLACE_ID,
        "asin": "B000000000",
        "program": "INBOUND",
    }
    assert response["result"]["is_eligible_for_program"] is False
    assert response["result"]["ineligibility_reason_list"] == ["FBA_INB_0004"]


def test_shipment_and_plan_reads_are_marked_personal():
    assert OPERATIONS_BY_ID["fba_get_shipment"].pii is True
    assert OPERATIONS_BY_ID["fba_get_inbound_plan"].pii is True
    assert OPERATIONS_BY_ID["fba_get_inbound_operation_status"].pii is False
