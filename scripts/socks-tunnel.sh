#!/bin/bash
# Residential SOCKS proxy tunnel to Hetzner server
#
# Exposes your PC's internet (Swedish residential IP) as a SOCKS5 proxy
# on the Hetzner server at port 1090. Used by tipwin which blocks VPN IPs.
#
# How it works:
# 1. SSH connects to Hetzner with remote port forwarding (-R)
# 2. A Python micro SOCKS proxy runs locally on your PC (port 1090)
# 3. Server port 1090 forwards to your PC's port 1090
# 4. Traffic exits through your Swedish home IP
#
# Run: bash scripts/socks-tunnel.sh
# Or starts automatically via mirror.bat

SERVER="root@148.251.40.251"
LOCAL_PORT=1090
REMOTE_PORT=1090
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Starting residential SOCKS tunnel to $SERVER..."
echo "Server port $REMOTE_PORT → your Swedish home IP"
echo "Used by: tipwin (VPN-blocked sites)"
echo "Press Ctrl+C to stop."

# Start local Python SOCKS5 proxy in background
python "$SCRIPT_DIR/mini_socks.py" $LOCAL_PORT &
SOCKS_PID=$!
sleep 1

if ! kill -0 $SOCKS_PID 2>/dev/null; then
    echo "ERROR: Failed to start local SOCKS proxy"
    exit 1
fi
echo "Local SOCKS proxy running on port $LOCAL_PORT (PID $SOCKS_PID)"

cleanup() {
    echo "Stopping..."
    kill $SOCKS_PID 2>/dev/null
    exit 0
}
trap cleanup INT TERM

while true; do
    ssh -R "0.0.0.0:${REMOTE_PORT}:localhost:${LOCAL_PORT}" \
        -N \
        -o ServerAliveInterval=15 \
        -o ServerAliveCountMax=5 \
        -o ExitOnForwardFailure=yes \
        -o TCPKeepAlive=yes \
        "$SERVER" 2>/dev/null

    echo "$(date): Tunnel disconnected. Reconnecting in 3s..."
    sleep 3
done
