"""The only way this extension reaches Amazon.

Two kinds of request leave the extension:

* SP-API calls, to the regional host of the connection's marketplace. The host is
  derived from the marketplace and never taken from workflow input; the path is
  one of the fixed templates in ``runtime/operations*.py`` with its parameters
  percent-encoded here.
* Document transfers, to the presigned Amazon S3 URL Amazon itself returned for a
  feed or report document, or a product type schema. Only ``https`` URLs on an
  Amazon S3 host are accepted: ``s3.amazonaws.com`` and its regional and
  dual-stack forms (``s3.<region>``, ``s3-<region>``, ``s3-external-1``,
  ``s3.dualstack.<region>``), with or without a bucket label in front. Other
  ``amazonaws.com`` hosts (EC2 instances, API Gateway, Lambda URLs) can be run
  by anyone with an AWS account and are refused. The access token is never sent
  to a document host.

Every request goes out through the public Flow Steward extension SDK, which
performs URL validation, DNS/IP pinning, connected-peer verification, redirect
rejection and timeout enforcement.

Retries are bounded. A read is retried on throttling, on a server error and on a
connection failure, with exponential backoff and full jitter, inside a fixed time
budget. A mutation is retried only on HTTP 429, where Amazon refused the call
before processing it; any other failure after a mutation was sent leaves the
outcome unknown and is reported that way instead of repeated. Each subprocess
retries on its own: Amazon's rate limits are shared by every connection of the
same seller and application in a region, and no cross-call limiter exists, so a
workflow that runs many calls in parallel must bound that parallelism itself.
"""

from __future__ import annotations

import http.client
import io
import json
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request

from flowsteward_extension_sdk import (
    PinnedPeerError,
    assert_safe_remote_https_url,
    open_pinned_url,
    parse_retry_after,
)

from . import errors
from .connection import Connection
from .errors import ExtensionError

DEFAULT_TIMEOUT_SECONDS = 25.0
MAX_TIMEOUT_SECONDS = 30.0
MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 6 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
CHUNK_BYTES = 64 * 1024

#: At most this many attempts for one call, and at most this much time asleep.
MAX_ATTEMPTS = 4
MAX_TOTAL_BACKOFF_SECONDS = 30.0
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 10.0

MAX_AMAZON_ERRORS = 20
MAX_AMAZON_MESSAGE_LENGTH = 500

#: Amazon S3 endpoints only: an optional bucket prefix, then ``s3`` with optional
#: regional / dual-stack labels, under ``amazonaws.com``. Any other amazonaws.com
#: host can be operated by an arbitrary AWS customer.
_S3_HOST_RE = re.compile(r"^(?:[a-z0-9][a-z0-9.-]*\.)?s3(?:[.-][a-z0-9-]+)*\.amazonaws\.com$")

USER_AGENT = "FlowSteward-AmazonSPAPI/0.1 (Language=Python)"

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_INVALID_JSON = "Amazon returned a response that is not valid JSON"


@dataclass(frozen=True)
class ApiResponse:
    """One decoded SP-API response."""

    status: int
    body: Any
    headers: Mapping[str, str] = field(default_factory=dict)


def encode_path(template: str, parameters: Mapping[str, str]) -> str:
    """Fill a fixed path template, percent-encoding every value as one segment.

    ``/`` inside a SKU or an order id is encoded rather than treated as a path
    separator, so a value can never reach a different endpoint.
    """
    path = template
    for name, value in parameters.items():
        if not isinstance(value, str) or not value:
            raise ExtensionError(errors.INVALID_PAYLOAD, f"{name} is required")
        path = path.replace("{" + name + "}", quote(value, safe=""))
    if "{" in path or "}" in path:  # pragma: no cover - templates are fixed
        raise ExtensionError(errors.INTERNAL_ERROR, "An endpoint template was left unfilled")
    return path


def encode_query(query: Mapping[str, Any]) -> str:
    """Encode query parameters the way SP-API expects them.

    Lists become one comma-separated value, booleans become ``true``/``false``,
    and ``None`` is left out entirely, which is how an absent filter is sent.
    """
    pairs: list[tuple[str, str]] = []
    for name, value in query.items():
        if value is None:
            continue
        if isinstance(value, bool):
            pairs.append((name, "true" if value else "false"))
        elif isinstance(value, (list, tuple)):
            if value:
                pairs.append((name, ",".join(str(item) for item in value)))
        else:
            pairs.append((name, str(value)))
    return urlencode(pairs, quote_via=quote)


class SpApiTransport:
    """Issues exactly the request one declared operation describes."""

    def __init__(
        self,
        connection: Connection,
        *,
        opener: Callable[..., Any] = open_pinned_url,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._connection = connection
        self._opener = opener
        self._sleep = sleep
        self._jitter = jitter
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), MAX_TIMEOUT_SECONDS))

    @property
    def connection(self) -> Connection:
        return self._connection

    def call(
        self,
        method: str,
        path_template: str,
        *,
        path: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
        mutating: bool,
        purpose: str,
        access_token: str | None = None,
    ) -> ApiResponse:
        """Send one SP-API request and return its decoded body.

        ``access_token`` replaces the connection's token for one call; it is how a
        restricted data token is used for the operations that need one.
        """
        url = self._connection.api_base_url + encode_path(path_template, path or {})
        encoded_query = encode_query(query or {})
        if encoded_query:
            url = f"{url}?{encoded_query}"
        data = _json_bytes(body) if body is not None else None
        token = access_token if access_token is not None else self._connection.access_token()
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "x-amz-access-token": token,
            "x-amz-date": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
        }
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        backoff_spent = 0.0
        attempt = 0
        while True:
            attempt += 1
            request = Request(url, method=method, data=data, headers=headers)
            try:
                return self._send(request, mutating=mutating, purpose=purpose)
            except _Retry as retry:
                delay = self._delay(attempt, retry)
                retryable = attempt < MAX_ATTEMPTS and (
                    backoff_spent + delay <= MAX_TOTAL_BACKOFF_SECONDS
                )
                if not retryable:
                    raise retry.error from None
                backoff_spent += delay
                self._sleep(delay)

    def _delay(self, attempt: int, retry: _Retry) -> float:
        ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        delay = self._jitter(0.0, ceiling)
        if retry.minimum_delay is not None:
            delay = max(delay, min(retry.minimum_delay, BACKOFF_CAP_SECONDS))
        return delay

    def _send(self, request: Request, *, mutating: bool, purpose: str) -> ApiResponse:
        try:
            with self._opener(
                request, timeout_seconds=self._timeout_seconds, purpose=purpose
            ) as response:
                status = _status(response)
                raw = read_bounded(response, MAX_RESPONSE_BYTES, mutating=mutating)
                response_headers = _headers(getattr(response, "headers", None))
        except HTTPError as exc:
            status = int(exc.code)
            raw_error = _read_error_body(exc)
            raise _failure(status, raw_error, exc.headers, mutating=mutating) from None
        except PinnedPeerError as exc:
            raise ExtensionError(
                errors.BLOCKED_ADDRESS, "The Amazon endpoint is not reachable under the host policy"
            ) from exc
        except TimeoutError as exc:
            if mutating:
                raise ExtensionError(
                    errors.OUTCOME_UNKNOWN,
                    "Amazon did not answer in time; read the current state before retrying",
                    **_transport_facts(mutating=True),
                ) from exc
            raise _Retry(
                ExtensionError(
                    errors.TIMEOUT,
                    "Amazon did not respond in time",
                    **_transport_facts(mutating=False),
                )
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            # HTTPException covers IncompleteRead and BadStatusLine: the connection
            # broke while the answer was being read, so a mutation may have landed.
            if mutating:
                raise ExtensionError(
                    errors.OUTCOME_UNKNOWN,
                    "Amazon could not be reached after the request started; read the current state before retrying",
                    **_transport_facts(mutating=True),
                ) from exc
            raise _Retry(
                ExtensionError(
                    errors.CONNECTION_FAILED,
                    "Amazon could not be reached",
                    **_transport_facts(mutating=False),
                )
            ) from exc
        if 300 <= status < 400:
            raise ExtensionError(
                errors.REDIRECT_REJECTED, "Amazon redirected the request and it was not followed"
            )
        if status >= 400:
            raise _failure(status, raw, None, mutating=mutating)
        if not raw.strip():
            return ApiResponse(status=status, body={}, headers=response_headers)
        return ApiResponse(
            status=status, body=decode_json(raw, mutating=mutating), headers=response_headers
        )

    def download_document(self, url: str, *, max_bytes: int, purpose: str) -> bytes:
        """Fetch one presigned document. The access token is not sent."""
        safe_url = assert_document_url(url)
        request = Request(safe_url, method="GET", headers={"User-Agent": USER_AGENT})
        try:
            with self._opener(
                request, timeout_seconds=self._timeout_seconds, purpose=purpose
            ) as response:
                status = _status(response)
                if status >= 300:
                    raise ExtensionError(
                        errors.DOCUMENT_UNAVAILABLE,
                        "The Amazon document could not be downloaded; request its URL again",
                    )
                return read_bounded(response, max_bytes)
        except HTTPError as exc:
            _read_error_body(exc)
            raise ExtensionError(
                errors.DOCUMENT_UNAVAILABLE,
                "The Amazon document URL is no longer valid; request the document again",
                http_status=int(exc.code),
            ) from None
        except PinnedPeerError as exc:
            raise ExtensionError(
                errors.BLOCKED_ADDRESS,
                "The document address is not reachable under the host policy",
            ) from exc
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            raise ExtensionError(
                errors.CONNECTION_FAILED, "The Amazon document could not be downloaded"
            ) from exc

    def upload_document(self, url: str, content: bytes, *, content_type: str, purpose: str) -> None:
        """Upload feed content to the presigned URL Amazon returned for it."""
        safe_url = assert_document_url(url)
        request = Request(
            safe_url,
            method="PUT",
            data=content,
            headers={"Content-Type": content_type, "User-Agent": USER_AGENT},
        )
        try:
            with self._opener(
                request, timeout_seconds=self._timeout_seconds, purpose=purpose
            ) as response:
                status = _status(response)
                read_bounded(response, MAX_ERROR_BODY_BYTES)
        except HTTPError as exc:
            _read_error_body(exc)
            raise ExtensionError(
                errors.DOCUMENT_UNAVAILABLE,
                "Amazon refused the document upload; create a new feed document and upload again",
                http_status=int(exc.code),
            ) from None
        except PinnedPeerError as exc:
            raise ExtensionError(
                errors.BLOCKED_ADDRESS, "The upload address is not reachable under the host policy"
            ) from exc
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            raise ExtensionError(
                errors.CONNECTION_FAILED,
                "The upload did not complete; create a new feed document and upload again",
            ) from exc
        if status >= 300:
            raise ExtensionError(
                errors.DOCUMENT_UNAVAILABLE,
                "Amazon refused the document upload; create a new feed document and upload again",
            )


class _Retry(Exception):
    """Internal: a failure the caller may retry after ``minimum_delay``."""

    def __init__(self, error: ExtensionError, minimum_delay: float | None = None) -> None:
        super().__init__(error.message)
        self.error = error
        self.minimum_delay = minimum_delay


def is_amazon_s3_host(host: str) -> bool:
    """Whether ``host`` is an Amazon S3 endpoint (path- or virtual-hosted style)."""
    return _S3_HOST_RE.fullmatch(host.lower()) is not None


def assert_document_url(url: Any) -> str:
    """Accept only an https presigned URL on an Amazon S3 host."""
    if not isinstance(url, str) or not url:
        raise ExtensionError(errors.DOCUMENT_UNAVAILABLE, "Amazon returned no document URL")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not is_amazon_s3_host(host):
        raise ExtensionError(
            errors.BLOCKED_ADDRESS, "Amazon returned a document URL outside Amazon S3"
        )
    try:
        return assert_safe_remote_https_url(url, purpose="Amazon document URL")
    except (PinnedPeerError, ValueError) as exc:
        raise ExtensionError(
            errors.BLOCKED_ADDRESS, "The document address is not reachable under the host policy"
        ) from exc


def _failure(status: int, raw: bytes, headers: Any, *, mutating: bool) -> Exception:
    code, message = errors.classify_status(status, mutating=mutating)
    amazon_errors = amazon_error_list(raw)
    if status == 400 and _mentions_next_token(amazon_errors):
        code = errors.CONTINUATION_TOKEN_INVALID
        message = "The continuation token is not valid any more; start the search again"
    retry_after = _retry_after(headers)
    ambiguous = code == errors.OUTCOME_UNKNOWN
    error = ExtensionError(
        code,
        message,
        failure_class="provider",
        retryable=status in _RETRYABLE_STATUSES and not ambiguous,
        retry_after_seconds=retry_after,
        definitely_no_external_effect=not ambiguous,
        external_effect_status="unknown" if ambiguous else "failed",
        http_status=status,
        amazon_errors=amazon_errors,
    )
    if status == 429 or (status in _RETRYABLE_STATUSES and not mutating):
        return _Retry(error, minimum_delay=retry_after or _rate_limit_delay(headers))
    return error


def amazon_error_list(raw: bytes) -> list[dict[str, str]]:
    """Amazon's ``errors`` array, reduced to bounded code/message/details strings."""
    try:
        payload = json.loads(raw.decode("utf-8", "strict")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    entries = payload.get("errors") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list):
        return []
    reduced: list[dict[str, str]] = []
    for entry in entries[:MAX_AMAZON_ERRORS]:
        if not isinstance(entry, Mapping):
            continue
        item = {
            key: str(entry.get(key))[:MAX_AMAZON_MESSAGE_LENGTH]
            for key in ("code", "message", "details")
            if entry.get(key) not in (None, "")
        }
        if item:
            reduced.append(item)
    return reduced


def _mentions_next_token(amazon_errors: Sequence[Mapping[str, str]]) -> bool:
    for entry in amazon_errors:
        text = f"{entry.get('message', '')} {entry.get('details', '')}".lower()
        if (
            "nexttoken" in text.replace(" ", "")
            or "next token" in text
            or "paginationtoken" in text
        ):
            return True
    return False


def read_bounded(response: Any, limit: int, *, mutating: bool = False) -> bytes:
    """Read at most ``limit`` bytes; an oversized answer to a mutation is unknown.

    The chunks go into a ``BytesIO`` whose ``getvalue()`` hands over its own
    buffer, so a 100 MB document is held once, not once more as a copy.
    """
    buffer = io.BytesIO()
    size = 0
    while True:
        chunk = response.read(min(CHUNK_BYTES, limit + 1 - size))
        if not chunk:
            break
        buffer.write(chunk)
        size += len(chunk)
        if size > limit:
            raise ExtensionError(
                errors.OUTCOME_UNKNOWN if mutating else errors.RESPONSE_TOO_LARGE,
                "The Amazon response exceeds its safe limit",
            )
    return buffer.getvalue()


def decode_json(raw: bytes, *, mutating: bool = False) -> Any:
    code = errors.OUTCOME_UNKNOWN if mutating else errors.INVALID_JSON_RESPONSE
    try:
        # Amounts are parsed as Decimal, never as binary floats, so a price or a
        # fee reaches the workflow exactly as Amazon sent it.
        return json.loads(raw.decode("utf-8", "strict"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtensionError(code, _INVALID_JSON) from exc


def _json_bytes(body: Any) -> bytes:
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BODY_BYTES:
        raise ExtensionError(errors.INVALID_PAYLOAD, "The request body exceeds its safe limit")
    return encoded


def _status(response: Any) -> int:
    return int(getattr(response, "status", 0) or getattr(response, "code", 0) or 0)


def _headers(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    wanted = ("x-amzn-ratelimit-limit", "x-amzn-requestid")
    found: dict[str, str] = {}
    items = headers.items() if hasattr(headers, "items") else []
    for key, value in items:
        lowered = str(key).lower()
        if lowered in wanted:
            found[lowered] = str(value)[:128]
    return found


def _read_error_body(exc: HTTPError) -> bytes:
    try:
        return exc.read(MAX_ERROR_BODY_BYTES) or b""
    except Exception:  # pragma: no cover - an unreadable body is simply absent
        return b""


def _retry_after(headers: Any) -> float | None:
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After")
    except (AttributeError, TypeError, ValueError):
        return None
    return parse_retry_after(raw)


def _rate_limit_delay(headers: Any) -> float | None:
    """One token interval from ``x-amzn-RateLimit-Limit`` (requests per second)."""
    if headers is None:
        return None
    try:
        raw = headers.get("x-amzn-RateLimit-Limit")
    except (AttributeError, TypeError, ValueError):
        return None
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    return min(1.0 / rate, BACKOFF_CAP_SECONDS)


def _transport_facts(*, mutating: bool) -> dict[str, Any]:
    return {
        "failure_class": "transient",
        "retryable": not mutating,
        "definitely_no_external_effect": not mutating,
        "external_effect_status": "unknown" if mutating else "failed",
    }


__all__ = [
    "MAX_ATTEMPTS",
    "MAX_RESPONSE_BYTES",
    "USER_AGENT",
    "ApiResponse",
    "SpApiTransport",
    "amazon_error_list",
    "assert_document_url",
    "decode_json",
    "encode_path",
    "encode_query",
    "is_amazon_s3_host",
    "read_bounded",
]
