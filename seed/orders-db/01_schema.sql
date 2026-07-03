-- Orders DB schema — order history (Customer Info + Order Fulfillment, design §1–2).
-- References to customers/products are stored as opaque refs, not FKs, because
-- those rows live in OTHER databases (database-per-service, design §6d ref [9]).

CREATE ROLE web_anon NOLOGIN;
CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD 'orders_pgrst_pw';
GRANT web_anon TO authenticator;

CREATE SCHEMA sales;
GRANT USAGE ON SCHEMA sales TO web_anon;

CREATE TABLE sales.orders (
    id           bigserial PRIMARY KEY,
    customer_ref bigint NOT NULL,            -- = customer.id in Customer DB
    status       text NOT NULL DEFAULT 'placed',  -- placed|paid|shipped|delivered|cancelled
    total_cents  int NOT NULL,
    currency     text NOT NULL DEFAULT 'USD',
    placed_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sales.order_items (
    id              bigserial PRIMARY KEY,
    order_id        bigint NOT NULL REFERENCES sales.orders(id),
    product_sku     text NOT NULL,           -- = catalog.products.sku in Catalog DB
    qty             int NOT NULL,
    unit_price_cents int NOT NULL
);

CREATE TABLE sales.shipments (
    id        bigserial PRIMARY KEY,
    order_id  bigint NOT NULL REFERENCES sales.orders(id),
    carrier   text,
    tracking  text,
    status    text NOT NULL DEFAULT 'pending'
);

GRANT SELECT ON ALL TABLES IN SCHEMA sales TO web_anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA sales GRANT SELECT ON TABLES TO web_anon;
