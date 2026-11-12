#!/usr/bin/env bash
# Publish the console on a public HTTPS address via a Cloudflare quick tunnel.
#
# Run by a2b-tunnel.service, as root. Not meant to be run by hand.
#
# Why a tunnel rather than opening port 80: the console has a login now, and a
# quick tunnel gets TLS terminated at Cloudflare for free. Opening the security
# group would put passwords on the wire in cleartext.
#
# The catch with a *quick* tunnel is that the hostname is assigned at connect
# time and changes on every restart. That matters here because A2B_MCP_BASE is
# compiled into entry prompts, so this script rewrites it whenever the name
# changes and restarts the API. A named tunnel on a real domain removes the
# whole problem; see the README.
set -euo pipefail

LOG=/var/log/a2b-tunnel.log
URL_FILE=/var/lib/a2b/public-url.txt
ENV_FILE=/etc/a2b/a2b.env

: >"$LOG"
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:80 >>"$LOG" 2>&1 &
PID=$!
# Do not leave a tunnel running if systemd stops this script.
trap 'kill "$PID" 2>/dev/null || true' EXIT TERM INT

URL=""
for _ in $(seq 1 90); do
    URL=$(grep -oE 'https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com' "$LOG" | head -1 || true)
    [ -n "$URL" ] && break
    # If cloudflared died, there is no point waiting the full ninety seconds.
    kill -0 "$PID" 2>/dev/null || { echo "cloudflared exited before publishing a URL"; cat "$LOG"; exit 1; }
    sleep 1
done

if [ -z "$URL" ]; then
    echo "no tunnel URL after 90s; last log lines:"
    tail -20 "$LOG"
    exit 1
fi

echo "tunnel up: $URL"
printf '%s\n' "$URL" >"$URL_FILE"

# Keep the scheme. This used to write a bare hostname, which travelled into
# every entry prompt and gave the customer's AI a string it could not fetch.
DESIRED="A2B_MCP_BASE=${URL}/mcp"
if ! grep -qxF "$DESIRED" "$ENV_FILE" 2>/dev/null; then
    echo "updating A2B_MCP_BASE -> ${URL}/mcp"
    sed -i '/^A2B_MCP_BASE=/d;/^A2B_COOKIE_SECURE=/d' "$ENV_FILE"
    {
        printf '%s\n' "$DESIRED"
        # Cloudflare terminates TLS, so the browser is on HTTPS and the session
        # cookie can carry Secure. Localhost stays usable: browsers treat
        # 127.0.0.1 as a secure origin, so the SSH-tunnel route still works.
        printf 'A2B_COOKIE_SECURE=1\n'
    } >>"$ENV_FILE"
    systemctl restart a2b-api
fi

wait "$PID"
