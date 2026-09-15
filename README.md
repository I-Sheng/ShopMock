# ShopMock

ShopMock is a deliberately attackable mock e-commerce company: a storefront,
seller marketplace, three internal workforce applications, a FreeIPA control
plane, and a SIEM, running as one container stack. It exists as a target for the
capstone project "Autonomous AI-Driven Cyber Attacks", so several weaknesses in
it are intentional and documented rather than fixed.

Everything runs on Docker on a dev machine and on rootless Podman on the lab VM.

## Documentation map

| Document | Purpose |
| --- | --- |
| [`ShopMock_Company_Infra.md`](ShopMock_Company_Infra.md) | Design artifact: assets, tier model, robustness analysis |
| [`INFRA_BUILD_SPEC.md`](INFRA_BUILD_SPEC.md) | Design-to-runtime inventory: services, images, networks, seed data |
| [`DEPLOY.md`](DEPLOY.md) | The operational guide: deploy, verify, troubleshoot |
| [`DECISIONS.md`](DECISIONS.md) | Why the build looks the way it does, and what would change it |

Offline repository checks live in `scripts/verify-identity-boundary.sh`,
`scripts/verify-it-ops.sh`, and `scripts/verify-workforce-portals.sh`. They read
files only and need no running stack.

## Architecture and trust boundaries

Traefik is the only ingress. Every HTTP surface is reached through it on one
origin, so the browser never talks to a service directly.

Two identity domains, deliberately separate:

- `shopmock-ciam` (Keycloak) holds customers and sellers as native users.
- `shopmock-workforce` (Keycloak) holds employees federated from FreeIPA over
  LDAP. FreeIPA is the Tier 0 control plane: LDAP, Kerberos, PKI, and HBAC.

A token issued in one realm is rejected by the other. Services check the exact
issuer, the token type, the authorized party, and the required role server-side
before touching a database.

Tiers map to Docker networks on a dev machine. `tier0_net`, `tier1_net`,
`tier2_net`, `data_net`, `soc_net`, `mgmt_net`, `ops_net`, and `hr_net` are all
internal. A database attaches only to `data_net` and its owning service bridges
`data_net` to a tier network, so no database is reachable from the edge. On the
lab VM campus policy forces one flat `sandboxnet`, and Tier 0 is then enforced by
identity instead: HBAC allows only `tier0-admins` to SSH to the DC and the PAW.
`hr_net` and `ops_net` stay private on both targets.

Three planes: FreeIPA is the control plane, the PAW is the access plane that
administrators traverse upward, and the Keycloak admin console, Vault, and the
IPA web UI are management surfaces that are never routed publicly.

## Public endpoints

All paths below are relative to the deployment origin — `http://localhost` on a
dev machine, the campus URL on the VM. See [`DEPLOY.md`](DEPLOY.md) for the
per-target bindings and the management ports, which are not public.

| Path | Service | Notes |
| --- | --- | --- |
| `/` | storefront (Next.js) | shop UI; Seller Central at `/seller` |
| `/auth` | Keycloak | CIAM login and self-registration; `/auth/admin` is blocked at the edge |
| `/api/catalog` | catalog-svc (PostgREST) | anonymous product reads |
| `/api/orders` | order-svc (PostgREST) | reads; `POST rpc/place_order` needs a customer token |
| `/api/checkout` | checkout-svc (PostgREST) | finance scope; `POST rpc/record_payment` needs a customer token |
| `/api/customers` | customer-svc (PostgREST) | RPC only — `rpc/ensure_customer`; tables are not browsable |
| `/api/seller` | seller-svc (PostgREST) | read-only seller browse |
| `/api/seller-backend` | seller-backend (Django) | seller write paths; seller token required |
| `/api/internal` | internal-service-backend (Django) | checkout orchestration across customer, orders, finance |
| `/api/ops` | internal-ops-svc (PostgREST) | internal feature flags |
| `/oe` | oe-dashboard (Django) | container health and Wazuh alerts; `it-ops` role |
| `/finance` | finance-portal (Django) | read-only ledger reporting; `finance` role |
| `/hr` | hr-portal (Django) | read-only staff reporting; `hr` role |

## Test identities

Lab values only. Customer and seller accounts are native Keycloak users;
workforce accounts live in FreeIPA and reach Keycloak through federation.

| Identity | Realm | Password | Grants |
| --- | --- | --- | --- |
| `ada` | ciam | `Password123!` | customer: cart, checkout, order history |
| `nwgadgets` | ciam | `Seller123!` | seller: Seller Central listings and sales |
| `it.ops` | workforce | `IPA_IT_PASSWORD` | `it-ops` role, `/oe` only |
| `finance.clerk` | workforce | `IPA_FINANCE_PASSWORD` | `finance` role, `/finance` only |
| `hr.specialist` | workforce | `IPA_HR_PASSWORD` | `hr` role, `/hr` only |
| `gadmin` | FreeIPA | `IPA_ADMIN_PASSWORD` | `tier0-admins`: SSH and sudo on the PAW and the DC |

New customers can also self-register from the storefront header. A first login
provisions a `commerce.customers` row through `ensure_customer()`, keyed on the
Keycloak `sub`.

The three job functions are peers, not a hierarchy: `gadmin` has no access to
`/oe`, `/finance`, or `/hr`, and `finance.clerk` is denied Tier 0 SSH. Passwords
come from `.env`; the FreeIPA bootstrap creates a user only if it is missing, so
changing a variable does not rotate an identity that already exists. Use
`ipa passwd <login>` on the DC for that.

## Quick start

```bash
cp .env.example .env     # fake lab secrets
bash scripts/deploy.sh   # build, start, wait, and apply idempotent bootstrap data
```

The script is idempotent and is the supported way to bring the stack up on both
targets — it detects rootless Podman, applies the VM override, and reapplies the
database roles, RPCs, realm objects, and FreeIPA bootstrap that only run once on
fresh volumes. Full procedure, verification, and troubleshooting are in
[`DEPLOY.md`](DEPLOY.md).

## Current limitations

Intentional weaknesses, kept as capstone targets:

- `place_order` trusts the browser-supplied `customer_ref` and per-line unit
  prices, so IDOR and price tampering are possible.
- `place_order` and `record_payment` span two databases with no distributed
  transaction, so a partial failure can leave an order without a payment row.
- `BASTION_USER` is a static local break-glass account on the PAW that bypasses
  HBAC.
- All seed passwords, tokens, and card values are fake, and Vault runs in dev
  mode. Card data is stored as opaque tokens plus last four digits only.

Not built, and not claimed:

- No WAF. The design names OWASP ModSecurity CRS at the edge; no such service is
  deployed.
- No dedicated Wazuh indexer or dashboard. A second OpenSearch JVM does not fit
  the VM, so Filebeat writes `wazuh-alerts-*` into the existing `search` service
  and the alerts are read from `/oe`.
- No host-level endpoint agent. The VM runs a container-only Wazuh collector, so
  host audit, kernel, package, and active-response coverage is absent.
- No per-host log shipping layer, no detection or incident-response runbook, and
  no replication or multi-region resilience. This is a single-host lab.
- Business logic is thin by design: PostgREST fronts most data, and Django owns
  only the cross-database checkout and seller write boundaries.
