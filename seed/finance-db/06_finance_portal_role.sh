#!/bin/sh
# Login role for finance-portal (Django). Password comes from the container env
# (FINANCE_PORTAL_DB_PASSWORD in .env) so no secret is inlined here.
# Idempotent (create-if-missing + ALTER) so the CD deploy can re-run it.
#
# The important line in this file is the one that is NOT here: the grant on
# finance.payment_methods lists columns, and `token` is not among them. The
# portal's SQL does not name that column and its serializer refuses to publish
# it — but this is the layer that makes it impossible rather than merely
# intended. A SELECT of the token as this role is a permission denied error.
#
# `customer_ref` is withheld for the same reason on both tables that carry it:
# customer identity belongs to customer-db, not to a finance report.
#
# This role is NOT granted web_anon (which holds table-wide SELECT for
# PostgREST); it is a fresh, read-only login that inherits nothing.
set -e
: "${FINANCE_PORTAL_DB_PASSWORD:?set FINANCE_PORTAL_DB_PASSWORD}"
psql -v ON_ERROR_STOP=1 -v role_password="$FINANCE_PORTAL_DB_PASSWORD" \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
SELECT 'CREATE ROLE finance_portal'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'finance_portal')\gexec
ALTER ROLE finance_portal WITH LOGIN PASSWORD :'role_password';
ALTER ROLE finance_portal SET default_transaction_read_only = on;

GRANT USAGE ON SCHEMA finance TO finance_portal;

GRANT SELECT (id, order_ref, amount_cents, kind, status, processed_at)
  ON finance.transactions TO finance_portal;
GRANT SELECT (id, brand, last4, exp_month, exp_year)
  ON finance.payment_methods TO finance_portal;
GRANT SELECT (day, gross_cents, refunds_cents)
  ON finance.revenue_daily TO finance_portal;
GRANT SELECT (id, currency, balance_cents)
  ON finance.wallets TO finance_portal;
SQL
