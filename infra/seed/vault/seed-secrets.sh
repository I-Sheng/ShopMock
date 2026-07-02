#!/bin/sh
# Seeds the Secrets/Key-Management node (HashiCorp Vault) with the credentials
# the rest of the stack uses. Runs automatically via the `vault-seed` one-shot
# service in docker-compose.yml (dev mode auto-unseals).
set -e

export VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
export VAULT_TOKEN="${VAULT_TOKEN:-lab-root-token}"

# depends_on only orders container start; wait until Vault answers.
echo "Waiting for Vault at $VAULT_ADDR ..."
i=0
until vault status >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -ge 60 ] && echo "Vault not ready after 120s, giving up." && exit 1
  sleep 2
done

echo "Seeding Vault at $VAULT_ADDR ..."

# Enable a KV v2 mount for ShopMock (ignore error if already enabled)
vault secrets enable -path=secret kv-v2 2>/dev/null || true

# --- Database credentials (per design: services read creds from Vault) ---
vault kv put secret/shopmock/db/customer  username=authenticator password=customer_pgrst_pw
vault kv put secret/shopmock/db/catalog   username=authenticator password=catalog_pgrst_pw
vault kv put secret/shopmock/db/orders    username=authenticator password=orders_pgrst_pw
vault kv put secret/shopmock/db/finance   username=authenticator password=finance_pgrst_pw

# --- Payment gateway + signing keys (Money / Identity crown jewels) ---
vault kv put secret/shopmock/payment/gateway \
  provider=mockpay api_key=sk_lab_live_DO_NOT_USE_0123456789

vault kv put secret/shopmock/identity/jwt \
  signing_key=lab-hs256-signing-key-change-me

echo "Vault seed complete."
