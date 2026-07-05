# ShopMock Infrastructure

Runnable Docker realization of the design in
[`../ShopMock_Company_Infra.md`](../ShopMock_Company_Infra.md). For the full
rationale, service→image map, and seed-data plan see
[`../INFRA_BUILD_SPEC.md`](../INFRA_BUILD_SPEC.md).

## Quick start

```bash
cp .env.example .env          # fake lab secrets
docker compose pull           # pull every image
docker compose up -d          # boots stack (incl. Wazuh) and seeds everything
```

Deploying to the lab VM (rootless podman, campus URL) is different — see
[`DEPLOY.md`](DEPLOY.md).

Every component seeds itself on `up`:

- Postgres DBs run `seed/<db>/01_schema.sql` then `02_seed.sql` on first boot.
- Keycloak imports `seed/identity/realm-shopmock.json` on start.
- The one-shot `vault-seed` and `search-seed` containers wait for their
  service to be ready, run `seed/vault/seed-secrets.sh` /
  `seed/search/index-catalog.sh`, and exit.

To reseed from scratch: `docker compose down -v && docker compose up -d`.

## Endpoints

| Service | URL | Notes |
| --- | --- | --- |
| Storefront (edge) | http://localhost/ | links to each API (HTTP port 80) |
| Seller Central (UI) | http://localhost/seller | seller login, listings manager, sales dashboard |
| Login / Sign-up (Keycloak) | http://localhost/auth/realms/shopmock/account | public OIDC login + self-registration, same-origin via the edge |
| Catalog API | http://localhost/api/catalog/products | PostgREST |
| Orders API | http://localhost/api/orders/orders | PostgREST; `POST /api/orders/rpc/place_order` (token) |
| Checkout/Payment API | http://localhost/api/checkout/transactions | PostgREST (finance); `POST /api/checkout/rpc/record_payment` (token) |
| Customer API | http://localhost/api/customers/rpc/ensure_customer | PostgREST (customer PII) — **RPC only**, tables not browsable |
| Seller API (Tier 2) | http://localhost/api/seller/sellers | PostgREST (read-only browse) |
| Seller Backend API (Tier 2) | http://localhost/api/seller-backend/listings | Django; seller token required (see below) |
| Internal Ops API (Tier 2) | http://localhost/api/ops/feature_flags | PostgREST |
| Traefik dashboard | http://localhost:8088 | lab only |
| Identity admin (Keycloak) | http://localhost:8081 | admin console — mgmt-only; `/auth/admin` is blocked at the public edge |
| Search dashboard | http://localhost:5602 | OpenSearch Dashboards |
| Vault | http://localhost:8200 | dev mode, token in `.env` |
| Bastion (SSH) | `ssh <BASTION_USER>@localhost` (port 22) | only path into `mgmt_net` |

## Customer login & checkout

The storefront supports the full customer journey — **sign up / log in** (Keycloak
OIDC, PKCE) and **check out a cart** (order + mock payment) — all same-origin
through the edge. See `../PLAN_AUTH_CHECKOUT.md` for the design.

- **Log in:** header → "Account & Lists". Redirects to Keycloak at `/auth`, back
  to the storefront with a token.
- **Sign up:** header → "New customer? Start here" (Keycloak self-registration is
  enabled). A first login provisions a `commerce.customers` row automatically via
  the `ensure_customer()` RPC — keyed on the user's Keycloak `sub`.
- **Check out:** add to cart → `/cart` → `/checkout`. Anonymous users are prompted
  to sign in. Placing an order calls, with the bearer token:
  `ensure_customer()` → `place_order()` → `record_payment()`, then shows the order id.
- **Order history:** header → "Returns & Orders" (`/orders`).

How the write path is gated: PostgREST verifies the Keycloak RS256 token against a
pinned public JWK (`PGRST_JWT_SECRET` in `.env`, matching the realm's signing key).
Anonymous requests run as `web_anon` (read-only); a valid token's `role: customer`
claim upgrades the request to the `customer` DB role, which may run the checkout
RPCs. Customer PII is never browsable — `customer-svc` exposes only the
`ensure_customer()` RPC, which returns just the caller's own id.

> **Deliberate lab weakness (attack surface, not a bug):** the browser supplies the
> `customer_ref` and per-line prices to `place_order`, so IDOR and price tampering
> are possible by design — realistic targets for the capstone.

Test logins (lab only): `ada` / `Password123!` (customer),
`nwgadgets` / `Seller123!` (seller), `gadmin` / `ChangeMe-Tier0!` (global-admin).
Or register a fresh customer from the storefront.

## Seller backend (Tier 2)

`seller-backend` is a Django service (like `internal-service-backend`) that owns
all seller write paths. Data boundary: it connects **only** to catalog-db
(`seller` + `catalog` schemas) and orders-db (read-only) — customer PII and
finance data stay with `internal-service-backend`.

Auth: a token from the `seller-dashboard` client (which stamps `role: seller`)
verified against the same pinned realm JWK. Ownership is always derived from
the verified `sub` claim → the caller's `seller.sellers` row.

Two login doors, two roles: customers sign in from the storefront header
(`storefront` client → `role: customer`); sellers sign in at **Seller Central**
(`http://localhost/seller`, `seller-dashboard` client → `role: seller`). Same
Keycloak realm behind both — only the client (and therefore the stamped role
claim and landing page) differs. Seller Central lets a seller add products
(SKU, price, stock, category), edit or deactivate their listings, and see
per-order sale lines with unit/gross totals. Try it with `nwgadgets` /
`Seller123!`.

| Endpoint | Method | What it does |
| --- | --- | --- |
| `/api/seller-backend/healthz` | GET | liveness, no auth |
| `/api/seller-backend/sellers/ensure` | POST | idempotent seller provisioning keyed on Keycloak `sub` |
| `/api/seller-backend/listings` | GET / POST | own listings; create product + inventory + listing atomically |
| `/api/seller-backend/listings/<id>` | PATCH | update own `name`, `description`, `price_cents`, `active`, `qty` (commission is platform-set) |
| `/api/seller-backend/sales` | GET | order lines for own SKUs from orders-db, with unit/gross totals |

Smoke test (after a fresh `docker compose up`, realm import needs fresh volumes):

```bash
TOKEN=$(curl -s http://localhost/auth/realms/shopmock/protocol/openid-connect/token \
  -d grant_type=password -d client_id=seller-dashboard \
  -d username=nwgadgets -d password='Seller123!' | jq -r .access_token)
curl -s -H "Authorization: Bearer $TOKEN" http://localhost/api/seller-backend/listings | jq
```

## Ports & exposure

**Only one port should be public: `80` (the storefront/API edge — standard HTTP).**
Everything else is either admin (reach via the bastion) or strictly internal.

### Host-published ports (in `docker-compose.yml`)

| Host port | → container:port | Service | Purpose | Expose to public? |
| --- | --- | --- | --- | --- |
| **80** | edge (traefik) :80 | Edge / reverse proxy | The single public ingress — storefront + all `/api/*` (HTTP) | ✅ **Yes** (the only one) |
| 22 | bastion :2222 | Bastion (SSH) | Controlled admin entry; the only door into `mgmt_net` | ⚠️ Restricted — IP-allowlist / VPN, never open-internet |
| 8081 | identity (keycloak) :8080 | Identity admin console | Realm/user/role administration | ❌ No — mgmt-only (via bastion) |
| 8200 | vault :8200 | Secrets/Key mgmt | Vault API + UI | ❌ No — mgmt-only (via bastion) |
| 8088 | edge (traefik) :8080 | Traefik dashboard | Routing introspection | ❌ No — lab debug only |
| 5602 | search-dashboard :5601 | OpenSearch Dashboards | Search ops UI | ❌ No — mgmt-only |

> The four `❌`/`⚠️` ports are published to the host **only for lab convenience**.
> To match the design (single public edge; Tier-0 reachable solely via the bastion
> on `mgmt_net`), delete the `ports:` entries for `identity`, `vault`, the traefik
> dashboard, and `search-dashboard` — then reach them by SSH-tunnelling through the
> bastion. For HTTPS, terminate TLS at the edge and publish **443** alongside
> (or instead of) port 80.

### Internal-only ports (never published; reachable only on their Docker network)

| Container:port | Service | Reachable from | Purpose |
| --- | --- | --- | --- |
| storefront :80 | Storefront (nginx) | edge_net (via traefik) | Static frontend |
| `*-svc` :3000 | 5× PostgREST APIs | tier1_net / tier2_net (via traefik) | Catalog/Order/Checkout/Seller/Ops APIs |
| search :9200 | OpenSearch | edge_net | Search index API |
| identity :8080 | Keycloak | tier1_net | OIDC/SSO token issuance to services |
| `*-db` :5432 | 4× PostgreSQL | **data_net only** | Datastores — no route from the edge |
| vault :8200 | Vault | soc_net | Secret reads by services |

The databases bind to `data_net` (an `internal: true` network) and publish **no**
host port, so there is no path to them from the public side — see below.

## How segmentation is enforced

Networks `tier1_net`, `tier2_net`, `data_net`, `soc_net`, `mgmt_net` are
`internal: true` (no internet, not bridged to the edge). A database attaches to
`data_net` only; its owning service bridges `data_net` ↔ its tier network. The
storefront/edge never touches `data_net`, so the DBs cannot be reached directly
from the public side — matching design §6a.

## Seed data → datastore

| File | Datastore |
| --- | --- |
| `seed/customer-db/0*.sql` | Customer DB (PII) |
| `seed/catalog-db/0*.sql` | Catalog DB (products, sellers, ops flags) |
| `seed/orders-db/0*.sql` | Orders DB |
| `seed/finance-db/0*.sql` | Financial/Wallet DB (PCI-scope, tokenized cards) |
| `seed/identity/realm-shopmock.json` | Keycloak (identity store) |
| `seed/vault/seed-secrets.sh` | Vault KV (`secret/shopmock/*`) |
| `seed/search/index-catalog.sh` | OpenSearch index `catalog` |

Test logins are listed under **Customer login & checkout** above.

## Resource requirements (maximum)

Size the host for the **worst case: the full stack with the SIEM on (default) and
the log volume at its 20 GB cap.** These are the numbers to provision for.

**Provision for the maximum: 16 GB RAM · 8 vCPU · 50 GB SSD.**

Per-container working set at peak:

| Container | Image | vCPU | RAM |
| --- | --- | --- | --- |
| `edge` (traefik) | traefik | 0.25 | 128 MB |
| `storefront` (nginx) | nginx | 0.10 | 64 MB |
| 5× `*-svc` (postgrest) | postgrest | 0.50 | 240 MB |
| `identity` (keycloak) | keycloak | 0.50 | 1 GB |
| `search` (opensearch) | opensearch | 1.00 | 2 GB |
| `search-dashboard` | opensearch-dashboards | 0.25 | 768 MB |
| 4× `*-db` (postgres) | postgres | 1.00 | 1 GB |
| `vault` | vault | 0.10 | 128 MB |
| `bastion` | openssh-server | 0.05 | 64 MB |
| `wazuh-manager` | wazuh-manager | 0.50 | 1 GB |
| `wazuh-indexer` | wazuh-indexer | 1.00 | 2.5 GB |
| `wazuh-dashboard` | wazuh-dashboard | 0.25 | 768 MB |
| **Working-set total** | | **~5.5 vCPU** | **~9.5 GB** |
| **Provision (≈30% headroom + Docker/OS)** | | **8 vCPU** | **16 GB** |

Maximum storage (~50 GB SSD):

| Item | Size |
| --- | --- |
| Container images (OpenSearch ×2 + Wazuh ×3 dominate) | ~7 GB |
| **Log retention** — `wazuh-indexer` hot volume | **20 GB** |
| Database volumes (seed <100 MB; headroom for order/txn growth) | ~10 GB |
| Search index + Keycloak + OS/Docker overhead | ~6 GB |
| Buffer | ~7 GB |

> The 20 GB log cap buys roughly **2–3 weeks** of hot, searchable retention at this
> lab's ingest (~1–2 GB/day). Higher ingest shrinks the window — that's the dial to
> watch. Run **core only** (Wazuh removed from the compose file, logs shipped
> elsewhere) and the box drops to **8 GB RAM · 4 vCPU · 25 GB**.
>
> This is the all-in-one single-host figure. Production ShopMock (Tier-1 replication
> + Tier-0 multi-VM per design §4.1) would be 3–4 nodes, ~48–64 GB RAM aggregate.

## SIEM (on by default)

The Wazuh manager starts with the core stack — no profile flag needed. Only
the manager runs by default; the full single-node bundle (indexer + dashboard)
needs a one-time certificate bootstrap (see the
[Wazuh Docker docs](https://documentation.wazuh.com/current/deployment-options/docker/index.html))
before it can be added.

## All images (pullable)

`traefik:v3.0` · `nginx:1.27-alpine` · `opensearchproject/opensearch:2.13.0` ·
`opensearchproject/opensearch-dashboards:2.13.0` · `quay.io/keycloak/keycloak:24.0` ·
`postgrest/postgrest:v12.2.0` · `postgres:16-alpine` · `hashicorp/vault:1.16` ·
`curlimages/curl:8.7.1` · `lscr.io/linuxserver/openssh-server:latest` ·
`wazuh/wazuh-manager:4.8.0`
