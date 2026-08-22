# Tier 0 FreeIPA and PAW implementation record

Status: **control plane and PAW implemented and verified** · Updated 2026-08-22

Open item: **Keycloak workforce LDAP attribute/group mapping** (separate from the
working FreeIPA-to-PAW Kerberos/HBAC path).

## Context

**Original problem.** ShopMock's Tier 0 was *a door with no room behind it*: the
old bastion was an SSH jump box whose only backing was the admin surfaces of other
tiers. The implementation below corrected three defects:

1. A bastion is the **access path** to Tier 0, not Tier 0 itself (Microsoft's Enterprise
   Access Model puts the jump host / PAW on the access plane, distinct from the control plane).
2. Nothing genuinely *lives* in Tier 0. The real identity system (Keycloak) is filed under
   Tier 1 in `INFRA_BUILD_SPEC.md` §1 ("Tier 1 (Tier-0 control plane)" — the parenthetical
   admits the muddle). Keycloak is doing two jobs: customer CIAM **and** pretending to be the
   admin control plane.
3. On the VM, `docker-compose.vm.yml` collapses every service onto one flat `--internal
   sandboxnet`, so `mgmt_net` disappears and the bastion gates nothing. The isolation is
   paper-only where the lab actually runs.

**Outcome.** ShopMock now uses **FreeIPA** (389DS LDAP + Kerberos KDC + Dogtag PKI/CA + HBAC) as the
Tier 0 control plane. It approximates the directory, Kerberos, and PKI responsibilities
of Microsoft Tier 0 without claiming Windows AD DS feature parity. The old bastion is now
an IPA-enrolled **PAW** (access plane). Keycloak remains a Tier-1 **CIAM workload**;
workforce federation from FreeIPA is configured but awaits mapper repair, while customer
and seller identity stays native. Because the VM forbids network segmentation, Tier 0 is
enforced by Kerberos and HBAC on the flat network.

This gives the capstone a Linux-native Tier 0 attack surface: Kerberos tickets and
service principals, LDAP replication/privilege risks, Dogtag certificate services,
and HBAC/sudo-policy abuse. Windows-only AD DS/NTLM/GPO/AD CS techniques require a
separate Windows lab and must not be claimed as identical FreeIPA behavior.

**Runs on both targets:** tiered dev box (real `tier0_net`) and the flat-`sandboxnet` VM
(identity-enforced). VM is the real target.

## Honest risks (stated up front)

- **FreeIPA is heavy and monolithic**: one systemd container runs LDAP, KDC, and
  CA services. The rootless-Podman runtime is now validated with cgroups v2,
  `cgroup: host`, relaxed seccomp, and tmpfs mounts for `/run` and `/tmp`.
- **Chicken-and-egg**: Keycloak's realm import (`--import-realm`) runs at startup; LDAP
  federation must point at a *live* IPA. Handled by ordering (Phase 3) — federation degrades
  gracefully (customers unaffected) if IPA is down.
- **DNS**: the lab does not configure FreeIPA DNS; Compose/Podman network aliases
  resolve `ipa.shopmock.lab`, avoiding a clash with campus DNS.

## Implemented approach

### Phase 1 — Tier-0 control plane (`ipa`) — complete
- **`docker-compose.yml`**: add service `ipa` (`quay.io/freeipa/freeipa-server:almalinux-9`),
  `hostname: ipa.shopmock.lab`, realm `SHOPMOCK.LAB` / domain `shopmock.lab`, install opts
  unattended installation with NTP setup disabled, admin password from `${IPA_ADMIN_PASSWORD}`,
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

### Phase 2 — IPA-enrolled PAW (access plane) — complete and verified
- Rename service `bastion` → `paw` in both compose files (comment: "access-plane jump host /
  PAW — the path *up* to Tier 0, governed by the directory it fronts"). Keep `bastion_net`
  (public SSH door) + add `tier0_net`; on the VM it stays on `sandboxnet`.
- Enroll `paw` as a **FreeIPA client** (SSSD) so admin SSH logins authenticate against Kerberos,
  not a local password. Keep `${BASTION_USER}` as a documented **break-glass local account** only.
- Run systemd as PAW PID 1 so `ipa-client-install` can configure and supervise
  SSSD, oddjobd, PAM, sudo integration, and SSHD. FreeIPA health gates first PAW startup.
- **`seed/ipa/bootstrap.sh`** (new; modeled on the `vault-seed` one-shot pattern) defines the AD
  tier model *inside* IPA: groups `tier0-admins` / `server-admins` / `helpdesk` (≈ MS Tier 0/1/2
  admins), the employee + `gadmin` users, and **HBAC rules** so only `tier0-admins` may log into
  `paw` and `ipa`. This is what enforces Tier 0 on the flat VM network.

### Phase 3 — Federate employees into Keycloak — configured, mapper repair pending
- **`seed/identity/realm-shopmock.json`**: add an LDAP user-federation component for
  `ldap://ipa.shopmock.lab:389` and remove inline workforce users. The current lab
  configuration binds with the IPA admin credential and lacks complete attribute/group
  mappers; replacing it with a dedicated least-privilege bind identity is required in
  Phase 3 remediation. Customers and sellers remain native Keycloak users.
- Keep the `employee` / `global-admin` realm roles and explicitly map FreeIPA groups
  after the base username/name/email mappers are validated.
- Treat FreeIPA health as a prerequisite for workforce federation tests; native CIAM
  must continue working when FreeIPA is unavailable.

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

## Verification record (2026-08-22)

1. FreeIPA reports healthy and all IPA services run.
2. PAW PID 1 is systemd; setup, SSSD, oddjobd, and SSHD services are active.
3. `id gadmin` resolves through SSSD; the host keytab contains
   `host/paw.shopmock.lab@SHOPMOCK.LAB`.
4. `tier0-access` targets both IPA and PAW hosts and the `sshd` service.
5. FreeIPA `hbactest` and PAW PAM checks allow `gadmin` and deny `finance.clerk`.
6. PAW restart preserves enrollment and identity resolution.
7. Storefront, catalog, Keycloak discovery, and seller health routes return HTTP 200.
8. Keycloak employee login is not yet accepted as verified; its LDAP mapper failure
   is the next identity task.
