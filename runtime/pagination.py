"""Continuation tokens that cannot be carried to another search.

Amazon's own page tokens say nothing about which connection, API version or
filters produced them, and Amazon's answer to a mismatched token varies by API.
This extension therefore hands the workflow its own opaque token: Amazon's token
wrapped together with the operation, the resolved API version, the connection
and a digest of the filters. An operation may name extra inputs that are allowed
to change between pages (``Operation.page_only_inputs``), for example the order
sections ``included_data`` of Orders 2026-01-01. A token presented with a different connection, version or
filter set is refused with ``continuation_token_invalid`` before anything is
sent. The token is not a secret and carries no credential, but it is not meant
to be read either.

Expiry is Amazon's: a token Amazon no longer accepts comes back as the same
error code from the transport.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from . import errors
from .errors import ExtensionError
from .registry import Context

_VERSION = 1
#: Inputs that may differ between pages of the same search. ``api_version`` is
#: not part of the filter digest because the envelope binds the *resolved*
#: version under ``a``: leaving it out and naming the default explicitly is the
#: same search, while naming the other version is still refused.
_PAGE_ONLY_INPUTS = frozenset(
    {"next_token", "max_results", "page_size", "connection_ref", "api_version"}
)


def _filter_digest(context: Context) -> str:
    ignored = _PAGE_ONLY_INPUTS | frozenset(context.operation.page_only_inputs)
    filters = {key: value for key, value in context.input.items() if key not in ignored}
    canonical = json.dumps(filters, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def wrap(context: Context, api_version: str, amazon_token: Any) -> str | None:
    """The workflow-facing token for Amazon's next page, or None on the last page."""
    if not isinstance(amazon_token, str) or not amazon_token:
        return None
    envelope = {
        "v": _VERSION,
        "o": context.operation.operation_id,
        "a": api_version,
        "c": context.connection.connection_id,
        "f": _filter_digest(context),
        "t": amazon_token,
    }
    raw = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def unwrap(context: Context, api_version: str) -> str | None:
    """Amazon's token from the input's ``next_token``, checked against this call."""
    value = context.input.get("next_token")
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        envelope = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError) as exc:
        raise _invalid() from exc
    if not isinstance(envelope, Mapping) or envelope.get("v") != _VERSION:
        raise _invalid()
    expected = {
        "o": context.operation.operation_id,
        "a": api_version,
        "c": context.connection.connection_id,
        "f": _filter_digest(context),
    }
    if any(envelope.get(key) != item for key, item in expected.items()):
        raise ExtensionError(
            errors.CONTINUATION_TOKEN_INVALID,
            "This continuation token belongs to another search, connection or API version; "
            "use the same inputs as the first page",
        )
    token = envelope.get("t")
    if not isinstance(token, str) or not token:
        raise _invalid()
    return token


def _invalid() -> ExtensionError:
    return ExtensionError(errors.CONTINUATION_TOKEN_INVALID, "The continuation token is not valid")


__all__ = ["unwrap", "wrap"]
