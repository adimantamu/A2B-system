#!/usr/bin/env bash
# One-time server setup. Idempotent — safe to re-run.
#
#   ssh a2b 'bash -s' < deploy/bootstrap.sh
#
# Installs system packages, creates the deploy tree and the data directory, and
# registers the systemd unit and the nginx site. It does NOT ship code; that is
# deploy.sh, which can then run as often as you like.
set -euo pipefail

APP_DIR=/opt/a2b
DATA_DIR=/var/lib/a2b
ENV_DIR=/etc/a2b

echo "==> system packages"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3-venv python3-dev build-essential \
    nginx rsync curl ca-certificates

echo "==> directories"
sudo mkdir -p "$APP_DIR" "$DATA_DIR" "$ENV_DIR"
sudo chown -R ubuntu:ubuntu "$APP_DIR" "$DATA_DIR"

# Written once, then left alone — this is where the instance's own settings go.
if [ ! -f "$ENV_DIR/a2b.env" ]; then
    echo "==> $ENV_DIR/a2b.env (first run)"
    # IMDSv2: the metadata service refuses an unauthenticated GET.
    TOKEN="$(curl -fsS --max-time 3 -X PUT http://169.254.169.254/latest/api/token \
        -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)"
    PUBLIC_HOST="$(curl -fsS --max-time 3 \
        -H "X-aws-ec2-metadata-token: $TOKEN" \
        http://169.254.169.254/latest/meta-data/public-hostname || echo localhost)"
    sudo tee "$ENV_DIR/a2b.env" >/dev/null <<EOF
# Entry prompts carry this host into the customer's AI, so it has to be the
# address the outside world can actually reach, scheme included: an agent
# handed a bare hostname cannot fetch it.
A2B_MCP_BASE=http://${PUBLIC_HOST}/mcp

# Who may open /internal — the portal that reads across every tenant. Empty
# means nobody, which is the right default: an instance that forgets to set
# this has no portal rather than one open to every account on it.
#
# Comma-separated. Deliberately not a rule about the email domain: signup does
# not verify an address, so anything read off the email itself could be granted
# by a stranger typing it. Editing this file is the grant.
A2B_STAFF_EMAILS=
EOF
    sudo chmod 640 "$ENV_DIR/a2b.env"
    sudo chown root:ubuntu "$ENV_DIR/a2b.env"
    echo "    A2B_MCP_BASE=http://${PUBLIC_HOST}/mcp"
fi

echo "==> cloudflared"
# From GitHub rather than Cloudflare's apt repo, which does not publish a suite
# for every Ubuntu release. The .deb is release-independent.
if ! command -v cloudflared >/dev/null; then
    TMP=$(mktemp -d)
    curl -fsSL -o "$TMP/cloudflared.deb" \
        https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
    sudo dpkg -i "$TMP/cloudflared.deb" >/dev/null
    rm -rf "$TMP"
fi
cloudflared --version

echo "==> python virtualenv"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade --quiet pip wheel

# The unit file and the nginx site live in the deploy tree, so they are not on
# disk until the first rsync. deploy.sh links them once the code is there.
echo "==> disabling the default nginx site"
sudo rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/a2b.conf

echo
echo "Bootstrap complete. Now run deploy.sh from your machine to ship the code."
