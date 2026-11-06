from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import (
    ACCESS_TOKEN,
    CONNECTION_ID,
    FakeAmazon,
    FakeResponse,
    SELLER_ID,
    UK_MARKETPLACE_ID,
    US_MARKETPLACE_ID,
    assert_result_matches_outputs,
    connection_target,
    make_payload,
)

from runtime import errors, operations
from runtime.connection import connection_from_payload
from runtime.errors import ExtensionError
from runtime.transport import assert_document_url, encode_query, is_amazon_s3_host

PARTICIPATIONS = {
    "payload": [
        {
            "marketplace": {
                "id": UK_MARKETPLACE_ID,
                "countryCode": "GB",
                "defaultCurrencyCode": "GBP",
            },
            "participation": {"isParticipating": True, "hasSuspendedListings": False},
            "storeName": "Test Store",
        }
    ]
}


def test_test_connection_reports_each_check(amazon, run):
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", PARTICIPATIONS)
    amazon.reply(
        "GET", f"/listings/2021-08-01/items/{SELLER_ID}", {"numberOfResults": 0, "items": []}
    )
    response = run("test_connection")
    result = response["result"]
    assert result["ok"] is True
    assert result["seller_id_confirmed"] is True
    assert result["region"] == "EU"
    assert result["store_name"] == "Test Store"
    assert [check["status"] for check in result["checks"]] == ["passed", "passed"]
    assert amazon.requests[1].query == {"marketplaceIds": UK_MARKETPLACE_ID, "pageSize": "1"}


def test_test_connection_explains_a_seller_id_amazon_refuses(amazon, run):
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", PARTICIPATIONS)
    amazon.reply(
        "GET",
        f"/listings/2021-08-01/items/{SELLER_ID}",
        {"errors": [{"code": "Unauthorized"}]},
        status=403,
    )
    result = run("test_connection")["result"]
    assert result["ok"] is False
    assert result["seller_id_confirmed"] is False
    assert "Merchant Token" in result["checks"][1]["message"]


def test_test_connection_on_a_rejected_token_is_an_error(amazon, run):
    amazon.reply(
        "GET",
        "/sellers/v1/marketplaceParticipations",
        {"errors": [{"code": "Unauthorized"}]},
        status=401,
    )
    amazon.reply("GET", f"/listings/2021-08-01/items/{SELLER_ID}", {})
    response = run("test_connection")
    assert response["ok"] is False
    assert response["error_code"] == "authentication_failed"


def _post_connect_payload(**target) -> dict:
    payload = make_payload("verify_connection", {}, target=connection_target(**target))
    payload["action"]["input"] = {}
    return payload


#: The only keys Core's parse_oauth_post_connect_result accepts from this extension.
_POST_CONNECT_KEYS = {"subject", "health", "error_code", "safe_metadata", "verified_display_name"}


def _verify(amazon, **target) -> dict:
    response = operations.handle_runtime(
        _post_connect_payload(**target), transport_factory=amazon.transport_factory()
    )
    assert response["ok"] is True, response
    assert_result_matches_outputs("verify_connection", response)
    assert set(response["result"]) == _POST_CONNECT_KEYS
    return response["result"]


def test_verify_connection_returns_the_confirmed_seller_as_subject(amazon):
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", PARTICIPATIONS)
    amazon.reply("GET", f"/listings/2021-08-01/items/{SELLER_ID}", {"items": []})
    assert _verify(amazon) == {
        "subject": SELLER_ID,
        "health": "ok",
        "error_code": None,
        "safe_metadata": {"marketplace": "UK", "region": "EU"},
        "verified_display_name": f"Test Store · Amazon UK · {SELLER_ID}",
    }


def test_verify_connection_declares_exactly_the_keys_it_returns():
    from runtime.catalog import OPERATIONS_BY_ID

    assert set(OPERATIONS_BY_ID["verify_connection"].result_fields) == _POST_CONNECT_KEYS


def _participations(marketplace_id: str = UK_MARKETPLACE_ID, **entry) -> dict:
    row = dict(PARTICIPATIONS["payload"][0], **entry)
    row["marketplace"] = dict(row["marketplace"], id=marketplace_id)
    return {"payload": [row]}


def _verified_name(amazon, *, seller_id=SELLER_ID, marketplace="UK", **entry) -> str:
    marketplace_id = {"UK": UK_MARKETPLACE_ID, "US": US_MARKETPLACE_ID}[marketplace]
    amazon.reply(
        "GET", "/sellers/v1/marketplaceParticipations", _participations(marketplace_id, **entry)
    )
    amazon.reply("GET", f"/listings/2021-08-01/items/{seller_id}", {"items": []})
    result = _verify(amazon, seller_id=seller_id, marketplace=marketplace)
    assert result["health"] == "ok" and result["error_code"] is None, result
    return result["verified_display_name"]


def test_verified_names_tell_one_store_in_two_marketplaces_apart():
    uk = _verified_name(FakeAmazon(), marketplace="UK")
    us = _verified_name(FakeAmazon(), marketplace="US")
    assert uk == f"Test Store · Amazon UK · {SELLER_ID}"
    assert us == f"Test Store · Amazon US · {SELLER_ID}"
    assert uk != us


def test_verified_names_tell_two_sellers_with_one_store_name_apart():
    first = _verified_name(FakeAmazon(), seller_id="A1SELLER0TEST1")
    second = _verified_name(FakeAmazon(), seller_id="A2SELLER0TEST2")
    assert first.endswith("· A1SELLER0TEST1") and second.endswith("· A2SELLER0TEST2")
    assert first != second


def test_verified_name_without_a_store_name_still_names_marketplace_and_seller(amazon):
    assert _verified_name(amazon, storeName="  ") == f"Amazon UK · {SELLER_ID}"


def test_verified_name_without_a_store_name_field(amazon):
    participations = _participations()
    del participations["payload"][0]["storeName"]
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", participations)
    amazon.reply("GET", f"/listings/2021-08-01/items/{SELLER_ID}", {"items": []})
    assert _verify(amazon)["verified_display_name"] == f"Amazon UK · {SELLER_ID}"


def test_a_long_unicode_store_name_is_cut_and_the_identity_suffix_kept(amazon):
    name = _verified_name(amazon, storeName="店" * 300)
    suffix = f" · Amazon UK · {SELLER_ID}"
    assert name.endswith(suffix)
    store = name.removesuffix(suffix)
    assert store and set(store) == {"店"}
    assert len(name.encode("utf-8")) <= 512
    # The cut keeps as much of the store name as fits on a character boundary.
    assert len(name.encode("utf-8")) > 512 - len("店".encode())


def test_a_store_name_with_combining_and_emoji_characters_stays_valid_utf8(amazon):
    name = _verified_name(amazon, storeName="Café 🛍️ " * 80)
    assert name.endswith(f" · Amazon UK · {SELLER_ID}")
    assert len(name.encode("utf-8")) <= 512
    name.encode("utf-8").decode("utf-8")


def test_verify_connection_flags_a_marketplace_the_seller_does_not_sell_in(amazon):
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", {"payload": []})
    amazon.reply("GET", f"/listings/2021-08-01/items/{SELLER_ID}", {"items": []})
    result = _verify(amazon)
    assert result["health"] == "needs_attention"
    assert result["error_code"] == "internal_failure"
    assert result["subject"] == ""
    assert result["safe_metadata"]["problem"] == "marketplace_not_confirmed"


def test_verify_connection_maps_a_dead_token_to_authorization_required(amazon):
    amazon.reply("GET", "/sellers/v1/marketplaceParticipations", {}, status=401)
    assert _verify(amazon) == {
        "subject": "",
        "health": "authorization_required",
        "error_code": "authorization_required",
        "safe_metadata": None,
        "verified_display_name": None,
    }


@pytest.mark.parametrize(
    ("config", "code"),
    [
        ({"seller_id": "bad id", "marketplace": "UK"}, errors.INVALID_CONFIGURATION),
        ({"seller_id": SELLER_ID, "marketplace": "GB"}, errors.INVALID_CONFIGURATION),
        (
            {"seller_id": SELLER_ID, "marketplace": "UK", "base_url": "https://evil.example"},
            errors.INVALID_CONNECTION,
        ),
    ],
)
def test_connection_settings_are_checked(config, code):
    target = connection_target()
    target["connection"]["config"] = config
    payload = make_payload("get_order", {"order_id": "1"}, target=target)
    with pytest.raises(ExtensionError) as raised:
        connection_from_payload(payload, connection_ref=CONNECTION_ID)
    assert raised.value.code == code


def test_missing_access_token_asks_for_a_new_refresh_token():
    target = connection_target()
    target["connection"]["secrets"] = {}
    with pytest.raises(ExtensionError) as raised:
        connection_from_payload(
            make_payload("get_order", target=target), connection_ref=CONNECTION_ID
        )
    assert raised.value.code == errors.REAUTHORIZATION_REQUIRED


def test_connection_repr_never_shows_the_token():
    connection = connection_from_payload(make_payload("get_order"), connection_ref=CONNECTION_ID)
    assert ACCESS_TOKEN not in repr(connection)


def test_query_encoding_joins_lists_and_drops_absent_values():
    assert (
        encode_query({"a": ["x", "y"], "b": None, "c": True, "d": 3, "e": []})
        == "a=x%2Cy&c=true&d=3"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://tortuga-prod-eu.s3-eu-west-1.amazonaws.com/doc",
        "https://evil.example/doc",
        "https://amazonaws.com.evil.example/doc",
        "",
    ],
)
def test_document_urls_outside_amazon_s3_are_refused(url):
    with pytest.raises(ExtensionError):
        assert_document_url(url)


def test_unknown_operation_is_refused(amazon, run):
    response = run("delete_everything")
    assert response["error_code"] == "unsupported_operation"
    assert amazon.requests == []


def test_server_error_on_a_read_is_retried_and_reported(amazon, run):
    for _ in range(4):
        amazon.reply("GET", "/orders/2026-01-01/orders", {}, status=503)
    response = run("search_orders", {"created_after": "2026-11-01T00:00:00Z"})
    assert response["error_code"] == "upstream_failure"
    assert len(amazon.requests) == 4


class _BrokenRead(FakeResponse):
    """A response whose body breaks off mid-read, as http.client reports it."""

    def __init__(self, error: Exception) -> None:
        super().__init__(200, b"")
        self._error = error

    def read(self, size: int = -1) -> bytes:
        raise self._error


def _breaking_opener(amazon, error: Exception):
    real = amazon.opener

    def opener(request, **kwargs):
        real(request, **kwargs)  # records the request and consumes the queued reply
        return _BrokenRead(error)

    return opener


@pytest.mark.parametrize(
    "error", [http.client.IncompleteRead(b"{", 10), http.client.BadStatusLine("garbage")]
)
def test_broken_answer_to_a_mutation_is_an_unknown_outcome(amazon, run, error):
    amazon.opener = _breaking_opener(amazon, error)
    amazon.reply("POST", "/orders/v0/orders/202-1/shipment", {})
    response = run("update_shipment_status", {"order_id": "202-1", "shipment_status": "PickedUp"})
    assert response["error_code"] == "outcome_unknown"
    assert response["definitely_no_external_effect"] is False
    assert response["external_effect_status"] == "unknown"
    assert len(amazon.requests) == 1


def test_broken_answer_to_a_read_is_retried_as_a_connection_failure(amazon, run):
    amazon.opener = _breaking_opener(amazon, http.client.IncompleteRead(b"{", 10))
    for _ in range(4):
        amazon.reply("GET", "/orders/2026-01-01/orders/202-1", {})
    response = run("get_order", {"order_id": "202-1"})
    assert response["error_code"] == "connection_failed"
    assert response["retryable"] is True
    assert len(amazon.requests) == 4


@pytest.mark.parametrize("method", ["download", "upload"])
def test_broken_document_transfer_is_a_connection_failure(amazon, monkeypatch, method):
    monkeypatch.setenv("FS_ALLOW_PRIVATE_REMOTE_URLS", "1")
    url = "https://tortuga-prod-eu.s3-eu-west-1.amazonaws.com/doc-1?X-Amz-Signature=s"
    amazon.reply("GET" if method == "download" else "PUT", "/doc-1", {})
    amazon.opener = _breaking_opener(amazon, http.client.IncompleteRead(b"x", 10))
    connection = connection_from_payload(make_payload("get_order"), connection_ref=CONNECTION_ID)
    transport = amazon.transport_factory()(connection)
    with pytest.raises(ExtensionError) as raised:
        if method == "download":
            transport.download_document(url, max_bytes=1024, purpose="test")
        else:
            transport.upload_document(url, b"{}", content_type="application/json", purpose="test")
    assert raised.value.code == errors.CONNECTION_FAILED


_S3_HOSTS = [
    "tortuga-prod-na.s3-external-1.amazonaws.com",
    "tortuga-prod-eu.s3-eu-west-1.amazonaws.com",
    "bucket.s3.us-east-1.amazonaws.com",
    "s3.eu-west-1.amazonaws.com",
    "selling-partner-definitions-prod-iad.s3.amazonaws.com",
    "bucket.s3.dualstack.us-east-1.amazonaws.com",
]
_NOT_S3_HOSTS = [
    # Load balancer and API names are chosen by their AWS owner and may start with s3-.
    "s3-evil-1.us-east-1.elb.amazonaws.com",
    "s3-evil.execute-api.amazonaws.com",
    "s3.evil.amazonaws.com",
    "ec2-1-2-3-4.compute-1.amazonaws.com",
    "abc123.execute-api.us-east-1.amazonaws.com",
    "nots3.amazonaws.com",
    "s3.amazonaws.com.evil.example",
    "amazonaws.com",
]


@pytest.mark.parametrize("host", _S3_HOSTS)
def test_amazon_s3_document_hosts_are_accepted(host, monkeypatch):
    monkeypatch.setenv("FS_ALLOW_PRIVATE_REMOTE_URLS", "1")
    assert is_amazon_s3_host(host)
    assert is_amazon_s3_host(host.upper())
    url = f"https://{host}/doc?X-Amz-Signature=s"
    assert assert_document_url(url) == url


@pytest.mark.parametrize("host", _NOT_S3_HOSTS)
def test_other_amazonaws_hosts_are_refused_as_document_hosts(host, monkeypatch):
    monkeypatch.setenv("FS_ALLOW_PRIVATE_REMOTE_URLS", "1")
    assert not is_amazon_s3_host(host)
    with pytest.raises(ExtensionError) as raised:
        assert_document_url(f"https://{host}/doc")
    assert raised.value.code == errors.BLOCKED_ADDRESS


def test_every_mutation_rehearses_with_the_effect_facts_only(amazon, run):
    """A suppressed result never puts null into an output that is not nullable."""
    from runtime.catalog import OPERATIONS_BY_ID

    mutations = [row for row in OPERATIONS_BY_ID.values() if row.mutating]
    assert mutations
    for row in mutations:
        minimal = {
            name: _sample(schema)
            for name, schema in row.properties.items()
            if name in row.required and name != "connection_ref"
        }
        options = {}
        if row.marketplaces and "UK" not in row.marketplaces:
            # Rehearse where the method is offered; elsewhere it is refused first.
            minimal["connection_ref"] = "conn-allowed"
            options["target"] = connection_target(
                connection_id="conn-allowed", marketplace=row.marketplaces[0]
            )
        response = run(row.operation_id, minimal, test_mode=True, **options)
        assert response["ok"] is True, (row.operation_id, response)
        assert response["result"] == {
            "external_effect_status": "suppressed",
            "definitely_no_external_effect": True,
            "suppressed": True,
        }
    assert amazon.requests == []


def _sample(schema: dict):
    kind = schema.get("type")
    kind = kind[0] if isinstance(kind, list) else kind
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "integer":
        return schema.get("minimum", 1)
    if kind == "number":
        return schema.get("minimum", 1)
    if kind == "array":
        return [_sample(schema.get("items") or {"type": "string"})] * max(
            1, schema.get("minItems", 1)
        )
    if kind == "object":
        return {
            name: _sample(child)
            for name, child in (schema.get("properties") or {}).items()
            if name in (schema.get("required") or ())
        }
    if kind == "boolean":
        return True
    return "X" * max(1, schema.get("minLength", 1))


#: Hosts that made the old S3-host regex backtrack exponentially.
_PATHOLOGICAL_HOSTS = (
    "s3" + "-a" * 5000 + ".x.amazonaws.co",
    "s3" + "-a" * 100 + ".amazonaws.com",
    "a." * 2000 + "s3.amazonaws.com",
    "s3." + "a-" * 120 + "a.amazonaws.com",
)
#: Generous: the label parser answers in milliseconds, a backtracking regex never.
_HOST_CHECK_DEADLINE_SECONDS = 30
_HOST_CHECK_SCRIPT = """
import json, sys
from runtime.transport import is_amazon_s3_host
hosts = json.loads(sys.stdin.read())
sys.exit(1 if any(is_amazon_s3_host(host) for host in hosts) else 0)
"""


def test_a_pathological_host_is_refused_within_a_bounded_time():
    """The S3 host check cannot backtrack (Sonar python:S5852 on the old regex).

    It runs in a child process with a hard deadline, so a regression that hangs is
    killed and reported instead of stalling the suite, and a busy scheduler cannot
    fail a check that only needs to finish.
    """
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(entry for entry in sys.path if entry))
    try:
        completed = subprocess.run(  # constant argv, no shell
            [sys.executable, "-c", _HOST_CHECK_SCRIPT],
            input=json.dumps(_PATHOLOGICAL_HOSTS),
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=_HOST_CHECK_DEADLINE_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"the S3 host check did not finish within {_HOST_CHECK_DEADLINE_SECONDS} s")
    assert completed.returncode == 0, completed.stderr
