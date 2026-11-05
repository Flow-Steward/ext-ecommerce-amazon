from __future__ import annotations

import json
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
if str(BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(BUNDLE_ROOT))
GENERATOR_ROOT = BUNDLE_ROOT / "tests" / "reference"
if str(GENERATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(GENERATOR_ROOT))

from runtime import operations  # noqa: E402
from runtime.catalog import OPERATIONS_BY_ID  # noqa: E402
from runtime.errors import ExtensionError  # noqa: E402
from runtime.transport import SpApiTransport  # noqa: E402
from runtime.validation import validate  # noqa: E402

CONNECTION_ID = "conn-amazon-uk"
SELLER_ID = "A1SELLER0TEST1"
ACCESS_TOKEN = "Atza|fixture-access-token-0001"  # pragma: allowlist secret
EXTERNAL_EFFECT_ID = "6a3f1c2e-7b45-4d0a-9c11-2f8e5b7a0d31"
HOST_CONTRACT_VERSION = "extension_host_v2"
ROOT_STEP_SCOPE: dict[str, Any] = {
    "group_id": None,
    "for_each_id": None,
    "step_id": "amazon_step",
    "step_execution_id": "5c1d2e3f-4a5b-4c6d-8e7f-000000000001",
}

UK_MARKETPLACE_ID = "A1F83G8C2ARO7P"
US_MARKETPLACE_ID = "ATVPDKIKX0DER"


class FakeResponse:
    """A deterministic stand-in for one urllib response."""

    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.code = status
        self._buffer = body
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._buffer = self._buffer, b""
            return chunk
        chunk, self._buffer = self._buffer[:size], self._buffer[size:]
        return chunk

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(type(value))


class Recorded:
    """One request the fake opener saw."""

    def __init__(self, request: Any) -> None:
        self.method = request.get_method()
        self.url = request.full_url
        parsed = urlparse(self.url)
        self.host = parsed.hostname
        self.path = parsed.path
        self.query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        self.headers = {key.lower(): value for key, value in request.header_items()}
        self.body = (
            json.loads(request.data)
            if request.data and self.headers.get("content-type", "").startswith("application/json")
            else request.data
        )


class FakeAmazon:
    """Routes requests to queued responses by (method, path) and records them.

    ``reply(method, path, body, status=200)`` queues one response; a route may be
    queued several times and is answered in order. A request with no queued
    response fails the test loudly.
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[tuple[int, bytes, dict[str, str]]]] = {}
        self.requests: list[Recorded] = []

    def reply(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> None:
        payload = (
            raw
            if raw is not None
            else json.dumps(body if body is not None else {}, default=_default).encode()
        )
        self.routes.setdefault((method, path), []).append((status, payload, headers or {}))

    def opener(self, request: Any, *, timeout_seconds: float, purpose: str = "") -> FakeResponse:
        recorded = Recorded(request)
        self.requests.append(recorded)
        key = (recorded.method, recorded.path)
        queue = self.routes.get(key)
        if not queue:
            raise AssertionError(f"unexpected request {key}")
        status, body, headers = queue.pop(0)
        if status >= 400:
            raise HTTPError(recorded.url, status, "error", headers, _BytesReader(body))  # type: ignore[arg-type]
        return FakeResponse(status, body, headers)

    def transport_factory(self) -> Callable[..., SpApiTransport]:
        def build(connection: Any) -> SpApiTransport:
            return SpApiTransport(
                connection, opener=self.opener, sleep=lambda _s: None, jitter=lambda a, b: 0.0
            )

        return build


class _BytesReader:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, size: int = -1) -> bytes:
        chunk, self._body = (
            (self._body, b"")
            if size is None or size < 0
            else (self._body[:size], self._body[size:])
        )
        return chunk

    def close(self) -> None:
        return None


def connection_target(
    *,
    connection_id: str = CONNECTION_ID,
    seller_id: str = SELLER_ID,
    marketplace: str = "UK",
    access_token: str = ACCESS_TOKEN,
    subject: str | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {"seller_id": seller_id, "marketplace": marketplace}
    if subject is not None:
        config["oauth"] = {"subject": subject}
    return {
        "connection_id": connection_id,
        "connection": {
            "connection_id": connection_id,
            "connection_type_id": "amazon_seller",
            "config": config,
            "secrets": {"access_token": access_token},
        },
    }


def make_payload(
    operation_id: str,
    operation_input: dict[str, Any] | None = None,
    *,
    target: dict[str, Any] | None = None,
    test_mode: bool = False,
    effect_id: str | None = EXTERNAL_EFFECT_ID,
) -> dict[str, Any]:
    runtime_context: dict[str, Any] = {"test_mode": test_mode}
    if effect_id is not None:
        runtime_context["external_effect_id"] = effect_id
    data = {"connection_ref": CONNECTION_ID}
    data.update(operation_input or {})
    return {
        "contract_version": HOST_CONTRACT_VERSION,
        "mode": "action",
        "scope": {
            "account_id": "acct-1",
            "project_id": "proj-1",
            "workflow_id": "wf-1",
            "job_id": "job-1",
            **ROOT_STEP_SCOPE,
        },
        "runtime_context": runtime_context,
        "action": {
            "action_id": operation_id,
            "input": data,
            "target": target or connection_target(),
        },
    }


@pytest.fixture
def amazon() -> FakeAmazon:
    return FakeAmazon()


@pytest.fixture
def run(amazon: FakeAmazon) -> Callable[..., dict[str, Any]]:
    """Run one operation through the real dispatcher against the fake Amazon."""

    def invoke(
        operation_id: str,
        operation_input: dict[str, Any] | None = None,
        *,
        artifact_reader: Callable[..., bytes] | None = None,
        artifact_writer: Callable[..., dict[str, Any]] | None = None,
        **payload_options: Any,
    ) -> dict[str, Any]:
        payload = make_payload(operation_id, operation_input, **payload_options)
        kwargs: dict[str, Any] = {"transport_factory": amazon.transport_factory()}
        if artifact_reader is not None:
            kwargs["artifact_reader"] = artifact_reader
        if artifact_writer is not None:
            kwargs["artifact_writer"] = artifact_writer
        response = operations.handle_runtime(payload, **kwargs)
        json.dumps(response)  # every response must be JSON-serializable
        assert ACCESS_TOKEN not in json.dumps(response)
        assert_result_matches_outputs(operation_id, response)
        return response

    return invoke


def assert_result_matches_outputs(operation_id: str, response: dict[str, Any]) -> None:
    """Every successful result carries each declared output, valid against its schema.

    The published contract tells the platform and the workflow what a step
    returns; a result that drops a field or puts a null into a non-nullable one
    breaks every step downstream that reads it. Errors (``ok`` false) carry no
    result fields and are not checked.

    A suppressed mutation (test mode) is exempt on purpose: nothing was sent to
    Amazon, so there is no SKU status, feed ID or shipment to report. The
    rehearsal returns only the effect facts (``external_effect_status``
    "suppressed", ``definitely_no_external_effect`` true, ``suppressed`` true),
    which is how the platform recognizes a rehearsal; filling the outputs with
    nulls or invented values would be a lie the schema cannot express.
    """
    if response.get("ok") is not True:
        return
    row = OPERATIONS_BY_ID.get(operation_id)
    if row is None:
        return
    result = response["result"]
    if result.get("external_effect_status") == "suppressed":
        assert row.mutating, f"{operation_id} is not a mutation but returned a suppressed result"
        assert result == {
            "external_effect_status": "suppressed",
            "definitely_no_external_effect": True,
            "suppressed": True,
        }
        return
    for output in row.outputs:
        assert output.name in result, f"{operation_id}: result has no {output.name}"
        try:
            validate(result[output.name], output.schema, f"result.{output.name}")
        except ExtensionError as exc:
            raise AssertionError(f"{operation_id}: {exc.message}") from None
