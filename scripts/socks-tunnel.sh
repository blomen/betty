#!/bin/bash
# Residential SOCKS proxy tunnel to Hetzner server
# Your Swedish residential IP is used by providers that block VPN IPs (tipwin).
#
# Creates a reverse SOCKS tunnel on port 1090 (NordVPN uses 1080).
# When active, RESIDENTIAL_PROXY_URL=socks5://host.docker.internal:1090 is used.
# When down, tipwin extraction skips gracefully.
#
# Run: bash scripts/socks-tunnel.sh
# Or starts automatically via mirror.bat

SERVER="root@148.251.40.251"
PORT=1090

echo "Starting residential SOCKS tunnel to $SERVER (port $PORT)..."
echo "Your Swedish home IP will be used for tipwin (VPN-blocked sites)."
echo "Press Ctrl+C to stop."

while true; do
    ssh -R "0.0.0.0:$PORT" -N \
        -o ServerAliveInterval=15 \
        -o ServerAliveCountMax=5 \
        -o ExitOnForwardFailure=yes \
        -o TCPKeepAlive=yes \
        "$SERVER" 2>/dev/null

    echo "$(date): Tunnel on port $PORT disconnected. Reconnecting in 3s..."
    sleep 3
done
