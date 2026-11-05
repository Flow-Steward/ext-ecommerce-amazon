"""One Amazon connection, rebuilt from what the host hydrates for one invocation.

A connection is one seller account in one marketplace. The platform imports the
seller's refresh token, keeps it, refreshes the access token under its own lock,
and hands this extension only the current access token in
``action.target.connection.secrets.access_token``. The refresh token and the LWA
client secret never reach this process.

The non-secret settings are ``seller_id`` and ``marketplace``, validated by the
platform against the connection type's ``config_schema`` and re-checked here.
``oauth`` is the platform-owned partition; its ``subject`` is the seller ID the
post-connect check confirmed, and when present it must match ``seller_id``.

Nothing here is cached or stored at module level, so two connections used one
after the other in the same worker process cannot see each other's token.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from . import errors
from .errors import ExtensionError
from .marketplaces import Marketplace, marketplace_for_code

CONNECTION_TYPE_ID = "amazon_seller"

CONFIG_FIELDS: frozenset[str] = frozenset({"seller_id", "marketplace", "oauth"})
SECRET_FIELDS: frozenset[str] = frozenset({"access_token"})

#: An Amazon merchant token: upper-case letters and digits.
_SELLER_ID_RE = re.compile(r"^[A-Z0-9]{8,20}$")
#: Printable, non-space ASCII. A token outside this set could inject a header.
_TOKEN_RE = re.compile(r"^[\x21-\x7e]+$")
MIN_TOKEN_LENGTH = 16
MAX_TOKEN_LENGTH = 4096

_RECONNECT = "Import a new refresh token for this Amazon connection to continue"


@dataclass(frozen=True)
class Connection:
    """One validated Amazon seller account in one marketplace."""

    connection_id: str
    seller_id: str
    marketplace: Marketplace
    _access_token: str = field(repr=False)

    @property
    def api_base_url(self) -> str:
        return self.marketplace.api_base_url

    @property
    def marketplace_id(self) -> str:
        return self.marketplace.marketplace_id

    def access_token(self) -> str:
        return self._access_token

    def __repr__(self) -> str:  # pragma: no cover - defensive, never carries the token
        return (
            f"Connection(connection_id={self.connection_id!r}, seller_id={self.seller_id!r}, "
            f"marketplace={self.marketplace.code!r})"
        )

    def __str__(self) -> str:  # pragma: no cover - defensive, never carries the token
        return self.__repr__()


def normalize_seller_id(value: Any) -> str:
    """The seller ID as Amazon writes it, or a refusal that names no value."""
    text = value.strip().upper() if isinstance(value, str) else ""
    if _SELLER_ID_RE.fullmatch(text) is None:
        raise ExtensionError(
            errors.INVALID_CONFIGURATION,
            "Enter the Seller ID (merchant token) from Seller Central: 8 to 20 letters and digits",
        )
    return text


def _access_token(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise ExtensionError(errors.REAUTHORIZATION_REQUIRED, _RECONNECT)
    if (
        len(text) < MIN_TOKEN_LENGTH
        or len(text) > MAX_TOKEN_LENGTH
        or _TOKEN_RE.fullmatch(text) is None
    ):
        raise ExtensionError(errors.INVALID_CONNECTION, "The Amazon access token is not usable")
    return text


def connection_from_payload(payload: Mapping[str, Any], *, connection_ref: str) -> Connection:
    """Validate the selected connection before any outbound request.

    The operation's ``connection_ref`` must name the connection the host hydrated,
    so a workflow cannot ask for one connection's data while the host hydrates
    another.
    """
    action = payload.get("action")
    target = action.get("target") if isinstance(action, Mapping) else None
    connection = target.get("connection") if isinstance(target, Mapping) else None
    if not isinstance(connection, Mapping):
        raise ExtensionError(errors.INVALID_CONNECTION, "The selected connection is unavailable")
    connection_id = str(connection.get("connection_id") or "")
    if not connection_id or connection_id != connection_ref:
        raise ExtensionError(
            errors.INVALID_CONNECTION, "The selected connection does not match connection_ref"
        )
    connection_type = str(
        connection.get("connection_type_id") or connection.get("connection_type") or ""
    )
    if connection_type and connection_type != CONNECTION_TYPE_ID:
        raise ExtensionError(errors.INVALID_CONNECTION, "The selected connection is not supported")
    config = connection.get("config")
    config = config if isinstance(config, Mapping) else {}
    secrets = connection.get("secrets")
    secrets = secrets if isinstance(secrets, Mapping) else {}
    if set(config) - CONFIG_FIELDS or set(secrets) - SECRET_FIELDS:
        raise ExtensionError(errors.INVALID_CONNECTION, "The connection carries unsupported fields")
    return connection_from_parts(
        connection_id=connection_id,
        config=config,
        access_token=secrets.get("access_token"),
    )


def connection_from_parts(
    *, connection_id: str, config: Mapping[str, Any], access_token: Any
) -> Connection:
    seller_id = normalize_seller_id(config.get("seller_id"))
    marketplace = marketplace_for_code(config.get("marketplace"))
    oauth = config.get("oauth")
    subject = oauth.get("subject") if isinstance(oauth, Mapping) else None
    if isinstance(subject, str) and subject.strip() and subject.strip().upper() != seller_id:
        raise ExtensionError(
            errors.INVALID_CONNECTION,
            "The connection's Seller ID differs from the one its check confirmed; "
            "import the refresh token again",
        )
    return Connection(
        connection_id=connection_id,
        seller_id=seller_id,
        marketplace=marketplace,
        _access_token=_access_token(access_token),
    )


__all__ = [
    "CONFIG_FIELDS",
    "CONNECTION_TYPE_ID",
    "SECRET_FIELDS",
    "Connection",
    "connection_from_parts",
    "connection_from_payload",
    "normalize_seller_id",
]
