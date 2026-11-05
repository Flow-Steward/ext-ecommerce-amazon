from __future__ import annotations

import gzip
import hashlib
import json
from typing import Any

import pytest
from conftest import SELLER_ID, UK_MARKETPLACE_ID

from runtime import documents
from runtime.errors import ExtensionError
from runtime.sections import feeds

UPLOAD_URL = "https://tortuga-prod-eu.s3-eu-west-1.amazonaws.com/feed-upload-1?X-Amz-Signature=upload-sig-secret"
RESULT_URL = "https://tortuga-prod-eu.s3-eu-west-1.amazonaws.com/feed-result-1?X-Amz-Signature=result-sig-secret"


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    # The container has no network; the SDK's DNS pinning is covered by the SDK itself.
    monkeypatch.setenv("FS_ALLOW_PRIVATE_REMOTE_URLS", "1")


def listings_feed(count: int = 2, *, seller_id: str = SELLER_ID) -> dict[str, Any]:
    return {
        "header": {"sellerId": seller_id, "version": "2.0", "issueLocale": "en_GB"},
        "messages": [
            {
                "messageId": index + 1,
                "sku": f"SKU-{index + 1}",
                "operationType": "PATCH",
                "productType": "PRODUCT",
                "patches": [
                    {"op": "replace", "path": "/attributes/fulfillment_availability", "value": []}
                ],
            }
            for index in range(count)
        ],
    }


def queue_upload(amazon, url: str = UPLOAD_URL) -> None:
    amazon.reply(
        "POST",
        "/feeds/2021-06-30/documents",
        {"feedDocumentId": "amzn1.tortuga.doc.1", "url": url},
        status=201,
    )
    amazon.reply("PUT", "/feed-upload-1", raw=b"")


def test_upload_from_content_creates_the_document_and_puts_the_content(amazon, run):
    queue_upload(amazon)
    content = listings_feed()
    response = run("upload_feed_document", {"feed_type": "JSON_LISTINGS_FEED", "content": content})
    assert response["ok"] is True, response
    result = response["result"]
    assert result["feed_document_id"] == "amzn1.tortuga.doc.1"
    assert result["message_count"] == 2
    create, upload = amazon.requests
    assert create.host == "sellingpartnerapi-eu.amazon.com"
    assert create.body == {"contentType": "application/json; charset=UTF-8"}
    assert upload.method == "PUT"
    assert upload.host == "tortuga-prod-eu.s3-eu-west-1.amazonaws.com"
    assert upload.headers["content-type"] == "application/json; charset=UTF-8"
    assert "x-amz-access-token" not in upload.headers
    assert upload.body == content
    assert result["size_bytes"] == len(
        json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()
    )
    dumped = json.dumps(response)
    assert "upload-sig-secret" not in dumped and "amazonaws.com" not in dumped


def test_upload_from_artifact_sends_the_file_byte_for_byte(amazon, run):
    queue_upload(amazon)
    body = json.dumps(listings_feed(3), indent=2).encode()
    seen: dict[str, Any] = {}

    def reader(payload, *, binding_key):
        seen["binding_key"] = binding_key
        return body

    response = run(
        "upload_feed_document",
        {"feed_type": "JSON_LISTINGS_FEED", "content_file": "artifact-handle-1"},
        artifact_reader=reader,
    )
    assert response["ok"] is True, response
    assert seen["binding_key"] == "amazon_feed_input"
    assert amazon.requests[1].body == listings_feed(3)
    assert response["result"]["sha256"] == hashlib.sha256(body).hexdigest()
    assert response["result"]["message_count"] == 3


def test_unreadable_artifact_is_a_clear_error(amazon, run):
    def reader(payload, *, binding_key):
        raise RuntimeError("grant missing")

    response = run(
        "upload_feed_document",
        {"feed_type": "JSON_LISTINGS_FEED", "content_file": "artifact-handle-1"},
        artifact_reader=reader,
    )
    assert response["error_code"] == "artifact_input_unavailable"
    assert amazon.requests == []


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"content": listings_feed(), "content_file": "artifact-handle-1"},
    ],
)
def test_exactly_one_content_source_is_required(amazon, run, extra):
    response = run(
        "upload_feed_document",
        {"feed_type": "JSON_LISTINGS_FEED", **extra},
        artifact_reader=lambda *a, **k: b"{}",
    )
    assert response["error_code"] == "invalid_payload"
    assert "exactly one" in response["error"]
    assert amazon.requests == []


def test_feed_for_another_seller_is_refused_before_anything_is_sent(amazon, run):
    response = run(
        "upload_feed_document",
        {"feed_type": "JSON_LISTINGS_FEED", "content": listings_feed(seller_id="A9OTHERSELLER")},
    )
    assert response["error_code"] == "invalid_payload"
    assert "Seller ID" in response["error"]
    assert amazon.requests == []


def test_too_many_messages_are_refused():
    with pytest.raises(ExtensionError) as caught:
        feeds.validate_listings_feed(listings_feed(feeds.MAX_LISTINGS_MESSAGES + 1), SELLER_ID)
    assert "25000" in caught.value.message
    assert (
        feeds.validate_listings_feed(listings_feed(feeds.MAX_LISTINGS_MESSAGES), SELLER_ID)
        == 25_000
    )


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda feed: feed["header"].update(version="1.0"), "version"),
        (lambda feed: feed.update(messages=[]), "non-empty"),
        (lambda feed: feed["messages"][0].pop("sku"), "sku is required"),
        (lambda feed: feed["messages"][0].pop("messageId"), "messageId"),
        (lambda feed: feed["messages"][1].update(messageId=1), "used twice"),
        (lambda feed: feed["messages"][0].update(operationType="UPSERT"), "operationType"),
        (lambda feed: feed["messages"][0].pop("patches"), "needs patches"),
        (lambda feed: feed["messages"][0].update(operationType="DELETE"), "must not have"),
    ],
)
def test_listings_feed_rules_are_checked_before_upload(amazon, run, mutate, expected):
    content = listings_feed()
    mutate(content)
    response = run("upload_feed_document", {"feed_type": "JSON_LISTINGS_FEED", "content": content})
    assert response["error_code"] == "invalid_payload", response
    assert expected in response["error"]
    assert amazon.requests == []


def test_upload_url_outside_amazon_s3_is_refused(amazon, run):
    amazon.reply(
        "POST",
        "/feeds/2021-06-30/documents",
        {"feedDocumentId": "doc-2", "url": "https://attacker.example.com/upload"},
    )
    response = run(
        "upload_feed_document", {"feed_type": "JSON_LISTINGS_FEED", "content": listings_feed()}
    )
    assert response["error_code"] == "blocked_address"
    assert len(amazon.requests) == 1
    assert "attacker.example.com" not in json.dumps(response)


def test_unsupported_feed_type_is_refused(amazon, run):
    response = run(
        "upload_feed_document", {"feed_type": "POST_PRODUCT_DATA", "content": listings_feed()}
    )
    assert response["error_code"] == "invalid_payload"
    assert amazon.requests == []


def test_create_feed_submits_for_the_connection_marketplace(amazon, run):
    amazon.reply("POST", "/feeds/2021-06-30/feeds", {"feedId": "50001019999"}, status=202)
    response = run(
        "create_feed",
        {"feed_type": "JSON_LISTINGS_FEED", "feed_document_id": "amzn1.tortuga.doc.1"},
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert result["feed_id"] == "50001019999"
    assert result["submission_status"] == "SUBMITTED"
    assert "not processed" in result["message"]
    assert result["external_effect_status"] == "succeeded"
    assert amazon.requests[0].body == {
        "feedType": "JSON_LISTINGS_FEED",
        "marketplaceIds": [UK_MARKETPLACE_ID],
        "inputFeedDocumentId": "amzn1.tortuga.doc.1",
    }


def test_create_feed_in_test_mode_sends_nothing(amazon, run):
    response = run(
        "create_feed",
        {"feed_type": "JSON_LISTINGS_FEED", "feed_document_id": "doc-1"},
        test_mode=True,
    )
    assert response["ok"] is True
    assert response["result"]["external_effect_status"] == "suppressed"
    assert "feed_id" not in response["result"]
    assert response["result"]["suppressed"] is True
    assert amazon.requests == []


def test_create_feed_without_external_effect_id_is_refused(amazon, run):
    response = run(
        "create_feed",
        {"feed_type": "JSON_LISTINGS_FEED", "feed_document_id": "doc-1"},
        effect_id=None,
    )
    assert response["error_code"] == "missing_idempotency_key"
    assert amazon.requests == []


def test_create_feed_server_error_is_an_unknown_outcome_and_not_repeated(amazon, run):
    amazon.reply(
        "POST", "/feeds/2021-06-30/feeds", {"errors": [{"code": "InternalFailure"}]}, status=500
    )
    response = run("create_feed", {"feed_type": "JSON_LISTINGS_FEED", "feed_document_id": "doc-1"})
    assert response["error_code"] == "outcome_unknown"
    assert response["external_effect_status"] == "unknown"
    assert len(amazon.requests) == 1


FEED_DONE = {
    "feedId": "50001",
    "feedType": "JSON_LISTINGS_FEED",
    "marketplaceIds": [UK_MARKETPLACE_ID],
    "createdTime": "2026-11-05T10:00:00Z",
    "processingStatus": "DONE",
    "processingStartTime": "2026-11-05T10:01:00Z",
    "processingEndTime": "2026-11-05T10:05:00Z",
    "resultFeedDocumentId": "amzn1.tortuga.result.1",
}


def test_get_feed_normalizes_status_and_timestamps(amazon, run):
    amazon.reply("GET", "/feeds/2021-06-30/feeds/50001", FEED_DONE)
    response = run("get_feed", {"feed_id": "50001"})
    result = response["result"]
    assert result["processing_status"] == "DONE"
    assert result["result_feed_document_id"] == "amzn1.tortuga.result.1"
    assert result["feed"]["processing_end_time"] == "2026-11-05T10:05:00Z"
    assert result["feed"]["amazon"] == FEED_DONE


def test_search_feeds_sends_filters_then_only_the_token(amazon, run):
    amazon.reply(
        "GET", "/feeds/2021-06-30/feeds", {"feeds": [FEED_DONE], "nextToken": "amz-feeds-2"}
    )
    inputs = {
        "feed_types": ["JSON_LISTINGS_FEED"],
        "processing_statuses": ["DONE", "FATAL"],
        "created_since": "2026-11-01T00:00:00Z",
        "page_size": 50,
    }
    first = run("search_feeds", inputs)
    assert first["ok"] is True, first
    query = amazon.requests[0].query
    assert query == {
        "feedTypes": "JSON_LISTINGS_FEED",
        "marketplaceIds": UK_MARKETPLACE_ID,
        "processingStatuses": "DONE,FATAL",
        "createdSince": "2026-11-01T00:00:00Z",
        "pageSize": "50",
    }
    assert first["result"]["feeds"][0]["feed_id"] == "50001"
    amazon.reply("GET", "/feeds/2021-06-30/feeds", {"feeds": []})
    second = run("search_feeds", {**inputs, "next_token": first["result"]["next_token"]})
    assert second["ok"] is True
    assert amazon.requests[1].query == {"nextToken": "amz-feeds-2"}
    assert second["result"]["next_token"] is None


def test_search_feeds_refuses_a_timestamp_without_zone(amazon, run):
    response = run(
        "search_feeds",
        {"feed_types": ["JSON_LISTINGS_FEED"], "created_since": "2026-11-01T00:00:00"},
    )
    assert response["error_code"] == "invalid_payload"
    assert amazon.requests == []


def test_feed_result_of_a_feed_in_progress_returns_the_status_only(amazon, run):
    amazon.reply(
        "GET",
        "/feeds/2021-06-30/feeds/50001",
        dict(FEED_DONE, processingStatus="IN_PROGRESS", resultFeedDocumentId=None),
    )
    response = run("get_feed_result", {"feed_id": "50001"})
    result = response["result"]
    assert result["processing_status"] == "IN_PROGRESS"
    assert result["report_available"] is False
    assert result["failed"] == [] and result["summary"] is None
    assert len(amazon.requests) == 1


PROCESSING_REPORT = {
    "header": {"sellerId": SELLER_ID, "version": "2.0", "feedId": "50001"},
    "issues": [
        {
            "messageId": 1,
            "sku": "SKU-1",
            "code": "90220",
            "message": "'condition_type' is required but not supplied.",
            "severity": "ERROR",
            "attributeName": "condition_type",
        },
        {
            "messageId": 1,
            "sku": "SKU-1",
            "code": "99010",
            "message": "Another problem",
            "severity": "WARNING",
        },
        {
            "messageId": 2,
            "sku": "SKU-2",
            "code": "18027",
            "message": "Price looks low",
            "severity": "WARNING",
        },
        {"code": "100", "message": "Feed-level note", "severity": "INFO"},
    ],
    "summary": {
        "errors": 1,
        "warnings": 2,
        "messagesProcessed": 3,
        "messagesAccepted": 2,
        "messagesInvalid": 1,
    },
}


def test_feed_result_parses_a_gzip_report_into_failed_and_accepted(amazon, run):
    raw = json.dumps(PROCESSING_REPORT).encode()
    amazon.reply("GET", "/feeds/2021-06-30/feeds/50001", FEED_DONE)
    amazon.reply(
        "GET",
        "/feeds/2021-06-30/documents/amzn1.tortuga.result.1",
        {
            "feedDocumentId": "amzn1.tortuga.result.1",
            "url": RESULT_URL,
            "compressionAlgorithm": "GZIP",
        },
    )
    amazon.reply("GET", "/feed-result-1", raw=gzip.compress(raw))
    written: dict[str, Any] = {}

    def writer(payload, body, *, binding_key, content_type):
        written.update(body=body, binding_key=binding_key, content_type=content_type)
        return {
            "artifact_handle": "art-report-1",
            "size_bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "content_type": content_type,
        }

    response = run(
        "get_feed_result", {"feed_id": "50001", "save_report_file": True}, artifact_writer=writer
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert result["report_available"] is True
    assert result["summary"]["messages_accepted"] == 2
    assert result["summary"]["messages_invalid"] == 1
    assert [entry["sku"] for entry in result["failed"]] == ["SKU-1"]
    assert [issue["severity"] for issue in result["failed"][0]["issues"]] == ["ERROR", "WARNING"]
    assert result["failed"][0]["issues"][0]["attribute_name"] == "condition_type"
    assert [entry["sku"] for entry in result["accepted_with_issues"]] == ["SKU-2"]
    assert result["feed_issues"][0]["message"] == "Feed-level note"
    assert written == {
        "body": raw,
        "binding_key": "amazon_feed_result",
        "content_type": "application/json",
    }
    assert result["report_file"]["artifact_handle"] == "art-report-1"
    download = amazon.requests[2]
    assert "x-amz-access-token" not in download.headers
    dumped = json.dumps(response)
    assert "result-sig-secret" not in dumped and "amazonaws.com" not in dumped


def test_feed_result_without_save_writes_no_file(amazon, run):
    amazon.reply("GET", "/feeds/2021-06-30/feeds/50001", FEED_DONE)
    amazon.reply(
        "GET",
        "/feeds/2021-06-30/documents/amzn1.tortuga.result.1",
        {"feedDocumentId": "x", "url": RESULT_URL},
    )
    amazon.reply("GET", "/feed-result-1", PROCESSING_REPORT)

    def writer(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("no file expected")

    response = run("get_feed_result", {"feed_id": "50001"}, artifact_writer=writer)
    assert response["ok"] is True, response
    assert response["result"]["report_file"] is None


def _big_report(messages: int) -> dict[str, Any]:
    return {
        "issues": [
            {
                "messageId": index + 1,
                "sku": f"SKU-{index + 1}",
                "code": "90220",
                "message": "The value for attribute item_name is not valid. " * 4,
                "severity": "ERROR" if index % 2 else "WARNING",
                "attributeName": "item_name",
            }
            for index in range(messages)
        ],
        "summary": {"errors": messages // 2, "warnings": messages - messages // 2},
    }


def _serve_report(amazon, raw: bytes) -> None:
    amazon.reply("GET", "/feeds/2021-06-30/feeds/50001", FEED_DONE)
    amazon.reply(
        "GET",
        "/feeds/2021-06-30/documents/amzn1.tortuga.result.1",
        {"feedDocumentId": "x", "url": RESULT_URL, "compressionAlgorithm": "GZIP"},
    )
    amazon.reply("GET", "/feed-result-1", raw=gzip.compress(raw))


def test_feed_result_issue_lists_share_one_byte_budget_and_fit_the_response(amazon, run):
    import main

    raw = json.dumps(_big_report(25_000)).encode()
    assert len(raw) > 3 * feeds.MAX_RETURNED_ISSUE_BYTES
    _serve_report(amazon, raw)
    written: list[bytes] = []

    def writer(payload, body, *, binding_key, content_type):
        written.append(body)
        return {"artifact_handle": "art-big", "size_bytes": len(body), "content_type": content_type}

    response = run(
        "get_feed_result", {"feed_id": "50001", "save_report_file": True}, artifact_writer=writer
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert result["issues_truncated"] is True
    assert result["failed"] and result["report_file"]["artifact_handle"] == "art-big"
    issue_bytes = sum(
        len(json.dumps(result[name], ensure_ascii=False, separators=(",", ":")).encode())
        for name in ("failed", "accepted_with_issues", "feed_issues")
    )
    assert issue_bytes <= feeds.MAX_RETURNED_ISSUE_BYTES + 16
    final, serialized = main._bounded_serialized_response(response)
    assert final["ok"] is True
    assert len(serialized) < main.MAX_SERIALIZED_RESPONSE_BYTES
    assert written == [raw]


def test_feed_result_small_report_is_not_truncated(amazon, run):
    _serve_report(amazon, json.dumps(_big_report(10)).encode())
    result = run("get_feed_result", {"feed_id": "50001"})["result"]
    assert result["issues_truncated"] is False
    assert len(result["failed"]) + len(result["accepted_with_issues"]) == 10


def test_feed_result_file_is_written_only_after_the_result_fits(amazon, run, monkeypatch):
    monkeypatch.setattr(feeds, "MAX_SERIALIZED_RESPONSE_BYTES", feeds._RESULT_HEADROOM_BYTES + 10)
    _serve_report(amazon, json.dumps(_big_report(10)).encode())

    def writer(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("no file may be written for a result that cannot be returned")

    response = run(
        "get_feed_result", {"feed_id": "50001", "save_report_file": True}, artifact_writer=writer
    )
    assert response["error_code"] == "response_too_large"


def test_result_budget_mirrors_the_entrypoint_cap():
    import main

    from runtime import values

    assert values.MAX_SERIALIZED_RESPONSE_BYTES == main.MAX_SERIALIZED_RESPONSE_BYTES


def test_feed_result_zip_bomb_is_refused(amazon, run, monkeypatch):
    monkeypatch.setattr(feeds, "MAX_RESULT_CONTENT_BYTES", 1024 * 1024)
    bomb = gzip.compress(b"\x00" * (8 * 1024 * 1024))
    assert len(bomb) < 64 * 1024
    amazon.reply("GET", "/feeds/2021-06-30/feeds/50001", FEED_DONE)
    amazon.reply(
        "GET",
        "/feeds/2021-06-30/documents/amzn1.tortuga.result.1",
        {"feedDocumentId": "x", "url": RESULT_URL, "compressionAlgorithm": "GZIP"},
    )
    amazon.reply("GET", "/feed-result-1", raw=bomb)
    response = run("get_feed_result", {"feed_id": "50001"})
    assert response["error_code"] == "response_too_large"


def test_feed_result_document_url_outside_amazon_s3_is_refused(amazon, run):
    amazon.reply("GET", "/feeds/2021-06-30/feeds/50001", FEED_DONE)
    amazon.reply(
        "GET",
        "/feeds/2021-06-30/documents/amzn1.tortuga.result.1",
        {"feedDocumentId": "x", "url": "https://amazonaws.com.evil.example/report"},
    )
    response = run("get_feed_result", {"feed_id": "50001"})
    assert response["error_code"] == "blocked_address"
    assert len(amazon.requests) == 2


def test_gunzip_bounded_handles_members_and_rejects_corruption():
    data = gzip.compress(b"abc") + gzip.compress(b"def")
    assert documents.gunzip_bounded(data, max_bytes=10) == b"abcdef"
    with pytest.raises(ExtensionError) as too_big:
        documents.gunzip_bounded(data, max_bytes=5)
    assert too_big.value.code == "response_too_large"
    with pytest.raises(ExtensionError) as corrupt:
        documents.gunzip_bounded(gzip.compress(b"abcdef" * 100)[:-12], max_bytes=10_000)
    assert corrupt.value.code == "document_unavailable"
    with pytest.raises(ExtensionError):
        documents.gunzip_bounded(b"not gzip at all", max_bytes=100)


def test_feed_rows_declare_their_effects():
    rows = {row.operation_id: row for row in feeds.OPERATIONS}
    assert rows["create_feed"].mutating is True
    assert rows["create_feed"].effect_kind == "amazon_feed_submission"
    assert rows["upload_feed_document"].mutating is False
    assert rows["upload_feed_document"].artifact_input.binding_key == "amazon_feed_input"
    assert rows["upload_feed_document"].artifact_input.max_size_bytes <= 10 * 1024 * 1024
    assert rows["get_feed_result"].artifact_output.binding_key == "amazon_feed_result"
