"""Listings Items 2021-08-01: request builders and normalizers shared by sections.

``listings``, ``requirements`` (validation preview), ``pricing`` (own offer, price
updates) and ``inventory`` (merchant quantity) all speak to the same Listings
Items endpoints. The request bodies are built here once, so a preview and the
real submission can never drift apart.

Facts this module relies on, checked against the official model
``listingsItems_2021-08-01.json`` and Amazon's Listings guides:

* The seller ID in the path is always the connection's, and ``marketplaceIds``
  is always the connection's one marketplace.
* ``patchListingsItem`` patches top-level attributes only (``/attributes/<name>``);
  ``merge`` is supported only for ``purchasable_offer`` and
  ``fulfillment_availability`` and needs non-empty selector fields.
* A submission answered ``ACCEPTED`` passed Amazon's synchronous checks only.
  Amazon applies it later and may still raise issues; the listing itself must be
  read (``get_listing``) to see the result. ``INVALID`` (HTTP 200) is a definite
  refusal: nothing was changed.
* ``mode=VALIDATION_PREVIEW`` runs the same checks without persisting anything
  and answers ``VALID`` or ``INVALID``; only then may ``includedData`` carry
  ``identifiers``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from . import errors
from .errors import ExtensionError
from .registry import (
    ARRAY_OF_OBJECTS,
    BOOLEAN,
    MAX_SKU_LENGTH,
    NULLABLE_TEXT,
    OBJECT,
    TEXT,
    Context,
    Output,
    enum,
    json_object,
    out,
    text,
)
from .values import as_list, dig, money

API_VERSION = "2021-08-01"
SEARCH_PATH = "/listings/2021-08-01/items/{sellerId}"
ITEM_PATH = "/listings/2021-08-01/items/{sellerId}/{sku}"
ROLE_PRODUCT_LISTING = "Product Listing"

GET_CALL = "Listings Items 2021-08-01 getListingsItem"
SEARCH_CALL = "Listings Items 2021-08-01 searchListingsItems"
PUT_CALL = "Listings Items 2021-08-01 putListingsItem"
PATCH_CALL = "Listings Items 2021-08-01 patchListingsItem"

CONDITION_TYPES = (
    "new_new",
    "new_open_box",
    "new_oem",
    "refurbished_refurbished",
    "used_like_new",
    "used_very_good",
    "used_good",
    "used_acceptable",
    "collectible_like_new",
    "collectible_very_good",
    "collectible_good",
    "collectible_acceptable",
    "club_club",
)
INCLUDED_DATA = (
    "summaries",
    "attributes",
    "issues",
    "offers",
    "fulfillmentAvailability",
    "procurement",
    "relationships",
    "productTypes",
)
DEFAULT_INCLUDED_DATA = ["summaries", "attributes", "issues", "offers", "fulfillmentAvailability"]
LISTING_KINDS = ("offer_on_existing_asin", "new_product")
PATCH_OPS = ("add", "replace", "merge", "delete")
#: Amazon supports ``merge`` for these attributes only.
MERGEABLE_ATTRIBUTES = frozenset({"purchasable_offer", "fulfillment_availability"})
#: The generic product type Amazon documents for offer-only submissions.
OFFER_ONLY_PRODUCT_TYPE = "PRODUCT"
MERCHANT_CHANNEL = "DEFAULT"

MAX_ATTRIBUTES = 400
MAX_ATTRIBUTE_BYTES = 1024 * 1024
MAX_PATCHES = 100

_ATTRIBUTE_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,100}$")
_PATCH_PATH_RE = re.compile(r"^/attributes/([A-Za-z0-9_]{1,100})$")
_LOCALE_RE = re.compile(r"^[a-z]{2}(_[A-Z]{2})?$")
_PRODUCT_TYPE_RE = re.compile(r"^[A-Z0-9_]{1,100}$")
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")

# -- input fragments --------------------------------------------------------

SKU_INPUT = text(
    "SKU",
    "Your seller SKU. Any character is allowed; it is sent percent-encoded.",
    max_length=MAX_SKU_LENGTH,
)
ASIN_INPUT = text(
    "ASIN", "The 10-character Amazon Standard Identification Number.", max_length=10, min_length=10
)
PRODUCT_TYPE_INPUT = text(
    "Amazon product type",
    "The Amazon product type of the listing, for example LUGGAGE (see search_product_types). "
    "Amazon requires it for every listing submission.",
    max_length=100,
)
ISSUE_LOCALE_INPUT = text(
    "Issue language",
    "Locale for issue messages, for example en_US or de_DE. Defaults to the marketplace language.",
    max_length=5,
    min_length=2,
)
CONDITION_TYPE_INPUT = enum("Condition", "The item condition of the offer.", CONDITION_TYPES)
LISTING_KIND_INPUT = enum(
    "Listing kind",
    "offer_on_existing_asin: sell an existing Amazon catalog product (offer only). "
    "new_product: create the product and the offer (Amazon may refuse to create a new ASIN "
    "without brand registry or GTIN approval).",
    LISTING_KINDS,
)
ATTRIBUTES_INPUT = json_object(
    "Attributes",
    "Listing attributes keyed by Amazon attribute name, each a list of values as the product "
    'type definition describes them, for example {"item_name": [{"value": "Mug", '
    '"language_tag": "en_GB"}]}. A marketplace_id inside a value must be the connection\'s.',
)
PATCHES_INPUT: dict[str, Any] = {
    "type": "array",
    "title": "Patches",
    "description": (
        "JSON Patch operations on top-level attributes: op add, replace, merge or delete; path "
        "/attributes/<attribute name>; value a list of attribute values (optional for delete). "
        "merge works only for purchasable_offer and fulfillment_availability."
    ),
    "minItems": 1,
    "maxItems": MAX_PATCHES,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op", "path"],
        "properties": {
            "op": {"type": "string", "enum": list(PATCH_OPS)},
            "path": {"type": "string", "minLength": 13, "maxLength": 112},
            "value": {"type": "array", "maxItems": 200, "items": {"type": "object"}},
        },
    },
}

#: Inputs of putListingsItem (create_listing and validate_listing with request_kind put).
PUT_PROPERTIES: dict[str, dict[str, Any]] = {
    "listing_kind": LISTING_KIND_INPUT,
    "asin": ASIN_INPUT,
    "condition_type": CONDITION_TYPE_INPUT,
    "product_type": PRODUCT_TYPE_INPUT,
    "attributes": ATTRIBUTES_INPUT,
}
#: Inputs of patchListingsItem (update_listing_attributes and validate_listing patch).
PATCH_PROPERTIES: dict[str, dict[str, Any]] = {
    "product_type": PRODUCT_TYPE_INPUT,
    "patches": PATCHES_INPUT,
}


def _invalid(message: str) -> ExtensionError:
    return ExtensionError(errors.INVALID_PAYLOAD, message)


def issue_locale(context: Context, name: str = "issue_locale") -> str | None:
    value = context.input.get(name)
    if value is None:
        return None
    if _LOCALE_RE.fullmatch(value) is None:
        raise _invalid(f"input.{name} must be a locale such as en_US")
    return value


def product_type(value: Any, name: str = "product_type") -> str:
    if not isinstance(value, str) or _PRODUCT_TYPE_RE.fullmatch(value) is None:
        raise _invalid(f"input.{name} must be an Amazon product type such as LUGGAGE")
    return value


def asin(value: Any, name: str = "asin") -> str:
    if not isinstance(value, str) or _ASIN_RE.fullmatch(value) is None:
        raise _invalid(f"input.{name} must be a 10-character ASIN of capital letters and digits")
    return value


def seller_path(context: Context, sku: str | None = None) -> dict[str, str]:
    path = {"sellerId": context.connection.seller_id}
    if sku is not None:
        path["sku"] = sku
    return path


# -- reading ------------------------------------------------------------------


def get_listing_item(context: Context, sku: str, included: list[str]) -> dict[str, Any]:
    """One getListingsItem call; the raw item, or ``not_found``."""
    response = context.transport.call(
        "GET",
        ITEM_PATH,
        path=seller_path(context, sku),
        query={
            "marketplaceIds": [context.connection.marketplace_id],
            "includedData": included,
            "issueLocale": issue_locale(context),
        },
        mutating=False,
        purpose="Amazon get listing",
    )
    body = response.body
    if not isinstance(body, dict) or not body.get("sku"):
        raise ExtensionError(errors.NOT_FOUND, "Amazon returned no listing for that SKU")
    return body


def normalize_issue(raw: Any) -> dict[str, Any]:
    enforcements = dig(raw, "enforcements")
    return {
        "code": dig(raw, "code"),
        "message": dig(raw, "message"),
        "severity": dig(raw, "severity"),
        "attribute_names": [str(item) for item in as_list(dig(raw, "attributeNames"))],
        "categories": [str(item) for item in as_list(dig(raw, "categories"))],
        "enforcement_actions": [
            dig(item, "action")
            for item in as_list(dig(enforcements, "actions"))
            if dig(item, "action")
        ],
        "exemption_status": dig(enforcements, "exemption", "status"),
        "marketplace_ids": [str(item) for item in as_list(dig(raw, "marketplaceIds"))],
    }


def normalize_issues(raw: Any) -> list[dict[str, Any]]:
    return [normalize_issue(item) for item in as_list(raw) if isinstance(item, Mapping)]


def normalize_offer(raw: Any) -> dict[str, Any]:
    """One entry of a listing's ``offers``: B2C or B2B price for one marketplace."""
    return {
        "marketplace_id": dig(raw, "marketplaceId"),
        "offer_type": dig(raw, "offerType"),
        "audience": dig(raw, "audience", "value"),
        "audience_display_name": dig(raw, "audience", "displayName"),
        "price": money(dig(raw, "price")),
        "points": dig(raw, "points", "pointsNumber"),
        "amazon": raw,
    }


def normalize_channel(raw: Any) -> dict[str, Any]:
    quantity = dig(raw, "quantity")
    return {
        "fulfillment_channel_code": dig(raw, "fulfillmentChannelCode"),
        "quantity": quantity
        if isinstance(quantity, int) and not isinstance(quantity, bool)
        else None,
    }


def marketplace_entry(entries: Any, marketplace_id: str) -> dict[str, Any] | None:
    for entry in as_list(entries):
        if isinstance(entry, dict) and entry.get("marketplaceId") == marketplace_id:
            return entry
    return None


def normalize_listing(raw: Any, marketplace_id: str) -> dict[str, Any]:
    summary = marketplace_entry(dig(raw, "summaries"), marketplace_id) or {}
    statuses = [str(item) for item in as_list(summary.get("status"))]
    issues = dig(raw, "issues")
    offers = dig(raw, "offers")
    channels = dig(raw, "fulfillmentAvailability")
    return {
        "sku": dig(raw, "sku"),
        "asin": summary.get("asin"),
        "product_type": summary.get("productType"),
        "item_name": summary.get("itemName"),
        "condition_type": summary.get("conditionType"),
        "statuses": statuses,
        "buyable": "BUYABLE" in statuses if summary else None,
        "discoverable": "DISCOVERABLE" in statuses if summary else None,
        "fn_sku": summary.get("fnSku"),
        "created_date": summary.get("createdDate"),
        "last_updated_date": summary.get("lastUpdatedDate"),
        "main_image": summary.get("mainImage"),
        "attributes": dig(raw, "attributes"),
        "issues": normalize_issues(issues) if issues is not None else None,
        "offers": [normalize_offer(item) for item in as_list(offers)]
        if offers is not None
        else None,
        "fulfillment_availability": (
            [normalize_channel(item) for item in as_list(channels)]
            if channels is not None
            else None
        ),
        "amazon": raw,
    }


# -- request builders -----------------------------------------------------------


def check_attribute_values(context: Context, name: str, values: Any, where: str) -> None:
    """Attribute values are lists of objects; a marketplace in them must be ours."""
    if not isinstance(values, list):
        raise _invalid(f"{where} must be a list of attribute values")
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise _invalid(f"{where}[{index}] must be an object")
        marketplace = value.get("marketplace_id")
        if marketplace is not None and marketplace != context.connection.marketplace_id:
            raise ExtensionError(
                errors.UNSUPPORTED_FILTER,
                f"{where}[{index}].marketplace_id must be the connection's marketplace "
                f"({context.connection.marketplace_id}); use a connection per marketplace",
            )


def check_attributes(context: Context, attributes: Any) -> dict[str, Any]:
    if not isinstance(attributes, Mapping) or not attributes:
        raise _invalid("input.attributes must be a non-empty object")
    if len(attributes) > MAX_ATTRIBUTES:
        raise _invalid(f"input.attributes allows at most {MAX_ATTRIBUTES} attributes")
    for name, values in attributes.items():
        if _ATTRIBUTE_NAME_RE.fullmatch(str(name)) is None:
            raise _invalid(f"input.attributes.{name} is not an Amazon attribute name")
        check_attribute_values(context, str(name), values, f"input.attributes.{name}")
    size = len(json.dumps(attributes, ensure_ascii=False, default=str).encode("utf-8"))
    if size > MAX_ATTRIBUTE_BYTES:
        raise _invalid("input.attributes exceeds 1 MB")
    return dict(attributes)


def build_put_body(context: Context) -> dict[str, Any]:
    """The putListingsItem body for either listing kind.

    ``offer_on_existing_asin`` sends ``requirements=LISTING_OFFER_ONLY`` and names
    the product through the ``merchant_suggested_asin`` attribute
    (``[{"value": <ASIN>, "marketplace_id": <marketplace>}]``, the standard
    attribute value shape of the product type definitions). Its product type
    defaults to ``PRODUCT``, the generic type Amazon documents for offer-only
    listings. ``new_product`` sends ``requirements=LISTING`` with the caller's
    product type and attributes.
    """
    data = context.input
    kind = data.get("listing_kind")
    if kind not in LISTING_KINDS:
        raise _invalid("input.listing_kind is required: offer_on_existing_asin or new_product")
    marketplace_id = context.connection.marketplace_id
    attributes = check_attributes(context, data.get("attributes"))
    if data.get("condition_type") is not None:
        if "condition_type" in attributes:
            raise _invalid("Give condition_type either as an input or inside attributes, not both")
        attributes["condition_type"] = [
            {"value": data["condition_type"], "marketplace_id": marketplace_id}
        ]
    if kind == "offer_on_existing_asin":
        if data.get("asin") is None:
            raise _invalid("input.asin is required for an offer on an existing ASIN")
        if "merchant_suggested_asin" in attributes:
            raise _invalid(
                "merchant_suggested_asin is set from input.asin; remove it from attributes"
            )
        attributes["merchant_suggested_asin"] = [
            {"value": asin(data["asin"]), "marketplace_id": marketplace_id}
        ]
        return {
            "productType": product_type(data.get("product_type") or OFFER_ONLY_PRODUCT_TYPE),
            "requirements": "LISTING_OFFER_ONLY",
            "attributes": attributes,
        }
    if data.get("asin") is not None:
        raise _invalid(
            "input.asin is only for offer_on_existing_asin; a new product has no ASIN yet"
        )
    if data.get("product_type") is None:
        raise _invalid("input.product_type is required for a new product")
    return {
        "productType": product_type(data["product_type"]),
        "requirements": "LISTING",
        "attributes": attributes,
    }


def check_patches(context: Context, patches: Any) -> list[dict[str, Any]]:
    checked: list[dict[str, Any]] = []
    for index, patch in enumerate(as_list(patches)):
        where = f"input.patches[{index}]"
        match = _PATCH_PATH_RE.fullmatch(str(patch.get("path", "")))
        if match is None:
            raise _invalid(
                f"{where}.path must be /attributes/<attribute name> (top-level attributes only)"
            )
        name = match.group(1)
        op = patch["op"]
        if op == "merge" and name not in MERGEABLE_ATTRIBUTES:
            raise _invalid(
                f"{where}: Amazon supports merge only for purchasable_offer and fulfillment_availability"
            )
        entry: dict[str, Any] = {"op": op, "path": patch["path"]}
        if "value" in patch:
            check_attribute_values(context, name, patch["value"], f"{where}.value")
            entry["value"] = patch["value"]
        elif op != "delete":
            raise _invalid(f"{where}.value is required for {op}")
        checked.append(entry)
    if not checked:
        raise _invalid("input.patches needs at least one patch")
    return checked


def build_patch_body(context: Context) -> dict[str, Any]:
    data = context.input
    if data.get("product_type") is None:
        raise _invalid("input.product_type is required: Amazon needs it for every patch")
    return {
        "productType": product_type(data["product_type"]),
        "patches": check_patches(context, data.get("patches")),
    }


def patch_body(type_name: str, attribute: str, value: list[dict[str, Any]]) -> dict[str, Any]:
    """A one-patch ``merge`` body touching exactly one attribute."""
    return {
        "productType": product_type(type_name),
        "patches": [{"op": "merge", "path": f"/attributes/{attribute}", "value": value}],
    }


#: Outputs of every real put/patch: acceptance is reported, application is not.
SUBMISSION_OUTPUTS: tuple[Output, ...] = (
    out("sku", TEXT),
    out("submission_id", NULLABLE_TEXT),
    out("status", TEXT),
    out("accepted", BOOLEAN),
    out("applied", BOOLEAN),
    out("check_with", TEXT),
    out("issues", ARRAY_OF_OBJECTS),
    out("amazon", OBJECT),
)
SUBMISSION_NOTE = (
    " Amazon answers ACCEPTED when the submission passed its synchronous checks; it applies "
    "the change later (usually within minutes) and may still raise issues, so accepted is not "
    "applied: read the listing with get_listing to confirm. INVALID means Amazon refused it and "
    "nothing changed (accepted false, external_effect_status failed)."
)


# -- submitting -------------------------------------------------------------------


def submit(
    context: Context,
    method: str,
    body: dict[str, Any],
    *,
    preview: bool,
) -> dict[str, Any]:
    """Send one put/patch, as a real submission or as a validation preview."""
    sku = str(context.input["sku"])
    query: dict[str, Any] = {
        "marketplaceIds": [context.connection.marketplace_id],
        "includedData": ["issues", "identifiers"] if preview else ["issues"],
        "mode": "VALIDATION_PREVIEW" if preview else None,
        "issueLocale": issue_locale(context),
    }
    response = context.transport.call(
        method,
        ITEM_PATH,
        path=seller_path(context, sku),
        query=query,
        body=body,
        mutating=not preview,
        purpose="Amazon listing preview" if preview else "Amazon listing submission",
    )
    raw = response.body if isinstance(response.body, dict) else {}
    status = raw.get("status")
    if preview:
        if status not in ("VALID", "INVALID"):
            raise ExtensionError(errors.UPSTREAM_FAILURE, "Amazon returned no validation status")
        identifiers = marketplace_entry(raw.get("identifiers"), context.connection.marketplace_id)
        return {
            "sku": raw.get("sku") or sku,
            "status": status,
            "valid": status == "VALID",
            "issues": normalize_issues(raw.get("issues")),
            "asin": dig(identifiers, "asin"),
            "amazon": raw,
        }
    if status not in ("ACCEPTED", "INVALID"):
        raise ExtensionError(
            errors.OUTCOME_UNKNOWN,
            "Amazon answered without a submission status; read the listing before retrying",
            definitely_no_external_effect=False,
            external_effect_status="unknown",
        )
    result: dict[str, Any] = {
        "sku": raw.get("sku") or sku,
        "submission_id": raw.get("submissionId"),
        "status": status,
        "accepted": status == "ACCEPTED",
        "applied": False,
        "check_with": "get_listing",
        "issues": normalize_issues(raw.get("issues")),
        "amazon": raw,
    }
    if status == "INVALID":
        # Amazon refused the submission synchronously: nothing was changed.
        result["external_effect_status"] = "failed"
        result["definitely_no_external_effect"] = True
    return result


__all__ = [
    "API_VERSION",
    "ITEM_PATH",
    "PATCH_PROPERTIES",
    "PUT_PROPERTIES",
    "SEARCH_PATH",
    "SUBMISSION_NOTE",
    "SUBMISSION_OUTPUTS",
    "build_patch_body",
    "build_put_body",
    "get_listing_item",
    "normalize_issues",
    "normalize_listing",
    "patch_body",
    "submit",
]
