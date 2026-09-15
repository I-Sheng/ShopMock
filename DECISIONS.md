# Design decisions

Durable decisions behind the current build, with the reasoning that is not
recoverable from the compose files or the code. Implementation detail lives in
`INFRA_BUILD_SPEC.md`; operating procedure lives in `DEPLOY.md`.

## FreeIPA is Tier 0; Keycloak is a Tier 1 workload

- Decision: FreeIPA (389DS LDAP, Kerberos KDC, Dogtag PKI/CA, HBAC) is the control plane; Keycloak holds customer and seller identity and federates workforce users from FreeIPA over LDAP.
- Why: an earlier build filed Keycloak as both Tier 0 and the customer login service, which made Tier 0 a label rather than an asset.
- Boundary and residual risk: a Keycloak compromise reaches tokens but not Kerberos keys, the CA, or host policy; federation binds with a dedicated read-only system account, not the directory admin. FreeIPA approximates Microsoft Tier 0 but is not AD DS — NTLM, Group Policy, and AD CS techniques do not transfer.
- Revisit if: the capstone needs Windows-specific privileged-identity attacks, which would require a separate lab rather than a change here.

## The PAW is the access plane, not Tier 0

- Decision: the jump host is an IPA-enrolled privileged access workstation between administrators and the control plane, not part of it.
- Why: the Microsoft Enterprise Access Model separates the path to the control plane from the control plane; calling the bastion Tier 0 hid the fact that nothing of value lived there.
- Boundary and residual risk: admin SSH authenticates against Kerberos with HBAC deciding access, and `tier0-admins` also hold sudo on the PAW through the `tier0-sudo` rule, so a compromised admin session is a credible path upward. `BASTION_USER` is a local break-glass account that bypasses HBAC entirely and is the weakest link by design.
- Revisit if: break-glass access needs to be auditable, which means a checked-out credential rather than a static one in `.env`.

## HBAC replaces network segmentation on the lab VM

- Decision: on the UWB VM every service joins one flat `sandboxnet`, and Tier 0 is enforced by identity — only `tier0-admins` may SSH to the DC and the PAW.
- Why: campus policy mandates the single network, so the tiered networks the dev box uses collapse there; without HBAC the bastion would gate nothing on the host where the lab actually runs.
- Boundary and residual risk: on the VM reachability is not a control, so only directory policy and each service's own token checks stand in the way. Two exceptions are preserved deliberately — `hr_net` and `ops_net` stay private even there.
- Revisit if: the network policy changes; the base compose file already expresses the intended segmentation.

## Two realms, not one mixed realm

- Decision: `shopmock-ciam` holds customers and sellers, `shopmock-workforce` holds federated employees, and the original mixed `shopmock` realm is disabled on deploy with its data retained.
- Why: a single realm meant a realm default group handed the customer role to every federated employee; separate realms make the boundary a property of the issuer rather than of role hygiene.
- Boundary and residual risk: services check the exact issuer, `azp`, and required role, so a token from one realm cannot be replayed against the other. Sessions against the retired issuer stopped working when it was disabled, and profile changes made only there were not migrated.
- Revisit if: the retired realm is confirmed unneeded and can be deleted.

## Customer writes go through database RPCs

- Decision: checkout writes are `SECURITY DEFINER` functions (`ensure_customer`, `place_order`, `record_payment`) rather than PostgREST table inserts, and each protected service runs a `security.check_ciam_customer` pre-request hook.
- Why: PostgREST cannot span several inserts in one transaction, and exposing the tables would make customer PII browsable; the hook re-checks issuer, `typ`, `azp`, and client-role membership inside the database so authorization does not depend on the proxy alone.
- Boundary and residual risk: anonymous callers run read-only as `web_anon`, and `customer-svc` exposes only `ensure_customer`, which returns the caller's own id. The trusted issuer is a database setting, so pooled sessions must be recycled when it changes.
- Revisit if: business logic outgrows SQL; the Django backends already own the cross-database paths.

## The RS256 verification key is pinned, not fetched

- Decision: `PGRST_JWT_SECRET` carries the realm's public JWK literally, shared by every PostgREST service and every Django service.
- Why: PostgREST v12 cannot fetch a JWKS URL, and a pinned key also removes a startup dependency on Keycloak.
- Boundary and residual risk: the value must stay byte-identical to the CIAM signing key in `seed/identity/realm-shopmock-ciam.json`; a mismatch silently fails every token check, and rotating the realm key is a manual coordinated change.
- Revisit if: PostgREST gains JWKS support, or key rotation becomes routine.

## Checkout is a best-effort cross-database saga

- Decision: `place_order` and `record_payment` run against separate databases with no distributed transaction, and the browser supplies `customer_ref` and per-line unit prices.
- Why: database-per-service makes a true cross-database transaction impossible, and the resulting partial-failure window is realistic; the client-trusted fields are kept as capstone targets rather than fixed.
- Boundary and residual risk: IDOR (ordering as another customer) and price tampering are possible by design, and a partial failure can leave an order with no payment row. These are documented attack surface, not defects.
- Revisit if: the lab needs to demonstrate the hardened form, which means deriving `customer_ref` from the verified `sub` and pricing server-side.

## Each workforce application gets its own PKCE client and realm role

- Decision: `/oe`, `/finance`, and `/hr` each have a dedicated public Keycloak client using authorization code with PKCE S256, direct grants and service accounts disabled, and a distinct realm role mapped from a FreeIPA group.
- Why: job functions are peers, not a hierarchy — a global administrator has no implicit reason to read infrastructure status, and finance none to read staff records. Deploy re-asserts these client settings every run, because silently downgrading PKCE would weaken applications whose only gate is the token.
- Boundary and residual risk: each service verifies issuer, `azp`, token type, and the realm role server-side, so a token minted for one portal is rejected by the others and a like-named client role is not accepted. HR is additionally isolated at the network and credential layer, with its own database and private network.
- Revisit if: a workforce user legitimately needs two applications, which is a group membership change rather than a client change.

## The container console reads through a GET-only socket proxy

- Decision: `oe-dashboard` never mounts the runtime socket; it issues one hard-coded list request to `oe-socket-proxy`, which allows read-only `/containers` endpoints and rejects every mutating verb.
- Why: the browser-facing service is the one most likely to be compromised, so it should hold nothing an attacker could turn into runtime control.
- Boundary and residual risk: `CONTAINERS=1` also permits `GET /containers/{id}/json`, which returns `Config.Env`. Nothing in the stack calls it, but the proxy cannot express a single-endpoint policy, so reaching it needs code execution inside `oe-dashboard` plus `ops_net` access. The response projection is an allowlist; environment, commands, mounts, ports, and networks are never returned.
- Revisit if: that residual inspect surface matters, which needs a purpose-built one-endpoint reader rather than the generic proxy.
