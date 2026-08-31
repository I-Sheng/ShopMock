#!/usr/bin/env bash
# Static verification of the IT-operations wiring — identity seed, FreeIPA
# bootstrap, compose topology and the socket-exposure rules.
#
# Deliberately offline: it reads repository files only, so it runs in CI, on a
# dev box and on the lab VM without a stack up and without touching .env.
# Runtime behaviour (real tokens, real containers) is covered by the
# oe-dashboard Django suite and the manual checks in PLAN_OE_DASHBOARD.md.
set -uo pipefail
cd "$(dirname "$0")/.."

REALM=seed/identity/realm-shopmock.json
IPA=seed/ipa/bootstrap.sh
BASE=docker-compose.yml
VM=docker-compose.vm.yml
LOCAL=docker-compose.local.yml

pass=0 fail=0

ok()   { printf '  \033[32mok\033[0m   %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail + 1)); }
group() { printf '\n%s\n' "$1"; }

# check <description> <command...>  — the command's exit status is the verdict.
check() { local what="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$what"; else bad "$what"; fi; }
# jqt <description> <filter> — the filter must evaluate to true.
jqt() { local what="$1" filter="$2"; if [ "$(jq -r "$filter" "$REALM" 2>/dev/null)" = true ]; then ok "$what"; else bad "$what"; fi; }

group "Keycloak realm seed ($REALM)"
check "realm seed is valid JSON" jq -e . "$REALM"
jqt "realm role 'it-ops' exists" \
    '[.roles.realm[].name] | index("it-ops") != null'
jqt "group /workforce/it-ops maps to the it-ops realm role" \
    '[.groups[] | select(.name=="workforce") | .subGroups[]
      | select(.name=="it-ops") | .realmRoles | index("it-ops") != null] == [true]'
jqt "client 'it-operations' exists and is a public browser client" \
    '[.clients[] | select(.clientId=="it-operations")
      | .publicClient == true and .standardFlowEnabled == true] == [true]'
jqt "client 'it-operations' requires PKCE S256" \
    '[.clients[] | select(.clientId=="it-operations")
      | .attributes["pkce.code.challenge.method"] == "S256"] == [true]'
jqt "client 'it-operations' disables direct password grants" \
    '[.clients[] | select(.clientId=="it-operations")
      | .directAccessGrantsEnabled == false] == [true]'
jqt "client 'it-operations' redirects only under /oe" \
    '[.clients[] | select(.clientId=="it-operations") | .redirectUris[]
      | startswith("http://localhost/oe")] | all and length > 0'
jqt "client 'it-operations' does not stamp a PostgREST 'role' claim" \
    '[.clients[] | select(.clientId=="it-operations")
      | (.protocolMappers // []) | map(select(.config["claim.name"]=="role")) | length] == [0]'
jqt "seller-dashboard is untouched by the /oe route" \
    '[.clients[] | select(.clientId=="seller-dashboard") | .redirectUris[]
      | contains("/oe")] | any | not'
jqt "LDAP group mapper federates cn=it-ops" \
    '[.components["org.keycloak.storage.UserStorageProvider"][]
      | .subComponents["org.keycloak.storage.ldap.mappers.LDAPStorageMapper"][]
      | select(.name=="freeipa groups") | .config["groups.ldap.filter"][0]
      | contains("(cn=it-ops)")] == [true]'
jqt "existing federation mappers are preserved" \
    '[.components["org.keycloak.storage.UserStorageProvider"][]
      | .config.bindDn[0]] == ["uid=keycloak-federation,cn=sysaccounts,cn=etc,dc=shopmock,dc=lab"]'

group "FreeIPA bootstrap ($IPA)"
check "bootstrap is syntactically valid bash" bash -n "$IPA"
check "creates the it-ops group" grep -q 'ensure_group it-ops' "$IPA"
check "creates a dedicated lab IT identity" grep -q 'ensure_user it.ops' "$IPA"
check "IT test password is not hardcoded" grep -q 'ensure_user it.ops.*\$IPA_IT_PASSWORD' "$IPA"
check "adds it.ops to it-ops" grep -qE 'group-add-member it-ops .*--users=it\.ops' "$IPA"
check "keeps it.ops in the federated employees group" grep -qE 'group-add-member employees .*--users=it\.ops' "$IPA"
if grep -qE 'group-add-member it-ops .*--users=(gadmin|finance\.clerk)' "$IPA"; then
  bad "gadmin/finance.clerk are NOT granted it-ops"
else
  ok "gadmin/finance.clerk are NOT granted it-ops"
fi
check "tier-0 HBAC rule is preserved" grep -q 'ensure_hbac tier0-access' "$IPA"

group "Deployment ($0's sibling deploy.sh)"
check "deploy is syntactically valid bash" bash -n scripts/deploy.sh
check "deploy converges the it-ops realm role" grep -q 'it-ops' scripts/deploy.sh
check "deploy registers it-operations redirect URIs" grep -q 'it-operations' scripts/deploy.sh
check "deploy still applies the least-privilege LDAP bind" grep -q 'keycloak-federation' scripts/deploy.sh

group "Compose topology"
check "oe-dashboard build context exists" test -f oe-dashboard/Dockerfile
check "oe-dashboard is routed under /oe" grep -q 'PathPrefix(`/oe`)' "$BASE"
check "a narrowly scoped socket proxy exists" grep -q 'oe-socket-proxy:' "$BASE"
check "the proxy allows only container endpoints" grep -q 'CONTAINERS: "1"' "$BASE"
check "the proxy refuses writes (POST=0)" grep -q 'POST: "0"' "$BASE"
check "local override no longer disables oe-dashboard" bash -c '! grep -q "oe-disabled" '"$LOCAL"
check "vm override keeps SELinux label=disable on the socket reader" \
      bash -c 'awk '\''$0=="  oe-socket-proxy:" {inside=1; next} /^  [A-Za-z0-9_-]+:/ {if(inside) exit} inside'\'' '"$VM"' | grep -q "label=disable"'

# The container that terminates browser requests must never hold the socket.
# Read the oe-dashboard service block out of each compose file and assert it.
socket_in_service() {  # <file> <service>
  awk -v svc="  $2:" '$0 == svc {inside=1; next} /^  [a-z]/ {inside=0} inside' "$1" \
    | grep -q 'docker.sock'
}
if socket_in_service "$BASE" oe-dashboard || socket_in_service "$VM" oe-dashboard; then
  bad "oe-dashboard does not mount the container socket"
else
  ok "oe-dashboard does not mount the container socket"
fi

group "oe-dashboard service"
check "python sources compile" python3 -m compileall -q oe-dashboard
check "no secret is read at import time" bash -c '! grep -rn "os.environ\[" oe-dashboard/oe_dashboard oe-dashboard/ops --include="*.py"'
check "only /containers/json is ever requested" \
      grep -q "_PATH = '/v1.41/containers/json?all=1'" oe-dashboard/ops/docker_client.py

printf '\n%s\n' "verify-it-ops: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
