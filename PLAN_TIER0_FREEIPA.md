# Redesign Tier 0 as a real FreeIPA control plane

## Context

**Problem.** ShopMock's Tier 0 is currently *a door with no room behind it*. The `bastion`
service is an SSH jump box whose only backing (`mgmt_net`) is the admin surfaces of
*other* tiers (Keycloak admin + Vault). Three defects follow:

1. A bastion is the **access path** to Tier 0, not Tier 0 itself (Microsoft's Enterprise
   Access Model puts the jump host / PAW on the access plane, distinct from the control plane).
2. Nothing genuinely *lives* in Tier 0. The real identity system (Keycloak) is filed under
   Tier 1 in `INFRA_BUILD_SPEC.md` §1 ("Tier 1 (Tier-0 control plane)" — the parenthetical
   admits the muddle). Keycloak is doing two jobs: customer CIAM **and** pretending to be the
   admin control plane.
3. On the VM, `docker-compose.vm.yml` collapses every service onto one flat `--internal
   sandboxnet`, so `mgmt_net` disappears and the bastion gates nothing. The isolation is
   paper-only where the lab actually runs.

**Outcome.** Introduce **FreeIPA** (389DS LDAP + Kerberos KDC + Dogtag PKI/CA + HBAC) as the
sole, genuine **Tier-0 control plane** — the open-source Active-Directory equivalent, which
maps 1:1 onto Microsoft Tier 0 = "the directory / domain controllers / PKI." Recast the
bastion as an IPA-enrolled **PAW** (access plane). Keep Keycloak as a Tier-1 **CIAM workload**
that *federates employees from FreeIPA* (customer identity stays native). Because the VM forbids
network segmentation, **Tier 0 is enforced by identity** (Kerberos realm + HBAC rules), which is
the only isolation lever left on a flat network — the constraint and the design agree.

This also hands the capstone ("Autonomous AI-Driven Cyber Attacks") the canonical Tier-0 attack
surface Keycloak lacks: Kerberos TGTs (Golden Ticket), Kerberoasting, a DCSync-analog against
389DS, Dogtag ESC-style cert abuse, and HBAC/sudo-rule bypass.

**Runs on both targets:** tiered dev box (real `tier0_net`) and the flat-`sandboxnet` VM
(identity-enforced). VM is the real target.

## Honest risks (stated up front)

- **FreeIPA is heavy & monolithic**: one systemd container running LDAP+KDC+CA, ~2 GB RAM,
  slow first boot. `--privileged` is *unsupported*; rootless podman needs cgroups v2. The exact
  run flags (`seccomp`, tmpfs `/run` `/tmp`, `SYS_TIME`/`no-new-privileges` handling) need **one
  bring-up iteration on the VM** — I cannot fully validate FreeIPA container startup locally.
  The plan isolates this risk in Phase 1 and gives exact bring-up + rollback commands.
- **Chicken-and-egg**: Keycloak's realm import (`--import-realm`) runs at startup; LDAP
  federation must point at a *live* IPA. Handled by ordering (Phase 3) — federation degrades
  gracefully (customers unaffected) if IPA is down.
- **DNS**: install FreeIPA with `--no-dns`; clients resolve `ipa.shopmock.lab` via compose
  network aliases / `extra_hosts`, avoiding a clash with campus + podman DNS on sandboxnet.

## Approach — phased so the risky part is isolated

### Phase 1 — Stand up the Tier-0 control plane (`ipa`)
- **`docker-compose.yml`**: add service `ipa` (`quay.io/freeipa/freeipa-server:almalinux-9`),
  `hostname: ipa.shopmock.lab`, realm `SHOPMOCK.LAB` / domain `shopmock.lab`, install opts
  `-U --no-dns --no-ntp` via `IPA_SERVER_INSTALL_OPTS`, admin pw from `${IPA_ADMIN_PASSWORD}`,
  Directory Manager pw `${IPA_DM_PASSWORD}`. Persistent named volume `ipa-data:/data`. tmpfs
  `/run`,`/tmp`; `security_opt: [seccomp=unconfined]`; **no** `privileged`. Networks:
  `[tier0_net, mgmt_net]`. Add `tier0_net: { internal: true }` and `volumes: ipa-data: {}`.
- **`.env.example`**: add `IPA_ADMIN_PASSWORD`, `IPA_DM_PASSWORD`, `IPA_LDAP_BIND_PASSWORD`
  (fake lab values), plus `IPA_REALM=SHOPMOCK.LAB` / `IPA_DOMAIN=shopmock.lab`.
- **`docker-compose.vm.yml`**: `ipa` joins `!override [sandboxnet]`; add
  `network aliases`/`extra_hosts` so `ipa.shopmock.lab` resolves on the flat net; keep any
  published admin port bound to `127.0.0.1` only (web UI `:8443`→loopback, mgmt-only).
- **Deliverable**: a real DC running; `kinit admin` + `ipa user-find` work from the mgmt path;
  web UI reachable only via loopback/PAW.

### Phase 2 — Recast the bastion as an IPA-enrolled PAW (access plane)
- Rename service `bastion` → `paw` in both compose files (comment: "access-plane jump host /
  PAW — the path *up* to Tier 0, governed by the directory it fronts"). Keep `bastion_net`
  (public SSH door) + add `tier0_net`; on the VM it stays on `sandboxnet`.
- Enroll `paw` as a **FreeIPA client** (SSSD) so admin SSH logins authenticate against Kerberos,
  not a local password. Keep `${BASTION_USER}` as a documented **break-glass local account** only.
- **`seed/ipa/bootstrap.sh`** (new; modeled on the `vault-seed` one-shot pattern) defines the AD
  tier model *inside* IPA: groups `tier0-admins` / `server-admins` / `helpdesk` (≈ MS Tier 0/1/2
  admins), the employee + `gadmin` users, and **HBAC rules** so only `tier0-admins` may log into
  `paw` and `ipa`. This is what enforces Tier 0 on the flat VM network.

### Phase 3 — Federate employees into Keycloak (Keycloak = Tier-1 CIAM workload)
- **`seed/identity/realm-shopmock.json`**: add an LDAP **user-federation component** →
  `ldap://ipa.shopmock.lab:389`, users DN `cn=users,cn=accounts,dc=shopmock,dc=lab`, bind via a
  dedicated account (`${IPA_LDAP_BIND_PASSWORD}`). **Remove the inline `finance.clerk` and
  `gadmin` users** (they now live in IPA and federate in); customers/sellers stay native.
- Keep the `employee` / `global-admin` realm roles; map federated group membership → those roles.
- Order Keycloak after `ipa` is healthy (`depends_on`), so federation binds to a live directory.

### Phase 4 — Correct the tier model in the docs
- `INFRA_BUILD_SPEC.md` §1: Keycloak → **Tier 1 (customer CIAM)**; add **FreeIPA = Tier 0
  control plane**; bastion row → PAW (access plane). §2: add `tier0_net`; describe the three
  planes (control / management / access).
- `ShopMock_Company_Infra.md` §3–5: rewrite the tier table so Tier 0 = FreeIPA identity/PKI,
  add plane separation, update the network-distribution narrative.
- `README.md`: ports table (`bastion`→`paw`; add IPA web UI mgmt-only), new admin login flow
  (kinit via PAW), crown-jewels note. `DEPLOY.md`: IPA one-time bring-up + client enrollment +
  troubleshooting rows. Correct the `mgmt_net`/tier list to include `tier0_net`.

### Phase 5 — Wire deploy + verify
- **`scripts/deploy.sh`**: after DBs are ready, add an idempotent step to run
  `seed/ipa/bootstrap.sh` (wait-for-IPA then apply groups/users/HBAC), mirroring the existing
  role/RPC re-apply blocks (initdb-style seed only runs once, so deploys re-assert it). Add IPA
  to the readiness wait loop with a longer timeout (first install is slow).

## Critical files
- `docker-compose.yml` (add `ipa`, `tier0_net`, `ipa-data`; rename `bastion`→`paw`)
- `docker-compose.vm.yml` (sandboxnet + host-alias overrides for `ipa`/`paw`; Keycloak ordering)
- `.env.example` (IPA_* vars)
- `seed/ipa/bootstrap.sh` (new — groups/users/HBAC; pattern from `seed/vault/seed-secrets.sh`)
- `seed/identity/realm-shopmock.json` (LDAP federation component; drop inline employee users)
- `scripts/deploy.sh` (IPA readiness wait + bootstrap re-apply, like the role blocks at L124–146)
- Docs: `INFRA_BUILD_SPEC.md`, `ShopMock_Company_Infra.md`, `README.md`, `DEPLOY.md`

## Verification (end-to-end)
1. **Control plane up**: `docker compose up -d ipa` → wait; `docker compose exec ipa bash -c
   'echo $IPA_ADMIN_PASSWORD | kinit admin && ipa user-find'` lists seeded employees.
2. **Tier-0 enforced by identity**: from `paw`, admin `kinit` + `ssh` succeeds; a non-admin
   (seller) is denied by HBAC. On the VM (flat sandboxnet) confirm the *same* denial holds even
   though every container shares one network — proving identity-enforced Tier 0.
3. **Federation**: employee `gadmin` logs into Keycloak with the **IPA** password (proving the
   employee identity chains up to Tier 0); a customer (`ada`) still logs in natively; the
   seller token round-trip in `DEPLOY.md` still returns listings (no regression).
4. **VM deploy**: `bash scripts/deploy.sh` reaches `deploy: complete` with `ipa` Up and
   `bootstrap` applied; storefront + `/api/catalog/products` still serve.
5. **Rollback**: `docker compose rm -sf ipa paw && docker volume rm shopmock_ipa-data` returns
   to the pre-FreeIPA stack; Keycloak federation is additive (customers unaffected if IPA absent).
