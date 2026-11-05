"""Prices: the seller's own offer, the competition, and price updates.

* ``get_own_offer`` reads the listing's own offers (Listings Items).
* ``get_listing_offers`` / ``get_item_offers`` are Product Pricing v0: the offers
  on an item, up to 20, with Amazon's summary of lowest and Buy Box prices.
* ``get_competitive_summary`` / ``get_featured_offer_expected_price`` are the
  Product Pricing 2022-05-01 batch calls. Each element of a batch succeeds or
  fails on its own, so every result carries its own status and errors.
* ``set_price`` patches only the ``purchasable_offer`` attribute.

Money is always ``{"amount": "<decimal string>", "currency_code"}``; amounts are
never added together here (a landed price is Amazon's figure, not a sum).

``set_price`` uses ``op: merge`` on ``/attributes/purchasable_offer`` with the
selectors Amazon documents (``marketplace_id``, ``currency``, ``audience``):
merge changes only ``our_price`` of the selected offer and leaves the other
audience, discounts and price limits as they are, where ``replace`` would
rewrite the whole attribute. Amazon's ``value_with_tax`` is a JSON number; an
amount is sent as a number only when it converts to one exactly.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .. import errors, listings_core, validation
from ..errors import ExtensionError
from ..listings_core import (
    ASIN_INPUT,
    GET_CALL,
    ISSUE_LOCALE_INPUT,
    PATCH_CALL,
    ROLE_PRODUCT_LISTING,
    SKU_INPUT,
    SUBMISSION_NOTE,
    SUBMISSION_OUTPUTS,
)
from ..registry import (
    ARRAY_OF_OBJECTS,
    CONNECTION_REF,
    INTEGER,
    MONEY,
    NULLABLE_OBJECT,
    NULLABLE_TEXT,
    OBJECT,
    TEXT,
    Context,
    Operation,
    enum,
    enum_list,
    out,
    text,
)
from ..values import as_list, dig, money

LISTING_OFFERS_PATH = "/products/pricing/v0/listings/{SellerSKU}/offers"
ITEM_OFFERS_PATH = "/products/pricing/v0/items/{Asin}/offers"
COMPETITIVE_SUMMARY_PATH = "/batches/products/pricing/2022-05-01/items/competitiveSummary"
COMPETITIVE_SUMMARY_URI = "/products/pricing/2022-05-01/items/competitiveSummary"
FOEP_PATH = "/batches/products/pricing/2022-05-01/offer/featuredOfferExpectedPrice"
FOEP_URI = "/products/pricing/2022-05-01/offer/featuredOfferExpectedPrice"

CONDITIONS = ("New", "Used", "Collectible", "Refurbished", "Club")
CUSTOMER_TYPES = ("Consumer", "Business")
SUMMARY_DATA = ("featuredBuyingOptions", "referencePrices", "lowestPricedOffers", "similarItems")
MAX_SUMMARY_ASINS = 20
MAX_FOEP_SKUS = 40
#: Fifteen significant digits always survive a round trip through a JSON number.
MAX_PRICE_DIGITS = 15

#: The currency of each marketplace's prices; a price in another currency is refused.
MARKETPLACE_CURRENCIES = {
    "CA": "CAD",
    "US": "USD",
    "MX": "MXN",
    "BR": "BRL",
    "IE": "EUR",
    "ES": "EUR",
    "UK": "GBP",
    "FR": "EUR",
    "BE": "EUR",
    "NL": "EUR",
    "DE": "EUR",
    "IT": "EUR",
    "SE": "SEK",
    "ZA": "ZAR",
    "PL": "PLN",
    "EG": "EGP",
    "TR": "TRY",
    "SA": "SAR",
    "AE": "AED",
    "IN": "INR",
    "SG": "SGD",
    "AU": "AUD",
    "JP": "JPY",
}

_ROLE_PRICING = "Pricing"


# -- own offer -----------------------------------------------------------------


def _get_own_offer(context: Context) -> dict[str, Any]:
    raw = listings_core.get_listing_item(
        context, str(context.input["sku"]), ["summaries", "offers", "fulfillmentAvailability"]
    )
    listing = listings_core.normalize_listing(raw, context.connection.marketplace_id)
    offers = [
        offer
        for offer in listing["offers"] or []
        if offer["marketplace_id"] == context.connection.marketplace_id
    ]
    standard = next((offer for offer in offers if offer["offer_type"] == "B2C"), None)
    business = next((offer for offer in offers if offer["offer_type"] == "B2B"), None)
    channels = listing["fulfillment_availability"] or []
    return {
        "sku": listing["sku"],
        "asin": listing["asin"],
        "price": standard["price"] if standard else None,
        "business_price": business["price"] if business else None,
        "offers": offers,
        "fulfillment_channels": [channel["fulfillment_channel_code"] for channel in channels],
        "amazon": {
            "offers": raw.get("offers") or [],
            "fulfillmentAvailability": raw.get("fulfillmentAvailability") or [],
        },
    }


# -- Product Pricing v0 ----------------------------------------------------------


def _price_point(raw: Any) -> dict[str, Any]:
    return {
        "condition": dig(raw, "condition"),
        "fulfillment_channel": dig(raw, "fulfillmentChannel"),
        "offer_type": dig(raw, "offerType"),
        "quantity_tier": dig(raw, "quantityTier"),
        "landed_price": money(dig(raw, "LandedPrice")),
        "listing_price": money(dig(raw, "ListingPrice")),
        "shipping": money(dig(raw, "Shipping")),
        "points": dig(raw, "Points", "PointsNumber"),
        "seller_id": dig(raw, "sellerId"),
    }


def _v0_offer(raw: Any) -> dict[str, Any]:
    return {
        "my_offer": dig(raw, "MyOffer"),
        "seller_id": dig(raw, "SellerId"),
        "offer_type": dig(raw, "offerType"),
        "sub_condition": dig(raw, "SubCondition"),
        "listing_price": money(dig(raw, "ListingPrice")),
        "shipping": money(dig(raw, "Shipping")),
        "points": dig(raw, "Points", "PointsNumber"),
        "is_fulfilled_by_amazon": dig(raw, "IsFulfilledByAmazon"),
        "is_buy_box_winner": dig(raw, "IsBuyBoxWinner"),
        "is_featured_merchant": dig(raw, "IsFeaturedMerchant"),
        "is_prime": dig(raw, "PrimeInformation", "IsPrime"),
        "shipping_time": dig(raw, "ShippingTime"),
        "ships_from_country": dig(raw, "ShipsFrom", "Country"),
        "feedback_count": dig(raw, "SellerFeedbackRating", "FeedbackCount"),
        "positive_feedback_percent": dig(
            raw, "SellerFeedbackRating", "SellerPositiveFeedbackRating"
        ),
        "amazon": raw,
    }


def _v0_offers(context: Context, path_template: str, path: dict[str, str]) -> dict[str, Any]:
    response = context.transport.call(
        "GET",
        path_template,
        path=path,
        query={
            "MarketplaceId": context.connection.marketplace_id,
            "ItemCondition": context.input["item_condition"],
            "CustomerType": context.input.get("customer_type"),
        },
        mutating=False,
        purpose="Amazon pricing offers",
    )
    payload = dig(response.body, "payload")
    summary = dig(payload, "Summary")
    offers = [_v0_offer(item) for item in as_list(dig(payload, "Offers"))]
    return {
        "asin": dig(payload, "ASIN") or dig(payload, "Identifier", "ASIN"),
        "sku": dig(payload, "SKU") or dig(payload, "Identifier", "SellerSKU"),
        "item_condition": dig(payload, "ItemCondition"),
        "status": dig(payload, "status"),
        "summary": {
            "total_offer_count": dig(summary, "TotalOfferCount"),
            "number_of_offers": [
                {
                    "condition": dig(item, "condition"),
                    "fulfillment_channel": dig(item, "fulfillmentChannel"),
                    "offer_count": dig(item, "OfferCount"),
                }
                for item in as_list(dig(summary, "NumberOfOffers"))
            ],
            "lowest_prices": [_price_point(item) for item in as_list(dig(summary, "LowestPrices"))],
            "buy_box_prices": [
                _price_point(item) for item in as_list(dig(summary, "BuyBoxPrices"))
            ],
            "list_price": money(dig(summary, "ListPrice")),
            "competitive_price_threshold": money(dig(summary, "CompetitivePriceThreshold")),
            "suggested_lower_price_plus_shipping": money(
                dig(summary, "SuggestedLowerPricePlusShipping")
            ),
            "sales_rankings": dig(summary, "SalesRankings"),
            "offers_available_time": dig(summary, "OffersAvailableTime"),
        },
        "offers": offers,
        "own_offer": next((offer for offer in offers if offer["my_offer"] is True), None),
        "amazon": payload if isinstance(payload, dict) else {},
    }


def _get_listing_offers(context: Context) -> dict[str, Any]:
    return _v0_offers(context, LISTING_OFFERS_PATH, {"SellerSKU": str(context.input["sku"])})


def _get_item_offers(context: Context) -> dict[str, Any]:
    return _v0_offers(
        context, ITEM_OFFERS_PATH, {"Asin": listings_core.asin(context.input["asin"])}
    )


# -- Product Pricing 2022-05-01 batches ---------------------------------------------


def _offer_2022(raw: Any) -> dict[str, Any]:
    return {
        "seller_id": dig(raw, "sellerId"),
        "condition": dig(raw, "condition"),
        "sub_condition": dig(raw, "subCondition"),
        "fulfillment_type": dig(raw, "fulfillmentType"),
        "listing_price": money(dig(raw, "listingPrice")),
        "shipping_options": [
            {"type": dig(item, "shippingOptionType"), "price": money(dig(item, "price"))}
            for item in as_list(dig(raw, "shippingOptions"))
        ],
        "points": dig(raw, "points", "pointsNumber"),
        "prime": dig(raw, "primeDetails"),
        "featured_offer_segments": dig(raw, "featuredOfferSegments"),
    }


def _batch_status(raw: Any) -> tuple[int | None, bool]:
    code = dig(raw, "status", "statusCode")
    code = code if isinstance(code, int) and not isinstance(code, bool) else None
    errors_list = as_list(dig(raw, "body", "errors"))
    return code, code is not None and 200 <= code < 300 and not errors_list


def _distinct(values: list[str], name: str) -> list[str]:
    if len(set(values)) != len(values):
        raise ExtensionError(errors.INVALID_PAYLOAD, f"input.{name} must not repeat a value")
    return values


def _get_competitive_summary(context: Context) -> dict[str, Any]:
    data = context.input
    asins = _distinct([listings_core.asin(item, "asins") for item in data["asins"]], "asins")
    included = list(data["included_data"])
    conditions = data.get("lowest_priced_offers_conditions")
    if "lowestPricedOffers" in included and not conditions:
        raise ExtensionError(
            errors.INVALID_PAYLOAD,
            "lowest_priced_offers_conditions is required with lowestPricedOffers",
        )
    if conditions and "lowestPricedOffers" not in included:
        raise ExtensionError(
            errors.UNSUPPORTED_FILTER,
            "lowest_priced_offers_conditions only applies with lowestPricedOffers",
        )
    marketplace_id = context.connection.marketplace_id
    requests = []
    for item in asins:
        request: dict[str, Any] = {
            "asin": item,
            "marketplaceId": marketplace_id,
            "includedData": included,
            "method": "GET",
            "uri": COMPETITIVE_SUMMARY_URI,
        }
        if conditions:
            request["lowestPricedOffersInputs"] = [
                {"itemCondition": condition, "offerType": "Consumer"} for condition in conditions
            ]
        requests.append(request)
    response = context.transport.call(
        "POST",
        COMPETITIVE_SUMMARY_PATH,
        body={"requests": requests},
        mutating=False,
        purpose="Amazon competitive summary",
    )
    results = []
    for raw in as_list(dig(response.body, "responses")):
        body = dig(raw, "body")
        status_code, ok = _batch_status(raw)
        results.append(
            {
                "asin": dig(body, "asin"),
                "ok": ok,
                "status_code": status_code,
                "errors": as_list(dig(body, "errors")),
                "featured_buying_options": [
                    {
                        "buying_option_type": dig(option, "buyingOptionType"),
                        "segmented_featured_offers": [
                            _offer_2022(offer)
                            for offer in as_list(dig(option, "segmentedFeaturedOffers"))
                        ],
                    }
                    for option in as_list(dig(body, "featuredBuyingOptions"))
                ],
                "reference_prices": [
                    {"name": dig(item, "name"), "price": money(dig(item, "price"))}
                    for item in as_list(dig(body, "referencePrices"))
                ],
                "lowest_priced_offers": [
                    {
                        "item_condition": dig(item, "lowestPricedOffersInput", "itemCondition"),
                        "offer_type": dig(item, "lowestPricedOffersInput", "offerType"),
                        "offers": [_offer_2022(offer) for offer in as_list(dig(item, "offers"))],
                    }
                    for item in as_list(dig(body, "lowestPricedOffers"))
                ],
                "similar_items": dig(body, "similarItems"),
                "amazon": raw,
            }
        )
    return {
        "results": results,
        "count": len(results),
        "failed_count": sum(1 for item in results if not item["ok"]),
    }


def _featured_offer(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {
        "seller_id": dig(raw, "offerIdentifier", "sellerId"),
        "sku": dig(raw, "offerIdentifier", "sku"),
        "asin": dig(raw, "offerIdentifier", "asin"),
        "fulfillment_type": dig(raw, "offerIdentifier", "fulfillmentType"),
        "condition": dig(raw, "condition"),
        "listing_price": money(dig(raw, "price", "listingPrice")),
        "shipping_price": money(dig(raw, "price", "shippingPrice")),
        "points": dig(raw, "price", "points", "pointsNumber"),
    }


def _get_featured_offer_expected_price(context: Context) -> dict[str, Any]:
    skus = _distinct(list(context.input["skus"]), "skus")
    marketplace_id = context.connection.marketplace_id
    requests = [
        {"uri": FOEP_URI, "method": "GET", "marketplaceId": marketplace_id, "sku": sku}
        for sku in skus
    ]
    response = context.transport.call(
        "POST",
        FOEP_PATH,
        body={"requests": requests},
        mutating=False,
        purpose="Amazon featured offer expected price",
    )
    results = []
    for raw in as_list(dig(response.body, "responses")):
        body = dig(raw, "body")
        status_code, ok = _batch_status(raw)
        results.append(
            {
                "sku": dig(raw, "request", "sku") or dig(body, "offerIdentifier", "sku"),
                "asin": dig(body, "offerIdentifier", "asin"),
                "ok": ok,
                "status_code": status_code,
                "errors": as_list(dig(body, "errors")),
                "results": [
                    {
                        "result_status": dig(item, "resultStatus"),
                        "expected_price": money(
                            dig(item, "featuredOfferExpectedPrice", "listingPrice")
                        ),
                        "expected_points": dig(
                            item, "featuredOfferExpectedPrice", "points", "pointsNumber"
                        ),
                        "current_featured_offer": _featured_offer(
                            dig(item, "currentFeaturedOffer")
                        ),
                        "competing_featured_offer": _featured_offer(
                            dig(item, "competingFeaturedOffer")
                        ),
                    }
                    for item in as_list(dig(body, "featuredOfferExpectedPriceResults"))
                ],
                "amazon": raw,
            }
        )
    return {
        "results": results,
        "count": len(results),
        "failed_count": sum(1 for item in results if not item["ok"]),
    }


# -- price update ----------------------------------------------------------------


def price_number(value: Any, name: str) -> int | float:
    """A decimal string as the JSON number Amazon expects, only if it is exact."""
    text_value = validation.decimal_string(value, name)
    amount = Decimal(text_value)
    if amount <= 0:
        raise ExtensionError(errors.INVALID_PAYLOAD, f"input.{name} must be greater than zero")
    if len(amount.normalize().as_tuple().digits) > MAX_PRICE_DIGITS:
        raise ExtensionError(errors.INVALID_PAYLOAD, f"input.{name} has too many digits")
    if amount == amount.to_integral_value():
        return int(amount)
    number = float(text_value)
    if Decimal(repr(number)) != amount:  # pragma: no cover - excluded by the digit limit
        raise ExtensionError(errors.INVALID_PAYLOAD, f"input.{name} cannot be sent exactly")
    return number


def _offer_value(
    marketplace_id: str, currency: str, audience: str, amount: int | float
) -> dict[str, Any]:
    return {
        "marketplace_id": marketplace_id,
        "currency": currency,
        "audience": audience,
        "our_price": [{"schedule": [{"value_with_tax": amount}]}],
    }


def build_price_body(context: Context) -> dict[str, Any]:
    data = context.input
    currency = validation.currency_code(data["currency_code"], "currency_code")
    expected = MARKETPLACE_CURRENCIES.get(context.connection.marketplace.code)
    if expected is not None and currency != expected:
        raise ExtensionError(
            errors.INVALID_PAYLOAD,
            f"Prices in the {context.connection.marketplace.code} marketplace are in {expected}, not {currency}",
        )
    marketplace_id = context.connection.marketplace_id
    value = [_offer_value(marketplace_id, currency, "ALL", price_number(data["price"], "price"))]
    if data.get("business_price") is not None:
        value.append(
            _offer_value(
                marketplace_id,
                currency,
                "B2B",
                price_number(data["business_price"], "business_price"),
            )
        )
    return listings_core.patch_body(data["product_type"], "purchasable_offer", value)


def _set_price(context: Context) -> dict[str, Any]:
    return listings_core.submit(context, "PATCH", build_price_body(context), preview=False)


# -- rows -----------------------------------------------------------------------

_CONDITION_INPUT = enum("Item condition", "The condition of the offers to return.", CONDITIONS)
_CUSTOMER_INPUT = enum("Customer type", "Consumer (default) or Business offers.", CUSTOMER_TYPES)
_V0_OUTPUTS = (
    out("asin", NULLABLE_TEXT),
    out("sku", NULLABLE_TEXT),
    out("item_condition", NULLABLE_TEXT),
    out("status", NULLABLE_TEXT),
    out("summary", OBJECT),
    out("offers", ARRAY_OF_OBJECTS),
    out("own_offer", NULLABLE_OBJECT),
    out("amazon", OBJECT),
)
_BATCH_OUTPUTS = (
    out("results", ARRAY_OF_OBJECTS),
    out("count", INTEGER),
    out("failed_count", INTEGER),
)

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        operation_id="get_own_offer",
        display_name="Get my offer",
        description=(
            "Returns the seller's own offer for a SKU in the connection's marketplace: the standard price, "
            "the business price if any, each offer's audience, and the listing's fulfillment channels."
        ),
        section="pricing",
        properties={"connection_ref": CONNECTION_REF, "sku": SKU_INPUT},
        required=("connection_ref", "sku"),
        outputs=(
            out("sku", TEXT),
            out("asin", NULLABLE_TEXT),
            out("price", MONEY),
            out("business_price", MONEY),
            out("offers", ARRAY_OF_OBJECTS),
            out("fulfillment_channels", {"type": "array", "items": {"type": ["string", "null"]}}),
            out("amazon", OBJECT),
        ),
        handler=_get_own_offer,
        amazon_calls=(GET_CALL + " (includedData summaries, offers, fulfillmentAvailability)",),
        roles=(ROLE_PRODUCT_LISTING,),
    ),
    Operation(
        operation_id="get_listing_offers",
        display_name="Get offers on my listing",
        description=(
            "Returns up to 20 offers on the item behind one of the seller's SKUs, including competitors, "
            "with Amazon's lowest and Buy Box prices and which offer is the seller's own."
        ),
        section="pricing",
        properties={
            "connection_ref": CONNECTION_REF,
            "sku": SKU_INPUT,
            "item_condition": _CONDITION_INPUT,
            "customer_type": _CUSTOMER_INPUT,
        },
        required=("connection_ref", "sku", "item_condition"),
        outputs=_V0_OUTPUTS,
        handler=_get_listing_offers,
        amazon_calls=("Product Pricing v0 getListingOffers",),
        roles=(_ROLE_PRICING,),
    ),
    Operation(
        operation_id="get_item_offers",
        display_name="Get offers on an ASIN",
        description=(
            "Returns up to 20 offers on any ASIN in the connection's marketplace, with Amazon's lowest and "
            "Buy Box prices."
        ),
        section="pricing",
        properties={
            "connection_ref": CONNECTION_REF,
            "asin": ASIN_INPUT,
            "item_condition": _CONDITION_INPUT,
            "customer_type": _CUSTOMER_INPUT,
        },
        required=("connection_ref", "asin", "item_condition"),
        outputs=_V0_OUTPUTS,
        handler=_get_item_offers,
        amazon_calls=("Product Pricing v0 getItemOffers",),
        roles=(_ROLE_PRICING,),
    ),
    Operation(
        operation_id="get_competitive_summary",
        display_name="Get competitive summary",
        description=(
            "Returns, for 1 to 20 ASINs, the featured buying options, reference prices and lowest priced "
            "offers. Each ASIN has its own ok, status_code and errors: one failing ASIN does not fail the step."
        ),
        section="pricing",
        properties={
            "connection_ref": CONNECTION_REF,
            "asins": {
                "type": "array",
                "title": "ASINs",
                "description": "1 to 20 distinct ASINs.",
                "minItems": 1,
                "maxItems": MAX_SUMMARY_ASINS,
                "items": {"type": "string", "minLength": 10, "maxLength": 10},
            },
            "included_data": enum_list("Included data", "The data sets to return.", SUMMARY_DATA),
            "lowest_priced_offers_conditions": enum_list(
                "Lowest priced offer conditions",
                "With lowestPricedOffers: the conditions to return the lowest consumer offers for (up to 5).",
                CONDITIONS,
            ),
        },
        required=("connection_ref", "asins", "included_data"),
        outputs=_BATCH_OUTPUTS,
        handler=_get_competitive_summary,
        amazon_calls=("Product Pricing 2022-05-01 getCompetitiveSummary",),
        roles=(_ROLE_PRICING,),
        extra_error_codes=(errors.UNSUPPORTED_FILTER,),
    ),
    Operation(
        operation_id="get_featured_offer_expected_price",
        display_name="Get featured offer expected price",
        description=(
            "Returns, for 1 to 40 of the seller's SKUs, the price at or below which the offer may become the "
            "featured offer (Buy Box), with the current and competing featured offers. Not a guarantee. "
            "Each SKU has its own ok, status_code and errors."
        ),
        section="pricing",
        properties={
            "connection_ref": CONNECTION_REF,
            "skus": {
                "type": "array",
                "title": "SKUs",
                "description": "1 to 40 distinct seller SKUs.",
                "minItems": 1,
                "maxItems": MAX_FOEP_SKUS,
                "items": {"type": "string", "minLength": 1, "maxLength": 200},
            },
        },
        required=("connection_ref", "skus"),
        outputs=_BATCH_OUTPUTS,
        handler=_get_featured_offer_expected_price,
        amazon_calls=("Product Pricing 2022-05-01 getFeaturedOfferExpectedPriceBatch",),
        roles=(_ROLE_PRICING,),
    ),
    Operation(
        operation_id="set_price",
        display_name="Set a price",
        description=(
            "Sets the price (including tax where applicable) of the seller's offer for a SKU in the "
            "connection's marketplace, and optionally the business (B2B) price. Only the purchasable_offer "
            "attribute is sent, merged by marketplace, currency and audience, so nothing else on the listing "
            "changes. currency_code must be the marketplace currency." + SUBMISSION_NOTE
        ),
        section="pricing",
        properties={
            "connection_ref": CONNECTION_REF,
            "sku": SKU_INPUT,
            "product_type": listings_core.PRODUCT_TYPE_INPUT,
            "price": text(
                "Price", 'The new price as a decimal string, for example "19.99".', max_length=24
            ),
            "currency_code": text(
                "Currency",
                "ISO 4217 code of the marketplace currency, for example GBP.",
                max_length=3,
                min_length=3,
            ),
            "business_price": text(
                "Business price",
                "Optional B2B price as a decimal string (Amazon Business sellers).",
                max_length=24,
            ),
            "issue_locale": ISSUE_LOCALE_INPUT,
        },
        required=("connection_ref", "sku", "product_type", "price", "currency_code"),
        outputs=SUBMISSION_OUTPUTS,
        handler=_set_price,
        amazon_calls=(PATCH_CALL + " (purchasable_offer only)",),
        roles=(ROLE_PRODUCT_LISTING,),
        kind="action",
        mutating=True,
        effect_kind="amazon_price_update",
    ),
)
