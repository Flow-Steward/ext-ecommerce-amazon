from __future__ import annotations

import json
import re

from conftest import SELLER_ID, UK_MARKETPLACE_ID

from runtime.sections import pricing

ITEM = f"/listings/2021-08-01/items/{SELLER_ID}/SKU-1"
V0_PAYLOAD = {
    "payload": {
        "marketplaceId": UK_MARKETPLACE_ID,
        "ASIN": "B000TEST01",
        "SKU": "SKU-1",
        "ItemCondition": "New",
        "status": "Success",
        "Identifier": {"MarketplaceId": UK_MARKETPLACE_ID, "ItemCondition": "New"},
        "Summary": {
            "TotalOfferCount": 2,
            "NumberOfOffers": [
                {"condition": "new", "fulfillmentChannel": "Merchant", "OfferCount": 2}
            ],
            "LowestPrices": [
                {
                    "condition": "new",
                    "fulfillmentChannel": "Merchant",
                    "LandedPrice": {"CurrencyCode": "GBP", "Amount": "__9.10__"},
                    "ListingPrice": {"CurrencyCode": "GBP", "Amount": "__8.10__"},
                    "Shipping": {"CurrencyCode": "GBP", "Amount": 1.00},
                }
            ],
            "BuyBoxPrices": [
                {
                    "condition": "New",
                    "LandedPrice": {"CurrencyCode": "GBP", "Amount": "__9.10__"},
                    "ListingPrice": {"CurrencyCode": "GBP", "Amount": "__8.10__"},
                    "Shipping": {"CurrencyCode": "GBP", "Amount": 1.00},
                }
            ],
        },
        "Offers": [
            {
                "MyOffer": False,
                "SellerId": "A2OTHER",
                "SubCondition": "new",
                "ListingPrice": {"CurrencyCode": "GBP", "Amount": "__8.10__"},
                "Shipping": {"CurrencyCode": "GBP", "Amount": 1.00},
                "ShippingTime": {"maximumHours": 48},
                "IsFulfilledByAmazon": False,
                "IsBuyBoxWinner": True,
            },
            {
                "MyOffer": True,
                "SellerId": SELLER_ID,
                "SubCondition": "new",
                "ListingPrice": {"CurrencyCode": "GBP", "Amount": 9.99},
                "Shipping": {"CurrencyCode": "GBP", "Amount": 0},
                "ShippingTime": {"maximumHours": 24},
                "IsFulfilledByAmazon": True,
                "IsBuyBoxWinner": False,
            },
        ],
    }
}


def _literal(payload):
    """JSON text in which "__1.50__" placeholders become the number literal 1.50."""
    return re.sub(r'"__([0-9.]+)__"', r"\1", json.dumps(payload)).encode()


def test_get_own_offer_splits_standard_and_business_price(amazon, run):
    amazon.reply(
        "GET",
        ITEM,
        {
            "sku": "SKU-1",
            "summaries": [
                {
                    "marketplaceId": UK_MARKETPLACE_ID,
                    "asin": "B000TEST01",
                    "productType": "MUG",
                    "status": [],
                    "createdDate": "x",
                    "lastUpdatedDate": "x",
                }
            ],
            "offers": [
                {
                    "marketplaceId": UK_MARKETPLACE_ID,
                    "offerType": "B2C",
                    "price": {"amount": 19.99, "currencyCode": "GBP"},
                    "audience": {"value": "ALL", "displayName": "All"},
                },
                {
                    "marketplaceId": UK_MARKETPLACE_ID,
                    "offerType": "B2B",
                    "price": {"amount": "17.50", "currencyCode": "GBP"},
                    "audience": {"value": "B2B"},
                },
            ],
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "AMAZON_EU"}],
        },
    )
    response = run("get_own_offer", {"sku": "SKU-1"})
    assert response["ok"] is True, response
    result = response["result"]
    assert result["price"] == {"amount": "19.99", "currency_code": "GBP"}
    assert result["business_price"] == {"amount": "17.50", "currency_code": "GBP"}
    assert result["offers"][0]["audience"] == "ALL"
    assert result["fulfillment_channels"] == ["AMAZON_EU"]
    assert amazon.requests[0].query["includedData"] == "summaries,offers,fulfillmentAvailability"


def test_get_listing_offers_normalizes_money_exactly(amazon, run):
    amazon.reply("GET", "/products/pricing/v0/listings/SKU-1/offers", raw=_literal(V0_PAYLOAD))
    response = run(
        "get_listing_offers", {"sku": "SKU-1", "item_condition": "New", "customer_type": "Business"}
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert result["summary"]["lowest_prices"][0]["landed_price"] == {
        "amount": "9.10",
        "currency_code": "GBP",
    }
    assert result["summary"]["buy_box_prices"][0]["listing_price"] == {
        "amount": "8.10",
        "currency_code": "GBP",
    }
    assert result["own_offer"]["listing_price"] == {"amount": "9.99", "currency_code": "GBP"}
    assert result["own_offer"]["shipping"] == {"amount": "0", "currency_code": "GBP"}
    assert result["offers"][0]["is_buy_box_winner"] is True
    assert amazon.requests[0].query == {
        "MarketplaceId": UK_MARKETPLACE_ID,
        "ItemCondition": "New",
        "CustomerType": "Business",
    }


def test_get_item_offers_by_asin(amazon, run):
    amazon.reply("GET", "/products/pricing/v0/items/B000TEST01/offers", raw=_literal(V0_PAYLOAD))
    response = run("get_item_offers", {"asin": "B000TEST01", "item_condition": "Used"})
    assert response["ok"] is True, response
    assert amazon.requests[0].query["ItemCondition"] == "Used"
    assert (
        run("get_item_offers", {"asin": "B000TEST01", "item_condition": "used"})["error_code"]
        == "invalid_payload"
    )


def test_competitive_summary_keeps_per_item_errors(amazon, run):
    amazon.reply(
        "POST",
        "/batches/products/pricing/2022-05-01/items/competitiveSummary",
        {
            "responses": [
                {
                    "status": {"statusCode": 200, "reasonPhrase": "OK"},
                    "body": {
                        "asin": "B000TEST01",
                        "marketplaceId": UK_MARKETPLACE_ID,
                        "featuredBuyingOptions": [
                            {
                                "buyingOptionType": "New",
                                "segmentedFeaturedOffers": [
                                    {
                                        "sellerId": "A2",
                                        "condition": "New",
                                        "fulfillmentType": "AFN",
                                        "listingPrice": {"amount": 10.5, "currencyCode": "GBP"},
                                        "shippingOptions": [
                                            {
                                                "shippingOptionType": "DEFAULT",
                                                "price": {"amount": 0, "currencyCode": "GBP"},
                                            }
                                        ],
                                        "featuredOfferSegments": [],
                                    }
                                ],
                            }
                        ],
                        "referencePrices": [
                            {
                                "name": "CompetitivePriceThreshold",
                                "price": {"amount": 11.25, "currencyCode": "GBP"},
                            }
                        ],
                        "lowestPricedOffers": [
                            {
                                "lowestPricedOffersInput": {
                                    "itemCondition": "New",
                                    "offerType": "Consumer",
                                },
                                "offers": [
                                    {
                                        "sellerId": "A3",
                                        "condition": "New",
                                        "fulfillmentType": "MFN",
                                        "listingPrice": {"amount": 9.99, "currencyCode": "GBP"},
                                    }
                                ],
                            }
                        ],
                    },
                },
                {
                    "status": {"statusCode": 400, "reasonPhrase": "Bad Request"},
                    "body": {
                        "asin": "B000BADBAD",
                        "marketplaceId": UK_MARKETPLACE_ID,
                        "errors": [{"code": "InvalidInput", "message": "bad asin"}],
                    },
                },
            ]
        },
    )
    response = run(
        "get_competitive_summary",
        {
            "asins": ["B000TEST01", "B000BADBAD"],
            "included_data": ["featuredBuyingOptions", "referencePrices", "lowestPricedOffers"],
            "lowest_priced_offers_conditions": ["New"],
        },
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert result["failed_count"] == 1
    first, second = result["results"]
    assert first["ok"] is True
    assert first["featured_buying_options"][0]["segmented_featured_offers"][0]["listing_price"] == {
        "amount": "10.5",
        "currency_code": "GBP",
    }
    assert first["reference_prices"] == [
        {"name": "CompetitivePriceThreshold", "price": {"amount": "11.25", "currency_code": "GBP"}}
    ]
    assert first["lowest_priced_offers"][0]["offers"][0]["listing_price"]["amount"] == "9.99"
    assert second["ok"] is False and second["status_code"] == 400
    assert second["errors"][0]["code"] == "InvalidInput"
    body = amazon.requests[0].body
    assert body["requests"][0] == {
        "asin": "B000TEST01",
        "marketplaceId": UK_MARKETPLACE_ID,
        "includedData": ["featuredBuyingOptions", "referencePrices", "lowestPricedOffers"],
        "method": "GET",
        "uri": "/products/pricing/2022-05-01/items/competitiveSummary",
        "lowestPricedOffersInputs": [{"itemCondition": "New", "offerType": "Consumer"}],
    }


def test_competitive_summary_input_rules(amazon, run):
    asins = [f"B00000{i:04d}" for i in range(21)]
    assert (
        run("get_competitive_summary", {"asins": asins, "included_data": ["referencePrices"]})[
            "error_code"
        ]
        == "invalid_payload"
    )
    assert (
        run(
            "get_competitive_summary",
            {"asins": ["B000TEST01", "B000TEST01"], "included_data": ["referencePrices"]},
        )["error_code"]
        == "invalid_payload"
    )
    assert (
        run(
            "get_competitive_summary",
            {"asins": ["B000TEST01"], "included_data": ["lowestPricedOffers"]},
        )["error_code"]
        == "invalid_payload"
    )
    assert (
        run(
            "get_competitive_summary",
            {
                "asins": ["B000TEST01"],
                "included_data": ["referencePrices"],
                "lowest_priced_offers_conditions": ["New"],
            },
        )["error_code"]
        == "unsupported_filter"
    )
    assert amazon.requests == []


def test_featured_offer_expected_price(amazon, run):
    amazon.reply(
        "POST",
        "/batches/products/pricing/2022-05-01/offer/featuredOfferExpectedPrice",
        raw=_literal(
            {
                "responses": [
                    {
                        "status": {"statusCode": 200},
                        "headers": {},
                        "request": {"marketplaceId": UK_MARKETPLACE_ID, "sku": "SKU-1"},
                        "body": {
                            "offerIdentifier": {
                                "marketplaceId": UK_MARKETPLACE_ID,
                                "asin": "B000TEST01",
                                "sku": "SKU-1",
                                "sellerId": SELLER_ID,
                            },
                            "featuredOfferExpectedPriceResults": [
                                {
                                    "resultStatus": "VALID_FOEP",
                                    "featuredOfferExpectedPrice": {
                                        "listingPrice": {"amount": 8.75, "currencyCode": "GBP"}
                                    },
                                    "currentFeaturedOffer": {
                                        "offerIdentifier": {
                                            "marketplaceId": UK_MARKETPLACE_ID,
                                            "asin": "B000TEST01",
                                            "sellerId": "A2",
                                        },
                                        "condition": "New",
                                        "price": {
                                            "listingPrice": {
                                                "amount": "__8.80__",
                                                "currencyCode": "GBP",
                                            }
                                        },
                                    },
                                }
                            ],
                        },
                    },
                    {
                        "status": {"statusCode": 404},
                        "headers": {},
                        "request": {"marketplaceId": UK_MARKETPLACE_ID, "sku": "SKU-X"},
                        "body": {"errors": [{"code": "NOT_FOUND", "message": "no offer"}]},
                    },
                ]
            }
        ),
    )
    response = run("get_featured_offer_expected_price", {"skus": ["SKU-1", "SKU-X"]})
    assert response["ok"] is True, response
    first, second = response["result"]["results"]
    assert first["results"][0]["expected_price"] == {"amount": "8.75", "currency_code": "GBP"}
    assert first["results"][0]["current_featured_offer"]["listing_price"] == {
        "amount": "8.80",
        "currency_code": "GBP",
    }
    assert second["sku"] == "SKU-X" and second["ok"] is False
    assert amazon.requests[0].body["requests"][1] == {
        "uri": "/products/pricing/2022-05-01/offer/featuredOfferExpectedPrice",
        "method": "GET",
        "marketplaceId": UK_MARKETPLACE_ID,
        "sku": "SKU-X",
    }
    assert (
        run("get_featured_offer_expected_price", {"skus": [f"S{i}" for i in range(41)]})[
            "error_code"
        ]
        == "invalid_payload"
    )


def test_set_price_sends_only_purchasable_offer(amazon, run):
    amazon.reply(
        "PATCH", ITEM, {"sku": "SKU-1", "status": "ACCEPTED", "submissionId": "sub-9", "issues": []}
    )
    response = run(
        "set_price",
        {"sku": "SKU-1", "product_type": "MUG", "price": "19.99", "currency_code": "GBP"},
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert (
        result["accepted"] is True
        and result["applied"] is False
        and result["check_with"] == "get_listing"
    )
    request = amazon.requests[0]
    assert request.query["marketplaceIds"] == UK_MARKETPLACE_ID
    assert request.body == {
        "productType": "MUG",
        "patches": [
            {
                "op": "merge",
                "path": "/attributes/purchasable_offer",
                "value": [
                    {
                        "marketplace_id": UK_MARKETPLACE_ID,
                        "currency": "GBP",
                        "audience": "ALL",
                        "our_price": [{"schedule": [{"value_with_tax": 19.99}]}],
                    }
                ],
            }
        ],
    }


def test_price_numbers_serialize_exactly():
    for text, wire in (
        ("19.99", "19.99"),
        ("0.1", "0.1"),
        ("20", "20"),
        ("20.00", "20"),
        ("1234567.891234", "1234567.891234"),
    ):
        assert json.dumps(pricing.price_number(text, "price")) == wire


def test_set_price_with_business_price(amazon, run):
    amazon.reply("PATCH", ITEM, {"sku": "SKU-1", "status": "ACCEPTED", "submissionId": "s"})
    run(
        "set_price",
        {
            "sku": "SKU-1",
            "product_type": "MUG",
            "price": "20",
            "business_price": "18.5",
            "currency_code": "GBP",
        },
    )
    (patch,) = amazon.requests[0].body["patches"]
    assert [entry["audience"] for entry in patch["value"]] == ["ALL", "B2B"]
    assert patch["value"][0]["our_price"][0]["schedule"][0]["value_with_tax"] == 20
    assert patch["value"][1]["our_price"][0]["schedule"][0]["value_with_tax"] == 18.5


def test_set_price_input_checks_send_nothing(amazon, run):
    base = {"sku": "SKU-1", "product_type": "MUG", "currency_code": "GBP"}
    for price in ("19,99", "1e3", "-1", "0", "0.00", "19.9999999", "12345678901234567"):
        assert run("set_price", {**base, "price": price})["error_code"] == "invalid_payload", price
    assert (
        run("set_price", {**base, "price": "1", "currency_code": "EUR"})["error_code"]
        == "invalid_payload"
    )
    assert (
        run("set_price", {**base, "price": "1", "currency_code": "gbp"})["error_code"]
        == "invalid_payload"
    )
    assert run("set_price", {**base, "price": 19.99})["error_code"] == "invalid_payload"
    assert (
        run("set_price", {**base, "price": "1", "marketplace_id": "ATVPDKIKX0DER"})["error_code"]
        == "invalid_payload"
    )
    assert amazon.requests == []


def test_set_price_modes(amazon, run):
    inputs = {"sku": "SKU-1", "product_type": "MUG", "price": "10", "currency_code": "GBP"}
    assert (
        run("set_price", inputs, test_mode=True)["result"]["external_effect_status"] == "suppressed"
    )
    assert run("set_price", inputs, effect_id=None)["error_code"] == "missing_idempotency_key"
    assert amazon.requests == []
    amazon.reply("PATCH", ITEM, {}, status=504)
    unknown = run("set_price", inputs)
    assert unknown["error_code"] == "outcome_unknown"
    assert len(amazon.requests) == 1
    amazon.reply(
        "PATCH",
        ITEM,
        {
            "sku": "SKU-1",
            "status": "INVALID",
            "submissionId": "s",
            "issues": [
                {"code": "x", "message": "price too low", "severity": "ERROR", "categories": []}
            ],
        },
    )
    refused = run("set_price", inputs)["result"]
    assert refused["accepted"] is False and refused["external_effect_status"] == "failed"
