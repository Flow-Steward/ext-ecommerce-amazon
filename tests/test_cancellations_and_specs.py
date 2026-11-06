from __future__ import annotations

from build_amazon_specs import SPECS_PATH, render_specs
from conftest import SELLER_ID, UK_MARKETPLACE_ID

from runtime.catalog import OPERATIONS
from runtime.transport import EXTRA_HEADERS

ROOT_SPECS = __import__("pathlib").Path(__file__).resolve().parents[1] / SPECS_PATH


def test_compiled_specs_match_amazons_models():
    assert ROOT_SPECS.read_text(encoding="utf-8") == render_specs(), (
        "runtime/amazon_specs.py is stale: run python3 tests/reference/refresh_contracts.py"
    )


def test_delete_listing_is_accepted_not_applied(amazon, run):
    amazon.reply(
        "DELETE",
        f"/listings/2021-08-01/items/{SELLER_ID}/MUG-1",
        {"sku": "MUG-1", "status": "ACCEPTED", "submissionId": "sub-1", "issues": []},
    )
    response = run("delete_listing", {"sku": "MUG-1"})
    assert response["ok"] is True, response
    assert amazon.requests[0].query == {"marketplaceIds": UK_MARKETPLACE_ID}
    result = response["result"]
    assert result["status"] == "ACCEPTED"
    assert result["applied"] is False
    assert result["submission_id"] == "sub-1"
    assert "JSON_LISTINGS_FEED" in result["note"]


def test_delete_listing_in_test_mode_sends_nothing(amazon, run):
    response = run("delete_listing", {"sku": "MUG-1"}, test_mode=True)
    assert response["result"]["suppressed"] is True
    assert amazon.requests == []


def test_cancel_feed_and_report_name_what_to_read(amazon, run):
    amazon.reply("DELETE", "/feeds/2021-06-30/feeds/FEED-1", raw=b"")
    feed = run("cancel_feed", {"feed_id": "FEED-1"})
    assert feed["ok"] is True, feed
    assert feed["result"]["cancellation_requested"] is True
    assert "IN_QUEUE" in feed["result"]["note"]
    amazon.reply("DELETE", "/reports/2021-06-30/reports/REP-1", raw=b"")
    report = run("cancel_report", {"report_id": "REP-1"})
    assert report["ok"] is True, report
    amazon.reply("DELETE", "/reports/2021-06-30/reports/REP-2", {"errors": []}, status=502)
    unknown = run("cancel_report", {"report_id": "REP-2"})
    assert unknown["error_code"] == "outcome_unknown"
    assert unknown["result"]["check"] == {
        "ids": {"report_id": "REP-2"},
        "read_with": ["get_report"],
    }


def test_only_two_fixed_headers_can_be_added():
    assert EXTRA_HEADERS == {"x-amzn-shipping-business-id", "x-amzn-IdempotencyKey"}


def test_no_method_loops_or_waits():
    # Every row built from a spec names exactly one Amazon call.
    for row in OPERATIONS:
        if row.api_version:
            assert len(row.amazon_calls) == 1, row.operation_id


def test_existing_report_allowlist_is_unchanged():
    from runtime.sections import reports

    assert len(reports.REPORT_TYPES) == 9
