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
| Other ports | various | all bind `127.0.0.1` only; PAW on `:2202` (`:22` is the VM's own sshd), FreeIPA Web UI on `:8443` |
| Tier 0 | `tier0_net` segment (FreeIPA DC + PAW) | flat `sandboxnet` — Tier 0 enforced by **FreeIPA HBAC** (identity), not network |
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

# --- Tier 0 (FreeIPA) --------------------------------------------------------------
# The DC is heavy and slow to install — deploy.sh WARNs (does not abort) if it is not
# ready, and re-applies the bootstrap idempotently on the next run.
podman compose logs ipa | tail -20                       # watch first-install progress
podman compose exec -e IPA_ADMIN_PASSWORD="$(grep ^IPA_ADMIN_PASSWORD= .env | cut -d= -f2-)" \
  ipa bash -c 'echo "$IPA_ADMIN_PASSWORD" | kinit admin && ipa user-find && ipa hbacrule-find'
# Expect: employees gadmin + finance.clerk present; hbac rule 'tier0-access' enabled,
# 'allow_all' disabled — i.e. Tier 0 is deny-by-default, tier0-admins only.

# Federation round-trip: gadmin authenticates against FreeIPA *through* Keycloak.
curl -s http://127.0.0.1:5002/auth/realms/shopmock/protocol/openid-connect/token \
  -d grant_type=password -d client_id=seller-dashboard \
  -d username=gadmin -d password="$(grep ^IPA_ADMIN_PASSWORD= .env | cut -d= -f2-)" | jq -r .access_token | head -c 40
```

Browser: `http://shopmock.uwb.edu/isheng07/` (storefront) and
`…/isheng07/seller` (Seller Central). FreeIPA Web UI: tunnel `:8443` through the PAW.

Seed data caveat: Keycloak realm and DB schema files import **only on fresh
volumes**. After changing anything under `seed/`, reseed with
`podman compose down -v && bash scripts/deploy.sh` (destroys all lab data).

## Troubleshooting

| Symptom | Cause → fix |
| --- | --- |
| `mkdir /var/run/docker.sock: permission denied` | vm override not active — the edge tried to bind the docker socket path from the base file. `git pull`; remove/fix any stale `COMPOSE_FILE` line in `.env`; confirm the run prints the "using vm override" line. No sudo is ever needed. |
| DB crash-loops with `ls: can't open '/docker-entrypoint-initdb.d/': Permission denied` | two host-side causes, both handled by deploy.sh now: (a) restrictive umask on the checkout (NETID homes: 077) → `chmod -R a+rX seed/`; (b) SELinux enforcing → `chcon -R -t container_file_t seed/` (the mounts also carry the `z` flag, but docker-compose over the podman socket can drop it). Verify with `ls -Z seed/customer-db` — files must show `container_file_t`, not `user_home_t`. After pulling these fixes run `podman compose down -v` once (the crash loop leaves half-initialized, unseeded data volumes) and redeploy. Full walkthrough: [step-by-step recovery](#step-by-step-recovery-seed-permission-crash-loop). |
| `WARN: The "SELLER_BACKEND_DB_PASSWORD" variable is not set` | `.env` predates the seller backend. Current deploy.sh auto-generates it; add it to the canonical env file to silence permanently. |
| `can only create exec sessions on running containers` / `service "X" is not running` | containers were created by an earlier failed `up` but never started. `podman compose down` (keeps volumes), then re-run the deploy. |
| `network sandboxnet declared as external, but could not be found` | one-time setup step 2 was skipped — create the network. |
| `yaml: unknown !override tag` (or similar parse error) | the compose provider is the Python `podman-compose`, which can't read the override. Install the docker-compose v2 binary (≥ 2.24) so `podman compose` delegates to it, or set `COMPOSE_CMD="docker-compose"`. |
| `deploy: podman socket not found at …` | `systemctl --user enable --now podman.socket` (and `loginctl enable-linger` so it survives logout). |
| `deploy: X never became ready — aborting` + logs | read the printed DB logs — this is a real database failure (bad volume, OOM, crash), not a script problem. |
| Login page loads but sign-in 502s | the stack is still booting behind the edge (Keycloak takes ~30–60 s). Wait and retry. |
| Everything returns `404 page not found` on `127.0.0.1:5002` | that page is Traefik's "no router matched" — the Docker provider registered nothing, almost always because SELinux denies the edge access to the mounted podman socket (`user_tmp_t`). The vm override sets `security_opt: label=disable` on the edge for this; confirm with `podman compose logs edge \| grep -i "permission\|provider"` and check `curl -s http://127.0.0.1:8088/api/http/routers \| head` lists routers after restarting the edge. |
| `deploy: WARN FreeIPA not ready — skipping Tier-0 bootstrap` | the DC's first install is slow (several minutes) or failed. It is **non-fatal** — the rest of the stack is fine. Watch `podman compose logs -f ipa`; once it prints `FreeIPA server configured`, just re-run `bash scripts/deploy.sh` to apply the bootstrap. If it never configures: FreeIPA needs **cgroups v2** and refuses `--privileged`; confirm `podman info \| grep cgroupVersion` says `v2` and that the host has ~2 GB free for it. |
| FreeIPA container exits / `systemd` errors on boot | the systemd-in-container flags need tuning for this host. The service sets `cgroup: host`, `security_opt: seccomp:unconfined`, and tmpfs `/run`+`/tmp`; on some rootless-podman hosts you also need `podman ... --systemd=always`. This is the one bring-up step that may need host-specific iteration — see PLAN_TIER0_FREEIPA.md. |
| PAW SSH works but domain (IPA) logins are refused / no HBAC | the PAW enrolls into FreeIPA **best-effort** at boot; if the DC was not up yet it serves only the `BASTION_USER` break-glass account. Re-create it after the DC is ready: `podman compose up -d --force-recreate paw`, then verify with `podman compose exec paw id gadmin`. HBAC then governs who may log in. |

## Step-by-step recovery: seed permission crash-loop

Full walkthrough for the stubborn variant of the
`ls: can't open '/docker-entrypoint-initdb.d/': Permission denied` crash-loop
(rootless podman + SELinux). deploy.sh handles all of this automatically —
when it *still* fails, one of two things is true: the checkout is running old
code, or the DB volumes were poisoned by earlier crash loops. Work through the
steps in order; each one verifies before moving on.

### 1. Confirm the checkout has the fixes

```bash
cd ~/ShopMock          # or wherever the checkout lives
git fetch origin
git pull
git log --oneline -3
```

The log must include `7362518` (`fix: relabel seed files with chcon ...`) or
newer. **On an older commit, every later step fails again** — stale checkouts
have caused repeat failures before.

### 2. Destroy the poisoned database volumes

A crash-looping Postgres marks its data volume "initialized" *before* the
seed scripts run, so the volume stays permanently empty-but-claimed. Fixing
file labels does nothing for those volumes — they must go:

```bash
podman compose down -v
```

If compose complains about files/socket, be explicit:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.vm.yml \
DOCKER_SOCK=/run/user/$(id -u)/podman/podman.sock \
podman compose down -v
```

### 3. Fix modes and SELinux labels by hand once — and verify

deploy.sh does this too, but doing it manually first shows immediately
whether `chcon` is allowed on this host at all:

```bash
chmod -R a+rX seed/
chcon -R -t container_file_t seed/
ls -Z seed/customer-db | head -3
```

Every line must show `container_file_t`. If it still shows `user_home_t`, or
`chcon` prints `Operation not permitted`, **stop** — the host policy forbids
the relabel, and the fix needs a different approach (named volumes instead of
bind mounts, or an admin request). Don't keep re-running the deploy.

### 4. Deploy

```bash
bash scripts/deploy.sh
```

The first two lines must be:

```
deploy: podman host detected — using vm override (COMPOSE_FILE=docker-compose.yml:docker-compose.vm.yml)
deploy: using 'podman compose' (DOCKER_HOST=unix:///run/user/<uid>/podman/podman.sock)
```

If they're not, stop — a stale `COMPOSE_FILE` line in `.env` is steering the
run at the wrong configuration.

### 5. Confirm the DBs came up seeded

The script fails fast with logs when a DB doesn't start, so a run that
reaches `deploy: complete` is genuinely healthy. Double-check:

```bash
podman compose ps                                        # everything Up
podman compose logs customer-db | grep -i "init\|error" | head
curl -s http://127.0.0.1:5002/api/catalog/products | head -c 200
```

Still failing? Collect exactly these three things before digging further —
together they pinpoint which layer (checkout, labels, or something new) is
wrong:

1. the **first two lines** of the deploy output,
2. `ls -Z seed/customer-db | head -3`,
3. the last ~20 lines the script printed.
