-- Authenticated write role for Keycloak tokens (access-token claim role =
-- "customer"). This is a NOLOGIN privilege role — no password, nobody can
-- connect as it. PostgREST SET ROLEs into it after verifying a token.
-- Anonymous stays web_anon (read-only); a valid token upgrades to `customer`,
-- which can run the mock-payment RPC in 04_rpc.sql. Reads inherit from web_anon.
CREATE ROLE customer NOLOGIN;
GRANT web_anon TO customer;          -- inherit the SELECT grants from 01_schema
GRANT customer TO authenticator;     -- let PostgREST SET ROLE customer
