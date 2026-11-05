"""Restricted data tokens for the legacy operations that still require them.

Orders ``v0`` returns buyer and shipping-address data only to a call made with a
restricted data token (RDT) from the Tokens API. Orders ``2026-01-01`` does not
use RDTs: it returns personal data according to the application's roles.

An RDT is a short-lived credential. It is used for the one request it was minted
for, never returned, never logged and never stored.
"""

from __future__ import annotations

from collections.abc import Sequence

from . import errors
from .errors import ExtensionError
from .registry import Context
from .transport import encode_path

TOKENS_PATH = "/tokens/2021-03-01/restrictedDataToken"


def restricted_data_token(context: Context, resources: Sequence[tuple[str, str, dict[str, str]]]) -> str:
    """Mint one RDT covering ``resources`` given as (method, path template, path params)."""
    body = {
        "restrictedResources": [
            {"method": method, "path": encode_path(template, params)}
            for method, template, params in resources
        ]
    }
    response = context.transport.call(
        "POST", TOKENS_PATH, body=body, mutating=False, purpose="Amazon restricted data token"
    )
    token = response.body.get("restrictedDataToken") if isinstance(response.body, dict) else None
    if not isinstance(token, str) or not token:
        raise ExtensionError(errors.UPSTREAM_FAILURE, "Amazon returned no restricted data token")
    return token


__all__ = ["TOKENS_PATH", "restricted_data_token"]
