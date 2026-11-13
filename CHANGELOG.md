# Changelog

## 0.2.12

- Clarify workflow field guidance and examples, with a setup and workflow action guide. Existing action IDs, input contracts and behavior are unchanged.

## 0.2.11

### Added

- Tests only, runtime unchanged: `tests/ui_mock/` adds a TEST / MOCK fixture
  for UI acceptance of several seller connections without Amazon credentials:
  a mock LWA token endpoint and the two SP-API reads of the connection check on
  reserved `*.amazon-mock.test` names, a TEST / MOCK copy of the bundle that
  names only those hosts, an overlay for Core's isolated manual OAuth browser
  lane, a seed (prepare / exercise with egress assertions) and `run.sh
  up|exercise|ca|status|down`. Unit tests in `tests/test_ui_mock_fixture.py`.

### Fixed

- Setup Guide: the seven official Amazon addresses are named Markdown links
  (they were plain text, so not clickable or reachable by keyboard).
- Setup Guide step 5 says that the Connection page opens only once the
  application configuration is *Ready for authorization* or *Verified*, where
  the reason is shown while it is not, and how to fix *Needs attention*
  (invalid_client: replace the client secret and save) and *Incomplete*.
- Request a report offers only the 8 requestable report types. The settlement
  report is no longer listed there (Amazon generates it); it stays in Find
  reports and Download a report. The schema enum still refuses anything else,
  and the handler still refuses a settlement request as a second gate.

## 0.2.10

### Added

- Automatic shared CI for pull requests and pushes to main, with tests and source-based release packaging.
- Tag and manual release callers for the shared extension release workflow.

## 0.2.9

### Changed

- Clarified the catalog description, supported operations and connection setup.

## 0.2.8 — unreleased

Sonar follow-up to 0.2.7, no behaviour change:
- The AWS region pattern of the S3 host check writes its digits as `\d` under
  `re.ASCII`, so it still takes ASCII digits only; the label check and
  `fullmatch` are unchanged. New negative cases: hosts whose region uses Arabic-
  Indic, fullwidth or mathematical digits are refused, and the region pattern
  refuses them on its own.
- Compound test assertions are split into one assertion each.
- The bounded S3-host regression no longer catches `TimeoutExpired`: the child
  process is still killed after 30 s and the test fails with that exception.

## 0.2.7 — unreleased

Source-review follow-up to 0.2.6: an `outcome_unknown` caused by a partial
answer keeps the schema-required identifiers Amazon did return in `check.ids`
(for example the `inbound_plan_id` of a createInboundPlan answer without its
`operation_id`), next to the input IDs and the row's `read_with`. Only non-blank
strings count as identifiers; numbers, booleans, objects, lists and blank strings
are missing and are not kept. No body content, no new calls.

## 0.2.6 — unreleased

Audit fixes (candidate after 0.2.5):
- Compatibility: `runtime.compatibility.platform_min: 1.6.0` (host contract;
  Flow Steward 0.9.12 ships host 1.6.0). `extension_host_v2` unchanged, no new
  manifest fields.
- Verified connection name: `store name · Amazon <marketplace> · <Seller ID>`, or
  `Amazon <marketplace> · <Seller ID>` without a store name, within 512 UTF-8
  bytes; only the store name is shortened, the marketplace and Seller ID suffix is
  always kept. One store in UK and US, or two sellers with one store name, are no
  longer indistinguishable in the workflow connection picker.
- Required response identifiers: a change whose 2xx answer lacks a top-level
  identifier its pinned response schema requires (`inbound_plan_id` and
  `operation_id` of createInboundPlan, `operation_id` of every asynchronous inbound
  step, `shipment_id` of purchaseShipment) now reports `outcome_unknown`
  (`definitely_no_external_effect: false`) with the input IDs to read back and
  Amazon's request ID, instead of success. Nothing is called again; optional and
  nested fields and 204 answers are unchanged.
- The pathological S3-host regression runs in a child process with a 30 s deadline
  instead of a 50 ms wall-clock assertion, so a hanging regression is killed and a
  busy scheduler cannot fail it.
- Docs and help: the minimum connection role (Product Listing, or Inventory and
  Order Tracking, for the Seller ID check) apart from operation roles; links to
  Amazon's documentation; `fba_list_prep_details` reads only API-set prep; the US
  prep/item-label service ended 2026-01-01; read every page of
  `fba_list_transportation_options` before concluding no partnered option; Amazon's
  partnered-carrier statements kept per source page.

## 0.2.5 — unreleased

Extension Sonar findings (analysis f9531072, revision 367dfba):
- python:S5852 (critical vulnerability): the S3 document-host check was a
  backtracking regex (1 s at 22 repetitions, unbounded beyond) that also accepted
  hosts whose first label an AWS customer chooses (s3-*.elb / execute-api). It is
  now a bounded, label-by-label parser that requires the S3 labels right before
  amazonaws.com.
- python:S8786: the plural-acronym fold in key conversion is a linear pass.
- python:S3776: amazon_api _output, to_amazon, _plan, build, _run and
  transport.call split into small helpers, behaviour unchanged.
- python:S1192, S5713, S7632, S5886 and two composite assertions in new tests.

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
