from __future__ import annotations

import gzip
import hashlib
import json
from typing import Any

import pytest
from conftest import UK_MARKETPLACE_ID

from runtime.sections import reports

REPORT_URL = "https://tortuga-prod-eu.s3-eu-west-1.amazonaws.com/report-doc-1?X-Amz-Signature=report-sig-secret"
SETTLEMENT = "GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2"
LISTINGS = "GET_MERCHANT_LISTINGS_ALL_DATA"
ORDERS = "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL"


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    monkeypatch.setenv("FS_ALLOW_PRIVATE_REMOTE_URLS", "1")


class Writer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, payload, body, *, binding_key, content_type):
        self.calls.append({"body": body, "binding_key": binding_key, "content_type": content_type})
        return {
            "artifact_id": "a-1",
            "artifact_handle": "artifact-handle-report-1",
            "size_bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "content_type": content_type,
        }


def report(status: str = "DONE", report_type: str = LISTINGS, **extra: Any) -> dict[str, Any]:
    body = {
        "reportId": "REP-1",
        "reportType": report_type,
        "processingStatus": status,
        "marketplaceIds": [UK_MARKETPLACE_ID],
        "createdTime": "2026-11-05T10:00:00Z",
        "dataStartTime": "2026-11-01T00:00:00Z",
        "dataEndTime": "2026-11-05T00:00:00Z",
    }
    if status == "DONE":
        body["reportDocumentId"] = "amzn1.spdoc.1"
    body.update(extra)
    return body


def test_allowlist_marks_settlement_as_not_creatable_and_uses_one_content_type():
    assert reports.REPORT_TYPES[SETTLEMENT].creatable is False
    assert SETTLEMENT not in reports.CREATABLE_REPORT_TYPE_NAMES
    assert reports.REPORT_TYPES[ORDERS].contains_pii is True
    assert reports.REPORT_TYPES[LISTINGS].contains_pii is False
    # The platform's upload grant is issued for the binding's content type only.
    assert {row.content_type for row in reports.REPORT_TYPES.values()} == {
        reports.REPORT_OUTPUT.content_type
    }


def test_create_report_for_the_connection_marketplace(amazon, run):
    amazon.reply("POST", "/reports/2021-06-30/reports", {"reportId": "REP-9"}, status=202)
    response = run(
        "create_report",
        {
            "report_type": ORDERS,
            "data_start_time": "2026-11-01T00:00:00Z",
            "data_end_time": "2026-11-05T00:00:00+01:00",
        },
    )
    assert response["ok"] is True, response
    assert response["result"] == {"report_id": "REP-9", "report_type": ORDERS, "contains_pii": True}
    assert amazon.requests[0].body == {
        "reportType": ORDERS,
        "marketplaceIds": [UK_MARKETPLACE_ID],
        "dataStartTime": "2026-11-01T00:00:00Z",
        "dataEndTime": "2026-11-05T00:00:00+01:00",
    }


def test_settlement_report_cannot_be_requested(amazon, run):
    response = run("create_report", {"report_type": SETTLEMENT})
    assert response["error_code"] == "invalid_payload"
    assert "cannot be requested" in response["error"] and "search_reports" in response["error"]
    assert amazon.requests == []


def test_report_type_outside_the_allowlist_is_refused(amazon, run):
    response = run("create_report", {"report_type": "GET_AMAZON_FULFILLED_SHIPMENTS_DATA_GENERAL"})
    assert response["error_code"] == "invalid_payload"
    response = run("search_reports", {"report_types": ["GET_SOMETHING_ELSE"]})
    assert response["error_code"] == "invalid_payload"
    assert amazon.requests == []


def test_create_report_refuses_a_timestamp_without_zone(amazon, run):
    response = run(
        "create_report", {"report_type": LISTINGS, "data_start_time": "2026-11-01T00:00:00"}
    )
    assert response["error_code"] == "invalid_payload"
    assert amazon.requests == []


def test_search_reports_sends_filters_then_only_the_token(amazon, run):
    amazon.reply(
        "GET",
        "/reports/2021-06-30/reports",
        {"reports": [report(report_type=SETTLEMENT)], "nextToken": "amz-rep-2"},
    )
    inputs = {
        "report_types": [SETTLEMENT],
        "processing_statuses": ["DONE"],
        "page_size": 100,
        "created_since": "2026-10-01T00:00:00Z",
    }
    first = run("search_reports", inputs)
    assert first["ok"] is True, first
    assert amazon.requests[0].query == {
        "reportTypes": SETTLEMENT,
        "processingStatuses": "DONE",
        "marketplaceIds": UK_MARKETPLACE_ID,
        "pageSize": "100",
        "createdSince": "2026-10-01T00:00:00Z",
    }
    found = first["result"]["reports"][0]
    assert found["report_document_id"] == "amzn1.spdoc.1"
    assert found["contains_pii"] is False
    amazon.reply("GET", "/reports/2021-06-30/reports", {"reports": []})
    run("search_reports", {**inputs, "next_token": first["result"]["next_token"]})
    assert amazon.requests[1].query == {"nextToken": "amz-rep-2"}
    other = run(
        "search_reports",
        {**inputs, "report_types": [LISTINGS], "next_token": first["result"]["next_token"]},
    )
    assert other["error_code"] == "continuation_token_invalid"


def test_search_reports_requires_report_types(amazon, run):
    response = run("search_reports", {})
    assert response["error_code"] == "invalid_payload"


def test_get_report_normalizes(amazon, run):
    amazon.reply("GET", "/reports/2021-06-30/reports/REP-1", report())
    response = run("get_report", {"report_id": "REP-1"})
    assert response["result"]["processing_status"] == "DONE"
    assert response["result"]["report_document_id"] == "amzn1.spdoc.1"
    assert response["result"]["report"]["amazon"]["reportId"] == "REP-1"


def test_download_report_by_id_gunzips_and_stores_a_file(amazon, run):
    content = "sku\tasin\tprice\nSKU-1\tB000TEST01\t12345678.99\n".encode()
    amazon.reply("GET", "/reports/2021-06-30/reports/REP-1", report())
    amazon.reply(
        "GET",
        "/reports/2021-06-30/documents/amzn1.spdoc.1",
        {"reportDocumentId": "amzn1.spdoc.1", "url": REPORT_URL, "compressionAlgorithm": "GZIP"},
    )
    amazon.reply("GET", "/report-doc-1", raw=gzip.compress(content))
    writer = Writer()
    response = run("download_report", {"report_id": "REP-1"}, artifact_writer=writer)
    assert response["ok"] is True, response
    result = response["result"]
    assert writer.calls == [
        {
            "body": content,
            "binding_key": "amazon_report_document",
            "content_type": "text/tab-separated-values",
        }
    ]
    assert result["artifact_handle"] == "artifact-handle-report-1"
    assert result["size_bytes"] == len(content)
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert result["content_type"] == "text/tab-separated-values"
    assert result["filename"] == f"{LISTINGS}-REP-1.tsv"
    assert result["report_type"] == LISTINGS
    assert result["was_compressed"] is True
    assert result["contains_pii"] is False
    assert "x-amz-access-token" not in amazon.requests[2].headers
    dumped = json.dumps(response)
    assert "report-sig-secret" not in dumped and "amazonaws.com" not in dumped


def test_download_settlement_report_by_report_id(amazon, run):
    amazon.reply(
        "GET",
        "/reports/2021-06-30/reports/SETTLE-1",
        report(report_type=SETTLEMENT, reportId="SETTLE-1", reportDocumentId="amzn1.spdoc.9"),
    )
    amazon.reply(
        "GET",
        "/reports/2021-06-30/documents/amzn1.spdoc.9",
        {"reportDocumentId": "amzn1.spdoc.9", "url": REPORT_URL},
    )
    amazon.reply("GET", "/report-doc-1", raw=b"settlement-id\ttotal-amount\n1\t0.10\n")
    writer = Writer()
    response = run("download_report", {"report_id": "SETTLE-1"}, artifact_writer=writer)
    assert response["ok"] is True, response
    assert response["result"]["report_type"] == SETTLEMENT
    assert response["result"]["report_id"] == "SETTLE-1"
    assert response["result"]["report_document_id"] == "amzn1.spdoc.9"
    assert response["result"]["was_compressed"] is False
    assert writer.calls[0]["body"] == b"settlement-id\ttotal-amount\n1\t0.10\n"
    assert [request.path for request in amazon.requests] == [
        "/reports/2021-06-30/reports/SETTLE-1",
        "/reports/2021-06-30/documents/amzn1.spdoc.9",
        "/report-doc-1",
    ]


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"report_document_id": "amzn1.spdoc.9"},
        {"report_document_id": "amzn1.spdoc.9", "report_type": LISTINGS},
        {"report_id": "REP-1", "report_type": LISTINGS},
    ],
)
def test_download_report_takes_a_report_id_only(amazon, run, inputs):
    """A document ID with a caller-named type would skip the allowlist and the PII flag."""
    response = run("download_report", inputs, artifact_writer=Writer())
    assert response["error_code"] == "invalid_payload"
    assert amazon.requests == []


def test_download_report_reads_the_type_from_amazon_not_the_caller(amazon, run):
    amazon.reply(
        "GET", "/reports/2021-06-30/reports/REP-7", report(report_type=ORDERS, reportId="REP-7")
    )
    amazon.reply(
        "GET",
        "/reports/2021-06-30/documents/amzn1.spdoc.1",
        {"reportDocumentId": "amzn1.spdoc.1", "url": REPORT_URL},
    )
    amazon.reply("GET", "/report-doc-1", raw=b"order-id\tbuyer-email\n1\ta@b.example\n")
    response = run("download_report", {"report_id": "REP-7"}, artifact_writer=Writer())
    assert response["ok"] is True, response
    assert response["result"]["report_type"] == ORDERS
    assert response["result"]["contains_pii"] is True


def test_report_content_cap_is_200_mb():
    assert reports.MAX_REPORT_CONTENT_BYTES == 200 * 1024 * 1024
    assert reports.REPORT_OUTPUT.max_size_bytes == reports.MAX_REPORT_CONTENT_BYTES


def test_download_of_a_report_not_done_is_refused(amazon, run):
    amazon.reply("GET", "/reports/2021-06-30/reports/REP-1", report(status="IN_PROGRESS"))
    response = run("download_report", {"report_id": "REP-1"}, artifact_writer=Writer())
    assert response["error_code"] == "document_unavailable"
    assert "IN_PROGRESS" in response["error"]
    assert len(amazon.requests) == 1


def test_download_of_a_type_outside_the_allowlist_is_refused(amazon, run):
    amazon.reply(
        "GET", "/reports/2021-06-30/reports/REP-1", report(report_type="GET_RESTRICTED_SOMETHING")
    )
    response = run("download_report", {"report_id": "REP-1"}, artifact_writer=Writer())
    assert response["error_code"] == "invalid_payload"
    assert len(amazon.requests) == 1


def test_report_zip_bomb_is_refused(amazon, run, monkeypatch):
    monkeypatch.setattr(reports, "MAX_REPORT_CONTENT_BYTES", 1024 * 1024)
    amazon.reply("GET", "/reports/2021-06-30/reports/REP-1", report())
    amazon.reply(
        "GET",
        "/reports/2021-06-30/documents/amzn1.spdoc.1",
        {"reportDocumentId": "amzn1.spdoc.1", "url": REPORT_URL, "compressionAlgorithm": "GZIP"},
    )
    amazon.reply("GET", "/report-doc-1", raw=gzip.compress(b"A" * (16 * 1024 * 1024)))
    writer = Writer()
    response = run("download_report", {"report_id": "REP-1"}, artifact_writer=writer)
    assert response["error_code"] == "response_too_large"
    assert writer.calls == []


def test_report_document_url_outside_amazon_s3_is_refused(amazon, run):
    amazon.reply("GET", "/reports/2021-06-30/reports/REP-1", report())
    amazon.reply(
        "GET",
        "/reports/2021-06-30/documents/amzn1.spdoc.1",
        {"reportDocumentId": "amzn1.spdoc.1", "url": "http://tortuga.s3.amazonaws.com/x"},
    )
    response = run("download_report", {"report_id": "REP-1"}, artifact_writer=Writer())
    assert response["error_code"] == "blocked_address"
    assert len(amazon.requests) == 2


def test_failed_file_write_is_a_clear_error(amazon, run):
    amazon.reply("GET", "/reports/2021-06-30/reports/REP-1", report())
    amazon.reply(
        "GET",
        "/reports/2021-06-30/documents/amzn1.spdoc.1",
        {"reportDocumentId": "amzn1.spdoc.1", "url": REPORT_URL},
    )
    amazon.reply("GET", "/report-doc-1", raw=b"a\tb\n")

    def writer(*args, **kwargs):
        raise RuntimeError("Artifact content type does not match the platform upload grant")

    response = run("download_report", {"report_id": "REP-1"}, artifact_writer=writer)
    assert response["error_code"] == "artifact_output_unavailable"
    assert "grant" in response["error"]
    assert "content type does not match" not in response["error"]


def test_expired_document_url_is_reported(amazon, run):
    amazon.reply("GET", "/reports/2021-06-30/reports/REP-1", report())
    amazon.reply(
        "GET",
        "/reports/2021-06-30/documents/amzn1.spdoc.1",
        {"reportDocumentId": "amzn1.spdoc.1", "url": REPORT_URL},
    )
    amazon.reply("GET", "/report-doc-1", raw=b"<Error>AccessDenied</Error>", status=403)
    response = run("download_report", {"report_id": "REP-1"}, artifact_writer=Writer())
    assert response["error_code"] == "document_unavailable"
    assert "report-sig-secret" not in json.dumps(response)
