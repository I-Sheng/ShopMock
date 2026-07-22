#!/bin/bash
# FreeIPA Tier-0 bootstrap — encodes the Microsoft AD tier model *inside* the directory as
# groups + HBAC rules. Runs INSIDE the `ipa` container (it has the ipa CLI + KDC); invoked by
# scripts/deploy.sh after the DC is up. Idempotent: safe to re-run on every deploy.
#
# Why HBAC is the point: on the VM every container shares one flat sandboxnet, so Tier 0 cannot
# be a network boundary. HBAC makes it an IDENTITY boundary — only tier0-admins may SSH to the
# control-plane hosts (the DC and the PAW), regardless of network reachability.
set -eu

: "${IPA_ADMIN_PASSWORD:?set IPA_ADMIN_PASSWORD}"
echo "$IPA_ADMIN_PASSWORD" | kinit admin >/dev/null

have() { "$@" >/dev/null 2>&1; }

ensure_group() { have ipa group-show "$1" || ipa group-add "$1" --desc="$2"; }
ensure_user() { # login first last password
  have ipa user-show "$1" || ipa user-add "$1" --first="$2" --last="$3" --password <<EOF
$4
$4
EOF
}
ensure_host() { have ipa host-show "$1" || ipa host-add "$1" --force; }  # --force: skip DNS
ensure_hbac() { have ipa hbacrule-show "$1" || ipa hbacrule-add "$1" --desc="$2"; }

# ---- Admin tiers (≈ MS Enterprise Access Model) ---------------------------------------------
ensure_group tier0-admins  "Tier 0 — control-plane / global admins (the key of the kingdom)"
ensure_group server-admins "Tier 1 — server / application admins"
ensure_group helpdesk      "Tier 2 — workstation / support admins"
# 'employees' is the set federated into Keycloak for workforce SSO (customers stay native there)
ensure_group employees     "ShopMock staff — federated into Keycloak"

# ---- Workforce identities (moved out of the Keycloak realm JSON; they live here now) --------
ensure_user gadmin        "Global"  "Admin" "$IPA_ADMIN_PASSWORD"
ensure_user finance.clerk "Finance" "Clerk" "Staff123!"

ipa group-add-member tier0-admins --users=gadmin        >/dev/null 2>&1 || true
ipa group-add-member helpdesk     --users=finance.clerk >/dev/null 2>&1 || true
ipa group-add-member employees    --users=gadmin,finance.clerk >/dev/null 2>&1 || true

# ---- Control-plane hosts ---------------------------------------------------------------------
ensure_host ipa.shopmock.lab
ensure_host paw.shopmock.lab   # pre-created so the HBAC rule can target it before PAW enrolls

# ---- HBAC: deny-by-default, then explicit Tier-0 access -------------------------------------
# FreeIPA ships an 'allow_all' rule; disabling it flips the realm to deny-by-default.
ipa hbacrule-disable allow_all >/dev/null 2>&1 || true

ensure_hbac tier0-access "Tier-0 admins may SSH to the control-plane hosts (DC + PAW)"
ipa hbacrule-add-user    tier0-access --groups=tier0-admins            >/dev/null 2>&1 || true
ipa hbacrule-add-host    tier0-access --hosts=ipa.shopmock.lab,paw.shopmock.lab >/dev/null 2>&1 || true
ipa hbacrule-add-service tier0-access --hbacsvcs=sshd                  >/dev/null 2>&1 || true

# Sudo on the PAW is a Tier-0-admins-only privilege.
have ipa sudorule-show tier0-sudo || ipa sudorule-add tier0-sudo --desc="Tier-0 admins sudo on the PAW"
ipa sudorule-add-user tier0-sudo --groups=tier0-admins >/dev/null 2>&1 || true
ipa sudorule-add-host tier0-sudo --hosts=paw.shopmock.lab >/dev/null 2>&1 || true
ipa sudorule-mod tier0-sudo --cmdcat=all >/dev/null 2>&1 || true

echo "ipa-bootstrap: tier groups, employees, and HBAC (deny-by-default + tier0-access) applied"
