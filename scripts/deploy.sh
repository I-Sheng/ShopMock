#!/usr/bin/env bash
# ShopMock lab deploy — used by the CD pipeline (self-hosted runner) and
# runnable by hand. Idempotent: safe to re-run on every push to main.
#
#   1. docker compose up -d --build   (rebuild changed images, start stack)
#   2. re-apply RPC functions + service roles to the live DBs (initdb scripts
#      only run on fresh volumes, so deploys must apply them explicitly)
#   3. NOTIFY PostgREST to reload its schema cache
set -euo pipefail
cd "$(dirname "$0")/.."

# No docker on this host but podman is here (the UWB VM): make sure the podman
# API socket is up and point both compose (DOCKER_HOST) and the vm override's
# ${DOCKER_SOCK} interpolation at it, unless the caller already set them.
if ! command -v docker >/dev/null 2>&1 && command -v podman >/dev/null 2>&1; then
  if [ "$(id -u)" = "0" ]; then
    sock=/run/podman/podman.sock
    systemctl start podman.socket 2>/dev/null || true
  else
    sock="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/podman/podman.sock"
    systemctl --user start podman.socket 2>/dev/null || true
  fi
  export DOCKER_HOST="${DOCKER_HOST:-unix://$sock}"
  export DOCKER_SOCK="${DOCKER_SOCK:-$sock}"
  if [ ! -S "$sock" ]; then
    echo "deploy: podman socket not found at $sock (systemctl --user enable --now podman.socket)" >&2
    exit 1
  fi
fi

# Pick a compose command: real docker, standalone docker-compose (talks to the
# podman socket via DOCKER_HOST), or podman compose. Override with COMPOSE_CMD.
if [ -n "${COMPOSE_CMD:-}" ]; then
  read -ra COMPOSE <<<"$COMPOSE_CMD"
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
elif command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
  COMPOSE=(podman compose)
else
  echo "deploy: no compose implementation found (docker compose / docker-compose / podman compose)" >&2
  exit 1
fi
echo "deploy: using '${COMPOSE[*]}' (DOCKER_HOST=${DOCKER_HOST:-default})"

# .env is gitignored; a CI checkout won't have it. Allow the runner host to
# point at the canonical copy instead.
if [ ! -f .env ] && [ -n "${SHOPMOCK_ENV_FILE:-}" ]; then
  cp "$SHOPMOCK_ENV_FILE" .env
fi
if [ ! -f .env ]; then
  echo "deploy: .env not found (set SHOPMOCK_ENV_FILE or create .env)" >&2
  exit 1
fi

"${COMPOSE[@]}" up -d --build

echo "deploy: waiting for databases..."
for db in customer-db orders-db finance-db; do
  for _ in $(seq 1 60); do
    "${COMPOSE[@]}" exec -T "$db" pg_isready -U postgres >/dev/null 2>&1 && break
    sleep 2
  done
done

echo "deploy: applying RPC functions..."
"${COMPOSE[@]}" exec -T customer-db psql -v ON_ERROR_STOP=1 -U postgres -d customer -f /docker-entrypoint-initdb.d/04_rpc.sql
"${COMPOSE[@]}" exec -T orders-db   psql -v ON_ERROR_STOP=1 -U postgres -d orders   -f /docker-entrypoint-initdb.d/04_rpc.sql
"${COMPOSE[@]}" exec -T finance-db  psql -v ON_ERROR_STOP=1 -U postgres -d finance  -f /docker-entrypoint-initdb.d/04_rpc.sql

echo "deploy: ensuring internal_backend role..."
pw=$(grep '^INTERNAL_BACKEND_DB_PASSWORD=' .env | cut -d= -f2-)
for db in customer-db orders-db finance-db; do
  "${COMPOSE[@]}" exec -T -e INTERNAL_BACKEND_DB_PASSWORD="$pw" "$db" \
    sh /docker-entrypoint-initdb.d/05_internal_backend_role.sh
done

echo "deploy: reloading PostgREST schema caches..."
"${COMPOSE[@]}" exec -T customer-db psql -U postgres -d customer -c "NOTIFY pgrst, 'reload schema';"
"${COMPOSE[@]}" exec -T orders-db   psql -U postgres -d orders   -c "NOTIFY pgrst, 'reload schema';"
"${COMPOSE[@]}" exec -T finance-db  psql -U postgres -d finance  -c "NOTIFY pgrst, 'reload schema';"

echo "deploy: complete"
