"""Regressions for the independent review of the logistics batch (2026-11-06)."""

from __future__ import annotations

import base64
import hashlib

from conftest import SELLER_ID, connection_target

from runtime.documents import detect_format

PLAN = "wf1234abcd-1234-abcd-5678-1234abcd5678"


def writer(calls):
    def write(payload, body, *, binding_key, content_type):
        calls.append(body)
        return {
            "artifact_id": "art-1",
            "binding_key": binding_key,
            "size_bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }

    return write


def test_mskus_are_sent_as_repeated_parameters(amazon, run):
    amazon.reply("GET", "/inbound/fba/2024-03-20/items/prepDetails", {"mskuPrepDetails": []})
    response = run("fba_list_prep_details", {"mskus": ["A", "B,C"]})
    assert response["ok"] is True, response
    query = amazon.requests[0].url.split("?", 1)[1]
    assert "mskus=A&mskus=B%2CC" in query


def test_a_refused_deletion_is_not_a_success(amazon, run):
    path = f"/listings/2021-08-01/items/{SELLER_ID}/MUG-1"
    amazon.reply(
        "DELETE",
        path,
        {
            "sku": "MUG-1",
            "status": "INVALID",
            "submissionId": "s",
            "issues": [{"code": "X", "message": "m", "severity": "ERROR"}],
        },
    )
    refused = run("delete_listing", {"sku": "MUG-1"})
    assert refused["ok"] is True, refused
    assert refused["result"]["status"] == "INVALID"
    assert refused["result"]["external_effect_status"] == "failed"
    assert refused["result"]["definitely_no_external_effect"] is True
    assert "ACCEPTED" not in refused["result"]["note"]
    amazon.reply("DELETE", path, {"sku": "MUG-1"})
    unknown = run("delete_listing", {"sku": "MUG-1"})
    assert unknown["error_code"] == "outcome_unknown"


def test_new_enum_values_and_nested_nulls_keep_the_output_contract(amazon, run):
    amazon.reply(
        "GET",
        "/shipping/v2/tracking",
        {
            "payload": {
                "trackingId": "T",
                "alternateLegTrackingId": "",
                "eventHistory": [
                    {"eventCode": "PickupScheduled", "eventTime": "2026-11-07T10:00:00Z"}
                ],
                "promisedDeliveryDate": "2026-11-09T00:00:00Z",
                "summary": {"status": "SomethingNew"},
            }
        },
    )
    assert run("get_shipping_tracking", {"tracking_id": "T", "carrier_id": "C"})["ok"] is True
    amazon.reply(
        "GET",
        f"/inbound/fba/2024-03-20/inboundPlans/{PLAN}/boxes",
        {
            "boxes": [
                {
                    "packageId": "pk-1",
                    "boxId": "FBA1U1",
                    "weight": None,
                    "quantity": 1,
                    "contentInformationSource": "NEW_SOURCE",
                }
            ],
            "pagination": {},
        },
    )
    assert run("fba_list_inbound_plan_boxes", {"inbound_plan_id": PLAN})["ok"] is True


def test_utf8_and_bom_zpl_labels_are_accepted():
    assert (
        detect_format("^XA^CI28^FO50,50^FDHélène Dupont^FS^XZ".encode(), allowed=("ZPL",)) == "ZPL"
    )
    assert detect_format(b"\xef\xbb\xbf^XA^FDx^FS^XZ", allowed=("ZPL",)) == "ZPL"
    assert detect_format(b"\n%PDF-1.7\n" + b"0" * 32, allowed=("PDF",)) == "PDF"


def test_an_expired_pagination_token_is_a_token_error(amazon, run):
    amazon.reply(
        "GET",
        "/inbound/fba/2024-03-20/inboundPlans",
        {"errors": [{"code": "BadRequest", "message": "The pagination token has expired."}]},
        status=400,
    )
    response = run("fba_list_inbound_plans", {})
    assert response["error_code"] == "continuation_token_invalid"


def test_a_plain_validation_error_is_not_an_invalid_state(amazon, run):
    path = f"/inbound/fba/2024-03-20/inboundPlans/{PLAN}/shipments/{PLAN.replace('wf', 'sh')}/sourceAddress"
    amazon.reply(
        "PUT",
        path,
        {
            "errors": [
                {
                    "code": "InvalidInput",
                    "message": "countryCode cannot be updated to a different country",
                }
            ]
        },
        status=400,
    )
    address = {
        "name": "W",
        "address_line1": "1",
        "city": "L",
        "country_code": "GB",
        "postal_code": "E1",
        "phone_number": "+440",
    }
    response = run(
        "fba_update_shipment_source_address",
        {"inbound_plan_id": PLAN, "shipment_id": PLAN.replace("wf", "sh"), "address": address},
    )
    assert response["error_code"] == "upstream_validation_failed"


def test_a_rehearsal_refuses_an_unsupported_marketplace_like_the_real_run(amazon, run):
    response = run(
        "purchase_shipping_shipment",
        {
            "connection_ref": "conn-de",
            "request_token": "rt",
            "rate_id": "r",
            "requested_document_specification": {
                "format": "PDF",
                "size": {"width": 4, "length": 6, "unit": "INCH"},
                "need_file_joining": False,
                "requested_document_types": ["LABEL"],
            },
        },
        target=connection_target(connection_id="conn-de", marketplace="DE"),
        test_mode=True,
    )
    assert response["error_code"] == "unsupported_marketplace"
    assert amazon.requests == []


def test_the_shipping_document_limit_is_reachable_below_the_response_cap():
    from runtime.sections.shipping_v2 import MAX_DOCUMENT_BYTES
    from runtime.transport import MAX_RESPONSE_BYTES

    assert (MAX_DOCUMENT_BYTES * 4) // 3 + 4096 < MAX_RESPONSE_BYTES
    _ = base64
