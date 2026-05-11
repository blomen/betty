#!/bin/bash
# Session cleanup — runs at the end of each scheduled training window.
# Four actions per the 2026-05-11 design:
#   1. Reset broker.tracker.consecutive_stops + clear any session halts via HTTP
#   2. Archive today's broker_trades to parquet under /app/data/rl/trades_archive/
#   3. Prune live_episodes/_chunks/* once they've been merged into the training pool
#   4. Cancel any orphan TopstepX orders left in the book from broken sessions
#
# Idempotent — safe to run multiple times. Logs to /app/data/rl/daemon.log
# via the caller.

set -u

ARNOLD_API_KEY="${ARNOLD_API_KEY:-}"
LIVE_DIR=/app/data/rl/live_episodes
ARCHIVE_DIR=/app/data/rl/trades_archive
TODAY=$(date -u +%Y-%m-%d)

log() {
    echo "[cleanup $(date -u '+%H:%M:%S')] $1"
}

# --- 1. Reset broker counters + clear halts ---
log "Step 1/4: Resetting broker counters + halts"
if [ -z "$ARNOLD_API_KEY" ]; then
    ARNOLD_API_KEY=$(grep -E '^ARNOLD_API_KEY=' /app/.env 2>/dev/null | cut -d= -f2-)
fi
# /api/stocks/recover flattens (no-op if flat), clears _halted, leaves
# tracker in flat state. We follow up with a direct python exec to reset
# the per-session counters that recover() doesn't touch.
curl -s -X POST "http://localhost:8000/api/stocks/recover" \
    -H "X-API-Key: $ARNOLD_API_KEY" >/dev/null 2>&1 || true
python3 -c "
try:
    from src.stocks import dashboard as _d
    adapter = _d._state.get('adapter')
    if adapter:
        adapter.tracker.consecutive_stops = 0
        adapter._halted = False
        adapter._halt_reason = ''
        print('Tracker counters reset (consecutive_stops=0, halt cleared)')
    else:
        print('No adapter in dash_state — counters NOT reset (separate process)')
except Exception as e:
    print(f'Counter reset failed: {e}')
" 2>&1 | sed 's/^/  /'

# --- 2. Archive today's broker_trades to parquet ---
log "Step 2/4: Archiving broker_trades for $TODAY"
mkdir -p "$ARCHIVE_DIR"
ARCHIVE_FILE="$ARCHIVE_DIR/${TODAY}.parquet"
python3 -c "
import os, sys
try:
    import pandas as pd
    from sqlalchemy import create_engine, text
    db_pwd = os.environ.get('DB_PASSWORD', '')
    eng = create_engine(f'postgresql://arnold:{db_pwd}@postgres:5432/arnold')
    q = text(\"SELECT * FROM broker_trades WHERE session_date = :d ORDER BY ts\")
    df = pd.read_sql(q, eng, params={'d': '$TODAY'})
    if len(df) == 0:
        print('No trades for $TODAY — skipping archive')
    else:
        df.to_parquet('$ARCHIVE_FILE', index=False)
        print(f'Wrote {len(df)} trades to $ARCHIVE_FILE')
except Exception as e:
    print(f'Archive failed: {e}')
" 2>&1 | sed 's/^/  /'

# --- 3. Prune merged live_episodes chunks ---
# After ingest-live-trades + merge-live folds chunks into the main training
# pool, the originals can be deleted. The pipeline's merge step writes
# .merged_chunks (a sentinel list of chunk filenames that have been folded).
# Anything in that list is safe to delete.
log "Step 3/4: Pruning merged live_episodes chunks"
MERGED_LIST="$LIVE_DIR/.merged_chunks"
if [ -f "$MERGED_LIST" ]; then
    pruned=0
    while IFS= read -r chunk_name; do
        chunk_path="$LIVE_DIR/$chunk_name"
        if [ -f "$chunk_path" ]; then
            rm -f "$chunk_path"
            pruned=$((pruned + 1))
        fi
    done < "$MERGED_LIST"
    > "$MERGED_LIST"  # Empty the list — next pipeline rewrites it
    log "  Pruned $pruned merged chunks"
else
    # Fall back: keep only chunks newer than 7 days (conservative — chunks
    # may not have been merged yet). Pipeline owns chunk lifecycle; this is
    # just a safety drain so /app/data/rl doesn't grow unbounded.
    aged=$(find "$LIVE_DIR" -maxdepth 1 -name 'obs_*.npy' -mtime +7 2>/dev/null | wc -l)
    if [ "$aged" -gt 0 ]; then
        find "$LIVE_DIR" -maxdepth 1 -name 'obs_*.npy' -mtime +7 -delete 2>/dev/null
        log "  Pruned $aged chunks older than 7 days (no .merged_chunks marker)"
    else
        log "  Nothing to prune"
    fi
fi

# --- 4. Cancel orphan TopstepX orders ---
# After the bracket-discovery + reconcile-loop work it's still possible for
# stop or limit orders to be left dangling (deploy interruptions, force
# flattens, etc.). Sweep at session boundary: query Order/searchOpen and
# cancel anything that doesn't have a matching live position.
log "Step 4/4: Cancelling orphan TopstepX orders"
python3 -c "
import asyncio
try:
    from src.stocks.config import TopstepXConfig
    from src.stocks.topstepx_client import TopstepXClient
    cfg = TopstepXConfig.from_env()
    async def t():
        c = TopstepXClient(cfg)
        if not await c.connect():
            print('TopstepX auth failed — skipping orphan sweep')
            return
        positions = await c.search_open_positions()
        if positions:
            print(f'  {len(positions)} open position(s) — leaving stops attached, NOT pruning')
        else:
            r = await c._post('/api/Order/searchOpen', {'accountId': c._account_id})
            orders = r.get('orders', []) or []
            print(f'  Found {len(orders)} open order(s) with no position')
            cancelled = 0
            for o in orders:
                oid = o.get('id')
                try:
                    await c.cancel_order(oid)
                    cancelled += 1
                except Exception as ex:
                    print(f'  Cancel of {oid} failed: {ex}')
            print(f'  Cancelled {cancelled} orphan order(s)')
        await c.close()
    asyncio.run(t())
except Exception as e:
    print(f'Orphan sweep failed: {e}')
" 2>&1 | sed 's/^/  /'

log "Session cleanup done."
