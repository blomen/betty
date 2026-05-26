# Arnold - Betting Analytics Platform

## WHAT This Project Is

Arnold compares odds across 40+ sportsbooks against sharp sources (Pinnacle) to find value bets.

**Tech stack:** Python 3.12+ / FastAPI / PostgreSQL / Docker / Playwright | React 19 / TypeScript / Vite / Tailwind

## Two Programs

| Program | Where it runs | What it does | How to start |
|---------|--------------|--------------|--------------|
| **Server** | Hetzner 24/7 | Headless data engine: extraction, analysis, DB, API | `docker compose up -d` |
| **Arnold (local)** | Your PC | Betting client: Sports, Bankroll, Stats, Playwright mirror | `arnold.bat` |

**Server** is a pure compute/data engine — no UI. Extraction, analysis, opportunity scanning live here.

**Arnold (local)** is one FastAPI process + one React SPA. Tabs: **Sports** (unified arb + value bet play), **Bankroll** (provider balances + Kelly sizing), **Stats** (historical bet performance). The launcher opens an SSH tunnel to the server API, starts the local FastAPI (which reverse-proxies `/api/*` to the tunnel and mounts `/mirror/*` for the Playwright browser control), and then opens the browser.

## Architecture

```
Hetzner Server (24/7, headless)              Your PC
├── backend/src/                             ├── local/                # Local client
│   ├── providers/    # 16 extractors        │   ├── server.py         # FastAPI: /api proxy + /mirror + static
│   ├── pipeline/     # orchestrator         │   ├── launch.py         # SSH tunnel + uvicorn + browser open
│   ├── analysis/     # scanner, devig       │   ├── proxy.py          # /api/* reverse-proxy to server via tunnel
│   ├── matching/     # Fuzzy matching       │   └── mirror/
│   ├── bankroll/     # Kelly sizing         │       ├── browser.py    # Playwright lifecycle + interception
│   ├── api/          # FastAPI              │       ├── play_loop.py  # Automated betting state machine
│   └── db/           # PostgreSQL ORM       │       ├── arb_runner.py
└── docker-compose.yml                       │       ├── pending_loop.py
                                             │       └── workflows/    # Provider DOM automation
                                             ├── frontend/             # React app (Vite + TS)
                                             │   └── src/pages/
                                             │       ├── PlayPage.tsx       (Sports tab)
                                             │       ├── BankrollPage.tsx
                                             │       └── StatsPage.tsx
                                             │
                                             └── arnold.bat  → SSH tunnel → server API (port 18000)
```

### Frontend

Single app at `frontend/`. Tabs:

| Tab | Sub-tabs | What it shows |
|-----|----------|---------------|
| **Sports** | Value Bets, Arbitrage | Unified betting view — value vs. Pinnacle, arb across soft books |
| **Bankroll** | — | Provider balances + Kelly sizing |
| **Stats** | — | Historical bet performance |

Each top-level tab is wrapped in its own `ErrorBoundary` so one tab's crash can't bring down the others.

**The server is API-only — no visual UI.** All betting happens through the local client.

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
- **CORS lockdown** — origins from `CORS_ORIGINS` env var, explicit methods/headers only
- `/health/*` endpoints exempt from auth (nginx `location /health` block with `auth_basic off`)

### Database
- **Main DB**: `postgresql://arnold:${DB_PASSWORD}@postgres:5432/arnold` (events, odds, bets, profiles, opportunities)

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
- **Stage 2** (Python): pip install cached by `pyproject.toml` layer → code-only changes skip pip rebuild
- **Auto-cleanup**: `docker image prune` runs after each rebuild to prevent disk bloat
- Code-only rebuilds take ~30s (cached deps). Full rebuilds (pyproject.toml change) take ~2min.

### Health Endpoints (Public, No Auth)
- `GET /health` — basic status, boot_id, uptime
- `GET /health/live` — liveness probe
- `GET /health/ready` — readiness probe (DB connectivity, provider count)
- `GET /health/extraction` — extraction pipeline health: last 3 runs, failed providers, match rates, issues

### Multi-Agent Coordination (IMPORTANT)

Multiple Claude Code agents may work on this repo concurrently. **Follow these rules to avoid conflicts:**

1. **Always check server status before deploying**: Run `server-deploy.sh status` first. Note: status only shows "active deploy" if `STATUS_FILE` is present — it does NOT detect a wedged-but-still-running script. To see whether the lockfile is actually held, also run `ssh root@148.251.40.251 "pgrep -fa 'server-deploy.sh' && lsof /opt/arnold/.deploy.lock 2>/dev/null"`. A `pgrep` hit means the slot is still in use.
2. **Never run raw `docker compose up/restart/build`** — always use `scripts/server-deploy.sh` which acquires an exclusive `flock`. A PreToolUse hook blocks raw docker compose commands.
3. **Read-only operations are safe concurrently**: logs, status, DB queries, extraction logs.
4. **Destructive operations are serialized by the lock**: rebuild, restart. **`git pull` outside the script is NOT lock-protected** — never run `cd /opt/arnold && git pull` manually. Use `bash server-deploy.sh pull` if you need to advance the server's working tree without rebuilding. Manual `git pull` followed by a cached rebuild creates source-vs-image drift: HEAD advances but the docker `COPY backend/` layer stays cached, so the new code is on disk but not in the running container.
5. **If the lock is held**, wait and retry — don't bypass it.
6. **Coordinate git pushes**: Before pushing + deploying, run `git fetch && git log HEAD..origin/main --oneline` to see what other agents pushed since you forked, and `git log origin/main..HEAD --oneline` to confirm your push is a clean fast-forward. If origin is ahead, rebase or merge before pushing — don't force-push.
7. **Use `/deploy` skill** for guided deployment with health verification.
8. **Use `/server-health` skill** for quick production status checks.
9. **Deploy cooldown enforced**: 5-minute minimum between rebuilds — each rebuild kills extraction for 5-10 min. Batch changes and deploy once, don't rebuild per commit.
10. **Health verification**: Deploy script waits up to 2 min for `/health` to respond after rebuild. If it fails, deploy exits non-zero — investigate before retrying.
11. **Container watchdog**: Cron checks every 5 min and auto-restarts if backend is down.
12. **Verify the running container actually has your code**: docker build cache + cached `COPY backend/ backend/` layers can ship an image whose source predates the latest `git pull`. After every rebuild, confirm:
    - `ssh root@148.251.40.251 "cd /opt/arnold && git rev-parse HEAD"` — server's git HEAD
    - `ssh root@148.251.40.251 "curl -sf http://localhost:8000/health"` — note the `boot_id` (changes on every container restart)
    - `ssh root@148.251.40.251 "cd /opt/arnold && docker compose ps backend --format json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get(\"CreatedAt\"))'"` — container creation time should be after your deploy completed
    - If git HEAD is ahead of what your deploy pulled (e.g. another agent pushed mid-deploy), the running container is stale — re-deploy with `--no-cache` or wait for the next pull cycle.
13. **Backend deploys vs frontend/local-client changes**: a commit touching ONLY `frontend/`, `local/mirror/`, `local/server.py`, `local/launch.py`, or `local/proxy.py` is **local-client only** and ships via `arnold.bat` (Vite + local FastAPI) — do NOT trigger a backend rebuild for these. Quick check: `git diff --name-only origin/main...HEAD | grep -vE '^(local|frontend)/' | head -1` — if empty, no backend deploy needed.
14. **Background-deploy etiquette**: when running deploys via `Bash run_in_background=true` and SSH, the remote bash survives if you cancel the local task — always `pgrep -fa 'server-deploy.sh'` on the server BEFORE assuming the slot is free.

### Currencies (READ BEFORE ANY CROSS-PROVIDER MATH)

**Providers run in DIFFERENT currencies. Never add, subtract, compare, or hedge-size across providers without converting first.**

| Currency | Providers |
|---|---|
| **USDC** | polymarket |
| **USD** | kalshi |
| **SEK** | every Swedish / EU softbook this user has: betinia, betsson, bethard, campobet, coolbet, dbet, leovegas, pinnacle (this account is SEK-funded), quickcasino, spelklubben, tipwin, unibet, vbet, 10bet, 888sport, comeon, hajper, marathon, rainbet, stake, cloudbet |

The `bets.currency` column is authoritative — query it (`SELECT provider_id, currency, COUNT(*) FROM bets GROUP BY 1,2`) when in doubt.

**The rule:**
- **In code:** currency conversion is implemented inline in `backend/src/{services,bankroll,repositories}` — look for `exchange_rate_sek`, `to_sek`, and `convert` functions. The old shared `money/` package was removed during the trading strip; do not look for it. Never write `stake_a + stake_b` across providers as raw floats — always convert to one base currency first.
- **In SQL analysis:** wrap with `CASE WHEN currency='SEK' THEN x/<sek_per_usd> WHEN currency IN ('USD','USDC') THEN x END` BEFORE any cross-provider `SUM`/`MIN`/`MAX`. A `MIN(stake * odds)` across legs without conversion is meaningless.
- **For arb checks:** worst-case payout = `MIN(stake_i × odds_i)` in **one base currency**, total stake = `SUM(stake_i)` in the **same base currency**. An arb is "guaranteed" iff `worst_payout_base ≥ total_stake_base`.
- **For bankroll / Kelly / stats / ROI:** same rule. Aggregate views over mixed-currency bets that don't convert are wrong by construction.

**The first hypothesis when a sizing/hedge/bankroll number looks off by 5-10×** is "did I mix currencies?" — not "the sizer is broken."

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
- Container watchdog cron (every 5 min, auto-restarts if backend is down)
- Daily PostgreSQL backup at 3 AM UTC (`docker/pg-backup.sh`)

### Memory Budget (IMPORTANT — OOM killed the server on 2026-04-12)
64 GB total, partitioned via Docker `mem_limit` to prevent kernel OOM:
- **Postgres**: 12 GB cap (shared_buffers=4GB + work_mem + OS cache)
- **Backend**: 48 GB cap (Python + Playwright browsers)
- **OS/SSH/kernel**: ~4 GB remaining

If the backend exceeds 48 GB, Docker kills the **container** (not the kernel) and `restart: unless-stopped` brings it back. Without these limits, the OOM killer takes down SSH and requires a Hetzner Robot hard reset.

## Arnold — Local Client

**Run `arnold.bat` (repo root) to start.** Opens SSH tunnel to server API + local FastAPI + Playwright browser.

### How It Works
1. SSH tunnel to server API (port 18000 → Docker backend:8000)
2. Local FastAPI (port 8000): proxies `/api/*` through the tunnel, mounts `/mirror/*` for browser control, serves the React SPA at `/`
3. React frontend: Sports, Bankroll, Stats — all in one app with per-tab `ErrorBoundary`
4. Playwright browser: headed Chromium for bet placement on provider sites

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
local/
├── launch.py              # SSH tunnel + uvicorn + browser open (+ zombie-tunnel watchdog)
├── server.py              # FastAPI: /mirror router + /api proxy + static
├── proxy.py               # /api/* reverse proxy through the SSH tunnel
├── mirror/
│   ├── browser.py         # Playwright lifecycle + network interception
│   ├── play_loop.py       # Automated betting state machine (value + arb coordination)
│   ├── arb_runner.py      # Arbitrage runner for soft books (anchor + auto-hedge)
│   ├── pending_loop.py    # Settlement sync loop (short-circuits when browser isn't up)
│   ├── router.py          # /mirror/* endpoints
│   ├── sse.py             # Local SSE broadcaster
│   └── workflows/         # Provider DOM automation
├── navigations/           # Playwright nav recordings (per-provider JSONs)
└── data/                  # local cache (tunnel lock file, etc.)

frontend/                  # React app (repo root) — served by local/server.py
arnold.bat                 # Windows launcher at repo root — invokes local/launch.py
```

### Frontend (IMPORTANT)
- **`frontend/`** (repo root) is the only frontend. Sports play/bankroll/stats live under `src/pages/`.
- **The server has no frontend.** It's API-only. All betting UI lives in `frontend/`.

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
- **Spread/total**: Pinnacle ships mainline + alternate handicaps so kalshi/cloudbet/polymarket/Kambi ladder lines have a sharp comparison baseline (period 0 + 6, esports map markets stay mainline-only). Kambi keeps all spread/total lines (betOfferType 1/6/7); the `MAIN_LINE`-only filter was removed 2026-05-25 so we capture Pinnacle's alternate ladder. Scanner groups by `(market, point)` so each handicap is independently scanned.
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
