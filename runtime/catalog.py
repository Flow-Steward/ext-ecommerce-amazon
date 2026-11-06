"""The closed operation registry: every routable operation, and nothing else.

Each section module declares literal rows; this module only collects them and
refuses duplicates. ``contracts/operation_manifest.yaml``,
``contracts/step_ui_manifest.yaml``, ``ui/actions/actions.yaml``,
``contracts/artifact_policies.yaml`` and the external-effect rows in
``extension.yaml`` are generated from ``OPERATIONS`` and held in exact parity by
the extension's own tests.
"""

from __future__ import annotations

from dataclasses import replace

from .registry import Operation
from .sections import (
    account,
    catalog_items,
    fba_inbound,
    fba_inbound_documents,
    feeds,
    finances,
    inventory,
    listings,
    orders,
    pricing,
    reports,
    requirements,
    shipping,
    shipping_v2,
)
from .validation import assert_executable

CATEGORY = "ecommerce"
CHANNEL = "amazon_sp_api"

SECTIONS = (
    account,
    orders,
    shipping,
    shipping_v2,
    catalog_items,
    listings,
    requirements,
    pricing,
    inventory,
    fba_inbound,
    fba_inbound_documents,
    feeds,
    finances,
    reports,
)

#: Hand-written methods that address a resource by its Amazon ID, or read
#: seller-wide data, so Amazon answers for any marketplace of the seller in the
#: connection's region (``registry.SCOPES``). ``list_financial_records`` is here
#: because v0 financial events have no marketplace filter and 2024-06-19 drops it
#: with ``omit_marketplace_filter``; its description says when it does filter. Methods built from Amazon's specs
#: derive their scope from the request instead.
SELLER_REGION_METHODS = frozenset(
    {
        "get_order",
        "get_order_items",
        "get_order_buyer_info",
        "confirm_shipment",
        "update_shipment_status",
        "get_feed",
        "get_feed_result",
        "get_report",
        "download_report",
        "list_financial_records",
        "list_financial_records_for_order",
        "list_financial_event_groups",
        "list_financial_events_by_group",
    }
)


def _scoped(row: Operation) -> Operation:
    if row.operation_id in SELLER_REGION_METHODS:
        return replace(row, scope="seller_region")
    return row


OPERATIONS: tuple[Operation, ...] = tuple(
    _scoped(row) for section in SECTIONS for row in section.OPERATIONS
)


def _index(rows: tuple[Operation, ...]) -> dict[str, Operation]:
    indexed: dict[str, Operation] = {}
    for row in rows:
        if row.operation_id in indexed:
            raise ValueError(f"duplicate operation id {row.operation_id}")
        assert_executable(row.input_schema, row.operation_id)
        indexed[row.operation_id] = row
    return indexed


OPERATIONS_BY_ID: dict[str, Operation] = _index(OPERATIONS)
NETWORK_OPERATION_IDS: frozenset[str] = frozenset(
    row.operation_id for row in OPERATIONS if row.contacts_amazon
)

__all__ = ["CATEGORY", "CHANNEL", "NETWORK_OPERATION_IDS", "OPERATIONS", "OPERATIONS_BY_ID"]
