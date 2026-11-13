"""Extra workflow examples for established inputs; never runtime defaults.

Keep operation-specific examples in the registry when available. These common
examples show formats only; IDs returned by Amazon must be bound from earlier
steps. Opaque feed, report, plan and shipment IDs deliberately have no fake hint.
"""

from __future__ import annotations

from typing import Any

FIELD_EXAMPLES: dict[str, Any] = {
    "order_id": "123-1234567-1234567",
    "amazon_order_id": "123-1234567-1234567",
    "amazon_order_ids": ["123-1234567-1234567"],
    "sku": "MUG-BLUE-01",
    "seller_sku": "MUG-BLUE-01",
    "seller_skus": ["MUG-BLUE-01"],
    "skus": ["MUG-BLUE-01"],
    "mskus": ["MUG-BLUE-01"],
    "asin": "B000000000",
    "asins": ["B000000000"],
    "keywords": ["blue ceramic mug"],
    "keywords_locale": "en_GB",
    "issue_locale": "en_GB",
    "quantity": 24,
    "price": "19.90",
    "business_price": "17.90",
    "currency_code": "GBP",
    "created_after": "2026-11-01T00:00:00Z",
    "created_before": "2026-11-08T00:00:00Z",
    "last_updated_after": "2026-11-01T00:00:00Z",
    "last_updated_before": "2026-11-08T00:00:00Z",
    "data_start_time": "2026-11-01T00:00:00Z",
    "data_end_time": "2026-11-08T00:00:00Z",
    "posted_after": "2026-11-01T00:00:00Z",
    "posted_before": "2026-11-08T00:00:00Z",
    "start_date_time": "2026-11-01T00:00:00Z",
}
