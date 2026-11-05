from __future__ import annotations

from conftest import SELLER_ID, UK_MARKETPLACE_ID

ITEM = f"/listings/2021-08-01/items/{SELLER_ID}/SKU-1"
SEARCH = f"/listings/2021-08-01/items/{SELLER_ID}"

LISTING = {
    "sku": "SKU-1",
    "summaries": [
        {
            "marketplaceId": UK_MARKETPLACE_ID,
            "asin": "B000TEST01",
            "productType": "MUG",
            "conditionType": "new_new",
            "status": ["BUYABLE", "DISCOVERABLE"],
            "itemName": "Mug",
            "createdDate": "2026-01-01T00:00:00Z",
            "lastUpdatedDate": "2026-11-01T00:00:00Z",
        }
    ],
    "issues": [
        {
            "code": "90220",
            "message": "'brand' is required but not supplied.",
            "severity": "ERROR",
            "attributeNames": ["brand"],
            "categories": ["MISSING_ATTRIBUTE"],
            "enforcements": {
                "actions": [{"action": "LISTING_SUPPRESSED"}],
                "exemption": {"status": "NOT_EXEMPT"},
            },
        },
        {"code": "1", "message": "warn", "severity": "WARNING", "categories": []},
    ],
    "offers": [
        {
            "marketplaceId": UK_MARKETPLACE_ID,
            "offerType": "B2C",
            "price": {"amount": 19.99, "currencyCode": "GBP"},
            "audience": {"value": "ALL"},
        }
    ],
    "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT", "quantity": 7}],
}

OFFER_ATTRIBUTES = {
    "purchasable_offer": [
        {
            "marketplace_id": UK_MARKETPLACE_ID,
            "currency": "GBP",
            "our_price": [{"schedule": [{"value_with_tax": 12.5}]}],
        }
    ],
    "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": 3}],
}


def test_get_listing_normalizes_with_default_included_data(amazon, run):
    amazon.reply("GET", ITEM, LISTING)
    response = run("get_listing", {"sku": "SKU-1"})
    assert response["ok"] is True, response
    listing = response["result"]["listing"]
    assert listing["asin"] == "B000TEST01"
    assert listing["buyable"] is True
    assert listing["offers"][0]["price"] == {"amount": "19.99", "currency_code": "GBP"}
    assert listing["fulfillment_availability"] == [
        {"fulfillment_channel_code": "DEFAULT", "quantity": 7}
    ]
    assert listing["amazon"]["sku"] == "SKU-1"
    query = amazon.requests[0].query
    assert query["marketplaceIds"] == UK_MARKETPLACE_ID
    assert query["includedData"] == "summaries,attributes,issues,offers,fulfillmentAvailability"


def test_sku_with_slash_is_one_encoded_segment(amazon, run):
    amazon.reply("GET", f"/listings/2021-08-01/items/{SELLER_ID}/a%2Fb%20c", LISTING)
    response = run("get_listing", {"sku": "a/b c"})
    assert response["ok"] is True, response
    assert amazon.requests[0].url.split("?")[0].endswith("/a%2Fb%20c")


def test_get_listing_issues_counts_by_severity(amazon, run):
    amazon.reply("GET", ITEM, {"sku": "SKU-1", "issues": LISTING["issues"]})
    result = run("get_listing_issues", {"sku": "SKU-1", "issue_locale": "en_GB"})["result"]
    assert result["error_count"] == 1 and result["warning_count"] == 1
    issue = result["issues"][0]
    assert issue["attribute_names"] == ["brand"]
    assert issue["categories"] == ["MISSING_ATTRIBUTE"]
    assert issue["enforcement_actions"] == ["LISTING_SUPPRESSED"]
    assert amazon.requests[0].query["includedData"] == "issues"
    assert amazon.requests[0].query["issueLocale"] == "en_GB"


def test_search_listings_maps_filters_and_paginates(amazon, run):
    amazon.reply(
        "GET",
        SEARCH,
        {"numberOfResults": 30, "items": [LISTING], "pagination": {"nextToken": "amz-1"}},
    )
    filters = {
        "with_status": ["BUYABLE"],
        "without_status": ["DISCOVERABLE"],
        "sort_by": "sku",
        "sort_order": "ASC",
        "last_updated_after": "2026-10-01T00:00:00Z",
    }
    result = run("search_listings", {**filters, "page_size": 20})["result"]
    assert result["count"] == 1 and result["number_of_results"] == 30
    query = amazon.requests[0].query
    assert query["withStatus"] == "BUYABLE"
    assert query["withoutStatus"] == "DISCOVERABLE"
    assert query["sortBy"] == "sku" and query["pageSize"] == "20"
    amazon.reply("GET", SEARCH, {"numberOfResults": 30, "items": []})
    second = run("search_listings", {**filters, "next_token": result["next_token"]})
    assert second["ok"] is True
    assert amazon.requests[1].query["pageToken"] == "amz-1"


def test_search_listings_refuses_incompatible_filters(amazon, run):
    both = run(
        "search_listings",
        {"identifiers": ["X"], "identifiers_type": "SKU", "variation_parent_sku": "P"},
    )
    assert both["error_code"] == "unsupported_filter"
    half = run("search_listings", {"identifiers": ["X"]})
    assert half["error_code"] == "invalid_payload"
    too_big = run("search_listings", {"page_size": 21})
    assert too_big["error_code"] == "invalid_payload"
    naive = run("search_listings", {"created_after": "2026-10-01T00:00:00"})
    assert naive["error_code"] == "invalid_payload"
    assert amazon.requests == []


def test_create_offer_on_existing_asin_sends_offer_only(amazon, run):
    amazon.reply(
        "PUT", ITEM, {"sku": "SKU-1", "status": "ACCEPTED", "submissionId": "sub-1", "issues": []}
    )
    response = run(
        "create_listing",
        {
            "sku": "SKU-1",
            "listing_kind": "offer_on_existing_asin",
            "asin": "B000TEST01",
            "condition_type": "new_new",
            "attributes": OFFER_ATTRIBUTES,
        },
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert result == {
        "sku": "SKU-1",
        "submission_id": "sub-1",
        "status": "ACCEPTED",
        "accepted": True,
        "applied": False,
        "check_with": "get_listing",
        "issues": [],
        "amazon": {"sku": "SKU-1", "status": "ACCEPTED", "submissionId": "sub-1", "issues": []},
        "external_effect_status": "succeeded",
        "definitely_no_external_effect": False,
    }
    request = amazon.requests[0]
    assert request.query["marketplaceIds"] == UK_MARKETPLACE_ID
    assert request.query["includedData"] == "issues"
    assert "mode" not in request.query
    body = request.body
    assert body["productType"] == "PRODUCT"
    assert body["requirements"] == "LISTING_OFFER_ONLY"
    assert body["attributes"]["merchant_suggested_asin"] == [
        {"value": "B000TEST01", "marketplace_id": UK_MARKETPLACE_ID}
    ]
    assert body["attributes"]["condition_type"] == [
        {"value": "new_new", "marketplace_id": UK_MARKETPLACE_ID}
    ]
    assert body["attributes"]["purchasable_offer"] == OFFER_ATTRIBUTES["purchasable_offer"]


def test_create_new_product_requires_product_type_and_refuses_asin(amazon, run):
    attributes = {"item_name": [{"value": "Mug", "language_tag": "en_GB"}]}
    missing = run(
        "create_listing", {"sku": "SKU-1", "listing_kind": "new_product", "attributes": attributes}
    )
    assert missing["error_code"] == "invalid_payload"
    with_asin = run(
        "create_listing",
        {
            "sku": "SKU-1",
            "listing_kind": "new_product",
            "product_type": "MUG",
            "asin": "B000TEST01",
            "attributes": attributes,
        },
    )
    assert with_asin["error_code"] == "invalid_payload"
    no_asin = run(
        "create_listing",
        {"sku": "SKU-1", "listing_kind": "offer_on_existing_asin", "attributes": attributes},
    )
    assert no_asin["error_code"] == "invalid_payload"
    assert amazon.requests == []
    amazon.reply("PUT", ITEM, {"sku": "SKU-1", "status": "ACCEPTED", "submissionId": "s"})
    ok = run(
        "create_listing",
        {
            "sku": "SKU-1",
            "listing_kind": "new_product",
            "product_type": "MUG",
            "attributes": attributes,
        },
    )
    assert ok["ok"] is True
    assert amazon.requests[0].body == {
        "productType": "MUG",
        "requirements": "LISTING",
        "attributes": attributes,
    }


def test_attribute_for_another_marketplace_is_refused(amazon, run):
    attributes = {"item_name": [{"value": "Mug", "marketplace_id": "ATVPDKIKX0DER"}]}
    response = run(
        "create_listing",
        {
            "sku": "SKU-1",
            "listing_kind": "new_product",
            "product_type": "MUG",
            "attributes": attributes,
        },
    )
    assert response["error_code"] == "unsupported_filter"
    assert amazon.requests == []


def test_invalid_submission_is_a_definite_refusal(amazon, run):
    issues = [
        {
            "code": "8541",
            "message": "conflict",
            "severity": "ERROR",
            "categories": ["INVALID_ATTRIBUTE"],
        }
    ]
    amazon.reply(
        "PATCH",
        ITEM,
        {"sku": "SKU-1", "status": "INVALID", "submissionId": "sub-2", "issues": issues},
    )
    response = run(
        "update_listing_attributes",
        {
            "sku": "SKU-1",
            "product_type": "MUG",
            "patches": [
                {"op": "replace", "path": "/attributes/item_name", "value": [{"value": "New"}]}
            ],
        },
    )
    assert response["ok"] is True
    result = response["result"]
    assert result["accepted"] is False and result["status"] == "INVALID"
    assert result["external_effect_status"] == "failed"
    assert result["definitely_no_external_effect"] is True
    assert result["issues"][0]["code"] == "8541"
    assert amazon.requests[0].body == {
        "productType": "MUG",
        "patches": [
            {"op": "replace", "path": "/attributes/item_name", "value": [{"value": "New"}]}
        ],
    }


def test_patch_paths_are_limited_to_top_level_attributes(amazon, run):
    for patch in (
        {"op": "replace", "path": "/productType", "value": [{}]},
        {"op": "replace", "path": "/attributes/a/b", "value": [{}]},
        {"op": "merge", "path": "/attributes/item_name", "value": [{}]},
        {"op": "add", "path": "/attributes/item_name"},
    ):
        response = run(
            "update_listing_attributes", {"sku": "SKU-1", "product_type": "MUG", "patches": [patch]}
        )
        assert response["error_code"] == "invalid_payload", patch
    assert amazon.requests == []
    amazon.reply("PATCH", ITEM, {"sku": "SKU-1", "status": "ACCEPTED", "submissionId": "s"})
    ok = run(
        "update_listing_attributes",
        {
            "sku": "SKU-1",
            "product_type": "MUG",
            "patches": [{"op": "delete", "path": "/attributes/bullet_point"}],
        },
    )
    assert ok["ok"] is True


def test_mutation_in_test_mode_sends_nothing(amazon, run):
    response = run(
        "update_listing_attributes",
        {
            "sku": "SKU-1",
            "product_type": "MUG",
            "patches": [{"op": "delete", "path": "/attributes/bullet_point"}],
        },
        test_mode=True,
    )
    assert response["ok"] is True
    assert response["result"]["external_effect_status"] == "suppressed"
    assert "submission_id" not in response["result"]
    assert amazon.requests == []


def test_mutation_without_external_effect_id_is_refused(amazon, run):
    response = run(
        "create_listing",
        {
            "sku": "SKU-1",
            "listing_kind": "new_product",
            "product_type": "MUG",
            "attributes": {"a": [{"value": 1}]},
        },
        effect_id=None,
    )
    assert response["error_code"] == "missing_idempotency_key"
    assert amazon.requests == []


def test_mutation_server_error_is_outcome_unknown_and_not_retried(amazon, run):
    amazon.reply("PATCH", ITEM, {"errors": [{"code": "InternalFailure"}]}, status=500)
    response = run(
        "update_listing_attributes",
        {
            "sku": "SKU-1",
            "product_type": "MUG",
            "patches": [{"op": "delete", "path": "/attributes/x"}],
        },
    )
    assert response["error_code"] == "outcome_unknown"
    assert response["external_effect_status"] == "unknown"
    assert response["definitely_no_external_effect"] is False
    assert len(amazon.requests) == 1


def test_mutation_forbidden_is_a_definite_failure(amazon, run):
    amazon.reply("PATCH", ITEM, {"errors": [{"code": "Unauthorized"}]}, status=403)
    response = run(
        "update_listing_attributes",
        {
            "sku": "SKU-1",
            "product_type": "MUG",
            "patches": [{"op": "delete", "path": "/attributes/x"}],
        },
    )
    assert response["error_code"] == "access_denied"
    assert response["definitely_no_external_effect"] is True
    assert response["external_effect_status"] == "failed"


def test_mutation_throttled_is_retried_once_then_accepted(amazon, run):
    amazon.reply("PATCH", ITEM, {"errors": [{"code": "QuotaExceeded"}]}, status=429)
    amazon.reply("PATCH", ITEM, {"sku": "SKU-1", "status": "ACCEPTED", "submissionId": "s"})
    response = run(
        "update_listing_attributes",
        {
            "sku": "SKU-1",
            "product_type": "MUG",
            "patches": [{"op": "delete", "path": "/attributes/x"}],
        },
    )
    assert response["ok"] is True
    assert len(amazon.requests) == 2


def test_mutation_answer_without_status_is_outcome_unknown(amazon, run):
    amazon.reply("PATCH", ITEM, {"sku": "SKU-1"})
    response = run(
        "update_listing_attributes",
        {
            "sku": "SKU-1",
            "product_type": "MUG",
            "patches": [{"op": "delete", "path": "/attributes/x"}],
        },
    )
    assert response["error_code"] == "outcome_unknown"
