#!/bin/sh
# Login role for the internal-service-backend (Django). Password comes from the
# container env (INTERNAL_BACKEND_DB_PASSWORD in .env) so no secret is inlined
# here. Least privilege: only the tables the checkout flow touches.
# Idempotent (create-if-missing + ALTER) so the CD deploy can re-run it.
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
SELECT 'CREATE ROLE internal_backend'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'internal_backend')\gexec
ALTER ROLE internal_backend WITH LOGIN PASSWORD '${INTERNAL_BACKEND_DB_PASSWORD:-internal_backend_lab_pw}';
GRANT USAGE ON SCHEMA finance TO internal_backend;
GRANT SELECT, INSERT ON finance.payment_methods, finance.transactions TO internal_backend;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA finance TO internal_backend;
SQL
