from __future__ import annotations

from conftest import SELLER_ID, UK_MARKETPLACE_ID, US_MARKETPLACE_ID

SEARCH = "/catalog/2022-04-01/items"
ITEM = {
    "asin": "B000TEST01",
    "summaries": [
        {"marketplaceId": US_MARKETPLACE_ID, "itemName": "US name"},
        {
            "marketplaceId": UK_MARKETPLACE_ID,
            "itemName": "Mug",
            "brand": "Acme",
            "itemClassification": "VARIATION_PARENT",
        },
    ],
    "images": [
        {
            "marketplaceId": UK_MARKETPLACE_ID,
            "images": [
                {
                    "variant": "PT01",
                    "link": "https://m.media-amazon.com/a.jpg",
                    "height": 1,
                    "width": 1,
                },
                {
                    "variant": "MAIN",
                    "link": "https://m.media-amazon.com/main.jpg",
                    "height": 500,
                    "width": 500,
                },
            ],
        }
    ],
    "identifiers": [
        {
            "marketplaceId": UK_MARKETPLACE_ID,
            "identifiers": [{"identifierType": "EAN", "identifier": "5000000000000"}],
        }
    ],
    "productTypes": [{"marketplaceId": UK_MARKETPLACE_ID, "productType": "MUG"}],
    "relationships": [
        {
            "marketplaceId": UK_MARKETPLACE_ID,
            "relationships": [
                {
                    "type": "VARIATION",
                    "childAsins": ["B000CHILD1"],
                    "variationTheme": {"attributes": ["color"], "theme": "COLOR"},
                }
            ],
        }
    ],
}


def test_search_by_keywords_normalizes_the_marketplace_entry(amazon, run):
    amazon.reply(
        "GET", SEARCH, {"numberOfResults": 2, "items": [ITEM], "pagination": {"nextToken": "amz-1"}}
    )
    response = run(
        "search_catalog_items",
        {
            "keywords": ["mug", "blue"],
            "brand_names": ["Acme"],
            "included_data": ["summaries", "images"],
            "page_size": 20,
        },
    )
    assert response["ok"] is True, response
    result = response["result"]
    item = result["items"][0]
    assert item["item_name"] == "Mug"
    assert item["brand"] == "Acme"
    assert item["main_image_url"] == "https://m.media-amazon.com/main.jpg"
    assert item["identifiers"] == [{"type": "EAN", "value": "5000000000000"}]
    assert item["variation"]["child_asins"] == ["B000CHILD1"]
    assert item["product_type"] == "MUG"
    assert result["number_of_results"] == 2 and result["next_token"]
    query = amazon.requests[0].query
    assert query["keywords"] == "mug,blue"
    assert query["brandNames"] == "Acme"
    assert query["marketplaceIds"] == UK_MARKETPLACE_ID
    assert "sellerId" not in query

    amazon.reply("GET", SEARCH, {"numberOfResults": 2, "items": []})
    run(
        "search_catalog_items",
        {
            "keywords": ["mug", "blue"],
            "brand_names": ["Acme"],
            "included_data": ["summaries", "images"],
            "next_token": result["next_token"],
        },
    )
    assert amazon.requests[1].query["pageToken"] == "amz-1"


def test_sku_search_sends_the_connection_seller(amazon, run):
    amazon.reply("GET", SEARCH, {"numberOfResults": 0, "items": []})
    run("search_catalog_items", {"identifiers": ["SKU-1"], "identifiers_type": "SKU"})
    assert amazon.requests[0].query["sellerId"] == SELLER_ID
    amazon.reply("GET", SEARCH, {"numberOfResults": 0, "items": []})
    run("search_catalog_items", {"identifiers": ["5000000000000"], "identifiers_type": "EAN"})
    assert "sellerId" not in amazon.requests[1].query


def test_search_needs_exactly_one_way(amazon, run):
    assert run("search_catalog_items", {})["error_code"] == "invalid_payload"
    assert (
        run(
            "search_catalog_items",
            {"keywords": ["a"], "identifiers": ["B"], "identifiers_type": "ASIN"},
        )["error_code"]
        == "invalid_payload"
    )
    assert run("search_catalog_items", {"identifiers": ["B"]})["error_code"] == "invalid_payload"
    assert (
        run("search_catalog_items", {"keywords": ["a"], "identifiers_type": "ASIN"})["error_code"]
        == "invalid_payload"
    )
    assert (
        run(
            "search_catalog_items",
            {"identifiers": ["B"], "identifiers_type": "ASIN", "brand_names": ["Acme"]},
        )["error_code"]
        == "unsupported_filter"
    )
    assert (
        run("search_catalog_items", {"keywords": ["a"], "page_size": 21})["error_code"]
        == "invalid_payload"
    )
    assert (
        run("search_catalog_items", {"keywords": [str(i) for i in range(21)]})["error_code"]
        == "invalid_payload"
    )
    assert (
        run("search_catalog_items", {"keywords": ["a"], "marketplace_id": US_MARKETPLACE_ID})[
            "error_code"
        ]
        == "invalid_payload"
    )
    assert amazon.requests == []


def test_get_catalog_item(amazon, run):
    amazon.reply("GET", "/catalog/2022-04-01/items/B000TEST01", ITEM)
    response = run(
        "get_catalog_item", {"asin": "B000TEST01", "included_data": ["relationships", "summaries"]}
    )
    assert response["ok"] is True, response
    assert response["result"]["item"]["variation"]["theme"] == {
        "attributes": ["color"],
        "theme": "COLOR",
    }
    assert response["result"]["item"]["amazon"]["asin"] == "B000TEST01"
    assert amazon.requests[0].query["includedData"] == "relationships,summaries"


def test_get_catalog_item_refuses_a_malformed_asin(amazon, run):
    assert run("get_catalog_item", {"asin": "b000/test1"})["error_code"] == "invalid_payload"
    assert amazon.requests == []


def test_catalog_not_found_and_server_error(amazon, run):
    amazon.reply(
        "GET",
        "/catalog/2022-04-01/items/B000TEST01",
        {"errors": [{"code": "NotFound"}]},
        status=404,
    )
    assert run("get_catalog_item", {"asin": "B000TEST01"})["error_code"] == "not_found"
    for _ in range(4):
        amazon.reply(
            "GET",
            "/catalog/2022-04-01/items/B000TEST01",
            {"errors": [{"code": "InternalFailure"}]},
            status=500,
        )
    response = run("get_catalog_item", {"asin": "B000TEST01"})
    assert response["error_code"] == "upstream_failure"
    assert len(amazon.requests) == 5
