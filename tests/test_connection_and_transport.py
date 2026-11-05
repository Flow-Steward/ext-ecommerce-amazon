from __future__ import annotations

import pytest
from conftest import ACCESS_TOKEN, CONNECTION_ID, SELLER_ID, UK_MARKETPLACE_ID, connection_target, make_payload

from runtime import errors, operations
from runtime.connection import connection_from_payload
from runtime.errors import ExtensionError
from runtime.transport import assert_document_url, encode_query

PARTICIPATIONS = {
    "payload": [
        {
            "marketplace": {"id": UK_MARKETPLACE_ID, "countryCode": "GB", "defaultCurrencyCode": "GBP"},
            "participation": {"isParticipating": True, "hasSuspendedListings": False},
            "storeName": "Test Store",
        }
    ]
}


def test_test_connection_reports_each_check(amazon, run):
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", PARTICIPATIONS)
    amazon.reply("GET", f"/listings/2021-08-01/items/{SELLER_ID}", {"numberOfResults": 0, "items": []})
    response = run("test_connection")
    result = response["result"]
    assert result["ok"] is True
    assert result["seller_id_confirmed"] is True
    assert result["region"] == "EU"
    assert result["store_name"] == "Test Store"
    assert [check["status"] for check in result["checks"]] == ["passed", "passed"]
    assert amazon.requests[1].query == {"marketplaceIds": UK_MARKETPLACE_ID, "pageSize": "1"}


def test_test_connection_explains_a_seller_id_amazon_refuses(amazon, run):
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", PARTICIPATIONS)
    amazon.reply("GET", f"/listings/2021-08-01/items/{SELLER_ID}", {"errors": [{"code": "Unauthorized"}]}, status=403)
    result = run("test_connection")["result"]
    assert result["ok"] is False
    assert result["seller_id_confirmed"] is False
    assert "Merchant Token" in result["checks"][1]["message"]


def test_test_connection_on_a_rejected_token_is_an_error(amazon, run):
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", {"errors": [{"code": "Unauthorized"}]}, status=401)
    amazon.reply("GET", f"/listings/2021-08-01/items/{SELLER_ID}", {})
    response = run("test_connection")
    assert response["ok"] is False
    assert response["error_code"] == "authentication_failed"


def _post_connect_payload() -> dict:
    payload = make_payload("verify_connection", {})
    payload["action"]["input"] = {}
    return payload


def test_verify_connection_returns_the_confirmed_seller_as_subject(amazon):
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", PARTICIPATIONS)
    amazon.reply("GET", f"/listings/2021-08-01/items/{SELLER_ID}", {"items": []})
    response = operations.handle_runtime(_post_connect_payload(), transport_factory=amazon.transport_factory())
    assert response["result"] == {
        "subject": SELLER_ID,
        "health": "ok",
        "safe_metadata": {"marketplace": "UK", "region": "EU"},
        "verified_display_name": "Test Store",
    }


def test_verify_connection_flags_a_marketplace_the_seller_does_not_sell_in(amazon):
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", {"payload": []})
    amazon.reply("GET", f"/listings/2021-08-01/items/{SELLER_ID}", {"items": []})
    result = operations.handle_runtime(_post_connect_payload(), transport_factory=amazon.transport_factory())["result"]
    assert result["health"] == "needs_attention"
    assert result["subject"] == ""
    assert result["safe_metadata"]["problem"] == "marketplace_not_confirmed"


def test_verify_connection_maps_a_dead_token_to_authorization_required(amazon):
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", {}, status=401)
    result = operations.handle_runtime(_post_connect_payload(), transport_factory=amazon.transport_factory())["result"]
    assert result == {"subject": "", "health": "authorization_required", "error_code": "authorization_required"}


@pytest.mark.parametrize(
    ("config", "code"),
    [
        ({"seller_id": "bad id", "marketplace": "UK"}, errors.INVALID_CONFIGURATION),
        ({"seller_id": SELLER_ID, "marketplace": "GB"}, errors.INVALID_CONFIGURATION),
        ({"seller_id": SELLER_ID, "marketplace": "UK", "base_url": "https://evil.example"}, errors.INVALID_CONNECTION),
    ],
)
def test_connection_settings_are_checked(config, code):
    target = connection_target()
    target["connection"]["config"] = config
    payload = make_payload("get_order", {"order_id": "1"}, target=target)
    with pytest.raises(ExtensionError) as raised:
        connection_from_payload(payload, connection_ref=CONNECTION_ID)
    assert raised.value.code == code


def test_missing_access_token_asks_for_a_new_refresh_token():
    target = connection_target()
    target["connection"]["secrets"] = {}
    with pytest.raises(ExtensionError) as raised:
        connection_from_payload(make_payload("get_order", target=target), connection_ref=CONNECTION_ID)
    assert raised.value.code == errors.REAUTHORIZATION_REQUIRED


def test_connection_repr_never_shows_the_token():
    connection = connection_from_payload(make_payload("get_order"), connection_ref=CONNECTION_ID)
    assert ACCESS_TOKEN not in repr(connection)


def test_query_encoding_joins_lists_and_drops_absent_values():
    assert encode_query({"a": ["x", "y"], "b": None, "c": True, "d": 3, "e": []}) == "a=x%2Cy&c=true&d=3"


@pytest.mark.parametrize(
    "url",
    [
        "http://tortuga-prod-eu.s3-eu-west-1.amazonaws.com/doc",
        "https://evil.example/doc",
        "https://amazonaws.com.evil.example/doc",
        "",
    ],
)
def test_document_urls_outside_amazon_s3_are_refused(url):
    with pytest.raises(ExtensionError):
        assert_document_url(url)


def test_unknown_operation_is_refused(amazon, run):
    response = run("delete_everything")
    assert response["error_code"] == "unsupported_operation"
    assert amazon.requests == []


def test_server_error_on_a_read_is_retried_and_reported(amazon, run):
    for _ in range(4):
        amazon.reply("GET", "/orders/2026-01-01/orders", {}, status=503)
    response = run("search_orders", {"created_after": "2026-11-01T00:00:00Z"})
    assert response["error_code"] == "upstream_failure"
    assert len(amazon.requests) == 4
