-- Manual seed data: wallets, tokenized cards, transactions, revenue. Lab data only.
-- Tokens/last4 are fake. No real card numbers anywhere (PCI scope).

INSERT INTO finance.wallets (customer_ref, balance_cents, currency) VALUES
  (1, 5000, 'USD'),
  (2,    0, 'USD'),
  (3, 1200, 'GBP');

INSERT INTO finance.payment_methods (customer_ref, brand, last4, token, exp_month, exp_year) VALUES
  (1, 'visa',       '4242', 'tok_lab_ada_visa',   8, 2028),
  (2, 'mastercard', '5454', 'tok_lab_grace_mc',  11, 2027),
  (3, 'amex',       '0005', 'tok_lab_alan_amex',  3, 2029);

INSERT INTO finance.transactions (order_ref, amount_cents, kind, status) VALUES
  (1, 142800, 'charge', 'settled'),
  (2,  24900, 'charge', 'settled'),
  (3,   4900, 'charge', 'settled');

INSERT INTO finance.revenue_daily (day, gross_cents, refunds_cents) VALUES
  (DATE '2026-06-27', 167700, 0),
  (DATE '2026-06-28',   4900, 0);
