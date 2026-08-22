#!/usr/bin/env bash
# PAW setup: (1) provision a local break-glass account so the SSH door always
# opens; (2) best-effort enroll into FreeIPA so domain admins authenticate via
# Kerberos and HBAC decides who may actually log in. systemd manages SSSD, oddjobd,
# and SSHD. Enrollment failure is non-fatal so break-glass SSH remains available.
set -u

# The system systemd manager intentionally does not pass the container's
# environment to system services. Import only the variables required by this
# one-shot PAW setup service directly from PID 1's environment. This avoids
# copying the enrollment and break-glass credentials into systemd's global
# manager environment.
while IFS= read -r -d '' entry; do
  key="${entry%%=*}"
  case "$key" in
    BASTION_USER|BASTION_PASSWORD|PAW_HOSTNAME|IPA_DOMAIN|IPA_REALM|IPA_SERVER|IPA_ENROLL_PASSWORD)
      export "$entry"
      ;;
  esac
done < /proc/1/environ

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

# A complete enrollment must contain all three artifacts. Checking `id admin`
# before SSSD starts gives a false negative on every system boot.
ipa_client_configured() {
  [ -s /etc/ipa/default.conf ] &&
    [ -s /etc/sssd/sssd.conf ] &&
    [ -s /etc/krb5.keytab ]
}

# 2. Best-effort FreeIPA enrollment. Wait for the KDC/HTTP to answer, then join.
#    On the flat sandboxnet there is no DNS discovery, so we pin --server + --domain
#    and rely on the compose network alias to resolve ipa.shopmock.lab.
if [ -n "$IPA_ENROLL_PASSWORD" ] && ! ipa_client_configured; then
  echo "paw: waiting for FreeIPA at ${IPA_SERVER} ..."

  ipa_ready=
  for _ in $(seq 1 60); do
    if curl -fsk \
      --connect-timeout 2 \
      --max-time 5 \
      "https://${IPA_SERVER}/ipa/config/ca.crt" \
      -o /dev/null; then
      ipa_ready=1
      break
    fi
    sleep 5
  done

  if [ -z "$ipa_ready" ]; then
    echo "paw: WARN FreeIPA never became ready — skipping enrollment; serving break-glass local account only"
  else
    echo "paw: attempting ipa-client enrollment (non-fatal on failure) ..."
    # Compose already sets the container FQDN to paw.shopmock.lab. Do not pass
    # --hostname: ipa-client-install would call hostnamectl, which is unavailable
    # because this PAW container does not run systemd as PID 1.
    ipa-client-install -U \
      --domain="$IPA_DOMAIN" \
      --realm="$IPA_REALM" \
      --server="$IPA_SERVER" \
      --principal=admin \
      --password="$IPA_ENROLL_PASSWORD" \
      --no-ntp \
      --no-nisdomain \
      --force-join \
      --mkhomedir \
      && echo "paw: enrolled into ${IPA_REALM} — admin logins now governed by HBAC" \
      || echo "paw: WARN enrollment failed; serving break-glass local account only"
  fi
elif ipa_client_configured; then
  echo "paw: existing FreeIPA client configuration found"
fi

# SSSD, oddjobd, and SSHD are started and supervised by systemd.
