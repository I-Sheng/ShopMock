FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends systemd \
    && rm -rf /var/lib/apt/lists/*

COPY relay.sh /usr/local/bin/shopmock-journal-relay
RUN chmod 0755 /usr/local/bin/shopmock-journal-relay

ENTRYPOINT ["/usr/local/bin/shopmock-journal-relay"]