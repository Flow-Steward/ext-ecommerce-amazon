from __future__ import annotations

import json

import pytest
from conftest import UK_MARKETPLACE_ID

from runtime.finances_records import _MAX_DEPTH, MAX_AMOUNTS_PER_RECORD, collect_amounts
from runtime.sections import finances

# Raw bytes so the amounts are JSON numbers, decoded by the transport as Decimal.
TRANSACTIONS_PAGE = b"""{"payload": {"nextToken": "amz-tx-2", "transactions": [
  {"sellingPartnerMetadata": {"sellingPartnerId": "A1SELLER0TEST1", "accountType": "Standard Orders", "marketplaceId": "A1F83G8C2ARO7P"},
   "relatedIdentifiers": [{"relatedIdentifierName": "FINANCIAL_EVENT_GROUP_ID", "relatedIdentifierValue": "GRP-1"},
                          {"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": "202-1111111-1111111"}],
   "transactionType": "Shipment", "transactionId": "TX-1", "transactionStatus": "RELEASED",
   "description": "Order Payment", "postedDate": "2026-11-01T10:00:00Z",
   "totalAmount": {"currencyAmount": 12345678.99, "currencyCode": "GBP"},
   "breakdowns": [{"breakdownType": "Sales", "breakdownAmount": {"currencyAmount": 0.10, "currencyCode": "GBP"},
                   "breakdowns": [{"breakdownType": "ProductCharges", "breakdownAmount": {"currencyAmount": 0.10, "currencyCode": "GBP"}}]}]}
]}}"""

V0_PAGE = b"""{"payload": {"NextToken": "amz-ev-2", "FinancialEvents": {
  "ShipmentEventList": [{"AmazonOrderId": "202-1", "PostedDate": "2026-11-01T10:00:00Z", "MarketplaceName": "Amazon.co.uk",
     "ShipmentItemList": [{"SellerSKU": "SKU-1",
        "ItemChargeList": [{"ChargeType": "Principal", "ChargeAmount": {"CurrencyCode": "GBP", "CurrencyAmount": 12345678.99}},
                           {"ChargeType": "Tax", "ChargeAmount": {"CurrencyCode": "GBP", "CurrencyAmount": 0.10}}],
        "ItemFeeList": [{"FeeType": "Commission", "FeeAmount": {"CurrencyCode": "GBP", "CurrencyAmount": -1.50}}]}]}],
  "RefundEventList": [{"AmazonOrderId": "202-2", "PostedDate": "2026-11-02T10:00:00Z",
     "ShipmentItemAdjustmentList": [{"ItemChargeAdjustmentList": [{"ChargeType": "Principal", "ChargeAmount": {"CurrencyCode": "EUR", "CurrencyAmount": -5.00}}],
                                     "ItemFeeAdjustmentList": [{"FeeType": "Commission", "FeeAmount": {"CurrencyCode": "GBP", "CurrencyAmount": 0.75}}]}]}],
  "ServiceFeeEventList": [{"FeeReason": "Subscription", "FeeList": [{"FeeType": "Subscription", "FeeAmount": {"CurrencyCode": "GBP", "CurrencyAmount": -25.00}}]}],
  "AdjustmentEventList": [{"AdjustmentType": "ReserveDebit", "PostedDate": "2026-11-03T00:00:00Z", "AdjustmentAmount": {"CurrencyCode": "GBP", "CurrencyAmount": -100}}],
  "ProductAdsPaymentEventList": [{"postedDate": "2026-11-04T00:00:00Z", "transactionType": "charge", "transactionValue": {"CurrencyCode": "GBP", "CurrencyAmount": 3.33}}],
  "RetrochargeEventList": []
}}}"""


def test_default_version_lists_2024_transactions(amazon, run):
    amazon.reply("GET", "/finances/2024-06-19/transactions", raw=TRANSACTIONS_PAGE)
    response = run(
        "list_financial_records",
        {
            "posted_after": "2026-11-01T00:00:00Z",
            "posted_before": "2026-11-03T00:00:00+01:00",
            "transaction_status": "RELEASED",
        },
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert result["api_version"] == "2024-06-19"
    assert result["amazon_operation"] == "Finances 2024-06-19 listTransactions"
    assert amazon.requests[0].query == {
        "postedAfter": "2026-11-01T00:00:00Z",
        "postedBefore": "2026-11-03T00:00:00+01:00",
        "marketplaceId": UK_MARKETPLACE_ID,
        "transactionStatus": "RELEASED",
    }
    (record,) = result["records"]
    assert record["record_type"] == "Shipment"
    assert record["record_id"] == "TX-1"
    assert record["amazon_order_id"] == "202-1111111-1111111"
    assert record["posted_date"] == "2026-11-01T10:00:00Z"
    assert record["status"] == "RELEASED"
    assert record["total"] == {"amount": "12345678.99", "currency_code": "GBP"}
    assert record["origin"] == {
        "api_version": "2024-06-19",
        "amazon_operation": "Finances 2024-06-19 listTransactions",
        "list_name": "transactions",
    }
    assert record["amounts"] == [
        {"label": "totalAmount", "amount": "12345678.99", "currency_code": "GBP"},
        {"label": "breakdowns[Sales].breakdownAmount", "amount": "0.10", "currency_code": "GBP"},
        {
            "label": "breakdowns[Sales].breakdowns[ProductCharges].breakdownAmount",
            "amount": "0.10",
            "currency_code": "GBP",
        },
    ]
    # Amazon's object is kept, with exact decimals.
    assert record["amazon"]["totalAmount"]["currencyAmount"] == "12345678.99"
    assert result["next_token"]


def test_v0_flattens_every_event_list_into_records(amazon, run):
    amazon.reply("GET", "/finances/v0/financialEvents", raw=V0_PAGE)
    response = run(
        "list_financial_records",
        {"api_version": "v0", "posted_after": "2026-11-01T00:00:00Z", "max_results": 50},
    )
    assert response["ok"] is True, response
    result = response["result"]
    assert amazon.requests[0].query == {
        "MaxResultsPerPage": "50",
        "PostedAfter": "2026-11-01T00:00:00Z",
    }
    assert result["amazon_operation"] == "Finances v0 listFinancialEvents"
    records = result["records"]
    assert [record["record_type"] for record in records] == [
        "ShipmentEvent",
        "RefundEvent",
        "ServiceFeeEvent",
        "AdjustmentEvent",
        "ProductAdsPaymentEvent",
    ]
    assert result["count"] == 5
    shipment, refund, service_fee, adjustment, ads = records
    assert shipment["amazon_order_id"] == "202-1"
    assert shipment["record_id"] is None and shipment["total"] is None
    assert shipment["origin"] == {
        "api_version": "v0",
        "amazon_operation": "Finances v0 listFinancialEvents",
        "list_name": "ShipmentEventList",
    }
    assert shipment["amounts"] == [
        {
            "label": "ShipmentItemList[].ItemChargeList[Principal].ChargeAmount",
            "amount": "12345678.99",
            "currency_code": "GBP",
        },
        {
            "label": "ShipmentItemList[].ItemChargeList[Tax].ChargeAmount",
            "amount": "0.10",
            "currency_code": "GBP",
        },
        {
            "label": "ShipmentItemList[].ItemFeeList[Commission].FeeAmount",
            "amount": "-1.50",
            "currency_code": "GBP",
        },
    ]
    # Two currencies stay two amounts; nothing is summed.
    assert {(item["currency_code"], item["amount"]) for item in refund["amounts"]} == {
        ("EUR", "-5.00"),
        ("GBP", "0.75"),
    }
    assert service_fee["posted_date"] is None and service_fee["amazon_order_id"] is None
    assert adjustment["amounts"] == [
        {"label": "AdjustmentAmount", "amount": "-100", "currency_code": "GBP"}
    ]
    assert ads["posted_date"] == "2026-11-04T00:00:00Z"
    assert ads["amounts"][0]["amount"] == "3.33"


def test_v0_and_2024_records_have_the_same_keys(amazon, run):
    amazon.reply("GET", "/finances/v0/financialEvents", raw=V0_PAGE)
    amazon.reply("GET", "/finances/2024-06-19/transactions", raw=TRANSACTIONS_PAGE)
    v0 = run(
        "list_financial_records", {"api_version": "v0", "posted_after": "2026-11-01T00:00:00Z"}
    )["result"]["records"][0]
    v2024 = run("list_financial_records", {"posted_after": "2026-11-01T00:00:00Z"})["result"][
        "records"
    ][0]
    assert set(v0) == set(v2024)


@pytest.mark.parametrize(
    ("inputs", "name"),
    [
        ({"api_version": "v0", "transaction_status": "RELEASED"}, "transaction_status"),
        ({"api_version": "v0", "omit_marketplace_filter": True}, "omit_marketplace_filter"),
        ({"max_results": 10}, "max_results"),
    ],
)
def test_filters_of_the_other_version_are_refused(amazon, run, inputs, name):
    response = run("list_financial_records", {"posted_after": "2026-11-01T00:00:00Z", **inputs})
    assert response["error_code"] == "unsupported_filter"
    assert name in response["error"]
    assert amazon.requests == []


def test_posted_after_is_required_and_needs_a_zone(amazon, run):
    assert run("list_financial_records", {})["error_code"] == "invalid_payload"
    assert (
        run("list_financial_records", {"posted_after": "2026-11-01T00:00:00"})["error_code"]
        == "invalid_payload"
    )
    assert (
        run("list_financial_records", {"api_version": "v0", "posted_after": "2026-11-01"})[
            "error_code"
        ]
        == "invalid_payload"
    )
    assert amazon.requests == []


def test_omit_marketplace_filter_leaves_the_marketplace_out(amazon, run):
    amazon.reply("GET", "/finances/2024-06-19/transactions", {"payload": {"transactions": []}})
    response = run(
        "list_financial_records",
        {"posted_after": "2026-11-01T00:00:00Z", "omit_marketplace_filter": True},
    )
    assert response["ok"] is True
    assert "marketplaceId" not in amazon.requests[0].query
    assert response["result"]["next_token"] is None


def test_next_page_resends_the_same_arguments_with_the_token(amazon, run):
    amazon.reply("GET", "/finances/2024-06-19/transactions", raw=TRANSACTIONS_PAGE)
    inputs = {"posted_after": "2026-11-01T00:00:00Z"}
    token = run("list_financial_records", inputs)["result"]["next_token"]
    amazon.reply("GET", "/finances/2024-06-19/transactions", {"payload": {"transactions": []}})
    run("list_financial_records", {**inputs, "next_token": token})
    assert amazon.requests[1].query == {
        "postedAfter": "2026-11-01T00:00:00Z",
        "marketplaceId": UK_MARKETPLACE_ID,
        "nextToken": "amz-tx-2",
    }
    wrong_version = run(
        "list_financial_records", {**inputs, "api_version": "v0", "next_token": token}
    )
    assert wrong_version["error_code"] == "continuation_token_invalid"
    assert len(amazon.requests) == 2


def test_records_for_order_in_both_versions(amazon, run):
    amazon.reply("GET", "/finances/2024-06-19/transactions", raw=TRANSACTIONS_PAGE)
    response = run("list_financial_records_for_order", {"order_id": "202-1111111-1111111"})
    assert response["ok"] is True, response
    assert amazon.requests[0].query == {
        "marketplaceId": UK_MARKETPLACE_ID,
        "relatedIdentifierName": "ORDER_ID",
        "relatedIdentifierValue": "202-1111111-1111111",
    }
    amazon.reply("GET", "/finances/v0/orders/202-1/financialEvents", raw=V0_PAGE)
    v0 = run("list_financial_records_for_order", {"order_id": "202-1", "api_version": "v0"})
    assert v0["ok"] is True, v0
    assert v0["result"]["amazon_operation"] == "Finances v0 listFinancialEventsByOrderId"
    assert (
        v0["result"]["records"][0]["origin"]["amazon_operation"]
        == "Finances v0 listFinancialEventsByOrderId"
    )
    assert amazon.requests[1].query == {}


def test_event_groups_normalize_totals_and_statuses(amazon, run):
    amazon.reply(
        "GET",
        "/finances/v0/financialEventGroups",
        raw=b"""{"payload": {"FinancialEventGroupList": [{"FinancialEventGroupId": "GRP-1", "ProcessingStatus": "Closed",
            "FundTransferStatus": "Succeeded", "OriginalTotal": {"CurrencyCode": "EUR", "CurrencyAmount": 12345678.99},
            "ConvertedTotal": {"CurrencyCode": "GBP", "CurrencyAmount": 0.10}, "FundTransferDate": "2026-11-03T00:00:00Z",
            "FinancialEventGroupStart": "2026-10-20T00:00:00Z", "FinancialEventGroupEnd": "2026-11-03T00:00:00Z",
            "TraceId": "T-1", "AccountTail": "123"}]}}""",
    )
    response = run(
        "list_financial_event_groups", {"started_after": "2026-10-01T00:00:00Z", "max_results": 10}
    )
    assert response["ok"] is True, response
    assert amazon.requests[0].query == {
        "MaxResultsPerPage": "10",
        "FinancialEventGroupStartedAfter": "2026-10-01T00:00:00Z",
    }
    (group,) = response["result"]["groups"]
    assert group["group_id"] == "GRP-1"
    assert group["original_total"] == {"amount": "12345678.99", "currency_code": "EUR"}
    assert group["converted_total"] == {"amount": "0.10", "currency_code": "GBP"}
    assert group["processing_status"] == "Closed"
    assert group["fund_transfer_status"] == "Succeeded"
    assert group["group_end"] == "2026-11-03T00:00:00Z"
    assert group["beginning_balance"] is None
    assert response["result"]["api_version"] == "v0"


def test_events_by_group_in_both_versions(amazon, run):
    amazon.reply("GET", "/finances/v0/financialEventGroups/GRP-1/financialEvents", raw=V0_PAGE)
    v0 = run(
        "list_financial_events_by_group",
        {"group_id": "GRP-1", "api_version": "v0", "posted_before": "2026-11-05T00:00:00Z"},
    )
    assert v0["ok"] is True, v0
    assert amazon.requests[0].query == {"PostedBefore": "2026-11-05T00:00:00Z"}
    assert v0["result"]["count"] == 5
    amazon.reply("GET", "/finances/2024-06-19/transactions", raw=TRANSACTIONS_PAGE)
    v2024 = run("list_financial_events_by_group", {"group_id": "GRP-1"})
    assert v2024["ok"] is True, v2024
    assert amazon.requests[1].query["relatedIdentifierName"] == "FINANCIAL_EVENT_GROUP_ID"
    assert amazon.requests[1].query["relatedIdentifierValue"] == "GRP-1"


def test_amounts_are_bounded():
    event = {
        "FeeList": [
            {"FeeType": f"F{index}", "FeeAmount": {"CurrencyCode": "GBP", "CurrencyAmount": "1"}}
            for index in range(MAX_AMOUNTS_PER_RECORD + 5)
        ]
    }
    amounts, truncated = collect_amounts(event)
    assert len(amounts) == MAX_AMOUNTS_PER_RECORD
    assert truncated is True


def _nested(depth: int, leaf: dict) -> dict:
    value = leaf
    for index in range(depth):
        value = {f"Level{index}": value}
    return value


def test_amounts_cut_by_the_depth_limit_are_flagged():
    money = {"CurrencyCode": "GBP", "CurrencyAmount": "1.00"}
    deep = {"Shallow": money, "Deep": _nested(_MAX_DEPTH + 3, money)}
    amounts, truncated = collect_amounts(deep)
    assert [item["label"] for item in amounts] == ["Shallow"]
    assert truncated is True


def test_deep_structure_without_amounts_is_not_flagged():
    money = {"CurrencyCode": "GBP", "CurrencyAmount": "1.00"}
    deep = {"Shallow": money, "Deep": _nested(_MAX_DEPTH + 3, {"Note": {"text": "x"}})}
    amounts, truncated = collect_amounts(deep)
    assert len(amounts) == 1
    assert truncated is False


def test_descriptions_state_the_removal_date_delay_and_no_mixing():
    rows = {row.operation_id: row for row in finances.OPERATIONS}
    assert "2027-08-27" in finances.API_VERSION_INPUT["description"]
    assert "2027-08-27" in finances.__doc__
    assert "48 hours" in rows["list_financial_records"].description
    assert "not interchangeable" in rows["list_financial_records"].description
    assert "not on Amazon's deprecation schedule" in rows["list_financial_event_groups"].description
    assert json.dumps([row.input_schema for row in rows.values()])
