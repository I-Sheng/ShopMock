# ShopMock — infrastructure build specification

Companion to `ShopMock_Company_Infra.md` (the design). This document is the
current design-to-runtime inventory: what actually runs, on what network, from
what image, and where its data comes from. `docker-compose.yml` and
`docker-compose.vm.yml` are authoritative; where this document and the compose
files disagree, the compose files are right.

Operating procedure — deploying, verifying, troubleshooting — lives in
[`DEPLOY.md`](DEPLOY.md). The reasoning behind the choices below, with boundaries
and residual risk, lives in [`DECISIONS.md`](DECISIONS.md).

## Goal and constraints

1. Every node in the design's DFD maps to a real running container. Infrastructure
   and data APIs use pinned upstream images; the customer, seller, and workforce
   journeys use small Next.js and Django components, because authentication and
   data ownership cannot be represented honestly by configuration alone.
2. The design's network segments are real Docker networks, so tier and
   blast-radius boundaries are enforced by the runtime rather than on paper. The
   lab VM is the exception, and is treated as one below.
3. Seed data is explicit: every row a human must supply is listed with its source
   file and destination datastore.
4. Business logic stays thin on purpose. Catalog, order, finance, and customer
   data APIs are PostgREST; Django owns the cross-database checkout and the
   seller and workforce write boundaries; Next.js supplies the storefront.

## Base services

Twenty-eight services in `docker-compose.yml`, grouped by segment.

### DMZ and edge

| Service | Image or build source | Networks | Role |
| --- | --- | --- | --- |
| `edge` | `traefik:v3.6` | edge, tier1, tier2 | Sole ingress. Docker-provider routing off container labels; `:80` public, `:8088` dashboard. No WAF middleware is configured or deployed. |
| `storefront` | build `./storefront` (Next.js) | edge | Customer shop UI; server-side catalog reads go back through the edge. |
| `search` | `opensearchproject/opensearch:2.13.0` | edge, soc | Catalog mirror index, and the store for `wazuh-alerts-*`. `compatibility.override_main_response_version` is required by Wazuh's Filebeat OSS 7.10. |
| `search-dashboard` | `opensearchproject/opensearch-dashboards:2.13.0` | edge | Search admin console on `:5602`. |

### Tier 1 — critical services

| Service | Image or build source | Networks | Role |
| --- | --- | --- | --- |
| `identity` | `quay.io/keycloak/keycloak:24.0` | tier1, mgmt | Customer/seller CIAM and the workforce realm. Public login at `/auth`; `/auth/admin` is blocked at the edge by an IP-allowlist middleware, and the admin console is reached on `:8081`. Workforce users federate from FreeIPA over LDAP on mgmt_net. |
| `catalog-svc` | `postgrest/postgrest:v12.2.0` | tier1, data | REST over the `catalog` schema in catalog-db; anonymous read. |
| `order-svc` | `postgrest/postgrest:v12.2.0` | tier1, data | REST over `sales` in orders-db; RS256 verification plus the `security.check_ciam_customer` pre-request hook. |
| `checkout-svc` | `postgrest/postgrest:v12.2.0` | tier1, data | REST over `finance` in finance-db (mock PCI scope); `record_payment` RPC; same JWT and hook. |
| `customer-svc` | `postgrest/postgrest:v12.2.0` | tier1, data | RPC-only over `commerce` in customer-db; exposes `ensure_customer()`, PII tables are not browsable. |

### Tier 2 — line of business and workforce

| Service | Image or build source | Networks | Role |
| --- | --- | --- | --- |
| `seller-svc` | `postgrest/postgrest:v12.2.0` | tier2, data | Read-only REST over the `seller` schema in catalog-db. |
| `internal-ops-svc` | `postgrest/postgrest:v12.2.0` | tier2, data | REST over the `ops` schema in catalog-db. |
| `internal-service-backend` | build `./internal-service-backend` (Django) | tier2, data | Checkout orchestration across customer-db, orders-db, and finance-db; holds the `internal_backend` login. |
| `seller-backend` | build `./seller-backend` (Django) | tier2, data | Seller writes to own listings in catalog-db and own-sales reads from orders-db; holds `seller_backend`. |
| `oe-dashboard` | build `./oe-dashboard` (Django) | tier2, ops, soc | IT operations console at `/oe`. Container health via the socket proxy over ops_net; Wazuh alerts read from `search` over soc_net with the read-only `wazuh-dashboard-reader` account. No socket and no database of its own. |
| `finance-portal` | build `./finance-portal` (Django) | tier2, data | Finance workforce app at `/finance`; reads finance-db as `finance_portal`. Deliberately not on hr_net. |
| `hr-portal` | build `./hr-portal` (Django) | tier2, hr | HR workforce app at `/hr`; the only client of hr-db, as the read-only `hr_portal` role. No route to data_net. |
| `oe-socket-proxy` | `tecnativa/docker-socket-proxy:0.3.0` | ops | The only container mounting the runtime socket. `CONTAINERS=1`, `POST=0`: read-only `/containers` endpoints, every mutating verb refused. |

### Data backend

| Service | Image | Networks | Role |
| --- | --- | --- | --- |
| `customer-db` | `postgres:16-alpine` | data | Customer PII, accounts, addresses. |
| `catalog-db` | `postgres:16-alpine` | data | Products, pricing, inventory; also hosts the `seller` and `ops` schemas. |
| `orders-db` | `postgres:16-alpine` | data | Orders, line items, shipments. |
| `finance-db` | `postgres:16-alpine` | data | Wallets, tokenized cards, transactions. |
| `hr-db` | `postgres:16-alpine` | hr | Staff directory, departments, leave. The only database with no PostgREST in front of it and no place on data_net. |

### SOC and security operations

| Service | Image | Networks | Role |
| --- | --- | --- | --- |
| `vault` | `hashicorp/vault:1.16` | soc, mgmt | Dev-mode secrets manager on `:8200`; models the HSM/Vault node. |
| `vault-seed` | `hashicorp/vault:1.16` | soc | One-shot: waits for Vault, writes `secret/shopmock/*`, exits. |
| `search-seed` | `curlimages/curl:8.7.1` | edge | One-shot: bulk-indexes the catalog mirror and converges the `wazuh-dashboard-reader` OpenSearch account and role. |
| `wazuh` | `wazuh/wazuh-manager:4.14.7` | soc | SIEM manager. Its Filebeat output targets the existing `search` service; there is no separate Wazuh indexer or dashboard, and no per-host log shipper. |

### Tier 0 control plane and access plane

| Service | Image or build source | Networks | Role |
| --- | --- | --- | --- |
| `ipa` | `quay.io/freeipa/freeipa-server:almalinux-9` | tier0, mgmt | The control plane: 389DS LDAP, Kerberos KDC, Dogtag PKI/CA, HBAC. One systemd container, as a domain controller actually is. Healthy means the install finished and the CA certificate is retrievable. Web UI on `:8443`. |
| `paw` | build `./paw` (AlmaLinux 9 + ipa-client) | bastion, tier0, mgmt | Privileged access workstation, the gated path up to Tier 0 — not Tier 0 itself. Runs systemd, SSSD, oddjobd, SSHD; enrolled once FreeIPA reports healthy, with `BASTION_USER` as break-glass. SSH on `:22`. |

### VM-only services

`docker-compose.vm.yml` adds two services that exist only on the rootless Podman
lab VM:

| Service | Image or build source | Role |
| --- | --- | --- |
| `wazuh-agent` | `wazuh/wazuh-agent:4.14.7` | Container-only endpoint named `shopmock-podman-collector`, enrolled against the manager. It reads the relay's buffer and selected deployment files; package, process, SCA, and rootcheck modules are off, because they would describe the container rather than the host. No Podman socket. |
| `wazuh-journal-relay` | build `./wazuh-journal-relay` | `network_mode: none`. Uses `journalctl` against the read-only host journal to select only named ShopMock workloads and writes JSON to a volume the agent reads. It exists because Wazuh's embedded journal reader opens the bind-mounted rootless journal and returns no records. |

## Networks

Ten networks in the base file. Every one except `edge_net` and `bastion_net` is
`internal: true`, which is what makes "the databases are reachable only through
their owning service" true at the runtime layer.

| Network | Internal | Segment | Attached |
| --- | --- | --- | --- |
| `edge_net` | no | DMZ ingress | edge, storefront, search, search-dashboard, search-seed |
| `bastion_net` | no | Public SSH door | paw only |
| `tier0_net` | yes | Control plane | ipa, paw |
| `tier1_net` | yes | Tier 1 services | edge, identity, catalog/order/checkout/customer-svc |
| `tier2_net` | yes | Tier 2 services | edge, seller-svc, internal-ops-svc, both Django backends, all three workforce portals |
| `data_net` | yes | Data backend | four Postgres databases and their owning services only |
| `soc_net` | yes | Security operations | vault, vault-seed, wazuh, search, oe-dashboard |
| `mgmt_net` | yes | Management plane | ipa, identity, vault, paw |
| `ops_net` | yes | Container status path | oe-dashboard, oe-socket-proxy, nothing else |
| `hr_net` | yes | HR data path | hr-portal, hr-db, nothing else |

Invariants the layout enforces: a database attaches to `data_net` only and its
owning service bridges `data_net` to a tier network; the storefront never touches
`data_net`; the FreeIPA DC shares `tier0_net` with the PAW alone; management
surfaces are reachable only over `mgmt_net`, whose only ingress is the PAW. Of
the three planes, the control plane is Tier 0, FreeIPA; the management plane is
the admin surfaces of workloads — Keycloak admin, Vault, the IPA web UI — on
`mgmt_net`; the access plane is the PAW. The bastion is never Tier 0.

### The VM collapse

On the UWB VM, campus policy mandates a single external `sandboxnet`, so
`docker-compose.vm.yml` overrides almost every service onto it and the tier
segmentation disappears there. Tier 0 is then enforced by identity instead of
reachability: FreeIPA HBAC permits only `tier0-admins` to SSH to the DC and the
PAW, and each service still verifies its own tokens.

Two exceptions are preserved deliberately. `hr-db` stays on `hr_net` alone and
`hr-portal` joins `sandboxnet` and `hr_net`, so staff records remain unreachable
from every other service. `oe-socket-proxy` stays on `ops_net` alone while
`oe-dashboard` joins `sandboxnet` and `ops_net`, so the container API is not
exposed to the flat network. The `wazuh-journal-relay` has no network at all.

## Seed data

Postgres files mounted at `/docker-entrypoint-initdb.d` run in filename order on
first boot only; `scripts/deploy.sh` reapplies the RPCs, token-boundary hooks,
and service roles on every run, because initdb does not re-execute on an existing
volume. Keycloak, Vault, and OpenSearch load by startup import or one-shot job.

| Datastore | Source files | Contents |
| --- | --- | --- |
| Customer DB (`customer`) | `seed/customer-db/01_schema.sql`, `02_seed.sql`, `03_roles.sql`, `04_rpc.sql`, `05_internal_backend_role.sh`, `07_token_boundary.sql` | `commerce` schema, `web_anon`/`authenticator`, customer PII rows, the `customer` write role, `ensure_customer()`, the `internal_backend` login, and the pre-request hook. Anonymous PII reads are revoked. |
| Catalog DB (`catalog`) | `seed/catalog-db/01_schema.sql`, `02_seed.sql`, `05_seller_backend_role.sh` | `catalog`, `seller`, and `ops` schemas; products, categories, pricing, inventory, sellers; the `seller_backend` login. |
| Orders DB (`orders`) | `seed/orders-db/01_schema.sql`, `02_seed.sql`, `03_roles.sql`, `04_rpc.sql`, `05_internal_backend_role.sh`, `06_seller_backend_role.sh`, `07_token_boundary.sql` | `sales` schema, orders and shipments, `place_order()`, both service logins, the pre-request hook. |
| Financial DB (`finance`) | `seed/finance-db/01_schema.sql`, `02_seed.sql`, `03_roles.sql`, `04_rpc.sql`, `05_internal_backend_role.sh`, `06_finance_portal_role.sh`, `07_token_boundary.sql` | `finance` schema, wallets, tokenized cards, transactions, `record_payment()`, the `internal_backend` and read-only `finance_portal` logins, the hook. |
| HR DB (`hr`) | `seed/hr-db/01_schema.sql`, `02_seed.sql`, `03_hr_portal_role.sh` | `hr` schema — departments, employees, leave requests — and the read-only `hr_portal` login, with `CONNECT` revoked from `PUBLIC`. Synthetic people only; no identifiers, addresses, or bank details. |
| Keycloak | `seed/identity/realm-shopmock-ciam.json`, `realm-shopmock-workforce.json`, `realm-shopmock.json` | CIAM realm (self-registration on; `storefront` and `seller-dashboard` clients; seed shoppers and a seller) and the workforce realm (`employee`, `global-admin`, `it-ops`, `finance`, `hr` roles; the `it-operations`, `finance-portal`, `hr-portal`, and `internal-services` clients; the FreeIPA LDAP provider and its mappers). The retired mixed `shopmock` realm is imported but disabled on deploy. |
| FreeIPA | `seed/ipa/bootstrap.sh` | The least-privilege `keycloak-federation` bind account; the tier and job groups; `gadmin`, `it.ops`, `finance.clerk`, `hr.specialist`; the `tier0-access` HBAC rule and `tier0-sudo`, with `allow_all` disabled. Run inside the DC by `deploy.sh`. |
| Vault | `seed/vault/seed-secrets.sh` | `secret/shopmock/db/*`, the payment-gateway key, and the JWT signing key, via the `vault-seed` one-shot. |
| OpenSearch | `seed/search/index-catalog.sh` | The `catalog` mirror index, plus the `wazuh_dashboard_reader` role, the `wazuh-dashboard-reader` internal user, and their mapping. |

Dependency order on a clean volume: customer-db and catalog-db first, then
orders-db and finance-db, which carry customer and product references. Keycloak
is independent, but seed customer emails should match. OpenSearch mirrors
catalog-db and runs after it. Vault is independent. HR is independent of all of
them by design.

Card numbers are stored as opaque tokens plus last four digits, never PANs. All
passwords and keys in seed files are obviously fake lab values. To change a
dataset, edit the source file and recreate the volume — see `DEPLOY.md`.

## Mapping to the design's claims

| Design claim | What makes it real |
| --- | --- |
| Per-service isolation | One container per service; a separate Docker network per tier. |
| Tiered blast radius | `tier1_net`, `tier2_net`, `data_net`, `hr_net`, and `ops_net` are distinct internal networks. |
| Data behind services | Databases sit on `data_net` (or `hr_net`) only; PostgREST or a Django backend is the sole HTTP path in. |
| Bastion path for Tier 0 | The DC and the management surfaces are reachable only through the PAW; on the flat VM network HBAC restricts control-plane SSH to `tier0-admins`. |
| Identity is the key of the kingdom | FreeIPA is the Tier-0 directory and PKI; Keycloak is a Tier-1 CIAM workload that federates employees from it and issues the tokens services trust. |
| Separation of duties | Each workforce app has its own PKCE client, realm role, and data scope; HR is additionally separated at the network and credential layer. |

## Known gaps and deliberate weaknesses

Gaps, in the same spirit as the design's own caveats: there is no WAF — the edge
is a plain reverse proxy, and the ModSecurity CRS container an earlier revision
described was never deployed. There is no detection or incident-response runbook,
no per-host log shipper, and no dedicated Wazuh indexer or analyst dashboard; the
manager writes to the shared OpenSearch instance and `/oe` renders a read-only
alert view. The VM agent is a container collector, not a host endpoint agent.
PostgREST gives thin business logic. These are operational-maturity items.

Deliberate attack surface, kept rather than fixed: `place_order` trusts the
browser-supplied `customer_ref` and per-line `unit_price_cents`, so IDOR and
price tampering are possible by design; `place_order` to `record_payment` is a
best-effort cross-database saga with no atomicity, so a partial failure can leave
an order with no payment row; `BASTION_USER` is a static break-glass account that
bypasses HBAC entirely; and `CONTAINERS=1` on the socket proxy still permits
`GET /containers/{id}/json`, reachable only with code execution inside
`oe-dashboard` plus `ops_net` access. `DECISIONS.md` records the reasoning and
the conditions under which each should be revisited.
