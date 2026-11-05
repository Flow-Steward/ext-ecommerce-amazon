"""Value normalization shared by every handler.

Amazon sends money in three shapes depending on the API and version:
``{"amount": "1.20", "currencyCode": "EUR"}`` (Orders 2026-01-01, Listings),
``{"Amount": "1.20", "CurrencyCode": "EUR"}`` (Orders v0, Pricing v0) and
``{"currencyAmount": 1.20, "currencyCode": "EUR"}`` (Finances 2024-06-19), plus
``CurrencyAmount`` in Finances v0. Every one becomes
``{"amount": "<decimal string>", "currency_code": "<ISO 4217>"}`` here. Amounts
are never added together: two currencies are two amounts.

Responses are decoded with ``Decimal`` for every number with a fraction, and
``json_safe`` turns those back into exact decimal strings for the result.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

#: The subprocess entrypoint (``main.MAX_SERIALIZED_RESPONSE_BYTES``) replaces any
#: response larger than this with ``response_too_large``. Handlers that return
#: content of unbounded size budget against it.
MAX_SERIALIZED_RESPONSE_BYTES = 9 * 512 * 1024

_AMOUNT_KEYS = ("amount", "Amount", "currencyAmount", "CurrencyAmount")
_CURRENCY_KEYS = ("currencyCode", "CurrencyCode")


def decimal_text(value: Any) -> str | None:
    """An exact decimal string for a JSON number or numeric string, else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):  # pragma: no cover - decoding never yields floats
        return format(Decimal(repr(value)), "f")
    if isinstance(value, str):
        try:
            return format(Decimal(value.strip()), "f")
        except ArithmeticError:
            return None
    return None


def money(value: Any) -> dict[str, str] | None:
    """One Amazon money object in the extension's single shape, or None if absent.

    None means Amazon did not send the amount. It is never turned into zero.
    """
    if not isinstance(value, Mapping):
        return None
    amount = next((value[key] for key in _AMOUNT_KEYS if key in value), None)
    currency = next((value[key] for key in _CURRENCY_KEYS if key in value), None)
    text = decimal_text(amount)
    if text is None or not isinstance(currency, str) or not currency:
        return None
    return {"amount": text, "currency_code": currency}


def json_safe(value: Any) -> Any:
    """A deep copy with every Decimal turned into its exact decimal string."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def first(value: Any, *keys: str) -> Any:
    """The first present key of a mapping, else None."""
    if not isinstance(value, Mapping):
        return None
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return None


def dig(value: Any, *path: str) -> Any:
    """Follow ``path`` through nested mappings; None as soon as a step is missing."""
    current = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def as_list(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def serialized_size(value: Any) -> int:
    """UTF-8 bytes of ``value`` serialized the way the entrypoint writes it."""
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_decimal_default,
        ).encode("utf-8")
    )


def _decimal_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(type(value).__name__)


__all__ = [
    "MAX_SERIALIZED_RESPONSE_BYTES",
    "as_list",
    "decimal_text",
    "dig",
    "first",
    "json_safe",
    "money",
    "serialized_size",
]
