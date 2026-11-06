# Changelog

## 0.2.4 — unreleased

Tests only: the FBA document tests no longer resolve the S3 host through DNS
(the same autouse fixture the report and feed tests use), so they pass in a
container without network. Runtime unchanged from 0.2.3.

## 0.2.3 — unreleased

Platform extension gate fixes, no behaviour change: mypy type errors in
runtime/amazon_api.py and the contract generator, and a bandit B105 false
positive on a variable that holds help text (renamed, no nosec).

## 0.2.2 — unreleased

Independent review fixes: MSKU lists are sent as repeated query parameters
(Amazon's `multi` format) in fba_list_prep_details and
fba_list_item_compliance_details; delete_listing reports an INVALID answer as a
refused change and a missing status as an unknown outcome; output schemas accept
new Amazon enum values and nested nulls; UTF-8 and BOM-prefixed ZPL labels and
PDFs with leading bytes are accepted; an expired pagination token is
continuation_token_invalid and "cannot be updated" validation errors are no
longer invalid_state; rehearsals refuse unsupported marketplaces like real runs;
the shipping document limit (4 MiB) sits below the response cap.

## 0.2.1 — unreleased

Audit fixes on 0.2.0: select and multi_select fields carry their options (enum
inputs were free text in the builder); list_financial_records is marked
seller-region; Setup Guide, Connection and behaviour pages state scope, single
actions, credential storage and the India programmes exactly.

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
