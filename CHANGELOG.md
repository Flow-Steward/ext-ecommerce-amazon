# Changelog

## 0.2.0 — unreleased

- FBM Amazon Shipping v2 for Amazon orders: get_shipping_rates,
  get_shipping_additional_inputs, purchase_shipping_shipment,
  get_shipping_documents, get_shipping_tracking, cancel_shipping_shipment
  (US, UK, IT, FR, ES; Amazon Shipping business from the connection).
- FBA inbound (Fulfillment Inbound 2024-03-20): 34 fba_* methods, one per
  Amazon operation, plus fba_get_item_eligibility_preview and the Inbound v0
  documents fba_get_labels and fba_get_bill_of_lading.
- India: compliance details, self-ship appointments, delivery challan.
- delete_listing, cancel_feed, cancel_report.
- Labels, bills of lading and challans are stored as files with their real name
  and type (ADR 0045 presentation claim); download links never leave the
  extension.
- New errors: unsupported_marketplace, offer_expired, invalid_state; every Amazon
  error carries amazon_request_id; an unknown change outcome names the IDs to read
  back. Forms show help, limits, examples and select lists for enums.
- New UI page: Operation matrix.

## 0.1.1 — unreleased

Pages use lists instead of tables (the page renderer draws no tables);
canonical extension id `flowsteward.amazon-sp-api`; review fixes.

## 0.1.0 — unreleased

First version: connection checks, orders (2026-01-01 and v0), FBM shipment
confirmation, catalog items, listings, product type definitions, listing
restrictions and validation preview, pricing, FBA/FBM stock, listing feeds,
finances (2024-06-19 and v0) and reports including settlement V2 downloads.
