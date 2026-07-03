-- Authenticated write role for Keycloak-issued tokens (access-token claim
-- role = "customer"). Anonymous stays web_anon (read-only); a valid token
-- upgrades the request to `customer`, which can additionally run the checkout
-- RPC in 04_rpc.sql. Reads are inherited from web_anon so the order-history
-- view (GET /api/orders/orders) still works with a bearer token.
CREATE ROLE customer NOLOGIN;
GRANT web_anon TO customer;          -- inherit the SELECT grants from 01_schema
GRANT customer TO authenticator;     -- let PostgREST SET ROLE customer
