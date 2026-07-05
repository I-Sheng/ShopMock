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

# The deploy host (UWB VM) runs rootless podman — podman is the first-class
# runtime here, docker only a fallback for dev machines. When podman exists,
# bring its API socket up and point both compose (DOCKER_HOST) and the vm
# override's ${DOCKER_SOCK} interpolation at it, unless the caller already did.
if command -v podman >/dev/null 2>&1; then
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

# Pick a compose command: podman compose first, then the standalone
# docker-compose binary (talks to the podman socket via DOCKER_HOST when one
# was found above), then real docker. Override with COMPOSE_CMD.
if [ -n "${COMPOSE_CMD:-}" ]; then
  read -ra COMPOSE <<<"$COMPOSE_CMD"
elif command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
  COMPOSE=(podman compose)
elif [ -n "${DOCKER_SOCK:-}" ] && command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "deploy: no compose implementation found (podman compose / docker-compose / docker compose)" >&2
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

# On the podman host the vm override must be active — rootless podman cannot
# mount /var/run/docker.sock (the edge's Docker-provider socket), and services
# must join sandboxnet. Honor an explicit COMPOSE_FILE (caller env or .env);
# otherwise switch it on automatically when the podman branch above fired.
if [ -n "${DOCKER_SOCK:-}" ]; then
  effective="${COMPOSE_FILE:-$(grep '^COMPOSE_FILE=' .env | cut -d= -f2- || true)}"
  if [ -z "$effective" ]; then
    export COMPOSE_FILE=docker-compose.yml:docker-compose.vm.yml
    echo "deploy: podman host detected — using vm override (COMPOSE_FILE=$COMPOSE_FILE)"
  elif ! printf '%s' "$effective" | grep -q 'docker-compose\.vm\.yml'; then
    echo "deploy: ERROR: podman host, but COMPOSE_FILE ($effective) does not include docker-compose.vm.yml —" >&2
    echo "        the edge would try to bind /var/run/docker.sock and fail (rootless podman cannot create it)." >&2
    echo "        Fix the COMPOSE_FILE line in .env (or unset it) and re-run." >&2
    exit 1
  fi
fi

# New compose variables can land in the repo before the runner's canonical
# .env learns about them. A missing var would interpolate to a blank string
# (services then disagree with the DB role password), so generate a lab value
# instead. The role scripts below ALTER the password on every deploy, so the
# DB side re-syncs to whatever .env now holds.
for var in SELLER_BACKEND_DB_PASSWORD; do
  if ! grep -q "^${var}=" .env; then
    echo "deploy: ${var} missing from .env — generating a lab value (add it to the canonical env file too)" >&2
    echo "${var}=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> .env
  fi
done

"${COMPOSE[@]}" up -d --build

echo "deploy: waiting for databases..."
for db in customer-db orders-db finance-db catalog-db; do
  ready=
  for _ in $(seq 1 60); do
    "${COMPOSE[@]}" exec -T "$db" pg_isready -U postgres >/dev/null 2>&1 && { ready=1; break; }
    sleep 2
  done
  if [ -z "$ready" ]; then
    echo "deploy: $db never became ready — aborting" >&2
    "${COMPOSE[@]}" ps "$db" >&2 || true
    "${COMPOSE[@]}" logs --tail 25 "$db" >&2 || true
    exit 1
  fi
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

echo "deploy: ensuring seller_backend role..."
pw=$(grep '^SELLER_BACKEND_DB_PASSWORD=' .env | cut -d= -f2-)
"${COMPOSE[@]}" exec -T -e SELLER_BACKEND_DB_PASSWORD="$pw" catalog-db \
  sh /docker-entrypoint-initdb.d/05_seller_backend_role.sh
"${COMPOSE[@]}" exec -T -e SELLER_BACKEND_DB_PASSWORD="$pw" orders-db \
  sh /docker-entrypoint-initdb.d/06_seller_backend_role.sh

echo "deploy: reloading PostgREST schema caches..."
"${COMPOSE[@]}" exec -T customer-db psql -U postgres -d customer -c "NOTIFY pgrst, 'reload schema';"
"${COMPOSE[@]}" exec -T orders-db   psql -U postgres -d orders   -c "NOTIFY pgrst, 'reload schema';"
"${COMPOSE[@]}" exec -T finance-db  psql -U postgres -d finance  -c "NOTIFY pgrst, 'reload schema';"

echo "deploy: complete"
