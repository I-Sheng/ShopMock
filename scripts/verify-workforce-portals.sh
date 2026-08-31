#!/usr/bin/env bash
# Static verification of the Finance/HR workforce-portal wiring — identity seed,
# FreeIPA bootstrap, database grants, compose topology and the two frontends.
#
# Deliberately offline: it reads repository files only, so it runs in CI, on a
# dev box and on the lab VM without a stack up and without touching .env.
# Runtime behaviour (real tokens, real rows) is covered by the two Django
# suites; cross-service properties that a single-service test image cannot see
# — the two portals sharing no colour, naming different domains — are asserted
# here, where the whole repository is present.
set -uo pipefail
cd "$(dirname "$0")/.."

REALM=seed/identity/realm-shopmock.json
IPA=seed/ipa/bootstrap.sh
BASE=docker-compose.yml
VM=docker-compose.vm.yml
DEPLOY=scripts/deploy.sh
FIN=finance-portal
HR=hr-portal

pass=0 fail=0

ok()   { printf '  \033[32mok\033[0m   %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail + 1)); }
group() { printf '\n%s\n' "$1"; }

# check <description> <command...>  — the command's exit status is the verdict.
check() { local what="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$what"; else bad "$what"; fi; }
# not_check <description> <command...> — the command must FAIL.
not_check() { local what="$1"; shift; if "$@" >/dev/null 2>&1; then bad "$what"; else ok "$what"; fi; }
# jqt <description> <filter> — the filter must evaluate to true.
jqt() { local what="$1" filter="$2"; if [ "$(jq -r "$filter" "$REALM" 2>/dev/null)" = true ]; then ok "$what"; else bad "$what"; fi; }

# Read one service block out of a compose file.
service_block() {   # <file> <service>
  awk -v svc="  $2:" '$0 == svc {inside=1; next} /^  [A-Za-z0-9_-]+:/ {if (inside) exit} inside' "$1"
}

# Join backslash-continued lines, so a check can match a statement that the
# source wraps across several lines.
unwrapped() {   # <file>
  sed -e :a -e '/\\$/N; s/\\\n//; ta' "$1"
}

# Executable Python only: comments and docstrings are dropped, so prose may
# explain a boundary that the code itself must never cross. Assigned constants
# (the SQL statements) survive.
py_code() {   # <file>...
  python3 - "$@" <<'PY'
import ast, sys
for path in sys.argv[1:]:
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            node.value.value = ''
    print(ast.unparse(tree))
PY
}

# SQL/shell statements only: comment lines dropped.
sql_code() {   # <file>
  grep -vE '^\s*(--|#)' "$1"
}

unwrapped_has() { unwrapped "$1" | grep -qE "$2"; }
sql_code_has() { sql_code "$1" | grep -qE "$2"; }
py_code_has() { local pattern="$1"; shift; py_code "$@" | grep -qE "$pattern"; }
service_has() { service_block "$1" "$2" | grep -qE "$3"; }

group "Keycloak realm seed ($REALM)"
check "realm seed is valid JSON" jq -e . "$REALM"
for role in finance hr; do
  jqt "realm role '$role' exists" \
      "[.roles.realm[].name] | index(\"$role\") != null"
  jqt "group /workforce/$role maps to the $role realm role" \
      "[.groups[] | select(.name==\"workforce\") | .subGroups[]
        | select(.name==\"$role\") | .realmRoles | index(\"$role\") != null] == [true]"
done
for client in finance-portal hr-portal; do
  jqt "client '$client' is a public browser client" \
      "[.clients[] | select(.clientId==\"$client\")
        | .publicClient == true and .standardFlowEnabled == true] == [true]"
  jqt "client '$client' requires PKCE S256" \
      "[.clients[] | select(.clientId==\"$client\")
        | .attributes[\"pkce.code.challenge.method\"] == \"S256\"] == [true]"
  jqt "client '$client' disables direct password grants" \
      "[.clients[] | select(.clientId==\"$client\")
        | .directAccessGrantsEnabled == false] == [true]"
  jqt "client '$client' does not stamp a PostgREST 'role' claim" \
      "[.clients[] | select(.clientId==\"$client\")
        | (.protocolMappers // []) | map(select(.config[\"claim.name\"]==\"role\")) | length] == [0]"
done
jqt "client 'finance-portal' redirects only under /finance" \
    '[.clients[] | select(.clientId=="finance-portal") | .redirectUris[]
      | startswith("http://localhost/finance")] | all and length > 0'
jqt "client 'hr-portal' redirects only under /hr" \
    '[.clients[] | select(.clientId=="hr-portal") | .redirectUris[]
      | startswith("http://localhost/hr")] | all and length > 0'
jqt "no other client claims the /finance or /hr routes" \
    '[.clients[] | select(.clientId != "finance-portal" and .clientId != "hr-portal")
      | (.redirectUris // [])[] | test("/(finance|hr)/")] | any | not'
jqt "the IT console is untouched by the new routes" \
    '[.clients[] | select(.clientId=="it-operations") | .redirectUris[]
      | startswith("http://localhost/oe")] | all and length > 0'
jqt "LDAP group mapper federates cn=finance and cn=hr" \
    '[.components["org.keycloak.storage.UserStorageProvider"][]
      | .subComponents["org.keycloak.storage.ldap.mappers.LDAPStorageMapper"][]
      | select(.name=="freeipa groups") | .config["groups.ldap.filter"][0]
      | contains("(cn=finance)") and contains("(cn=hr)") and contains("(cn=it-ops)")] == [true]'
jqt "existing federation bind identity is preserved" \
    '[.components["org.keycloak.storage.UserStorageProvider"][]
      | .config.bindDn[0]] == ["uid=keycloak-federation,cn=sysaccounts,cn=etc,dc=shopmock,dc=lab"]'

group "FreeIPA bootstrap ($IPA)"
check "bootstrap is syntactically valid bash" bash -n "$IPA"
check "creates the finance group" grep -q 'ensure_group finance' "$IPA"
check "creates the hr group" grep -q 'ensure_group hr ' "$IPA"
check "creates a dedicated lab HR identity" grep -q 'ensure_user hr.specialist' "$IPA"
check "finance.clerk joins the finance group" \
      grep -qE 'group-add-member finance .*--users=finance\.clerk' "$IPA"
check "hr.specialist joins the hr group" \
      grep -qE 'group-add-member hr .*--users=hr\.specialist' "$IPA"
check "hr.specialist stays in the federated employees group" \
      unwrapped_has "$IPA" 'group-add-member employees .*--users=hr\.specialist'
check "the HR password comes from the environment" \
      grep -q 'ensure_user hr.specialist.*\$IPA_HR_PASSWORD' "$IPA"
check "the finance password comes from the environment" \
      grep -q 'ensure_user finance.clerk.*\$IPA_FINANCE_PASSWORD' "$IPA"
not_check "no lab password is hardcoded in the bootstrap" \
      grep -qE "ensure_user [a-z.]+ +\"[^\"]*\" +\"[^\"]*\" +\"[^\$]" "$IPA"
# Cross-role separation, asserted on the directory that decides it.
not_check "finance.clerk is NOT granted hr" \
      grep -qE 'group-add-member hr .*--users=finance\.clerk' "$IPA"
not_check "hr.specialist is NOT granted finance" \
      grep -qE 'group-add-member finance .*--users=hr\.specialist' "$IPA"
not_check "neither identity is granted it-ops" \
      grep -qE 'group-add-member it-ops .*--users=(finance\.clerk|hr\.specialist)' "$IPA"
not_check "neither identity is granted tier0-admins" \
      grep -qE 'group-add-member tier0-admins .*--users=(finance\.clerk|hr\.specialist)' "$IPA"
check "tier-0 HBAC rule is preserved" grep -q 'ensure_hbac tier0-access' "$IPA"
check "the it-ops job function is preserved" grep -q 'ensure_group it-ops' "$IPA"

group "Database grants (least privilege)"
FINROLE=seed/finance-db/06_finance_portal_role.sh
HRROLE=seed/hr-db/03_hr_portal_role.sh
check "finance role script is valid sh" sh -n "$FINROLE"
check "hr role script is valid sh" sh -n "$HRROLE"
check "finance_portal password comes from the environment" \
      grep -q 'FINANCE_PORTAL_DB_PASSWORD' "$FINROLE"
check "hr_portal password comes from the environment" \
      grep -q 'HR_PORTAL_DB_PASSWORD' "$HRROLE"
not_check "finance_portal is NOT granted the payment token column" \
      grep -qE 'GRANT SELECT[^;]*\btoken\b' "$FINROLE"
check "finance_portal gets column-level SELECT on payment_methods" \
      grep -q 'GRANT SELECT (id, brand, last4, exp_month, exp_year)' "$FINROLE"
not_check "finance_portal is NOT granted customer_ref" \
      sql_code_has "$FINROLE" 'customer_ref'
not_check "finance_portal is NOT granted web_anon" \
      grep -qE 'GRANT web_anon TO finance_portal' "$FINROLE"
for f in "$FINROLE" "$HRROLE"; do
  check "$(basename "$f") grants no write privilege" \
        bash -c "! grep -qE 'GRANT [^;]*(INSERT|UPDATE|DELETE|TRUNCATE|ALL)' '$f'"
  check "$(basename "$f") pins the session read-only" \
        grep -q 'default_transaction_read_only = on' "$f"
done
check "hr_portal reads only the hr schema" \
      grep -q 'GRANT SELECT ON hr.departments, hr.employees, hr.leave_requests' "$HRROLE"
not_check "the HR seed stores no government identifier or bank detail" \
      grep -qiE '\b(ssn|social_security|national_id|passport|iban|bank_account|routing_number)\b' \
      seed/hr-db/01_schema.sql seed/hr-db/02_seed.sql
check "the HR database has no PostgREST authenticator role" \
      bash -c "! grep -hEv '^\s*(--|#)' seed/hr-db/*.sql seed/hr-db/*.sh | grep -qE '\bauthenticator\b|\bweb_anon\b'"

group "Compose topology ($BASE)"
check "finance-portal build context exists" test -f "$FIN/Dockerfile"
check "hr-portal build context exists" test -f "$HR/Dockerfile"
check "finance-portal is routed under /finance" grep -q 'PathPrefix(`/finance`)' "$BASE"
check "hr-portal is routed under /hr" grep -q 'PathPrefix(`/hr`)' "$BASE"
check "hr-db service exists" grep -q '^  hr-db:' "$BASE"
check "hr_net is an internal network" grep -qE '^  hr_net: +\{ internal: true \}' "$BASE"
check "hr-db data is persisted" grep -qE '^  hr-data: *\{\}' "$BASE"
check "the IT console route is preserved" grep -q 'PathPrefix(`/oe`)' "$BASE"

# The isolation that matters: HR must not be able to reach finance-db, and
# finance-portal must not be able to reach hr-db. On this stack that is a
# network fact, so read it out of the service blocks.
if service_block "$BASE" hr-portal | grep -qE 'networks:.*data_net'; then
  bad "hr-portal is NOT on data_net (no path to finance/customer/order DBs)"
else
  ok "hr-portal is NOT on data_net (no path to finance/customer/order DBs)"
fi
if service_block "$BASE" finance-portal | grep -qE 'networks:.*hr_net'; then
  bad "finance-portal is NOT on hr_net (no path to the HR database)"
else
  ok "finance-portal is NOT on hr_net (no path to the HR database)"
fi
if service_block "$BASE" hr-db | grep -qE 'networks:.*data_net'; then
  bad "hr-db is NOT on the shared data_net"
else
  ok "hr-db is NOT on the shared data_net"
fi
for svc in finance-portal hr-portal; do
  if service_block "$BASE" "$svc" | grep -q 'docker.sock'; then
    bad "$svc does not mount the container socket"
  else
    ok "$svc does not mount the container socket"
  fi
done
check "hr-db keeps its private network on the rootless VM" \
      service_has "$VM" hr-db 'hr_net'
if service_block "$VM" hr-db | grep -q 'sandboxnet'; then
  bad "the vm override does not move hr-db onto the flat sandboxnet"
else
  ok "the vm override does not move hr-db onto the flat sandboxnet"
fi
check "the vm override routes finance-portal through Traefik" \
      service_has "$VM" finance-portal sandboxnet
check "the vm override routes hr-portal through Traefik" \
      service_has "$VM" hr-portal sandboxnet

group "Deployment ($DEPLOY)"
check "deploy is syntactically valid bash" bash -n "$DEPLOY"
check "deploy waits for hr-db" grep -qE 'for db in .*hr-db' "$DEPLOY"
check "deploy converges the finance realm role" grep -q 'ensure_realm_role finance' "$DEPLOY"
check "deploy converges the hr realm role" grep -q 'ensure_realm_role hr ' "$DEPLOY"
check "deploy converges the it-ops realm role" grep -q 'ensure_realm_role it-ops' "$DEPLOY"
check "deploy converges the workforce group role mappings" \
      grep -q 'ensure_workforce_group_role finance' "$DEPLOY"
check "deploy converges a missing /workforce parent group" \
      grep -q 'kc create groups -s name=workforce' "$DEPLOY"
check "deploy creates the finance-portal client" \
      grep -q 'ensure_browser_client finance-portal' "$DEPLOY"
check "deploy creates the hr-portal client" \
      grep -q 'ensure_browser_client hr-portal' "$DEPLOY"
check "deploy registers /finance redirect URIs" grep -q "finance-portal)   echo '/finance/ /finance/\*'" "$DEPLOY"
check "deploy registers /hr redirect URIs" grep -q "hr-portal)        echo '/hr/ /hr/\*'" "$DEPLOY"
check "deploy re-asserts PKCE on both new clients" \
      grep -q 'it-operations|finance-portal|hr-portal' "$DEPLOY"
check "deploy applies the finance_portal DB role" \
      grep -q '06_finance_portal_role.sh' "$DEPLOY"
check "deploy applies the hr_portal DB role" \
      grep -q '03_hr_portal_role.sh' "$DEPLOY"
check "deploy passes the new FreeIPA passwords through" \
      grep -q 'IPA_HR_PASSWORD="\$ipa_hr_pw"' "$DEPLOY"
check "deploy generates missing lab secrets rather than failing" \
      grep -q 'ensure_env_var HR_PORTAL_DB_PASSWORD' "$DEPLOY"
check "deploy still applies the least-privilege LDAP bind" \
      grep -q 'keycloak-federation' "$DEPLOY"
check "deploy still federates cn=it-ops" grep -q 'cn=it-ops' "$DEPLOY"

group "Environment template (.env.example)"
for var in FINANCE_PORTAL_DB_PASSWORD HR_PORTAL_DB_PASSWORD IPA_FINANCE_PASSWORD IPA_HR_PASSWORD; do
  check "$var is documented" grep -q "^${var}=" .env.example
done
check ".env is still ignored by git" grep -qx '.env' .gitignore

group "Portal services"
for svc in "$FIN" "$HR"; do
  check "$svc python sources compile" python3 -m compileall -q "$svc"
  check "$svc reads no secret at import time" \
        bash -c "! grep -rn 'os.environ\[' '$svc' --include='*.py'"
  check "$svc runs as a non-root user" grep -q '^USER ' "$svc/Dockerfile"
done
# The finance service must never name the payment token column anywhere.
not_check "finance-portal never selects the payment token" \
      py_code_has '\btoken\b' "$FIN/ledger/queries.py"
check "finance-portal serializes through an allowlist" \
      grep -q 'FORBIDDEN_FIELDS' "$FIN/ledger/serializers.py"
check "hr-portal serializes through an allowlist" \
      grep -q 'FORBIDDEN_FIELDS' "$HR/people/serializers.py"
# HR must have no reference to any other domain's datastore in executable code.
for foreign in finance-db customer-db orders-db catalog-db payment_methods wallets; do
  mapfile -t hr_sources < <(find "$HR" -name '*.py' ! -path '*/tests/*' -print)
  if py_code "${hr_sources[@]}" | grep -q "$foreign"; then
    bad "hr-portal source never names $foreign"
  else
    ok "hr-portal source never names $foreign"
  fi
done

group "Two genuinely distinct frontends"
FIN_CSS=$FIN/ledger/assets/app.css
HR_CSS=$HR/people/assets/app.css
FIN_HTML=$FIN/ledger/templates/ledger/index.html
HR_HTML=$HR/people/templates/people/index.html
shared=$(comm -12 \
  <(grep -oE '#[0-9a-fA-F]{3,8}' "$FIN_CSS" | tr 'A-F' 'a-f' | sort -u) \
  <(grep -oE '#[0-9a-fA-F]{3,8}' "$HR_CSS"  | tr 'A-F' 'a-f' | sort -u) | wc -l)
if [ "$shared" -eq 0 ]; then
  ok "the two stylesheets share no colour value"
else
  bad "the two stylesheets share no colour value ($shared shared)"
fi
not_check "the two pages are not the same document" cmp -s "$FIN_HTML" "$HR_HTML"
check "the Finance page names its own domain" grep -q '<title>ShopMock — Finance</title>' "$FIN_HTML"
check "the HR page names its own domain" grep -q '<title>ShopMock — People Operations</title>' "$HR_HTML"
not_check "the Finance page does not borrow HR vocabulary" grep -q 'hr-portal' "$FIN_HTML"
not_check "the HR page does not borrow Finance vocabulary" grep -q 'finance-portal' "$HR_HTML"
for css in "$FIN_CSS" "$HR_CSS"; do
  check "$(basename "$(dirname "$(dirname "$css")")") stylesheet is responsive" \
        grep -q '@media' "$css"
  not_check "$(dirname "$css") does not reuse the IT console palette" \
        grep -qE '#8b6ff0|#c3f53c' "$css"
done
for html in "$FIN_HTML" "$HR_HTML"; do
  check "$(basename "$(dirname "$html")") page has a denial state" grep -q 'id="panel-denied"' "$html"
  check "$(basename "$(dirname "$html")") page has a live region" grep -q 'aria-live="polite"' "$html"
  not_check "$(basename "$(dirname "$html")") page uses no inline script" \
        grep -qE '<script(?![^>]*src=)' "$html"
done

printf '\n%s\n' "verify-workforce-portals: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
