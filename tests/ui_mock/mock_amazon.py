#!/usr/bin/env python3
"""TEST / MOCK Amazon for the isolated UI fixture. Never a real Amazon endpoint.

It answers only the calls a connection makes, on reserved ``.test`` host names that
cannot resolve on the internet:

* ``lwa.amazon-mock.test``   POST /auth/o2/token (refresh_token grant, LWA shape);
* ``spapi-{na,eu,fe}.amazon-mock.test``  GET /sellers/v1/marketplaceParticipations and
  GET /listings/2021-08-01/items/{sellerId} (searchListingsItems, page size one).

Every other host, method or path is refused and recorded as unexpected. The request
log (``GET /__mock/requests`` on any mock host) never holds a secret, a token or a
body: only host, method, path, query names, the scenario and the answer status. The
canned data is synthetic and marked TEST MOCK; nothing is forwarded anywhere.

The scenario logic (``MockAmazon``) is plain Python so it is unit tested without a
socket; ``main`` serves it over TLS with a certificate from the fixture's per-run CA.
"""

from __future__ import annotations

import argparse
import json
import ssl
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

LWA_HOST = "lwa.amazon-mock.test"
REGION_HOSTS = {
    "NA": "spapi-na.amazon-mock.test",
    "EU": "spapi-eu.amazon-mock.test",
    "FE": "spapi-fe.amazon-mock.test",
}
MOCK_HOSTS = frozenset({LWA_HOST, *REGION_HOSTS.values()})

#: The fake LWA application. Both values are public test strings, not credentials.
CLIENT_ID = "amzn1.application-oa2-client.TESTMOCK00000000000000000000000"
CLIENT_SECRET = "TEST-MOCK-client-secret-not-a-real-secret"  # pragma: allowlist secret

STORE_NAME = "TEST MOCK Store"
SELLER_A = "A1TESTMOCKSELLRA"
SELLER_B = "A2TESTMOCKSELLRB"

#: Marketplace ID, country code, currency (Amazon's published IDs).
MARKETPLACES = {
    "UK": ("A1F83G8C2ARO7P", "GB", "GBP"),
    "DE": ("A1PA6795UKMFR9", "DE", "EUR"),
    "PL": ("A1C3SOZRARQ6R3", "PL", "PLN"),
    "US": ("ATVPDKIKX0DER", "US", "USD"),
    "CA": ("A2EUQ1WTGCTBG2", "CA", "CAD"),
}


@dataclass(frozen=True)
class Grant:
    """What one TEST refresh token stands for: one seller in one selling region."""

    seller_id: str
    region: str
    marketplaces: tuple[str, ...]


#: Seller A in Europe (UK) and North America (US, Canada) with the same Seller ID;
#: seller B in Europe (Germany, Poland). Both use the same store name on purpose.
REFRESH_TOKENS: dict[str, Grant] = {
    "Atzr|TEST-MOCK-seller-a-eu": Grant(SELLER_A, "EU", ("UK",)),
    "Atzr|TEST-MOCK-seller-a-na": Grant(SELLER_A, "NA", ("US", "CA")),
    "Atzr|TEST-MOCK-seller-b-eu": Grant(SELLER_B, "EU", ("DE", "PL")),
}
#: LWA refuses this one with invalid_grant: the refused-token case.
REFUSED_REFRESH_TOKEN = "Atzr|TEST-MOCK-refused"  # pragma: allowlist secret


@dataclass(frozen=True)
class Answer:
    status: int
    body: dict[str, Any]


@dataclass
class MockAmazon:
    """The canned behaviour, with a secret-free log of every request."""

    _access: dict[str, Grant] = field(default_factory=dict)
    _serial: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def handle(
        self,
        *,
        host: str,
        method: str,
        target: str,
        headers: dict[str, str],
        form: dict[str, str] | None = None,
    ) -> Answer:
        split = urlsplit(target)
        query = parse_qs(split.query, keep_blank_values=True)
        name = host.split(":", 1)[0].lower()
        answer, scenario = self._route(name, method, split.path, query, headers, form or {})
        with self._lock:
            self.requests.append(
                {
                    "host": name,
                    "method": method,
                    "path": split.path if name in MOCK_HOSTS else "<refused>",
                    "query": sorted(query),
                    "scenario": scenario,
                    "status": answer.status,
                }
            )
        return answer

    def log(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self.requests]

    # -- routing -------------------------------------------------------------------

    def _route(self, host, method, path, query, headers, form) -> tuple[Answer, str]:
        if host not in MOCK_HOSTS:
            return Answer(
                HTTPStatus.MISDIRECTED_REQUEST, {"error": "not a mock host"}
            ), "unexpected"
        if host == LWA_HOST:
            if method == "POST" and path == "/auth/o2/token":
                return self._token(form)
            return _not_found(), "unexpected"
        region = next(code for code, value in REGION_HOSTS.items() if value == host)
        if method != "GET":
            return _not_found(), "unexpected"
        grant = self._access.get(headers.get("x-amz-access-token", ""))
        if grant is None:
            return _sp_error(
                HTTPStatus.FORBIDDEN, "Unauthorized", "Access token is invalid."
            ), "no_token"
        if grant.region != region:
            return _sp_error(
                HTTPStatus.FORBIDDEN, "Unauthorized", "Wrong selling region."
            ), "wrong_region"
        if path == "/sellers/v1/marketplaceParticipations":
            return Answer(
                HTTPStatus.OK, {"payload": [_participation(code) for code in grant.marketplaces]}
            ), "participations"
        prefix = "/listings/2021-08-01/items/"
        if path.startswith(prefix) and path.count("/") == 4:
            return self._search_listings(grant, path[len(prefix) :], query)
        return _not_found(), "unexpected"

    def _token(self, form: dict[str, str]) -> tuple[Answer, str]:
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            return Answer(
                HTTPStatus.UNAUTHORIZED,
                {"error": "invalid_client", "error_description": "TEST MOCK: unknown client"},
            ), "invalid_client"
        if form.get("grant_type") != "refresh_token":
            return Answer(
                HTTPStatus.BAD_REQUEST, {"error": "unsupported_grant_type"}
            ), "unsupported_grant"
        refresh = form.get("refresh_token", "")
        grant = REFRESH_TOKENS.get(refresh)
        if grant is None:
            scenario = "refused_token" if refresh == REFUSED_REFRESH_TOKEN else "unknown_token"
            return Answer(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_grant", "error_description": "TEST MOCK: token refused"},
            ), scenario
        with self._lock:
            self._serial += 1
            access = f"TEST-MOCK-access-{grant.seller_id}-{grant.region}-{self._serial}"
            self._access[access] = grant
        return Answer(
            HTTPStatus.OK, {"access_token": access, "token_type": "bearer", "expires_in": 3600}
        ), "token_issued"

    @staticmethod
    def _search_listings(
        grant: Grant, seller_id: str, query: dict[str, list[str]]
    ) -> tuple[Answer, str]:
        if seller_id != grant.seller_id:
            return _sp_error(
                HTTPStatus.FORBIDDEN, "Unauthorized", "Access to requested resource is denied."
            ), "wrong_seller"
        wanted = (query.get("marketplaceIds") or [""])[0]
        allowed = {MARKETPLACES[code][0] for code in grant.marketplaces}
        if wanted not in allowed:
            return _sp_error(
                HTTPStatus.BAD_REQUEST, "InvalidInput", "Seller does not sell in this marketplace."
            ), "wrong_marketplace"
        return Answer(HTTPStatus.OK, {"numberOfResults": 0, "items": []}), "seller_confirmed"


def _participation(code: str) -> dict[str, Any]:
    marketplace_id, country, currency = MARKETPLACES[code]
    return {
        "marketplace": {
            "id": marketplace_id,
            "countryCode": country,
            "defaultCurrencyCode": currency,
            "name": f"TEST MOCK {code}",
        },
        "participation": {"isParticipating": True, "hasSuspendedListings": False},
        "storeName": STORE_NAME,
    }


def _not_found() -> Answer:
    return Answer(
        HTTPStatus.NOT_FOUND,
        {"errors": [{"code": "NotFound", "message": "TEST MOCK: not offered"}]},
    )


def _sp_error(status: int, code: str, message: str) -> Answer:
    return Answer(status, {"errors": [{"code": code, "message": f"TEST MOCK: {message}"}]})


def _handler(mock: MockAmazon) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "TestMockAmazon/1"

        def _send(self, answer: Answer) -> None:
            data = json.dumps(answer.body).encode()
            self.send_response(answer.status)
            self.send_header("content-type", "application/json")
            self.send_header("x-amzn-requestid", "TEST-MOCK-request")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _headers(self) -> dict[str, str]:
            return {key.lower(): value for key, value in self.headers.items()}

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if urlsplit(self.path).path == "/__mock/requests":
                self._send(Answer(HTTPStatus.OK, {"requests": mock.log()}))
                return
            if urlsplit(self.path).path == "/__mock/health":
                self._send(Answer(HTTPStatus.OK, {"ok": True}))
                return
            self._send(
                mock.handle(
                    host=self.headers.get("host", ""),
                    method="GET",
                    target=self.path,
                    headers=self._headers(),
                )
            )

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = min(int(self.headers.get("content-length") or 0), 16384)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            form = {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}
            self._send(
                mock.handle(
                    host=self.headers.get("host", ""),
                    method="POST",
                    target=self.path,
                    headers=self._headers(),
                    form=form,
                )
            )

        def log_message(self, _format: str, *_args: object) -> None:
            return  # the secret-free request log is the only record

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container-internal listener
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--cert-file", required=True)
    parser.add_argument("--key-file", required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), _handler(MockAmazon()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert_file, args.key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
