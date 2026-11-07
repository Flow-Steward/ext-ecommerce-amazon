"""The TEST / MOCK UI fixture: canned Amazon answers and the copied bundle.

Only the fixture's own logic is tested here; the live stack is started by
tests/ui_mock/run.sh inside a separate Docker grant.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ui_mock import mock_amazon, preprovision
from ui_mock.mock_amazon import (
    CLIENT_ID,
    CLIENT_SECRET,
    LWA_HOST,
    REGION_HOSTS,
    SELLER_A,
    SELLER_B,
    MockAmazon,
)

ROOT = Path(__file__).resolve().parents[1]
UK = mock_amazon.MARKETPLACES["UK"][0]
US = mock_amazon.MARKETPLACES["US"][0]
DE = mock_amazon.MARKETPLACES["DE"][0]
PL = mock_amazon.MARKETPLACES["PL"][0]


def _token(mock: MockAmazon, refresh: str, *, client_secret: str = CLIENT_SECRET):
    return mock.handle(
        host=LWA_HOST,
        method="POST",
        target="/auth/o2/token",
        headers={},
        form={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": CLIENT_ID,
            "client_secret": client_secret,
        },
    )


def _get(mock: MockAmazon, region: str, target: str, access: str):
    return mock.handle(
        host=REGION_HOSTS[region],
        method="GET",
        target=target,
        headers={"x-amz-access-token": access},
    )


def _check(mock: MockAmazon, refresh: str, region: str, seller: str, marketplace_id: str):
    access = _token(mock, refresh).body["access_token"]
    participations = _get(mock, region, "/sellers/v1/marketplaceParticipations", access)
    listings = _get(
        mock,
        region,
        f"/listings/2021-08-01/items/{seller}?marketplaceIds={marketplace_id}&pageSize=1",
        access,
    )
    return participations, listings


def test_one_seller_in_two_regions_and_a_second_seller_share_a_store_name():
    mock = MockAmazon()
    uk = _check(mock, "Atzr|TEST-MOCK-seller-a-eu", "EU", SELLER_A, UK)
    us = _check(mock, "Atzr|TEST-MOCK-seller-a-na", "NA", SELLER_A, US)
    de = _check(mock, "Atzr|TEST-MOCK-seller-b-eu", "EU", SELLER_B, DE)
    for participations, listings in (uk, us, de):
        assert participations.status == 200 and listings.status == 200
        assert {row["storeName"] for row in participations.body["payload"]} == {"TEST MOCK Store"}
    assert [row["marketplace"]["id"] for row in us[0].body["payload"]] == [
        US,
        mock_amazon.MARKETPLACES["CA"][0],
    ]


@pytest.mark.parametrize(
    ("refresh", "region", "seller", "marketplace_id", "scenario"),
    [
        ("Atzr|TEST-MOCK-seller-a-eu", "EU", SELLER_B, UK, "wrong_seller"),
        ("Atzr|TEST-MOCK-seller-a-eu", "EU", SELLER_A, PL, "wrong_marketplace"),
    ],
)
def test_a_wrong_seller_or_marketplace_is_refused_by_the_seller_check(
    refresh, region, seller, marketplace_id, scenario
):
    mock = MockAmazon()
    _participations, listings = _check(mock, refresh, region, seller, marketplace_id)
    assert listings.status in {400, 403}
    assert mock.log()[-1]["scenario"] == scenario


def test_lwa_refuses_a_refused_token_an_unknown_token_and_a_wrong_client():
    mock = MockAmazon()
    assert _token(mock, mock_amazon.REFUSED_REFRESH_TOKEN).body["error"] == "invalid_grant"
    assert _token(mock, "Atzr|real-looking-token").body["error"] == "invalid_grant"
    wrong = _token(mock, "Atzr|TEST-MOCK-seller-a-eu", client_secret="other")
    assert (wrong.status, wrong.body["error"]) == (401, "invalid_client")


def test_a_token_works_only_in_its_own_region_and_only_on_mock_hosts():
    mock = MockAmazon()
    access = _token(mock, "Atzr|TEST-MOCK-seller-a-eu").body["access_token"]
    assert _get(mock, "NA", "/sellers/v1/marketplaceParticipations", access).status == 403
    real = mock.handle(
        host="sellingpartnerapi-eu.amazon.com",
        method="GET",
        target="/sellers/v1/marketplaceParticipations",
        headers={"x-amz-access-token": access},
    )
    assert real.status == 421
    assert _get(mock, "EU", "/orders/v0/orders", access).status == 404
    scenarios = [row["scenario"] for row in mock.log()]
    assert scenarios[-3:] == ["wrong_region", "unexpected", "unexpected"]


def test_the_request_log_never_holds_a_secret_or_token():
    mock = MockAmazon()
    _check(mock, "Atzr|TEST-MOCK-seller-a-eu", "EU", SELLER_A, UK)
    text = json.dumps(mock.log())
    assert CLIENT_SECRET not in text and "Atzr|" not in text and "TEST-MOCK-access" not in text
    assert {row["host"] for row in mock.log()} <= mock_amazon.MOCK_HOSTS


def test_the_copied_bundle_is_marked_test_mock_and_names_no_real_amazon_host(tmp_path):
    destination = preprovision.preprovision(
        source=ROOT, extensions_root=tmp_path, revision="0" * 40
    )
    manifest = yaml.safe_load((destination / "extension.yaml").read_text(encoding="utf-8"))
    assert manifest["extension_id"] == preprovision.MOCK_EXTENSION_ID
    assert "TEST / MOCK" in manifest["display_name"]
    provider = manifest["runtime"]["extension_contract_v2"]["oauth_providers"][0]
    assert provider["token_endpoint"]["url"] == f"https://{LWA_HOST}/auth/o2/token"
    assert set(manifest["egress"]["hosts"]) == mock_amazon.MOCK_HOSTS
    regions = (destination / "runtime" / "marketplaces.py").read_text(encoding="utf-8")
    assert "amazon.com" not in regions and all(host in regions for host in REGION_HOSTS.values())
    assert not (destination / "tests" / "ui_mock").exists()
    marker = json.loads((destination / preprovision.MARKER).read_text(encoding="utf-8"))
    assert marker["source_revision"] == "0" * 40
    # The product bundle itself is untouched.
    assert (
        yaml.safe_load((ROOT / "extension.yaml").read_text(encoding="utf-8"))["extension_id"]
        == preprovision.PRODUCT_EXTENSION_ID
    )


def test_a_copy_that_would_keep_a_real_host_is_refused(tmp_path):
    source = tmp_path / "source"
    (source / "runtime").mkdir(parents=True)
    manifest = yaml.safe_load((ROOT / "extension.yaml").read_text(encoding="utf-8"))
    manifest["description"] = "calls https://sellingpartnerapi-eu.amazon.com directly"
    (source / "extension.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (source / "runtime" / "marketplaces.py").write_text(
        (ROOT / "runtime" / "marketplaces.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(preprovision.PreprovisionError, match="real Amazon host"):
        preprovision.preprovision(
            source=source, extensions_root=tmp_path / "out", revision="1" * 40
        )
