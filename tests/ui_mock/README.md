# TEST / MOCK Amazon UI fixture

An isolated Flow Steward stack in which the Amazon extension can be connected with
several seller accounts **without any Amazon credential and without any request to
Amazon**. It exists for UI acceptance only.

## Why the current stand cannot do this

On the fs-amazon stand the configuration "Test SP-API app" is *Needs attention*
because its LWA client ID and secret are fake: on 2026-11-05 a refresh-token import
on that stand sent them to the real LWA token endpoint, which answered
`invalid_client`, and Core marked the configuration (the stored `last_error.code` is
expected to be `invalid_client`; confirm read-only with
`select status, last_error->>'code' from extension_oauth_configurations where display_name = 'Test SP-API app'`
inside a grant). The guard is correct; nothing here bypasses it.

## What it reuses

Core's existing manual OAuth browser lane (`infra/docker/docker-compose.oauth-browser.yaml`
and `-manual.yaml`): its own Compose project, a default network with
`internal: true`, a per-run CA trusted only inside the project (`SSL_CERT_FILE`),
`FS_STACK_ENVIRONMENT=development`, Traefik on 127.0.0.1 only, seeded test users,
and a verified `down`. **No Core file is changed for this fixture.**

## What it adds (all under `tests/ui_mock/`, inert in the bundle)

| File | Role |
|---|---|
| `mock_amazon.py` | TEST LWA token endpoint and the two SP-API reads a connection check makes, on `*.amazon-mock.test` only; secret-free request log at `/__mock/requests` |
| `preprovision.py` | copies the bundle as `flowsteward.amazon-sp-api-test-mock` ("TEST / MOCK"), points the token endpoint, egress and region hosts at the mock, refuses a copy that still names `amazon.com`/`amazonaws.com` |
| `compose.amazon-mock.yaml` | overlay: source volume (tar of one revision, blob-verified, no bind mount), mock certificate from the lane CA, the mock (no published port), preprovisioning, seeding |
| `seed.py` | `prepare`: enable the TEST / MOCK extension, create project "TEST MOCK Amazon" (seeding only). `exercise`: the real import path through the HTTP API plus egress assertions |
| `run.sh` | `up`, `exercise`, `ca`, `status`, `down` |

The runtime never imports this folder; Core's production image does not contain it
(Core images are built from Core's source slices; the extension bundle is installed
separately and its runtime entry point is `main.py`).

## Canned data (synthetic, all marked TEST MOCK)

- LWA client: `amzn1.application-oa2-client.TESTMOCK…` / `TEST-MOCK-client-secret-not-a-real-secret`.
- Seller A `A1TESTMOCKSELLRA`: `Atzr|TEST-MOCK-seller-a-eu` (UK), `Atzr|TEST-MOCK-seller-a-na` (US, Canada).
- Seller B `A2TESTMOCKSELLRB`: `Atzr|TEST-MOCK-seller-b-eu` (Germany, Poland).
- Store name of both sellers: `TEST MOCK Store`.
- Refusals: `Atzr|TEST-MOCK-refused` (LWA `invalid_grant`); seller A's EU token with
  seller B's ID (wrong seller, 403); seller A's EU token for Poland (wrong
  marketplace, 400); any other client secret (`invalid_client`).

## Run (only inside a separate build/stand grant)

Record the Compose project `fs-amazon-mock-manual-<id>` and the three image tags in
the owner ledger first. Then, from a clean Core checkout at the accepted SHA:

```bash
export FS_COMPOSE_PROJECT_NAME=fs-amazon-mock-manual-ui028
export FS_OAUTH_MANUAL_PORT=18491 FS_OAUTH_MANUAL_PROVIDER_PORT=18492
export FS_AMAZON_MOCK_CORE_ROOT=/path/to/clean/core FS_AMAZON_MOCK_CORE_REVISION=<full core sha>
export FS_AMAZON_MOCK_EXT_REVISION=<full extension sha>
tests/ui_mock/run.sh up        # builds offline, seeds users/extension/project only
tests/ui_mock/run.sh ca        # per-run CA for the auditor's browser profile
tests/ui_mock/run.sh exercise  # optional: API-driven import of the 3 + 3 cases
tests/ui_mock/run.sh down      # removes everything; fails if anything is left
```

`up` refuses a dirty Core checkout or a Core HEAD other than the given SHA, renders
the project and refuses any bind mount, any published port except Traefik's
loopback ports, a published mock port, or a non-internal default network.

## UI cases and where each is proven

| Case | UI (auditor, after `up`) | `exercise` (API, same endpoints) |
|---|---|---|
| Separate TEST LWA configuration, masking, required/invalid | Setup Guide form | creates "TEST MOCK LWA app" |
| Seller A UK + US (same Seller ID), seller B DE, 3 connection IDs | Connection page import ×3 | 3 imports, distinct IDs, all `connected` |
| Same store name stays distinguishable | verified names in list and picker | names = `TEST MOCK Store · Amazon UK/US/DE · <Seller ID>` |
| All 23 marketplaces incl. Germany/Poland/Canada | marketplace select | — |
| Seller ID / marketplace / refresh token labels and help | Connection form | — |
| Wrong seller / wrong marketplace / refused token not healthy | Connection page | 3 refusals, none `connected` |
| UK and US of one seller do not replace each other | list after both imports | 3 distinct IDs remain |
| Workflow picker and switching accounts | workflow step Connection select | — |
| No egress except the mock | — | mock log: only mock hosts, only the two read paths + token |

`prepare` is seeding only (extension enabled, project created). Everything that
proves the import path goes through Core's normal configuration save (encrypted
secret refs), LWA exchange, post-connect check and connection guards; nothing is
written to the database directly and no status is forced.

## Known limits

- Not run yet: needs a Docker/build grant. The unit tests (`tests/test_ui_mock_fixture.py`)
  run in the canonical extension test check.
- The extension's dependencies must install offline in the lane
  (`FS_EXTENSION_DEPS_FETCH=0`); if they do not, `up` stops at `extension-dependencies`.
- Paid or mutating Amazon operations are never exercised; the mock answers only the
  connection check and refuses every other path.
