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


def test_no_spec_built_method_lets_input_choose_seller_marketplace_or_headers():
    for row in OPERATIONS:
        if not row.api_version:
            continue
        exposed = [
            name
            for name in row.properties
            if any(part in name for part in ("marketplace", "seller", "business", "idempotency"))
        ]
        assert exposed == [], (row.operation_id, exposed)


def test_an_oversized_label_is_refused_before_it_is_stored(amazon, run):
    import base64

    from runtime.sections.shipping_v2 import MAX_DOCUMENT_BYTES

    big = b"%PDF" + b"0" * MAX_DOCUMENT_BYTES
    amazon.reply(
        "GET",
        "/shipping/v2/shipments/SHIP-1/documents",
        {
            "payload": {
                "shipmentId": "SHIP-1",
                "packageDocumentDetail": {
                    "packageClientReferenceId": "pkg-1",
                    "packageDocuments": [
                        {
                            "type": "LABEL",
                            "format": "PDF",
                            "contents": base64.b64encode(big).decode(),
                        }
                    ],
                },
            }
        },
    )
    calls: list[bytes] = []
    response = run(
        "get_shipping_documents",
        {"shipment_id": "SHIP-1", "package_client_reference_id": "pkg-1"},
        artifact_writer=lambda payload, body, **kw: calls.append(body) or {},
    )
    assert response["error_code"] in {"response_too_large"}
    assert calls == []


def test_every_method_states_what_it_can_reach():
    from runtime.catalog import OPERATIONS_BY_ID
    from runtime.registry import SCOPES

    for row in OPERATIONS:
        assert row.scope in SCOPES, row.operation_id
    # Addressed by Amazon ID: the plan or shipment may belong to another marketplace
    # of the same seller in the region, and the contract says so.
    for operation_id in (
        "fba_list_inbound_plans",
        "fba_get_inbound_plan",
        "fba_cancel_inbound_plan",
        "fba_get_shipment",
        "fba_get_labels",
        "get_order",
        "get_report",
        "cancel_feed",
    ):
        assert OPERATIONS_BY_ID[operation_id].scope == "seller_region", operation_id
    for operation_id in (
        "fba_create_inbound_plan",
        "fba_list_prep_details",
        "delete_listing",
        "search_orders",
    ):
        assert OPERATIONS_BY_ID[operation_id].scope == "marketplace", operation_id
    assert OPERATIONS_BY_ID["purchase_shipping_shipment"].scope == "shipping_business"


def test_region_wide_methods_say_so_in_their_published_description():
    from build_contracts import operation_manifest

    rows = {row["operation_id"]: row for row in operation_manifest()["operations"]}
    assert "any marketplace of the seller" in rows["fba_get_inbound_plan"]["description"]
    assert "Scope:" not in rows["fba_create_inbound_plan"]["description"]
