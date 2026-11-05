# Amazon Selling Partner API extension for Flow Steward

Atomic Amazon SP-API methods for workflows: orders, FBM shipping, catalog, listings,
product type requirements, prices, FBA/FBM stock, listing feeds, finances and reports.

- **Connections.** One connection is one seller account in one marketplace
  (for example *Amazon UK* and *Amazon US*). The API region follows from the
  marketplace. A private SP-API application is used: the seller self-authorizes it
  in Seller Central and the refresh token is imported into Flow Steward, which keeps
  it and renews access tokens. The extension only receives access tokens.
  Requires Flow Steward with the `refresh_token_import` OAuth flow (ADR 0058).
- **Two API versions.** Orders (`2026-01-01` / `v0`) and Finances (`2024-06-19` /
  `v0`) take an explicit `api_version`; current is the default.
- **No hidden behaviour.** One call returns one page; changes are submitted, not
  awaited; unsupported filters are errors; retries are bounded and a change is not
  repeated after an unknown outcome.

See `docs/operation-matrix.md` for every method, the Amazon operations it calls,
the roles it needs and its external effect, and the extension's UI pages
(Setup Guide, Connection, How the methods behave) for setup and behaviour.

## Development

```bash
python3 tests/reference/refresh_contracts.py   # after changing runtime/sections/*
```

Contracts in `contracts/`, `ui/actions/actions.yaml`, the effect and artifact
blocks of `extension.yaml` and `docs/operation-matrix.md` are generated from the
registry; `tests/test_manifest_parity.py` fails when they are stale.
