-- Customer DB schema — Customer Info crown jewel (design §1)
-- Loaded automatically by postgres initdb (runs before 02_seed.sql).

-- PostgREST roles: the service authenticates as `authenticator` and switches
-- to `web_anon` for anonymous reads. This is the ONLY way data leaves the DB.
CREATE ROLE web_anon NOLOGIN;
CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD 'customer_pgrst_pw';
GRANT web_anon TO authenticator;

CREATE SCHEMA commerce;
GRANT USAGE ON SCHEMA commerce TO web_anon;

CREATE TABLE commerce.customers (
    id           bigserial PRIMARY KEY,
    keycloak_sub text UNIQUE,                 -- links to identity store (Keycloak)
    email        text UNIQUE NOT NULL,
    full_name    text NOT NULL,
    phone        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE commerce.accounts (
    id           bigserial PRIMARY KEY,
    customer_id  bigint NOT NULL REFERENCES commerce.customers(id),
    status       text NOT NULL DEFAULT 'active',
    loyalty_tier text NOT NULL DEFAULT 'standard'
);

CREATE TABLE commerce.addresses (
    id           bigserial PRIMARY KEY,
    customer_id  bigint NOT NULL REFERENCES commerce.customers(id),
    kind         text NOT NULL DEFAULT 'shipping',   -- shipping | billing
    line1        text NOT NULL,
    city         text NOT NULL,
    region       text,
    postal       text,
    country      text NOT NULL
);

GRANT SELECT ON ALL TABLES IN SCHEMA commerce TO web_anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA commerce GRANT SELECT ON TABLES TO web_anon;
