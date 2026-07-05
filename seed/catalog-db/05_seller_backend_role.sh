#!/bin/sh
# Login role for the seller-backend (Django). Password comes from the container
# env (SELLER_BACKEND_DB_PASSWORD in .env) so no secret is inlined here.
# Least privilege: a seller manages their own seller rows plus the catalog rows
# behind their listings; nothing in `ops`, no DELETE anywhere.
# Idempotent (create-if-missing + ALTER) so the CD deploy can re-run it.
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
SELECT 'CREATE ROLE seller_backend'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'seller_backend')\gexec
ALTER ROLE seller_backend WITH LOGIN PASSWORD '${SELLER_BACKEND_DB_PASSWORD:-seller_backend_lab_pw}';
GRANT USAGE ON SCHEMA seller, catalog TO seller_backend;
GRANT SELECT, INSERT, UPDATE ON seller.sellers, seller.listings TO seller_backend;
GRANT SELECT, INSERT, UPDATE ON catalog.products, catalog.inventory TO seller_backend;
GRANT SELECT ON catalog.categories TO seller_backend;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA seller, catalog TO seller_backend;
SQL
