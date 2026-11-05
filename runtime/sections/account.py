"""Connection checks: the workflow-visible test and the platform's post-connect check.

Both make the same two reads:

1. Sellers ``getMarketplaceParticipations`` — does the token work, and does the
   seller sell in the connection's marketplace?
2. Listings Items ``searchListingsItems`` for the connection's Seller ID with a
   page size of one — does the Seller ID belong to this token? The Seller ID is
   typed by a person, so it counts as confirmed only after Amazon answers this.

A 403 is reported with the list of things to check, never as "role X is
missing": Amazon's answer does not say which of them is the cause.
"""

from __future__ import annotations

from typing import Any

from .. import errors
from ..errors import ExtensionError
from ..registry import (
    ARRAY_OF_OBJECTS,
    BOOLEAN,
    CONNECTION_REF,
    NULLABLE_OBJECT,
    NULLABLE_TEXT,
    TEXT,
    Context,
    Operation,
    out,
)
from ..values import as_list, dig

PARTICIPATIONS_PATH = "/sellers/v1/marketplaceParticipations"
LISTINGS_SEARCH_PATH = "/listings/2021-08-01/items/{sellerId}"
#: Core refuses a verified display name longer than this many UTF-8 bytes.
MAX_DISPLAY_NAME_BYTES = 512


def _check(
    name: str, status: str, *, error_code: str | None = None, message: str = ""
) -> dict[str, Any]:
    return {"check": name, "status": status, "error_code": error_code, "message": message}


def _participation(context: Context) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        response = context.transport.call(
            "GET", PARTICIPATIONS_PATH, mutating=False, purpose="Amazon marketplace participations"
        )
    except ExtensionError as exc:
        return _check(
            "marketplace_participation", "failed", error_code=exc.code, message=exc.message
        ), None
    for entry in as_list(dig(response.body, "payload")):
        if dig(entry, "marketplace", "id") == context.connection.marketplace_id:
            participating = dig(entry, "participation", "isParticipating") is True
            status = "passed" if participating else "failed"
            message = (
                "" if participating else "The seller does not currently sell in this marketplace"
            )
            return _check("marketplace_participation", status, message=message), entry
    return (
        _check(
            "marketplace_participation",
            "failed",
            error_code=errors.INVALID_CONFIGURATION,
            message="The seller is not registered in the connection's marketplace",
        ),
        None,
    )


def _seller_check(context: Context) -> dict[str, Any]:
    try:
        context.transport.call(
            "GET",
            LISTINGS_SEARCH_PATH,
            path={"sellerId": context.connection.seller_id},
            query={"marketplaceIds": [context.connection.marketplace_id], "pageSize": 1},
            mutating=False,
            purpose="Amazon seller ID check",
        )
    except ExtensionError as exc:
        message = exc.message
        if exc.code in {errors.ACCESS_DENIED, errors.UPSTREAM_VALIDATION_FAILED, errors.NOT_FOUND}:
            message = (
                "Amazon did not accept this Seller ID for this token. Check the Seller ID "
                "(Seller Central > Settings > Account Info > Merchant Token) and that the "
                "application has the Product Listing role."
            )
        return _check("seller_id", "failed", error_code=exc.code, message=message)
    return _check("seller_id", "passed")


def _test_connection(context: Context) -> dict[str, Any]:
    participation_check, entry = _participation(context)
    if participation_check["error_code"] in {
        errors.AUTHENTICATION_FAILED,
        errors.REAUTHORIZATION_REQUIRED,
    }:
        raise ExtensionError(participation_check["error_code"], participation_check["message"])
    seller_check = _seller_check(context)
    marketplace = context.connection.marketplace
    return {
        "ok": participation_check["status"] == "passed" and seller_check["status"] == "passed",
        "seller_id": context.connection.seller_id,
        "seller_id_confirmed": seller_check["status"] == "passed",
        "marketplace": marketplace.code,
        "marketplace_id": marketplace.marketplace_id,
        "region": marketplace.region,
        "store_name": dig(entry, "storeName"),
        "default_currency_code": dig(entry, "marketplace", "defaultCurrencyCode"),
        "checks": [participation_check, seller_check],
    }


def _post_connect(
    *,
    subject: str = "",
    health: str,
    error_code: str | None,
    safe_metadata: dict[str, Any] | None = None,
    verified_display_name: str | None = None,
) -> dict[str, Any]:
    """The platform's closed post-connect shape, every key present every time."""
    return {
        "subject": subject,
        "health": health,
        "error_code": error_code,
        "safe_metadata": safe_metadata,
        "verified_display_name": verified_display_name,
    }


def _display_name(value: Any) -> str:
    """The store name within Core's 512-byte limit, cut on a character boundary."""
    if not isinstance(value, str):
        return ""
    encoded = value.strip().encode("utf-8")[:MAX_DISPLAY_NAME_BYTES]
    return encoded.decode("utf-8", "ignore").strip()


def _verify_connection(context: Context) -> dict[str, Any]:
    """The post-connect check, in the platform's closed identity/health shape.

    Core accepts exactly these keys; ``error_code`` is null when ``health`` is
    ``ok`` and one of Core's codes otherwise.
    """
    participation_check, entry = _participation(context)
    code = participation_check["error_code"]
    if code in {errors.AUTHENTICATION_FAILED, errors.REAUTHORIZATION_REQUIRED}:
        return _post_connect(health="authorization_required", error_code="authorization_required")
    if code in {
        errors.TIMEOUT,
        errors.CONNECTION_FAILED,
        errors.UPSTREAM_FAILURE,
        errors.RATE_LIMITED,
    }:
        return _post_connect(health="needs_attention", error_code="provider_unavailable")
    seller_check = _seller_check(context)
    marketplace = context.connection.marketplace
    metadata = {"marketplace": marketplace.code, "region": marketplace.region}
    if participation_check["status"] != "passed":
        metadata["problem"] = "marketplace_not_confirmed"
    elif seller_check["status"] != "passed":
        metadata["problem"] = "seller_id_not_confirmed"
    if "problem" in metadata:
        return _post_connect(
            health="needs_attention", error_code="internal_failure", safe_metadata=metadata
        )
    store_name = dig(entry, "storeName")
    display_name = _display_name(store_name)
    return _post_connect(
        subject=context.connection.seller_id,
        health="ok",
        error_code=None,
        safe_metadata=metadata,
        verified_display_name=display_name or None,
    )


_ROLES = (
    "Selling Partner Insights or Product Listing (getMarketplaceParticipations)",
    "Product Listing (searchListingsItems)",
)
_CALLS = (
    "Sellers v1 getMarketplaceParticipations",
    "Listings Items 2021-08-01 searchListingsItems",
)

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        operation_id="test_connection",
        display_name="Test the connection",
        description=(
            "Checks that the token works, that the seller sells in the connection's marketplace, "
            "and that Amazon accepts the connection's Seller ID. Reports each check separately."
        ),
        section="account",
        properties={"connection_ref": CONNECTION_REF},
        required=("connection_ref",),
        outputs=(
            out("ok", BOOLEAN),
            out("seller_id", TEXT),
            out("seller_id_confirmed", BOOLEAN),
            out("marketplace", TEXT),
            out("marketplace_id", TEXT),
            out("region", TEXT),
            out("store_name", NULLABLE_TEXT),
            out("default_currency_code", NULLABLE_TEXT),
            out("checks", ARRAY_OF_OBJECTS),
        ),
        handler=_test_connection,
        amazon_calls=_CALLS,
        roles=_ROLES,
        labels={"connection_ref": "Connection"},
    ),
    Operation(
        operation_id="verify_connection",
        display_name="Verify the connection",
        description="Runs after a refresh token is imported and confirms the seller and marketplace.",
        section="account",
        properties={},
        required=(),
        outputs=(
            out("subject", TEXT),
            out("health", TEXT),
            out("error_code", NULLABLE_TEXT),
            out("safe_metadata", NULLABLE_OBJECT),
            out("verified_display_name", NULLABLE_TEXT),
        ),
        handler=_verify_connection,
        amazon_calls=_CALLS,
        roles=_ROLES,
        workflow_visible=False,
    ),
)
