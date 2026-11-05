"""What Amazon requires before a listing: product types, restrictions, a dry run.

* Product Type Definitions 2020-09-01: find a product type and read its
  definition. The definition's JSON Schema lives behind a link Amazon documents
  as valid for seven days; such a link is a signed URL, so the result carries
  the link without its query string (where the signature lives) and a
  ``signed`` flag, never the signed URL itself. ``include_schema`` downloads
  the schema through the transport's presigned-document path (Amazon S3 hosts
  only, no access token sent, capped at 5 MB) and returns it parsed.
* Listings Restrictions 2021-08-01: whether the seller may list an ASIN.
* ``validate_listing``: the exact put or patch request of ``create_listing`` /
  ``update_listing_attributes`` (built by the same code in
  ``runtime/listings_core.py``) sent with ``mode=VALIDATION_PREVIEW``. Amazon
  does not persist a preview, so it is not a mutation.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
from urllib.parse import urlparse, urlunparse

from .. import errors, listings_core
from ..errors import ExtensionError
from ..listings_core import (
    ASIN_INPUT,
    CONDITION_TYPE_INPUT,
    ISSUE_LOCALE_INPUT,
    PATCH_PROPERTIES,
    PUT_PROPERTIES,
    ROLE_PRODUCT_LISTING,
    SKU_INPUT,
)
from ..registry import (
    ARRAY_OF_OBJECTS,
    BOOLEAN,
    CONNECTION_REF,
    NULLABLE_OBJECT,
    NULLABLE_TEXT,
    OBJECT,
    TEXT,
    Context,
    Operation,
    boolean,
    enum,
    out,
    text,
    text_list,
)
from ..values import as_list, dig

SEARCH_TYPES_PATH = "/definitions/2020-09-01/productTypes"
TYPE_PATH = "/definitions/2020-09-01/productTypes/{productType}"
RESTRICTIONS_PATH = "/listings/2021-08-01/restrictions"

MAX_SCHEMA_BYTES = 5 * 1024 * 1024
REQUIREMENTS = ("LISTING", "LISTING_PRODUCT_ONLY", "LISTING_OFFER_ONLY")
REQUIREMENTS_ENFORCED = ("ENFORCED", "NOT_ENFORCED")
PARENTAGE_LEVELS = ("NONE", "CHILD", "PARENT")
#: The ``locale`` values the getDefinitionsProductType model accepts.
DEFINITION_LOCALES = (
    "DEFAULT",
    "ar",
    "ar_AE",
    "de",
    "de_DE",
    "en",
    "en_AE",
    "en_AU",
    "en_CA",
    "en_GB",
    "en_IN",
    "en_SG",
    "en_US",
    "es",
    "es_ES",
    "es_MX",
    "es_US",
    "fr",
    "fr_CA",
    "fr_FR",
    "it",
    "it_IT",
    "ja",
    "ja_JP",
    "nl",
    "nl_NL",
    "pl",
    "pl_PL",
    "pt",
    "pt_BR",
    "pt_PT",
    "sv",
    "sv_SE",
    "tr",
    "tr_TR",
    "zh",
    "zh_CN",
    "zh_TW",
)
REQUEST_KINDS = ("put", "patch")
_PUT_ONLY = ("listing_kind", "asin", "condition_type", "attributes")
_PATCH_ONLY = ("patches",)


# -- product types ------------------------------------------------------------


def _search_product_types(context: Context) -> dict[str, Any]:
    data = context.input
    if (data.get("keywords") is None) == (data.get("item_name") is None):
        raise ExtensionError(
            errors.INVALID_PAYLOAD, "Search product types by keywords or by item_name (exactly one)"
        )
    response = context.transport.call(
        "GET",
        SEARCH_TYPES_PATH,
        query={
            "marketplaceIds": [context.connection.marketplace_id],
            "keywords": data.get("keywords"),
            "itemName": data.get("item_name"),
            "locale": data.get("locale"),
            "searchLocale": data.get("search_locale"),
        },
        mutating=False,
        purpose="Amazon search product types",
    )
    types = as_list(dig(response.body, "productTypes"))
    return {
        "product_types": [
            {
                "name": dig(item, "name"),
                "display_name": dig(item, "displayName"),
                "marketplace_ids": [str(value) for value in as_list(dig(item, "marketplaceIds"))],
            }
            for item in types
        ],
        "count": len(types),
        "product_type_version": dig(response.body, "productTypeVersion"),
    }


def _link(raw: Any) -> dict[str, Any] | None:
    """A schema link without its query string: a signed URL never leaves here."""
    resource = dig(raw, "link", "resource")
    if not isinstance(resource, str) or not resource:
        return None
    parsed = urlparse(resource)
    return {
        "resource": urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", "")),
        "verb": dig(raw, "link", "verb"),
        "checksum": dig(raw, "checksum"),
        "signed": bool(parsed.query),
    }


def _checksum_matches(content: bytes, checksum: Any) -> bool | None:
    """Amazon documents a Base64 MD5; its own example shows hex. Accept either."""
    if not isinstance(checksum, str) or not checksum:
        return None
    digest = hashlib.md5(content, usedforsecurity=False).digest()
    return checksum in (base64.b64encode(digest).decode("ascii"), digest.hex())


def _download_schema(context: Context, raw_link: Any) -> tuple[Any, bool | None, int]:
    resource = dig(raw_link, "link", "resource")
    content = context.transport.download_document(
        resource, max_bytes=MAX_SCHEMA_BYTES, purpose="Amazon product type schema"
    )
    try:
        schema = json.loads(content.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtensionError(
            errors.INVALID_JSON_RESPONSE, "The product type schema is not valid JSON"
        ) from exc
    return schema, _checksum_matches(content, dig(raw_link, "checksum")), len(content)


def _get_product_type_definition(context: Context) -> dict[str, Any]:
    data = context.input
    name = listings_core.product_type(data["product_type"])
    response = context.transport.call(
        "GET",
        TYPE_PATH,
        path={"productType": name},
        query={
            "marketplaceIds": [context.connection.marketplace_id],
            "sellerId": context.connection.seller_id if data.get("seller_specific", True) else None,
            "productTypeVersion": data.get("product_type_version"),
            "requirements": data.get("requirements"),
            "requirementsEnforced": data.get("requirements_enforced"),
            "locale": data.get("locale"),
            "parentageLevel": data.get("parentage_level"),
        },
        mutating=False,
        purpose="Amazon get product type definition",
    )
    body = response.body if isinstance(response.body, dict) else {}
    schema = verified = size = None
    if data.get("include_schema"):
        schema, verified, size = _download_schema(context, body.get("schema"))
    version = body.get("productTypeVersion")
    return {
        "product_type": body.get("productType"),
        "display_name": body.get("displayName"),
        "requirements": body.get("requirements"),
        "requirements_enforced": body.get("requirementsEnforced"),
        "locale": body.get("locale"),
        "marketplace_ids": [str(item) for item in as_list(body.get("marketplaceIds"))],
        "product_type_version": {
            "version": dig(version, "version"),
            "latest": dig(version, "latest"),
            "release_candidate": dig(version, "releaseCandidate"),
        },
        "property_groups": body.get("propertyGroups") or {},
        "schema_link": _link(body.get("schema")),
        "meta_schema_link": _link(body.get("metaSchema")),
        "schema": schema,
        "schema_checksum_verified": verified,
        "schema_bytes": size,
    }


# -- restrictions ---------------------------------------------------------------


def _check_listing_restrictions(context: Context) -> dict[str, Any]:
    data = context.input
    product_type = data.get("product_type")
    response = context.transport.call(
        "GET",
        RESTRICTIONS_PATH,
        query={
            "asin": listings_core.asin(data["asin"]),
            "conditionType": data.get("condition_type"),
            "sellerId": context.connection.seller_id,
            "marketplaceIds": [context.connection.marketplace_id],
            "reasonLocale": listings_core.issue_locale(context, "reason_locale"),
            "productType": listings_core.product_type(product_type)
            if product_type is not None
            else None,
        },
        mutating=False,
        purpose="Amazon listing restrictions",
    )
    raw = as_list(dig(response.body, "restrictions"))
    restrictions = [
        {
            "marketplace_id": dig(item, "marketplaceId"),
            "condition_type": dig(item, "conditionType"),
            "reasons": [
                {
                    "reason_code": dig(reason, "reasonCode"),
                    "message": dig(reason, "message"),
                    "links": [
                        {
                            "resource": dig(link, "resource"),
                            "title": dig(link, "title"),
                            "type": dig(link, "type"),
                            "verb": dig(link, "verb"),
                        }
                        for link in as_list(dig(reason, "links"))
                    ],
                }
                for reason in as_list(dig(item, "reasons"))
            ],
        }
        for item in raw
    ]
    return {
        "asin": data["asin"],
        "restricted": bool(restrictions),
        "restrictions": restrictions,
        "amazon": response.body if isinstance(response.body, dict) else {},
    }


# -- validation preview -----------------------------------------------------------


def _validate_listing(context: Context) -> dict[str, Any]:
    data = context.input
    kind = data["request_kind"]
    foreign = _PATCH_ONLY if kind == "put" else _PUT_ONLY
    used = [name for name in foreign if data.get(name) is not None]
    if used:
        raise ExtensionError(
            errors.INVALID_PAYLOAD, f"{', '.join(used)} does not belong to a {kind} request"
        )
    if kind == "put":
        return listings_core.submit(
            context, "PUT", listings_core.build_put_body(context), preview=True
        )
    return listings_core.submit(
        context, "PATCH", listings_core.build_patch_body(context), preview=True
    )


# -- rows -----------------------------------------------------------------------

_ROLES = (ROLE_PRODUCT_LISTING,)
_LINK = {"type": ["object", "null"]}

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        operation_id="search_product_types",
        display_name="Find product types",
        description=(
            "Finds the Amazon product types of the connection's marketplace by keywords, or recommends "
            "one from a product title (item_name). The product type is needed to create or update listings."
        ),
        section="requirements",
        properties={
            "connection_ref": CONNECTION_REF,
            "keywords": text_list("Keywords", "Up to 20 keywords.", max_items=20, max_length=100),
            "item_name": text(
                "Product title",
                "A product title to get a product type recommendation for.",
                max_length=500,
            ),
            "locale": text(
                "Display language", "Locale for display names, for example en_GB.", max_length=10
            ),
            "search_locale": text(
                "Search language", "Language of keywords or item_name.", max_length=10
            ),
        },
        required=("connection_ref",),
        outputs=(
            out("product_types", ARRAY_OF_OBJECTS),
            out("count", {"type": "integer"}),
            out("product_type_version", NULLABLE_TEXT),
        ),
        handler=_search_product_types,
        amazon_calls=("Product Type Definitions 2020-09-01 searchDefinitionsProductTypes",),
        roles=_ROLES,
    ),
    Operation(
        operation_id="get_product_type_definition",
        display_name="Get product type requirements",
        description=(
            "Returns a product type's definition for the connection's marketplace: version, property "
            "groups and the link to its JSON Schema (without the signed query string). include_schema "
            "also downloads the schema (up to 5 MB) and returns it parsed; parentage_level makes it smaller."
        ),
        section="requirements",
        properties={
            "connection_ref": CONNECTION_REF,
            "product_type": listings_core.PRODUCT_TYPE_INPUT,
            "requirements": enum(
                "Requirements", "Requirement set (Amazon default LISTING).", REQUIREMENTS
            ),
            "requirements_enforced": enum(
                "Requirements enforced",
                "NOT_ENFORCED checks attribute structure without requiring every attribute (for partial updates).",
                REQUIREMENTS_ENFORCED,
            ),
            "locale": enum("Locale", "Language of labels in the definition.", DEFINITION_LOCALES),
            "product_type_version": text(
                "Product type version",
                "A version ID, LATEST (default) or RELEASE_CANDIDATE.",
                max_length=100,
            ),
            "parentage_level": enum(
                "Parentage level", "Resolve the schema for this variation level.", PARENTAGE_LEVELS
            ),
            "seller_specific": boolean(
                "Seller-specific values",
                "Fill seller-specific values such as the seller's brands (default true).",
            ),
            "include_schema": boolean(
                "Include schema", "Download and return the JSON Schema (up to 5 MB)."
            ),
        },
        required=("connection_ref", "product_type"),
        outputs=(
            out("product_type", TEXT),
            out("display_name", NULLABLE_TEXT),
            out("requirements", NULLABLE_TEXT),
            out("requirements_enforced", NULLABLE_TEXT),
            out("locale", NULLABLE_TEXT),
            out("marketplace_ids", {"type": "array", "items": {"type": "string"}}),
            out("product_type_version", OBJECT),
            out("property_groups", OBJECT),
            out("schema_link", _LINK),
            out("meta_schema_link", _LINK),
            out("schema", NULLABLE_OBJECT),
            out("schema_checksum_verified", {"type": ["boolean", "null"]}),
            out("schema_bytes", {"type": ["integer", "null"]}),
        ),
        handler=_get_product_type_definition,
        amazon_calls=(
            "Product Type Definitions 2020-09-01 getDefinitionsProductType",
            "Amazon S3 presigned schema download (include_schema)",
        ),
        roles=_ROLES,
        extra_error_codes=(errors.DOCUMENT_UNAVAILABLE,),
    ),
    Operation(
        operation_id="check_listing_restrictions",
        display_name="Check listing restrictions",
        description=(
            "Tells whether the seller may list an ASIN in the connection's marketplace, optionally for one "
            "condition, with Amazon's reasons and links to request approval."
        ),
        section="requirements",
        properties={
            "connection_ref": CONNECTION_REF,
            "asin": ASIN_INPUT,
            "condition_type": CONDITION_TYPE_INPUT,
            "product_type": listings_core.PRODUCT_TYPE_INPUT,
            "reason_locale": text(
                "Reason language",
                "Locale for reasons, for example en_US.",
                max_length=5,
                min_length=2,
            ),
        },
        required=("connection_ref", "asin"),
        outputs=(
            out("asin", TEXT),
            out("restricted", BOOLEAN),
            out("restrictions", ARRAY_OF_OBJECTS),
            out("amazon", OBJECT),
        ),
        handler=_check_listing_restrictions,
        amazon_calls=("Listings Restrictions 2021-08-01 getListingsRestrictions",),
        roles=_ROLES,
    ),
    Operation(
        operation_id="validate_listing",
        display_name="Validate a listing (dry run)",
        description=(
            "Sends the same request as create_listing (request_kind put) or update_listing_attributes "
            "(request_kind patch) in Amazon's VALIDATION_PREVIEW mode: Amazon checks it and reports VALID "
            "or INVALID with issues, and changes nothing."
        ),
        section="requirements",
        properties={
            "connection_ref": CONNECTION_REF,
            "sku": SKU_INPUT,
            "request_kind": enum(
                "Request kind",
                "put = create_listing, patch = update_listing_attributes.",
                REQUEST_KINDS,
            ),
            **PUT_PROPERTIES,
            **PATCH_PROPERTIES,
            "issue_locale": ISSUE_LOCALE_INPUT,
        },
        required=("connection_ref", "sku", "request_kind"),
        outputs=(
            out("sku", TEXT),
            out("status", TEXT),
            out("valid", BOOLEAN),
            out("issues", ARRAY_OF_OBJECTS),
            out("asin", NULLABLE_TEXT),
            out("amazon", OBJECT),
        ),
        handler=_validate_listing,
        amazon_calls=(
            "Listings Items 2021-08-01 putListingsItem (mode VALIDATION_PREVIEW)",
            "Listings Items 2021-08-01 patchListingsItem (mode VALIDATION_PREVIEW)",
        ),
        roles=_ROLES,
        extra_error_codes=(errors.UNSUPPORTED_FILTER,),
    ),
)
