# Operation matrix

Generated from `runtime/catalog.py` by `tests/reference/refresh_contracts.py`; `tests/test_manifest_parity.py` fails when it is stale.

Every operation runs against one connection, i.e. one seller account in one marketplace; the marketplace and the Seller ID always come from the connection.

Scope: `marketplace` = the request names the connection's marketplace; `seller_region` = addressed by Amazon ID or seller-wide, so Amazon answers for any marketplace of the seller in the connection's region; `shipping_business` = the seller's labels in the Amazon Shipping business of the connection's country.

| Method | Section | Amazon operations | Roles | Marketplaces | Scope | External effect | Personal data | File |
|---|---|---|---|---|---|---|---|---|
| `test_connection` | account | Sellers v1 getMarketplaceParticipations<br>Listings Items 2021-08-01 searchListingsItems | Selling Partner Insights or Product Listing (getMarketplaceParticipations)<br>Product Listing (searchListingsItems) | all 23 | marketplace | none (read) | no | no |
| `verify_connection` (platform only) | account | Sellers v1 getMarketplaceParticipations<br>Listings Items 2021-08-01 searchListingsItems | Selling Partner Insights or Product Listing (getMarketplaceParticipations)<br>Product Listing (searchListingsItems) | all 23 | marketplace | none (read) | no | no |
| `search_orders` | orders | Orders 2026-01-01 searchOrders (includedData FULFILLMENT, PROCEEDS)<br>Orders v0 getOrders | Inventory and Order Tracking | all 23 | marketplace | none (read) | no | no |
| `get_order` | orders | Orders 2026-01-01 getOrder (includedData FULFILLMENT, PROCEEDS)<br>Orders v0 getOrder | Inventory and Order Tracking | all 23 | seller_region | none (read) | no | no |
| `get_order_items` | orders | Orders 2026-01-01 getOrder (includedData FULFILLMENT, PROCEEDS)<br>Orders v0 getOrderItems | Inventory and Order Tracking | all 23 | seller_region | none (read) | no | no |
| `get_order_buyer_info` | orders | Orders 2026-01-01 getOrder (includedData BUYER, RECIPIENT)<br>Tokens 2021-03-01 createRestrictedDataToken + Orders v0 getOrderBuyerInfo, getOrderAddress | Inventory and Order Tracking<br>Direct-to-Consumer Shipping (restricted) for buyer and address data | all 23 | seller_region | none (read) | yes | no |
| `confirm_shipment` | shipping | Orders v0 confirmShipment | Inventory and Order Tracking | all 23 | seller_region | `amazon_shipment_confirmation` | no | no |
| `update_shipment_status` | shipping | Orders v0 updateShipmentStatus | Inventory and Order Tracking | all 23 | seller_region | `amazon_shipment_status_update` | no | no |
| `get_shipping_rates` | shipping | Shipping v2 getRates | Direct-to-Consumer Shipping (restricted) | US, UK, IT, FR, ES | shipping_business | none (read) | no | no |
| `get_shipping_additional_inputs` | shipping | Shipping v2 getAdditionalInputs | Direct-to-Consumer Shipping (restricted) | US, UK, IT, FR, ES | shipping_business | none (read) | no | no |
| `purchase_shipping_shipment` | shipping | Shipping v2 purchaseShipment | Direct-to-Consumer Shipping (restricted) | US, UK, IT, FR, ES | shipping_business | `amazon_shipping_label_purchase` | no | no |
| `get_shipping_documents` | shipping | Shipping v2 getShipmentDocuments | Direct-to-Consumer Shipping (restricted) | US, UK, IT, FR, ES | shipping_business | none (read) | yes | writes a file |
| `get_shipping_tracking` | shipping | Shipping v2 getTracking | Direct-to-Consumer Shipping (restricted) | US, UK, IT, FR, ES | shipping_business | none (read) | no | no |
| `cancel_shipping_shipment` | shipping | Shipping v2 cancelShipment | Direct-to-Consumer Shipping (restricted) | US, UK, IT, FR, ES | shipping_business | `amazon_shipping_label_cancellation` | no | no |
| `search_catalog_items` | catalog_items | Catalog Items 2022-04-01 searchCatalogItems | Product Listing | all 23 | marketplace | none (read) | no | no |
| `get_catalog_item` | catalog_items | Catalog Items 2022-04-01 getCatalogItem | Product Listing | all 23 | marketplace | none (read) | no | no |
| `search_listings` | listings | Listings Items 2021-08-01 searchListingsItems | Product Listing | all 23 | marketplace | none (read) | no | no |
| `get_listing` | listings | Listings Items 2021-08-01 getListingsItem | Product Listing | all 23 | marketplace | none (read) | no | no |
| `get_listing_issues` | listings | Listings Items 2021-08-01 getListingsItem (includedData issues) | Product Listing | all 23 | marketplace | none (read) | no | no |
| `create_listing` | listings | Listings Items 2021-08-01 putListingsItem | Product Listing | all 23 | marketplace | `amazon_listing_put` | no | no |
| `update_listing_attributes` | listings | Listings Items 2021-08-01 patchListingsItem | Product Listing | all 23 | marketplace | `amazon_listing_patch` | no | no |
| `delete_listing` | listings | Listings Items 2021-08-01 deleteListingsItem | Product Listing | all 23 | marketplace | `amazon_listing_deletion` | no | no |
| `search_product_types` | requirements | Product Type Definitions 2020-09-01 searchDefinitionsProductTypes | Product Listing | all 23 | marketplace | none (read) | no | no |
| `get_product_type_definition` | requirements | Product Type Definitions 2020-09-01 getDefinitionsProductType<br>Amazon S3 presigned schema download (include_schema) | Product Listing | all 23 | marketplace | none (read) | no | no |
| `check_listing_restrictions` | requirements | Listings Restrictions 2021-08-01 getListingsRestrictions | Product Listing | all 23 | marketplace | none (read) | no | no |
| `validate_listing` | requirements | Listings Items 2021-08-01 putListingsItem (mode VALIDATION_PREVIEW)<br>Listings Items 2021-08-01 patchListingsItem (mode VALIDATION_PREVIEW) | Product Listing | all 23 | marketplace | none (read) | no | no |
| `get_own_offer` | pricing | Listings Items 2021-08-01 getListingsItem (includedData summaries, offers, fulfillmentAvailability) | Product Listing | all 23 | marketplace | none (read) | no | no |
| `get_listing_offers` | pricing | Product Pricing v0 getListingOffers | Pricing | all 23 | marketplace | none (read) | no | no |
| `get_item_offers` | pricing | Product Pricing v0 getItemOffers | Pricing | all 23 | marketplace | none (read) | no | no |
| `get_competitive_summary` | pricing | Product Pricing 2022-05-01 getCompetitiveSummary | Pricing | all 23 | marketplace | none (read) | no | no |
| `get_featured_offer_expected_price` | pricing | Product Pricing 2022-05-01 getFeaturedOfferExpectedPriceBatch | Pricing | all 23 | marketplace | none (read) | no | no |
| `set_price` | pricing | Listings Items 2021-08-01 patchListingsItem (purchasable_offer only) | Product Listing | all 23 | marketplace | `amazon_price_update` | no | no |
| `get_fba_inventory` | inventory | FBA Inventory v1 getInventorySummaries | Amazon Fulfillment | all 23 | marketplace | none (read) | no | no |
| `get_fbm_quantity` | inventory | Listings Items 2021-08-01 getListingsItem (includedData fulfillmentAvailability) | Product Listing | all 23 | marketplace | none (read) | no | no |
| `set_fbm_quantity` | inventory | Listings Items 2021-08-01 getListingsItem (includedData fulfillmentAvailability)<br>Listings Items 2021-08-01 patchListingsItem (fulfillment_availability only) | Product Listing | all 23 | marketplace | `amazon_fbm_quantity_update` | no | no |
| `fba_list_prep_details` | fba_inbound | Fulfillment Inbound 2024-03-20 listPrepDetails | Amazon Fulfillment | all except BR | marketplace | none (read) | no | no |
| `fba_set_prep_details` | fba_inbound | Fulfillment Inbound 2024-03-20 setPrepDetails | Amazon Fulfillment | all except BR | marketplace | `amazon_fba_prep_details_update` | no | no |
| `fba_create_marketplace_item_labels` | fba_inbound | Fulfillment Inbound 2024-03-20 createMarketplaceItemLabels | Amazon Fulfillment | all except BR | marketplace | none (read) | no | writes a file |
| `fba_create_inbound_plan` | fba_inbound | Fulfillment Inbound 2024-03-20 createInboundPlan | Amazon Fulfillment | all except BR | marketplace | `amazon_fba_inbound_plan_creation` | no | no |
| `fba_list_inbound_plans` | fba_inbound | Fulfillment Inbound 2024-03-20 listInboundPlans | Amazon Fulfillment | all except BR | seller_region | none (read) | yes | no |
| `fba_get_inbound_plan` | fba_inbound | Fulfillment Inbound 2024-03-20 getInboundPlan | Amazon Fulfillment | all except BR | seller_region | none (read) | yes | no |
| `fba_cancel_inbound_plan` | fba_inbound | Fulfillment Inbound 2024-03-20 cancelInboundPlan | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_inbound_plan_cancellation` | no | no |
| `fba_get_inbound_operation_status` | fba_inbound | Fulfillment Inbound 2024-03-20 getInboundOperationStatus | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_list_inbound_plan_items` | fba_inbound | Fulfillment Inbound 2024-03-20 listInboundPlanItems | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_list_inbound_plan_boxes` | fba_inbound | Fulfillment Inbound 2024-03-20 listInboundPlanBoxes | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_list_inbound_plan_pallets` | fba_inbound | Fulfillment Inbound 2024-03-20 listInboundPlanPallets | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_update_inbound_plan_name` | fba_inbound | Fulfillment Inbound 2024-03-20 updateInboundPlanName | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_inbound_plan_rename` | no | no |
| `fba_generate_packing_options` | fba_inbound | Fulfillment Inbound 2024-03-20 generatePackingOptions | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_packing_options_generation` | no | no |
| `fba_list_packing_options` | fba_inbound | Fulfillment Inbound 2024-03-20 listPackingOptions | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_confirm_packing_option` | fba_inbound | Fulfillment Inbound 2024-03-20 confirmPackingOption | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_packing_option_confirmation` | no | no |
| `fba_list_packing_group_items` | fba_inbound | Fulfillment Inbound 2024-03-20 listPackingGroupItems | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_list_packing_group_boxes` | fba_inbound | Fulfillment Inbound 2024-03-20 listPackingGroupBoxes | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_set_packing_information` | fba_inbound | Fulfillment Inbound 2024-03-20 setPackingInformation | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_packing_information_update` | no | no |
| `fba_generate_placement_options` | fba_inbound | Fulfillment Inbound 2024-03-20 generatePlacementOptions | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_placement_options_generation` | no | no |
| `fba_list_placement_options` | fba_inbound | Fulfillment Inbound 2024-03-20 listPlacementOptions | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_confirm_placement_option` | fba_inbound | Fulfillment Inbound 2024-03-20 confirmPlacementOption | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_placement_option_confirmation` | no | no |
| `fba_generate_transportation_options` | fba_inbound | Fulfillment Inbound 2024-03-20 generateTransportationOptions | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_transportation_options_generation` | no | no |
| `fba_list_transportation_options` | fba_inbound | Fulfillment Inbound 2024-03-20 listTransportationOptions | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_confirm_transportation_options` | fba_inbound | Fulfillment Inbound 2024-03-20 confirmTransportationOptions | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_transportation_confirmation` | no | no |
| `fba_generate_delivery_window_options` | fba_inbound | Fulfillment Inbound 2024-03-20 generateDeliveryWindowOptions | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_delivery_windows_generation` | no | no |
| `fba_list_delivery_window_options` | fba_inbound | Fulfillment Inbound 2024-03-20 listDeliveryWindowOptions | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_confirm_delivery_window_options` | fba_inbound | Fulfillment Inbound 2024-03-20 confirmDeliveryWindowOptions | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_delivery_window_confirmation` | no | no |
| `fba_get_shipment` | fba_inbound | Fulfillment Inbound 2024-03-20 getShipment | Amazon Fulfillment | all except BR | seller_region | none (read) | yes | no |
| `fba_list_shipment_items` | fba_inbound | Fulfillment Inbound 2024-03-20 listShipmentItems | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_list_shipment_boxes` | fba_inbound | Fulfillment Inbound 2024-03-20 listShipmentBoxes | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_list_shipment_pallets` | fba_inbound | Fulfillment Inbound 2024-03-20 listShipmentPallets | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_update_shipment_name` | fba_inbound | Fulfillment Inbound 2024-03-20 updateShipmentName | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_shipment_rename` | no | no |
| `fba_update_shipment_source_address` | fba_inbound | Fulfillment Inbound 2024-03-20 updateShipmentSourceAddress | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_shipment_source_address_update` | no | no |
| `fba_update_shipment_tracking_details` | fba_inbound | Fulfillment Inbound 2024-03-20 updateShipmentTrackingDetails | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_shipment_tracking_update` | no | no |
| `fba_get_item_eligibility_preview` | fba_inbound | FBA Inbound Eligibility v1 getItemEligibilityPreview | Amazon Fulfillment | all except BR | marketplace | none (read) | no | no |
| `fba_get_labels` | fba_inbound | Fulfillment Inbound v0 getLabels | Amazon Fulfillment | all except BR | seller_region | none (read) | no | writes a file |
| `fba_get_bill_of_lading` | fba_inbound | Fulfillment Inbound v0 getBillOfLading | Amazon Fulfillment | all except BR | seller_region | none (read) | no | writes a file |
| `fba_list_item_compliance_details` | fba_inbound_india | Fulfillment Inbound 2024-03-20 listItemComplianceDetails | Amazon Fulfillment<br>Product Listing | IN | marketplace | none (read) | no | no |
| `fba_update_item_compliance_details` | fba_inbound_india | Fulfillment Inbound 2024-03-20 updateItemComplianceDetails | Amazon Fulfillment<br>Product Listing | IN | marketplace | `amazon_fba_compliance_details_update` | no | no |
| `fba_generate_self_ship_appointment_slots` | fba_inbound_india | Fulfillment Inbound 2024-03-20 generateSelfShipAppointmentSlots | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_appointment_slots_generation` | no | no |
| `fba_get_self_ship_appointment_slots` | fba_inbound_india | Fulfillment Inbound 2024-03-20 getSelfShipAppointmentSlots | Amazon Fulfillment | all except BR | seller_region | none (read) | no | no |
| `fba_schedule_self_ship_appointment` | fba_inbound_india | Fulfillment Inbound 2024-03-20 scheduleSelfShipAppointment | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_appointment_scheduling` | no | no |
| `fba_cancel_self_ship_appointment` | fba_inbound_india | Fulfillment Inbound 2024-03-20 cancelSelfShipAppointment | Amazon Fulfillment | all except BR | seller_region | `amazon_fba_appointment_cancellation` | no | no |
| `fba_get_delivery_challan_document` | fba_inbound_india | Fulfillment Inbound 2024-03-20 getDeliveryChallanDocument | Amazon Fulfillment<br>Product Listing | IN | seller_region | none (read) | no | writes a file |
| `upload_feed_document` | feeds | Feeds 2021-06-30 createFeedDocument<br>Upload to the presigned Amazon S3 URL | Product Listing | all 23 | marketplace | none (read) | no | reads a file |
| `create_feed` | feeds | Feeds 2021-06-30 createFeed | Product Listing | all 23 | marketplace | `amazon_feed_submission` | no | no |
| `get_feed` | feeds | Feeds 2021-06-30 getFeed | Product Listing | all 23 | seller_region | none (read) | no | no |
| `search_feeds` | feeds | Feeds 2021-06-30 getFeeds | Product Listing | all 23 | marketplace | none (read) | no | no |
| `get_feed_result` | feeds | Feeds 2021-06-30 getFeed<br>Feeds 2021-06-30 getFeedDocument + download from the presigned Amazon S3 URL | Product Listing | all 23 | seller_region | none (read) | no | writes a file |
| `cancel_feed` | feeds | Feeds 2021-06-30 cancelFeed | Product Listing | all 23 | seller_region | `amazon_feed_cancellation` | no | no |
| `list_financial_records` | finances | Finances 2024-06-19 listTransactions<br>Finances v0 listFinancialEvents | Finance and Accounting | all 23 | marketplace | none (read) | no | no |
| `list_financial_records_for_order` | finances | Finances 2024-06-19 listTransactions (relatedIdentifierName=ORDER_ID)<br>Finances v0 listFinancialEventsByOrderId | Finance and Accounting | all 23 | seller_region | none (read) | no | no |
| `list_financial_event_groups` | finances | Finances v0 listFinancialEventGroups | Finance and Accounting | all 23 | seller_region | none (read) | no | no |
| `list_financial_events_by_group` | finances | Finances 2024-06-19 listTransactions (relatedIdentifierName=FINANCIAL_EVENT_GROUP_ID)<br>Finances v0 listFinancialEventsByGroupId | Finance and Accounting | all 23 | seller_region | none (read) | no | no |
| `search_reports` | reports | Reports 2021-06-30 getReports | The role the report type needs (for example Inventory and Order Tracking, Product Listing, Amazon Fulfillment, Finance and Accounting) | all 23 | marketplace | none (read) | no | no |
| `create_report` | reports | Reports 2021-06-30 createReport | The role the report type needs (for example Inventory and Order Tracking, Product Listing, Amazon Fulfillment, Finance and Accounting) | all 23 | marketplace | none (read) | no | no |
| `get_report` | reports | Reports 2021-06-30 getReport | The role the report type needs (for example Inventory and Order Tracking, Product Listing, Amazon Fulfillment, Finance and Accounting) | all 23 | seller_region | none (read) | no | no |
| `download_report` | reports | Reports 2021-06-30 getReport<br>Reports 2021-06-30 getReportDocument + download from the presigned Amazon S3 URL | The role the report type needs (for example Inventory and Order Tracking, Product Listing, Amazon Fulfillment, Finance and Accounting) | all 23 | seller_region | none (read) | yes | writes a file |
| `cancel_report` | reports | Reports 2021-06-30 cancelReport | The role the report type needs (for example Inventory and Order Tracking, Product Listing, Amazon Fulfillment, Finance and Accounting) | all 23 | seller_region | `amazon_report_cancellation` | no | no |
