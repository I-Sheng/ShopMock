# Backing up and restoring ShopMock

How to take a snapshot of a running ShopMock deployment, credentials included,
and either roll the stack back to it or rebuild it on a fresh host if the live
one is lost. It assumes rootless Podman, as on the lab VM, and covers where
each piece of state lives on the host, because that decides which backup method
is safe.

See [`DEPLOY.md`](DEPLOY.md) for the deploy itself; a restore ends with an
ordinary `bash scripts/deploy.sh`.

## What a snapshot has to contain

`deploy.sh` rebuilds everything that is in git, so a snapshot only needs the
state that is not.

| Piece | Lives in | Without a backup |
| --- | --- | --- |
| Source, compose files, seed data | git | Recoverable from the remote; record the commit |
| Credentials | `.env`, untracked, mode `600` | **Unrecoverable.** Regenerated passwords no longer match the identities already in FreeIPA |
| Customer, catalog, orders, finance, HR rows | `*-data` named volumes | Reseeded; every row written at runtime is lost |
| FreeIPA: users, Kerberos keys, CA | `ipa-data` named volume | Reinstalled with a new CA, new keys, and only the bootstrap users |
| Wazuh manager state and agent keys | `wazuh-*` named volumes | Reinstalled; alert history and agent registration lost |
| Catalog search index and Wazuh alerts | `search-data` named volume | Empty until `search-seed` reruns; alert history lost |
| Keycloak realms and users | **container layer, no volume** | Realms reimported from `seed/identity`; self-registered users and runtime changes lost |
| Vault | dev mode, in memory | Nothing to lose; `vault-seed` repopulates it |

Two consequences are worth stating plainly:

- The minimum useful snapshot is `.env` plus the commit hash. With those two a
  fresh host comes back to the seeded lab state with the same credentials.
- Keycloak is not in any volume. `identity` runs `start-dev`, which keeps an H2
  database under `/opt/keycloak/data/h2` inside the container's writable layer.
  A volume backup never captures it, and recreating the container, for example
  with `compose up --force-recreate` or `down`, discards it. Export it
  separately, as below.

## How volumes are stored

### Naming

Compose prefixes every named volume with the project name, which
`docker-compose.yml` pins to `shopmock` regardless of the checkout directory:
`search-data` becomes `shopmock_search-data` on the host. Compose attaches an
existing volume of that name as it is, and a container that finds data there
skips its first-time initialization. A restore must therefore recreate the
volumes under those exact names, or compose creates new empty ones beside them.

The volumes declared across both compose files:

| Volume | Mounted at | Service | Source file |
| --- | --- | --- | --- |
| `customer-data`, `catalog-data`, `orders-data`, `finance-data`, `hr-data` | `/var/lib/postgresql/data` | the five `*-db` services | base |
| `ipa-data` | `/data` | `ipa` | base |
| `search-data` | `/usr/share/opensearch/data` | `search` | base |
| `wazuh-api-configuration`, `wazuh-etc`, `wazuh-logs`, `wazuh-queue`, `wazuh-var-multigroups`, `wazuh-integrations`, `wazuh-active-response`, `wazuh-agentless`, `wazuh-wodles`, `wazuh-filebeat-etc`, `wazuh-filebeat-data` | under `/var/ossec` and `/etc/filebeat` | `wazuh` | base |
| `wazuh-agent-etc` | `/var/ossec/etc` | `wazuh-agent` | VM override |
| `wazuh-journal-buffer` | `/buffer`, `/var/log/podman` | relay, agent | VM override |

`wazuh-journal-buffer` is a rolling copy of the journal and needs no backup.

Everything under `./seed`, plus `config/wazuh-agent.conf`, is a bind mount
from the checkout, read-only. Those files are in git, not in a volume.

### Rootless Podman

Everything belongs to the deploying user. There is no daemon and no root.

- Storage root: `~/.local/share/containers/storage`, so a volume lives at
  `~/.local/share/containers/storage/volumes/shopmock_<name>/_data`.
  `podman info --format '{{.Store.VolumePath}}'` prints the directory.
- UIDs are shifted through a user namespace. Container root maps to your own
  UID, and container UID *n* maps to the start of your `/etc/subuid` range plus
  *n* − 1. With the usual range starting at `100000`, Postgres UID 70 appears
  on the host as `100069`, which is what `ls -l` shows on the database volumes.
  `podman unshare cat /proc/self/uid_map` prints the mapping.
- Because of that shift your login user cannot read most of the volume
  contents. Plain `du` or `tar` on `_data` silently skips files: `du` reports
  `ipa-data` at about 77 MB while the real size is about 1.9 GB. Use
  `podman unshare` to enter the namespace, or use `podman volume export`, and
  never copy `_data` with a plain `cp -r` or `tar` from outside.
- An archive taken inside the namespace stores container UIDs (70, not 100069),
  so it restores correctly on a host with a different subuid range.
- SELinux: the `,z` suffix on the bind mounts relabels the seed files as
  `container_file_t`. Named volumes are labeled by Podman when created, so a
  volume restored with `podman volume import` needs no manual `chcon`.
- The volumes sit on the VM's own disk. Losing the VM loses them, so a backup
  that stays on the VM protects against a broken stack, not a lost host.

### Volumes that are not ShopMock's

`podman volume ls` also lists volumes named by a 64-character hash. Those are
anonymous volumes created for image-declared `VOLUME` paths, such as Vault's
`/vault/file` and `/vault/logs`; every recreation of such a container leaves the
previous pair behind. Vault runs in dev mode and writes nothing there, so they
hold no ShopMock state and can be skipped. `podman volume prune` removes the
ones no container uses.

## Taking a snapshot

Run the commands on the VM, from the checkout.

```bash
cd ~/ShopMock
SNAP="shopmock-$(date +%F)-$(git rev-parse --short HEAD)"
OUT="$HOME/shopmock-backups/$SNAP"
mkdir -p "$OUT" && chmod 700 "$OUT"
```

### 1. Credentials and commit

```bash
install -m 600 .env "$OUT/.env"
git rev-parse HEAD > "$OUT/COMMIT"
git status --porcelain > "$OUT/GIT_STATUS"     # should be empty; if not, commit or note it
```

This step alone is a usable snapshot. If it is all you need, skip to
[Move off the host](#4-move-off-the-host).

### 2. Databases, live

A logical dump is consistent while the database runs and restores across
Postgres patch versions, so prefer it to copying the raw data volumes.

```bash
for db in customer catalog orders finance hr; do
  podman compose exec -T "$db-db" pg_dumpall -U postgres > "$OUT/$db-db.sql"
done
```

`pg_dumpall` includes the roles. Their passwords are reset from `.env` by the
next deploy anyway.

Export Keycloak with users while it runs:

```bash
podman compose exec -T identity /opt/keycloak/bin/kc.sh export \
  --dir /tmp/kc-export --users realm_file
podman compose cp identity:/tmp/kc-export "$OUT/keycloak"
```

If the export fails on a locked H2 database, stop only `identity`, copy the
database files out, then start it again:

```bash
podman compose stop identity
podman cp shopmock-identity-1:/opt/keycloak/data/h2 "$OUT/keycloak-h2"
podman compose start identity
```

### 3. Stateful volumes, stopped

FreeIPA, Wazuh, and OpenSearch write continuously, and a copy taken while they
run can be internally inconsistent. Stop the stack, without `-v`, and archive
the volumes:

```bash
podman compose stop
for v in ipa-data search-data wazuh-api-configuration wazuh-etc wazuh-logs \
         wazuh-queue wazuh-var-multigroups wazuh-integrations \
         wazuh-active-response wazuh-agentless wazuh-wodles \
         wazuh-filebeat-etc wazuh-filebeat-data wazuh-agent-etc; do
  podman volume export "shopmock_$v" --output "$OUT/$v.tar"
done
podman compose start
```

`podman compose stop` keeps the containers, so Keycloak's container layer
survives it. `down` would not.

Add the five `*-data` volumes to the loop if you want raw copies in addition
to the SQL dumps. A raw copy restores only onto the same Postgres major
version, 16.

### 4. Move off the host

```bash
tar -C "$(dirname "$OUT")" -czf "$SNAP.tar.gz" "$SNAP"
scp "$SNAP.tar.gz" backup-host:shopmock/     # anywhere that is not this VM
```

An archive kept only on the VM disappears with the VM. The snapshot holds
`.env` and the FreeIPA CA and keys, so keep it out of git.

## Rolling back on the same host

If the volumes are still on the VM, there is nothing to restore: `podman
compose stop` or `podman compose down`, never `down -v`, leaves them in place,
and the next `bash scripts/deploy.sh` reattaches them with their data.
`deploy.sh` never removes volumes.

To go back to an earlier snapshot instead, replace the current volumes with its
archives. Podman cannot rename a volume, so take a fresh snapshot first if you
may want the current state back.

```bash
cd ~/ShopMock
SNAP_DIR=../shopmock-2026-11-01-abc1234          # unpacked snapshot
git checkout "$(cat "$SNAP_DIR/COMMIT")"
install -m 600 "$SNAP_DIR/.env" .env             # the .env must match the volumes
podman compose down                              # removes containers, keeps volumes
for f in "$SNAP_DIR"/*.tar; do
  v="shopmock_$(basename "$f" .tar)"
  podman volume rm -f "$v"
  podman volume create "$v"
  podman volume import "$v" "$f"
done
bash scripts/deploy.sh
```

`down` also discards Keycloak's container layer, so finish with step 6 of the
restore below. If the snapshot has SQL dumps instead of raw `*-data` archives,
load them with step 5.

## Restoring on a new host

On a new host:

1. Do the one-time host setup in [`DEPLOY.md`](DEPLOY.md): podman socket,
   linger, `sandboxnet`, and the compose provider.
2. Unpack and check out the recorded commit:

   ```bash
   tar -xzf "$SNAP.tar.gz"
   git clone git@github.com:I-Sheng/ShopMock.git && cd ShopMock
   git checkout "$(cat "../$SNAP/COMMIT")"
   install -m 600 "../$SNAP/.env" .env
   ```

   If the new host has a different user, UID, or address, update `DOCKER_SOCK`
   and `PUBLIC_ORIGIN` in `.env`. Leave every password and `PGRST_JWT_SECRET`
   unchanged.

3. For a credentials-only restore, stop here and run `bash scripts/deploy.sh`.
   The stack comes up seeded, with the original passwords.

4. For a full restore, create and fill the volumes **before** the first deploy,
   so the containers find existing data instead of initializing new data:

   ```bash
   for f in ../"$SNAP"/*.tar; do
     v="shopmock_$(basename "$f" .tar)"
     podman volume create "$v"
     podman volume import "$v" "$f"
   done
   bash scripts/deploy.sh
   ```

5. Load the SQL dumps if you did not restore raw `*-data` volumes. The fresh
   databases already contain the seed rows, so drop and reload the application
   database rather than loading over it:

   ```bash
   for db in customer catalog orders finance hr; do
     podman compose exec -T "$db-db" psql -U postgres -d postgres \
       -c "DROP DATABASE $db WITH (FORCE)"
     podman compose exec -T "$db-db" psql -U postgres -d postgres \
       < "../$SNAP/$db-db.sql"
   done
   bash scripts/deploy.sh      # reapplies RPCs, token hooks, roles, issuer
   ```

   `pg_dumpall` output recreates roles that already exist, and the
   `role already exists` errors it prints are harmless.

6. Restore Keycloak. With a realm export, copy the realm files over
   `seed/identity` only on the restore host, never in a commit, and recreate
   `identity` so `--import-realm` picks them up. With an H2 copy, stop
   `identity`, `podman cp` the directory back to `/opt/keycloak/data/h2`, and
   start it. Either way, run `bash scripts/deploy.sh` afterwards to reconverge
   the clients and federation.

7. Verify with the checks in [`DEPLOY.md`](DEPLOY.md#verification). Also check
   that a runtime-created customer can still sign in and that
   `ipa user-find` lists more than the bootstrap users.

## Limitations

- The SQL, Keycloak, and volume commands in this guide are not yet scripted
  or exercised by CI. Test a restore on a throwaway host before relying on it.
- Restored FreeIPA data keeps its hostname `ipa.shopmock.lab` and CA. Enrolled
  hosts like the PAW are recreated by deploy and re-enroll against it.
- Giving `identity` a named volume for `/opt/keycloak/data` would remove the
  separate Keycloak step, because its state would then be covered by
  [step 3](#3-stateful-volumes-stopped).
