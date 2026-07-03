#!/bin/sh
# Mirrors the catalog into the Search service (OpenSearch) index `catalog`.
# Runs automatically via the `search-seed` one-shot service in docker-compose.yml.
# In a real build this would read from catalog-db; here we bulk-load the same
# rows as catalog-db/02_seed.sql so search and catalog stay consistent.
set -e

OS_URL="${OS_URL:-https://opensearch:9200}"
OS_AUTH="${OS_AUTH:-admin:ShopMockAdmin123!}"
CURL="curl -sk -u $OS_AUTH"

# depends_on only orders container start; OpenSearch takes a while to come up.
echo "Waiting for OpenSearch at $OS_URL ..."
i=0
until $CURL -f "$OS_URL/_cluster/health" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -ge 90 ] && echo "OpenSearch not ready after 180s, giving up." && exit 1
  sleep 2
done

echo "Creating index 'catalog' on $OS_URL ..."
$CURL -X PUT "$OS_URL/catalog" -H 'Content-Type: application/json' -d '{
  "mappings": { "properties": {
    "sku":   { "type": "keyword" },
    "name":  { "type": "text" },
    "description": { "type": "text" },
    "category": { "type": "keyword" },
    "price_cents": { "type": "integer" }
  }}
}' || true

echo "Bulk indexing catalog documents ..."
$CURL -X POST "$OS_URL/_bulk" -H 'Content-Type: application/x-ndjson' --data-binary '
{ "index": { "_index": "catalog", "_id": "LAP-13-AIR" } }
{ "sku": "LAP-13-AIR", "name": "ShopMock Air 13\"", "description": "13-inch ultralight laptop", "category": "Laptops", "price_cents": 129900 }
{ "index": { "_index": "catalog", "_id": "LAP-15-PRO" } }
{ "sku": "LAP-15-PRO", "name": "ShopMock Pro 15\"", "description": "15-inch performance laptop", "category": "Laptops", "price_cents": 199900 }
{ "index": { "_index": "catalog", "_id": "AUD-BUDS-1" } }
{ "sku": "AUD-BUDS-1", "name": "ShopMock Buds", "description": "Wireless earbuds", "category": "Audio", "price_cents": 12900 }
{ "index": { "_index": "catalog", "_id": "AUD-CANS-1" } }
{ "sku": "AUD-CANS-1", "name": "ShopMock Studio Cans", "description": "Over-ear headphones", "category": "Audio", "price_cents": 24900 }
{ "index": { "_index": "catalog", "_id": "HOM-LAMP-1" } }
{ "sku": "HOM-LAMP-1", "name": "ShopMock Desk Lamp", "description": "LED desk lamp", "category": "Home", "price_cents": 4900 }
'
echo ""
echo "Search seed complete."
