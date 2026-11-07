# Amazon Selling Partner API extension for Flow Steward

Connect Amazon seller operations to Flow Steward workflows across order handling, catalog maintenance, fulfillment and financial reporting. Read orders and order items, retrieve permitted buyer information, confirm merchant-fulfilled shipments and update shipment status. Search Amazon catalog items and seller listings, inspect listing issues, retrieve product type requirements, check restrictions and validate proposed listing changes before submitting them.

Catalog and inventory steps can create, update or delete listings, change offer prices and merchant-fulfilled quantities, read FBA inventory and compare offers and competitive pricing. FBA inbound operations cover plans, packing and placement choices, transportation, delivery windows, shipment details and shipping documents, with additional India compliance and challan operations. Amazon Shipping steps retrieve rates, purchase or cancel shipments, download labels and read tracking in supported countries.

Use listing feeds for batch submissions, inspect their processing status and download results. Finance steps retrieve transactions, order-related financial records and legacy financial event groups; report steps request, inspect and download Amazon reports, including eligible settlement reports. These building blocks suit stock updates, shipment processing and accounting exports. Each connection selects one seller and marketplace. Lists return a page, and asynchronous submissions need later status checks; roles and marketplace availability determine which operations can run.

Use Flow Steward 0.9.12 or later and configure a private SP-API application with its LWA credentials. Self-authorize the seller in Seller Central, import its refresh token, and select the seller ID and marketplace; grant Product Listing for connection verification and add the roles needed by the operations you use.

- **Connections.** One connection is one seller account in one marketplace
  (for example *Amazon UK* and *Amazon US*). The API region follows from the
  marketplace. A private SP-API application is used: the seller self-authorizes it
  in Seller Central and the refresh token is imported into Flow Steward, which keeps
  it and renews access tokens. The extension only receives access tokens.
  Requires Flow Steward 0.9.12 or later (technically: host contract 1.6.0,
  `runtime.compatibility.platform_min`, which Flow Steward 0.9.12 ships), for the
  `refresh_token_import` OAuth flow (ADR 0058).
- **Verified connection name.** After connecting, Flow Steward shows the name the
  extension confirmed with Amazon, *store name · Amazon marketplace · Seller ID*
  (*Amazon marketplace · Seller ID* without a store name), instead of the typed
  one. It is what the workflow connection picker shows, so the marketplace and
  Seller ID are always part of it; only a long store name is shortened.
- **Roles.** Every connection needs *Product Listing* (Amazon also accepts
  *Inventory and Order Tracking*), even for finance-only use: connecting checks the
  Seller ID with Listings Items `searchListingsItems`. Other roles follow from the
  methods used (Operation matrix).
- **Two API versions.** Orders (`2026-01-01` / `v0`) and Finances (`2024-06-19` /
  `v0`) take an explicit `api_version`; current is the default.
- **No hidden behaviour.** Each method performs one explicit action (most are one
  Amazon call; the few technical extra calls are listed in the matrix). A list
  returns one page; changes are submitted, not awaited; unsupported filters are errors; retries are bounded and a change is not
  repeated after an unknown outcome.

- **Where methods work.** Every method declares its marketplaces; Amazon Shipping
  labels run in US/UK/IT/FR/ES, FBA inbound everywhere but Brazil, the India steps
  only in India. Elsewhere a method answers `unsupported_marketplace` without
  calling Amazon.

Amazon's documentation: [self-authorization](https://developer-docs.amazon/sp-api/docs/self-authorization),
[marketplace IDs](https://developer-docs.amazon/sp-api/docs/marketplace-ids),
[Amazon Shipping API v2](https://developer-docs.shipping.amazon.com/apis/docs/shipping-api-v2-reference),
[Fulfillment Inbound API](https://developer-docs.amazon/sp-api/docs/fulfillment-inbound-api),
[creating an inbound shipment](https://developer-docs.amazon/sp-api/docs/create-an-inbound-shipment),
[partnered-carrier shipments](https://developer-docs.amazon/sp-api/docs/create-amazon-partnered-carrier-shipment),
[inbound to India](https://developer-docs.amazon/sp-api/docs/ship-inventory-to-india-fc).
FBA notes from those pages: `fba_list_prep_details` returns only prep details set
with `fba_set_prep_details` (not Seller Central's); since January 1, 2026 the US
store has no Amazon prep or item label service; `fba_list_transportation_options`
returns one page, so read every page before concluding no partnered option exists.

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

## CI and releases

Pull requests and pushes to `main` run the shared extension build workflow. The
repository recipe is `.github/release-config.json`: it selects the test
requirements, test paths and portable source package. Shared tooling installs
the verified Flow Steward extension SDK wheel before the development requirements.

After the release changes are merged, create a new tag matching `extension.yaml`:

```sh
git tag v0.2.11
git push origin v0.2.11
```

To run the same release pipeline manually for that immutable tag:

```sh
gh workflow run release.yml --ref main -f tag=v0.2.11
```

The release caller builds, tests and packages the tagged source through the shared
workflow. Keep published tags immutable; release a new patch version for later
changes rather than moving an existing tag or uploading a replacement ZIP.
