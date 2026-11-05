"""Health command for the Amazon SP-API extension."""

from __future__ import annotations

import json


def check_health() -> dict[str, object]:
    from runtime.catalog import NETWORK_OPERATION_IDS, OPERATIONS
    from runtime.marketplaces import MARKETPLACES

    return {
        "extension_id": "flowsteward.amazon-sp-api",
        "ok": True,
        "status": "healthy",
        "declared_operations": len(OPERATIONS),
        "network_operations": len(NETWORK_OPERATION_IDS),
        "marketplaces": len(MARKETPLACES),
    }


if __name__ == "__main__":
    print(json.dumps(check_health(), separators=(",", ":"), sort_keys=True))
