"""Catalog Items 2022-04-01: products in the Amazon catalog.

A search is by identifiers or by keywords, never both (Amazon refuses the mix),
and the keyword-only refinements (brands, classifications, keyword locale) are
refused with identifiers rather than dropped. The seller ID is sent only for an
identifier search by SKU, where Amazon requires it, and it is the connection's.

Each item keeps Amazon's original object under ``amazon``; the normalized fields
are taken from the connection's marketplace entry of each per-marketplace list.
"""

from __future__ import annotations

from typing import Any

from .. import errors, pagination
from ..errors import ExtensionError
from ..listings_core import ASIN_INPUT, asin
from ..registry import (
    ARRAY_OF_OBJECTS,
    CONNECTION_REF,
    INTEGER,
    NEXT_TOKEN,
    NEXT_TOKEN_OUTPUT,
    OBJECT,
    Context,
    Operation,
    enum,
    enum_list,
    integer,
    out,
    text,
    text_list,
)
from ..values import as_list, dig

API_VERSION = "2022-04-01"
SEARCH_PATH = "/catalog/2022-04-01/items"
ITEM_PATH = "/catalog/2022-04-01/items/{asin}"

IDENTIFIER_TYPES = ("ASIN", "EAN", "GTIN", "ISBN", "JAN", "MINSAN", "SKU", "UPC")
INCLUDED_DATA = (
    "attributes",
    "classifications",
    "dimensions",
    "identifiers",
    "images",
    "productTypes",
    "relationships",
    "salesRanks",
    "summaries",
    "vendorDetails",
)
_KEYWORD_ONLY = ("brand_names", "classification_ids", "keywords_locale")


def _for_marketplace(entries: Any, marketplace_id: str) -> dict[str, Any] | None:
    for entry in as_list(entries):
        if isinstance(entry, dict) and entry.get("marketplaceId") == marketplace_id:
            return entry
    return None


def normalize_item(raw: Any, marketplace_id: str) -> dict[str, Any]:
    summary = _for_marketplace(dig(raw, "summaries"), marketplace_id) or {}
    images = as_list(dig(_for_marketplace(dig(raw, "images"), marketplace_id), "images"))
    main = next((image for image in images if dig(image, "variant") == "MAIN"), None)
    identifiers = as_list(
        dig(_for_marketplace(dig(raw, "identifiers"), marketplace_id), "identifiers")
    )
    product_type = dig(_for_marketplace(dig(raw, "productTypes"), marketplace_id), "productType")
    relationships = as_list(
        dig(_for_marketplace(dig(raw, "relationships"), marketplace_id), "relationships")
    )
    variation = next((item for item in relationships if dig(item, "type") == "VARIATION"), None)
    return {
        "asin": dig(raw, "asin"),
        "item_name": summary.get("itemName"),
        "brand": summary.get("brand"),
        "manufacturer": summary.get("manufacturer"),
        "model_number": summary.get("modelNumber"),
        "item_classification": summary.get("itemClassification"),
        "product_type": product_type,
        "main_image_url": dig(main, "link"),
        "images": [
            {
                "variant": dig(image, "variant"),
                "url": dig(image, "link"),
                "height": dig(image, "height"),
                "width": dig(image, "width"),
            }
            for image in images
        ],
        "identifiers": [
            {"type": dig(item, "identifierType"), "value": dig(item, "identifier")}
            for item in identifiers
        ],
        "variation": {
            "parent_asins": [str(item) for item in as_list(dig(variation, "parentAsins"))],
            "child_asins": [str(item) for item in as_list(dig(variation, "childAsins"))],
            "theme": dig(variation, "variationTheme"),
        }
        if variation
        else None,
        "attributes": dig(raw, "attributes"),
        "amazon": raw,
    }


def _search_catalog_items(context: Context) -> dict[str, Any]:
    data = context.input
    by_identifiers = data.get("identifiers") is not None
    by_keywords = data.get("keywords") is not None
    if by_identifiers == by_keywords:
        raise ExtensionError(
            errors.INVALID_PAYLOAD, "Search either by identifiers or by keywords (exactly one)"
        )
    if (data.get("identifiers_type") is not None) != by_identifiers:
        raise ExtensionError(
            errors.INVALID_PAYLOAD, "identifiers_type goes with identifiers, and only with them"
        )
    if by_identifiers:
        used = [name for name in _KEYWORD_ONLY if data.get(name) is not None]
        if used:
            raise ExtensionError(
                errors.UNSUPPORTED_FILTER, f"{', '.join(used)} only works with a keyword search"
            )
    token = pagination.unwrap(context, API_VERSION)
    response = context.transport.call(
        "GET",
        SEARCH_PATH,
        query={
            "marketplaceIds": [context.connection.marketplace_id],
            "identifiers": data.get("identifiers"),
            "identifiersType": data.get("identifiers_type"),
            "sellerId": context.connection.seller_id
            if data.get("identifiers_type") == "SKU"
            else None,
            "keywords": data.get("keywords"),
            "brandNames": data.get("brand_names"),
            "classificationIds": data.get("classification_ids"),
            "keywordsLocale": data.get("keywords_locale"),
            "includedData": data.get("included_data"),
            "locale": data.get("locale"),
            "pageSize": data.get("page_size"),
            "pageToken": token,
        },
        mutating=False,
        purpose="Amazon search catalog",
    )
    items = as_list(dig(response.body, "items"))
    marketplace_id = context.connection.marketplace_id
    return {
        "items": [normalize_item(item, marketplace_id) for item in items],
        "count": len(items),
        "number_of_results": dig(response.body, "numberOfResults"),
        "next_token": pagination.wrap(
            context, API_VERSION, dig(response.body, "pagination", "nextToken")
        ),
    }


def _get_catalog_item(context: Context) -> dict[str, Any]:
    response = context.transport.call(
        "GET",
        ITEM_PATH,
        path={"asin": asin(context.input["asin"])},
        query={
            "marketplaceIds": [context.connection.marketplace_id],
            "includedData": context.input.get("included_data"),
            "locale": context.input.get("locale"),
        },
        mutating=False,
        purpose="Amazon get catalog item",
    )
    if not isinstance(response.body, dict) or not response.body.get("asin"):
        raise ExtensionError(errors.NOT_FOUND, "Amazon returned no catalog item for that ASIN")
    return {"item": normalize_item(response.body, context.connection.marketplace_id)}


_INCLUDED_DATA_INPUT = enum_list(
    "Included data",
    "Catalog data sets to return (Amazon default: summaries). relationships gives variation "
    "parents and children; vendorDetails needs a vendor account.",
    INCLUDED_DATA,
)
_LOCALE_INPUT = text("Locale", "Locale for localized summaries, for example en_GB.", max_length=10)
_ROLES = ("Product Listing",)

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        operation_id="search_catalog_items",
        display_name="Search the Amazon catalog",
        description=(
            "Finds products in the Amazon catalog of the connection's marketplace, either by up to 20 "
            "identifiers of one type (ASIN, EAN, GTIN, ISBN, JAN, MINSAN, SKU, UPC) or by keywords. "
            "Pass next_token for the next page."
        ),
        section="catalog_items",
        properties={
            "connection_ref": CONNECTION_REF,
            "identifiers": text_list(
                "Identifiers", "Up to 20 product identifiers.", max_items=20, max_length=200
            ),
            "identifiers_type": enum(
                "Identifier type", "The type of every value in identifiers.", IDENTIFIER_TYPES
            ),
            "keywords": text_list(
                "Keywords", "Up to 20 search keywords.", max_items=20, max_length=200
            ),
            "brand_names": text_list(
                "Brands",
                "Keyword search only: limit to these brands.",
                max_items=20,
                max_length=200,
            ),
            "classification_ids": text_list(
                "Classification IDs",
                "Keyword search only: limit to these browse classifications.",
                max_items=20,
                max_length=64,
            ),
            "keywords_locale": text(
                "Keywords language",
                "Keyword search only: the language of the keywords.",
                max_length=10,
            ),
            "included_data": _INCLUDED_DATA_INPUT,
            "locale": _LOCALE_INPUT,
            "page_size": integer("Page size", "Items per page, 1 to 20.", minimum=1, maximum=20),
            "next_token": NEXT_TOKEN,
        },
        required=("connection_ref",),
        outputs=(
            out("items", ARRAY_OF_OBJECTS),
            out("count", INTEGER),
            out("number_of_results", {"type": ["integer", "null"]}),
            NEXT_TOKEN_OUTPUT,
        ),
        handler=_search_catalog_items,
        amazon_calls=("Catalog Items 2022-04-01 searchCatalogItems",),
        roles=_ROLES,
        extra_error_codes=(errors.UNSUPPORTED_FILTER, errors.CONTINUATION_TOKEN_INVALID),
    ),
    Operation(
        operation_id="get_catalog_item",
        display_name="Get a catalog item",
        description=(
            "Returns one Amazon catalog product by ASIN: summary, attributes, images, identifiers and, with "
            "relationships, its variation parent and children."
        ),
        section="catalog_items",
        properties={
            "connection_ref": CONNECTION_REF,
            "asin": ASIN_INPUT,
            "included_data": _INCLUDED_DATA_INPUT,
            "locale": _LOCALE_INPUT,
        },
        required=("connection_ref", "asin"),
        outputs=(out("item", OBJECT),),
        handler=_get_catalog_item,
        amazon_calls=("Catalog Items 2022-04-01 getCatalogItem",),
        roles=_ROLES,
    ),
)
