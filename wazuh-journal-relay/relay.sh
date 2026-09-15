#!/bin/sh
set -eu

output=/buffer/podman-journal.json
touch "$output"
chmod 0644 "$output"

# Repeated matches on the same journald field are ORed. Different fields would
# be ANDed. Keep this explicit allowlist aligned with the VM Compose services;
# Wazuh's own containers are intentionally absent to prevent collection loops.
exec journalctl \
  --directory=/host/journal \
  --follow \
  --lines=0 \
  --output=json \
  CONTAINER_NAME=shopmock-edge-1 \
  CONTAINER_NAME=shopmock-storefront-1 \
  CONTAINER_NAME=shopmock-search-1 \
  CONTAINER_NAME=shopmock-search-dashboard-1 \
  CONTAINER_NAME=shopmock-identity-1 \
  CONTAINER_NAME=shopmock-catalog-svc-1 \
  CONTAINER_NAME=shopmock-order-svc-1 \
  CONTAINER_NAME=shopmock-checkout-svc-1 \
  CONTAINER_NAME=shopmock-customer-svc-1 \
  CONTAINER_NAME=shopmock-seller-svc-1 \
  CONTAINER_NAME=shopmock-internal-ops-svc-1 \
  CONTAINER_NAME=shopmock-internal-service-backend-1 \
  CONTAINER_NAME=shopmock-seller-backend-1 \
  CONTAINER_NAME=shopmock-oe-dashboard-1 \
  CONTAINER_NAME=shopmock-oe-socket-proxy-1 \
  CONTAINER_NAME=shopmock-finance-portal-1 \
  CONTAINER_NAME=shopmock-hr-portal-1 \
  CONTAINER_NAME=shopmock-customer-db-1 \
  CONTAINER_NAME=shopmock-catalog-db-1 \
  CONTAINER_NAME=shopmock-orders-db-1 \
  CONTAINER_NAME=shopmock-finance-db-1 \
  CONTAINER_NAME=shopmock-hr-db-1 \
  CONTAINER_NAME=shopmock-vault-1 \
  CONTAINER_NAME=shopmock-ipa-1 \
  CONTAINER_NAME=shopmock-paw-1 \
  >> "$output"