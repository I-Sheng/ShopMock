-- Financial / Wallet DB schema — Money crown jewel, PCI-DSS scope (design §1).
-- SECURITY: never store PANs. Cards are represented by an opaque token + last4.

CREATE ROLE web_anon NOLOGIN;
CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD 'finance_pgrst_pw';
GRANT web_anon TO authenticator;

CREATE SCHEMA finance;
GRANT USAGE ON SCHEMA finance TO web_anon;

CREATE TABLE finance.wallets (
    id            bigserial PRIMARY KEY,
    customer_ref  bigint NOT NULL,           -- = customer.id in Customer DB
    balance_cents bigint NOT NULL DEFAULT 0,
    currency      text NOT NULL DEFAULT 'USD'
);

CREATE TABLE finance.payment_methods (
    id           bigserial PRIMARY KEY,
    customer_ref bigint NOT NULL,
    brand        text NOT NULL,              -- visa | mastercard | amex
    last4        char(4) NOT NULL,
    token        text UNIQUE NOT NULL,       -- opaque vault/gateway token, NOT a PAN
    exp_month    int NOT NULL,
    exp_year     int NOT NULL
);

CREATE TABLE finance.transactions (
    id           bigserial PRIMARY KEY,
    order_ref    bigint,                     -- = sales.orders.id in Orders DB
    amount_cents bigint NOT NULL,
    kind         text NOT NULL,              -- charge | refund | payout
    status       text NOT NULL DEFAULT 'settled',
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE finance.revenue_daily (
    day          date PRIMARY KEY,
    gross_cents  bigint NOT NULL,
    refunds_cents bigint NOT NULL DEFAULT 0
);

GRANT SELECT ON ALL TABLES IN SCHEMA finance TO web_anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA finance GRANT SELECT ON TABLES TO web_anon;
