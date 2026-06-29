# ShopMock — Infrastructure Build Specification

**Companion to:** `ShopMock_Company_Infra.md` (design) — this document turns that
design into a **runnable Docker stack** and a **seed-data plan**.

I-Sheng Lee | Capstone: Autonomous AI-Driven Cyber Attacks | June 2026

---

## 0. Goal & Guiding Constraints

The design doc describes *what* ShopMock should look like (assets, tiers, blast
radius, segmentation). This spec describes *how to actually stand it up* as a
local, attackable lab target:

1. **Every node in the DFD maps to a real, pullable container image** — no
   bespoke application code. Where a "service" has no off-the-shelf product, it
   is realized with a generic-but-real component (`postgrest` over a database,
   `nginx` for the storefront), so the running system is honest about what it is:
   a mock with a real attack surface, not a hand-written app.
2. **Network segments from §4–5 of the design become real Docker networks**, so
   the tier/blast-radius boundaries are enforced by the runtime, not just on paper.
3. **Seed data is explicit**: every piece of data a human must add by hand is
   listed with its source file and its destination datastore (§3 here).

> **Design choice (stated honestly):** business "services" (catalog, order,
> checkout, seller, internal-ops) are backed by **PostgREST** auto-exposing their
> owning database as a REST API. This keeps the data-behind-services invariant
> from design §6a real, gives a genuine HTTP attack surface, and needs zero app
> code. The trade-off: business logic is thin. For the capstone's purpose
> (attack-surface realism + clean segmentation) this is the right altitude.

---

## 1. Service → Image Mapping (answers requirement #1)

Every box in the DFD (`media/image1.png`) is mapped to a concrete image tag you
can `docker pull` today.

| DFD node | Tier / Segment | Real image (pullable) | Role in the lab |
| --- | --- | --- | --- |
| Edge / reverse proxy + WAF | DMZ ingress | `traefik:v3.0` | Single ingress; routes to storefront/services; TLS + WAF middleware |
| WAF ruleset (optional, inline) | DMZ | `owasp/modsecurity-crs:nginx` | OWASP CRS in front of storefront |
| Storefront frontend | DMZ / Tier 2 | `nginx:1.27-alpine` | Static web frontend only; **not** a proxy — routing is traefik's job |
| Search service | DMZ / Tier 2 | `opensearchproject/opensearch:2.13.0` | Product search index (catalog mirror) |
| Search dashboard (ops) | DMZ / Tier 2 | `opensearchproject/opensearch-dashboards:2.13.0` | Search admin UI |
| Identity service / SSO | **Tier 1 (Tier-0 control plane)** | `quay.io/keycloak/keycloak:24.0` | OIDC/SSO — the "key of the kingdom" |
| Catalog service | Tier 1 | `postgrest/postgrest:v12.2.0` | REST API over **catalog-db** |
| Order service | Tier 1 | `postgrest/postgrest:v12.2.0` | REST API over **orders-db** |
| Checkout / Payment service | Tier 1 | `postgrest/postgrest:v12.2.0` | REST API over **finance-db** (mock PCI scope) |
| Seller dashboard service | Tier 2 | `postgrest/postgrest:v12.2.0` | REST API over `seller` schema in **catalog-db** |
| Internal ops service | Tier 2 | `postgrest/postgrest:v12.2.0` | REST API over `ops` schema (internal tooling) |
| Catalog DB | Data backend | `postgres:16-alpine` | Products, pricing, inventory |
| Orders DB | Data backend | `postgres:16-alpine` | Orders, items, shipments |
| Customer DB | Data backend | `postgres:16-alpine` | Customer PII, accounts, addresses |
| Financial / Wallet DB | Data backend | `postgres:16-alpine` | Wallets, tokenized cards, transactions (isolated) |
| Secrets / Key Mgmt (HSM/Vault) | SOC | `hashicorp/vault:1.16` | DB creds, API keys, signing keys |
| SIEM manager | SOC | `wazuh/wazuh-manager:4.8.0` | Log ingest, detection rules |
| SIEM indexer | SOC | `wazuh/wazuh-indexer:4.8.0` | Event storage |
| SIEM dashboard | SOC | `wazuh/wazuh-dashboard:4.8.0` | Analyst console |
| Log shipper (per host) | all segments | `fluent/fluent-bit:3.0` | Ships container logs → SIEM |
| Bastion / jump host | **Tier 0** | `lscr.io/linuxserver/openssh-server:latest` | Only entry to Tier-0 admin path |
| Global Admin console | **Tier 0** | *(Keycloak admin, reached via bastion only)* | Realm/identity governance |

**Why these specific products**

- **Keycloak** is a genuine identity provider (OIDC, SSO, admin console) — it
  *is* the Tier-0 "identity system" from the design, not a stand-in.
- **PostgREST** makes "service in front of a DB, DB never exposed to the web
  tier" literally true: the only way to the data is the service's HTTP API.
- **OpenSearch** is a real search engine; mirroring catalog into it reproduces
  the common "search index leaks data the API wouldn't" attack surface.
- **HashiCorp Vault** is a real secrets manager; it models the HSM/Vault SOC node.
- **Wazuh** is a real open-source SIEM/XDR (manager + indexer + dashboard).
- **Traefik** + **OWASP ModSecurity CRS** give a real edge/WAF.

---

## 2. Network Segmentation (design §4–5 → Docker networks)

Each segment in the DFD becomes a Docker network. The **data networks are
`internal: true`** (no route to the internet and not reachable from the edge),
which is what enforces "DBs are reachable only through their owning service."

| Docker network | Internal? | Segment | Who attaches |
| --- | --- | --- | --- |
| `edge_net` | no | DMZ / public ingress | traefik, storefront, search |
| `tier1_net` | yes | Tier 1 critical services | catalog/order/checkout svc, keycloak, traefik |
| `tier2_net` | yes | Tier 2 line-of-business | seller svc, internal-ops svc, traefik |
| `data_net` | **yes** | Data backend (private) | the 4 Postgres DBs + their owning services only |
| `soc_net` | yes | SOC / security ops | vault, wazuh-*, fluent-bit |
| `mgmt_net` | yes | Tier-0 admin / bastion path | bastion, keycloak (admin), vault (admin) |

Enforced invariants (matching design §6a):

- A DB attaches to `data_net` **only**; its owning service bridges
  `data_net` ↔ its tier network. The storefront never touches `data_net`.
- Tier-0 admin surfaces (Keycloak admin, Vault) are reachable only via
  `mgmt_net`, whose only ingress is the **bastion**.
- Tier 2 cannot route to `tier1_net` or `data_net` except through published APIs.

```
Internet ─▶ traefik (edge_net)        ── the ONLY reverse proxy / router
              ├─▶ storefront (edge_net)          ── static SPA host (no proxying)
              ├─▶ search (edge_net)
              ├─▶ catalog/order/checkout (tier1_net) ─▶ [data_net] ─▶ *-db
              └─▶ seller/internal-ops (tier2_net)     ─▶ [data_net] ─▶ catalog-db

Admin/Operators ─▶ bastion (mgmt_net) ─▶ Keycloak admin / Vault
All containers ─▶ fluent-bit ─▶ Wazuh (soc_net)
```

---

## 3. Manual Seed Data — file → datastore (answers requirement #2)

This is the data a human must add by hand. Postgres files mounted into
`/docker-entrypoint-initdb.d` run **automatically in filename order** on first
boot (`01_` schema before `02_` data). Keycloak/Vault/Search are loaded by an
import on startup or a one-shot job.

| # | What data | Source file (in repo) | Loaded into (datastore) | How it loads |
| --- | --- | --- | --- | --- |
| 1 | Roles + schema for customers, addresses, accounts | `infra/seed/customer-db/01_schema.sql` | **Customer DB** (`postgres`, db `customer`) | initdb |
| 2 | Customer PII rows (accounts, addresses) | `infra/seed/customer-db/02_seed.sql` | **Customer DB** | initdb |
| 3 | Catalog + seller/ops schemas | `infra/seed/catalog-db/01_schema.sql` | **Catalog DB** (db `catalog`) | initdb |
| 4 | Products, categories, pricing, inventory, sellers | `infra/seed/catalog-db/02_seed.sql` | **Catalog DB** | initdb |
| 5 | Orders schema | `infra/seed/orders-db/01_schema.sql` | **Orders DB** (db `orders`) | initdb |
| 6 | Orders, line items, shipments | `infra/seed/orders-db/02_seed.sql` | **Orders DB** | initdb |
| 7 | Finance schema (PCI scope) | `infra/seed/finance-db/01_schema.sql` | **Financial/Wallet DB** (db `finance`) | initdb |
| 8 | Wallets, tokenized cards, transactions, revenue | `infra/seed/finance-db/02_seed.sql` | **Financial/Wallet DB** | initdb |
| 9 | Realm, clients, roles, **users** (customers, sellers, employees, global-admin) | `infra/seed/identity/realm-shopmock.json` | **Identity store** (Keycloak) | `--import-realm` on start |
| 10 | Secrets: DB creds, payment-gateway key, JWT signing key | `infra/seed/vault/seed-secrets.sh` | **Vault** KV (`secret/shopmock/*`) | one-shot job after Vault unseals |
| 11 | Search index documents (catalog mirror) | `infra/seed/search/index-catalog.sh` | **OpenSearch** index `catalog` | one-shot bulk job |

**Data ownership / dependency order** (must seed in this order on a clean volume):

```
customer-db ─┐
catalog-db ──┼─▶ orders-db (references customer + product refs)
             └─▶ finance-db (references customer + order refs)
identity (Keycloak)  ── independent, but customer emails should match seed
search (OpenSearch)  ── mirrors catalog-db, run after catalog is up
vault                ── independent; holds the creds the DBs/services use
```

> **PII / PCI note (capstone-relevant):** rows in Customer DB and Financial DB are
> the lab's "crown jewels" from design §1. Card numbers are stored as **opaque
> tokens + last4 only** (never PANs) — realistic for PCI scope and safe to commit.
> All passwords/keys in seed files are obviously fake lab values.

To change the dataset, edit the `02_seed.sql` (or realm JSON), then recreate the
volume: `docker compose down -v && docker compose up -d`.

---

## 4. How to Build & Run

```bash
cd infra
cp .env.example .env                 # fake lab secrets; edit if you like
docker compose pull                  # pulls every image in §1
docker compose up -d                 # initdb runs all 01_/02_ seed files
docker compose run --rm vault-seed   # loads §3 row 10 into Vault
docker compose run --rm search-seed  # loads §3 row 11 into OpenSearch
```

Endpoints (default): storefront `http://localhost/` (HTTP port 80), Keycloak admin (via
bastion / `mgmt_net`) `:8081`, Wazuh dashboard `:5601`, Vault `:8200`.

See `infra/README.md` for the per-service URL/port table and the bastion login.

---

## 5. Mapping back to the design's robustness claims

| Design claim (§6a) | How this build makes it real |
| --- | --- |
| Per-service isolation | One container per service; separate Docker networks per tier |
| Tiered blast radius | `tier1_net`/`tier2_net`/`data_net` are distinct `internal` networks |
| Data behind services | DBs on `data_net` only; PostgREST is the sole HTTP path in |
| Bastion path for Tier 0 | Keycloak admin + Vault reachable only via `mgmt_net` → bastion |
| Identity = key of kingdom | Keycloak issues the tokens every service trusts |

**Known gaps (same honest caveat as design §6d):** no live detection/IR runbook,
WAF rules are CRS defaults, and PostgREST gives thin business logic. These are
operational-maturity items, not architectural ones.
