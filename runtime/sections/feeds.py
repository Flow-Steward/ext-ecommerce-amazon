"""Feeds API 2021-06-30: upload a feed document, submit it, read the result.

A feed goes through four steps, each its own operation so a workflow can wait
between them on its own schedule (nothing here polls):

1. ``upload_feed_document`` validates the content and uploads it to Amazon.
   Nothing changes on Amazon's catalog yet.
2. ``create_feed`` submits the uploaded document for processing. This is the
   external effect: Amazon starts changing listings.
3. ``get_feed`` / ``search_feeds`` read the processing status.
4. ``get_feed_result`` downloads and parses Amazon's processing report once the
   feed is finished.

Only ``JSON_LISTINGS_FEED`` is accepted. Amazon stopped accepting XML and flat
file listings feeds on 2025-07-31; another JSON feed type can be added to
``FEED_TYPES`` once its validation exists. The JSON_LISTINGS_FEED rules checked
before upload are those of Amazon's ``listings-feed-schema-v2.json``: header
``sellerId`` and ``version`` "2.0", 1 to 25,000 messages, each with an integer
``messageId``, a ``sku`` and an ``operationType`` (UPDATE, PARTIAL_UPDATE, PATCH,
DELETE) with the fields that operation requires.

The presigned document URLs Amazon returns never leave this extension.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .. import documents, errors, pagination, validation
from ..errors import ExtensionError
from ..registry import (
    ARRAY_OF_OBJECTS,
    BOOLEAN,
    CONNECTION_REF,
    INTEGER,
    NEXT_TOKEN,
    NEXT_TOKEN_OUTPUT,
    NULLABLE_OBJECT,
    NULLABLE_TEXT,
    OBJECT,
    TEXT,
    ArtifactBinding,
    Context,
    Operation,
    boolean,
    enum,
    enum_list,
    integer,
    json_object,
    out,
    text,
    timestamp,
)
from ..values import MAX_SERIALIZED_RESPONSE_BYTES, as_list, dig, serialized_size

API_VERSION = "2021-06-30"

FEEDS = "/feeds/2021-06-30/feeds"
FEED = "/feeds/2021-06-30/feeds/{feedId}"
DOCUMENTS = "/feeds/2021-06-30/documents"
DOCUMENT = "/feeds/2021-06-30/documents/{feedDocumentId}"

JSON_LISTINGS_FEED = "JSON_LISTINGS_FEED"
#: The closed allowlist: feed type → the content type its document is uploaded with.
FEED_TYPES: dict[str, str] = {JSON_LISTINGS_FEED: "application/json; charset=UTF-8"}
FEED_TYPE_NAMES = tuple(FEED_TYPES)

PROCESSING_STATUSES = ("CANCELLED", "DONE", "FATAL", "IN_PROGRESS", "IN_QUEUE")

#: Amazon's listings-feed-schema-v2: messages minItems 1, maxItems 25000.
MAX_LISTINGS_MESSAGES = 25_000
MAX_MESSAGE_ID = 2_147_483_647
OPERATION_TYPES = ("UPDATE", "PARTIAL_UPDATE", "PATCH", "DELETE")
#: Per operationType (listings-feed-schema-v2): fields that must be present / absent.
_REQUIRED_BY_OPERATION: dict[str, tuple[str, ...]] = {
    "UPDATE": ("productType", "attributes"),
    "PARTIAL_UPDATE": ("productType", "attributes"),
    "PATCH": ("productType", "patches"),
    "DELETE": (),
}
_FORBIDDEN_BY_OPERATION: dict[str, tuple[str, ...]] = {
    "UPDATE": ("patches",),
    "PARTIAL_UPDATE": ("requirements", "patches"),
    "PATCH": ("requirements", "attributes"),
    "DELETE": ("requirements", "attributes", "productType", "patches"),
}
_MAX_REPORTED_PROBLEMS = 20

MAX_FEED_BYTES = 10 * 1024 * 1024
MAX_RESULT_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_RESULT_CONTENT_BYTES = 64 * 1024 * 1024
#: Serialized size of all returned issue lists together. The step result must
#: stay under the entrypoint's response cap (4.5 MB); the full report can be
#: saved as a file.
MAX_RETURNED_ISSUE_BYTES = 2_621_440  # 2.5 MiB
#: Room kept for the rest of the result (summary, report_file facts, envelope).
_RESULT_HEADROOM_BYTES = 64 * 1024

FEED_INPUT = ArtifactBinding(
    binding_key="amazon_feed_input",
    field="content_file",
    content_type="application/json",
    max_size_bytes=MAX_FEED_BYTES,
    required=False,
)
FEED_RESULT_OUTPUT = ArtifactBinding(
    binding_key="amazon_feed_result",
    field="report_file",
    filename="amazon-feed-processing-report.json",
    content_type="application/json",
    max_size_bytes=MAX_RESULT_CONTENT_BYTES,
)

_ROLE_LISTINGS = "Product Listing"
_FEED_TYPE_INPUT = enum(
    "Feed type", "The Amazon feed type. Only JSON_LISTINGS_FEED is supported.", FEED_TYPE_NAMES
)
_FEED_ID_INPUT = text("Feed ID", "The feed_id create_feed returned.", max_length=64)

SUBMITTED_MESSAGE = (
    "Submitted to Amazon, not processed yet. Read the outcome later with get_feed and get_feed_result; "
    "processing can take minutes and, under load, several hours."
)


def _feed_type(context: Context) -> str:
    return str(context.input.get("feed_type") or JSON_LISTINGS_FEED)


# -- validation of JSON_LISTINGS_FEED content ------------------------------


def validate_listings_feed(content: Any, seller_id: str) -> int:
    """Check one JSON_LISTINGS_FEED document; return its message count.

    The checks are Amazon's own schema rules plus two of this extension's: the
    header seller must be the connection's seller, and message IDs must be unique
    so that each issue in the processing report points at exactly one message.
    """
    if not isinstance(content, Mapping):
        raise _feed_invalid("the feed must be a JSON object with header and messages")
    header = content.get("header")
    if not isinstance(header, Mapping):
        raise _feed_invalid("header is required")
    if header.get("sellerId") != seller_id:
        raise ExtensionError(
            errors.INVALID_PAYLOAD,
            "header.sellerId must be this connection's Seller ID; a feed cannot be sent for another seller",
        )
    if header.get("version") != "2.0":
        raise _feed_invalid('header.version must be "2.0"')
    messages = content.get("messages")
    if not isinstance(messages, list) or not messages:
        raise _feed_invalid("messages must be a non-empty list")
    if len(messages) > MAX_LISTINGS_MESSAGES:
        raise _feed_invalid(f"Amazon accepts at most {MAX_LISTINGS_MESSAGES} messages in one feed")
    problems: list[str] = []
    seen: set[int] = set()
    for index, message in enumerate(messages):
        problems.extend(_message_problems(message, index, seen))
        if len(problems) >= _MAX_REPORTED_PROBLEMS:
            break
    if problems:
        raise _feed_invalid("; ".join(problems[:_MAX_REPORTED_PROBLEMS]))
    return len(messages)


def _message_problems(message: Any, index: int, seen: set[int]) -> list[str]:
    where = f"messages[{index}]"
    if not isinstance(message, Mapping):
        return [f"{where} must be an object"]
    problems: list[str] = []
    message_id = message.get("messageId")
    if (
        isinstance(message_id, bool)
        or not isinstance(message_id, int)
        or not 1 <= message_id <= MAX_MESSAGE_ID
    ):
        problems.append(f"{where}.messageId must be an integer from 1 to {MAX_MESSAGE_ID}")
    elif message_id in seen:
        problems.append(f"{where}.messageId {message_id} is used twice")
    else:
        seen.add(message_id)
    sku = message.get("sku")
    if not isinstance(sku, str) or not sku:
        problems.append(f"{where}.sku is required")
    operation_type = message.get("operationType")
    if operation_type not in OPERATION_TYPES:
        problems.append(f"{where}.operationType must be one of {', '.join(OPERATION_TYPES)}")
        return problems
    missing = [name for name in _REQUIRED_BY_OPERATION[operation_type] if name not in message]
    if missing:
        problems.append(f"{where} ({operation_type}) needs {', '.join(missing)}")
    forbidden = [name for name in _FORBIDDEN_BY_OPERATION[operation_type] if name in message]
    if forbidden:
        problems.append(f"{where} ({operation_type}) must not have {', '.join(forbidden)}")
    return problems


def _feed_invalid(detail: str) -> ExtensionError:
    return ExtensionError(errors.INVALID_PAYLOAD, f"The listings feed is not valid: {detail}")


# -- upload ----------------------------------------------------------------


def _feed_bytes(context: Context) -> tuple[bytes, Any]:
    """The bytes to upload and the parsed document, from exactly one source."""
    has_content = context.input.get("content") is not None
    has_file = context.input.get("content_file") is not None
    if has_content == has_file:
        raise ExtensionError(errors.INVALID_PAYLOAD, "Give exactly one of content or content_file")
    if has_content:
        content = context.input["content"]
        body = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_FEED_BYTES:
            raise ExtensionError(
                errors.INVALID_PAYLOAD, f"The feed is larger than {MAX_FEED_BYTES} bytes"
            )
        return body, content
    body = documents.read_artifact(context, FEED_INPUT)
    try:
        # The file is uploaded byte for byte as it was read; parsing is only for checks.
        parsed = json.loads(body.decode("utf-8-sig"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtensionError(
            errors.INVALID_PAYLOAD, "content_file is not a UTF-8 JSON document"
        ) from exc
    return body, parsed


def _upload_feed_document(context: Context) -> dict[str, Any]:
    """createFeedDocument and the upload of the content, in one operation.

    The upload URL Amazon returns is a time-limited credential that lets its
    holder write the feed document. Returning it to the workflow would store it
    in run history, where anyone who can read the run could replace the feed
    content before it is submitted. The URL is therefore used here, immediately,
    and only the feedDocumentId is returned. Nothing changes on Amazon until
    create_feed submits the document, so this operation is not an external effect.
    """
    feed_type = _feed_type(context)
    content_type = FEED_TYPES[feed_type]
    body, parsed = _feed_bytes(context)
    message_count = validate_listings_feed(parsed, context.connection.seller_id)
    response = context.transport.call(
        "POST",
        DOCUMENTS,
        body={"contentType": content_type},
        # Creating an empty feed document changes nothing; a repeat is harmless.
        mutating=False,
        purpose="Amazon create feed document",
    )
    document_id = dig(response.body, "feedDocumentId")
    if not isinstance(document_id, str) or not document_id:
        raise ExtensionError(errors.UPSTREAM_FAILURE, "Amazon returned no feed document ID")
    context.transport.upload_document(
        dig(response.body, "url"),
        body,
        content_type=content_type,
        purpose="Amazon feed document upload",
    )
    return {
        "feed_document_id": document_id,
        "feed_type": feed_type,
        "content_type": content_type,
        "size_bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "message_count": message_count,
    }


# -- create / read ---------------------------------------------------------


def _create_feed(context: Context) -> dict[str, Any]:
    feed_type = _feed_type(context)
    response = context.transport.call(
        "POST",
        FEEDS,
        body={
            "feedType": feed_type,
            "marketplaceIds": [context.connection.marketplace_id],
            "inputFeedDocumentId": str(context.input["feed_document_id"]),
        },
        mutating=True,
        purpose="Amazon create feed",
    )
    feed_id = dig(response.body, "feedId")
    if not isinstance(feed_id, str) or not feed_id:
        raise ExtensionError(
            errors.OUTCOME_UNKNOWN,
            "Amazon answered without a feed ID; search feeds before submitting again",
        )
    return {
        "feed_id": feed_id,
        "feed_type": feed_type,
        "submission_status": "SUBMITTED",
        "message": SUBMITTED_MESSAGE,
    }


def normalize_feed(raw: Any) -> dict[str, Any]:
    return {
        "feed_id": dig(raw, "feedId"),
        "feed_type": dig(raw, "feedType"),
        "processing_status": dig(raw, "processingStatus"),
        "marketplace_ids": as_list(dig(raw, "marketplaceIds")),
        "created_time": dig(raw, "createdTime"),
        "processing_start_time": dig(raw, "processingStartTime"),
        "processing_end_time": dig(raw, "processingEndTime"),
        "result_feed_document_id": dig(raw, "resultFeedDocumentId"),
        "amazon": raw,
    }


def _read_feed(context: Context) -> dict[str, Any]:
    response = context.transport.call(
        "GET",
        FEED,
        path={"feedId": str(context.input["feed_id"])},
        mutating=False,
        purpose="Amazon get feed",
    )
    if not isinstance(response.body, Mapping) or not response.body.get("feedId"):
        raise ExtensionError(errors.NOT_FOUND, "Amazon returned no feed for that ID")
    return normalize_feed(response.body)


def _get_feed(context: Context) -> dict[str, Any]:
    feed = _read_feed(context)
    return {
        "feed": feed,
        "processing_status": feed["processing_status"],
        "result_feed_document_id": feed["result_feed_document_id"],
    }


def _search_feeds(context: Context) -> dict[str, Any]:
    data = context.input
    for name in ("created_since", "created_until"):
        validation.timestamp(data.get(name), name)
    token = pagination.unwrap(context, API_VERSION)
    if token is not None:
        # Amazon: nextToken must be the only parameter. The wrapped token already
        # proves that the filters are those of the first page.
        query: dict[str, Any] = {"nextToken": token}
    else:
        query = {
            "feedTypes": data["feed_types"],
            "marketplaceIds": [context.connection.marketplace_id],
            "processingStatuses": data.get("processing_statuses"),
            "createdSince": data.get("created_since"),
            "createdUntil": data.get("created_until"),
            "pageSize": data.get("page_size"),
        }
    response = context.transport.call(
        "GET", FEEDS, query=query, mutating=False, purpose="Amazon search feeds"
    )
    feeds = as_list(dig(response.body, "feeds"))
    return {
        "feeds": [normalize_feed(item) for item in feeds],
        "count": len(feeds),
        "next_token": pagination.wrap(context, API_VERSION, dig(response.body, "nextToken")),
    }


# -- processing report -----------------------------------------------------


def parse_processing_report(report: Any) -> dict[str, Any]:
    """Amazon's JSON_LISTINGS_FEED processing report, split by outcome.

    Amazon lists only messages that have issues. A message with at least one
    ERROR issue was not applied (``failed``); a message with only WARNING/INFO
    issues was applied (``accepted_with_issues``). Messages without any issue do
    not appear in the report at all: they are counted in
    ``summary.messages_accepted``, and a workflow that needs their SKUs compares
    the messageIds it submitted with ``failed``.
    """
    if not isinstance(report, Mapping):
        raise ExtensionError(
            errors.INVALID_JSON_RESPONSE, "The processing report is not a JSON object"
        )
    raw_summary = report.get("summary")
    summary: Mapping[str, Any] = raw_summary if isinstance(raw_summary, Mapping) else {}
    issues = [_issue(raw) for raw in as_list(report.get("issues")) if isinstance(raw, Mapping)]
    by_message: dict[Any, dict[str, Any]] = {}
    feed_issues: list[dict[str, Any]] = []
    for issue in issues:
        if issue["message_id"] is None and issue["sku"] is None:
            feed_issues.append(issue)
            continue
        key = ("m", issue["message_id"]) if issue["message_id"] is not None else ("s", issue["sku"])
        entry = by_message.setdefault(
            key, {"message_id": issue["message_id"], "sku": issue["sku"], "issues": []}
        )
        if entry["sku"] is None:
            entry["sku"] = issue["sku"]
        entry["issues"].append(issue)
    failed: list[dict[str, Any]] = []
    accepted_with_issues: list[dict[str, Any]] = []
    for entry in by_message.values():
        has_error = any(item["severity"] == "ERROR" for item in entry["issues"])
        (failed if has_error else accepted_with_issues).append(entry)
    item_summary = summary.get("itemSummary")
    # Feed-level problems first, then rejected messages, then accepted ones with
    # warnings: when the budget runs out, the least important entries are cut.
    (kept_feed, kept_failed, kept_accepted), truncated = _within_budget(
        (feed_issues, failed, accepted_with_issues), MAX_RETURNED_ISSUE_BYTES
    )
    return {
        "summary": {
            "errors": summary.get("errors"),
            "warnings": summary.get("warnings"),
            "messages_processed": summary.get("messagesProcessed"),
            "messages_accepted": summary.get("messagesAccepted"),
            "messages_invalid": summary.get("messagesInvalid"),
            "item_summary": dict(item_summary) if isinstance(item_summary, Mapping) else None,
        },
        "failed": kept_failed,
        "accepted_with_issues": kept_accepted,
        "feed_issues": kept_feed,
        "issues_truncated": truncated,
    }


def _within_budget(
    lists: tuple[list[dict[str, Any]], ...], budget: int
) -> tuple[tuple[list[dict[str, Any]], ...], bool]:
    """The longest prefix of ``lists`` (taken in order) whose JSON fits ``budget`` bytes.

    Once one entry does not fit, nothing after it is added, so what is returned
    is always "everything up to a point" in priority order, never a sample.
    """
    remaining = budget
    kept: list[list[dict[str, Any]]] = []
    truncated = False
    for entries in lists:
        taken: list[dict[str, Any]] = []
        for entry in entries:
            if truncated:
                break
            cost = serialized_size(entry) + 1  # the separating comma
            if cost > remaining:
                truncated = True
                break
            remaining -= cost
            taken.append(entry)
        kept.append(taken)
    return tuple(kept), truncated


def _issue(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "message_id": raw.get("messageId"),
        "sku": raw.get("sku"),
        "code": raw.get("code"),
        "message": raw.get("message"),
        "severity": raw.get("severity"),
        "attribute_name": raw.get("attributeName"),
    }


def _get_feed_result(context: Context) -> dict[str, Any]:
    """getFeed, then the processing report when Amazon has one; never waits.

    A feed that is still IN_QUEUE or IN_PROGRESS (or was CANCELLED) returns its
    status with ``report_available`` false. A DONE feed, and a FATAL one that has
    a report explaining why, is downloaded, decompressed within a cap and parsed.
    """
    feed = _read_feed(context)
    status = feed["processing_status"]
    document_id = feed["result_feed_document_id"]
    result: dict[str, Any] = {
        "feed_id": feed["feed_id"],
        "processing_status": status,
        "report_available": False,
        "summary": None,
        "failed": [],
        "accepted_with_issues": [],
        "feed_issues": [],
        "issues_truncated": False,
        "report_file": None,
    }
    if status not in ("DONE", "FATAL") or not isinstance(document_id, str) or not document_id:
        return result
    response = context.transport.call(
        "GET",
        DOCUMENT,
        path={"feedDocumentId": document_id},
        mutating=False,
        purpose="Amazon get feed result document",
    )
    content, _compressed = documents.fetch_document(
        context,
        response.body,
        max_download_bytes=MAX_RESULT_DOWNLOAD_BYTES,
        max_content_bytes=MAX_RESULT_CONTENT_BYTES,
        purpose="Amazon feed processing report download",
    )
    try:
        report = json.loads(content.decode("utf-8-sig"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtensionError(
            errors.INVALID_JSON_RESPONSE, "The processing report is not valid JSON"
        ) from exc
    result.update(parse_processing_report(report), report_available=True)
    # The file is written only once the result is known to fit the response cap:
    # a stored file whose step then fails as response_too_large would be orphaned.
    if serialized_size(result) + _RESULT_HEADROOM_BYTES > MAX_SERIALIZED_RESPONSE_BYTES:
        raise ExtensionError(
            errors.RESPONSE_TOO_LARGE,
            "The processing report summary is larger than one step result can carry",
        )
    if context.input.get("save_report_file"):
        result["report_file"] = documents.write_artifact(context, FEED_RESULT_OUTPUT, content)
    return result


# -- rows ------------------------------------------------------------------

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        operation_id="upload_feed_document",
        display_name="Upload a feed document",
        description=(
            "Validates a JSON_LISTINGS_FEED (header.sellerId must be the connection's seller, "
            "version 2.0, 1 to 25,000 messages each with messageId, sku and operationType) and "
            "uploads it to Amazon. Returns the feed_document_id for create_feed; Amazon keeps it "
            "for two days. Nothing changes on Amazon until create_feed."
        ),
        section="feeds",
        properties={
            "connection_ref": CONNECTION_REF,
            "feed_type": _FEED_TYPE_INPUT,
            "content": json_object(
                "Feed content",
                "The JSON_LISTINGS_FEED document as a JSON object (header and messages). "
                "Give this or content_file, not both.",
            ),
            "content_file": {
                "type": ["string", "object"],
                "title": "Feed file",
                "description": "A JSON feed file from an earlier step, up to 10 MB. Give this or content, not both.",
            },
        },
        required=("connection_ref", "feed_type"),
        outputs=(
            out("feed_document_id", TEXT),
            out("feed_type", TEXT),
            out("content_type", TEXT),
            out("size_bytes", INTEGER),
            out("sha256", TEXT),
            out("message_count", INTEGER),
        ),
        handler=_upload_feed_document,
        amazon_calls=(
            "Feeds 2021-06-30 createFeedDocument",
            "Upload to the presigned Amazon S3 URL",
        ),
        roles=(_ROLE_LISTINGS,),
        extra_error_codes=(errors.DOCUMENT_UNAVAILABLE, errors.ARTIFACT_INPUT_UNAVAILABLE),
        artifact_input=FEED_INPUT,
    ),
    Operation(
        operation_id="create_feed",
        display_name="Submit a feed",
        description=(
            "Submits an uploaded feed document for processing in the connection's marketplace. "
            "Amazon processes it asynchronously: the result means submitted, not processed. "
            "Check get_feed and get_feed_result later."
        ),
        section="feeds",
        properties={
            "connection_ref": CONNECTION_REF,
            "feed_type": _FEED_TYPE_INPUT,
            "feed_document_id": text(
                "Feed document ID",
                "The feed_document_id upload_feed_document returned.",
                max_length=256,
            ),
        },
        required=("connection_ref", "feed_type", "feed_document_id"),
        outputs=(
            out("feed_id", TEXT),
            out("feed_type", TEXT),
            out("submission_status", TEXT),
            out("message", TEXT),
        ),
        handler=_create_feed,
        amazon_calls=("Feeds 2021-06-30 createFeed",),
        roles=(_ROLE_LISTINGS,),
        kind="action",
        mutating=True,
        effect_kind="amazon_feed_submission",
    ),
    Operation(
        operation_id="get_feed",
        display_name="Get feed status",
        description="Returns one feed's processing status, timestamps and result document ID.",
        section="feeds",
        properties={"connection_ref": CONNECTION_REF, "feed_id": _FEED_ID_INPUT},
        required=("connection_ref", "feed_id"),
        outputs=(
            out("feed", OBJECT),
            out("processing_status", TEXT),
            out("result_feed_document_id", NULLABLE_TEXT),
        ),
        handler=_get_feed,
        amazon_calls=("Feeds 2021-06-30 getFeed",),
        roles=(_ROLE_LISTINGS,),
    ),
    Operation(
        operation_id="search_feeds",
        display_name="Find feeds",
        description=(
            "Returns one page of the connection's feeds of the given types in its marketplace. "
            "Amazon keeps feeds for 90 days."
        ),
        section="feeds",
        properties={
            "connection_ref": CONNECTION_REF,
            "feed_types": enum_list("Feed types", "Feed types to find.", FEED_TYPE_NAMES),
            "processing_statuses": enum_list(
                "Processing statuses", "Only feeds in these statuses.", PROCESSING_STATUSES
            ),
            "created_since": timestamp(
                "Created since", "Feeds created at or after this time (default: 90 days ago)."
            ),
            "created_until": timestamp(
                "Created until", "Feeds created before this time (default: now)."
            ),
            "page_size": integer("Page size", "Feeds per page, 1 to 100.", minimum=1, maximum=100),
            "next_token": NEXT_TOKEN,
        },
        required=("connection_ref", "feed_types"),
        outputs=(out("feeds", ARRAY_OF_OBJECTS), out("count", INTEGER), NEXT_TOKEN_OUTPUT),
        handler=_search_feeds,
        amazon_calls=("Feeds 2021-06-30 getFeeds",),
        roles=(_ROLE_LISTINGS,),
        extra_error_codes=(errors.CONTINUATION_TOKEN_INVALID,),
    ),
    Operation(
        operation_id="get_feed_result",
        display_name="Get feed result",
        description=(
            "Reads a feed and, once Amazon has finished it, its processing report: summary counts, "
            "messages that failed (at least one ERROR) and messages applied with warnings, each with "
            "messageId, sku and Amazon's issues. Messages without issues appear only in the summary "
            "count. The issue lists together are capped at 2.5 MB (feed-level issues first, then failed, "
            "then accepted with issues); issues_truncated is true when entries were left out, and "
            "save_report_file keeps the full report. While the feed is still processing it returns the "
            "status alone; it never waits."
        ),
        section="feeds",
        properties={
            "connection_ref": CONNECTION_REF,
            "feed_id": _FEED_ID_INPUT,
            "save_report_file": boolean(
                "Save the report as a file",
                "Also store Amazon's full processing report as a JSON file.",
            ),
        },
        required=("connection_ref", "feed_id"),
        outputs=(
            out("feed_id", TEXT),
            out("processing_status", TEXT),
            out("report_available", BOOLEAN),
            out("summary", NULLABLE_OBJECT),
            out("failed", ARRAY_OF_OBJECTS),
            out("accepted_with_issues", ARRAY_OF_OBJECTS),
            out("feed_issues", ARRAY_OF_OBJECTS),
            out("issues_truncated", BOOLEAN),
            out("report_file", NULLABLE_OBJECT),
        ),
        handler=_get_feed_result,
        amazon_calls=(
            "Feeds 2021-06-30 getFeed",
            "Feeds 2021-06-30 getFeedDocument + download from the presigned Amazon S3 URL",
        ),
        roles=(_ROLE_LISTINGS,),
        extra_error_codes=(errors.DOCUMENT_UNAVAILABLE, errors.ARTIFACT_OUTPUT_UNAVAILABLE),
        artifact_output=FEED_RESULT_OUTPUT,
    ),
)
