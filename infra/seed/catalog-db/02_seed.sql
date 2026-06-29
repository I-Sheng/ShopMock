-- Manual seed data: catalog, pricing, inventory, sellers, ops flags. Lab data only.

INSERT INTO catalog.categories (id, name, parent_id) VALUES
  (1, 'Electronics', NULL),
  (2, 'Laptops',     1),
  (3, 'Audio',       1),
  (4, 'Home',        NULL);
SELECT setval('catalog.categories_id_seq', 4);

INSERT INTO catalog.products (id, sku, name, description, category_id, price_cents) VALUES
  (1, 'LAP-13-AIR',  'ShopMock Air 13"',     '13-inch ultralight laptop',  2, 129900),
  (2, 'LAP-15-PRO',  'ShopMock Pro 15"',     '15-inch performance laptop', 2, 199900),
  (3, 'AUD-BUDS-1',  'ShopMock Buds',        'Wireless earbuds',           3,  12900),
  (4, 'AUD-CANS-1',  'ShopMock Studio Cans', 'Over-ear headphones',        3,  24900),
  (5, 'HOM-LAMP-1',  'ShopMock Desk Lamp',   'LED desk lamp',              4,   4900);
SELECT setval('catalog.products_id_seq', 5);

INSERT INTO catalog.inventory (product_id, warehouse, qty) VALUES
  (1, 'SEA1', 120), (2, 'SEA1', 45), (3, 'SEA1', 800), (4, 'SEA1', 230), (5, 'SEA1', 1500);

INSERT INTO seller.sellers (id, keycloak_sub, display_name, contact_email) VALUES
  (1, 'sell-2001', 'NW Gadgets',   'sales@nwgadgets.example'),
  (2, 'sell-2002', 'AudioWorks',   'hi@audioworks.example');
SELECT setval('seller.sellers_id_seq', 2);

INSERT INTO seller.listings (seller_id, product_id, commission_pct) VALUES
  (1, 1, 10.00), (1, 2, 10.00), (2, 3, 15.00), (2, 4, 15.00);

INSERT INTO ops.feature_flags (key, enabled, note) VALUES
  ('checkout.v2',        true,  'new checkout flow'),
  ('search.fuzzy',       true,  'fuzzy matching on storefront search'),
  ('seller.payouts.hold', false, 'global payout freeze switch');
