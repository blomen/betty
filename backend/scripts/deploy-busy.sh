#!/usr/bin/env bash
# deploy-busy.sh — probe whether ANY deploy/build/teardown is in flight on this server.
#
# Background: server-deploy.sh holds an exclusive flock on .deploy.lock for the
# duration of its work, so two concurrent invocations of THAT script serialise.
# But raw `docker compose build`, `docker compose down`, `docker buildx`, and
# `docker rmi arnold-backend` (and `docker system prune`) bypass the lock
# entirely. When multiple agents collaborate on this server, an agent that only
# checks the flock will get a false-clear-to-go while another agent is mid
# teardown / rebuild.
#
# This script reports the union of all relevant in-flight states so an agent
# can probe before doing anything destructive.
#
# Usage:
#   bash deploy-busy.sh           # human-readable, exit 0 if free, 1 if busy
#   bash deploy-busy.sh --json    # JSON output, same exit codes
#
# Exit codes:
#   0  — server appears free (no deploy script, no docker compose work, image
#        present, container present)
#   1  — busy: at least one indicator says deploy/teardown is in flight
#   2  — degraded: image or container missing (likely teardown in progress
#        or post-teardown limbo before someone starts the rebuild)
#
# Reports each independent indicator separately so the caller can decide
# whether the situation is "active deploy" (wait) vs "stale lock" (clean up).

set -u

ARNOLD_DIR="${ARNOLD_DIR:-/opt/arnold}"
LOCK_FILE="${ARNOLD_DIR}/.deploy.lock"
JSON=0
[ "${1:-}" = "--json" ] && JSON=1

# ---- helpers ----

# Returns matching PIDs+cmdlines, one per line, or empty.
_pgrep() {
    pgrep -fa "$1" 2>/dev/null | grep -v "deploy-busy.sh" || true
}

# Returns "yes" or "no"
_lock_held() {
    if lsof "$LOCK_FILE" >/dev/null 2>&1; then
        echo "yes"
    else
        echo "no"
    fi
}

# Returns image creation timestamp + ID, or empty.
_image_state() {
    docker images arnold-backend --format '{{.CreatedAt}} {{.ID}}' 2>/dev/null | head -1
}

# Returns container Status string, or empty.
_container_state() {
    docker inspect arnold-backend-1 --format '{{.State.Status}} ExitCode={{.State.ExitCode}}' 2>/dev/null || true
}

# JSON-escape a string (basic, sufficient for our values)
_jq_escape() {
    python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))' 2>/dev/null \
        || printf '"%s"' "$(printf '%s' "$1" | sed 's/"/\\"/g')"
}

# ---- gather ----

DEPLOY_SCRIPT_PIDS="$(_pgrep 'server-deploy\.sh')"
COMPOSE_BUILD_PIDS="$(_pgrep 'docker compose.* build|docker-compose.* build|docker compose.*--build')"
COMPOSE_UPDOWN_PIDS="$(_pgrep 'docker compose.* up|docker compose.* down|docker compose.* stop|docker compose.* rm')"
BUILDX_PIDS="$(_pgrep 'docker buildx|docker-buildx|/usr/libexec/docker/cli-plugins/docker-buildx')"
PRUNE_PIDS="$(_pgrep 'docker (system )?prune|docker rmi|docker rm ')"

LOCK_HELD="$(_lock_held)"
IMAGE_STATE="$(_image_state)"
CONTAINER_STATE="$(_container_state)"

# ---- classify ----

BUSY=0
DEGRADED=0
PHASES=()
DETAILS=()

if [ -n "$DEPLOY_SCRIPT_PIDS" ]; then
    BUSY=1
    PHASES+=("deploy-script-running")
    DETAILS+=("server-deploy.sh PIDs: $(echo "$DEPLOY_SCRIPT_PIDS" | wc -l)")
fi
if [ -n "$COMPOSE_BUILD_PIDS" ] || [ -n "$BUILDX_PIDS" ]; then
    BUSY=1
    PHASES+=("docker-build-running")
fi
if [ -n "$COMPOSE_UPDOWN_PIDS" ]; then
    BUSY=1
    PHASES+=("compose-up-down-running")
fi
if [ -n "$PRUNE_PIDS" ]; then
    BUSY=1
    PHASES+=("docker-prune-or-rm-running")
fi
if [ "$LOCK_HELD" = "yes" ] && [ $BUSY -eq 0 ]; then
    # Lock held but no process matches — stale lock
    BUSY=1
    PHASES+=("stale-lock")
fi
if [ -z "$IMAGE_STATE" ]; then
    DEGRADED=1
    PHASES+=("image-missing")
fi
if [ -z "$CONTAINER_STATE" ]; then
    DEGRADED=1
    PHASES+=("container-missing")
fi

PHASE_LIST="$(IFS=,; echo "${PHASES[*]:-none}")"

# ---- emit ----

if [ "$JSON" -eq 1 ]; then
    python3 - <<PY
import json
print(json.dumps({
    "busy": bool($BUSY),
    "degraded": bool($DEGRADED),
    "phases": [p for p in "$PHASE_LIST".split(",") if p and p != "none"],
    "lock_held": "$LOCK_HELD" == "yes",
    "image_state": "$IMAGE_STATE",
    "container_state": "$CONTAINER_STATE",
    "deploy_script_pids": [l.split()[0] for l in """$DEPLOY_SCRIPT_PIDS""".splitlines() if l.strip()],
    "compose_build_pids": [l.split()[0] for l in """$COMPOSE_BUILD_PIDS""".splitlines() if l.strip()],
    "compose_updown_pids": [l.split()[0] for l in """$COMPOSE_UPDOWN_PIDS""".splitlines() if l.strip()],
    "buildx_pids": [l.split()[0] for l in """$BUILDX_PIDS""".splitlines() if l.strip()],
    "prune_pids": [l.split()[0] for l in """$PRUNE_PIDS""".splitlines() if l.strip()],
}, indent=2))
PY
else
    echo "busy=$BUSY degraded=$DEGRADED"
    echo "phases: $PHASE_LIST"
    echo "lock_held: $LOCK_HELD"
    echo "image: ${IMAGE_STATE:-MISSING}"
    echo "container: ${CONTAINER_STATE:-MISSING}"
    [ -n "$DEPLOY_SCRIPT_PIDS"  ] && echo "deploy-script:" && echo "$DEPLOY_SCRIPT_PIDS"
    [ -n "$COMPOSE_BUILD_PIDS"  ] && echo "compose-build:" && echo "$COMPOSE_BUILD_PIDS"
    [ -n "$COMPOSE_UPDOWN_PIDS" ] && echo "compose-up/down:" && echo "$COMPOSE_UPDOWN_PIDS"
    [ -n "$BUILDX_PIDS"         ] && echo "buildx:" && echo "$BUILDX_PIDS"
    [ -n "$PRUNE_PIDS"          ] && echo "prune/rm:" && echo "$PRUNE_PIDS"
fi

# Exit policy: 0=free, 1=busy, 2=degraded-only.
# Busy beats degraded — if work IS in flight, that's the most important fact.
if [ $BUSY -eq 1 ]; then
    exit 1
elif [ $DEGRADED -eq 1 ]; then
    exit 2
else
    exit 0
fi
