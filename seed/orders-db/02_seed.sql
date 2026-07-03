-- Manual seed data: orders, items, shipments. Lab data only.
-- customer_ref -> Customer DB ids; product_sku -> Catalog DB skus.

INSERT INTO sales.orders (id, customer_ref, status, total_cents) VALUES
  (1, 1, 'delivered', 142800),   -- Ada: laptop + buds
  (2, 2, 'shipped',    24900),   -- Grace: headphones
  (3, 1, 'paid',        4900);   -- Ada: lamp
SELECT setval('sales.orders_id_seq', 3);

INSERT INTO sales.order_items (order_id, product_sku, qty, unit_price_cents) VALUES
  (1, 'LAP-13-AIR', 1, 129900),
  (1, 'AUD-BUDS-1', 1,  12900),
  (2, 'AUD-CANS-1', 1,  24900),
  (3, 'HOM-LAMP-1', 1,   4900);

INSERT INTO sales.shipments (order_id, carrier, tracking, status) VALUES
  (1, 'UPS',   '1Z999AA10123456784', 'delivered'),
  (2, 'FedEx', '7712 3456 7890',     'in_transit');
