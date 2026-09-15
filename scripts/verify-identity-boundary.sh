#!/usr/bin/env bash
# Offline regression checks for the Keycloak CIAM/workforce trust boundary.
set -uo pipefail
cd "$(dirname "$0")/.."
pass=0 fail=0
check() { local label="$1"; shift; if "$@" >/dev/null 2>&1; then pass=$((pass+1)); printf 'ok - %s\n' "$label"; else fail=$((fail+1)); printf 'FAIL - %s\n' "$label"; fi; }
ciam=seed/identity/realm-shopmock-ciam.json
workforce=seed/identity/realm-shopmock-workforce.json
retired=seed/identity/realm-shopmock.json

check 'all realm imports are valid JSON' jq -e . "$ciam" "$workforce" "$retired"
check 'retired mixed realm is disabled' jq -e '.realm=="shopmock" and .enabled==false and .registrationAllowed==false' "$retired"
check 'CIAM has only commerce clients and no federation' jq -e '([.clients[].clientId]|sort)==["seller-dashboard","storefront"] and (.components|not)' "$ciam"
check 'workforce has no commerce clients or native users' jq -e '([.clients[].clientId]|index("storefront")|not) and ([.clients[].clientId]|index("seller-dashboard")|not) and (.users|length)==0' "$workforce"
check 'registration defaults to customer membership' jq -e '.registrationAllowed and (.defaultRoles|index("customer")!=null) and (.roles.realm[]|select(.name=="customer")|.composites.client.storefront==["customer"])' "$ciam"
check 'seller membership is not a registration default' jq -e '(.defaultRoles|index("seller")|not) and (.users[]|select(.username=="nwgadgets")|.realmRoles==["seller"])' "$ciam"
check 'hardcoded role claim mappers are gone' bash -c '! jq -e '\''[..|objects|select(.protocolMapper?=="oidc-hardcoded-claim-mapper")] | length>0'\'' ' "$ciam"
check 'commerce client roles use RealmRepresentation.roles.client' jq -e '(.roles.client.storefront[]|select(.name=="customer")) and (.roles.client["seller-dashboard"][]|select(.name=="seller")) and ([.clients[]|has("roles")]|any|not)' "$ciam"
check 'commerce role claims derive from client roles' jq -e '[.clients[].protocolMappers[]|.protocolMapper] | all(.=="oidc-usermodel-client-role-mapper")' "$ciam"
check 'frontends use CIAM realm' bash -c '! rg -n "realm: '\''shopmock'\''" storefront/app'
check 'workforce services default to workforce realm' bash -c 'rg -l "OIDC_REALM.*shopmock-workforce" oe-dashboard/oe_dashboard/settings.py finance-portal/finance_portal/settings.py hr-portal/hr_portal/settings.py | wc -l | grep -qx 3'
check 'PostgREST services use the DB pre-request gate' bash -c 'grep -c "PGRST_DB_PRE_REQUEST: security.check_ciam_customer" docker-compose.yml | grep -qx 3'
check 'DB gate checks issuer, type, client, membership, and subject' bash -c 'for f in seed/{customer,orders,finance}-db/07_token_boundary.sql; do grep -q "claims->>.*iss" "$f" && grep -q "claims->>.*typ" "$f" && grep -q "claims->>.*azp" "$f" && grep -q "resource_access" "$f" && grep -q "sub" "$f" || exit; done'
check 'deploy creates both replacement realms and disables retired realm' bash -c 'grep -q "for realm in shopmock-ciam shopmock-workforce" scripts/deploy.sh && grep -q "update realms/shopmock" scripts/deploy.sh'
check 'deploy restarts PostgREST pooled connections after issuer setting' grep -q -- '--force-recreate customer-svc order-svc checkout-svc' scripts/deploy.sh

printf 'verify-identity-boundary: %s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
