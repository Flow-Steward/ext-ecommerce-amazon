# Amazon Selling Partner API extension for Flow Steward

Atomic Amazon SP-API methods for workflows: orders, FBM shipment confirmation and
Amazon Shipping labels, FBA inbound shipments (Fulfillment Inbound 2024-03-20,
including India), catalog, listings, product type requirements, prices, FBA/FBM
stock, listing feeds, finances and reports. You build the processes; no ready-made
workflows are shipped.

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

- **Where methods work.** Every method declares its marketplaces; Amazon Shipping
  labels run in US/UK/IT/FR/ES, FBA inbound everywhere but Brazil, the India steps
  only in India. Elsewhere a method answers `unsupported_marketplace` without
  calling Amazon.

See `docs/live-verification.md` for what still needs a live seller account,
`docs/operation-matrix.md` for every method (Amazon operation, roles, marketplaces,
external effect, personal data, files), `docs/platform-gaps.md` for what the
platform does not yet do with personal data, and the extension's UI pages (Setup
Guide, Connection, How the methods behave, Operation matrix).

## Development

```bash
python3 tests/reference/refresh_contracts.py   # after changing runtime/sections/*
```

Contracts in `contracts/`, `ui/actions/actions.yaml`, `ui/pages/operation-matrix.yaml`,
the effect and artifact blocks of `extension.yaml` and `docs/operation-matrix.md`
are generated from the registry; `runtime/amazon_specs.py` is compiled from
Amazon's official models in `tests/reference/models`. The parity tests fail when
any of them is stale.
