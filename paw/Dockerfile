# PAW / jump host — access plane, NOT Tier 0 (see PLAN_TIER0_FREEIPA.md).
# AlmaLinux 9 + FreeIPA client + OpenSSH: at boot it best-effort enrolls into the
# FreeIPA realm (SSSD + PAM), so admin SSH auth becomes Kerberos governed by HBAC.
# A local break-glass account keeps the door usable if the DC is unreachable.
FROM almalinux:9

RUN dnf -y install ipa-client openssh-server sudo iputils bind-utils \
    && dnf clean all \
    && ssh-keygen -A

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Match the compose port mapping (22:2222) — sshd listens on 2222 in-container.
RUN sed -i 's/^#\?Port .*/Port 2222/' /etc/ssh/sshd_config

EXPOSE 2222
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
