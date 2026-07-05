#!/bin/sh
# Login role for the seller-backend (Django) on orders-db. Read-only: the
# seller sales view only ever SELECTs order lines for the seller's own SKUs.
# Password comes from the container env (SELLER_BACKEND_DB_PASSWORD in .env).
# Idempotent (create-if-missing + ALTER) so the CD deploy can re-run it.
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
SELECT 'CREATE ROLE seller_backend'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'seller_backend')\gexec
ALTER ROLE seller_backend WITH LOGIN PASSWORD '${SELLER_BACKEND_DB_PASSWORD:-seller_backend_lab_pw}';
GRANT USAGE ON SCHEMA sales TO seller_backend;
GRANT SELECT ON sales.orders, sales.order_items TO seller_backend;
SQL
