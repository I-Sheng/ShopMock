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

# Rootless podman maps in-container service users (postgres uid 70, keycloak,
# vault, …) to unprivileged subuids, so bind-mounted seed/ files must be world-
# readable for them. Checkouts made under a restrictive umask (NETID homes use
# 077) aren't, which crash-loops the DBs with "can't open
# /docker-entrypoint-initdb.d/: Permission denied". All fake lab data — safe
# to open up, and a no-op on dev machines.
chmod -R a+rX seed/

# SELinux-enforcing hosts additionally need a container-readable label on the
# bind-mounted files (user_home_t is denied to containers). The mounts carry
# the `z` flag, but docker-compose over the podman API socket has been seen
# dropping it — relabel explicitly; chcon on your own files needs no sudo.
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = "Enforcing" ]; then
  chcon -R -t container_file_t seed/ \
    || echo "deploy: warning: could not relabel seed/ for SELinux (chcon failed)" >&2
fi

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

# Keep browser redirects environment-specific without opening Keycloak to
# arbitrary redirect hosts. Compose reads .env itself, but this script does not,
# so load only PUBLIC_ORIGIN explicitly. A trailing slash is normalized away.
public_origin="${PUBLIC_ORIGIN:-$(grep '^PUBLIC_ORIGIN=' .env | cut -d= -f2- || true)}"
# Compose accepts a simply quoted value; normalize that form before validating.
if [[ "$public_origin" == \"*\" ]] || [[ "$public_origin" == \'*\' ]]; then
  public_origin="${public_origin:1:${#public_origin}-2}"
fi
public_origin="${public_origin%/}"
if [ -z "$public_origin" ]; then
  echo "deploy: ERROR: PUBLIC_ORIGIN is not set and was not found in .env" >&2
  exit 1
fi
if [[ ! "$public_origin" =~ ^https?://(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+)(:[0-9]{1,5})?$ ]]; then
  echo "deploy: ERROR: PUBLIC_ORIGIN must be an http(s) origin with no path (got '$public_origin')" >&2
  exit 1
fi

echo "deploy: waiting for Keycloak before applying PUBLIC_ORIGIN=$public_origin..."
kcadm=/opt/keycloak/bin/kcadm.sh
kcadm_config=/tmp/shopmock-kcadm.config
cleanup_kcadm() {
  "${COMPOSE[@]}" exec -T identity rm -f "$kcadm_config" >/dev/null 2>&1 || true
}
trap cleanup_kcadm EXIT
keycloak_ready=
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T identity sh -c \
       'exec /opt/keycloak/bin/kcadm.sh config credentials \
          --config /tmp/shopmock-kcadm.config \
          --server http://127.0.0.1:8080/auth --realm master \
          --user "$KEYCLOAK_ADMIN" --password "$KEYCLOAK_ADMIN_PASSWORD"' \
       >/dev/null 2>&1; then
    keycloak_ready=1
    break
  fi
  sleep 2
done
if [ -z "$keycloak_ready" ]; then
  echo "deploy: ERROR: Keycloak never became ready for client redirect configuration" >&2
  # Emit one actionable error after the quiet readiness retries.
  "${COMPOSE[@]}" exec -T identity sh -c \
    'exec /opt/keycloak/bin/kcadm.sh config credentials \
       --config /tmp/shopmock-kcadm.config \
       --server http://127.0.0.1:8080/auth --realm master \
       --user "$KEYCLOAK_ADMIN" --password "$KEYCLOAK_ADMIN_PASSWORD"' || true
  exit 1
fi

# Every kcadm call below targets the shopmock realm with the session config.
kc() {   # kc <verb> <endpoint> [args...]
  local verb="$1" endpoint="$2"; shift 2
  "${COMPOSE[@]}" exec -T identity "$kcadm" "$verb" "$endpoint" \
    --config "$kcadm_config" -r shopmock "$@"
}

client_uuid_of() {
  local uuid
  uuid=$(kc get clients -q "clientId=$1" --fields id --format csv --noquotes)
  uuid="${uuid##*$'\n'}"
  printf '%s' "${uuid//$'\r'/}"
}

group_child_id() {   # <parent-uuid> <child name>
  { kc get "groups/$1/children" 2>/dev/null || kc get "groups/$1" | jq '.subGroups // []'; } \
    | jq -r --arg n "$2" 'if type=="array" then .[] else empty end | select(.name==$n) | .id' \
    | head -1
}

# --- IT operations identity objects ------------------------------------------
# The realm seed carries these, but Keycloak imports the seed only onto a fresh
# realm volume. Re-assert them so an in-place deploy of an already-running stack
# converges too — same reasoning as the DB role scripts above.
if ! kc get roles/it-ops >/dev/null 2>&1; then
  kc create roles -s name=it-ops \
    -s 'description=Internal IT operations — container health console (/oe)' >/dev/null
  echo "deploy: created Keycloak realm role 'it-ops'"
fi

workforce_gid=$(kc get groups -q search=workforce \
  | jq -r '.[] | select(.name=="workforce") | .id' | head -1)
if [[ "$workforce_gid" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  itops_gid=$(group_child_id "$workforce_gid" it-ops)
  if [[ ! "$itops_gid" =~ ^[0-9a-fA-F-]{36}$ ]]; then
    kc create "groups/$workforce_gid/children" -s name=it-ops >/dev/null
    itops_gid=$(group_child_id "$workforce_gid" it-ops)
    echo "deploy: created Keycloak group /workforce/it-ops"
  fi
  if [[ "$itops_gid" =~ ^[0-9a-fA-F-]{36}$ ]]; then
    if ! kc get "groups/$itops_gid/role-mappings/realm" \
         | jq -e '.[] | select(.name=="it-ops")' >/dev/null; then
      kc add-roles --gid "$itops_gid" --rolename it-ops >/dev/null
    fi
    kc get "groups/$itops_gid/role-mappings/realm" \
      | jq -e '.[] | select(.name=="it-ops")' >/dev/null \
      || { echo "deploy: ERROR: /workforce/it-ops lacks the it-ops role" >&2; exit 1; }
  fi
else
  echo "deploy: WARN /workforce group not found — it-ops federation mapping skipped" >&2
fi

# A dedicated browser client: the console must not ride on the storefront's or
# Seller Central's client, so a token minted there cannot reach /oe.
if [[ ! "$(client_uuid_of it-operations)" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  kc create clients \
    -s clientId=it-operations \
    -s 'description=IT operations console (/oe) — authorization code + PKCE' \
    -s enabled=true -s publicClient=true -s standardFlowEnabled=true \
    -s directAccessGrantsEnabled=false -s serviceAccountsEnabled=false \
    -s 'attributes."pkce.code.challenge.method"=S256' >/dev/null
  echo "deploy: created Keycloak client 'it-operations'"
fi

# --- Browser client callbacks -------------------------------------------------
# These lists are deliberate desired state, not a merge of ad-hoc console
# changes. Keep realm seed defaults and the configured environment origin.
redirect_paths() {
  case "$1" in
    storefront)       echo '/*' ;;
    seller-dashboard) echo '/seller/* /silent-check-sso.html' ;;
    it-operations)    echo '/oe/ /oe/*' ;;
  esac
}

redirect_json() {   # every path, under http://localhost and the public origin
  local origins=("http://localhost") out=() origin path
  [ "$public_origin" != http://localhost ] && origins+=("$public_origin")
  for origin in "${origins[@]}"; do
    # Word splitting is the point: redirect_paths emits a space-separated list.
    for path in $(redirect_paths "$1"); do out+=("\"$origin$path\""); done
  done
  local IFS=,
  printf '[%s]' "${out[*]}"
}

if [ "$public_origin" = http://localhost ]; then
  origins='["http://localhost","+"]'
else
  origins="[\"http://localhost\",\"$public_origin\",\"+\"]"
fi

for client in storefront seller-dashboard it-operations; do
  client_uuid=$(client_uuid_of "$client")
  if [[ ! "$client_uuid" =~ ^[0-9a-fA-F-]{36}$ ]]; then
    echo "deploy: ERROR: invalid UUID for Keycloak client '$client' (got '$client_uuid')" >&2
    exit 1
  fi

  redirects=$(redirect_json "$client")
  extra=()
  # Re-assert PKCE each deploy: downgrading it would silently weaken the console.
  if [ "$client" = it-operations ]; then
    extra=(-s enabled=true -s publicClient=true -s standardFlowEnabled=true \
      -s serviceAccountsEnabled=false -s directAccessGrantsEnabled=false \
      -s 'attributes."pkce.code.challenge.method"=S256')
  fi

  kc update "clients/$client_uuid" \
    -s "redirectUris=$redirects" -s "webOrigins=$origins" \
    ${extra[@]+"${extra[@]}"} >/dev/null
  echo "deploy: Keycloak client '$client' allows redirects for $public_origin"
done
cleanup_kcadm
trap - EXIT

# FreeIPA (Tier-0 control plane) — apply the tier groups + HBAC each deploy (like the DB role
# blocks, the in-container bootstrap is idempotent). Deliberately NON-FATAL: FreeIPA is the
# heavy new component and its first install is slow; a not-ready DC must WARN, not abort the
# whole stack deploy. seed/ipa/bootstrap.sh runs inside the DC (it owns the ipa CLI + KDC).
echo "deploy: waiting for FreeIPA (first install can take several minutes)..."
ipa_pw=$(grep '^IPA_ADMIN_PASSWORD=' .env | cut -d= -f2-)
ipa_dm_pw=$(grep '^IPA_DM_PASSWORD=' .env | cut -d= -f2-)
ipa_bind_pw=$(grep '^IPA_LDAP_BIND_PASSWORD=' .env | cut -d= -f2-)
ipa_it_pw=$(grep '^IPA_IT_PASSWORD=' .env | cut -d= -f2-)
ipa_domain=$(grep '^IPA_DOMAIN=' .env | cut -d= -f2-)
for required in ipa_pw ipa_dm_pw ipa_bind_pw ipa_it_pw ipa_domain; do
  if [ -z "${!required}" ]; then
    echo "deploy: ERROR: ${required#ipa_} is missing from .env" >&2
    exit 1
  fi
done
ipa_ready=
for _ in $(seq 1 90); do   # up to ~15 min on a cold first install
  if "${COMPOSE[@]}" exec -T -e IPA_ADMIN_PASSWORD="$ipa_pw" ipa \
       bash -c 'echo "$IPA_ADMIN_PASSWORD" | kinit admin' >/dev/null 2>&1; then
    ipa_ready=1; break
  fi
  sleep 10
done
if [ -z "$ipa_ready" ]; then
  echo "deploy: WARN FreeIPA not ready — skipping Tier-0 bootstrap (check '${COMPOSE[*]} logs ipa')" >&2
else
  echo "deploy: applying FreeIPA Tier-0 bootstrap (tier groups + HBAC + federation bind)..."
  ipa_bootstrap_ok=
  if "${COMPOSE[@]}" exec -T \
       -e IPA_ADMIN_PASSWORD="$ipa_pw" \
       -e IPA_DM_PASSWORD="$ipa_dm_pw" \
       -e IPA_LDAP_BIND_PASSWORD="$ipa_bind_pw" \
       -e IPA_IT_PASSWORD="$ipa_it_pw" \
       -e IPA_DOMAIN="$ipa_domain" \
       ipa bash /seed/bootstrap.sh; then
    ipa_bootstrap_ok=1
  else
    echo "deploy: WARN Tier-0 bootstrap failed — re-run after the DC settles" >&2
  fi

  # The realm seed deliberately contains no LDAP bind credential. Apply the
  # runtime secret only after the least-privilege FreeIPA system account exists.
  if [ -n "$ipa_bootstrap_ok" ]; then
    trap cleanup_kcadm EXIT
    "${COMPOSE[@]}" exec -T identity sh -c \
      'exec /opt/keycloak/bin/kcadm.sh config credentials \
         --config /tmp/shopmock-kcadm.config \
         --server http://127.0.0.1:8080/auth --realm master \
         --user "$KEYCLOAK_ADMIN" --password "$KEYCLOAK_ADMIN_PASSWORD"' >/dev/null
    ldap_id=$("${COMPOSE[@]}" exec -T identity "$kcadm" get components \
      --config "$kcadm_config" -r shopmock | jq -r \
      '.[] | select(.providerId == "ldap" and .name == "freeipa") | .id')
    if [[ ! "$ldap_id" =~ ^[0-9a-fA-F-]{36}$ ]]; then
      echo "deploy: ERROR: could not identify the Keycloak freeipa LDAP component" >&2
      exit 1
    fi
    bind_dn="uid=keycloak-federation,cn=sysaccounts,cn=etc,dc=${ipa_domain//./,dc=}"
    "${COMPOSE[@]}" exec -T identity "$kcadm" update "components/$ldap_id" \
      --config "$kcadm_config" -r shopmock \
      -s "config.bindDn=[\"$bind_dn\"]" \
      -s "config.bindCredential=[\"$ipa_bind_pw\"]" >/dev/null

    # Keycloak validates a custom groups.path while importing the mapper, before
    # same-file realm groups exist. The seed therefore omits this one property;
    # set it after the realm and /workforce group have been imported.
    group_mapper_id=$("${COMPOSE[@]}" exec -T identity "$kcadm" get components \
      --config "$kcadm_config" -r shopmock | jq -r \
      '.[] | select(.providerId == "group-ldap-mapper" and .name == "freeipa groups") | .id')
    if [[ ! "$group_mapper_id" =~ ^[0-9a-fA-F-]{36}$ ]]; then
      echo "deploy: ERROR: could not identify the Keycloak FreeIPA group mapper" >&2
      exit 1
    fi
    # groups.ldap.filter is re-asserted here too: a stack that federated before
    # it-ops existed would otherwise never import the new group.
    "${COMPOSE[@]}" exec -T identity "$kcadm" update "components/$group_mapper_id" \
      --config "$kcadm_config" -r shopmock \
      -s 'config."groups.path"=["/workforce"]' \
      -s 'config."groups.ldap.filter"=["(|(cn=employees)(cn=tier0-admins)(cn=server-admins)(cn=helpdesk)(cn=it-ops))"]' >/dev/null
    cleanup_kcadm
    trap - EXIT
    echo "deploy: Keycloak federation uses the least-privilege FreeIPA bind identity"
  fi
fi

echo "deploy: complete"
