"""One record shape for Finances API v0 events and 2024-06-19 transactions.

Finances v0 answers with an object of named event lists (``ShipmentEventList``,
``RefundEventList``, ``ServiceFeeEventList``, ``AdjustmentEventList`` and about
thirty more). Finances 2024-06-19 answers with a flat list of transactions.
Both become records of the same shape::

    {record_type, record_id, posted_date, amazon_order_id, status, description,
     total, amounts, origin: {api_version, amazon_operation, list_name}, amazon}

The shape is shared; the data is not. A v0 event and a 2024-06-19 transaction
for the same sale are different objects with different breakdowns, and they
share no identifier (v0 events have none at all). Records of the two versions
must never be mixed to deduplicate or reconcile.

``amounts`` lists every money value found in the Amazon object, each labelled
with where it sits (for example
``ShipmentItemList[].ItemChargeList[Principal].ChargeAmount``). Amounts are never
added up here: some are parts of others (a breakdown and its total), and two
currencies are always two amounts. ``total`` is Amazon's own total where the API
gives one (2024-06-19 ``totalAmount``) and null otherwise.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .values import as_list, first, money

V2024 = "2024-06-19"
V0 = "v0"

MAX_AMOUNTS_PER_RECORD = 200
_MAX_DEPTH = 12
#: Fields that say what one entry of a nested list is (a charge type, a fee type...).
_DISCRIMINATORS = (
    "ChargeType",
    "FeeType",
    "PromotionType",
    "TaxCollectionModel",
    "DirectPaymentType",
    "breakdownType",
)
_POSTED_DATE_KEYS = ("PostedDate", "postedDate", "TransactionPostedDate", "TransactionCreationDate")


def collect_amounts(value: Any) -> tuple[list[dict[str, str]], bool]:
    """Every money value inside ``value``, labelled by its path; and whether cut off.

    The result is cut off (``True``) when more than ``MAX_AMOUNTS_PER_RECORD``
    amounts exist, or when an amount sits deeper than ``_MAX_DEPTH`` levels and
    is therefore not listed. The full object stays under ``amazon`` either way.
    """
    found: list[dict[str, str]] = []
    state = {"truncated": False}
    _walk(value, "", found, 0, state)
    return found, state["truncated"]


def _walk(
    value: Any, label: str, found: list[dict[str, str]], depth: int, state: dict[str, bool]
) -> bool:
    """Collect amounts; return True to stop the walk (the count cap was reached)."""
    if depth > _MAX_DEPTH:
        if _contains_money(value):
            state["truncated"] = True
        return False
    if isinstance(value, Mapping):
        amount = money(value)
        if amount is not None:
            if len(found) >= MAX_AMOUNTS_PER_RECORD:
                state["truncated"] = True
                return True
            found.append({"label": label or "amount", **amount})
            return False
        for key, item in value.items():
            if isinstance(item, (Mapping, list)):
                child = f"{label}.{key}" if label else str(key)
                if _walk(item, child, found, depth + 1, state):
                    return True
        return False
    if isinstance(value, list):
        for item in value:
            kind = first(item, *_DISCRIMINATORS)
            child = f"{label}[{kind}]" if isinstance(kind, str) and kind else f"{label}[]"
            if _walk(item, child, found, depth + 1, state):
                return True
    return False


def _contains_money(value: Any) -> bool:
    """Whether a subtree holds at least one money value; iterative, so depth costs no stack."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            if money(item) is not None:
                return True
            pending.extend(child for child in item.values() if isinstance(child, (Mapping, list)))
        elif isinstance(item, list):
            pending.extend(child for child in item if isinstance(child, (Mapping, list)))
    return False


def _record(
    *,
    record_type: Any,
    record_id: Any,
    posted_date: Any,
    amazon_order_id: Any,
    status: Any,
    description: Any,
    total: Any,
    raw: Any,
    api_version: str,
    amazon_operation: str,
    list_name: str,
) -> dict[str, Any]:
    amounts, truncated = collect_amounts(raw)
    return {
        "record_type": record_type,
        "record_id": record_id,
        "posted_date": posted_date,
        "amazon_order_id": amazon_order_id,
        "status": status,
        "description": description,
        "total": total,
        "amounts": amounts,
        "amounts_truncated": truncated,
        "origin": {
            "api_version": api_version,
            "amazon_operation": amazon_operation,
            "list_name": list_name,
        },
        "amazon": raw,
    }


def records_from_v0(financial_events: Any, amazon_operation: str) -> list[dict[str, Any]]:
    """Flatten v0 ``FinancialEvents`` (an object of event lists) into records.

    Lists keep Amazon's order; within a list, events keep Amazon's order.
    """
    records: list[dict[str, Any]] = []
    if not isinstance(financial_events, Mapping):
        return records
    for list_name, events in financial_events.items():
        record_type = list_name[: -len("List")] if list_name.endswith("List") else list_name
        for event in as_list(events):
            if not isinstance(event, Mapping):
                continue
            records.append(
                _record(
                    record_type=record_type,
                    record_id=None,
                    posted_date=first(event, *_POSTED_DATE_KEYS),
                    amazon_order_id=event.get("AmazonOrderId"),
                    status=None,
                    description=None,
                    total=None,
                    raw=event,
                    api_version=V0,
                    amazon_operation=amazon_operation,
                    list_name=str(list_name),
                )
            )
    return records


def _related(transaction: Mapping[str, Any], name: str) -> Any:
    for item in as_list(transaction.get("relatedIdentifiers")):
        if isinstance(item, Mapping) and item.get("relatedIdentifierName") == name:
            return item.get("relatedIdentifierValue")
    return None


def records_from_2024(transactions: Any, amazon_operation: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for transaction in as_list(transactions):
        if not isinstance(transaction, Mapping):
            continue
        records.append(
            _record(
                record_type=transaction.get("transactionType"),
                record_id=transaction.get("transactionId"),
                posted_date=transaction.get("postedDate"),
                amazon_order_id=_related(transaction, "ORDER_ID"),
                status=transaction.get("transactionStatus"),
                description=transaction.get("description"),
                total=money(transaction.get("totalAmount")),
                raw=transaction,
                api_version=V2024,
                amazon_operation=amazon_operation,
                list_name="transactions",
            )
        )
    return records


def normalize_event_group(raw: Any) -> dict[str, Any]:
    return {
        "group_id": first(raw, "FinancialEventGroupId"),
        "processing_status": first(raw, "ProcessingStatus"),
        "fund_transfer_status": first(raw, "FundTransferStatus"),
        "original_total": money(first(raw, "OriginalTotal")),
        "converted_total": money(first(raw, "ConvertedTotal")),
        "beginning_balance": money(first(raw, "BeginningBalance")),
        "fund_transfer_date": first(raw, "FundTransferDate"),
        "group_start": first(raw, "FinancialEventGroupStart"),
        "group_end": first(raw, "FinancialEventGroupEnd"),
        "trace_id": first(raw, "TraceId"),
        "account_tail": first(raw, "AccountTail"),
        "amazon": raw,
    }


__all__ = [
    "MAX_AMOUNTS_PER_RECORD",
    "V0",
    "V2024",
    "collect_amounts",
    "normalize_event_group",
    "records_from_2024",
    "records_from_v0",
]
