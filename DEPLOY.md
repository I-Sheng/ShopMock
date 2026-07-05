# Deploying ShopMock to the lab VM

How to deploy the stack to the target machine — the UWB VM
(`shopmock.uwb.edu`), a **rootless Podman** host — either by hand or through
the CD pipeline. For what the stack *is*, see [`README.md`](README.md) and
[`INFRA_BUILD_SPEC.md`](INFRA_BUILD_SPEC.md).

The same script also runs on a plain Docker dev machine (it falls back to
`docker compose` automatically); everything Podman-specific below simply
doesn't apply there.

## How the VM differs from a dev machine

| | Dev machine (Docker) | UWB VM (rootless Podman) |
| --- | --- | --- |
| Runtime | docker daemon | rootless podman + API socket (no sudo anywhere) |
| Compose files | `docker-compose.yml` only | + `docker-compose.vm.yml` override (**required**) |
| Networks | tiered (`dmz_net`, `tier1_net`, …) | single external `sandboxnet` (campus policy) |
| Edge binding | `0.0.0.0:80` | `127.0.0.1:5002` (campus URL `http://shopmock.uwb.edu/isheng07/` forwards here) |
| Other ports | various | all bind `127.0.0.1` only; bastion on `:2202` (`:22` is the VM's own sshd) |
| Traefik's Docker socket | `/var/run/docker.sock` | the podman socket, remapped by the override |

The override is the load-bearing piece: rootless podman **cannot** create
`/var/run/docker.sock`, so running with the base compose file alone fails with
`mkdir /var/run/docker.sock: permission denied`. `scripts/deploy.sh` activates
the override automatically on a podman host — and aborts with an explanation
if a stale `COMPOSE_FILE` in `.env` would prevent that.

## One-time host setup

```bash
# 1. Podman API socket (rootless, survives logout with linger enabled)
systemctl --user enable --now podman.socket
loginctl enable-linger "$USER"
ls -l /run/user/$(id -u)/podman/podman.sock     # must exist

# 2. The admin-mandated network (compose declares it external, so create it once)
podman network create --internal --subnet 10.202.0.0/24 sandboxnet

# 3. A compose provider that understands `!override` tags:
#    docker-compose v2 binary >= 2.24 (the Python podman-compose does NOT work)
podman compose version    # check what provider it delegates to

# 4. Clone
git clone git@github.com:I-Sheng/ShopMock.git && cd ShopMock
```

## `.env` on the VM

```bash
cp .env.example .env
```

Then adjust:

1. **Add the two VM-only lines** (not in `.env.example`):

   ```bash
   COMPOSE_FILE=docker-compose.yml:docker-compose.vm.yml
   DOCKER_SOCK=/run/user/1000/podman/podman.sock    # your uid: /run/user/$(id -u)/...
   ```

   `scripts/deploy.sh` sets both automatically when it detects podman, but
   having them in `.env` means manual `podman compose ps` / `logs` commands
   work too. **Never** leave a `COMPOSE_FILE` line that omits
   `docker-compose.vm.yml` — the deploy script refuses to run with one.

2. **Keep `PGRST_JWT_SECRET` byte-identical to `.env.example`.** It is the
   pinned RS256 *public* JWK matching the realm signing key in
   `seed/identity/realm-shopmock.json`; changing it breaks token verification
   in every PostgREST service and both Django backends.

3. **Make sure `SELLER_BACKEND_DB_PASSWORD` is present** (newer than some
   `.env` copies). If missing, the deploy script generates one and appends it
   to `.env` — but add it to the canonical env file so the warning stops.

4. Everything else (`PG_SUPERUSER_PASSWORD`, `KC_ADMIN_*`, `BASTION_*`,
   `DJANGO_SECRET_KEY`, `INTERNAL_BACKEND_DB_PASSWORD`, …) can keep its lab
   value or be changed freely — they only need to be internally consistent,
   and the role scripts re-`ALTER` DB passwords from `.env` on every deploy.

## Deploying

```bash
bash scripts/deploy.sh
```

The script is idempotent — safe to re-run on every push. It:

1. detects podman, starts/points at its socket, activates the vm override;
2. fills in any missing new `.env` variables with generated lab values;
3. `compose up -d --build` (rebuilds changed images, starts everything);
4. waits for `customer-db`, `orders-db`, `finance-db`, `catalog-db` —
   **aborting with that DB's logs** if one never becomes ready;
5. re-applies the RPC functions and the `internal_backend` +
   `seller_backend` DB roles (initdb scripts only run on fresh volumes, so
   deploys onto existing volumes must do this explicitly);
6. `NOTIFY`s PostgREST to reload schema caches.

A healthy run starts like:

```
deploy: podman host detected — using vm override (COMPOSE_FILE=docker-compose.yml:docker-compose.vm.yml)
deploy: using 'podman compose' (DOCKER_HOST=unix:///run/user/1000/podman/podman.sock)
```

If the first two lines don't say that, stop and check `.env` / `git log` —
the rest of the run is running against the wrong configuration.

### Via CI/CD instead

The `deploy` job in `.github/workflows/cicd.yml` runs `scripts/deploy.sh` on
the self-hosted runner (`[self-hosted, shopmock]`) for **pushes to `main`
only**, gated on the repository variable `DEPLOY_ENABLED == 'true'`. The
checkout has no `.env` (gitignored); the runner supplies it via the
`SHOPMOCK_ENV_FILE` repository variable pointing at the canonical copy on the
VM. Feature branches therefore never auto-deploy — merge to `main` or run the
script by hand.

## Verifying

```bash
podman compose ps                      # everything Up
curl -s http://127.0.0.1:5002/ | head  # storefront through the edge
curl -s http://127.0.0.1:5002/api/catalog/products | head -c 200

# seller login round-trip (token carries role: seller)
TOKEN=$(curl -s http://127.0.0.1:5002/auth/realms/shopmock/protocol/openid-connect/token \
  -d grant_type=password -d client_id=seller-dashboard \
  -d username=nwgadgets -d password='Seller123!' | jq -r .access_token)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:5002/api/seller-backend/listings | jq
```

Browser: `http://shopmock.uwb.edu/isheng07/` (storefront) and
`…/isheng07/seller` (Seller Central).

Seed data caveat: Keycloak realm and DB schema files import **only on fresh
volumes**. After changing anything under `seed/`, reseed with
`podman compose down -v && bash scripts/deploy.sh` (destroys all lab data).

## Troubleshooting

| Symptom | Cause → fix |
| --- | --- |
| `mkdir /var/run/docker.sock: permission denied` | vm override not active — the edge tried to bind the docker socket path from the base file. `git pull`; remove/fix any stale `COMPOSE_FILE` line in `.env`; confirm the run prints the "using vm override" line. No sudo is ever needed. |
| `WARN: The "SELLER_BACKEND_DB_PASSWORD" variable is not set` | `.env` predates the seller backend. Current deploy.sh auto-generates it; add it to the canonical env file to silence permanently. |
| `can only create exec sessions on running containers` / `service "X" is not running` | containers were created by an earlier failed `up` but never started. `podman compose down` (keeps volumes), then re-run the deploy. |
| `network sandboxnet declared as external, but could not be found` | one-time setup step 2 was skipped — create the network. |
| `yaml: unknown !override tag` (or similar parse error) | the compose provider is the Python `podman-compose`, which can't read the override. Install the docker-compose v2 binary (≥ 2.24) so `podman compose` delegates to it, or set `COMPOSE_CMD="docker-compose"`. |
| `deploy: podman socket not found at …` | `systemctl --user enable --now podman.socket` (and `loginctl enable-linger` so it survives logout). |
| `deploy: X never became ready — aborting` + logs | read the printed DB logs — this is a real database failure (bad volume, OOM, crash), not a script problem. |
| Login page loads but sign-in 502s | the stack is still booting behind the edge (Keycloak takes ~30–60 s). Wait and retry. |
