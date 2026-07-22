#!/usr/bin/env bash
# PAW entrypoint: (1) provision a local break-glass account so the SSH door always
# opens; (2) best-effort enroll into FreeIPA so domain admins authenticate via
# Kerberos and HBAC decides who may actually log in; (3) run sshd in the foreground.
# Enrollment failure is non-fatal by design — the PAW must not hard-depend on the DC.
set -u

BASTION_USER="${BASTION_USER:-jump}"
BASTION_PASSWORD="${BASTION_PASSWORD:-changeme}"
IPA_DOMAIN="${IPA_DOMAIN:-shopmock.lab}"
IPA_REALM="${IPA_REALM:-SHOPMOCK.LAB}"
IPA_SERVER="${IPA_SERVER:-ipa.shopmock.lab}"
IPA_ENROLL_PASSWORD="${IPA_ENROLL_PASSWORD:-}"

# 1. Break-glass local account (documented, lab-only fallback).
if ! id "$BASTION_USER" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$BASTION_USER"
  echo "${BASTION_USER}:${BASTION_PASSWORD}" | chpasswd
fi

# 2. Best-effort FreeIPA enrollment. Wait for the KDC/HTTP to answer, then join.
#    On the flat sandboxnet there is no DNS discovery, so we pin --server + --domain
#    and rely on the compose network alias to resolve ipa.shopmock.lab.
if [ -n "$IPA_ENROLL_PASSWORD" ] && ! id admin >/dev/null 2>&1; then
  echo "paw: waiting for FreeIPA at ${IPA_SERVER} ..."
  for _ in $(seq 1 60); do
    curl -sk "https://${IPA_SERVER}/ipa/config/ca.crt" -o /dev/null && break
    sleep 5
  done
  echo "paw: attempting ipa-client enrollment (non-fatal on failure) ..."
  ipa-client-install -U \
    --hostname="${PAW_HOSTNAME:-paw.shopmock.lab}" \
    --domain="$IPA_DOMAIN" \
    --realm="$IPA_REALM" \
    --server="$IPA_SERVER" \
    --principal=admin \
    --password="$IPA_ENROLL_PASSWORD" \
    --no-ntp \
    --force-join \
    --mkhomedir \
    && echo "paw: enrolled into ${IPA_REALM} — admin logins now governed by HBAC" \
    || echo "paw: WARN enrollment failed; serving break-glass local account only"
fi

# 3. Foreground sshd.
exec /usr/sbin/sshd -D -e
