# ShopMock Infrastructure

Runnable Docker realization of the design in
[`../ShopMock_Company_Infra.md`](../ShopMock_Company_Infra.md). For the full
rationale, service→image map, and seed-data plan see
[`../INFRA_BUILD_SPEC.md`](../INFRA_BUILD_SPEC.md).

## Quick start

```bash
cp .env.example .env          # fake lab secrets
docker compose pull           # pull every image
docker compose up -d          # boots stack; Postgres runs all seed/*-db SQL
docker compose --profile seed run --rm vault-seed    # load secrets into Vault
docker compose --profile seed run --rm search-seed   # index catalog into OpenSearch
```

First boot of the databases runs the `seed/<db>/01_schema.sql` then
`02_seed.sql` automatically. To reseed from scratch:
`docker compose down -v && docker compose up -d`.

## Endpoints

| Service | URL | Notes |
| --- | --- | --- |
| Storefront (edge) | http://localhost:8080 | links to each API |
| Catalog API | http://localhost:8080/api/catalog/products | PostgREST |
| Orders API | http://localhost:8080/api/orders/orders | PostgREST |
| Checkout/Payment API | http://localhost:8080/api/checkout/transactions | PostgREST (finance) |
| Seller API (Tier 2) | http://localhost:8080/api/seller/sellers | PostgREST |
| Internal Ops API (Tier 2) | http://localhost:8080/api/ops/feature_flags | PostgREST |
| Traefik dashboard | http://localhost:8088 | lab only |
| Identity (Keycloak) | http://localhost:8081 | admin console — treat as mgmt-only |
| Search dashboard | http://localhost:5602 | OpenSearch Dashboards |
| Vault | http://localhost:8200 | dev mode, token in `.env` |
| Bastion (SSH) | `ssh -p 2222 <BASTION_USER>@localhost` | only path into `mgmt_net` |

## Ports & exposure

**Only one port should be public: `8080` (the storefront/API edge).** Everything
else is either admin (reach via the bastion) or strictly internal.

### Host-published ports (in `docker-compose.yml`)

| Host port | → container:port | Service | Purpose | Expose to public? |
| --- | --- | --- | --- | --- |
| **8080** | edge (traefik) :80 | Edge / reverse proxy | The single public ingress — storefront + all `/api/*` | ✅ **Yes** (the only one) |
| 2222 | bastion :2222 | Bastion (SSH) | Controlled admin entry; the only door into `mgmt_net` | ⚠️ Restricted — IP-allowlist / VPN, never open-internet |
| 8081 | identity (keycloak) :8080 | Identity admin console | Realm/user/role administration | ❌ No — mgmt-only (via bastion) |
| 8200 | vault :8200 | Secrets/Key mgmt | Vault API + UI | ❌ No — mgmt-only (via bastion) |
| 8088 | edge (traefik) :8080 | Traefik dashboard | Routing introspection | ❌ No — lab debug only |
| 5602 | search-dashboard :5601 | OpenSearch Dashboards | Search ops UI | ❌ No — mgmt-only |

> The four `❌`/`⚠️` ports are published to the host **only for lab convenience**.
> To match the design (single public edge; Tier-0 reachable solely via the bastion
> on `mgmt_net`), delete the `ports:` entries for `identity`, `vault`, the traefik
> dashboard, and `search-dashboard` — then reach them by SSH-tunnelling through the
> bastion. For real exposure, terminate TLS at the edge and publish **443** instead
> of (or alongside) 8080.

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

Test logins (lab only): `ada` / `Password123!` (customer),
`nwgadgets` / `Seller123!` (seller), `gadmin` / `ChangeMe-Tier0!` (global-admin).

## Resource requirements (maximum)

Size the host for the **worst case: the full stack with the SIEM profile on and
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
> watch. Run **core only** (no `siem` profile, logs shipped elsewhere) and the box
> drops to **8 GB RAM · 4 vCPU · 25 GB**.
>
> This is the all-in-one single-host figure. Production ShopMock (Tier-1 replication
> + Tier-0 multi-VM per design §4.1) would be 3–4 nodes, ~48–64 GB RAM aggregate.

## Optional: SIEM

```bash
docker compose --profile siem up -d wazuh
```

Wazuh single-node needs a one-time certificate bootstrap (see the
[Wazuh Docker docs](https://documentation.wazuh.com/current/deployment-options/docker/index.html));
the manager is kept behind the `siem` profile so the core stack stays light.

## All images (pullable)

`traefik:v3.0` · `nginx:1.27-alpine` · `opensearchproject/opensearch:2.13.0` ·
`opensearchproject/opensearch-dashboards:2.13.0` · `quay.io/keycloak/keycloak:24.0` ·
`postgrest/postgrest:v12.2.0` · `postgres:16-alpine` · `hashicorp/vault:1.16` ·
`curlimages/curl:8.7.1` · `lscr.io/linuxserver/openssh-server:latest` ·
`wazuh/wazuh-manager:4.8.0`
