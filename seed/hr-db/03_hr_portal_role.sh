#!/bin/sh
# Login role for hr-portal (Django). Password comes from the container env
# (HR_PORTAL_DB_PASSWORD in .env) so no secret is inlined here.
# Idempotent (create-if-missing + ALTER) so the CD deploy can re-run it.
#
# Least privilege, three ways:
#   * SELECT only — no INSERT/UPDATE/DELETE anywhere, and the session itself
#     defaults to read-only, so even a bug that reached a write statement is
#     refused by Postgres rather than by the application;
#   * named tables only — no CREATE on the schema, so it cannot add its own;
#   * CONNECT revoked from PUBLIC on this database, so only this role and the
#     superuser can open a connection at all. (finance-db is deliberately NOT
#     locked this way: PostgREST's `authenticator` relies on the default PUBLIC
#     connect grant there, and this feature does not change existing behaviour.)
set -e
: "${HR_PORTAL_DB_PASSWORD:?set HR_PORTAL_DB_PASSWORD}"
psql -v ON_ERROR_STOP=1 -v role_password="$HR_PORTAL_DB_PASSWORD" \
  -v db_name="$POSTGRES_DB" -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
SELECT 'CREATE ROLE hr_portal'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hr_portal')\gexec
ALTER ROLE hr_portal WITH LOGIN PASSWORD :'role_password';
ALTER ROLE hr_portal SET default_transaction_read_only = on;

REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db_name" TO hr_portal;

GRANT USAGE ON SCHEMA hr TO hr_portal;
GRANT SELECT ON hr.departments, hr.employees, hr.leave_requests TO hr_portal;
SQL
