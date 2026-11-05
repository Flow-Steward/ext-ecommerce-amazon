# Operation matrix

Generated from `runtime/catalog.py` by `tests/reference/refresh_contracts.py`; `tests/test_manifest_parity.py` fails when it is stale.

Every operation runs against one connection, i.e. one seller account in one marketplace; the marketplace and the Seller ID always come from the connection.

| Method | Section | Amazon operations | Roles | External effect | Personal data |
|---|---|---|---|---|---|
| `test_connection` | account | Sellers v1 getMarketplaceParticipations<br>Listings Items 2021-08-01 searchListingsItems | Selling Partner Insights or Product Listing (getMarketplaceParticipations)<br>Product Listing (searchListingsItems) | none (read) | no |
| `verify_connection` (platform only) | account | Sellers v1 getMarketplaceParticipations<br>Listings Items 2021-08-01 searchListingsItems | Selling Partner Insights or Product Listing (getMarketplaceParticipations)<br>Product Listing (searchListingsItems) | none (read) | no |
| `search_orders` | orders | Orders 2026-01-01 searchOrders (includedData FULFILLMENT, PROCEEDS)<br>Orders v0 getOrders | Inventory and Order Tracking | none (read) | no |
| `get_order` | orders | Orders 2026-01-01 getOrder (includedData FULFILLMENT, PROCEEDS)<br>Orders v0 getOrder | Inventory and Order Tracking | none (read) | no |
| `get_order_items` | orders | Orders 2026-01-01 getOrder (includedData FULFILLMENT, PROCEEDS)<br>Orders v0 getOrderItems | Inventory and Order Tracking | none (read) | no |
| `get_order_buyer_info` | orders | Orders 2026-01-01 getOrder (includedData BUYER, RECIPIENT)<br>Tokens 2021-03-01 createRestrictedDataToken + Orders v0 getOrderBuyerInfo, getOrderAddress | Inventory and Order Tracking<br>Direct-to-Consumer Shipping (restricted) for buyer and address data | none (read) | yes |
| `confirm_shipment` | shipping | Orders v0 confirmShipment | Inventory and Order Tracking | `amazon_shipment_confirmation` | no |
| `update_shipment_status` | shipping | Orders v0 updateShipmentStatus | Inventory and Order Tracking | `amazon_shipment_status_update` | no |
| `search_catalog_items` | catalog_items | Catalog Items 2022-04-01 searchCatalogItems | Product Listing | none (read) | no |
| `get_catalog_item` | catalog_items | Catalog Items 2022-04-01 getCatalogItem | Product Listing | none (read) | no |
| `search_listings` | listings | Listings Items 2021-08-01 searchListingsItems | Product Listing | none (read) | no |
| `get_listing` | listings | Listings Items 2021-08-01 getListingsItem | Product Listing | none (read) | no |
| `get_listing_issues` | listings | Listings Items 2021-08-01 getListingsItem (includedData issues) | Product Listing | none (read) | no |
| `create_listing` | listings | Listings Items 2021-08-01 putListingsItem | Product Listing | `amazon_listing_put` | no |
| `update_listing_attributes` | listings | Listings Items 2021-08-01 patchListingsItem | Product Listing | `amazon_listing_patch` | no |
| `search_product_types` | requirements | Product Type Definitions 2020-09-01 searchDefinitionsProductTypes | Product Listing | none (read) | no |
| `get_product_type_definition` | requirements | Product Type Definitions 2020-09-01 getDefinitionsProductType<br>Amazon S3 presigned schema download (include_schema) | Product Listing | none (read) | no |
| `check_listing_restrictions` | requirements | Listings Restrictions 2021-08-01 getListingsRestrictions | Product Listing | none (read) | no |
| `validate_listing` | requirements | Listings Items 2021-08-01 putListingsItem (mode VALIDATION_PREVIEW)<br>Listings Items 2021-08-01 patchListingsItem (mode VALIDATION_PREVIEW) | Product Listing | none (read) | no |
| `get_own_offer` | pricing | Listings Items 2021-08-01 getListingsItem (includedData summaries, offers, fulfillmentAvailability) | Product Listing | none (read) | no |
| `get_listing_offers` | pricing | Product Pricing v0 getListingOffers | Pricing | none (read) | no |
| `get_item_offers` | pricing | Product Pricing v0 getItemOffers | Pricing | none (read) | no |
| `get_competitive_summary` | pricing | Product Pricing 2022-05-01 getCompetitiveSummary | Pricing | none (read) | no |
| `get_featured_offer_expected_price` | pricing | Product Pricing 2022-05-01 getFeaturedOfferExpectedPriceBatch | Pricing | none (read) | no |
| `set_price` | pricing | Listings Items 2021-08-01 patchListingsItem (purchasable_offer only) | Product Listing | `amazon_price_update` | no |
| `get_fba_inventory` | inventory | FBA Inventory v1 getInventorySummaries | Amazon Fulfillment | none (read) | no |
| `get_fbm_quantity` | inventory | Listings Items 2021-08-01 getListingsItem (includedData fulfillmentAvailability) | Product Listing | none (read) | no |
| `set_fbm_quantity` | inventory | Listings Items 2021-08-01 getListingsItem (includedData fulfillmentAvailability)<br>Listings Items 2021-08-01 patchListingsItem (fulfillment_availability only) | Product Listing | `amazon_fbm_quantity_update` | no |
| `upload_feed_document` | feeds | Feeds 2021-06-30 createFeedDocument<br>Upload to the presigned Amazon S3 URL | Product Listing | none (read) | no |
| `create_feed` | feeds | Feeds 2021-06-30 createFeed | Product Listing | `amazon_feed_submission` | no |
| `get_feed` | feeds | Feeds 2021-06-30 getFeed | Product Listing | none (read) | no |
| `search_feeds` | feeds | Feeds 2021-06-30 getFeeds | Product Listing | none (read) | no |
| `get_feed_result` | feeds | Feeds 2021-06-30 getFeed<br>Feeds 2021-06-30 getFeedDocument + download from the presigned Amazon S3 URL | Product Listing | none (read) | no |
| `list_financial_records` | finances | Finances 2024-06-19 listTransactions<br>Finances v0 listFinancialEvents | Finance and Accounting | none (read) | no |
| `list_financial_records_for_order` | finances | Finances 2024-06-19 listTransactions (relatedIdentifierName=ORDER_ID)<br>Finances v0 listFinancialEventsByOrderId | Finance and Accounting | none (read) | no |
| `list_financial_event_groups` | finances | Finances v0 listFinancialEventGroups | Finance and Accounting | none (read) | no |
| `list_financial_events_by_group` | finances | Finances 2024-06-19 listTransactions (relatedIdentifierName=FINANCIAL_EVENT_GROUP_ID)<br>Finances v0 listFinancialEventsByGroupId | Finance and Accounting | none (read) | no |
| `search_reports` | reports | Reports 2021-06-30 getReports | The role the report type needs (for example Inventory and Order Tracking, Product Listing, Amazon Fulfillment, Finance and Accounting) | none (read) | no |
| `create_report` | reports | Reports 2021-06-30 createReport | The role the report type needs (for example Inventory and Order Tracking, Product Listing, Amazon Fulfillment, Finance and Accounting) | none (read) | no |
| `get_report` | reports | Reports 2021-06-30 getReport | The role the report type needs (for example Inventory and Order Tracking, Product Listing, Amazon Fulfillment, Finance and Accounting) | none (read) | no |
| `download_report` | reports | Reports 2021-06-30 getReport<br>Reports 2021-06-30 getReportDocument + download from the presigned Amazon S3 URL | The role the report type needs (for example Inventory and Order Tracking, Product Listing, Amazon Fulfillment, Finance and Accounting) | none (read) | yes |
