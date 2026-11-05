from __future__ import annotations

import pytest
from conftest import (
    ACCESS_TOKEN,
    SELLER_ID,
    UK_MARKETPLACE_ID,
    US_MARKETPLACE_ID,
    connection_target,
)

V2026_ORDER = {
    "orderId": "202-1111111-1111111",
    "createdTime": "2026-11-01T10:00:00Z",
    "lastUpdatedTime": "2026-11-01T12:00:00Z",
    "salesChannel": {"channelName": "AMAZON", "marketplaceId": UK_MARKETPLACE_ID},
    "fulfillment": {"fulfillmentStatus": "UNSHIPPED", "fulfilledBy": "MERCHANT"},
    "proceeds": {"grandTotal": {"amount": "19.99", "currencyCode": "GBP"}},
    "orderItems": [
        {
            "orderItemId": "11111",
            "quantityOrdered": 2,
            "product": {
                "asin": "B000TEST01",
                "sellerSku": "SKU-1",
                "title": "Mug",
                "price": {"unitPrice": {"amount": "9.995", "currencyCode": "GBP"}},
            },
            "fulfillment": {"quantityFulfilled": 0},
        }
    ],
}
V0_ORDER = {
    "AmazonOrderId": "202-2222222-2222222",
    "PurchaseDate": "2026-11-01T10:00:00Z",
    "LastUpdateDate": "2026-11-01T12:00:00Z",
    "OrderStatus": "Canceled",
    "FulfillmentChannel": "AFN",
    "MarketplaceId": UK_MARKETPLACE_ID,
    "OrderTotal": {"CurrencyCode": "GBP", "Amount": "0.10"},
}


def test_search_orders_2026_normalizes_and_wraps_the_token(amazon, run):
    amazon.reply(
        "GET",
        "/orders/2026-01-01/orders",
        {"orders": [V2026_ORDER], "pagination": {"nextToken": "amz-next-1"}},
    )
    response = run(
        "search_orders",
        {
            "created_after": "2026-11-01T00:00:00Z",
            "statuses": ["UNSHIPPED"],
            "fulfilled_by": ["MERCHANT"],
        },
    )
    assert response["ok"] is True, response
    result = response["result"]
    order = result["orders"][0]
    assert order["order_id"] == "202-1111111-1111111"
    assert order["status"] == "UNSHIPPED"
    assert order["fulfilled_by"] == "MERCHANT"
    assert order["order_total"] == {"amount": "19.99", "currency_code": "GBP"}
    assert result["api_version"] == "2026-01-01"
    assert result["next_token"] and result["next_token"] != "amz-next-1"
    request = amazon.requests[0]
    assert request.host == "sellingpartnerapi-eu.amazon.com"
    assert request.query["marketplaceIds"] == UK_MARKETPLACE_ID
    assert request.query["fulfillmentStatuses"] == "UNSHIPPED"
    assert request.headers["x-amz-access-token"] == ACCESS_TOKEN

    amazon.reply("GET", "/orders/2026-01-01/orders", {"orders": [], "pagination": {}})
    second = run(
        "search_orders",
        {
            "created_after": "2026-11-01T00:00:00Z",
            "statuses": ["UNSHIPPED"],
            "fulfilled_by": ["MERCHANT"],
            "next_token": result["next_token"],
        },
    )
    assert second["ok"] is True
    assert amazon.requests[1].query["paginationToken"] == "amz-next-1"
    assert second["result"]["next_token"] is None


def test_search_orders_v0_maps_statuses_and_channels(amazon, run):
    amazon.reply("GET", "/orders/v0/orders", {"payload": {"Orders": [V0_ORDER], "NextToken": None}})
    response = run(
        "search_orders",
        {
            "api_version": "v0",
            "last_updated_after": "2026-11-01T00:00:00+01:00",
            "statuses": ["CANCELLED"],
            "fulfilled_by": ["AMAZON"],
        },
    )
    assert response["ok"] is True, response
    order = response["result"]["orders"][0]
    assert order["status"] == "CANCELLED"
    assert order["fulfilled_by"] == "AMAZON"
    assert order["order_total"] == {"amount": "0.10", "currency_code": "GBP"}
    assert order["api_version"] == "v0"
    query = amazon.requests[0].query
    assert query["OrderStatuses"] == "Canceled"
    assert query["FulfillmentChannels"] == "AFN"
    assert query["MarketplaceIds"] == UK_MARKETPLACE_ID


def test_filters_of_the_other_version_are_refused_not_dropped(amazon, run):
    response = run(
        "search_orders", {"created_after": "2026-11-01T00:00:00Z", "amazon_order_ids": ["202-1"]}
    )
    assert response["error_code"] == "unsupported_filter"
    response = run(
        "search_orders",
        {"api_version": "v0", "created_after": "2026-11-01T00:00:00Z", "included_data": ["BUYER"]},
    )
    assert response["error_code"] == "unsupported_filter"
    response = run(
        "search_orders",
        {"created_after": "2026-11-01T00:00:00Z", "statuses": ["INVOICE_UNCONFIRMED"]},
    )
    assert response["error_code"] == "unsupported_filter"
    assert amazon.requests == []


def test_timestamp_without_zone_is_refused(amazon, run):
    response = run("search_orders", {"created_after": "2026-11-01T00:00:00"})
    assert response["error_code"] == "invalid_payload"
    assert amazon.requests == []


def test_token_from_another_version_or_filter_is_refused(amazon, run):
    amazon.reply(
        "GET", "/orders/2026-01-01/orders", {"orders": [], "pagination": {"nextToken": "amz-next"}}
    )
    token = run("search_orders", {"created_after": "2026-11-01T00:00:00Z"})["result"]["next_token"]
    other_version = run(
        "search_orders",
        {"api_version": "v0", "created_after": "2026-11-01T00:00:00Z", "next_token": token},
    )
    assert other_version["error_code"] == "continuation_token_invalid"
    other_filter = run(
        "search_orders", {"created_after": "2026-10-01T00:00:00Z", "next_token": token}
    )
    assert other_filter["error_code"] == "continuation_token_invalid"
    assert len(amazon.requests) == 1


def test_us_connection_uses_the_na_endpoint(amazon, run):
    amazon.reply("GET", "/orders/2026-01-01/orders", {"orders": []})
    response = run(
        "search_orders",
        {"created_after": "2026-11-01T00:00:00Z"},
        target=connection_target(marketplace="US", access_token="Atza|us-token-000000001"),
    )
    assert response["ok"] is True
    assert amazon.requests[0].host == "sellingpartnerapi-na.amazon.com"
    assert amazon.requests[0].query["marketplaceIds"] == US_MARKETPLACE_ID
    assert amazon.requests[0].headers["x-amz-access-token"] == "Atza|us-token-000000001"


def test_get_order_items_2026_reads_embedded_items(amazon, run):
    amazon.reply("GET", "/orders/2026-01-01/orders/202-1111111-1111111", {"order": V2026_ORDER})
    response = run("get_order_items", {"order_id": "202-1111111-1111111"})
    item = response["result"]["items"][0]
    assert item["seller_sku"] == "SKU-1"
    assert item["unit_price"] == {"amount": "9.995", "currency_code": "GBP"}
    assert item["quantity_shipped"] == 0


def test_get_order_items_v0_has_no_unit_price_but_a_line_total(amazon, run):
    amazon.reply(
        "GET",
        "/orders/v0/orders/202-2/orderItems",
        {
            "payload": {
                "OrderItems": [
                    {
                        "OrderItemId": "9",
                        "SellerSKU": "S",
                        "QuantityOrdered": 3,
                        "ItemPrice": {"CurrencyCode": "GBP", "Amount": "30.00"},
                    }
                ]
            }
        },
    )
    response = run("get_order_items", {"order_id": "202-2", "api_version": "v0"})
    item = response["result"]["items"][0]
    assert item["unit_price"] is None
    assert item["item_total"] == {"amount": "30.00", "currency_code": "GBP"}


def test_buyer_info_v0_uses_a_restricted_data_token_and_never_returns_it(amazon, run):
    amazon.reply(
        "POST",
        "/tokens/2021-03-01/restrictedDataToken",
        {"restrictedDataToken": "Atz.sprdt|rdt-secret-value", "expiresIn": 3600},
    )
    amazon.reply(
        "GET",
        "/orders/v0/orders/202-2/buyerInfo",
        {"payload": {"BuyerName": "Ann", "BuyerEmail": "ann@marketplace.amazon.co.uk"}},
    )
    amazon.reply(
        "GET",
        "/orders/v0/orders/202-2/address",
        {"payload": {"ShippingAddress": {"Name": "Ann", "City": "Leeds", "CountryCode": "GB"}}},
    )
    response = run("get_order_buyer_info", {"order_id": "202-2", "api_version": "v0"})
    assert response["ok"] is True, response
    assert response["result"]["buyer"]["email"] == "ann@marketplace.amazon.co.uk"
    assert response["result"]["shipping_address"]["city"] == "Leeds"
    rdt_request = amazon.requests[0]
    paths = [item["path"] for item in rdt_request.body["restrictedResources"]]
    assert paths == ["/orders/v0/orders/202-2/buyerInfo", "/orders/v0/orders/202-2/address"]
    assert amazon.requests[1].headers["x-amz-access-token"] == "Atz.sprdt|rdt-secret-value"
    assert "rdt-secret-value" not in str(response)


def test_buyer_info_2026_asks_for_buyer_and_recipient(amazon, run):
    order = dict(
        V2026_ORDER,
        buyer={"buyerName": "Bo", "buyerEmail": "bo@x.example"},
        recipient={"deliveryAddress": {"name": "Bo", "city": "York"}},
    )
    amazon.reply("GET", "/orders/2026-01-01/orders/202-1", {"order": order})
    response = run("get_order_buyer_info", {"order_id": "202-1"})
    assert amazon.requests[0].query["includedData"] == "BUYER,RECIPIENT"
    assert response["result"]["buyer"]["name"] == "Bo"
    assert response["result"]["shipping_address"]["city"] == "York"


def test_order_id_with_a_slash_cannot_reach_another_path(amazon, run):
    amazon.reply("GET", "/orders/2026-01-01/orders/..%2Fx", {"order": V2026_ORDER})
    run("get_order", {"order_id": "../x"})
    assert amazon.requests[0].path == "/orders/2026-01-01/orders/..%2Fx"
    assert amazon.requests[0].url.split("?")[0].endswith("/orders/2026-01-01/orders/..%2Fx")


def test_rate_limited_read_is_retried_then_succeeds(amazon, run):
    amazon.reply(
        "GET", "/orders/2026-01-01/orders", {"errors": [{"code": "QuotaExceeded"}]}, status=429
    )
    amazon.reply("GET", "/orders/2026-01-01/orders", {"orders": []})
    response = run("search_orders", {"created_after": "2026-11-01T00:00:00Z"})
    assert response["ok"] is True
    assert len(amazon.requests) == 2


def test_rate_limit_that_persists_ends_in_a_clear_error(amazon, run):
    for _ in range(4):
        amazon.reply(
            "GET",
            "/orders/2026-01-01/orders",
            {"errors": [{"code": "QuotaExceeded", "message": "You exceeded your quota"}]},
            status=429,
        )
    response = run("search_orders", {"created_after": "2026-11-01T00:00:00Z"})
    assert response["error_code"] == "rate_limited"
    assert response["retryable"] is True
    assert response["result"]["amazon_errors"][0]["code"] == "QuotaExceeded"
    assert len(amazon.requests) == 4


def test_forbidden_is_reported_with_what_to_check(amazon, run):
    amazon.reply(
        "GET", "/orders/2026-01-01/orders", {"errors": [{"code": "Unauthorized"}]}, status=403
    )
    response = run("search_orders", {"created_after": "2026-11-01T00:00:00Z"})
    assert response["error_code"] == "access_denied"
    assert "role" in response["error"] and "Seller ID" in response["error"]
    assert len(amazon.requests) == 1


def test_connection_ref_must_match_the_hydrated_connection(amazon, run):
    response = run("get_order", {"order_id": "1", "connection_ref": "conn-other"})
    assert response["error_code"] == "invalid_connection"
    assert amazon.requests == []


def test_confirmed_subject_must_match_the_seller_id(amazon, run):
    response = run(
        "get_order", {"order_id": "1"}, target=connection_target(subject="A9OTHERSELLER")
    )
    assert response["error_code"] == "invalid_connection"
    ok = run(
        "search_orders",
        {"created_after": "2026-11-01T00:00:00Z"},
        target=connection_target(subject=SELLER_ID),
    )
    assert ok["error_code"] != "invalid_connection"


def test_2026_calls_always_ask_for_fulfillment_and_proceeds(amazon, run):
    """status, fulfilled_by, order_total, quantity_shipped and item_total live in those sections."""
    amazon.reply("GET", "/orders/2026-01-01/orders", {"orders": [V2026_ORDER]})
    amazon.reply("GET", "/orders/2026-01-01/orders/202-1111111-1111111", {"order": V2026_ORDER})
    amazon.reply("GET", "/orders/2026-01-01/orders/202-1111111-1111111", {"order": V2026_ORDER})
    amazon.reply("GET", "/orders/2026-01-01/orders/202-1111111-1111111", {"order": V2026_ORDER})
    run("search_orders", {"created_after": "2026-11-01T00:00:00Z"})
    run("get_order", {"order_id": "202-1111111-1111111"})
    run(
        "get_order",
        {"order_id": "202-1111111-1111111", "included_data": ["BUYER", "PROCEEDS", "TAX"]},
    )
    items = run("get_order_items", {"order_id": "202-1111111-1111111"})
    assert [request.query["includedData"] for request in amazon.requests] == [
        "FULFILLMENT,PROCEEDS",
        "FULFILLMENT,PROCEEDS",
        "BUYER,PROCEEDS,TAX,FULFILLMENT",
        "FULFILLMENT,PROCEEDS",
    ]
    item = items["result"]["items"][0]
    assert item["quantity_shipped"] == 0


def test_search_with_included_data_merges_without_repeats(amazon, run):
    amazon.reply("GET", "/orders/2026-01-01/orders", {"orders": []})
    run(
        "search_orders",
        {"created_after": "2026-11-01T00:00:00Z", "included_data": ["FULFILLMENT", "BUYER"]},
    )
    assert amazon.requests[0].query["includedData"] == "FULFILLMENT,BUYER,PROCEEDS"


@pytest.mark.parametrize("version", ["2026-01-01", "v0"])
@pytest.mark.parametrize(
    "updated",
    [
        {"last_updated_after": "2026-11-01T00:00:00Z"},
        {"last_updated_before": "2026-11-02T00:00:00Z"},
    ],
)
def test_created_before_with_an_update_window_is_refused(amazon, run, version, updated):
    inputs = {"api_version": version, "created_before": "2026-11-03T00:00:00Z", **updated}
    if "last_updated_after" not in updated:
        inputs["last_updated_after"] = "2026-11-01T00:00:00Z"
    response = run("search_orders", inputs)
    assert response["error_code"] == "invalid_payload"
    assert "not both" in response["error"]
    assert amazon.requests == []


def test_token_of_the_default_version_works_with_the_version_named(amazon, run):
    amazon.reply(
        "GET", "/orders/2026-01-01/orders", {"orders": [], "pagination": {"nextToken": "n1"}}
    )
    token = run("search_orders", {"created_after": "2026-11-01T00:00:00Z"})["result"]["next_token"]
    amazon.reply("GET", "/orders/2026-01-01/orders", {"orders": []})
    second = run(
        "search_orders",
        {"created_after": "2026-11-01T00:00:00Z", "api_version": "2026-01-01", "next_token": token},
    )
    assert second["ok"] is True, second
    assert amazon.requests[1].query["paginationToken"] == "n1"


def test_included_data_may_change_between_pages(amazon, run):
    amazon.reply(
        "GET", "/orders/2026-01-01/orders", {"orders": [], "pagination": {"nextToken": "n1"}}
    )
    token = run("search_orders", {"created_after": "2026-11-01T00:00:00Z"})["result"]["next_token"]
    amazon.reply("GET", "/orders/2026-01-01/orders", {"orders": []})
    second = run(
        "search_orders",
        {"created_after": "2026-11-01T00:00:00Z", "included_data": ["BUYER"], "next_token": token},
    )
    assert second["ok"] is True, second
    assert amazon.requests[1].query["includedData"] == "BUYER,FULFILLMENT,PROCEEDS"
    # A real filter still binds the token.
    other = run(
        "search_orders",
        {"created_after": "2026-11-01T00:00:00Z", "statuses": ["SHIPPED"], "next_token": token},
    )
    assert other["error_code"] == "continuation_token_invalid"
