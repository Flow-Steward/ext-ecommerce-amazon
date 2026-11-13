# Amazon workflow actions

Create a private SP-API application and configure its LWA Client ID and secret
in the extension's **Setup Guide**. Self-authorize the seller in Seller Central
and import the refresh token. Add a project connection with the seller ID and
marketplace, then run **Test the connection**. The connection identifies one
seller and marketplace; the Operation matrix states when a method reads data
across that seller's region. Product Listing is required for verification; other
roles depend on the chosen actions. Keep credentials in connection secrets.

Use action names from the builder; IDs below identify the existing actions.
Example SKUs, ASINs and order IDs show formats only. Replace them with real
values from the connected seller. Bind opaque report, feed, plan, option and
shipment IDs from the actions that returned them.

## Read orders for fulfillment

1. Choose **Find orders** (`search_orders`). For example, set **Created after**
   to `2026-11-01T00:00:00Z` and **Created before** to
   `2026-11-08T00:00:00Z`. Keep **Amazon API version** at the current default
   unless you intentionally need legacy behavior.
2. Process `result.orders`. **Get an order** and **Get order items** accept an
   Amazon order ID such as `123-1234567-1234567`.
3. A search returns one page. If `result.next_token` is present, supply it to the
   same action with the same connection and filters. Tokens are tied to the
   original search; do not create or alter them.
4. Use the dedicated shipment confirmation/status actions for merchant-fulfilled
   orders. Read their required carrier, tracking, package and item fields before
   submission. Buyer/address retrieval requires the relevant restricted role.

## Read a listing, then set price or merchant stock

Use **Get a listing** (`get_listing`) with **SKU** `MUG-BLUE-01` to read the
seller's listing. Keep its real product type for the following writes.

- **Set a price** (`set_price`) takes that SKU, the listing's `product_type`,
  decimal text **Price** `19.90` and **Currency** `GBP` for a UK marketplace
  listing. Use the actual marketplace currency.
- **Set merchant quantity** (`set_fbm_quantity`) takes that SKU, its product
  type and integer **Quantity** `24`. This sets the absolute DEFAULT-channel
  quantity. The listing must already support merchant fulfillment; it does not
  change FBA inventory.

A submission response means Amazon accepted the request for processing. Read the
listing and its issues afterward to determine the resulting state. Product type
requirements, listing restrictions and **Validate a listing** help prepare a
change before submission. Attributes must follow the retrieved product-type
schema; a generic attribute object is not interchangeable across product types.

## Submit a listing feed or request a report

For a listing batch, use **Upload a feed document** with a JSON_LISTINGS_FEED
value or the supported workflow file input. Its header seller ID must match the
connection, version must be `2.0`, and message IDs must be unique. Supply real
product types and attributes matching Amazon's listing schema. Then:

1. Bind its `feed_document_id` into **Submit a feed** (`create_feed`) and choose
   `JSON_LISTINGS_FEED`.
2. Later, use **Get feed status** (`get_feed`) with the returned `feed_id`.
3. Once finished, use **Get feed result** (`get_feed_result`) and
   inspect individual message outcomes. Submission does not mean every listing
   change succeeded; this extension does not wait for completion automatically.

For reports, **Request a report** (`create_report`) takes one supported report
type and optional `data_start_time` / `data_end_time`, for example
`2026-11-01T00:00:00Z` and `2026-11-08T00:00:00Z`. Bind the returned `report_id`
into **Get report status**. Download the finished report with **Download a
report** and pass its file handle to later parsing steps. The download action
does not parse report contents. Amazon generates settlement reports itself;
find those with **Find reports**, then download them.

## Select other operation families

| Need | How to compose the existing actions |
| --- | --- |
| Catalog research | Search Amazon's catalog, then get a catalog item by ASIN. An ASIN is not a seller SKU. |
| FBA stock | Read FBA inventory summaries; fulfillable, inbound, reserved and other quantities remain separate. Continuation tokens expire quickly. |
| Pricing research | Read own/competing offers or competitive summaries before choosing a price. Research actions do not change offers. |
| FBA inbound | Create an inbound plan; bind its IDs into packing, placement and transportation generation/list/confirmation steps. Check asynchronous operation status and select real returned option IDs. |
| Amazon Shipping | Retrieve rates, provide any additional inputs, purchase the selected rate, then get documents and tracking. Purchase/cancellation actions change real shipments. Country restrictions apply. |
| Accounting | Read financial records or order financial records, or search/download supported settlement reports. API-version-specific filters must match the selected version. |

The [operation matrix](operation-matrix.md) gives every operation's roles,
marketplaces, files and Amazon calls. Preserve supported shipping/FBA nested
examples already shown in the forms and replace their sample values with your
shipment's actual details. Write actions are suppressed in test mode. After an
unknown outcome, read back the affected resource before repeating a write.
