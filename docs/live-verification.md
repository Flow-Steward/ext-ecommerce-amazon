# What is verified, and what still needs a live seller account

Everything below was built from Amazon's published OpenAPI models
(`amzn/selling-partner-api-models`, read 2026-11-05) and documentation pages, and
tested against a fake Amazon. Nothing has been run against a real seller account
yet. Sandbox and fixtures are not evidence of live behaviour.

## Verified against Amazon's models and docs

- Every path, query parameter, request body and response path used by the
  extension (OpenAPI models).
- Marketplace IDs and regional endpoints (Marketplace IDs page updated
  2026-10-23; SP-API endpoints page updated 2026-09-09).
- LWA token endpoint and refresh body; `x-amz-access-token` header.
- Removal dates: Orders v0 reads 2027-03-27; Finances v0 events 2027-08-27;
  legacy settlement XML/flat file 2026-11-11 (deprecations page updated
  2026-09-09).
- Settlement reports cannot be requested or scheduled (report type values page).
- JSON_LISTINGS_FEED v2 header and message rules, and the processing report
  shape (Amazon's published JSON schemas).
- Listings: put/patch responses mean "accepted for processing"; issues after
  acceptance appear only in `getListingsItem`.

## Needs a live check

| Area | What is unconfirmed |
|---|---|
| Connection | That `searchListingsItems` with a wrong Seller ID answers 400/403 (used to confirm the Seller ID). |
| Finances | Whether v0 financial event group IDs equal 2024-06-19 `FINANCIAL_EVENT_GROUP_ID` values (`list_financial_events_by_group` defaults to 2024-06-19). |
| Finances | US MFN orders need `omit_marketplace_filter` with 2024-06-19 `listTransactions` (Amazon's note); behaviour in other marketplaces. |
| Listings | `merchant_suggested_asin` value shape and the `PRODUCT` default product type for offer-only listings. |
| Pricing | Full shape of a B2B `our_price` entry in `purchasable_offer` (the `audience: B2B` selector is documented, a complete example is not). |
| Inventory | Whether `fulfillment_availability` needs `marketplace_id` in a merge patch. |
| Orders | That Orders 2026-01-01 returns `fulfillment` and `proceeds` (order and item level) only when `includedData` names FULFILLMENT and PROCEEDS, as the model's optional sections suggest; the extension always asks for both. |
| Documents | That every presigned feed, report and schema URL Amazon returns is on an Amazon S3 host (`*.s3*.amazonaws.com`); any other `amazonaws.com` host is refused. |
| Requirements | That product type schema links are signed S3 URLs (inferred from "valid for seven days"), and how many product types exceed the 2.5 MB inline schema cap without `parentage_level`. |
| Reports | Character encoding of flat files per marketplace (files are stored unchanged). |
| Roles | Role names per operation in the matrix (not part of the OpenAPI models). |
| Rate limits | Behaviour under real throttling with several connections of one seller in a region. |

Live mutations (price, quantity, listing, shipment confirmation, feed) must run
only on agreed test SKUs and orders, with the original state read first and
restored afterwards where Amazon allows it.
