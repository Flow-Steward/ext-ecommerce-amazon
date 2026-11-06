from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from conftest import EXTERNAL_EFFECT_ID, connection_target

RATES = "/shipping/v2/shipments/rates"
PURCHASE = "/shipping/v2/shipments"
ADDITIONAL = "/shipping/v2/shipments/additionalInputs/schema"
DOCUMENTS = "/shipping/v2/shipments/SHIP-1/documents"
TRACKING = "/shipping/v2/tracking"
CANCEL = "/shipping/v2/shipments/SHIP-1/cancel"

PDF = b"%PDF-1.7\n" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
ZPL = b"^XA^FO50,50^ADN,36,20^FDLabel^FS^XZ"

SHIP_FROM = {
    "name": "Warehouse 1",
    "address_line1": "1 Example Street",
    "city": "London",
    "state_or_region": "London",
    "postal_code": "EC1A 1BB",
    "country_code": "GB",
}
PACKAGE = {
    "package_client_reference_id": "pkg-1",
    "dimensions": {"length": 30, "width": 20, "height": 10, "unit": "CENTIMETER"},
    "weight": {"value": 1.2, "unit": "KILOGRAM"},
    "insured_value": {"value": 25, "unit": "GBP"},
    "items": [
        {"item_identifier": "11111", "quantity": 1, "item_value": {"value": 25, "unit": "GBP"}}
    ],
}
RATES_INPUT = {
    "amazon_order_id": "202-1111111-1111111",
    "ship_from": SHIP_FROM,
    "packages": [PACKAGE],
}
SPEC = {
    "format": "PDF",
    "size": {"width": 4, "length": 6, "unit": "INCH"},
    "need_file_joining": False,
    "requested_document_types": ["LABEL"],
}
PURCHASE_INPUT = {
    "request_token": "rt-1",
    "rate_id": "rate-1",
    "requested_document_specification": SPEC,
}


class Writer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, payload, body, *, binding_key, content_type):
        self.calls.append({"body": body, "binding_key": binding_key, "content_type": content_type})
        return {
            "artifact_id": "art-1",
            "binding_key": binding_key,
            "size_bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }


def rates_payload() -> dict[str, Any]:
    return {
        "payload": {
            "requestToken": "rt-1",
            "rates": [
                {
                    "rateId": "rate-1",
                    "carrierId": "ROYAL_MAIL",
                    "carrierName": "Royal Mail",
                    "serviceId": "RM_TRACKED_48",
                    "serviceName": "Tracked 48",
                    "totalCharge": {"value": 3.45, "unit": "GBP"},
                    "promise": {
                        "deliveryWindow": {
                            "start": "2026-11-08T00:00:00Z",
                            "end": "2026-11-09T00:00:00Z",
                        }
                    },
                    "supportedDocumentSpecifications": [
                        {
                            "format": "PDF",
                            "size": {"width": 4, "length": 6, "unit": "INCH"},
                            "printOptions": [
                                {
                                    "supportedDPIs": [203, 300],
                                    "supportedPageLayouts": ["LEFT"],
                                    "supportedFileJoiningOptions": [False],
                                    "supportedDocumentDetails": [
                                        {"name": "LABEL", "isMandatory": True}
                                    ],
                                }
                            ],
                        }
                    ],
                    "requiresAdditionalInputs": False,
                }
            ],
            "ineligibleRates": [],
        }
    }


def documents_payload(
    contents: bytes, *, document_format: str = "PDF", types=("LABEL",)
) -> dict[str, Any]:
    return {
        "payload": {
            "shipmentId": "SHIP-1",
            "packageDocumentDetail": {
                "packageClientReferenceId": "pkg-1",
                "trackingId": "TRK-1",
                "packageDocuments": [
                    {
                        "type": kind,
                        "format": document_format,
                        "contents": base64.b64encode(contents).decode(),
                    }
                    for kind in types
                ],
            },
        }
    }


def test_rates_send_an_amazon_order_with_the_marketplace_business(amazon, run):
    amazon.reply("POST", RATES, rates_payload(), headers={"x-amzn-RequestId": "req-123"})
    response = run("get_shipping_rates", RATES_INPUT)
    assert response["ok"] is True, response
    request = amazon.requests[0]
    assert request.headers["x-amzn-shipping-business-id"] == "AmazonShipping_UK"
    assert request.body["channelDetails"] == {
        "channelType": "AMAZON",
        "amazonOrderDetails": {"orderId": "202-1111111-1111111"},
    }
    assert "shipTo" not in request.body
    assert request.body["packages"][0]["packageClientReferenceId"] == "pkg-1"
    assert request.body["packages"][0]["items"][0]["itemIdentifier"] == "11111"
    assert request.body["shipFrom"]["addressLine1"] == "1 Example Street"
    result = response["result"]
    assert result["request_token"] == "rt-1"
    rate = result["rates"][0]
    assert rate["rate_id"] == "rate-1"
    assert rate["total_charge"] == {"value": "3.45", "unit": "GBP"}
    assert rate["supported_document_specifications"][0]["print_options"][0]["supported_dpis"] == [
        203,
        300,
    ]
    assert "not the connection's access token" in result["note"]
    assert result["amazon_operation"] == "getRates"
    assert result["api_version"] == "v2"
    assert result["amazon_request_id"] == "req-123"


def test_rates_use_each_connections_own_business(amazon, run):
    amazon.reply("POST", RATES, rates_payload())
    us_input = {
        **RATES_INPUT,
        "ship_from": {**SHIP_FROM, "country_code": "US"},
        "connection_ref": "conn-us",
    }
    response = run(
        "get_shipping_rates",
        us_input,
        target=connection_target(connection_id="conn-us", marketplace="US"),
    )
    assert response["ok"] is True, response
    assert amazon.requests[0].headers["x-amzn-shipping-business-id"] == "AmazonShipping_US"
    assert amazon.requests[0].host == "sellingpartnerapi-na.amazon.com"


def test_rates_are_refused_outside_the_documented_marketplaces(amazon, run):
    response = run(
        "get_shipping_rates",
        {
            **RATES_INPUT,
            "connection_ref": "conn-de",
            "ship_from": {**SHIP_FROM, "country_code": "DE"},
        },
        target=connection_target(connection_id="conn-de", marketplace="DE"),
    )
    assert response["error_code"] == "unsupported_marketplace"
    assert "Germany" in response["error"]
    assert amazon.requests == []


def test_rates_refuse_a_foreign_origin_and_a_missing_order_item(amazon, run):
    foreign = run(
        "get_shipping_rates", {**RATES_INPUT, "ship_from": {**SHIP_FROM, "country_code": "FR"}}
    )
    assert foreign["error_code"] == "unsupported_marketplace"
    item = {**PACKAGE, "items": [{"quantity": 1, "item_value": {"value": 25, "unit": "GBP"}}]}
    missing = run("get_shipping_rates", {**RATES_INPUT, "packages": [item]})
    assert missing["error_code"] == "invalid_payload"
    assert "order_item_id" in missing["error"]
    two = run("get_shipping_rates", {**RATES_INPUT, "packages": [PACKAGE, PACKAGE]})
    assert two["error_code"] == "invalid_payload"
    assert amazon.requests == []


def test_purchase_names_the_offer_and_never_returns_label_bytes(amazon, run):
    amazon.reply(
        "POST",
        PURCHASE,
        {
            "payload": {
                "shipmentId": "SHIP-1",
                "packageDocumentDetails": [
                    {
                        "packageClientReferenceId": "pkg-1",
                        "trackingId": "TRK-1",
                        "packageDocuments": [
                            {
                                "type": "LABEL",
                                "format": "PDF",
                                "contents": base64.b64encode(PDF).decode(),
                            }
                        ],
                    }
                ],
                "promise": {},
            }
        },
    )
    response = run(
        "purchase_shipping_shipment",
        {**PURCHASE_INPUT, "additional_inputs": {"senderTaxId": "GB123", "custom_field": "x"}},
    )
    assert response["ok"] is True, response
    request = amazon.requests[0]
    assert request.headers["x-amzn-shipping-business-id"] == "AmazonShipping_UK"
    assert request.headers["x-amzn-idempotencykey"] == EXTERNAL_EFFECT_ID
    assert request.body["requestToken"] == "rt-1"
    assert request.body["rateId"] == "rate-1"
    assert request.body["requestedDocumentSpecification"]["needFileJoining"] is False
    # Additional input keys are Amazon's own schema names and are sent unchanged.
    assert request.body["additionalInputs"] == {"senderTaxId": "GB123", "custom_field": "x"}
    result = response["result"]
    assert result["shipment_id"] == "SHIP-1"
    assert result["rate_id"] == "rate-1"
    assert result["packages"] == [
        {
            "package_client_reference_id": "pkg-1",
            "tracking_id": "TRK-1",
            "documents": [{"type": "LABEL", "format": "PDF"}],
        }
    ]
    assert "charged" in result["note"]
    assert base64.b64encode(PDF).decode() not in json.dumps(response)
    assert result["external_effect_status"] == "succeeded"


def test_an_expired_offer_is_reported_and_never_requoted(amazon, run):
    amazon.reply(
        "POST",
        PURCHASE,
        {"errors": [{"code": "TOKEN_EXPIRED", "message": "Token expired"}]},
        status=400,
        headers={"x-amzn-RequestId": "req-9"},
    )
    response = run("purchase_shipping_shipment", PURCHASE_INPUT)
    assert response["error_code"] == "offer_expired"
    assert response["result"]["amazon_request_id"] == "req-9"
    assert response["result"]["amazon_errors"][0]["code"] == "TOKEN_EXPIRED"
    assert [request.path for request in amazon.requests] == [PURCHASE]


def test_an_unknown_purchase_outcome_names_what_to_check_and_is_not_repeated(amazon, run):
    amazon.reply("POST", PURCHASE, {"errors": []}, status=503)
    response = run("purchase_shipping_shipment", PURCHASE_INPUT)
    assert response["error_code"] == "outcome_unknown"
    assert response["definitely_no_external_effect"] is False
    check = response["result"]["check"]
    assert check["ids"] == {"request_token": "rt-1", "rate_id": "rate-1"}
    assert check["read_with"] == ["get_order"]
    assert len(amazon.requests) == 1


def test_a_throttled_purchase_is_retried_because_amazon_did_not_process_it(amazon, run):
    amazon.reply("POST", PURCHASE, {"errors": [{"code": "QuotaExceeded"}]}, status=429)
    amazon.reply(
        "POST",
        PURCHASE,
        {"payload": {"shipmentId": "SHIP-1", "packageDocumentDetails": [], "promise": {}}},
    )
    response = run("purchase_shipping_shipment", PURCHASE_INPUT)
    assert response["ok"] is True, response
    assert len(amazon.requests) == 2


def test_purchase_in_test_mode_sends_nothing(amazon, run):
    response = run("purchase_shipping_shipment", PURCHASE_INPUT, test_mode=True)
    assert response["result"]["external_effect_status"] == "suppressed"
    assert amazon.requests == []


def test_additional_inputs_schema_is_returned_unchanged(amazon, run):
    schema = {
        "type": "object",
        "properties": {"senderTaxId": {"type": "string"}},
        "required": ["senderTaxId"],
    }
    amazon.reply("GET", ADDITIONAL, {"payload": schema})
    response = run("get_shipping_additional_inputs", {"request_token": "rt-1", "rate_id": "rate-1"})
    assert response["ok"] is True, response
    assert response["result"]["additional_inputs_schema"] == schema
    assert amazon.requests[0].query == {"requestToken": "rt-1", "rateId": "rate-1"}


def test_documents_store_the_label_with_its_real_name_and_type(amazon, run):
    amazon.reply("GET", DOCUMENTS, documents_payload(PDF, types=("LABEL", "RECEIPT")))
    writer = Writer()
    response = run(
        "get_shipping_documents",
        {"shipment_id": "SHIP-1", "package_client_reference_id": "pkg-1", "format": "PDF"},
        artifact_writer=writer,
    )
    assert response["ok"] is True, response
    assert writer.calls == [
        {
            "body": PDF,
            "binding_key": "amazon_shipping_document",
            "content_type": "application/octet-stream",
        }
    ]
    claim = response["result"]["document_file"]
    assert claim == {
        "artifact_id": "art-1",
        "binding_key": "amazon_shipping_document",
        "filename": "amazon-label-pkg-1.pdf",
        "content_type": "application/pdf",
        "size_bytes": len(PDF),
        "sha256": hashlib.sha256(PDF).hexdigest(),
    }
    assert response["result"]["other_document_types"] == ["RECEIPT"]
    assert amazon.requests[0].query == {"packageClientReferenceId": "pkg-1", "format": "PDF"}
    assert base64.b64encode(PDF).decode() not in json.dumps(response)


def test_documents_store_zpl_as_text_and_refuse_a_mislabelled_file(amazon, run):
    amazon.reply("GET", DOCUMENTS, documents_payload(ZPL, document_format="ZPL"))
    zpl = run(
        "get_shipping_documents",
        {
            "shipment_id": "SHIP-1",
            "package_client_reference_id": "pkg-1",
            "format": "ZPL",
            "dpi": 300,
        },
        artifact_writer=Writer(),
    )
    assert zpl["ok"] is True, zpl
    assert zpl["result"]["document_file"]["content_type"] == "text/plain"
    assert zpl["result"]["document_file"]["filename"].endswith(".zpl")

    amazon.reply("GET", DOCUMENTS, documents_payload(PNG, document_format="PDF"))
    writer = Writer()
    wrong = run(
        "get_shipping_documents",
        {"shipment_id": "SHIP-1", "package_client_reference_id": "pkg-1"},
        artifact_writer=writer,
    )
    assert wrong["error_code"] == "document_unavailable"
    assert writer.calls == []


def test_documents_report_a_missing_document_type(amazon, run):
    amazon.reply("GET", DOCUMENTS, documents_payload(PDF, types=("LABEL",)))
    response = run(
        "get_shipping_documents",
        {
            "shipment_id": "SHIP-1",
            "package_client_reference_id": "pkg-1",
            "document_type": "CUSTOM_FORM",
        },
        artifact_writer=Writer(),
    )
    assert response["error_code"] == "not_found"
    assert "LABEL" in response["error"]


def test_tracking_reads_and_changes_nothing(amazon, run):
    amazon.reply(
        "GET",
        TRACKING,
        {
            "payload": {
                "trackingId": "TRK-1",
                "alternateLegTrackingId": "",
                "eventHistory": [{"eventCode": "PickupDone", "eventTime": "2026-11-07T10:00:00Z"}],
                "promisedDeliveryDate": "2026-11-09T00:00:00Z",
                "summary": {"status": "InTransit"},
            }
        },
    )
    response = run("get_shipping_tracking", {"tracking_id": "TRK-1", "carrier_id": "ROYAL_MAIL"})
    assert response["ok"] is True, response
    assert response["result"]["summary"] == {"status": "InTransit"}
    assert response["result"]["event_history"][0]["event_code"] == "PickupDone"
    assert [(request.method, request.query) for request in amazon.requests] == [
        ("GET", {"trackingId": "TRK-1", "carrierId": "ROYAL_MAIL"})
    ]


def test_cancel_reports_an_ineligible_state(amazon, run):
    amazon.reply("PUT", CANCEL, {"payload": {}})
    ok = run("cancel_shipping_shipment", {"shipment_id": "SHIP-1"})
    assert ok["ok"] is True, ok
    assert ok["result"]["cancelled"] is True
    amazon.reply(
        "PUT",
        CANCEL,
        {
            "errors": [
                {
                    "code": "InvalidInput",
                    "message": "x",
                    "details": "Cancellation failed as shipment is in ineligible state.",
                }
            ]
        },
        status=400,
    )
    refused = run("cancel_shipping_shipment", {"shipment_id": "SHIP-1"})
    assert refused["error_code"] == "invalid_state"
