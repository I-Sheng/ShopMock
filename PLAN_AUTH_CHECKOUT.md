# Plan — Storefront Login / Sign-up / Checkout

**Companion to:** `INFRA_BUILD_SPEC.md`. This plan makes the customer journey real:
a visitor can **sign up or log in** (Keycloak OIDC) and **check out their cart**
(order + mock payment recorded in the tiered backends), all through the existing
Traefik edge — without breaking the segmentation invariants of spec §2.

Status: **implemented & verified** · Written 2026-07-03 · Completed 2026-07-03

All six phases landed. Backend verified end-to-end via API smoke test (Keycloak
token → `ensure_customer` → `place_order` → `record_payment`, rows confirmed in
each DB); new-user provisioning, PII lockdown (anon 401 / token 403 on the
customer tables), and the `/auth/admin` public block all confirmed. The storefront
builds and serves all pages; Keycloak login + self-registration render through the
edge. Only the interactive browser OIDC redirect was not driven headlessly.

---

## 0. Current gaps (why this doesn't work today)

| # | Gap | Where |
| --- | --- | --- |
| 1 | Browser can't reach Keycloak — it sits on `tier1_net`/`mgmt_net` only, but the OIDC browser flow needs the user's browser to load Keycloak's login/registration pages | `docker-compose.yml` (`identity`) |
| 2 | Realm forbids self-registration (`registrationAllowed: false`) and the `storefront` client's redirect URIs point at stale `http://localhost:8080/*` (shop now on port 80) | `seed/identity/realm-shopmock.json` |
| 3 | All PostgREST services are anonymous **read-only**: every DB grants `web_anon` SELECT only; no `PGRST_JWT_SECRET` configured, so no write path exists for placing an order | `seed/*-db/01_schema.sql`, `docker-compose.yml` |
| 4 | No customer provisioning: `commerce.customers.keycloak_sub` exists for the linkage, but nothing creates a customer row on sign-up — and customer-db deliberately has no public API | `seed/customer-db/01_schema.sql` |
| 5 | Frontend has no auth or checkout UI: static "Hello, sign in" header; localStorage cart with `add()` only; no cart/checkout/orders pages | `storefront/app/*` |

---

## Phase 1 — Route identity through the edge (config only)

**Goal:** login & registration pages reachable at `http://localhost/auth/...`,
same-origin with the storefront (no CORS), Traefik remains the single ingress.

1. `docker-compose.yml` → `identity`:
   - Add env `KC_HTTP_RELATIVE_PATH: /auth` and proxy-header settings
     (`--proxy-headers=xforwarded` for Keycloak 24).
   - Add Traefik labels: router `PathPrefix(/auth)` on
     `traefik.docker.network: shopmock_tier1_net`, service port 8080.
   - **Do not route `/auth/admin` publicly** — add a higher-priority edge rule
     that blocks it (or an allowlist middleware). Admin console stays on the
     mgmt path (`:8081` / bastion), preserving the Tier-0 invariant.
2. `seed/identity/realm-shopmock.json`:
   - `"registrationAllowed": true` — this *is* the sign-up feature (Keycloak's
     built-in registration page; zero custom code).
   - Fix `storefront` client: `redirectUris: ["http://localhost/*"]`,
     `webOrigins: ["http://localhost"]`.
   - Add a protocol mapper on the `storefront` client emitting a top-level
     `"role": "customer"` claim in access tokens (consumed by PostgREST in
     Phase 2). Assign new registrations the `customer` role via the realm's
     default group (`/customers`) so self-registered users get the claim too.

**Verify:** `curl http://localhost/auth/realms/shopmock/.well-known/openid-configuration`
returns the realm; `http://localhost/auth/admin/` is blocked at the edge.

## Phase 2 — JWT-verified writes in PostgREST (config + SQL)

**Goal:** anonymous stays read-only (`web_anon`); a valid Keycloak token
upgrades the request to a `customer` DB role that can write via RPCs.

1. Pin a **fixed RS256 realm keypair** inside `realm-shopmock.json` so the
   public JWK is a static value we can commit — avoids fetching JWKS at
   container startup (PostgREST v12 can't fetch a JWKS URL).
2. `docker-compose.yml` → `order-svc`, `checkout-svc` (and `customer-svc` from
   Phase 3): set `PGRST_JWT_SECRET` to that JWK (via `.env`), and
   `PGRST_JWT_ROLE_CLAIM_KEY: .role`.
3. New seed files `seed/orders-db/03_roles.sql` and
   `seed/finance-db/03_roles.sql`:
   ```sql
   CREATE ROLE customer NOLOGIN;
   GRANT customer TO authenticator;
   GRANT USAGE ON SCHEMA <schema> TO customer;
   -- table grants stay minimal; writes happen through Phase-4 RPCs only
   ```

**Verify:** password-grant token for seeded user `ada` (`Password123!`) →
request with `Authorization: Bearer` no longer runs as `web_anon`
(check via a probe RPC returning `current_user`).

## Phase 3 — Customer provisioning on first login (new service + SQL)

**Goal:** a freshly signed-up Keycloak user gets a `commerce.customers` row,
without giving the customer DB a browsable public API.

1. `docker-compose.yml`: add `customer-svc` (PostgREST over `customer-db`,
   schema `commerce`, networks `tier1_net` + `data_net`), routed at
   `/api/customers` with the same strip-prefix pattern as the other services.
2. `seed/customer-db/03_rpc.sql`: expose **only** one RPC —
   `commerce.ensure_customer()` — a `SECURITY DEFINER` function that reads
   `sub` / `email` / `name` from `current_setting('request.jwt.claims')`,
   upserts the caller's own `customers` (+ `accounts`) row keyed on
   `keycloak_sub`, and returns the customer id. Revoke everything else from
   `customer`; `web_anon` gets nothing. Callers can only ever create/read
   *their own* row → the "customer PII = strictest isolation" invariant holds.

**Verify:** call `POST /api/customers/rpc/ensure_customer` with a token for a
brand-new self-registered user → new row in `commerce.customers` with the
user's Keycloak `sub`; calling twice returns the same id (idempotent).

## Phase 4 — Checkout as transactional RPCs (SQL)

**Goal:** one atomic order write, plus a mock payment record. Plain PostgREST
table inserts can't span multiple inserts in one transaction; RPCs can.

1. `seed/orders-db/04_rpc.sql`: `sales.place_order(customer_ref bigint, items jsonb)`
   — validates the payload, inserts `sales.orders` + `sales.order_items`
   atomically, returns the order id. `GRANT EXECUTE … TO customer` only.
2. `seed/finance-db/04_rpc.sql`: `finance.record_payment(order_ref bigint, amount_cents bigint)`
   — inserts a `charge` row into `finance.transactions`. Called by the
   frontend after `place_order` as a best-effort saga: a true cross-DB
   transaction is impossible with database-per-service, and that's realistic.
3. *(Optional)* catalog RPC to decrement `catalog.inventory` per line item.

> **Honest lab note (attack surface, keep documented not fixed):** the client
> supplies unit prices and its own `customer_ref`, so price tampering and IDOR
> are possible by design. For the capstone this is a feature — record it in
> `INFRA_BUILD_SPEC.md` §5's known-gaps list.

**Verify:** with ada's token, `rpc/place_order` creates order + items rows;
`rpc/record_payment` lands a transaction row; both fail without a token.

## Phase 5 — Storefront UI (the bulk of the work, Next.js)

1. **Auth plumbing:** add `keycloak-js` (PKCE, public `storefront` client,
   auth-server URL `http://localhost/auth`). New `app/auth-context.jsx`
   provider alongside the existing `CartProvider` exposing
   `{ user, token, login, register, logout }`.
2. **Header (`app/header.jsx`):** real state — "Hello, Ada / Sign out" when
   authenticated; "Sign in" → Keycloak login; "New customer? Start here" →
   Keycloak registration page.
3. **Cart:** extend `app/cart-context.jsx` with `remove` / `setQty` / `clear`;
   new `/cart` page joining cart ids against `/api/catalog/products` for
   names/prices/stock, with a running total and a "Proceed to checkout" CTA.
4. **Checkout (`/checkout`):** redirects to login if anonymous. On submit:
   `ensure_customer()` → `place_order(...)` → `record_payment(...)` → clear
   cart → confirmation screen with the order id. Address + payment method are
   mock selections (seeded addresses / wallet).
5. **Orders (`/orders`):** order history for the logged-in customer via
   `GET /api/orders/orders?customer_ref=eq.<id>` with the bearer token.

## Phase 6 — End-to-end verification

1. `docker compose down -v && docker compose up -d --build` — realm and seed
   changes only apply on **fresh volumes**.
2. **API smoke test** (scriptable): password-grant token for `ada` →
   `ensure_customer` → `place_order` → `record_payment` → assert rows via the
   read APIs.
3. **Browser test:** sign up a brand-new user → login round-trip back to the
   storefront → add to cart → checkout → confirmation → order visible in
   `/orders`; confirm the finance transaction row exists.
4. Update `README.md` (new `/auth` and `/api/customers` routes, test-user
   walkthrough) and `INFRA_BUILD_SPEC.md` (§1 service table + §3 seed table
   rows for the new SQL files; known-gaps note from Phase 4).

---

## Effort & order

Phases 1–2 are config-only; Phases 3–4 are ~4 small SQL files plus one compose
service; Phase 5 is most of the work. Phases must land in order — each
verify step depends on the previous phase.
