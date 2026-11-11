from __future__ import annotations

from pathlib import Path

import yaml
from build_contracts import (
    GENERATED_FILES,
    GENERATED_TEXT,
    artifact_policies,
    dump,
    external_effects,
)

from runtime.catalog import OPERATIONS, OPERATIONS_BY_ID
from runtime.connection import CONNECTION_TYPE_ID
from runtime.marketplaces import MARKETPLACE_CODES

ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    return yaml.safe_load((ROOT / "extension.yaml").read_text(encoding="utf-8"))


def test_generated_contracts_match_the_registry():
    for relative, builder in GENERATED_FILES.items():
        assert (ROOT / relative).read_text(encoding="utf-8") == dump(builder()), (
            f"{relative} is stale: run python3 tests/reference/refresh_contracts.py"
        )
    for relative, builder in GENERATED_TEXT.items():
        assert (ROOT / relative).read_text(encoding="utf-8") == builder(), f"{relative} is stale"


def test_manifest_effects_and_artifact_policies_match_the_registry():
    manifest = _manifest()
    assert (manifest.get("external_effects") or []) == external_effects()
    assert (manifest.get("artifact_policies") or []) == artifact_policies()["artifact_policies"]


def test_every_mutation_declares_an_effect_and_every_row_names_its_amazon_calls():
    for row in OPERATIONS:
        assert row.amazon_calls, row.operation_id
        assert row.roles, row.operation_id
        if row.mutating:
            assert row.effect_kind and row.effect_kind.startswith("amazon_"), row.operation_id
        else:
            assert row.effect_kind is None, row.operation_id


def test_effect_kinds_are_unique():
    kinds = [row.effect_kind for row in OPERATIONS if row.mutating]
    assert len(kinds) == len(set(kinds))


def test_post_connect_action_has_the_closed_profile():
    manifest = _manifest()
    provider = manifest["runtime"]["extension_contract_v2"]["oauth_providers"][0]
    row = OPERATIONS_BY_ID[provider["post_connect_action_id"]]
    assert row.workflow_visible is False
    assert row.mutating is False
    assert row.properties == {}


def test_connection_type_lists_exactly_the_supported_marketplaces():
    data = yaml.safe_load(
        (ROOT / "contracts" / "connection_types.yaml").read_text(encoding="utf-8")
    )
    (connection_type,) = data["connection_types"]
    assert connection_type["connection_type_id"] == CONNECTION_TYPE_ID
    marketplace = connection_type["config_schema"]["properties"]["marketplace"]
    assert marketplace["enum"] == list(MARKETPLACE_CODES)
    assert connection_type["auth"]["provider_id"] == "amazon_lwa"


def test_egress_covers_every_host_the_extension_calls():
    hosts = set(_manifest()["egress"]["hosts"])
    assert {
        "api.amazon.com",
        "sellingpartnerapi-na.amazon.com",
        "sellingpartnerapi-eu.amazon.com",
        "sellingpartnerapi-fe.amazon.com",
        "*.amazonaws.com",
    } <= hosts


#: Amazon's official OpenAPI models, copied unchanged as the source the logistics
#: specs are compiled from; they are reference data for the tests, not authoring files.
AMAZON_MODELS = ROOT / "tests" / "reference" / "models"


def test_no_json_authoring_files():
    offenders = [
        path.relative_to(ROOT)
        for path in ROOT.rglob("*.json")
        if ".git" not in path.parts
        and "dev-wheels" not in path.parts
        and path.parent != AMAZON_MODELS
        # The repository build recipe is tooling, not an extension authoring file.
        and path != ROOT / ".github" / "release-config.json"
    ]
    assert offenders == []
