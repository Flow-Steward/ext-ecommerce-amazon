from __future__ import annotations

import base64
import hashlib
import json

import pytest
from conftest import SELLER_ID, UK_MARKETPLACE_ID

SCHEMA_HOST = "selling-partner-definitions-prod-iad.s3.amazonaws.com"
SCHEMA_PATH = "/schema/amzn1.ps.MUG.json"
SIGNED = f"https://{SCHEMA_HOST}{SCHEMA_PATH}?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=secret-signature-value"
SCHEMA = {
    "$schema": "https://schemas.amazon.com/selling-partners/definitions/product-types/meta-schema/v1",
    "properties": {"item_name": {"minItems": 1}, "list_price": {"minimum": 0.01}},
}
SCHEMA_BYTES = json.dumps(SCHEMA).encode()
DEFINITION = {
    "productType": "MUG",
    "displayName": "Mug",
    "requirements": "LISTING",
    "requirementsEnforced": "ENFORCED",
    "locale": "en_GB",
    "marketplaceIds": [UK_MARKETPLACE_ID],
    "productTypeVersion": {"version": "U1234", "latest": True, "releaseCandidate": False},
    "propertyGroups": {"offer": {"title": "Offer", "propertyNames": ["purchasable_offer"]}},
    "schema": {
        "link": {"resource": SIGNED, "verb": "GET"},
        "checksum": base64.b64encode(hashlib.md5(SCHEMA_BYTES).digest()).decode(),
    },
    "metaSchema": {
        "link": {
            "resource": "https://example.amazonaws.com/meta.json?X-Amz-Signature=x",
            "verb": "GET",
        },
        "checksum": "abc",
    },
}
ITEM = f"/listings/2021-08-01/items/{SELLER_ID}/SKU-1"


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    monkeypatch.setenv("FS_ALLOW_PRIVATE_REMOTE_URLS", "1")


def test_search_product_types_by_keywords(amazon, run):
    amazon.reply(
        "GET",
        "/definitions/2020-09-01/productTypes",
        {
            "productTypes": [
                {"name": "MUG", "displayName": "Mug", "marketplaceIds": [UK_MARKETPLACE_ID]}
            ],
            "productTypeVersion": "V1",
        },
    )
    response = run("search_product_types", {"keywords": ["mug", "cup"]})
    assert response["ok"] is True, response
    assert response["result"]["product_types"] == [
        {"name": "MUG", "display_name": "Mug", "marketplace_ids": [UK_MARKETPLACE_ID]}
    ]
    query = amazon.requests[0].query
    assert query["keywords"] == "mug,cup" and query["marketplaceIds"] == UK_MARKETPLACE_ID


def test_search_product_types_needs_exactly_one_criterion(amazon, run):
    assert run("search_product_types", {})["error_code"] == "invalid_payload"
    assert (
        run("search_product_types", {"keywords": ["a"], "item_name": "A mug"})["error_code"]
        == "invalid_payload"
    )
    assert amazon.requests == []


def test_definition_never_returns_the_signed_url(amazon, run):
    amazon.reply("GET", "/definitions/2020-09-01/productTypes/MUG", DEFINITION)
    response = run(
        "get_product_type_definition",
        {"product_type": "MUG", "requirements": "LISTING_OFFER_ONLY", "parentage_level": "CHILD"},
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert result["schema_link"]["resource"] == f"https://{SCHEMA_HOST}{SCHEMA_PATH}"
    assert result["schema_link"]["signed"] is True
    assert result["schema"] is None
    assert "secret-signature-value" not in json.dumps(response)
    assert "X-Amz" not in json.dumps(response)
    query = amazon.requests[0].query
    assert query["sellerId"] == SELLER_ID
    assert query["requirements"] == "LISTING_OFFER_ONLY"
    assert query["parentageLevel"] == "CHILD"
    assert len(amazon.requests) == 1


def test_definition_with_schema_downloads_and_verifies_it(amazon, run):
    amazon.reply("GET", "/definitions/2020-09-01/productTypes/MUG", DEFINITION)
    amazon.reply("GET", SCHEMA_PATH, raw=SCHEMA_BYTES)
    response = run(
        "get_product_type_definition",
        {"product_type": "MUG", "include_schema": True, "seller_specific": False},
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert result["schema"]["properties"]["list_price"]["minimum"] == 0.01
    assert result["schema_checksum_verified"] is True
    assert result["schema_bytes"] == len(SCHEMA_BYTES)
    assert "sellerId" not in amazon.requests[0].query
    download = amazon.requests[1]
    assert download.host == SCHEMA_HOST
    assert "x-amz-access-token" not in download.headers


def test_schema_link_outside_s3_is_refused(amazon, run):
    definition = dict(
        DEFINITION,
        schema={
            "link": {"resource": "https://evil.example.com/s.json", "verb": "GET"},
            "checksum": "x",
        },
    )
    amazon.reply("GET", "/definitions/2020-09-01/productTypes/MUG", definition)
    response = run("get_product_type_definition", {"product_type": "MUG", "include_schema": True})
    assert response["error_code"] == "blocked_address"
    assert len(amazon.requests) == 1


def test_schema_link_on_a_non_s3_amazonaws_host_is_refused(amazon, run):
    definition = dict(
        DEFINITION,
        schema={
            "link": {
                "resource": "https://abc123.execute-api.us-east-1.amazonaws.com/s.json",
                "verb": "GET",
            },
            "checksum": "x",
        },
    )
    amazon.reply("GET", "/definitions/2020-09-01/productTypes/MUG", definition)
    response = run("get_product_type_definition", {"product_type": "MUG", "include_schema": True})
    assert response["error_code"] == "blocked_address"
    assert len(amazon.requests) == 1


def test_schema_over_the_inline_cap_points_to_the_narrowing_filters(amazon, run):
    from runtime.sections import requirements

    assert requirements.MAX_SCHEMA_BYTES == 2_621_440
    big = json.dumps(
        {"properties": {f"p{index}": {"title": "x" * 100} for index in range(30_000)}}
    ).encode()
    assert len(big) > requirements.MAX_SCHEMA_BYTES
    amazon.reply("GET", "/definitions/2020-09-01/productTypes/MUG", DEFINITION)
    amazon.reply("GET", SCHEMA_PATH, raw=big)
    response = run("get_product_type_definition", {"product_type": "MUG", "include_schema": True})
    assert response["error_code"] == "response_too_large"
    assert "parentage_level" in response["error"] and "requirements" in response["error"]


def test_product_type_name_is_checked(amazon, run):
    assert (
        run("get_product_type_definition", {"product_type": "../x"})["error_code"]
        == "invalid_payload"
    )
    assert amazon.requests == []


def test_listing_restrictions(amazon, run):
    amazon.reply(
        "GET",
        "/listings/2021-08-01/restrictions",
        {
            "restrictions": [
                {
                    "marketplaceId": UK_MARKETPLACE_ID,
                    "conditionType": "used_good",
                    "reasons": [
                        {
                            "reasonCode": "APPROVAL_REQUIRED",
                            "message": "Approval required",
                            "links": [
                                {
                                    "resource": "https://sellercentral.amazon.co.uk/hz/approvalrequest",
                                    "verb": "GET",
                                    "title": "Request approval",
                                    "type": "text/html",
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    response = run(
        "check_listing_restrictions",
        {"asin": "B000TEST01", "condition_type": "used_good", "reason_locale": "en_GB"},
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert result["restricted"] is True
    assert result["restrictions"][0]["reasons"][0]["reason_code"] == "APPROVAL_REQUIRED"
    query = amazon.requests[0].query
    assert query == {
        "asin": "B000TEST01",
        "conditionType": "used_good",
        "sellerId": SELLER_ID,
        "marketplaceIds": UK_MARKETPLACE_ID,
        "reasonLocale": "en_GB",
    }

    amazon.reply("GET", "/listings/2021-08-01/restrictions", {"restrictions": []})
    assert (
        run("check_listing_restrictions", {"asin": "B000TEST01"})["result"]["restricted"] is False
    )


def test_validate_put_reuses_the_create_listing_request(amazon, run):
    inputs = {
        "sku": "SKU-1",
        "listing_kind": "offer_on_existing_asin",
        "asin": "B000TEST01",
        "condition_type": "new_new",
        "attributes": {
            "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": 1}]
        },
    }
    amazon.reply("PUT", ITEM, {"sku": "SKU-1", "status": "ACCEPTED", "submissionId": "s"})
    run("create_listing", inputs)
    amazon.reply(
        "PUT",
        ITEM,
        {
            "sku": "SKU-1",
            "status": "VALID",
            "submissionId": "p",
            "issues": [],
            "identifiers": [{"marketplaceId": UK_MARKETPLACE_ID, "asin": "B000TEST01"}],
        },
    )
    response = run("validate_listing", {**inputs, "request_kind": "put"}, effect_id=None)
    assert response["ok"] is True, response
    result = response["result"]
    assert result["valid"] is True and result["asin"] == "B000TEST01"
    assert "external_effect_status" not in result
    real, preview = amazon.requests
    assert preview.body == real.body
    assert preview.query["mode"] == "VALIDATION_PREVIEW"
    assert preview.query["includedData"] == "issues,identifiers"


def test_validate_patch_reports_invalid(amazon, run):
    amazon.reply(
        "PATCH",
        ITEM,
        {
            "sku": "SKU-1",
            "status": "INVALID",
            "submissionId": "p",
            "issues": [{"code": "1", "message": "bad", "severity": "ERROR", "categories": []}],
        },
    )
    response = run(
        "validate_listing",
        {
            "sku": "SKU-1",
            "request_kind": "patch",
            "product_type": "MUG",
            "patches": [
                {"op": "replace", "path": "/attributes/item_name", "value": [{"value": "x"}]}
            ],
        },
        test_mode=True,
    )
    assert response["ok"] is True, response
    assert response["result"]["valid"] is False
    assert response["result"]["issues"][0]["message"] == "bad"
    assert len(amazon.requests) == 1


def test_validate_refuses_fields_of_the_other_request_kind(amazon, run):
    response = run(
        "validate_listing",
        {
            "sku": "SKU-1",
            "request_kind": "patch",
            "product_type": "MUG",
            "patches": [{"op": "delete", "path": "/attributes/x"}],
            "attributes": {"a": [{}]},
        },
    )
    assert response["error_code"] == "invalid_payload"
    response = run(
        "validate_listing",
        {
            "sku": "SKU-1",
            "request_kind": "put",
            "listing_kind": "new_product",
            "product_type": "MUG",
            "attributes": {"a": [{}]},
            "patches": [{"op": "delete", "path": "/attributes/x"}],
        },
    )
    assert response["error_code"] == "invalid_payload"
    assert amazon.requests == []


def test_validate_server_error_is_retried_as_a_read(amazon, run):
    amazon.reply("PATCH", ITEM, {"errors": [{"code": "InternalFailure"}]}, status=500)
    amazon.reply("PATCH", ITEM, {"sku": "SKU-1", "status": "VALID", "submissionId": "p"})
    response = run(
        "validate_listing",
        {
            "sku": "SKU-1",
            "request_kind": "patch",
            "product_type": "MUG",
            "patches": [{"op": "delete", "path": "/attributes/x"}],
        },
    )
    assert response["ok"] is True
    assert len(amazon.requests) == 2
