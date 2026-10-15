# Deploying and operating ShopMock

The single operational guide for both targets: a Docker dev machine and the UWB
lab VM (`shopmock.uwb.edu`), a rootless Podman host. `scripts/deploy.sh` handles
both; it detects the runtime and applies the VM override automatically.

See [`README.md`](README.md) for orientation and [`INFRA_BUILD_SPEC.md`](INFRA_BUILD_SPEC.md) for the service inventory.

## How the two targets differ

| | Dev machine (Docker) | Lab VM (rootless Podman) |
| --- | --- | --- |
| Runtime | docker daemon | rootless podman plus its API socket, no sudo anywhere |
| Compose files | `docker-compose.yml` | plus `docker-compose.vm.yml`, required |
| Networks | tiered, all internal except `edge_net` and `bastion_net` | one external `sandboxnet`; `hr_net` and `ops_net` still private |
| Edge | `0.0.0.0:80` | `127.0.0.1:5002`; the campus URL `http://shopmock.uwb.edu/isheng07/` forwards there |
| Tier 0 | `tier0_net` segment | flat network, enforced by FreeIPA HBAC instead |
| Traefik provider socket | `/var/run/docker.sock` | the podman socket, remapped by the override |
| Wazuh | manager only | manager plus a container-only agent and journal relay |

Besides the edge, the stack publishes the Traefik dashboard on `8088`, the
Keycloak admin console on `8081`, OpenSearch Dashboards on `5602`, Vault on
`8200`, and the FreeIPA web UI on `8443`. None is public: on the VM every one
binds loopback only, so reach them by tunnelling through the PAW. PAW SSH is
`22` on a dev machine and `127.0.0.1:2202` on the VM, whose `:22` is its own
sshd.

The override is the load-bearing piece: rootless podman cannot create
`/var/run/docker.sock`, so the base file alone fails with
`mkdir /var/run/docker.sock: permission denied`. `scripts/deploy.sh` activates
the override on a podman host and aborts with an explanation if a stale
`COMPOSE_FILE` in `.env` would prevent that.

## One-time host setup, VM only

```bash
# 1. Podman API socket, rootless, surviving logout
systemctl --user enable --now podman.socket
loginctl enable-linger "$USER"
ls -l /run/user/$(id -u)/podman/podman.sock     # must exist

# 2. The admin-mandated network; compose declares it external
podman network create --internal --subnet 10.202.0.0/24 sandboxnet

# 3. A compose provider that understands `!override` tags: the docker-compose v2
#    binary >= 2.24. The Python podman-compose does not work.
podman compose version

# 4. Clone
git clone git@github.com:I-Sheng/ShopMock.git && cd ShopMock
```

FreeIPA needs cgroups v2 and refuses `--privileged`; confirm
`podman info | grep cgroupVersion` reports `v2` and that roughly 2 GB is free
for it.

## Environment

Run `cp .env.example .env`, then:

- VM only: add `COMPOSE_FILE=docker-compose.yml:docker-compose.vm.yml` and
  `DOCKER_SOCK=/run/user/<uid>/podman/podman.sock`. deploy.sh sets both when it
  detects podman, but having them in `.env` makes manual `podman compose ps` and
  `logs` work too. Never leave a `COMPOSE_FILE` line that omits the override, as
  the script refuses to run with one.
- `PUBLIC_ORIGIN`: the browser-visible origin, with no path. Deploy applies it to
  every Keycloak client's redirect URIs and web origins and derives the trusted
  CIAM issuer from it.
- `PGRST_JWT_SECRET`: keep byte-identical to `.env.example`. It is the pinned
  RS256 public JWK matching the signing key in
  `seed/identity/realm-shopmock-ciam.json`; changing it breaks token
  verification in every PostgREST service and all five Django services.
- Everything else can keep its lab value; the role scripts re-`ALTER` database
  passwords from `.env` on every deploy. The four service-role passwords,
  `WAZUH_DASHBOARD_PASSWORD`, `IPA_FINANCE_PASSWORD`, and `IPA_HR_PASSWORD` are
  generated and appended when an older `.env` lacks them. `OE_USERNAME` and
  `OE_PASSWORD` are vestigial; `/oe` is OIDC only and reads neither.

A FreeIPA password only takes effect for an identity that does not yet exist,
since the bootstrap never resets one. Use `ipa passwd <login>` on the DC.

## Deploying

```bash
bash scripts/deploy.sh
```

Idempotent and safe to re-run on every push. In order it:

1. detects podman, starts and points at its socket, activates the VM override;
2. fills in any missing `.env` variables with generated lab values;
3. relaxes seed file modes, and relabels them with `chcon` under SELinux;
4. runs `compose up -d --build`;
5. waits for all five databases, aborting with that database's logs if one never
   becomes ready;
6. reapplies the RPC functions, the `07_token_boundary.sql` pre-request hooks,
   and the four service roles, then `NOTIFY`s PostgREST to reload its schema
   caches, since initdb scripts only run on fresh volumes;
7. sets `app.ciam_issuer` with `ALTER DATABASE` and force-recreates
   `customer-svc`, `order-svc`, and `checkout-svc`, because pooled sessions do
   not inherit a changed database setting;
8. imports `shopmock-ciam` and `shopmock-workforce` into an existing Keycloak
   volume, disables the retired mixed `shopmock` realm without deleting it, and
   converges the workforce realm roles, the `/workforce/<name>` groups and their
   role mappings, the three PKCE browser clients, and every client's redirect
   URIs and web origins for `PUBLIC_ORIGIN`;
9. waits for FreeIPA and applies the groups, users, and HBAC bootstrap, then
   points Keycloak's LDAP federation at the least-privilege bind identity and
   sets the group mapper path and filter.

On the VM, confirm the first deploy messages say that Podman and the VM override
are active. Otherwise stop and fix `.env` before the script changes the stack.

Step 9 is deliberately non-fatal: FreeIPA's first install is slow, so a
not-ready DC warns and the next run applies the bootstrap.

Reseeding from scratch destroys all lab data and is not a routine update:
`podman compose down -v && bash scripts/deploy.sh`. Use it only for changes to
first-boot schemas or seed rows that `deploy.sh` does not reconcile. RPCs,
service roles, token hooks, realm clients/groups, and FreeIPA policy are
reapplied during a normal deploy.

### Via CI/CD

`.github/workflows/cicd.yml` runs storefront, backend, portal, seed-SQL, and
compose-config checks on every push and pull request. Two jobs are restricted to
pushes on `main`: publishing images to GHCR, and `deploy`, which runs
`scripts/deploy.sh` on the self-hosted runner (`[self-hosted, shopmock]`) and is
additionally gated on the repository variable `DEPLOY_ENABLED == 'true'`. The
checkout has no `.env`, so the runner supplies one through the
`SHOPMOCK_ENV_FILE` repository variable. Feature branches never auto-deploy.

## Verification

Substitute the target origin: `http://localhost` on a dev machine,
`http://127.0.0.1:5002` on the VM. Start with the offline repository checks —
no stack needed, and the fastest way to catch a wiring regression:

```bash
bash scripts/verify-identity-boundary.sh    # CIAM/workforce realm separation
bash scripts/verify-it-ops.sh               # /oe identity, topology, socket exposure
bash scripts/verify-workforce-portals.sh    # finance/HR isolation and grants
```

Core stack and customer path:

```bash
podman compose ps                                     # everything Up
curl -s http://127.0.0.1:5002/ | head
curl -s http://127.0.0.1:5002/api/catalog/products | head -c 200

# seller round-trip; the token carries the seller client role
TOKEN=$(curl -s http://127.0.0.1:5002/auth/realms/shopmock-ciam/protocol/openid-connect/token \
  -d grant_type=password -d client_id=seller-dashboard \
  -d username=nwgadgets -d password='Seller123!' | jq -r .access_token)
curl -s -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:5002/api/seller-backend/listings | jq
```

Workforce portals. `/oe`, `/finance`, and `/hr` use authorization code with PKCE
and have direct grants disabled, so a password grant cannot exercise them; sign
in from a browser as `it.ops`, `finance.clerk`, and `hr.specialist`. Expect each
identity to reach only its own portal, `gadmin` to be refused by all three, and a
missing or malformed token to return 401 rather than 403. The Django suites cover
the same ground offline through each image's `test` target.

Tier 0, FreeIPA and the PAW:

```bash
podman compose logs ipa | tail -20            # first-install progress
podman compose exec -e IPA_ADMIN_PASSWORD="$(grep ^IPA_ADMIN_PASSWORD= .env | cut -d= -f2-)" \
  ipa bash -c 'echo "$IPA_ADMIN_PASSWORD" | kinit admin && ipa user-find && ipa hbacrule-find'
# Expect gadmin, it.ops, finance.clerk, hr.specialist; tier0-access enabled and
# allow_all disabled, so Tier 0 is deny-by-default.

podman compose exec paw systemctl is-active shopmock-paw-setup sssd oddjobd sshd
podman compose exec paw sssctl user-checks -a acct -s sshd gadmin
podman compose exec paw sssctl user-checks -a acct -s sshd finance.clerk
# Expect services active, gadmin allowed, finance.clerk denied.
```

Enrollment and identity resolution survive a PAW restart; if they do not, see
the troubleshooting row for break-glass mode.

## Wazuh topology and verification

The manager starts with the core stack on both targets. The VM override adds a
containerized agent, `shopmock-podman-collector`; do not install the
`wazuh-agent` package on the Ubuntu host, and keep manager and agent on the same
pinned version. A networkless `wazuh-journal-relay` mounts the rootless user
journal read-only, selects only named ShopMock workloads, and writes JSON to a
shared volume the agent reads, with Wazuh's own containers excluded to avoid a
collection loop. The relay exists because Wazuh's embedded journal reader opens
the bind-mounted rootless journal but returns no records; do not simplify it
back to a direct journald `<localfile>` source. The Podman socket is absent from
both containers by design.

Alerts go to the existing `search` service rather than a dedicated Wazuh
indexer, which would not fit the VM's memory budget. Keep
`compatibility.override_main_response_version: "true"` on `search`: without it
Filebeat OSS 7.10 reads OpenSearch 2.13 as Elasticsearch 2.x, sends the removed
bulk `_type` field, and loops on HTTP 400. `search-data` holds both catalog and
Wazuh indices, so rerun `search-seed` after a deployment that first creates that
volume. `search-seed` also converges the `wazuh-dashboard-reader` account, which
has read-only access to `wazuh-alerts-*` and nothing else; `/oe` reads alerts
with it.

The collector is not a full endpoint agent: its package, process, SCA, and
rootcheck views would describe the container rather than the host, so those
modules are off.

```bash
# expect all three up, the agent active, and a non-empty relay buffer
podman compose ps wazuh wazuh-agent wazuh-journal-relay
podman compose exec -T wazuh /var/ossec/bin/agent_control -lc
podman compose logs --tail 100 wazuh | grep -Ei 'filebeat|indexer|error'
podman compose exec -T wazuh-journal-relay wc -l /buffer/podman-journal.json
podman compose exec -T wazuh-agent sed -n '1,120p' /var/ossec/var/run/wazuh-logcollector.state
```

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `mkdir /var/run/docker.sock: permission denied` | VM override not active. Remove or fix a stale `COMPOSE_FILE` line in `.env` and confirm the run prints the "using vm override" line. No sudo is needed. |
| DB crash-loops with `ls: can't open '/docker-entrypoint-initdb.d/': Permission denied` | Restrictive umask on the checkout (NETID homes use 077) or SELinux enforcing. deploy.sh handles both; if it still fails see the recovery section below. |
| `network sandboxnet declared as external, but could not be found` | One-time setup step 2 was skipped. |
| `yaml: unknown !override tag` | The provider is the Python `podman-compose`. Install the docker-compose v2 binary (>= 2.24), or set `COMPOSE_CMD="docker-compose"`. |
| `deploy: podman socket not found at …` | `systemctl --user enable --now podman.socket`, plus `loginctl enable-linger`. |
| `can only create exec sessions on running containers` | Containers were created by an earlier failed `up` but never started. `podman compose down`, which keeps volumes, then redeploy. |
| `deploy: X never became ready — aborting` | A real database failure. Read the printed logs; this is not a script problem. |
| Everything returns `404 page not found` on the edge | Traefik matched no router, almost always because SELinux denies the edge the mounted podman socket. The override sets `security_opt: label=disable` for this; check `podman compose logs edge \| grep -i "permission\|provider"` and that `curl -s http://127.0.0.1:8088/api/http/routers` lists routers. |
| Login page loads but sign-in 502s | Keycloak is still booting behind the edge, 30 to 60 seconds. |
| `deploy: WARN FreeIPA not ready — skipping Tier-0 bootstrap` | Non-fatal; the rest of the stack is fine. Watch `podman compose logs -f ipa` and re-run deploy once it prints `FreeIPA server configured`. |
| FreeIPA container exits or errors on boot | The systemd-in-container flags need host tuning. The service sets `cgroup: host`, `seccomp:unconfined`, and tmpfs `/run` and `/tmp`; some hosts also need `podman ... --systemd=always`. |
| PAW starts in break-glass-only mode | Confirm `ipa` is healthy, then `podman compose up -d --no-deps --build --force-recreate paw`. Check `journalctl -u shopmock-paw-setup` inside the PAW. |
| `gadmin` resolves but SSH account checks deny it | `ipa hbacrule-show tier0-access` must list `ipa.shopmock.lab`, `paw.shopmock.lab`, and service `sshd`. Re-run the bootstrap, clear the SSSD cache, retest. |
| A workforce login succeeds but the portal returns 403 | The realm role is missing. Re-run deploy, which reconverges the `/workforce/<name>` group to realm-role mappings, then re-authenticate so the new token carries the role. |

## Recovery from a seed permission crash-loop

deploy.sh fixes modes and labels automatically, so reaching this means the
volumes were poisoned by earlier crash loops, the host forbids the relabel, or
both. A crash-looping Postgres marks its volume initialized before the seed
scripts run, so it stays permanently empty but claimed and relabelling alone
cannot recover it.

```bash
podman compose down -v                       # the poisoned volumes must go
chmod -R a+rX seed/
chcon -R -t container_file_t seed/
ls -Z seed/customer-db | head -3             # every line must show container_file_t
bash scripts/deploy.sh
```

If those lines still show `user_home_t`, or `chcon` prints
`Operation not permitted`, stop: host policy forbids the relabel, and the fix is
named volumes instead of bind mounts rather than another deploy. If the relabel
worked but a database is still unseeded, collect the first two lines of the
deploy output, `ls -Z seed/customer-db | head -3`, and the last 20 lines the
script printed; together they identify which layer is wrong.
