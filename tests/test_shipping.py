from __future__ import annotations

from conftest import UK_MARKETPLACE_ID, connection_target

CONFIRM = "/orders/v0/orders/202-1111111-1111111/shipmentConfirmation"
STATUS = "/orders/v0/orders/202-1111111-1111111/shipment"
BASE = {
    "order_id": "202-1111111-1111111",
    "package_reference_id": "1",
    "carrier_code": "Royal Mail",
    "tracking_number": "RM123456789GB",
    "ship_date": "2026-11-05T10:00:00Z",
    "items": [{"order_item_id": "11111", "quantity": 2}],
}


def test_confirm_shipment_sends_the_package_and_marketplace(amazon, run):
    amazon.reply("POST", CONFIRM, raw=b"", status=204)
    response = run(
        "confirm_shipment",
        {
            **BASE,
            "shipping_method": "Tracked 48",
            "items": [{"order_item_id": "11111", "quantity": 2, "transparency_codes": ["T1"]}],
        },
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert result["confirmed"] is True
    assert result["package_reference_id"] == "1"
    assert "get_order" in result["note"]
    assert result["external_effect_status"] == "succeeded"
    assert amazon.requests[0].body == {
        "marketplaceId": UK_MARKETPLACE_ID,
        "packageDetail": {
            "packageReferenceId": "1",
            "carrierCode": "Royal Mail",
            "trackingNumber": "RM123456789GB",
            "shipDate": "2026-11-05T10:00:00Z",
            "orderItems": [{"orderItemId": "11111", "quantity": 2, "transparencyCodes": ["T1"]}],
            "shippingMethod": "Tracked 48",
        },
    }


def test_confirm_shipment_input_checks_send_nothing(amazon, run):
    assert (
        run("confirm_shipment", {**BASE, "ship_date": "2026-11-05T10:00:00"})["error_code"]
        == "invalid_payload"
    )
    assert (
        run("confirm_shipment", {**BASE, "package_reference_id": "PKG-1"})["error_code"]
        == "invalid_payload"
    )
    assert (
        run("confirm_shipment", {**BASE, "carrier_code": "Other"})["error_code"]
        == "invalid_payload"
    )
    assert run("confirm_shipment", {**BASE, "items": []})["error_code"] == "invalid_payload"
    assert (
        run("confirm_shipment", {**BASE, "items": [{"order_item_id": "1", "quantity": 0}]})[
            "error_code"
        ]
        == "invalid_payload"
    )
    assert (
        run("confirm_shipment", {**BASE, "cod_collection_method": "DirectPayment"})["error_code"]
        == "unsupported_filter"
    )
    assert (
        run("confirm_shipment", {**BASE, "marketplace_id": "ATVPDKIKX0DER"})["error_code"]
        == "invalid_payload"
    )
    assert amazon.requests == []


def test_cod_is_sent_in_japan(amazon, run):
    amazon.reply("POST", CONFIRM, raw=b"", status=204)
    response = run(
        "confirm_shipment",
        {**BASE, "cod_collection_method": "DirectPayment"},
        target=connection_target(marketplace="JP"),
    )
    assert response["ok"] is True, response
    assert amazon.requests[0].body["codCollectionMethod"] == "DirectPayment"
    assert amazon.requests[0].host == "sellingpartnerapi-fe.amazon.com"


def test_confirm_shipment_in_test_mode_sends_nothing(amazon, run):
    response = run("confirm_shipment", BASE, test_mode=True)
    assert response["result"]["external_effect_status"] == "suppressed"
    assert response["result"]["confirmed"] is None
    assert amazon.requests == []


def test_confirm_shipment_needs_an_external_effect_id(amazon, run):
    assert run("confirm_shipment", BASE, effect_id=None)["error_code"] == "missing_idempotency_key"
    assert amazon.requests == []


def test_confirm_shipment_server_error_is_unknown_and_not_repeated(amazon, run):
    amazon.reply("POST", CONFIRM, {"errors": [{"code": "InternalFailure"}]}, status=503)
    response = run("confirm_shipment", BASE)
    assert response["error_code"] == "outcome_unknown"
    assert response["definitely_no_external_effect"] is False
    assert len(amazon.requests) == 1


def test_confirm_shipment_rejected_is_a_definite_failure(amazon, run):
    amazon.reply(
        "POST",
        CONFIRM,
        {"errors": [{"code": "InvalidInput", "message": "bad carrier"}]},
        status=400,
    )
    response = run("confirm_shipment", BASE)
    assert response["error_code"] == "upstream_validation_failed"
    assert response["definitely_no_external_effect"] is True
    assert response["result"]["amazon_errors"][0]["code"] == "InvalidInput"


def test_order_id_with_slash_is_encoded(amazon, run):
    amazon.reply("POST", "/orders/v0/orders/..%2Fx/shipment", raw=b"", status=204)
    response = run("update_shipment_status", {"order_id": "../x", "shipment_status": "PickedUp"})
    assert response["ok"] is True, response


def test_update_shipment_status_with_items(amazon, run):
    amazon.reply("POST", STATUS, raw=b"", status=204)
    response = run(
        "update_shipment_status",
        {
            "order_id": "202-1111111-1111111",
            "shipment_status": "ReadyForPickup",
            "items": [{"order_item_id": "11111", "quantity": 1}],
        },
    )
    assert response["ok"] is True, response
    assert response["result"]["updated"] is True
    assert amazon.requests[0].body == {
        "marketplaceId": UK_MARKETPLACE_ID,
        "shipmentStatus": "ReadyForPickup",
        "orderItems": [{"orderItemId": "11111", "quantity": 1}],
    }
    bad = run("update_shipment_status", {"order_id": "1", "shipment_status": "Shipped"})
    assert bad["error_code"] == "invalid_payload"
    codes = run(
        "update_shipment_status",
        {
            "order_id": "1",
            "shipment_status": "PickedUp",
            "items": [{"order_item_id": "1", "quantity": 1, "transparency_codes": ["T"]}],
        },
    )
    assert codes["error_code"] == "invalid_payload"
    assert len(amazon.requests) == 1


def test_throttled_status_update_is_retried(amazon, run):
    amazon.reply("POST", STATUS, {"errors": [{"code": "QuotaExceeded"}]}, status=429)
    amazon.reply("POST", STATUS, raw=b"", status=204)
    response = run(
        "update_shipment_status", {"order_id": "202-1111111-1111111", "shipment_status": "PickedUp"}
    )
    assert response["ok"] is True
    assert len(amazon.requests) == 2
