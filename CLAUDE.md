# Arnold - Betting Analytics Platform

> **PROJECT RENAMED 2026-04-23: `firev` → `arnold`.** If your context still references `firev`, you're stale — re-read this file. Summary of changes below; full plan in [docs/superpowers/plans/2026-04-23-rename-firev-to-arnold.md](docs/superpowers/plans/2026-04-23-rename-firev-to-arnold.md).
>
> | Was | Now |
> |---|---|
> | `blomen/Firev` (GitHub) | `blomen/Arnold` (GitHub auto-redirects old URLs) |
> | `/opt/firev` (server) | `/opt/arnold` |
> | `c:\Users\rasmu\firev` (local) | `c:\Users\rasmu\arnold` (rename pending — may still be `firev` until user closes VS Code) |
> | `firevsports/`, `firevstocks/` | `arnoldsports/`, `arnoldstocks/` |
> | `firev-{backend,nginx,postgres}-1` | `arnold-{backend,nginx,postgres}-1` |
> | `firev_*` Docker volumes | `arnold_*` (old volumes deleted) |
> | DB role + name `firev` | `arnold` (market DB name unchanged) |
> | env `FIREV_API_KEY` | `ARNOLD_API_KEY` |
> | nginx basic-auth user `firev` | `arnold` (same password) |
>
> **Data loss to be aware of:** the historical Databento NQ tick parquets (~39 months in `/app/data/rl/ticks/*.parquet`) were lost during the rename's volume cleanup — `firev_firev_data` was deleted before we realized it held the only copy. Recovery via filesystem forensics failed (inode metadata already purged). Surviving: trained v5 RL models (in `arnold_arnold_data/rl/archive/20260423_200549/`), 748 pre-processed episodes, market DB (4M live trades), all event/odds data. Future RL re-processing of raw ticks is blocked unless the user re-downloads from Databento.
>
> **New backup script:** `/root/rl-backup.sh` runs daily at 04:00 UTC → `/root/rl-backups/` (rsyncs ticks/archive/episodes + pg_dumps both DBs). Same disk only — set up offsite if you care.

## WHAT This Project Is

Arnold compares odds across 40+ sportsbooks against sharp sources (Pinnacle) to find value bets.

**Tech stack:** Python 3.10+ / FastAPI / PostgreSQL / Docker / Playwright | React 19 / TypeScript / Vite / Tailwind

## Two Programs

The previous `arnoldsports/` + `arnoldstocks/` split was collapsed into a single local client; the repo now contains two programs sharing one codebase:

| Program | Where it runs | What it does | How to start |
|---------|--------------|--------------|--------------|
| **Server** | Hetzner 24/7 | Headless data engine: extraction, analysis, DB, API, signals WS, RL training | `docker compose up -d` |
| **Arnold (local)** | Your PC | Unified betting + trading client: Sports, Stocks (signals console), Bankroll, Stats, Playwright mirror, TopstepX stream/relay | `arnold.bat` |

**Server** is a pure compute/data engine — no UI. Extraction, analysis, signal generation via `level_monitor`, the RL training daemon, and the `/ws/signals` WebSocket all live here.

**Arnold (local)** is one FastAPI process + one React SPA. Tabs: **Sports** (unified arb + value bet play), **Stocks** (signals console — zone cards + live signal feed; chart drawn by Tampermonkey userscript on TradingView), **Bankroll** (Sportbets + Trading sub-tabs), **Stats** (Betting + Trading sub-tabs). The launcher opens an SSH tunnel to the server API, starts the local FastAPI (which reverse-proxies `/api/*` to the tunnel, mounts `/mirror/*` for the Playwright browser control, and mounts `/stocks/*` for the TopstepX dashboard), and then opens the browser. TopstepX authentication + signal relay run as asyncio tasks inside the same process unless `STOCKS_AUTONOMOUS=true` (server-side broker mode, tested).

## Architecture

```
Hetzner Server (24/7, headless)              Your PC
├── backend/src/                             ├── arnold/               # Unified local client
│   ├── providers/    # 16 extractors        │   ├── server.py         # FastAPI: /api proxy + /mirror + /stocks + static
│   ├── pipeline/     # orchestrator         │   ├── launch.py         # SSH tunnel + uvicorn + browser open
│   ├── analysis/     # scanner, devig       │   ├── proxy.py          # /api/* reverse-proxy to server via tunnel
│   ├── matching/     # Fuzzy matching       │   ├── stocks_runtime.py # TopstepX client + stream + SignalRelay
│   ├── market_data/  # level_monitor, VWAP  │   ├── mirror/
│   ├── rl/           # zones, DQN, training │   │   ├── browser.py    # Playwright lifecycle + interception
│   ├── bankroll/     # Kelly sizing         │   │   ├── play_loop.py  # Automated betting state machine
│   ├── api/          # FastAPI + /ws/signals│   │   ├── arb_runner.py │   │   ├── pending_loop.py
│   └── db/           # PostgreSQL ORM       │   │   └── workflows/    # Provider DOM automation
└── docker-compose.yml                       │   └── frontend/         # One React app — all tabs live here
                                             │       └── src/pages/
                                             │           ├── PlayPage.tsx       (Sports tab)
                                             │           ├── BankrollPage.tsx   (Sportbets bankroll)
                                             │           ├── StatsPage.tsx      (Betting stats)
                                             │           └── stocks/
                                             │               ├── SignalsPage.tsx (cards-based console; chart drawn by Tampermonkey userscript on TradingView)
                                             │               ├── BankrollPage.tsx
                                             │               └── StatsPage.tsx
                                             │
                                             └── arnold.bat  → SSH tunnel → server API (port 18000)
```

### Frontend

Single app at `arnold/frontend/`. Tabs and sub-tabs:

| Tab | Sub-tabs | What it shows |
|-----|----------|---------------|
| **Sports** | Value Bets, Arbitrage | Unified betting view — value vs. Pinnacle, arb across soft books |
| **Stocks** | — | Signals console: zone cards + live signal feed. Chart rendering moved to Tampermonkey userscript on TradingView (`arnold/tv_overlay/userscript/arnold-overlay.user.js`). |
| **Bankroll** | Sportbets, Trading | Provider balances + Kelly sizing; TopstepX account + drawdown |
| **Stats** | Betting, Trading | Historical bet + trade performance |

Each top-level tab is wrapped in its own `ErrorBoundary` so a stocks-side crash can't bring down the sports tabs. `useDashboardWS` is mounted at the App root so ticks/zones/signals accumulate regardless of active tab.

**The server is API-only — no visual UI.** All betting/trading happens through the local client.

### Server Backend

```
backend/src/
├── providers/        # 16 extractors (Kambi, Altenar, Gecko V2, Spectate, Pinnacle, Polymarket, etc.)
├── pipeline/         # orchestrator, storage, scheduler, pool_manager, circuit_breaker, cache, health, metrics
├── analysis/         # scanner, value, bonus, devig, ev_enrichment
├── matching/         # Event normalization + fuzzy matching
├── bankroll/         # Kelly criterion + stake sizing
├── repositories/     # Data access abstraction
├── services/         # Business logic coordination
├── db/               # SQLAlchemy models — ORM only
├── api/              # FastAPI application + routes
├── constants.py      # ALLOWED_MARKETS, SHARP_PROVIDERS
└── app.py            # Typer CLI
```

## Production Deployment (IMPORTANT — READ FIRST)

**Arnold runs in production on a Hetzner server. Do NOT try to run the backend locally — it's deployed.**

### Server Details
- **Server**: Hetzner Dedicated i7-7700 (4c/8t, 64 GB RAM, 2x 256 GB SSD RAID 1), Ubuntu 24.04
- **IP**: `148.251.40.251`
- **SSH**: `ssh root@148.251.40.251`
- **App URL**: `https://148.251.40.251` (behind nginx basic auth, self-signed cert)
- **Repo on server**: `/opt/arnold` (main branch)

### Docker Containers
3 containers via `docker-compose.yml`:
- `arnold-backend-1` — FastAPI + uvicorn + Playwright (internal only, no public port)
- `arnold-postgres-1` — PostgreSQL 16 (internal only, no public port)
- `arnold-nginx-1` — Nginx reverse proxy (ports 80/443, HTTPS + basic auth)

### Security
- **Nginx basic auth** protects all routes (credentials in `nginx/.htpasswd` on server, gitignored)
- **No public ports** for backend (8000) or postgres (5432) — only reachable via Docker internal network
- **Non-root container** — backend runs as `arnold` user (uid 1000), not root
- **HTTPS enforced** with TLS 1.2/1.3, HSTS, rate limiting (30 req/s per IP)
- **Security headers**: CSP, X-Frame-Options DENY, Referrer-Policy, Permissions-Policy, `server_tokens off`
- **CORS lockdown** — origins from `CORS_ORIGINS` env var (not hardcoded), explicit methods/headers only
- `/health/*` endpoints are exempted from auth (nginx `location /health` block with `auth_basic off`)
- To update the password: `ssh root@148.251.40.251 "openssl passwd -apr1 NEW_PASSWORD | xargs -I{} echo 'arnold:{}' > /opt/arnold/nginx/.htpasswd && cd /opt/arnold && docker compose restart nginx"`

### Database
- **Main DB**: `postgresql://arnold:${DB_PASSWORD}@postgres:5432/arnold` (events, odds, bets, profiles, opportunities)
- **Market DB**: `postgresql://arnold:${DB_PASSWORD}@postgres:5432/market` (trades, candles — high-frequency tick data)
- **No more SQLite** — fully migrated to PostgreSQL. SQLite fallback exists in code for local dev without Docker.

### Environment
- `.env.docker` — API keys, DB config, and `CORS_ORIGINS` (loaded via `env_file` in docker-compose)
- `.env` — just `DB_PASSWORD=${DB_PASSWORD}` (for docker-compose `${DB_PASSWORD}` substitution)
- `PROXY_URL` — ISP residential proxy for Pinnacle (datacenter IPs blocked)
- `CORS_ORIGINS` — comma-separated allowed origins (e.g. `https://148.251.40.251`)

### How to Deploy Changes

**IMPORTANT: Always use the deploy script to prevent conflicts between concurrent agents.**

```bash
# After pushing to main (full rebuild — needed for ANY code/Dockerfile change):
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"

# For config/env-only changes (restart is NOT enough for code changes — code is baked into Docker image):
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh restart backend"

# Check logs (no lock needed):
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh logs backend 30"

# Check deploy status + containers + disk:
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh status"

# Clean up old Docker images and build cache:
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh cleanup"

# Check extraction:
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend cat /app/logs/extraction.log | tail -30"
```

### Docker Build (Multi-Stage)
The `Dockerfile` uses a 2-stage build for fast rebuilds:
- **Stage 1** (Node.js): Builds frontend → only `dist/` carried to final image (no Node.js runtime)
- **Stage 2** (Python): pip install cached by `pyproject.toml` layer → code-only changes skip pip/torch rebuild
- **Auto-cleanup**: `docker image prune` runs after each rebuild to prevent disk bloat
- Code-only rebuilds take ~30s (cached deps). Full rebuilds (pyproject.toml change) take ~5min.

### Health Endpoints (Public, No Auth)
- `GET /health` — basic status, boot_id, uptime
- `GET /health/live` — liveness probe
- `GET /health/ready` — readiness probe (DB connectivity, provider count)
- `GET /health/extraction` — extraction pipeline health: last 3 runs, failed providers, match rates, issues

### Multi-Agent Coordination (IMPORTANT)

Multiple Claude Code agents may work on this repo concurrently. **Follow these rules to avoid conflicts:**

1. **Always check server status before deploying**: Run `server-deploy.sh status` first. Note: status only shows "active deploy" if `STATUS_FILE` is present — it does NOT detect a wedged-but-still-running script. To see whether the lockfile is actually held, also run `ssh root@148.251.40.251 "pgrep -fa 'server-deploy.sh' && lsof /opt/arnold/.deploy.lock 2>/dev/null"`. A `pgrep` hit means the slot is still in use.
2. **Never run raw `docker compose up/restart/build`** — always use `scripts/server-deploy.sh` which acquires an exclusive `flock`. A PreToolUse hook blocks raw docker compose commands.
3. **Read-only operations are safe concurrently**: logs, status, DB queries, extraction logs
4. **Destructive operations are serialized by the lock**: rebuild, restart. **`git pull` outside the script is NOT lock-protected** — never run `cd /opt/arnold && git pull` manually. Use `bash server-deploy.sh pull` if you need to advance the server's working tree without rebuilding. Manual `git pull` followed by a cached rebuild creates source-vs-image drift: HEAD advances but the docker `COPY backend/` layer stays cached, so the new code is on disk but not in the running container.
5. **If the lock is held**, wait and retry — don't bypass it. If you suspect the holder is wedged, see "Deploy stuck on RL wait" below before forcibly clearing the lock.
6. **Coordinate git pushes**: Before pushing + deploying, run `git fetch && git log HEAD..origin/main --oneline` to see what other agents pushed since you forked, and `git log origin/main..HEAD --oneline` to confirm your push is a clean fast-forward. If origin is ahead, rebase or merge before pushing — don't force-push.
7. **Use `/deploy` skill** for guided deployment with health verification
8. **Use `/server-health` skill** for quick production status checks
9. **Deploy cooldown enforced**: 5-minute minimum between rebuilds — each rebuild kills extraction for 5-10 min. Batch changes and deploy once, don't rebuild per commit.
10. **Health verification**: Deploy script waits up to 2 min for `/health` to respond after rebuild. If it fails, deploy exits non-zero — investigate before retrying.
11. **Container watchdog**: Cron checks every 5 min and auto-restarts if backend is down. Don't rely on manual monitoring.
12. **Deploy stuck on RL wait (DEADLOCK ESCAPE)**: `server-deploy.sh` calls `wait_for_rl_training` which blocks up to **7200s** (2h) for a pipeline that the script's own comment admits "has never completed in 12 days." There is NO chunk-progress watchdog — the script only checks `ps aux | grep rl_train_pipeline`, so a daemon stuck at "step 1, chunks: 0/38" looks identical to one making real progress. **If the deploy hasn't advanced past `step 1` for 5 min**, treat it as wedged:
    1. Confirm the wedge: `ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend cat /app/data/rl/pipeline_progress; docker compose exec -T backend bash -c 'ls /app/data/rl/episodes/_chunks/obs_*.npy 2>/dev/null | wc -l'"` — if both numbers are unchanged after 5 min, RL is not progressing.
    2. Find the daemon PID: `docker compose exec -T backend bash -c 'ps aux | grep -E "rl_train" | grep -v grep'`.
    3. Kill it inside the container: `docker compose exec -T backend kill -9 <PID>` (`pkill` may itself get killed in a memory-pressured container — kill by PID).
    4. The deploy script's wait loop will see `rl_running=0` on its next 30s tick and proceed.
    5. **If you killed the local SSH but the remote bash is still running**: SSH to the server and `pkill -f 'server-deploy.sh rebuild'` then `rm -f /opt/arnold/.deploy.lock` — the orphaned bash holds the flock indefinitely. Verify with `pgrep -fa 'server-deploy.sh'` showing nothing before clearing the lock.
13. **Verify the running container actually has your code**: docker build cache + cached `COPY backend/ backend/` layers can ship an image whose source predates the latest `git pull`. After every rebuild, confirm:
    - `ssh root@148.251.40.251 "cd /opt/arnold && git rev-parse HEAD"` — server's git HEAD
    - `ssh root@148.251.40.251 "curl -sf http://localhost:8000/health"` — note the `boot_id` (changes on every container restart)
    - `ssh root@148.251.40.251 "cd /opt/arnold && docker compose ps backend --format json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get(\"CreatedAt\"))'"` — container creation time should be after your deploy completed
    - If git HEAD is ahead of what your deploy pulled (e.g. another agent pushed mid-deploy), the running container is stale — re-deploy with `--no-cache` or wait for the next pull cycle.
14. **Backend deploys vs frontend/local-client changes**: a commit touching ONLY `arnold/frontend/`, `arnold/mirror/`, `arnold/stocks_runtime.py`, `arnold/server.py`, `arnold/launch.py`, `arnold/proxy.py`, or `arnold/tv_overlay/` is **local-client only** and ships via `arnold.bat` (Vite + local FastAPI) — do NOT trigger a backend rebuild for these. Quick check: `git diff --name-only origin/main...HEAD | grep -v '^arnold/' | head -1` — if empty, no backend deploy needed. The autonomous broker tracker on active trades is far more fragile than any local-client bug.
15. **Background-deploy etiquette**: when running deploys via `Bash run_in_background=true` and SSH, the remote bash survives if you cancel the local task — always `pgrep -fa 'server-deploy.sh'` on the server BEFORE assuming the slot is free. Prefer foreground deploys when the change is blocking; background only when you have other independent work to do in parallel.
16. **Stocks-aware rebuild rules (when `STOCKS_AUTONOMOUS=true`)**: every rebuild severs the TopstepX SignalR session, causing ~15-60s of tick/candle data loss and a "Multiple sessions detected" reconnect race. For the trading side this matters more than for extraction. Rules:
    - **Open-position gate (enforced)**: `rebuild` and `restart` for the `backend` service in `server-deploy.sh` query TopstepX directly via `Position/searchOpen` and abort if anything is open. To deploy through a live trade anyway (e.g. paper account, or you accept the flatten), pass `ALLOW_OPEN_POSITION_DEPLOY=1`:
      ```bash
      ssh root@148.251.40.251 "ALLOW_OPEN_POSITION_DEPLOY=1 bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
      ```
      Default-deny — an agent can't silently force-deploy through a live trade. The shutdown handler flattens the position; that's a real PnL event, not a rebuild artifact.
    - **Batch frequent edits**: if you're iterating (many small commits on the same feature), accumulate locally and deploy once — not once per commit. Target ≤ 2 stocks-impacting rebuilds per hour during trading.
    - **Stocks-hot window**: US RTH runs 14:30–21:00 UTC and that's when zone density and trade opportunities peak. Non-critical rebuilds in this window trade model-learning data for convenience. Prefer deploys outside this window when the change isn't blocking.
    - **Startup grace**: the server waits `STOCKS_AUTH_STARTUP_DELAY_SEC` (default 30s) before auth'ing TopstepX on a fresh container, so the prior container's SignalR session can be cleaned up by TopstepX before we connect. Shorten via env if you're sure no other session exists.

### Currencies (READ BEFORE ANY CROSS-PROVIDER MATH)

**Providers run in DIFFERENT currencies. Never add, subtract, compare, or hedge-size across providers without converting first.**

| Currency | Providers |
|---|---|
| **USDC** | polymarket |
| **USD** | kalshi |
| **SEK** | every Swedish / EU softbook this user has: betinia, betsson, bethard, campobet, coolbet, dbet, interwetten, leovegas, pinnacle (this account is SEK-funded), quickcasino, spelklubben, tipwin, unibet, vbet, 10bet, 888sport, comeon, hajper, marathon, rainbet, stake, cloudbet |

The `bets.currency` column is authoritative — query it (`SELECT provider_id, currency, COUNT(*) FROM bets GROUP BY 1,2`) when in doubt.

**The rule:**
- **In code:** use `money.Money` + `money.Currency` from the [`money/`](money/) package. `money.convert(amount, from, to)` pivots through SEK. The pin lives in [`backend/src/config/`](backend/src/config/) — one source of truth (commit `ca144533`). Never write `stake_a + stake_b` across providers as raw floats.
- **In SQL analysis:** wrap with `CASE WHEN currency='SEK' THEN x/<sek_per_usd> WHEN currency IN ('USD','USDC') THEN x END` BEFORE any cross-provider `SUM`/`MIN`/`MAX`. A `MIN(stake * odds)` across legs without conversion is meaningless.
- **For arb checks:** worst-case payout = `MIN(stake_i × odds_i)` in **one base currency**, total stake = `SUM(stake_i)` in the **same base currency**. An arb is "guaranteed" iff `worst_payout_base ≥ total_stake_base`. Comparing 268 SEK to $15 USDC and concluding "broken hedge" is the canonical wrong answer — that's actually ~$25 vs $15, perfectly fine.
- **For bankroll / Kelly / stats / ROI:** same rule. Aggregate views over mixed-currency bets that don't convert are wrong by construction.

**The first hypothesis when a sizing/hedge/bankroll number looks off by 5-10×** is "did I mix currencies?" — not "the sizer is broken." `feature/unified-currency-layer` (the active branch) is rolling this enforcement out everywhere in code; analysts reading the DB directly are not exempt.

### Postgres FK Enforcement
**PostgreSQL enforces foreign key constraints — SQLite did not.** When writing storage code:
- Always `session.flush()` parent rows before inserting children (e.g., flush Event before inserting Odds)
- Delete children before parents in cleanup (delete Odds → Opportunities → Events)
- Boolean columns require actual `True`/`False`, not `0`/`1` integers
- Integer columns reject strings — cast or filter invalid data

### What Runs Autonomously
The server runs 24/7 without intervention:
- Extraction scheduler (see Extraction Tiers below for actual intervals per provider)
- Opportunity scanner (after each extraction)
- RL training daemon (replays ticks → trains GBT/DQN models, checks for new episodes every 4h)
- Container watchdog cron (every 5 min, auto-restarts if backend is down)
- Daily PostgreSQL backup at 3 AM UTC (`docker/pg-backup.sh`)

### Memory Budget (IMPORTANT — OOM killed the server on 2026-04-12)
64 GB total, partitioned via Docker `mem_limit` to prevent kernel OOM:
- **Postgres**: 12 GB cap (shared_buffers=4GB + work_mem + OS cache)
- **Backend**: 48 GB cap (Python + Playwright browsers + RL training)
- **OS/SSH/kernel**: ~4 GB remaining
If the backend exceeds 48 GB, Docker kills the **container** (not the kernel) and `restart: unless-stopped` brings it back. Without these limits, the OOM killer takes down SSH and requires a Hetzner Robot hard reset.

### CPU Isolation (RL vs Extraction)
RL training and extraction share the i7-7700 (4 cores / 8 HT threads). To prevent contention:
- **Cores 0-1 (threads 0,1,4,5)** → RL training daemon (2 workers, nice 19, via `taskset`)
- **Cores 2-3 (threads 2,3,6,7)** → Extraction browsers + API + everything else
- Set in `rl_train_daemon.sh`, `rl_train_pipeline.sh`, and the auto-start in `api/__init__.py`
- Disable daemon: `touch /app/data/rl/daemon_disabled` inside the container
- Manual pipeline run: `taskset -c 0,1,4,5 nice -n 19 bash /app/backend/scripts/rl_train_pipeline.sh`

### Stocks — Chart & Model Conventions (IMPORTANT)

Zones drawn on TradingView by the userscript at `arnold/tv_overlay/userscript/arnold-overlay.user.js` MUST reflect what the DQN sees, not a derived aesthetic. Keep the following invariants in sync between the userscript's rendering and the model observation.

**Zones are the single consolidated level view.** Individual level types (PDH/PDL, IB H/L, session H/L, TPO POC/VAH/VAL, per-TF VP POC/VAH/VAL, daily/weekly swings) and SMC signals (FVGs, order blocks) are all clustered into zones server-side. VWAP center + σ bands and zone bands render on the TradingView overlay — everything else rolls up into a zone's member count and strength.

**Zone strength math** (`backend/src/rl/zone_builder.py:_compute_strength`, as of 2026-04-24):
- Group members by **family** (`_LEVEL_FAMILY`) — VWAP center + σ bands share one family, daily POC/VAH/VAL share one, FVG bull/bear share one, order-block bull/bear share one, each swing timeframe is its own family, etc.
- **Max within family** — kills redundancy (5 VWAP bands ≈ 1 VWAP contribution, not 5).
- **Sum across families** — monotonic in confluence; adding any new-family level strictly grows raw strength.
- **Synergy bonuses** added for meaningful co-occurrence (`_SYNERGY_BONUS`): daily_swing+daily_vp, fvg+order_block, prior_session+vwap, daily_vp+prior_session, daily_swing+fvg, daily_swing+order_block. Conservative defaults pending empirical calibration.
- **Saturation** via `1 - exp(-raw / 1.5)` so score sits on [0, 1]. Single strong level lands near 0.5; 3-family confluence near 0.9.
- Adding a weak level can **never lower** the score (previous mean-based math had this bug).

**Userscript paint = model observation axes** (`rl/features/level_features.py:encode_zone_features`):
- Fill hue ← `COLOR_BY_STRENGTH(strength)` (heatmap: slate-blue → indigo → fuchsia → orange → red).
- Fill alpha (transparency) ← scaled inversely by strength (strong zones more opaque).
- Band geometry (top/bottom) ← zone `top` / `bottom` as emitted by `OverlayBroadcaster._zone_payload` (previously `upper_bound` / `lower_bound` — field names changed in broadcaster).
- Member count surfaces as the rectangle label `"<kind> ×<members>"`.
- `session_relevance` is a 4th model dim, not currently painted.
- **Do not fold multiple model dims into a single composite strength** — even though the userscript only paints fill hue today, the broadcaster emits all four dims as separate fields so future card / overlay tweaks can use them.

**FVGs and order blocks are first-class zone members.** Their ranges feed `level_monitor.load_levels` at the midpoint. `_LEVEL_FAMILY` puts FVG bull+bear into one family and OB bull+bear into another. Weights: FVG 0.6, OB 0.8 (`_HIERARCHY_WEIGHTS` in `zone_builder.py`). Do NOT re-introduce separate FVG overlays — the whole point of the consolidation is that SMC signals affect zone heat, not chart noise.

**Trade rendering uses ONLY TV's native `long_position` / `short_position` widget — both active AND closed.** Do NOT substitute custom rectangles, diagonals, exit markers, or any other shape for closed trades. This has been tried multiple times (v0.5.0 stop-to-exit band, v0.6.0 entry→exit diagonal) and rejected each time. The canonical view is:
- **Active trade**: long/short_position widget with `stopLevel` + `profitLevel` overrides. Phase 1 snapshot freezes both at first tick (broker's original_stop_price + tp_price). Phase 2 follows live broker values + draws a horizontal red trail-stop line on top.
- **Closed trade**: same widget, `end_time` = `closed_at`, frozen at the broker's final stop/TP values. NO trail line, NO rectangle, NO exit marker.
- Daily-only scope: broadcaster filters closed trades by `session_date == today_utc`; previous days' trades drop off the chart automatically at UTC midnight rollover.
- Current canonical implementation: [`arnold/tv_overlay/userscript/arnold-overlay.user.js`](arnold/tv_overlay/userscript/arnold-overlay.user.js) v0.8.0+, `_drawWidget(p, anchor, endEpoch, isLive)`.

**Model calibration shift (2026-04-24 → ~2026-05-15):** The live DQN weights were trained against the old mean-weight hierarchy (`sum/len/1.2`). The new `_compute_strength` shifts the distribution — isolated weak zones score *lower*, multi-family confluence scores *higher*. Both shifts are directionally correct (the old math could reduce strength when a weak level was added). The monotonic "higher = trust more" relationship the DQN learned keeps working, but absolute thresholds are recalibrating. Expected realignment: 2-3 weeks of live-episode accumulation at ~20-30 setups/day lets the daemon's natural retrain cycle drift the training pool toward new-math-dominant. Don't force a retrain now — the historical tick parquets are gone so a fresh replay would use a much smaller dataset (only April 2026 ticks survive).

**Volume profile (server-side — rendering moved off-chart):**
- Server still computes three VP windows: daily (today's session), weekly (rolling 7 days), monthly (rolling 30 days). Rolling windows are used instead of calendar boundaries — see `backend/src/services/market_service.py:_get_period_bars` — don't revert without thinking through the day-of-week/day-of-month thinness problem.
- d/w/m POC/VAH/VAL are zone members, not chart-spanning price lines. They influence zone strength via `_compute_strength` and are emitted to the userscript as zone data.
- VP histogram rendering (the right-edge panel) was part of the deleted `CandleChart.tsx` and is not present in the current frontend or userscript. Server still computes TPO and VP data — rendering is simply not wired yet.

**Touch-without-trade recording (already correct — don't "fix"):**
- `level_monitor._emit_zone_dqn_inference` calls `live_collector.on_zone_touch()` UNCONDITIONALLY after `dqn.infer()`, regardless of the decision. Every touch → `PendingEpisode`.
- Outcomes measured over `OUTCOME_WINDOWS = [10, 30, 60, 120, 300]s` in `live_collector._compute_reward` — handles delayed market reaction. Flushed to `data/rl/live_episodes/*.npy`.
- Skip / low-confidence touches ARE in the training set, labeled with actual post-touch reaction. Don't gate recording on inference output.

**No hard time-gating in reckless mode (added 2026-05-11).** In `RECKLESS_LEARNING_MODE=1` (paper-account default), `ZONE_COOLDOWN_S` and `MIN_TRADE_INTERVAL_S` are both **0** — the RL feedback loop teaches the model when to skip. Every rejected signal is a training tuple the trainer never gets, so hard cooldowns make the model permanently naive about "don't re-enter a zone that just stopped you out." When the model takes a 10-stop streak at one zone (today's trades 601-610 at 29,399.75), those 10 losing tuples → correlate cron → ingest-live-trades → next training cycle teaches "this signature → SKIP." Strict mode keeps the gates (120s zone / 30s interval) for live-capital protection. Don't propose post-loss backoff, exponential cooldowns, or any time-based suppression as a "fix" for losing streaks — that's the symptom; the cure is the feedback loop. See [feedback_no_hard_time_gates memory](C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\feedback_no_hard_time_gates.md). Structural rules that are NOT time gates (Phase 1 sacred, _pending_trade state check, _signal_lock) stay active in both modes.

### Live trade → training feedback loop (added 2026-04-25)

End-to-end ground-truth pipeline so the model learns from its own real outcomes, not just simulator estimates:

```
LevelMonitor fires signal
  → build_observation(rl_state) explicitly captures the 279-dim obs vector
  → _persist_stock_signal_async writes signal + observation_b64 to stock_signals
       ↓ (TopstepX fills, broker_adapter places + manages)
broker_trade row created with full context (entry/exit/stop/tp/was_stop/
  trail_count/signal_*/orderflow_score)
       ↓ (nightly cron at 23:55 UTC)
POST /api/stocks/signals/correlate → joins signal.trade_id = trade.id by
  ts (±60s) + entry_price (±5pt)
       ↓ (next pipeline cycle, step 0b)
rl ingest-live-trades reads (obs, action, realized_pnl_r) from labelled
  pairs, writes obs_LT*.npy / rc_/rr_/lt_/st_ to live_episodes/.
  Idempotent — tracks ingested trade_ids in .ingested_trade_ids.
       ↓
merge-live folds them into the main training pool
       ↓
DQN training learns from BOTH simulator episodes AND realized trades
```

Schema columns supporting this:
- `stock_signals.observation_b64` (TEXT, base64 of float32 bytes, ~1.5 KB/row)
- `stock_signals.observation_dim` (INTEGER)
- `stock_signals.trade_id` (FK to broker_trades, filled by correlate)
- `broker_trades.{tp_price, was_stop, trail_count, stop_ticks, signal_trigger, signal_cont_p, signal_rev_p, orderflow_score}` (added same day)

Postgres ALTER TABLE migrations live in `models._run_pg_migrations` — add new columns there, not via Alembic.

**Don't break the loop**:
- `level_monitor` MUST call `build_observation` BEFORE `dqn.infer` so the captured obs is the same one DQN saw (deterministic — both call the same builder).
- `_persist_stock_signal_async` is fire-and-forget threaded; if it raises, the trade itself still completes.
- The correlate cron MUST run before `ingest-live-trades` for that cycle, otherwise pairs stay unlinked. Current chain: `23:55 UTC cron → POST /correlate → session_review → next pipeline picks up`.
- `_pending_trade` dict in broker_adapter must include `orderflow_score` — it's how `of_score` survives from signal-time into the trade row.

### Stocks autonomous trading (added 2026-04-24)

`STOCKS_AUTONOMOUS=true` (set in `.env.docker`) makes the server own the TopstepX session. Without it, the local arnold app authenticates and trades. With both, TopstepX kicks one with "Multiple sessions detected" — local `arnold/stocks_runtime.py` checks the env var and no-ops.

Server bootstrap lives in `backend/src/stocks/server_bootstrap.py`:
- Authenticates TopstepXClient
- Starts TopstepXStream (ticks + fills server-side)
- Wires BrokerAdapter to LevelMonitor via `set_broker_adapter` (same pattern as Rithmic / Tradovate paths)
- Direct DB insert for closed trades — no HTTP POST round-trip
- Runs as a background task in lifespan so the 30s startup grace doesn't block /health

`STOCKS_AUTH_STARTUP_DELAY_SEC` (default 30) — waits before TopstepX auth on container start. Lets the previous container's SignalR session be torn down on TopstepX's side before we connect, eliminating the "Multiple sessions detected" race kick. Safe to lower to 10-15s if you control all sessions.

`/api/stocks/runtime-status` reports current position, halt reason, session PnL.
`POST /api/stocks/halt?flatten=true` panic stops + flattens.
`POST /api/stocks/resume` clears the pause flag.

### Stocks — Trade Lifecycle (Phase 1 / Phase 2 state machine, added 2026-05-09)

The autonomous broker runs a deterministic two-phase state machine. **Spec at [docs/superpowers/specs/2026-05-09-phase1-phase2-mechanics-design.md](docs/superpowers/specs/2026-05-09-phase1-phase2-mechanics-design.md), plan at [docs/superpowers/plans/2026-05-09-phase1-phase2-mechanics.md](docs/superpowers/plans/2026-05-09-phase1-phase2-mechanics.md). Read those before changing live trade dispatch.**

**Tracker.phase** ([backend/src/broker/position_tracker.py](backend/src/broker/position_tracker.py)):
- `0` = flat
- `1` = sacred bracket — pre-1.5R, no DQN re-eval / trail / pyramid / flip
- `2` = zone-driven ride — post-1.5R, BE-lock fired (`tracker.locked_BE=True`)

**Module-level helpers in [backend/src/market_data/level_monitor.py](backend/src/market_data/level_monitor.py)** (top of file, lines ~24-130):
- `_conf_floor()` / `_of_floor()` — env-var-aware entry floors. Reckless (paper, default) = 0.0/0.0; strict (real money) = 0.15/0.30
- `MIN_ENTRY_STOP_TICKS = 6.0` / `MAX_ENTRY_STOP_TICKS = 40.0` / `_stop_ticks_in_bounds()` — sanity bound; rejects nonsense-stop trades the trainer can't learn from
- `PHASE_2_THRESHOLD_R = 1.5` — must match `BE_LOCK_R` in broker_adapter.py
- `PHASE_2_BASE_SIZE = 1` / `_pyramid_add_size(conf)` — confidence-scaled pyramid; pyramid_decision head's add_size is IGNORED in live
- `_is_phase2_rev_opposite(result, tr, approach)` — gates fall-through to broker.on_signal for REV-flip
- `_should_run_phase2_handlers(tr)` — wraps the entire in-position handler; Phase 1 result → None unconditionally
- `_reversal_signals_active()` / `_early_exit_lock_active()` — both default OFF; set `ENABLE_PER_TICK_REVERSAL=1` / `ENABLE_EARLY_EXIT_LOCK=1` to opt back in. **Per spec, Phase 2 decisions are zone-driven only.**

**Entry gate stack** (FLAT only, applied in order; first failure wins): `halted` → `action != SKIP` → `confidence ≥ _conf_floor()` → `of_score ≥ _of_floor()` → `_stop_ticks_in_bounds(stop_ticks)` → `is_flat`. The `_build_inference_gates` UI dict and the broker dispatch path read from the SAME helpers, so the UI can never lie about what the broker actually did.

**Sizing — confidence-scaled at every stage**, via `src.rl.confidence.size_multiplier(composite_confidence)` × `BASE_SIZE`, floored at 1 contract. Tiers: `≥0.85→1.5`, `0.70-0.85→1.0`, `0.50-0.70→0.6`, `0.30-0.50→0.3`, `<0.30 reckless→0.5`. With `BASE_SIZE=1`, only `conf ≥ 0.85` produces 2 contracts; everything else floors to 1. Applies to: Phase 1 entry size (`_execute_entry`), Phase 2 pyramid add size, Phase 2 REV-flip fresh entry size. **Don't bring back the size_model.predict path** — `size_model_v5.joblib` stays in the model pool but is no longer called from live.

**Phase 1 → Phase 2 transition (BE-lock):** when `peak_R` first crosses 1.5, `broker_adapter.update_mark_and_check_be_lock` moves stop to `entry ± 2 ticks` ($10/contract — covers spread + commission with buffer) and sets `tracker.locked_BE=True`. Single-shot via the flag. **This is the "barely profitable" point** the user spec'd — worst case the trade closes flat-plus-pennies, never below break-even.

**Phase 2 dispatch on next zone touch** (gated by `_should_run_phase2_handlers`):
- `action == CONTINUATION` → cont-trail (skipped if of_score < 0.3) AND pyramid (these COEXIST after Task 12; previously they were mutually-exclusive elif branches)
- `action == REVERSAL` opposite to current side → fall through to `broker.on_signal` which calls `flatten("flip_on_reversal")` then `_execute_entry` for the opposite direction. Two distinct `broker_trades` rows. New position enters Phase 1.
- `action == REVERSAL` same side → suppressed (e.g., long position + REVERSAL at down-approach implies wanting long = same side)
- `action == SKIP` → hold

**Action strings — exact match required.** DQN emits `Action.CONTINUATION.name = "CONTINUATION"` and `Action.REVERSAL.name = "REVERSAL"`. Branches checking `"CONT"` or `"REV"` are dead code (this exact bug shipped in Task 10's first pass and was caught during Task 11 review).

**Trail bug context (resolved in commit `d783180a`):** TopstepX's `SubscribeContractTrades` is silent by design — every third-party ProjectX integration (Go runbook, Python tsxapi4py, TypeScript topstepx-api) treats `GatewayQuote` as the primary price feed. Without `on_quote` wired, `peak_R` never advances and Phase 2 is structurally unreachable. The fix wires `stream.on_quote` to `adapter.update_mark_and_check_be_lock(lastPrice or (bestBid+bestAsk)/2)` in [server_bootstrap.py](backend/src/stocks/server_bootstrap.py). Don't remove this wiring. See [project_trail_bug_root_cause_resolved.md memory](C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\project_trail_bug_root_cause_resolved.md).

**TopstepX API quirks (added in commits `d25eadcc`, `89d4a206`, `09c4da1d`, `4de32117`):**
- `Auth/validate` response field is `newToken`, NOT `token` — asymmetric with `Auth/loginKey`. Reading the wrong field forces silent full re-auth every cycle.
- User hub subscriptions: `SubscribeAccounts` (canTrade flip detection) + `SubscribePositions` + `SubscribeOrders` + `SubscribeTrades`. **All four required** — without `SubscribeAccounts` we don't know about prop-firm violations until the next `Order/place` rejects.
- `GatewayUserAccount.canTrade=false` → broker halts + flattens any open position via `adapter.flatten("account_violation")`.
- Startup contract verification via `/api/Contract/available` — logs CRITICAL if `TOPSTEPX_CONTRACT_ID` is no longer active. **NQ rolls quarterly: M26 → U26 on 2026-06-15.**
- See [project_topstepx_api_subscription.md memory](C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\project_topstepx_api_subscription.md) for billing quirks (separate $14.50/mo with code `topstep`; cancellation revokes API access immediately; weekend maintenance returns errorCode 3 indistinguishable from a revoked key).

**Deferred follow-ups** (not yet implemented):
- Bracket orders — `/api/Order/place` accepts `stopLossBracket` + `takeProfitBracket` for atomic OCO leg attachment. Would eliminate the entry-fill race entirely. Larger refactor of `_execute_entry`.
- Pre-existing `test_broker_adapter.py` failures (3 tests) — stale `max_daily_loss` default, wrong stop type constant, positional-arg `modify_order`. Real bugs but unrelated to the Phase 1/2 work.

**Don't touch unless you understand the spec:**
- The `_should_run_phase2_handlers` wrap. Removing it puts Phase 2 logic back into Phase 1, which means underwater pyramids and chopped winners.
- The `FORCE_REV_ONLY` flag in `session_manager.py`. That's the BACKTEST class, not live. Live path was un-forced in commit `9a9dccc5` (2026-04-28). If you see code referring to `FORCE_REV_ONLY=True` affecting live trades, it's wrong — that flag only matters in `SessionManager` which is the simulator.

## Arnold — Local Client

**Run `arnold.bat` (repo root) to start.** Opens SSH tunnel to server API + local FastAPI + Playwright browser + TopstepX relay (unless `STOCKS_AUTONOMOUS=true`).

### How It Works
1. SSH tunnel to server API (port 18000 → Docker backend:8000)
2. Local FastAPI (port 8000): proxies `/api/*` through the tunnel, mounts `/mirror/*` for browser control, mounts `/stocks/*` for the TopstepX dashboard + `/stocks/ws/dashboard` WebSocket, and serves the React SPA at `/`
3. React frontend: Sports, Stocks, Bankroll, Stats — all in one app with per-tab `ErrorBoundary`
4. Playwright browser: headed Chromium for bet placement on provider sites
5. TopstepX client + SignalR stream + `SignalRelayClient` connect as asyncio tasks (`arnold/stocks_runtime.py`); a heartbeat supervisor restarts either one if their forever-loop ever exits unexpectedly
6. SignalRelay forwards ticks to server `/ws/signals` with `X-API-Key` auth; a bounded outbox (`_OUTBOX_MAX=2000`) buffers messages across brief disconnects so ticks/fills aren't silently dropped during the 5 s reconnect window

### Play Workflow (HIGH-LEVEL)
1. Select a funded provider (amber highlight)
2. Click Start → opens provider site in Playwright browser
3. Log in on the Playwright browser → detected via DOM balance scrape → green highlight
4. PlayLoop auto-navigates to bets, auto-fills stakes
5. User confirms Place/Skip for each bet
6. Bets recorded to server DB via API proxy

### Mirror Workflow (IMPORTANT — all providers follow this)

**Canonical reference: [`docs/mirror-workflow.md`](docs/mirror-workflow.md)** — full checklist, per-platform details, capability matrix, troubleshooting.

Every provider follows the same state machine. No exceptions:

```
IDLE → OPENING → LOGIN_WAITING → SETTLING → NAVIGATING → READY → PLACING → back to NAVIGATING or IDLE
```

**The 8-step checklist (summary):**
1. **Wire interception** — balance/history/placement URL patterns in `browser.py`
2. **Open site & await login** — `find_tab()` → `check_login()` (120s timeout)
3. **Sync balance** — interceptor → workflow API → DOM scrape → `POST /api/bankroll/set/{provider_id}`
4. **Settle pending** — `sync_history()` → 3-tier fuzzy match → broadcast for user review → record to DB. **Settlement MUST complete before placing any bet.**
5. **Navigate** — pop highest-edge bet from cluster queue → `navigate_to_event()`
6. **Sync odds & confirm edge** — `prep_betslip()` → `check_live_price()` → auto-skip if -EV
7. **Await place & intercept** — user clicks Place on site → interceptor catches → `POST /api/bets`
8. **Move to pending** — bet recorded, PendingLoop picks up for future settlement → next bet

**Key rules:**
- Cluster deduplication: siblings share odds, one bet blocks all (`play_loop.py:_CLUSTER_MEMBERS`)
- Daily cap: 10/day per soft provider (uncapped: pinnacle, polymarket, cloudbet)
- Provider history is source of truth — unknown bets recorded to DB during settlement

**Mirror invariants (read before touching ANY workflow):**

| Rule | Where | Why |
|---|---|---|
| Only `navigate_to_event` is auto. Everything else passive — no `page.goto` in `sync_history`, `check_login`, `sync_balance`, settle flows | All workflows | User owns navigation outside arb event-click; auto-nav clobbered open pages |
| **PendingLoop polling is DELETED.** Recovery is reactive — user navigates to history → `history_intercepted` → `_reactive_history_sync(pid)` → workflow.sync_history → reconcile + `_record_unknown_open_bets` | `router.py` `_on_browser_event` | The user explicitly drives navigation; polling was clobbering pages |
| History status parser MUST map every open-state variant (`"open"`, `"pending"`, `"active"`, `"accepted"`, `"placed"`, `"running"`, `"0"`, `""`) to `"pending"`. Returning None silently drops every open bet | Each workflow's `_parse_history_entry` | 6 Altenar tenants had this bug; spelklubben's coupon-history shipped null couponStatus |
| `_record_unknown_open_bets` dedup: provider_bet_id first → (odds, stake) signature as Counter (count-based) → track inserts within the same call so paginated history doesn't double-insert | `pending_loop.py:_record_unknown_open_bets` | Betinia returned same bet × 5 pages → 5 dups |
| `_record_manual_bet` NEVER falls back to planned/request stake. If response doesn't expose actual_stake → emit `bet_record_deferred` SSE and defer to reactive sync | `play_loop.py:_record_manual_bet` | Bookmakers stake-limit; request body still carries requested amount |
| `_record_manual_bet` dedup keyed on `(provider_id, parsed_bet_id_or_body_hash)` 60s TTL — same intercept can fire twice (req + resp halves) | `play_loop.on_bet_intercepted` | Polymarket × 4 dup spam |
| Live-odds debounce lives SYNCHRONOUSLY in the SSE callback (not inside the async task it spawns) | `router.py` `history_intercepted` handler | Concurrent intercepts all read same stale timestamp before any wrote |
| Workflows reach the active browser via module-level `get_active_browser()`. Never attach attributes to `page.context` — Playwright proxies may strip them | `browser.py:_ACTIVE_BROWSER` | Gecko V2 sync_history reads `provider_data[pid]['coupon_history_by_url']` cache populated by interceptor |
| DOM-scrape live prices must match by TEAM NAME, not column index. Pass `display_home`/`display_away` into the JS, match by full name + surname, fall back to index | `workflows/altenar.py:read_outcome_odds_dom` | UFC: scanner says Allen=away but Betinia shows Allen first → idx=1 returned Costa price |
| Bets without an Event row use `bet.boost_event` for the free-text event name. `/api/opportunities/play/pending-bets` surfaces it as `event_name`; UI falls back to that when `home_team`/`away_team` are null | `pending_loop._record_unknown_open_bets` + `opportunities.get_pending_bets` | Manually-recovered bets had blank rows otherwise |
| Frontend pending row contract: BOTH soft-cluster + unlimited-cluster render sites in `PlayPage.tsx` must show event_name fallback, placed time, starts time + countdown (ttkClass), live/ready-to-settle pills | `PlayPage.tsx` ~2879 + ~3690 | Two divergent renders existed; unified 2026-05-12 |
| Global event+market blacklist for arb table — derive `placedEventMarketKeys` from `pendingByProvider`, normalise `1x2 ↔ moneyline`, filter `opps` | `PlayPage.tsx` subTab === 'arb' block | Same arb resurfaced after placement; different markets on same event stay visible |

**Polymarket CLOB caveat:** order placement frequently bypasses HTTP intercept (WebSocket or unintercepted paths). The reliable capture is reactive sync via `data-api.polymarket.com/positions` interception when the user navigates to `/portfolio?tab=positions`. Always nav there after placing.

### Key Files
```
arnold/
├── launch.py              # SSH tunnel + uvicorn + browser open (+ zombie-tunnel watchdog)
├── server.py              # FastAPI: /mirror router + /stocks router + /api proxy + static
├── proxy.py               # /api/* reverse proxy through the SSH tunnel
├── stocks_runtime.py      # TopstepX client + stream + relay + heartbeat supervisor
├── mirror/
│   ├── browser.py         # Playwright lifecycle + network interception
│   ├── play_loop.py       # Automated betting state machine (value + arb coordination)
│   ├── arb_runner.py      # Arbitrage runner for soft books (anchor + auto-hedge)
│   ├── pending_loop.py    # Settlement sync loop (short-circuits when browser isn't up)
│   ├── router.py          # /mirror/* endpoints
│   ├── sse.py             # Local SSE broadcaster
│   └── workflows/         # Provider DOM automation
├── frontend/              # Single React app — sports + stocks in one bundle
└── data/                  # local cache (tunnel lock file, etc.)

arnold.bat                 # Windows launcher at repo root — invokes arnold/launch.py
```

### Frontend (IMPORTANT)
- **`arnold/frontend/`** is the only frontend. Sports play/bankroll/stats live under `src/pages/`, stocks under `src/pages/stocks/`. A single `useDashboardWS` instance at the App root keeps the stocks websocket alive regardless of active tab.
- **The server has no frontend.** It's API-only. All betting/trading UI lives in `arnold/frontend/`.
- Any legacy `arnoldsports/` or `arnoldstocks/` path you see in docs or code is stale — the merge landed 2026-04-24 (commit `9a9dccc5`).

## WHY It's Structured This Way

- **Provider extractors are isolated** - Each bookmaker has unique API/DOM structure
- **Sharp sources separate** - Pinnacle provides "fair odds" baseline (Polymarket for event matching only)
- **Matching layer abstracts providers** - Fuzzy matching normalizes "Real Madrid CF" → canonical event
- **Analysis is provider-agnostic** - Works on normalized events/odds
- **Repositories abstract DB access** - All queries go through repo classes, not raw `session.query()` in routes/services
- **Services coordinate business logic** - Routes are thin HTTP handlers, services own the logic
- **`db/models.py` is ORM-only** - No helper functions, no business logic — just model definitions and DB init

## Performance Philosophy (IMPORTANT)
**Make sure the PC is the bottleneck, not the code.** Always optimize code paths so hardware limits are what caps performance — not inefficient algorithms, blocking I/O, unnecessary allocations, redundant DB queries, or event loop starvation. Profile before guessing. Batch where possible. Offload blocking work to threads. Keep the async event loop free.

## HOW To Work In This Codebase

### Commands
```bash
# Production (on server via SSH):
ssh root@148.251.40.251 "cd /opt/arnold && curl -X POST 'http://localhost:8000/api/extraction/run?providers=pinnacle'"

# Local dev (only if needed — production runs on server):
cd backend && python run_dev.py   # Starts uvicorn on localhost:8000

# Tests (local):
cd backend && pytest tests/
```

**Production runs on the Hetzner server.** Local dev is optional — only for writing/testing code before pushing.

### Key Domain Concepts
- **Fair odds**: True probability from Pinnacle (after devigging)
- **Edge %**: `(provider_odds / fair_odds - 1) × 100`
- **Value bet**: Single outcome with positive edge
- **Sharp source**: Pinnacle ONLY (Polymarket is NOT used as sharp)

### Extraction Scope
**We extract 1x2/moneyline, spread, and total markets. All other markets are skipped.**

- **Markets extracted**: `1x2`, `moneyline` (match winner), `spread` (handicap), `total` (over/under)
- **Spread/total**: Pinnacle ships mainline + alternate handicaps so kalshi/cloudbet/polymarket ladder lines have a sharp comparison baseline (period 0 + 6, esports map markets stay mainline-only); Kambi mainline only (betOfferType 6/7). Scanner groups spreads by **line** (home-team handicap) — `home@P` and `away@-P` co-locate at key `spread_P` — so each handicap is independently scanned and opposite-favorite alternate lines (e.g. Polymarket's `Spread: Thunder (-3.5)` vs Pinnacle's `home -3.5`) never merge into one comparison. Totals stay keyed by raw point. See [`backend/src/analysis/scanner.py`](backend/src/analysis/scanner.py) `group_odds`.
- **Markets skipped**: props, player markets, corners, cards, correct score, etc.
- **Live events**: Skipped entirely - only pre-match odds
- **Whitelist enforced in**: `constants.py` via `ALLOWED_MARKETS` (imported by `pipeline/storage.py`)

## Configuration

- `src/config/providers.yaml` - **Single source of truth** for all provider config: endpoints, types, bonuses, active list, extraction tiers, orchestrator settings. Always read this file for current provider state — never hardcode provider lists elsewhere.
- `src/config/sports.yaml` - Sport/league mappings with provider-specific IDs
- PostgreSQL database in Docker (queryable via postgres MCP)

## When Working Here

- Provider APIs return JSON - no HTML scraping needed for most
- Playwright only for DOM-based providers (Spectate, ComeOn, Hajper)
- Rate limits enforced via circuit breaker in orchestrator
- Event matching uses `rapidfuzz` for team name normalization
- Shared constants in `constants.py` (ALLOWED_MARKETS, SHARP_PROVIDERS)

### Code Cleanup Rule (IMPORTANT)
**If you find any redundant code handling markets other than 1x2/moneyline/spread/total, remove it immediately.**

We only support 1x2, moneyline, spread, and total markets. Any code for props, player markets, corners, cards, correct score, etc. is dead code and should be deleted. Keep the codebase lean - delete, don't comment out.

## Pipeline Data Flow

```
Provider API → StandardEvent
    ↓
normalize_team_name() + normalize_market()
    ↓
_resolve_event_id() → exact match / fuzzy match / swapped-team fallback
    ↓
store_provider_event() → Event + Odds (via OddsBatchProcessor)
    ↓
detect_and_fix_inversion() → swap if needed (cached sharp odds)
    ↓
OpportunityScanner.scan_value() → pre-computed Pinnacle dict + soft prob sums
```

### Extraction Tiers

Configured in `providers.yaml` under `extraction_scheduling`. Each provider runs independently (`grouped: false` except where noted). The **cycle time = run duration + cooldown interval** — so a provider taking 300s with a 2-min cooldown runs every ~420s, not every 120s.

| Trigger | Cooldown | Providers | Typical Run Duration |
|---------|----------|-----------|---------------------|
| `sharp` | 1 min | pinnacle | ~130s |
| `polymarket` | 5 min | polymarket | ~200s |
| `api_soft` | 2 min | unibet, betinia, betsson, bethard, spelklubben, vbet | ~300s |
| `browser_soft` | 10 min | 888sport, 10bet, tipwin | ~400-1000s |
| `browser_antibot` | 25 min | comeon, rainbet | ~700-1700s |
| `signal_international` | 5 min | stake, cloudbet, marathon | ~16-340s |

**Rainbet (Betby tenant, added 2026-05-10):** lives in `browser_antibot` alongside ComeOn but uses **patchright** (Chromium with cross-origin-iframe-click patches), not Camoufox. ComeOn uses Camoufox (Imperva-protected). Rainbet uses patchright (Cloudflare Turnstile) — the two anti-bot stacks differ enough that one pattern doesn't cover both. The retriever launches its own patchright via `--disable-http2 --disable-quic --no-locale --no-geo` (NOT through `BrowserTransport`, whose default args trip Turnstile re-challenges). Spec at [docs/superpowers/specs/2026-05-10-rainbet-provider-design.md](docs/superpowers/specs/2026-05-10-rainbet-provider-design.md), discovery doc at [docs/superpowers/research/2026-05-10-rainbet-discovery.md](docs/superpowers/research/2026-05-10-rainbet-discovery.md). When debugging Turnstile-clear timeouts, the exit signal is `sptpub_hits > 0` (any response from `*.sptpub.com`), NOT cookie+iframe state — the iframe persists in the DOM after the SPA bootstraps.

### Pinnacle Match Rate (Key Health Metric)

The primary extraction quality metric is **how many soft provider events match against Pinnacle events**. The extraction report flags match rates automatically:
- `!` = Critical: failed providers, 0 events, match rate < 30%
- `~` = Warning: missing markets, slow extraction, rate limits

**Use the postgres MCP to query `extraction_runs`, `provider_run_metrics`, and `sport_run_metrics` for extraction health.**

**If match rate drops:** check `sports.yaml` aliases, team name normalization, timezone date offset.

### Scanner Quality Filters
- `MIN_VALID_PROB_SUM = 0.90` - Filter incomplete markets
- `MAX_ODDS_RATIO = 1.35` - Filter event mismatches (fuzzy matching false positives)

## Extraction Health Checklist

**After extraction runs, query these via postgres MCP (tables: `extraction_runs`, `provider_run_metrics`, `sport_run_metrics`).**

Check in order of severity:
- Failed providers (`status != 'success'`, error messages, 0 events)
- Match rate drops (`events_unmatched / events_processed` ratio increasing)
- Missing market types (`spread_count=0` or `total_count=0`)
- Timing regressions (duration significantly higher than baseline)
- Sport-level gaps (sports with 0 events or 0 matches)
- Opportunity yield (query `opportunities` table)

Record findings in `backend/docs/provider_performance.md`.

## Specials / Odds Boosts Pipeline

**Separate from regular extraction** — different data models, schedules, no shared lock. Boosts run on their own 120-minute scheduler tier.

```
scrape_specials.scrape_all()  →  Special dataclass list
    ↓
save_specials()               →  JSON backup (data/specials.json)
    ↓
filter_expired()              →  Remove started/expired events
    ↓
enrich_specials_with_ev()     →  Match vs Pinnacle fair odds → edge_pct, fair_odds, is_positive_ev
    ↓
store_specials_to_db()        →  Full replace into `specials` table (DELETE all + INSERT)
```

**EV enrichment runs at scrape time, not at query time.** The GET /api/specials endpoint reads pre-computed data from DB.

EV logic (`src/analysis/ev_enrichment.py`):
- Only 1x2/moneyline boosts can be EV-analyzed (combos/props filtered via PROP_KEYWORDS)
- Matches boost event name against Pinnacle events using normalized team names
- De-vigs Pinnacle odds (multiplicative method) to get fair odds
- `edge_pct = (boosted_odds / fair_odds - 1) * 100`
- Sanity check: edge > 100% = wrong match, skip

## Workflow Automation

**Use plugins/skills to automate the development loop:**

- **New features / providers**: `/brainstorm` → `/write-plan` → `/execute-plan` (superpowers)
- **Debugging extraction**: `systematic-debugging` triggers automatically for root cause investigation
- **Shipping**: `/commit-push-pr` (commit-commands) → `/code-review` (posts review comment on PR)
- **Code review** runs 5 parallel agents checking: CLAUDE.md compliance, bugs, git history, previous PRs, code comments. Only issues scoring 80+ confidence are posted.
- **Deploying**: `/deploy` — guided deploy with lock coordination + health verification
- **Server monitoring**: `/server-health` — quick production status (containers, extraction, DB, disk)
- **Extraction monitoring**: Scheduled remote agent checks `/health/extraction` every 3h, commits alert on WARNING/CRITICAL
- **Frontend changes**: Use Claude Preview (`preview_start`, `preview_screenshot`) to verify UI
- **DB queries**: Use postgres MCP directly — no Python scripts needed
- **Multi-file sweeps**: `/ralph-loop` for repetitive changes across many files
- **Docs lookup**: context7 MCP for FastAPI, SQLAlchemy, Playwright, rapidfuzz docs
- **Auto-formatting**: PostToolUse hooks auto-run `ruff` on `.py` files and `eslint --fix` on `.ts/.tsx` files after every Edit/Write
- **CI linting**: GitHub Actions runs `ruff check` + `ruff format --check` + `npm run lint` on every push/PR
