#!/usr/bin/env bash
# Ship the current working tree to the server and restart the service.
#
#   ./deploy/deploy.sh              # deploy
#   ./deploy/deploy.sh --seed       # deploy, then seed the demo vendor
#
# Run bootstrap.sh once before the first deploy.
set -euo pipefail

HOST=${A2B_HOST:-a2b}
APP_DIR=/opt/a2b
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEED=false
[ "${1:-}" = "--seed" ] && SEED=true

cd "$REPO_ROOT"

echo "==> syncing $REPO_ROOT -> $HOST:$APP_DIR"
# --delete keeps the server from accumulating files that no longer exist here.
# The data directory is deliberately outside APP_DIR so it is never in scope.
# --stats rather than --info=, which the rsync macOS ships does not know.
rsync -az --delete --stats \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude 'data/' \
    --exclude '.shots/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    ./ "$HOST:$APP_DIR/"

echo "==> installing dependencies"
ssh "$HOST" "cd $APP_DIR && .venv/bin/pip install --quiet --upgrade -r requirements.txt"

# Both of these live in the deploy tree, so they are only linkable after a sync.
echo "==> registering units"
ssh "$HOST" "
    sudo ln -sfn $APP_DIR/deploy/a2b-api.service    /etc/systemd/system/a2b-api.service
    sudo ln -sfn $APP_DIR/deploy/a2b-tunnel.service /etc/systemd/system/a2b-tunnel.service
    sudo chmod +x $APP_DIR/deploy/tunnel.sh
    sudo systemctl daemon-reload
    sudo systemctl enable a2b-api >/dev/null
"

# nginx-a2b.conf claims `listen 80 default_server`, and so does whatever else
# is already enabled. Linking it in unconditionally is how you get two default
# servers on one port: `nginx -t` then fails, and because the symlink outlives
# this script, the failure is inherited by the next reload or reboot rather
# than by the person who caused it.
#
# So it goes in only when nothing else is serving port 80. The instance now
# runs a hand-written multi-environment config that this file knows nothing
# about, and quietly winning that argument is worse than refusing to have it.
#
# `grep -R`, not `-r`: sites-enabled is a directory of symlinks into
# sites-available, and `-r` skips symlinks silently. The first version of this
# guard used `-r`, found nothing, linked the file anyway, and produced the exact
# duplicate-default-server failure it was written to prevent.
echo "==> nginx site"
ssh "$HOST" "
    if [ -e /etc/nginx/sites-enabled/a2b.conf ]; then
        echo '    a2b.conf already linked — leaving it alone'
    elif sudo grep -Rlq 'default_server' /etc/nginx/sites-enabled/ 2>/dev/null; then
        echo '    another site already owns port 80:'
        sudo grep -Rl 'default_server' /etc/nginx/sites-enabled/ | sed 's/^/      /'
        echo '    not linking deploy/nginx-a2b.conf. Routing is a deliberate change —'
        echo '    see deploy/nginx-a2b.conf and reconcile it by hand.'
    else
        sudo ln -sfn $APP_DIR/deploy/nginx-a2b.conf /etc/nginx/sites-enabled/a2b.conf
        echo '    linked deploy/nginx-a2b.conf'
    fi
"

if [ "$SEED" = true ]; then
    echo "==> seeding demo data"
    ssh "$HOST" "cd $APP_DIR && A2B_DATA_DIR=/var/lib/a2b .venv/bin/python scripts/seed_demo.py"
fi

echo "==> restarting services"
ssh "$HOST" "sudo systemctl restart a2b-api && sudo nginx -t && sudo systemctl reload nginx"

echo "==> health"
sleep 2
ssh "$HOST" "curl -fsS --max-time 5 http://127.0.0.1/health && echo"

# `hostname -f` gives the VPC-internal name, which is useless from a laptop.
PUBLIC=$(ssh "$HOST" '
    T=$(curl -fsS --max-time 3 -X PUT http://169.254.169.254/latest/api/token \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
    curl -fsS --max-time 3 -H "X-aws-ec2-metadata-token: $T" \
        http://169.254.169.254/latest/meta-data/public-hostname' 2>/dev/null || echo "$HOST")

echo
# Deliberately not restarted above: a quick tunnel is handed a new hostname
# every time it reconnects, and that hostname is baked into published prompts.
TUNNEL=$(ssh "$HOST" 'cat /var/lib/a2b/public-url.txt 2>/dev/null' || true)
if [ -n "$TUNNEL" ]; then
    echo "Deployed.  public:  $TUNNEL/console/"
    echo "           direct:  http://$PUBLIC/console/  (needs port 80 open)"
else
    echo "Deployed.  console: http://$PUBLIC/console/"
    echo "If that does not load, port 80 is closed in the instance's security group."
    echo "For a public HTTPS link without touching AWS: sudo systemctl start a2b-tunnel"
fi
