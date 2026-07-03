-- customer-db now sits behind a PostgREST (customer-svc). Without this file the
-- web_anon SELECT grants from 01_schema would make ALL customer PII browsable at
-- GET /api/customers/customers — the opposite of the "strictest isolation"
-- invariant. So: strip web_anon's read access entirely, and expose the DB only
-- through the ensure_customer() RPC (04_rpc.sql), which returns just the
-- caller's own id.

-- Token-mapped privilege role (NOLOGIN, no password). PostgREST SET ROLEs into
-- it after verifying a Keycloak token whose `role` claim is "customer".
CREATE ROLE customer NOLOGIN;
GRANT customer TO authenticator;

-- Revoke the anonymous read access 01_schema handed to web_anon.
REVOKE SELECT ON ALL TABLES IN SCHEMA commerce FROM web_anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA commerce REVOKE SELECT ON TABLES FROM web_anon;
REVOKE USAGE ON SCHEMA commerce FROM web_anon;

-- customer gets schema usage only; table access stays closed. Every read/write
-- of PII happens inside the SECURITY DEFINER RPC, scoped to the caller's own row.
GRANT USAGE ON SCHEMA commerce TO customer;
