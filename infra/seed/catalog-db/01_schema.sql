-- Catalog DB schema — Catalog & Pricing crown jewel (design §1).
-- Hosts three schemas: `catalog` (Tier 1), `seller` and `ops` (Tier 2 surfaces).

CREATE ROLE web_anon NOLOGIN;
CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD 'catalog_pgrst_pw';
GRANT web_anon TO authenticator;

CREATE SCHEMA catalog;
CREATE SCHEMA seller;
CREATE SCHEMA ops;
GRANT USAGE ON SCHEMA catalog, seller, ops TO web_anon;

CREATE TABLE catalog.categories (
    id        bigserial PRIMARY KEY,
    name      text NOT NULL,
    parent_id bigint REFERENCES catalog.categories(id)
);

CREATE TABLE catalog.products (
    id          bigserial PRIMARY KEY,
    sku         text UNIQUE NOT NULL,
    name        text NOT NULL,
    description text,
    category_id bigint REFERENCES catalog.categories(id),
    price_cents int NOT NULL,
    currency    text NOT NULL DEFAULT 'USD',
    active      boolean NOT NULL DEFAULT true
);

CREATE TABLE catalog.inventory (
    product_id bigint PRIMARY KEY REFERENCES catalog.products(id),
    warehouse  text NOT NULL DEFAULT 'SEA1',
    qty        int  NOT NULL DEFAULT 0
);

-- Tier 2 line-of-business: sellers own their own rows (Vendors/Marketplace, design §2)
CREATE TABLE seller.sellers (
    id           bigserial PRIMARY KEY,
    keycloak_sub text UNIQUE,
    display_name text NOT NULL,
    contact_email text NOT NULL,
    payout_status text NOT NULL DEFAULT 'active'
);

CREATE TABLE seller.listings (
    id         bigserial PRIMARY KEY,
    seller_id  bigint NOT NULL REFERENCES seller.sellers(id),
    product_id bigint NOT NULL REFERENCES catalog.products(id),
    commission_pct numeric(4,2) NOT NULL DEFAULT 12.00
);

-- Tier 2 internal ops tooling
CREATE TABLE ops.feature_flags (
    key     text PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    note    text
);

GRANT SELECT ON ALL TABLES IN SCHEMA catalog, seller, ops TO web_anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA catalog GRANT SELECT ON TABLES TO web_anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA seller  GRANT SELECT ON TABLES TO web_anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops     GRANT SELECT ON TABLES TO web_anon;
