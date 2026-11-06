"""Stable, safe error vocabulary for the Amazon SP-API extension.

Every failure the runtime can produce maps to exactly one of these codes. No
message carries the access token, a restricted data token, a presigned URL or
unbounded upstream content. Amazon's own error ``code`` values are short closed
identifiers and are passed through in ``amazon_errors``; their free-text
``message`` is cut to a bounded length and never carries credentials, because
Amazon does not echo them.
"""

from __future__ import annotations

from collections.abc import Mapping

_MESSAGE_UNKNOWN_OUTCOME = (
    "The outcome of this request is unknown; read the current state before retrying"
)
_MESSAGE_AMAZON_FAILED = "Amazon could not complete the request"

INVALID_PAYLOAD = "invalid_payload"
INVALID_HOST_ENVELOPE = "invalid_host_envelope"
INVALID_CONFIGURATION = "invalid_configuration"
INVALID_CONNECTION = "invalid_connection"
#: The connection holds no usable grant any more: import a new refresh token.
REAUTHORIZATION_REQUIRED = "reauthorization_required"
UNSUPPORTED_OPERATION = "unsupported_operation"
UNSUPPORTED_FILTER = "unsupported_filter"
UNSUPPORTED_API_VERSION = "unsupported_api_version"
MISSING_IDEMPOTENCY_KEY = "missing_idempotency_key"
AUTHENTICATION_FAILED = "authentication_failed"
#: Amazon answered 403. That alone does not say which role is missing: the
#: application may lack the role, the seller may not have authorized it, the
#: seller ID may be wrong, or the marketplace may not be one the seller sells in.
ACCESS_DENIED = "access_denied"
NOT_FOUND = "not_found"
RATE_LIMITED = "rate_limited"
UPSTREAM_VALIDATION_FAILED = "upstream_validation_failed"
UPSTREAM_FAILURE = "upstream_failure"
INVALID_JSON_RESPONSE = "invalid_json_response"
RESPONSE_TOO_LARGE = "response_too_large"
CONTINUATION_TOKEN_INVALID = "continuation_token_invalid"  # nosec B105 - an error code, not a credential
DOCUMENT_UNAVAILABLE = "document_unavailable"
ARTIFACT_INPUT_UNAVAILABLE = "artifact_input_unavailable"
ARTIFACT_OUTPUT_UNAVAILABLE = "artifact_output_unavailable"
TIMEOUT = "timeout"
CONNECTION_FAILED = "connection_failed"
REDIRECT_REJECTED = "redirect_rejected"
BLOCKED_ADDRESS = "blocked_address"
#: A mutation was sent and no reliable answer came back.
OUTCOME_UNKNOWN = "outcome_unknown"
#: The connection's marketplace is not one where Amazon offers this API or program.
UNSUPPORTED_MARKETPLACE = "unsupported_marketplace"
#: A rate, a request token or an option Amazon offered has expired; request a new one.
OFFER_EXPIRED = "offer_expired"
#: The plan, shipment or listing is not in a state that allows this change.
INVALID_STATE = "invalid_state"
INTERNAL_ERROR = "internal_error"

SAFE_ERROR_CODES: tuple[str, ...] = (
    INVALID_PAYLOAD,
    INVALID_HOST_ENVELOPE,
    INVALID_CONFIGURATION,
    INVALID_CONNECTION,
    REAUTHORIZATION_REQUIRED,
    UNSUPPORTED_OPERATION,
    UNSUPPORTED_FILTER,
    UNSUPPORTED_API_VERSION,
    MISSING_IDEMPOTENCY_KEY,
    AUTHENTICATION_FAILED,
    ACCESS_DENIED,
    NOT_FOUND,
    RATE_LIMITED,
    UPSTREAM_VALIDATION_FAILED,
    UPSTREAM_FAILURE,
    INVALID_JSON_RESPONSE,
    RESPONSE_TOO_LARGE,
    CONTINUATION_TOKEN_INVALID,
    DOCUMENT_UNAVAILABLE,
    ARTIFACT_INPUT_UNAVAILABLE,
    ARTIFACT_OUTPUT_UNAVAILABLE,
    TIMEOUT,
    CONNECTION_FAILED,
    REDIRECT_REJECTED,
    BLOCKED_ADDRESS,
    OUTCOME_UNKNOWN,
    UNSUPPORTED_MARKETPLACE,
    OFFER_EXPIRED,
    INVALID_STATE,
    INTERNAL_ERROR,
)

#: Codes that leave a mutation's outcome unknown to the caller.
AMBIGUOUS_ERROR_CODES: frozenset[str] = frozenset({OUTCOME_UNKNOWN})

#: Codes any Amazon-contacting operation can raise.
TRANSPORT_ERROR_CODES: tuple[str, ...] = (
    INVALID_PAYLOAD,
    INVALID_CONFIGURATION,
    INVALID_CONNECTION,
    REAUTHORIZATION_REQUIRED,
    UNSUPPORTED_OPERATION,
    AUTHENTICATION_FAILED,
    ACCESS_DENIED,
    NOT_FOUND,
    RATE_LIMITED,
    UPSTREAM_VALIDATION_FAILED,
    OFFER_EXPIRED,
    INVALID_STATE,
    UPSTREAM_FAILURE,
    INVALID_JSON_RESPONSE,
    RESPONSE_TOO_LARGE,
    TIMEOUT,
    CONNECTION_FAILED,
    REDIRECT_REJECTED,
    BLOCKED_ADDRESS,
    INTERNAL_ERROR,
)

ACCESS_DENIED_HELP = (
    "Amazon refused this call (HTTP 403). Check, in this order: the application has the "
    "role this operation needs in the Solution Provider Portal; the seller authorized the "
    "application after that role was added (a new refresh token is needed then); the "
    "connection's Seller ID is the seller's merchant token; and the seller sells in the "
    "connection's marketplace."
)


class ExtensionError(Exception):
    """A failure already reduced to a safe code and a safe message."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        failure_class: str | None = None,
        retryable: bool | None = None,
        retry_after_seconds: float | None = None,
        definitely_no_external_effect: bool | None = None,
        external_effect_status: str | None = None,
        http_status: int | None = None,
        amazon_errors: list[dict[str, str]] | None = None,
        amazon_request_id: str | None = None,
        check: Mapping[str, object] | None = None,
    ) -> None:
        safe_code = code if code in SAFE_ERROR_CODES else INTERNAL_ERROR
        super().__init__(message)
        self.code = safe_code
        self.message = message
        self.failure_class = failure_class
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.definitely_no_external_effect = definitely_no_external_effect
        self.external_effect_status = external_effect_status
        self.http_status = http_status
        self.amazon_errors = amazon_errors or []
        #: Amazon's x-amzn-RequestId, for a support case; it identifies no person.
        self.amazon_request_id = amazon_request_id
        #: For an unknown outcome: the identifiers to read back and the method to read them with.
        self.check = dict(check) if check else None

    def retry_facts(self) -> dict[str, object]:
        """Project only closed, non-secret facts understood by Core."""
        facts: dict[str, object] = {}
        for key in (
            "failure_class",
            "retryable",
            "retry_after_seconds",
            "definitely_no_external_effect",
            "external_effect_status",
            "http_status",
        ):
            value = getattr(self, key)
            if value is not None:
                facts[key] = value
        if self.failure_class in {"provider", "transient"}:
            facts["provider_error_code"] = self.code
        return facts


def error_response(
    code: str,
    message: str,
    *,
    retry_facts: Mapping[str, object] | None = None,
    amazon_errors: list[dict[str, str]] | None = None,
    amazon_request_id: str | None = None,
    check: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the failing runtime envelope for one safe error."""
    safe_code = code if code in SAFE_ERROR_CODES else INTERNAL_ERROR
    result: dict[str, object] = {}
    if amazon_errors:
        result["amazon_errors"] = amazon_errors
    if amazon_request_id:
        result["amazon_request_id"] = amazon_request_id
    if check:
        result["check"] = dict(check)
    response: dict[str, object] = {
        "ok": False,
        "result": result,
        "error_code": safe_code,
        "error": message,
        "errors": [{"code": safe_code, "message": message}],
    }
    if safe_code in AMBIGUOUS_ERROR_CODES:
        response["external_effect_status"] = "unknown"
        response["definitely_no_external_effect"] = False
    if retry_facts:
        response.update(retry_facts)
    return response


def classify_status(status: int, *, mutating: bool) -> tuple[str, str]:
    """Map one SP-API HTTP status onto a safe code and a safe message.

    A 5xx or a timeout after a mutation was sent leaves the outcome unknown, so it
    is reported as such rather than as a plain failure.
    """
    if 300 <= status < 400:
        return REDIRECT_REJECTED, "Amazon redirected the request and it was not followed"
    if status == 401:
        return AUTHENTICATION_FAILED, "Amazon rejected this connection's access token"
    if status == 403:
        return ACCESS_DENIED, ACCESS_DENIED_HELP
    if status == 404:
        return NOT_FOUND, "Amazon did not find the requested resource"
    if status == 429:
        return RATE_LIMITED, "Amazon is throttling this operation; retry later from the workflow"
    if status in {400, 409, 413, 415, 422}:
        return UPSTREAM_VALIDATION_FAILED, "Amazon rejected the request as invalid"
    if status >= 500 or status == 408:
        if mutating:
            return OUTCOME_UNKNOWN, _MESSAGE_UNKNOWN_OUTCOME
        return UPSTREAM_FAILURE, _MESSAGE_AMAZON_FAILED
    return UPSTREAM_FAILURE, _MESSAGE_AMAZON_FAILED


__all__ = [
    "ACCESS_DENIED_HELP",
    "AMBIGUOUS_ERROR_CODES",
    "SAFE_ERROR_CODES",
    "TRANSPORT_ERROR_CODES",
    "ExtensionError",
    "classify_status",
    "error_response",
]
