"""A change whose success answer lacks its identifiers has an unknown outcome.

The pinned response schemas mark the plan ID of a created inbound plan, the
operation ID of every asynchronous inbound step and the shipment ID of a bought
label as required. A 2xx answer without one of them (an empty object, a missing
field, an empty string) may still mean Amazon made the change, so the step reports
``outcome_unknown`` with the identifiers to read back, calls Amazon only once and
keeps Amazon's request ID. Answers that carry the identifiers, and 204 answers that
have no body by design, still succeed.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest
from test_shipping_v2 import PURCHASE, PURCHASE_INPUT

import runtime.sections
from runtime import amazon_api

BASE = "/inbound/fba/2024-03-20"
PLAN = "wf1234abcd-1234-abcd-5678-1234abcd5678"
OPERATION = "1234abcd-1234-abcd-5678-1234abcd5678"
REQUEST_ID = {"x-amzn-RequestId": "req-unknown-1"}

CREATE_PLAN_INPUT = {
    "items": [{"msku": "MUG-1", "quantity": 24, "prep_owner": "SELLER", "label_owner": "SELLER"}],
    "source_address": {
        "name": "W1",
        "address_line1": "1 Street",
        "city": "London",
        "country_code": "GB",
        "postal_code": "EC1A 1BB",
        "phone_number": "+442000000000",
    },
}


def _assert_unknown(response: dict, amazon, *, missing: str) -> dict:
    assert response["ok"] is False, response
    assert response["error_code"] == "outcome_unknown"
    assert response["external_effect_status"] == "unknown"
    assert response["definitely_no_external_effect"] is False
    assert missing in response["error"]
    assert response["result"]["amazon_request_id"] == "req-unknown-1"
    assert len(amazon.requests) == 1, "an unknown outcome is never sent again"
    return response["result"]["check"]


# -- create inbound plan --------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "missing"),
    [
        ({}, "inbound_plan_id"),
        ({"operationId": OPERATION}, "inbound_plan_id"),
        ({"inboundPlanId": PLAN}, "operation_id"),
        ({"inboundPlanId": "", "operationId": OPERATION}, "inbound_plan_id"),
        ({"inboundPlanId": PLAN, "operationId": "   "}, "operation_id"),
        ({"inboundPlanId": None, "operationId": OPERATION}, "inbound_plan_id"),
    ],
)
def test_a_created_plan_without_its_ids_is_unknown(amazon, run, answer, missing):
    amazon.reply("POST", f"{BASE}/inboundPlans", answer, status=202, headers=REQUEST_ID)
    response = run("fba_create_inbound_plan", CREATE_PLAN_INPUT)
    check = _assert_unknown(response, amazon, missing=missing)
    assert check["read_with"] == ["fba_list_inbound_plans"]


def test_a_partial_answer_keeps_the_plan_id_it_returned(amazon, run):
    amazon.reply(
        "POST", f"{BASE}/inboundPlans", {"inboundPlanId": PLAN}, status=202, headers=REQUEST_ID
    )
    response = run("fba_create_inbound_plan", CREATE_PLAN_INPUT)
    check = _assert_unknown(response, amazon, missing="operation_id")
    assert check["ids"] == {"inbound_plan_id": PLAN}
    assert check["read_with"] == ["fba_list_inbound_plans"]


def test_a_partial_answer_keeps_the_operation_id_it_returned(amazon, run):
    amazon.reply(
        "POST", f"{BASE}/inboundPlans", {"operationId": OPERATION}, status=202, headers=REQUEST_ID
    )
    response = run("fba_create_inbound_plan", CREATE_PLAN_INPUT)
    check = _assert_unknown(response, amazon, missing="inbound_plan_id")
    assert check["ids"] == {"operation_id": OPERATION}
    assert "inbound_plan_id" not in check["ids"]
    assert check["read_with"] == ["fba_list_inbound_plans"]


@pytest.mark.parametrize("bad", [42, 0.5, True, False, {"id": PLAN}, [PLAN], "", "   ", None])
def test_a_wrong_type_or_blank_id_counts_as_missing_and_is_not_kept(amazon, run, bad):
    amazon.reply(
        "POST",
        f"{BASE}/inboundPlans",
        {"inboundPlanId": bad, "operationId": OPERATION},
        status=202,
        headers=REQUEST_ID,
    )
    response = run("fba_create_inbound_plan", CREATE_PLAN_INPUT)
    check = _assert_unknown(response, amazon, missing="inbound_plan_id")
    assert check["ids"] == {"operation_id": OPERATION}


def test_the_answer_body_never_reaches_the_check(amazon, run):
    amazon.reply(
        "POST",
        f"{BASE}/inboundPlans",
        {"inboundPlanId": PLAN, "sourceAddress": {"name": "Jane Doe"}, "extra": "x"},
        status=202,
        headers=REQUEST_ID,
    )
    response = run("fba_create_inbound_plan", CREATE_PLAN_INPUT)
    check = _assert_unknown(response, amazon, missing="operation_id")
    assert check == {"ids": {"inbound_plan_id": PLAN}, "read_with": ["fba_list_inbound_plans"]}
    assert "Jane Doe" not in str(response)


def test_an_async_step_keeps_its_input_ids_and_read_with(amazon, run):
    amazon.reply(
        "POST",
        f"{BASE}/inboundPlans/{PLAN}/packingOptions",
        {"operationId": 7},
        status=202,
        headers=REQUEST_ID,
    )
    response = run("fba_generate_packing_options", {"inbound_plan_id": PLAN})
    check = _assert_unknown(response, amazon, missing="operation_id")
    assert check["ids"] == {"inbound_plan_id": PLAN}
    assert check["read_with"]


@pytest.mark.parametrize("bad", [123, True, {"id": "SHIP-1"}, ["SHIP-1"]])
def test_a_purchase_with_a_wrong_type_shipment_id_is_unknown(amazon, run, bad):
    amazon.reply(
        "POST",
        PURCHASE,
        {"payload": {"shipmentId": bad, "packageDocumentDetails": [], "promise": {}}},
        headers=REQUEST_ID,
    )
    response = run("purchase_shipping_shipment", PURCHASE_INPUT)
    check = _assert_unknown(response, amazon, missing="shipment_id")
    assert check["ids"] == {"request_token": "rt-1", "rate_id": "rate-1"}
    assert check["read_with"] == ["get_order"]


def test_a_created_plan_with_an_empty_body_is_unknown(amazon, run):
    amazon.reply("POST", f"{BASE}/inboundPlans", status=202, headers=REQUEST_ID, raw=b"")
    response = run("fba_create_inbound_plan", CREATE_PLAN_INPUT)
    _assert_unknown(response, amazon, missing="inbound_plan_id")


def test_a_created_plan_with_both_ids_succeeds(amazon, run):
    amazon.reply(
        "POST",
        f"{BASE}/inboundPlans",
        {"inboundPlanId": PLAN, "operationId": OPERATION},
        status=202,
        headers=REQUEST_ID,
    )
    response = run("fba_create_inbound_plan", CREATE_PLAN_INPUT)
    assert response["ok"] is True, response
    assert response["result"]["external_effect_status"] == "succeeded"
    assert response["result"]["amazon_request_id"] == "req-unknown-1"


# -- asynchronous inbound step ---------------------------------------------------


@pytest.mark.parametrize("answer", [{}, {"operationId": ""}, {"operationId": None}])
def test_generating_packing_options_without_an_operation_id_is_unknown(amazon, run, answer):
    amazon.reply(
        "POST", f"{BASE}/inboundPlans/{PLAN}/packingOptions", answer, status=202, headers=REQUEST_ID
    )
    response = run("fba_generate_packing_options", {"inbound_plan_id": PLAN})
    check = _assert_unknown(response, amazon, missing="operation_id")
    assert check["ids"] == {"inbound_plan_id": PLAN}
    assert check["read_with"]


def test_generating_packing_options_with_an_operation_id_succeeds(amazon, run):
    amazon.reply(
        "POST", f"{BASE}/inboundPlans/{PLAN}/packingOptions", {"operationId": OPERATION}, status=202
    )
    response = run("fba_generate_packing_options", {"inbound_plan_id": PLAN})
    assert response["ok"] is True, response
    assert response["result"]["operation_id"] == OPERATION


def test_a_204_change_without_a_body_still_succeeds(amazon, run):
    amazon.reply("PUT", f"{BASE}/inboundPlans/{PLAN}/name", status=204, raw=b"")
    response = run("fba_update_inbound_plan_name", {"inbound_plan_id": PLAN, "name": "Spring"})
    assert response["ok"] is True, response
    assert response["result"]["accepted"] is True
    assert response["result"]["external_effect_status"] == "succeeded"


# -- paid label purchase ----------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"payload": {}},
        {"payload": {"packageDocumentDetails": [], "promise": {}}},
        {"payload": {"shipmentId": "", "packageDocumentDetails": [], "promise": {}}},
    ],
)
def test_a_purchase_without_a_shipment_id_is_unknown_and_not_repeated(amazon, run, answer):
    amazon.reply("POST", PURCHASE, answer, headers=REQUEST_ID)
    response = run("purchase_shipping_shipment", PURCHASE_INPUT)
    check = _assert_unknown(response, amazon, missing="shipment_id")
    assert check["ids"] == {"request_token": "rt-1", "rate_id": "rate-1"}
    assert check["read_with"] == ["get_order"]


def test_a_purchase_with_an_empty_body_is_unknown(amazon, run):
    amazon.reply("POST", PURCHASE, headers=REQUEST_ID, raw=b"")
    response = run("purchase_shipping_shipment", PURCHASE_INPUT)
    _assert_unknown(response, amazon, missing="shipment_id")


def test_a_purchase_with_a_shipment_id_succeeds(amazon, run):
    amazon.reply(
        "POST",
        PURCHASE,
        {"payload": {"shipmentId": "SHIP-1", "packageDocumentDetails": [], "promise": {}}},
        headers=REQUEST_ID,
    )
    response = run("purchase_shipping_shipment", PURCHASE_INPUT)
    assert response["ok"] is True, response
    assert response["result"]["shipment_id"] == "SHIP-1"
    assert response["result"]["amazon_request_id"] == "req-unknown-1"


# -- scope ---------------------------------------------------------------------------


def _mutating_methods() -> dict[str, amazon_api.Method]:
    found: dict[str, amazon_api.Method] = {}
    for module in pkgutil.iter_modules(runtime.sections.__path__):
        section = importlib.import_module(f"runtime.sections.{module.name}")
        for method in getattr(section, "METHODS", ()):
            if method.mutating:
                found[method.operation_id] = method
    return found


def test_only_required_top_level_ids_of_changes_are_checked():
    """The check is bounded: these identifiers, read from the pinned schemas, and no others."""
    checked = {
        operation_id: amazon_api._plan(method).required_ids
        for operation_id, method in _mutating_methods().items()
    }
    expected_ids = {
        "fba_create_inbound_plan": ("inbound_plan_id", "operation_id"),
        "purchase_shipping_shipment": ("shipment_id",),
    }
    no_ids = {
        "fba_update_inbound_plan_name",
        "fba_update_shipment_name",
        "fba_schedule_self_ship_appointment",
        "cancel_shipping_shipment",
    }
    for operation_id, ids in checked.items():
        if operation_id in expected_ids:
            assert ids == expected_ids[operation_id], operation_id
        elif operation_id in no_ids:
            assert ids == (), operation_id
        else:
            assert ids == ("operation_id",), operation_id
    assert set(expected_ids) | no_ids <= set(checked)


def test_reads_are_never_checked():
    from runtime.sections import fba_inbound

    reads = [method for method in fba_inbound.METHODS if not method.mutating]
    assert reads
    assert all(amazon_api._plan(method).required_ids == () for method in reads)
