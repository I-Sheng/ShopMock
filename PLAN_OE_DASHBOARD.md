# IT Operations Container Dashboard

Status: implemented and verified in the live rootless-Podman deployment

## Purpose

Provide an internal, read-only view of every container in the `shopmock` Compose project. Access is limited to the FreeIPA `it-ops` workforce group, federated into Keycloak as the `it-ops` realm role.

## Identity model

- FreeIPA group: `it-ops`
- Lab verification identity: `it.ops`
- Keycloak group: `/workforce/it-ops`
- Keycloak realm role: `it-ops`
- Dedicated public browser client: `it-operations`
- Flow: authorization code with mandatory PKCE S256
- Direct password grants: disabled

`gadmin`, `finance.clerk`, customers, and sellers are denied unless separately added to `it-ops`. Global administration does not implicitly grant IT operations access.

## Architecture

```text
Browser
  │  /oe/ + OIDC code/PKCE
  ▼
Traefik
  ▼
oe-dashboard (unprivileged Django)
  │  verified RS256 access token; issuer + client + realm role
  │  GET /v1.41/containers/json?all=1 only
  ▼
oe-socket-proxy (GET-only container API)
  │  rootless Podman Docker-compatible socket
  ▼
ShopMock project containers
```

The browser-facing service never mounts the container socket. It exposes only an allowlisted projection: short ID, project, service, container name, image, state, status, health, creation time, age, exit code, and an `ok` verdict. Environment variables, commands, mounts, ports, networks, and arbitrary labels are never returned.

## UI

The responsive Sentry-inspired interface uses deep purple surfaces and a lime status accent. It provides:

- signed-out, denied, error, and authorized states;
- summary tiles for total, running, healthy, starting, completed jobs, failing, and not-started;
- a complete container table that becomes labeled cards on mobile;
- manual refresh and 15-second auto-refresh with exponential failure backoff;
- text labels in addition to color, keyboard focus styles, a skip link, live-region updates, and reduced-motion support.

## Security controls

- RS256 only against the pinned realm public JWK.
- Required `exp`, `iat`, `iss`, and `sub` claims.
- Trusted issuer allowlist.
- Access-token type check.
- `azp`/audience must identify `it-operations`.
- Realm role must contain exactly `it-ops`; a client role or lookalike is insufficient.
- Authorization runs before any container API call.
- GET-only endpoint; write methods return 405.
- Responses use `Cache-Control: no-store` and restrictive CSP/frame/content-type headers.
- Project label filtering prevents co-tenant container disclosure.
- Backend failures return a generic message; socket paths and internals stay in server logs.

Residual risk: `CONTAINERS=1` in the generic socket proxy permits other GET container endpoints, including inspect, to callers already on its private network. `oe-dashboard` makes one hard-coded list request and shares `ops_net` only with the proxy. A purpose-built one-endpoint proxy would further reduce this residual risk.

## Tests

```bash
bash scripts/verify-it-ops.sh
python3 oe-dashboard/manage.py test ops.tests
```

The Django suite covers positive IT access, missing/malformed/expired/foreign tokens, wrong issuer/client/token type, non-IT workforce users, customer/seller denial, response allowlisting, project filtering, health/exit normalization, summary counts, no-store behavior, and contained backend failures.

## Runtime acceptance

- `/oe/` loads through Traefik.
- `it.ops` completes authorization code + PKCE and can read `/oe/api/containers`.
- `gadmin` and `finance.clerk` receive HTTP 403 from the API.
- Missing/malformed tokens receive HTTP 401.
- Dashboard reports all 25 project containers after deployment: 23 running services and two successfully completed one-shot seed jobs, assuming the baseline stack is otherwise healthy.
- Storefront, seller, Keycloak, FreeIPA, PAW, and existing API checks remain green.
