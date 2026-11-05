from __future__ import annotations

import pytest
from conftest import SELLER_ID, UK_MARKETPLACE_ID

ITEM = f"/listings/2021-08-01/items/{SELLER_ID}/SKU-1"
SUMMARIES = "/fba/inventory/v1/summaries"
FULL = {
    "asin": "B000TEST01",
    "fnSku": "X000TEST01",
    "sellerSku": "SKU-1",
    "condition": "NewItem",
    "lastUpdatedTime": "2026-11-01T00:00:00Z",
    "productName": "Mug",
    "totalQuantity": 40,
    "stores": ["Amazon"],
    "inventoryDetails": {
        "fulfillableQuantity": 20,
        "inboundWorkingQuantity": 5,
        "inboundShippedQuantity": 4,
        "inboundReceivingQuantity": 3,
        "reservedQuantity": {
            "totalReservedQuantity": 6,
            "pendingCustomerOrderQuantity": 2,
            "pendingTransshipmentQuantity": 3,
            "fcProcessingQuantity": 1,
        },
        "researchingQuantity": {
            "totalResearchingQuantity": 1,
            "researchingQuantityBreakdown": [
                {"name": "researchingQuantityInShortTerm", "quantity": 1}
            ],
        },
        "unfulfillableQuantity": {
            "totalUnfulfillableQuantity": 0,
            "customerDamagedQuantity": 0,
            "defectiveQuantity": 0,
        },
    },
}


def test_fba_inventory_keeps_categories_and_missing_stays_null(amazon, run):
    amazon.reply(
        "GET",
        SUMMARIES,
        {
            "payload": {
                "granularity": {"granularityType": "Marketplace"},
                "inventorySummaries": [FULL, {"sellerSku": "SKU-2"}],
            },
            "pagination": {"nextToken": "amz-1"},
        },
    )
    response = run("get_fba_inventory", {"seller_skus": ["SKU-1", "SKU-2"]})
    assert response["ok"] is True, response
    full, bare = response["result"]["summaries"]
    assert full["fulfillable_quantity"] == 20
    assert (
        full["inbound_working_quantity"],
        full["inbound_shipped_quantity"],
        full["inbound_receiving_quantity"],
    ) == (5, 4, 3)
    assert full["reserved"] == {
        "total": 6,
        "pending_customer_order": 2,
        "pending_transshipment": 3,
        "fc_processing": 1,
    }
    assert full["researching"] == {"total": 1, "short_term": 1, "mid_term": None, "long_term": None}
    assert full["unfulfillable"]["total"] == 0
    assert full["unfulfillable"]["carrier_damaged"] is None
    assert full["total_quantity"] == 40
    assert bare["fulfillable_quantity"] is None and bare["total_quantity"] is None
    assert bare["reserved"]["total"] is None
    query = amazon.requests[0].query
    assert query == {
        "details": "true",
        "granularityType": "Marketplace",
        "granularityId": UK_MARKETPLACE_ID,
        "marketplaceIds": UK_MARKETPLACE_ID,
        "sellerSkus": "SKU-1,SKU-2",
    }
    token = response["result"]["next_token"]
    amazon.reply("GET", SUMMARIES, {"payload": {"inventorySummaries": []}})
    run("get_fba_inventory", {"seller_skus": ["SKU-1", "SKU-2"], "next_token": token})
    assert amazon.requests[1].query["nextToken"] == "amz-1"
    other = run("get_fba_inventory", {"seller_skus": ["SKU-1"], "next_token": token})
    assert other["error_code"] == "continuation_token_invalid"


def test_fba_inventory_filters(amazon, run):
    both = run(
        "get_fba_inventory", {"seller_skus": ["SKU-1"], "start_date_time": "2026-10-01T00:00:00Z"}
    )
    assert both["error_code"] == "unsupported_filter"
    naive = run("get_fba_inventory", {"start_date_time": "2026-10-01T00:00:00"})
    assert naive["error_code"] == "invalid_payload"
    too_many = run("get_fba_inventory", {"seller_skus": [f"S{i}" for i in range(51)]})
    assert too_many["error_code"] == "invalid_payload"
    assert amazon.requests == []
    amazon.reply("GET", SUMMARIES, {"payload": {"inventorySummaries": []}})
    run("get_fba_inventory", {"start_date_time": "2026-10-01T00:00:00+02:00"})
    assert amazon.requests[0].query["startDateTime"] == "2026-10-01T00:00:00+02:00"


def test_get_fbm_quantity(amazon, run):
    amazon.reply(
        "GET",
        ITEM,
        {
            "sku": "SKU-1",
            "fulfillmentAvailability": [
                {"fulfillmentChannelCode": "DEFAULT", "quantity": 0},
                {"fulfillmentChannelCode": "AMAZON_EU"},
            ],
        },
    )
    result = run("get_fbm_quantity", {"sku": "SKU-1"})["result"]
    assert result["quantity"] == 0
    assert result["merchant_channel_found"] is True
    assert result["channels"][1] == {"fulfillment_channel_code": "AMAZON_EU", "quantity": None}
    assert amazon.requests[0].query["includedData"] == "fulfillmentAvailability"
    amazon.reply("GET", ITEM, {"sku": "SKU-1"})
    missing = run("get_fbm_quantity", {"sku": "SKU-1"})["result"]
    assert missing["quantity"] is None and missing["merchant_channel_found"] is False


def test_set_fbm_quantity_sends_only_fulfillment_availability(amazon, run):
    amazon.reply(
        "GET",
        ITEM,
        {
            "sku": "SKU-1",
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT", "quantity": 3}],
        },
    )
    amazon.reply("PATCH", ITEM, {"sku": "SKU-1", "status": "ACCEPTED", "submissionId": "sub-1"})
    response = run("set_fbm_quantity", {"sku": "SKU-1", "product_type": "MUG", "quantity": 12})
    assert response["ok"] is True, response
    assert response["result"]["accepted"] is True and response["result"]["applied"] is False
    patch = amazon.requests[1]
    assert patch.method == "PATCH"
    assert patch.body == {
        "productType": "MUG",
        "patches": [
            {
                "op": "merge",
                "path": "/attributes/fulfillment_availability",
                "value": [{"fulfillment_channel_code": "DEFAULT", "quantity": 12}],
            }
        ],
    }


def test_set_fbm_quantity_refuses_an_fba_only_sku(amazon, run):
    amazon.reply(
        "GET",
        ITEM,
        {"sku": "SKU-1", "fulfillmentAvailability": [{"fulfillmentChannelCode": "AMAZON_EU"}]},
    )
    response = run("set_fbm_quantity", {"sku": "SKU-1", "product_type": "MUG", "quantity": 1})
    assert response["error_code"] == "invalid_payload"
    assert response["definitely_no_external_effect"] is True
    assert [request.method for request in amazon.requests] == ["GET"]


@pytest.mark.parametrize(
    "listing",
    [
        {"sku": "SKU-1", "fulfillmentAvailability": []},
        {"sku": "SKU-1"},
    ],
)
def test_set_fbm_quantity_refuses_a_listing_without_any_channel(amazon, run, listing):
    amazon.reply("GET", ITEM, listing)
    response = run("set_fbm_quantity", {"sku": "SKU-1", "product_type": "MUG", "quantity": 1})
    assert response["error_code"] == "invalid_payload"
    assert "no fulfillment availability" in response["error"]
    assert response["definitely_no_external_effect"] is True
    assert [request.method for request in amazon.requests] == ["GET"]


def test_set_fbm_quantity_input_and_modes(amazon, run):
    assert (
        run("set_fbm_quantity", {"sku": "SKU-1", "product_type": "MUG", "quantity": -1})[
            "error_code"
        ]
        == "invalid_payload"
    )
    assert (
        run("set_fbm_quantity", {"sku": "SKU-1", "quantity": 1})["error_code"] == "invalid_payload"
    )
    assert (
        run(
            "set_fbm_quantity",
            {"sku": "SKU-1", "product_type": "MUG", "quantity": 1},
            test_mode=True,
        )["result"]["external_effect_status"]
        == "suppressed"
    )
    assert (
        run(
            "set_fbm_quantity",
            {"sku": "SKU-1", "product_type": "MUG", "quantity": 1},
            effect_id=None,
        )["error_code"]
        == "missing_idempotency_key"
    )
    assert amazon.requests == []
    amazon.reply(
        "GET",
        ITEM,
        {
            "sku": "SKU-1",
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT", "quantity": 4}],
        },
    )
    amazon.reply("PATCH", ITEM, {}, status=500)
    unknown = run("set_fbm_quantity", {"sku": "SKU-1", "product_type": "MUG", "quantity": 1})
    assert unknown["error_code"] == "outcome_unknown"
    assert [request.method for request in amazon.requests] == ["GET", "PATCH"]


def test_set_fbm_quantity_sku_with_slash_is_encoded(amazon, run):
    path = f"/listings/2021-08-01/items/{SELLER_ID}/a%2Fb"
    amazon.reply(
        "GET",
        path,
        {
            "sku": "a/b",
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT", "quantity": 1}],
        },
    )
    amazon.reply("PATCH", path, {"sku": "a/b", "status": "ACCEPTED", "submissionId": "s"})
    assert (
        run("set_fbm_quantity", {"sku": "a/b", "product_type": "MUG", "quantity": 2})["ok"] is True
    )
