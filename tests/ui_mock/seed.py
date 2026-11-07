#!/usr/bin/env python3
"""Prepare, and optionally exercise, the TEST / MOCK Amazon fixture in an isolated stack.

Runs inside the fixture's Compose project after Core's OAuth browser seed has created
its acceptance admin and operator. Two separate stages, reported separately:

``prepare`` (seeding only)
    enables the TEST / MOCK extension and creates the project "TEST MOCK Amazon". No
    OAuth configuration and no connection is created: those are for the auditor to
    make in the UI, through the normal Setup Guide and Connection pages.

``exercise`` (the real import path, API-driven)
    makes, through the same HTTP endpoints the UI calls, a TEST LWA configuration and
    three connections from TEST refresh tokens (seller A in UK and US, seller B in
    DE), then the three refusals (wrong seller, wrong marketplace, refused token).
    Core performs its normal work: encrypted secret refs, the LWA exchange (answered by
    the mock), the extension's post-connect check (answered by the mock) and the
    connection guards. Nothing is written to the database directly. It then reads the
    mock's request log and fails unless every request went to a mock host, only the
    expected paths were called, and no unexpected request was seen.

Either stage writes a JSON report without any secret or token.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_amazon import (  # noqa: E402
    CLIENT_ID,
    CLIENT_SECRET,
    LWA_HOST,
    MOCK_HOSTS,
    REFUSED_REFRESH_TOKEN,
    SELLER_A,
    SELLER_B,
    STORE_NAME,
)
from preprovision import MOCK_EXTENSION_ID  # noqa: E402

# Core's OAuth browser seed creates these test users in the isolated stack.
ADMIN = ("oauth-admin@example.test", "OAuthAcceptanceAdmin_123!")  # pragma: allowlist secret
OPERATOR = (
    "oauth-operator@example.test",
    "OAuthAcceptanceOperator_123!",
)  # pragma: allowlist secret
PROJECT_NAME = "TEST MOCK Amazon"
PROVIDER_ID = "amazon_lwa"
CONNECTION_TYPE = "amazon_seller"

#: name, refresh token, Seller ID, marketplace code, expected import outcome.
GOOD = (
    ("TEST MOCK seller A UK", "Atzr|TEST-MOCK-seller-a-eu", SELLER_A, "UK"),
    ("TEST MOCK seller A US", "Atzr|TEST-MOCK-seller-a-na", SELLER_A, "US"),
    ("TEST MOCK seller B DE", "Atzr|TEST-MOCK-seller-b-eu", SELLER_B, "DE"),
)
REFUSED = (
    ("TEST MOCK wrong seller", "Atzr|TEST-MOCK-seller-a-eu", SELLER_B, "UK"),
    ("TEST MOCK wrong marketplace", "Atzr|TEST-MOCK-seller-a-eu", SELLER_A, "PL"),
    ("TEST MOCK refused token", REFUSED_REFRESH_TOKEN, SELLER_A, "UK"),
)
EXPECTED_PATHS = {
    (LWA_HOST, "POST", "/auth/o2/token"),
}


def _call(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {response.status_code}")
    return response.json() if response.content else {}


def _login(base_url: str, credentials: tuple[str, str]) -> httpx.Client:
    client = httpx.Client(base_url=base_url, verify=True, timeout=60)
    payload = _call(
        client,
        "POST",
        "/api/auth/login",
        json={"email": credentials[0], "password": credentials[1], "remember_me": False},
    )
    client.headers["x-csrf-token"] = str(payload["csrf_token"])
    return client


def _install_id(admin: httpx.Client) -> str:
    detail = _call(admin, "GET", f"/api/extensions/{MOCK_EXTENSION_ID}")
    if not detail.get("active"):
        _call(admin, "POST", f"/api/extensions/{MOCK_EXTENSION_ID}/enable")
        detail = _call(admin, "GET", f"/api/extensions/{MOCK_EXTENSION_ID}")
    install_id = str(detail.get("install_id") or detail.get("extension_install_id") or "")
    if not install_id:
        raise RuntimeError("the TEST / MOCK extension has no install id")
    return install_id


def _project_id(admin: httpx.Client) -> str:
    listed = _call(admin, "GET", "/api/v2/projects?page=1&per_page=200")
    for row in listed.get("projects", []):
        if row.get("name") == PROJECT_NAME:
            return str(row.get("id") or row.get("project_id"))
    workspaces = _call(admin, "GET", "/api/workspaces")
    rows = workspaces if isinstance(workspaces, list) else workspaces.get("workspaces", [])
    created = _call(
        admin,
        "POST",
        "/api/projects",
        json={
            "name": PROJECT_NAME,
            "description": "TEST / MOCK — isolated UI fixture, no real Amazon",
            "workspace_id": str(rows[0].get("id") or rows[0].get("workspace_id")),
        },
    )
    return str(created.get("id") or created.get("project_id"))


def prepare(base_url: str) -> dict[str, Any]:
    with _login(base_url, ADMIN) as admin:
        return {
            "stage": "prepare (seeding only)",
            "extension_id": MOCK_EXTENSION_ID,
            "install_id": _install_id(admin),
            "project_id": _project_id(admin),
        }


def _import(
    operator: httpx.Client,
    prefix: str,
    configuration_id: str,
    name: str,
    token: str,
    seller: str,
    market: str,
) -> dict[str, Any]:
    response = operator.post(
        f"{prefix}/connections/import",
        json={
            "configuration_id": configuration_id,
            "connection_type": CONNECTION_TYPE,
            "display_name": name,
            "refresh_token": token,
            "connection_config": {"seller_id": seller, "marketplace": market},
        },
    )
    body = response.json() if response.content else {}
    return {
        "typed_name": name,
        "status": response.status_code,
        "outcome": body.get("outcome"),
        "connection_id": body.get("connection_id"),
        "error": body.get("error") or body.get("code"),
    }


def exercise(base_url: str, mock_log_url: str, prepared: dict[str, Any]) -> dict[str, Any]:
    prefix = f"/api/projects/{prepared['project_id']}/extensions/{prepared['install_id']}/oauth/{PROVIDER_ID}"
    with _login(base_url, OPERATOR) as operator:
        configuration = _call(
            operator,
            "POST",
            f"{prefix}/configurations",
            json={
                "display_name": "TEST MOCK LWA app",
                "values": {"client_id": CLIENT_ID},
                "secret_replacements": {"client_secret": CLIENT_SECRET},
            },
        )
        configuration_id = configuration["configuration_id"]
        good = [_import(operator, prefix, configuration_id, *case) for case in GOOD]
        refused = [_import(operator, prefix, configuration_id, *case) for case in REFUSED]
        connections = _call(operator, "GET", f"/api/projects/{prepared['project_id']}/connections")
    rows = connections if isinstance(connections, list) else connections.get("connections", [])
    failures: list[str] = []
    ids = [row["connection_id"] for row in good]
    if len(set(filter(None, ids))) != 3 or any(row["outcome"] != "connected" for row in good):
        failures.append("the three good imports did not each create their own connected connection")
    names = {str(row.get("display_name")) for row in rows if row.get("connection_id") in ids}
    expected = {
        f"{STORE_NAME} · Amazon UK · {SELLER_A}",
        f"{STORE_NAME} · Amazon US · {SELLER_A}",
        f"{STORE_NAME} · Amazon DE · {SELLER_B}",
    }
    if names != expected:
        failures.append(f"verified names differ: {sorted(names)}")
    healthy_refused = [row for row in refused if row["outcome"] == "connected"]
    if healthy_refused:
        failures.append(
            f"a refusal case produced a connected connection: {[row['typed_name'] for row in healthy_refused]}"
        )
    log = httpx.get(mock_log_url, verify=True, timeout=10).json()["requests"]
    stray = [row for row in log if row["host"] not in MOCK_HOSTS or row["scenario"] == "unexpected"]
    if stray:
        failures.append(f"unexpected egress seen by the mock: {stray}")
    called = {
        (
            row["host"],
            row["method"],
            row["path"].rsplit("/items/", 1)[0]
            + ("/items/{sellerId}" if "/items/" in row["path"] else ""),
        )
        for row in log
    }
    allowed = EXPECTED_PATHS | {
        (host, "GET", path)
        for host in MOCK_HOSTS - {LWA_HOST}
        for path in (
            "/sellers/v1/marketplaceParticipations",
            "/listings/2021-08-01/items/{sellerId}",
        )
    }
    if not called <= allowed:
        failures.append(
            f"paths outside the connection check were called: {sorted(called - allowed)}"
        )
    return {
        "stage": "exercise (real import path through the HTTP API; external answers from the mock)",
        "configuration_id": configuration_id,
        "good": good,
        "refused": refused,
        "verified_names": sorted(names),
        "mock_requests": len(log),
        "mock_scenarios": sorted({row["scenario"] for row in log}),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "exercise"))
    parser.add_argument("--base-url", default="https://traefik:8099")
    parser.add_argument("--mock-log-url", default=f"https://{LWA_HOST}/__mock/requests")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report: dict[str, Any] = {
        "fixture": "TEST / MOCK — no real Amazon",
        "prepare": prepare(args.base_url),
    }
    if args.stage == "exercise":
        report["exercise"] = exercise(args.base_url, args.mock_log_url, report["prepare"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    failures = report.get("exercise", {}).get("failures", [])
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
