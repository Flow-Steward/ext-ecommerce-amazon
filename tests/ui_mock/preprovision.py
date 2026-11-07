#!/usr/bin/env python3
"""Copy the Amazon bundle into an isolated fixture runtime as a TEST / MOCK variant.

The product bundle is never changed. The copy differs from it in exactly these
places, each checked so that a source change cannot silently leave a real Amazon
host in place:

* ``extension_id`` gains ``-test-mock`` and the display name says TEST / MOCK, so the
  variant can never be mistaken for, or installed over, the real extension;
* the LWA token endpoint and the egress allowlist name only the mock hosts;
* ``runtime/marketplaces.py`` maps each selling region to its mock host.

Afterwards no ``amazon.com`` or ``amazonaws.com`` address is left in the copied
manifest or region table, or the copy is refused (fail closed). A marker file
records the source revision the copy was made from.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import yaml

try:  # imported as a module in tests, run as a script in the fixture container
    from .mock_amazon import LWA_HOST, REGION_HOSTS
except ImportError:  # pragma: no cover - script execution
    from mock_amazon import LWA_HOST, REGION_HOSTS  # type: ignore[no-redef]

PRODUCT_EXTENSION_ID = "flowsteward.amazon-sp-api"
MOCK_EXTENSION_ID = "flowsteward.amazon-sp-api-test-mock"
MOCK_DISPLAY_SUFFIX = " — TEST / MOCK (no real Amazon)"
MARKER = "TEST_MOCK_FIXTURE.json"

_PRODUCTION_REGION_HOSTS = {
    "NA": "sellingpartnerapi-na.amazon.com",
    "EU": "sellingpartnerapi-eu.amazon.com",
    "FE": "sellingpartnerapi-fe.amazon.com",
}
_REAL_HOST_MARKERS = ("amazon.com", "amazonaws.com")


class PreprovisionError(RuntimeError):
    pass


def _rewrite_manifest(path: Path) -> None:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    if manifest.get("extension_id") != PRODUCT_EXTENSION_ID:
        raise PreprovisionError(
            f"expected the {PRODUCT_EXTENSION_ID} bundle, got {manifest.get('extension_id')!r}"
        )
    manifest["extension_id"] = MOCK_EXTENSION_ID
    manifest["display_name"] = (
        f"{manifest.get('display_name') or 'Amazon Selling Partner API'}{MOCK_DISPLAY_SUFFIX}"
    )
    manifest["egress"] = {"hosts": sorted({LWA_HOST, *REGION_HOSTS.values()})}
    providers = manifest["runtime"]["extension_contract_v2"]["oauth_providers"]
    if len(providers) != 1:
        raise PreprovisionError("expected exactly one OAuth provider")
    providers[0]["token_endpoint"]["url"] = f"https://{LWA_HOST}/auth/o2/token"
    providers[0]["display_name"] = f"{providers[0]['display_name']} (TEST / MOCK)"
    text = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    _refuse_real_hosts(text, path.name, ignore=("support_url", "website_url"))
    path.write_text(text, encoding="utf-8")


def _rewrite_region_hosts(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for region, production in _PRODUCTION_REGION_HOSTS.items():
        quoted = f'"{production}"'
        if text.count(quoted) != 1:
            raise PreprovisionError(f"{path.name}: expected one {quoted} for region {region}")
        text = text.replace(quoted, f'"{REGION_HOSTS[region]}"')
    _refuse_real_hosts(text, path.name)
    path.write_text(text, encoding="utf-8")


def _refuse_real_hosts(text: str, name: str, *, ignore: tuple[str, ...] = ()) -> None:
    for line in text.splitlines():
        if any(key in line for key in ignore):
            continue
        stripped = line.split("#", 1)[0]
        if any(marker in stripped for marker in _REAL_HOST_MARKERS):
            raise PreprovisionError(f"{name} still names a real Amazon host: {line.strip()[:120]}")


def preprovision(*, source: Path, extensions_root: Path, revision: str) -> Path:
    source = source.resolve(strict=True)
    destination = extensions_root / "flowsteward_amazon_sp_api_test_mock"
    if destination.exists():
        raise PreprovisionError(
            f"{destination} already exists; the fixture starts from an empty volume"
        )
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(".git", ".fs-python", "__pycache__", "*.pyc", "ui_mock"),
    )
    _rewrite_manifest(destination / "extension.yaml")
    _rewrite_region_hosts(destination / "runtime" / "marketplaces.py")
    (destination / MARKER).write_text(
        json.dumps(
            {
                "fixture": "TEST / MOCK — isolated UI fixture, no real Amazon",
                "source_extension_id": PRODUCT_EXTENSION_ID,
                "source_revision": revision,
                "extension_id": MOCK_EXTENSION_ID,
                "hosts": sorted({LWA_HOST, *REGION_HOSTS.values()}),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="Read-only export of the bundle at one revision"
    )
    parser.add_argument("--extensions-root", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    print(
        preprovision(
            source=args.source, extensions_root=args.extensions_root, revision=args.revision
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
