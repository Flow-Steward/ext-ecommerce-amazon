"""The closed operation registry: every routable operation, and nothing else.

Each section module declares literal rows; this module only collects them and
refuses duplicates. ``contracts/operation_manifest.yaml``,
``contracts/step_ui_manifest.yaml``, ``ui/actions/actions.yaml``,
``contracts/artifact_policies.yaml`` and the external-effect rows in
``extension.yaml`` are generated from ``OPERATIONS`` and held in exact parity by
the extension's own tests.
"""

from __future__ import annotations

from .registry import Operation
from .sections import (
    account,
    catalog_items,
    feeds,
    finances,
    inventory,
    listings,
    orders,
    pricing,
    reports,
    requirements,
    shipping,
)
from .validation import assert_executable

CATEGORY = "ecommerce"
CHANNEL = "amazon_sp_api"

SECTIONS = (
    account,
    orders,
    shipping,
    catalog_items,
    listings,
    requirements,
    pricing,
    inventory,
    feeds,
    finances,
    reports,
)

OPERATIONS: tuple[Operation, ...] = tuple(row for section in SECTIONS for row in section.OPERATIONS)


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
