#!/bin/sh
# Install the supplied reconcile units on the standard Attested Memory Compose host.
set -eu

units=$(CDPATH= cd -- "$(dirname -- "$0")/systemd" && pwd)
hub=/opt/aicom/attested/attested-memory-hub
if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root on the Attested Memory host." >&2
    exit 1
fi
if [ ! -f "$hub/docker-compose.yml" ]; then
    echo "The supplied service expects the Compose project at $hub." >&2
    exit 1
fi
command -v systemctl >/dev/null
install -m 0644 "$units/attested-provenance-reconcile.service" /etc/systemd/system/
install -m 0644 "$units/attested-provenance-reconcile.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now attested-provenance-reconcile.timer
