-- Manual seed data: Customer PII (crown jewel). Lab data only.
-- keycloak_sub values must match the user IDs in identity/realm-shopmock.json.

INSERT INTO commerce.customers (id, keycloak_sub, email, full_name, phone) VALUES
  (1, 'cust-1001', 'ada@example.com',   'Ada Lovelace',     '+1-206-555-0101'),
  (2, 'cust-1002', 'grace@example.com', 'Grace Hopper',     '+1-206-555-0102'),
  (3, 'cust-1003', 'alan@example.com',  'Alan Turing',      '+44-20-5550-0103');
SELECT setval('commerce.customers_id_seq', 3);

INSERT INTO commerce.accounts (customer_id, status, loyalty_tier) VALUES
  (1, 'active',    'gold'),
  (2, 'active',    'standard'),
  (3, 'suspended', 'standard');

INSERT INTO commerce.addresses (customer_id, kind, line1, city, region, postal, country) VALUES
  (1, 'shipping', '1 Analytical Engine Way', 'Seattle',    'WA', '98101', 'US'),
  (1, 'billing',  '1 Analytical Engine Way', 'Seattle',    'WA', '98101', 'US'),
  (2, 'shipping', '500 Compiler Ct',         'Arlington',  'VA', '22201', 'US'),
  (3, 'shipping', '2 Turing Ave',            'Manchester', NULL, 'M1 1AA', 'GB');
