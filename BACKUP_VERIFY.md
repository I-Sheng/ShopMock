# Verifying a ShopMock backup

How to prove a snapshot from [`BACKUP.md`](BACKUP.md) can actually be restored,
without putting production at risk.

## Ground rule: never test a restore on production

A second copy of the stack cannot run beside production on the lab VM. It
would compete for the published host ports (80, 8081, 8443, 8088, 5002), the
external `sandboxnet` network, and the `ipa.shopmock.lab` identity. Checks on
the production VM are therefore limited to reading the snapshot. A real
restore happens on a throwaway VM.

| Level | Where | Proves | When |
| --- | --- | --- | --- |
| 1. File integrity | Production VM | Archives and dumps are complete and readable | Every snapshot |
| 2. SQL dump load | Production VM, isolated container | The database dumps restore into Postgres 16 | Every snapshot |
| 3. Full restore | Throwaway VM | Volumes, `.env`, FreeIPA, Keycloak, and `deploy.sh` work together | After compose, volume, or `BACKUP.md` changes; otherwise monthly |

## Level 1: file integrity

```bash
SNAP_DIR=~/shopmock-backups/shopmock-2026-11-05-f42bed9
cd "$SNAP_DIR"

for f in *.tar; do tar -tf "$f" >/dev/null && echo "OK   $f" || echo "BAD  $f"; done
for f in *-db.sql; do
  tail -n 3 "$f" | grep -q 'dump complete' && echo "OK   $f" || echo "TRUNCATED $f"
done
test -s .env && grep -c PASSWORD .env       # credentials are present
ls keycloak/ 2>/dev/null || ls keycloak-h2/ # Keycloak export is present
```

If you also produced the compressed archive, check that it reads end to end
with `tar -tzf "$SNAP.tar.gz" >/dev/null`.

## Level 2: load the SQL dumps in isolation

A scratch Postgres with no network and no published ports cannot reach the
live databases:

```bash
podman run -d --rm --name pgverify --network none \
  -e POSTGRES_PASSWORD=x docker.io/library/postgres:16-alpine
sleep 5
for db in customer catalog orders finance hr; do
  podman exec -i pgverify psql -U postgres -q < "$SNAP_DIR/$db-db.sql" \
    && echo "OK   $db" || echo "FAIL $db"
done
podman exec pgverify psql -U postgres -l
podman stop pgverify
```

Errors about roles that already exist are harmless. Any other error means the
dump cannot be trusted.

## Level 3: full restore on a throwaway VM

### 1. Fingerprint production at snapshot time

Run this right after the dumps in step 2 of `BACKUP.md`, so that live writes do
not make the comparison drift:

```bash
count_rows() {  # exact row count for every table in one database
  podman compose exec -T "$1-db" psql -U postgres -d "$1" -tA -c "
    SELECT table_schema||'.'||table_name||' '||
      (xpath('/row/c/text()', query_to_xml(format('select count(*) c from %I.%I',
        table_schema, table_name), false, true, '')))[1]::text
    FROM information_schema.tables
    WHERE table_type='BASE TABLE' AND table_schema NOT IN ('pg_catalog','information_schema')
    ORDER BY 1"
}
{ for db in customer catalog orders finance hr; do echo "== $db"; count_rows "$db"; done
  echo "== ipa"
  podman compose exec -T ipa bash -c \
    'echo "$IPA_ADMIN_PASSWORD" | kinit admin >/dev/null && ipa user-find --sizelimit=0 | grep "Number of entries"'
} > "$SNAP_DIR/FINGERPRINT"
```

### 2. Restore

Follow "Restoring on a new host" in `BACKUP.md` exactly as written. A step that
fails when followed literally is a defect in the guide, and finding those is
part of the test.

### 3. Compare

Run the same fingerprint commands on the restored VM, saving to
`FINGERPRINT.restored`, then:

```bash
diff "$SNAP_DIR/FINGERPRINT" FINGERPRINT.restored && echo "data matches"
```

### 4. Functional checks

Run the checks in [`DEPLOY.md`](DEPLOY.md#verification): the three
`scripts/verify-*.sh` scripts, the storefront and catalog `curl` calls, and a
token login. Then add the checks that only a restore can prove:

- A customer who registered after the seed can still sign in. This proves the
  Keycloak restore, the one piece not held in a volume.
- An order placed before the snapshot appears in that customer's history.
- `ipa user-find` lists more than the bootstrap users, and an existing employee
  can sign in to a workforce portal.

### 5. Destroy the VM

Delete the VM as soon as the test ends. It holds the production credentials and
the FreeIPA CA.

## What a pass means

| Result | Meaning |
| --- | --- |
| Levels 1 and 2 pass | The snapshot files are intact and the databases can be rebuilt |
| Level 3 fingerprint matches | Every table row and FreeIPA user came back |
| Level 3 functional checks pass | The restored stack works, including sign-in with restored credentials |

Only a Level 3 pass shows the backup can rebuild production. Levels 1 and 2
catch broken snapshots early but cannot prove that `.env`, FreeIPA, and
Keycloak still agree with each other.
