"""Finances, in both Amazon API versions.

The record operations take ``api_version``: ``2024-06-19`` (the default,
``listTransactions``) or ``v0`` (``listFinancialEvents`` and its by-order and
by-group variants). Both return records of one shape (see
``runtime/finances_records.py``), and what differs is stated, not hidden:

* v0 events and 2024-06-19 transactions are NOT interchangeable. They are
  different objects with different breakdowns and share no identifier, so a
  workflow must never mix records of the two versions to deduplicate or
  reconcile. Pick one version per workflow.
* Filters that exist in only one version are accepted only with that version;
  sending one to the other version is ``unsupported_filter``, never a silently
  dropped condition. 2024-06-19 has ``transaction_status`` and
  ``omit_marketplace_filter`` and no page size; v0 has ``max_results`` and no
  marketplace filter at all (its events cover every marketplace of the region).
* Amazon may take up to 48 hours to post a financial event, so a window that
  ends now is not complete yet.
* Money is ``{"amount": "<exact decimal>", "currency_code"}``; amounts are never
  added together, and each record keeps Amazon's object under ``amazon``.

Removal date: Amazon deprecated v0 ``listFinancialEvents``,
``listFinancialEventsByGroupId`` and ``listFinancialEventsByOrderId`` on
2025-07-21 and removes them on 2027-08-27. After that date the ``v0`` choice
fails with Amazon's own error; nothing falls back to the other version on its own.
``listFinancialEventGroups`` (v0) is not on Amazon's deprecation schedule and has
no 2024-06-19 counterpart, so ``list_financial_event_groups`` is v0 only.
"""

from __future__ import annotations

from typing import Any

from .. import errors, pagination, validation
from ..errors import ExtensionError
from ..finances_records import V0, V2024, normalize_event_group, records_from_2024, records_from_v0
from ..registry import (
    AMAZON_OPERATION_OUTPUT,
    API_VERSION_OUTPUT,
    ARRAY_OF_OBJECTS,
    CONNECTION_REF,
    INTEGER,
    NEXT_TOKEN,
    NEXT_TOKEN_OUTPUT,
    Context,
    Operation,
    boolean,
    enum,
    integer,
    out,
    text,
    timestamp,
)
from ..values import as_list, dig

API_VERSIONS = (V2024, V0)

TRANSACTIONS = "/finances/2024-06-19/transactions"
EVENTS_V0 = "/finances/v0/financialEvents"
EVENTS_BY_ORDER_V0 = "/finances/v0/orders/{orderId}/financialEvents"
EVENTS_BY_GROUP_V0 = "/finances/v0/financialEventGroups/{eventGroupId}/financialEvents"
GROUPS_V0 = "/finances/v0/financialEventGroups"

TRANSACTION_STATUSES = ("DEFERRED", "RELEASED", "DEFERRED_RELEASED")

_DELAY_NOTE = (
    "Amazon may take up to 48 hours to post an event, so a window ending now is not complete yet."
)
_MIX_NOTE = (
    "v0 events and 2024-06-19 transactions are not interchangeable and share no ID: never mix "
    "records of both versions to deduplicate or reconcile."
)

API_VERSION_INPUT = enum(
    "Amazon API version",
    "2024-06-19 (listTransactions, default) or v0 (financial events; Amazon removes "
    "listFinancialEvents, listFinancialEventsByGroupId and listFinancialEventsByOrderId on "
    "2027-08-27). The two versions return different records with no shared ID; do not mix them.",
    API_VERSIONS,
)
_POSTED_AFTER = timestamp(
    "Posted after", "Records posted at or after this time; at least two minutes in the past."
)
_POSTED_BEFORE = timestamp(
    "Posted before",
    "Records posted before this time (default: two minutes ago). At most 180 days after posted_after.",
)
_TRANSACTION_STATUS = enum(
    "Transaction status (2024-06-19)", "Only transactions in this status.", TRANSACTION_STATUSES
)
_OMIT_MARKETPLACE = boolean(
    "Do not filter by marketplace (2024-06-19)",
    "Leave out Amazon's marketplace filter. Amazon's own note: to get MFN orders in the US store, "
    "do not send the marketplace. Records may then cover other marketplaces of the seller.",
)
_MAX_RESULTS_V0 = integer("Page size (v0)", "Events per page, 1 to 100.", minimum=1, maximum=100)
_ORDER_ID = text(
    "Amazon order ID", "The Amazon order ID, for example 123-1234567-1234567.", max_length=64
)
_GROUP_ID = text(
    "Financial event group ID", "A group_id from list_financial_event_groups.", max_length=256
)

_ONLY_2024 = ("transaction_status", "omit_marketplace_filter")
_ONLY_V0 = ("max_results",)

_ROLE_FINANCE = "Finance and Accounting"

_RECORD_OUTPUTS = (
    out("records", ARRAY_OF_OBJECTS),
    out("count", INTEGER),
    NEXT_TOKEN_OUTPUT,
    API_VERSION_OUTPUT,
    AMAZON_OPERATION_OUTPUT,
)


def api_version(context: Context) -> str:
    return str(context.input.get("api_version") or V2024)


def _refuse_other_version_filters(context: Context, version: str) -> None:
    foreign = _ONLY_V0 if version == V2024 else _ONLY_2024
    used = [name for name in foreign if context.input.get(name) is not None]
    if used:
        raise ExtensionError(
            errors.UNSUPPORTED_FILTER,
            f"{', '.join(used)} is not available with Finances API {version}",
        )


def _marketplace(context: Context) -> str | None:
    return (
        None if context.input.get("omit_marketplace_filter") else context.connection.marketplace_id
    )


def _posted_window(
    context: Context, *, required: bool, token: str | None
) -> tuple[str | None, str | None]:
    after = validation.timestamp(context.input.get("posted_after"), "posted_after")
    before = validation.timestamp(context.input.get("posted_before"), "posted_before")
    if required and after is None and token is None:
        raise ExtensionError(errors.INVALID_PAYLOAD, "posted_after is required")
    return after, before


def _transactions(
    context: Context,
    *,
    token: str | None,
    after: str | None = None,
    before: str | None = None,
    related: tuple[str, str] | None = None,
    operation: str,
) -> dict[str, Any]:
    # Amazon: a page token is sent together with the same arguments as the first call.
    query = {
        "postedAfter": after,
        "postedBefore": before,
        "marketplaceId": _marketplace(context),
        "transactionStatus": context.input.get("transaction_status"),
        "relatedIdentifierName": related[0] if related else None,
        "relatedIdentifierValue": related[1] if related else None,
        "nextToken": token,
    }
    response = context.transport.call(
        "GET", TRANSACTIONS, query=query, mutating=False, purpose="Amazon list transactions"
    )
    records = records_from_2024(dig(response.body, "payload", "transactions"), operation)
    return _page(context, V2024, records, dig(response.body, "payload", "nextToken"), operation)


def _events_v0(
    context: Context,
    template: str,
    *,
    token: str | None,
    path: dict[str, str] | None = None,
    after: str | None = None,
    before: str | None = None,
    operation: str,
) -> dict[str, Any]:
    query = {
        "MaxResultsPerPage": context.input.get("max_results"),
        "PostedAfter": after,
        "PostedBefore": before,
        "NextToken": token,
    }
    response = context.transport.call(
        "GET",
        template,
        path=path,
        query=query,
        mutating=False,
        purpose="Amazon list financial events",
    )
    records = records_from_v0(dig(response.body, "payload", "FinancialEvents"), operation)
    return _page(context, V0, records, dig(response.body, "payload", "NextToken"), operation)


def _page(
    context: Context, version: str, records: list[dict[str, Any]], amazon_token: Any, operation: str
) -> dict[str, Any]:
    return {
        "records": records,
        "count": len(records),
        "next_token": pagination.wrap(context, version, amazon_token),
        "api_version": version,
        "amazon_operation": operation,
    }


def _list_financial_records(context: Context) -> dict[str, Any]:
    version = api_version(context)
    _refuse_other_version_filters(context, version)
    token = pagination.unwrap(context, version)
    after, before = _posted_window(context, required=True, token=token)
    if version == V2024:
        return _transactions(
            context,
            token=token,
            after=after,
            before=before,
            operation="Finances 2024-06-19 listTransactions",
        )
    return _events_v0(
        context,
        EVENTS_V0,
        token=token,
        after=after,
        before=before,
        operation="Finances v0 listFinancialEvents",
    )


def _list_financial_records_for_order(context: Context) -> dict[str, Any]:
    version = api_version(context)
    _refuse_other_version_filters(context, version)
    token = pagination.unwrap(context, version)
    order_id = str(context.input["order_id"])
    if version == V2024:
        return _transactions(
            context,
            token=token,
            related=("ORDER_ID", order_id),
            operation="Finances 2024-06-19 listTransactions (relatedIdentifierName=ORDER_ID)",
        )
    return _events_v0(
        context,
        EVENTS_BY_ORDER_V0,
        token=token,
        path={"orderId": order_id},
        operation="Finances v0 listFinancialEventsByOrderId",
    )


def _list_financial_events_by_group(context: Context) -> dict[str, Any]:
    version = api_version(context)
    _refuse_other_version_filters(context, version)
    token = pagination.unwrap(context, version)
    after, before = _posted_window(context, required=False, token=token)
    group_id = str(context.input["group_id"])
    if version == V2024:
        return _transactions(
            context,
            token=token,
            after=after,
            before=before,
            related=("FINANCIAL_EVENT_GROUP_ID", group_id),
            operation="Finances 2024-06-19 listTransactions (relatedIdentifierName=FINANCIAL_EVENT_GROUP_ID)",
        )
    return _events_v0(
        context,
        EVENTS_BY_GROUP_V0,
        token=token,
        path={"eventGroupId": group_id},
        after=after,
        before=before,
        operation="Finances v0 listFinancialEventsByGroupId",
    )


def _list_financial_event_groups(context: Context) -> dict[str, Any]:
    data = context.input
    started_after = validation.timestamp(data.get("started_after"), "started_after")
    started_before = validation.timestamp(data.get("started_before"), "started_before")
    token = pagination.unwrap(context, V0)
    query = {
        "MaxResultsPerPage": data.get("max_results"),
        "FinancialEventGroupStartedAfter": started_after,
        "FinancialEventGroupStartedBefore": started_before,
        "NextToken": token,
    }
    response = context.transport.call(
        "GET", GROUPS_V0, query=query, mutating=False, purpose="Amazon list financial event groups"
    )
    groups = as_list(dig(response.body, "payload", "FinancialEventGroupList"))
    return {
        "groups": [normalize_event_group(group) for group in groups],
        "count": len(groups),
        "next_token": pagination.wrap(context, V0, dig(response.body, "payload", "NextToken")),
        "api_version": V0,
        "amazon_operation": "Finances v0 listFinancialEventGroups",
    }


_RECORD_SHAPE = (
    "Each record has record_type, posted_date, amazon_order_id (when present), amounts (labelled "
    "money values, never summed across currencies), origin and Amazon's object under amazon."
)

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        operation_id="list_financial_records",
        display_name="List financial records",
        description=(
            "Returns one page of financial records posted in a time window: 2024-06-19 transactions "
            "(default) or v0 financial events, flattened into one list. "
            + _RECORD_SHAPE
            + " "
            + _MIX_NOTE
            + " "
            + _DELAY_NOTE
        ),
        section="finances",
        properties={
            "connection_ref": CONNECTION_REF,
            "api_version": API_VERSION_INPUT,
            "posted_after": _POSTED_AFTER,
            "posted_before": _POSTED_BEFORE,
            "transaction_status": _TRANSACTION_STATUS,
            "omit_marketplace_filter": _OMIT_MARKETPLACE,
            "max_results": _MAX_RESULTS_V0,
            "next_token": NEXT_TOKEN,
        },
        required=("connection_ref",),
        outputs=_RECORD_OUTPUTS,
        handler=_list_financial_records,
        amazon_calls=("Finances 2024-06-19 listTransactions", "Finances v0 listFinancialEvents"),
        roles=(_ROLE_FINANCE,),
        extra_error_codes=(errors.UNSUPPORTED_FILTER, errors.CONTINUATION_TOKEN_INVALID),
    ),
    Operation(
        operation_id="list_financial_records_for_order",
        display_name="List financial records of an order",
        description=(
            "Returns one page of the financial records of one order: 2024-06-19 transactions related "
            "to the order ID (default) or v0 financial events of the order. "
            + _RECORD_SHAPE
            + " "
            + _MIX_NOTE
            + " "
            + _DELAY_NOTE
        ),
        section="finances",
        properties={
            "connection_ref": CONNECTION_REF,
            "order_id": _ORDER_ID,
            "api_version": API_VERSION_INPUT,
            "transaction_status": _TRANSACTION_STATUS,
            "omit_marketplace_filter": _OMIT_MARKETPLACE,
            "max_results": _MAX_RESULTS_V0,
            "next_token": NEXT_TOKEN,
        },
        required=("connection_ref", "order_id"),
        outputs=_RECORD_OUTPUTS,
        handler=_list_financial_records_for_order,
        amazon_calls=(
            "Finances 2024-06-19 listTransactions (relatedIdentifierName=ORDER_ID)",
            "Finances v0 listFinancialEventsByOrderId",
        ),
        roles=(_ROLE_FINANCE,),
        extra_error_codes=(errors.UNSUPPORTED_FILTER, errors.CONTINUATION_TOKEN_INVALID),
    ),
    Operation(
        operation_id="list_financial_event_groups",
        display_name="List financial event groups",
        description=(
            "Returns one page of financial event groups (settlement periods) opened in a time window, "
            "with original and converted totals, processing and fund transfer status. Finances v0 "
            "listFinancialEventGroups: v0 only, and not on Amazon's deprecation schedule."
        ),
        section="finances",
        properties={
            "connection_ref": CONNECTION_REF,
            "started_after": timestamp("Started after", "Groups opened at or after this time."),
            "started_before": timestamp(
                "Started before",
                "Groups opened before this time; at most 180 days after started_after.",
            ),
            "max_results": integer(
                "Page size", "Groups per page, 1 to 100.", minimum=1, maximum=100
            ),
            "next_token": NEXT_TOKEN,
        },
        required=("connection_ref",),
        outputs=(
            out("groups", ARRAY_OF_OBJECTS),
            out("count", INTEGER),
            NEXT_TOKEN_OUTPUT,
            API_VERSION_OUTPUT,
            AMAZON_OPERATION_OUTPUT,
        ),
        handler=_list_financial_event_groups,
        amazon_calls=("Finances v0 listFinancialEventGroups",),
        roles=(_ROLE_FINANCE,),
        extra_error_codes=(errors.CONTINUATION_TOKEN_INVALID,),
    ),
    Operation(
        operation_id="list_financial_events_by_group",
        display_name="List financial records of a group",
        description=(
            "Returns one page of the financial records of one financial event group: 2024-06-19 "
            "transactions related to the group ID (default) or v0 listFinancialEventsByGroupId. "
            + _RECORD_SHAPE
            + " "
            + _MIX_NOTE
        ),
        section="finances",
        properties={
            "connection_ref": CONNECTION_REF,
            "group_id": _GROUP_ID,
            "api_version": API_VERSION_INPUT,
            "posted_after": _POSTED_AFTER,
            "posted_before": _POSTED_BEFORE,
            "transaction_status": _TRANSACTION_STATUS,
            "omit_marketplace_filter": _OMIT_MARKETPLACE,
            "max_results": _MAX_RESULTS_V0,
            "next_token": NEXT_TOKEN,
        },
        required=("connection_ref", "group_id"),
        outputs=_RECORD_OUTPUTS,
        handler=_list_financial_events_by_group,
        amazon_calls=(
            "Finances 2024-06-19 listTransactions (relatedIdentifierName=FINANCIAL_EVENT_GROUP_ID)",
            "Finances v0 listFinancialEventsByGroupId",
        ),
        roles=(_ROLE_FINANCE,),
        extra_error_codes=(errors.UNSUPPORTED_FILTER, errors.CONTINUATION_TOKEN_INVALID),
    ),
)
