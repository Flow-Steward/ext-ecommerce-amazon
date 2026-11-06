"""Listings Items 2021-08-01: the seller's own listings.

The seller ID is always the connection's and the marketplace is always the
connection's one marketplace; neither is an input. Request bodies are built in
``runtime/listings_core.py`` and shared with the validation preview, the price
update and the merchant quantity update.

Two writes exist here, ``create_listing`` (putListingsItem) and
``update_listing_attributes`` (patchListingsItem). Both report Amazon's
synchronous verdict only: ``ACCEPTED`` is not "applied". An ``INVALID`` answer
(HTTP 200) is returned as a successful step with ``accepted: false`` and
``external_effect_status: failed`` rather than as an error, so a workflow can
branch on Amazon's issues list, which is the useful part of that answer.
"""

from __future__ import annotations

from .. import amazon_api

from typing import Any

from .. import errors, listings_core, pagination, validation
from ..errors import ExtensionError
from ..listings_core import (
    API_VERSION,
    GET_CALL,
    INCLUDED_DATA,
    ISSUE_LOCALE_INPUT,
    PATCH_CALL,
    PATCH_PROPERTIES,
    PUT_CALL,
    PUT_PROPERTIES,
    ROLE_PRODUCT_LISTING,
    SEARCH_CALL,
    SEARCH_PATH,
    SKU_INPUT,
    SUBMISSION_NOTE,
    SUBMISSION_OUTPUTS,
)
from ..registry import (
    BOOLEAN,
    ARRAY_OF_OBJECTS,
    CONNECTION_REF,
    INTEGER,
    NEXT_TOKEN,
    NEXT_TOKEN_OUTPUT,
    OBJECT,
    TEXT,
    Context,
    Operation,
    enum,
    enum_list,
    integer,
    out,
    text,
    text_list,
    timestamp,
)
from ..values import as_list, dig

IDENTIFIER_TYPES = ("ASIN", "EAN", "FNSKU", "GTIN", "ISBN", "JAN", "MINSAN", "SKU", "UPC")
STATUSES = ("BUYABLE", "DISCOVERABLE")
SEVERITIES = ("WARNING", "ERROR")
SORT_FIELDS = ("sku", "createdDate", "lastUpdatedDate")
SORT_ORDERS = ("ASC", "DESC")


# -- search -----------------------------------------------------------------


def _search_listings(context: Context) -> dict[str, Any]:
    data = context.input
    if (data.get("identifiers") is None) != (data.get("identifiers_type") is None):
        raise ExtensionError(
            errors.INVALID_PAYLOAD, "Give identifiers and identifiers_type together"
        )
    exclusive = [
        name
        for name in ("identifiers", "variation_parent_sku", "package_hierarchy_sku")
        if data.get(name) is not None
    ]
    if len(exclusive) > 1:
        raise ExtensionError(
            errors.UNSUPPORTED_FILTER,
            f"Amazon does not combine {' and '.join(exclusive)} in one listings search",
        )
    for name in ("created_after", "created_before", "last_updated_after", "last_updated_before"):
        validation.timestamp(data.get(name), name)
    token = pagination.unwrap(context, API_VERSION)
    response = context.transport.call(
        "GET",
        SEARCH_PATH,
        path=listings_core.seller_path(context),
        query={
            "marketplaceIds": [context.connection.marketplace_id],
            "issueLocale": listings_core.issue_locale(context),
            "includedData": data.get("included_data"),
            "identifiers": data.get("identifiers"),
            "identifiersType": data.get("identifiers_type"),
            "variationParentSku": data.get("variation_parent_sku"),
            "packageHierarchySku": data.get("package_hierarchy_sku"),
            "createdAfter": data.get("created_after"),
            "createdBefore": data.get("created_before"),
            "lastUpdatedAfter": data.get("last_updated_after"),
            "lastUpdatedBefore": data.get("last_updated_before"),
            "withIssueSeverity": data.get("with_issue_severity"),
            "withStatus": data.get("with_status"),
            "withoutStatus": data.get("without_status"),
            "sortBy": data.get("sort_by"),
            "sortOrder": data.get("sort_order"),
            "pageSize": data.get("page_size"),
            "pageToken": token,
        },
        mutating=False,
        purpose="Amazon search listings",
    )
    items = as_list(dig(response.body, "items"))
    marketplace_id = context.connection.marketplace_id
    return {
        "listings": [listings_core.normalize_listing(item, marketplace_id) for item in items],
        "count": len(items),
        "number_of_results": dig(response.body, "numberOfResults"),
        "next_token": pagination.wrap(
            context, API_VERSION, dig(response.body, "pagination", "nextToken")
        ),
    }


# -- one listing ----------------------------------------------------------------


def _get_listing(context: Context) -> dict[str, Any]:
    included = context.input.get("included_data") or list(listings_core.DEFAULT_INCLUDED_DATA)
    raw = listings_core.get_listing_item(context, str(context.input["sku"]), included)
    return {"listing": listings_core.normalize_listing(raw, context.connection.marketplace_id)}


def _get_listing_issues(context: Context) -> dict[str, Any]:
    raw = listings_core.get_listing_item(context, str(context.input["sku"]), ["issues"])
    issues = listings_core.normalize_issues(raw.get("issues"))
    severities = [issue["severity"] for issue in issues]
    return {
        "sku": raw.get("sku"),
        "issues": issues,
        "issue_count": len(issues),
        "error_count": severities.count("ERROR"),
        "warning_count": severities.count("WARNING"),
        "amazon": {"issues": raw.get("issues") or []},
    }


# -- writes ---------------------------------------------------------------------


def _create_listing(context: Context) -> dict[str, Any]:
    return listings_core.submit(
        context, "PUT", listings_core.build_put_body(context), preview=False
    )


def _update_listing_attributes(context: Context) -> dict[str, Any]:
    return listings_core.submit(
        context, "PATCH", listings_core.build_patch_body(context), preview=False
    )


# -- rows -----------------------------------------------------------------------

_INCLUDED_DATA_INPUT = enum_list(
    "Included data",
    "Listing data sets to return. Default for get_listing: summaries, attributes, issues, offers, "
    "fulfillmentAvailability; for search_listings Amazon's default is summaries.",
    INCLUDED_DATA,
)

_SEARCH_PROPERTIES: dict[str, dict[str, Any]] = {
    "connection_ref": CONNECTION_REF,
    "identifiers": text_list(
        "Identifiers",
        "Up to 20 product identifiers of identifiers_type.",
        max_items=20,
        max_length=200,
    ),
    "identifiers_type": enum(
        "Identifier type", "The type of every value in identifiers.", IDENTIFIER_TYPES
    ),
    "variation_parent_sku": text(
        "Variation parent SKU", "Only variation children of this SKU.", max_length=200
    ),
    "package_hierarchy_sku": text(
        "Package hierarchy SKU",
        "Only listings that contain or are contained by this SKU.",
        max_length=200,
    ),
    "created_after": timestamp("Created after", "Listings created at or after this time."),
    "created_before": timestamp("Created before", "Listings created at or before this time."),
    "last_updated_after": timestamp("Updated after", "Listings updated at or after this time."),
    "last_updated_before": timestamp("Updated before", "Listings updated at or before this time."),
    "with_issue_severity": enum_list(
        "With issue severity", "Only listings with issues of these severities.", SEVERITIES
    ),
    "with_status": enum_list("With status", "Only listings with these statuses.", STATUSES),
    "without_status": enum_list(
        "Without status", "Only listings without these statuses.", STATUSES
    ),
    "sort_by": enum("Sort by", "Sort field (Amazon default lastUpdatedDate).", SORT_FIELDS),
    "sort_order": enum("Sort order", "ASC or DESC (Amazon default DESC).", SORT_ORDERS),
    "included_data": _INCLUDED_DATA_INPUT,
    "issue_locale": ISSUE_LOCALE_INPUT,
    "page_size": integer("Page size", "Listings per page, 1 to 20.", minimum=1, maximum=20),
    "next_token": NEXT_TOKEN,
}

_ROLES = (ROLE_PRODUCT_LISTING,)
_LISTING_ERRORS = (errors.UNSUPPORTED_FILTER,)

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        operation_id="search_listings",
        display_name="Find listings",
        description=(
            "Returns one page of the connection's own listings in its marketplace, filtered by "
            "identifiers, parent SKU, dates, status or issue severity. Pass next_token for the next page."
        ),
        section="listings",
        properties=_SEARCH_PROPERTIES,
        required=("connection_ref",),
        outputs=(
            out("listings", ARRAY_OF_OBJECTS),
            out("count", INTEGER),
            out("number_of_results", {"type": ["integer", "null"]}),
            NEXT_TOKEN_OUTPUT,
        ),
        handler=_search_listings,
        amazon_calls=(SEARCH_CALL,),
        roles=_ROLES,
        extra_error_codes=(errors.UNSUPPORTED_FILTER, errors.CONTINUATION_TOKEN_INVALID),
    ),
    Operation(
        operation_id="get_listing",
        display_name="Get a listing",
        description=(
            "Returns one of the seller's listings by SKU: summary, attributes, issues, offers and quantities."
        ),
        section="listings",
        properties={
            "connection_ref": CONNECTION_REF,
            "sku": SKU_INPUT,
            "included_data": _INCLUDED_DATA_INPUT,
            "issue_locale": ISSUE_LOCALE_INPUT,
        },
        required=("connection_ref", "sku"),
        outputs=(out("listing", OBJECT),),
        handler=_get_listing,
        amazon_calls=(GET_CALL,),
        roles=_ROLES,
    ),
    Operation(
        operation_id="get_listing_issues",
        display_name="Get listing issues",
        description=(
            "Returns the issues Amazon reports for one listing (code, message, severity, attributes, "
            "categories, enforcement), including issues raised after a submission was accepted."
        ),
        section="listings",
        properties={
            "connection_ref": CONNECTION_REF,
            "sku": SKU_INPUT,
            "issue_locale": ISSUE_LOCALE_INPUT,
        },
        required=("connection_ref", "sku"),
        outputs=(
            out("sku", TEXT),
            out("issues", ARRAY_OF_OBJECTS),
            out("issue_count", INTEGER),
            out("error_count", INTEGER),
            out("warning_count", INTEGER),
            out("amazon", OBJECT),
        ),
        handler=_get_listing_issues,
        amazon_calls=(GET_CALL + " (includedData issues)",),
        roles=_ROLES,
    ),
    Operation(
        operation_id="create_listing",
        display_name="Create or replace a listing",
        description=(
            "Creates a listing for a SKU, or fully replaces an existing one (attributes left out are "
            "dropped). offer_on_existing_asin sells an existing catalog product: give asin and the offer "
            "attributes (price, quantity, condition); it is sent as LISTING_OFFER_ONLY with the ASIN as "
            "merchant_suggested_asin. new_product creates the product too (requirements LISTING): give "
            "product_type and all required attributes; Amazon may refuse to create a new ASIN without "
            "brand registry or GTIN approval. Check eligibility with check_listing_restrictions and "
            "try validate_listing first." + SUBMISSION_NOTE
        ),
        section="listings",
        properties={
            "connection_ref": CONNECTION_REF,
            "sku": SKU_INPUT,
            **PUT_PROPERTIES,
            "issue_locale": ISSUE_LOCALE_INPUT,
        },
        required=("connection_ref", "sku", "listing_kind", "attributes"),
        outputs=SUBMISSION_OUTPUTS,
        handler=_create_listing,
        amazon_calls=(PUT_CALL,),
        roles=_ROLES,
        kind="action",
        mutating=True,
        effect_kind="amazon_listing_put",
        extra_error_codes=_LISTING_ERRORS,
    ),
    Operation(
        operation_id="update_listing_attributes",
        display_name="Update listing attributes",
        description=(
            "Changes selected top-level attributes of an existing listing with JSON Patch operations "
            "(add, replace, merge, delete on /attributes/<name>); other attributes stay as they are. "
            "Amazon requires the listing's product_type." + SUBMISSION_NOTE
        ),
        section="listings",
        properties={
            "connection_ref": CONNECTION_REF,
            "sku": SKU_INPUT,
            **PATCH_PROPERTIES,
            "issue_locale": ISSUE_LOCALE_INPUT,
        },
        required=("connection_ref", "sku", "product_type", "patches"),
        outputs=SUBMISSION_OUTPUTS,
        handler=_update_listing_attributes,
        amazon_calls=(PATCH_CALL,),
        roles=_ROLES,
        kind="action",
        mutating=True,
        effect_kind="amazon_listing_patch",
        extra_error_codes=_LISTING_ERRORS,
    ),
)

_DELETE_NOTE = (
    "Amazon accepted the deletion (status ACCEPTED); it is not applied yet. Read the listing with "
    "get_listing to see when it is gone. Deleting many SKUs at once is done with a "
    "JSON_LISTINGS_FEED DELETE message (upload_feed_document, create_feed)."
)


def _delete_result(context, converted, _response):
    return {
        "sku": converted.get("sku") or context.input["sku"],
        "status": converted.get("status"),
        "submission_id": converted.get("submission_id"),
        "issues": converted.get("issues") or [],
        "applied": False,
        "note": _DELETE_NOTE,
    }


OPERATIONS = OPERATIONS + amazon_api.operations(
    (
        amazon_api.Method(
            operation_id="delete_listing",
            display_name="Delete a listing",
            description=(
                "Deletes one SKU's listing in the connection's marketplace (Listings Items "
                "deleteListingsItem). Amazon answers ACCEPTED and processes the deletion later; the "
                "result never claims it was applied."
            ),
            section="listings",
            namespace="listings_2021_08_01",
            amazon_operation="deleteListingsItem",
            roles=("Product Listing",),
            mutating=True,
            effect_kind="amazon_listing_deletion",
            inputs={
                "sku": {"title": "SKU", "description": "The seller SKU whose listing is deleted."}
            },
            read_back=("get_listing",),
            result=_delete_result,
            outputs=(
                out("sku", TEXT),
                out("status", {"type": ["string", "null"]}),
                out("submission_id", {"type": ["string", "null"]}),
                out("issues", {"type": "array", "items": {"type": "object"}}),
                out("applied", BOOLEAN),
                out("note", TEXT),
            ),
            pii=False,
        ),
    )
)
