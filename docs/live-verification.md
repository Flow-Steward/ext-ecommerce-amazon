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
- Shipping v2, Fulfillment Inbound 2024-03-20 and v0, FBA Inbound Eligibility v1,
  `deleteListingsItem`, `cancelFeed`, `cancelReport`: request and response shapes
  are compiled from the models in `tests/reference/models` (read 2026-11-06) and
  held by the parity test.
- Shipping v2: AMAZON channel needs `amazonOrderDetails.orderId` and
  `itemIdentifier` = order item ID; one package per request; `TOKEN_EXPIRED`
  after 10 minutes; ON-Amazon labels listed for US, UK, IT, FR, ES (Shipping FAQ);
  the header default is `AmazonShipping_UK`, so the extension always sends it.
- Inbound: no shipment creation in Brazil; partnered carriers in the contiguous
  US per the PCP tutorial (the general guide's EU terms and the India challan
  tutorial are separate statements, not merged into a region list);
  `listPrepDetails` returns only API-set prep; US prep/item-label service ended
  2026-01-01; `listTransportationOptions` is paginated; Pack Later only for pallets; void windows 24 h (SPD) and 1 h (LTL);
  `getLabels`/`getBillOfLading` take the shipment confirmation ID; India
  compliance and challan are IN-only (Amazon docs read 2026-11-06).

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
| Shipping | Whether `purchaseShipment` for an Amazon order marks the order shipped and uploads tracking (Amazon's Shipping v2 pages do not say); whether `cancelShipment` reverts it. Until checked, workflows read the order before calling `confirm_shipment`. |
| Shipping | Whether Amazon honours `x-amzn-IdempotencyKey` on `purchaseShipment` (listed in the model, undocumented). |
| Shipping | ON-Amazon availability beyond US/UK/IT/FR/ES (the model's header enum also lists JP, IN, UAE, SA, EG, which the FAQ lists only for off-Amazon labels). |
| Shipping | Whether the Direct-to-Consumer Shipping role is the only role Amazon checks for Shipping v2 (the roles page lists the v2 operations under it). |
| Inbound documents | That `getLabels`, `getBillOfLading`, `createMarketplaceItemLabels` and `getDeliveryChallanDocument` URLs are on Amazon S3 (only S3 hosts are accepted), and whether `getLabels` returns PDF or ZIP for each page type. `createMarketplaceItemLabels` is expected to return one document. |
| Inbound | That an expired option or quote is reported with wording the extension maps to `offer_expired`, and invalid states with wording mapped to `invalid_state` (otherwise both come back as `upstream_validation_failed` with Amazon's code). |
| Inbound | Self-ship appointments outside India. |

Live mutations (price, quantity, listing, shipment confirmation, feed) must run
only on agreed test SKUs and orders, with the original state read first and
restored afterwards where Amazon allows it. Paid steps (purchase_shipping_shipment,
fba_confirm_transportation_options with a partnered carrier, placement fees) are
never run live without the owner's explicit authorization for that run.
