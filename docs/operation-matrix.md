# Operation matrix

Generated from `runtime/catalog.py` by `tests/reference/refresh_contracts.py`; `tests/test_manifest_parity.py` fails when it is stale.

Every operation runs against one connection, i.e. one seller account in one marketplace; the marketplace and the Seller ID always come from the connection.

| Method | Section | Amazon operations | Roles | External effect | Personal data |
|---|---|---|---|---|---|
| `test_connection` | account | Sellers v1 getMarketplaceParticipations<br>Listings Items 2021-08-01 searchListingsItems | Selling Partner Insights or Product Listing (getMarketplaceParticipations)<br>Product Listing (searchListingsItems) | none (read) | no |
| `verify_connection` (platform only) | account | Sellers v1 getMarketplaceParticipations<br>Listings Items 2021-08-01 searchListingsItems | Selling Partner Insights or Product Listing (getMarketplaceParticipations)<br>Product Listing (searchListingsItems) | none (read) | no |
| `search_orders` | orders | Orders 2026-01-01 searchOrders<br>Orders v0 getOrders | Inventory and Order Tracking | none (read) | no |
| `get_order` | orders | Orders 2026-01-01 getOrder<br>Orders v0 getOrder | Inventory and Order Tracking | none (read) | no |
| `get_order_items` | orders | Orders 2026-01-01 getOrder<br>Orders v0 getOrderItems | Inventory and Order Tracking | none (read) | no |
| `get_order_buyer_info` | orders | Orders 2026-01-01 getOrder (includedData BUYER, RECIPIENT)<br>Tokens 2021-03-01 createRestrictedDataToken + Orders v0 getOrderBuyerInfo, getOrderAddress | Inventory and Order Tracking<br>Direct-to-Consumer Shipping (restricted) for buyer and address data | none (read) | yes |
