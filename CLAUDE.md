# Firev - Betting Analytics Platform

## WHAT This Project Is

Firev compares odds across 40+ sportsbooks against sharp sources (Pinnacle) to find value bets.

**Tech stack:** Python 3.10+ / FastAPI / PostgreSQL / Docker / Playwright | React 19 / TypeScript / Vite / Tailwind

## Three Programs

The repo contains three independent programs sharing one codebase:

| Program | Where it runs | What it does | How to start |
|---------|--------------|--------------|--------------|
| **Server** | Hetzner 24/7 | Headless data engine: extraction, analysis, DB, API | `docker compose up -d` |
| **FirevSports** | Your PC | Local betting client: Play, Pending, Dutch, Bankroll, Stats + Playwright mirror | `firevsports/firevsports.bat` |
| **FirevStocks** | Your PC | Local trading client: Chart, DQN, Bankroll, Stats + TopstepX | `firevstocks/firevstocks.bat` |

**Server** is a pure compute/data engine — no UI for betting or trading. It runs extraction, analysis, and serves the API.

**FirevSports** is the local betting app. It connects to the server API via SSH tunnel, runs a Playwright browser for bet placement, and has its own React frontend with 5 tabs.

**FirevStocks** is the local trading app (managed by a separate agent).

## Architecture

```
Hetzner Server (24/7, headless)              Your PC
├── backend/src/                             ├── firevsports/        # Local betting client
│   ├── providers/    # 16 extractors        │   ├── server.py       # Thin FastAPI proxy + mirror
│   ├── pipeline/     # orchestrator         │   ├── mirror/         # Playwright browser + interceptor
│   ├── analysis/     # scanner, devig       │   │   ├── browser.py  # Browser lifecycle + network interception
│   ├── matching/     # Fuzzy matching       │   │   ├── play_loop.py    # Automated betting loop
│   ├── bankroll/     # Kelly sizing         │   │   ├── pending_loop.py # Settlement sync loop
│   ├── api/          # FastAPI endpoints    │   │   └── workflows/  # Provider DOM automation
│   └── db/           # PostgreSQL ORM       │   └── frontend/      # React: Play, Pending, Dutch, Bankroll, Stats
├── frontend/src/     # Server dashboard     │
│   └── pages/        # Poly, Soft, Pinnacle,├── firevstocks/        # Local trading client (separate agent)
│                     # Dutch, Bankroll, Stats│   ├── server.py
└── docker-compose.yml                       │   └── frontend/
                                             │
                                             └── SSH tunnel → server API (port 18000)
```

### Frontends

| Frontend | Location | Purpose | Served by |
|----------|----------|---------|-----------|
| **Server dashboard** | `frontend/` | Read-only: Poly, Soft, Pinnacle, Dutch, Bankroll, Stats | Nginx on server |
| **FirevSports** | `firevsports/frontend/` | Betting: Play, Pending, Dutch, Bankroll, Stats + mirror control | Local FastAPI |
| **FirevStocks** | `firevstocks/frontend/` | Trading: Chart, DQN, Bankroll, Stats | Local FastAPI |

**The server `frontend/` has NO Play tab.** All betting happens through FirevSports locally.

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

**Firev runs in production on a Hetzner server. Do NOT try to run the backend locally — it's deployed.**

### Server Details
- **Server**: Hetzner Dedicated i7-7700 (4c/8t, 64 GB RAM, 2x 256 GB SSD RAID 1), Ubuntu 24.04
- **IP**: `148.251.40.251`
- **SSH**: `ssh root@148.251.40.251`
- **App URL**: `https://148.251.40.251` (behind nginx basic auth, self-signed cert)
- **Repo on server**: `/opt/firev` (main branch)

### Docker Containers
3 containers via `docker-compose.yml`:
- `firev-backend-1` — FastAPI + uvicorn + Playwright (internal only, no public port)
- `firev-postgres-1` — PostgreSQL 16 (internal only, no public port)
- `firev-nginx-1` — Nginx reverse proxy (ports 80/443, HTTPS + basic auth)

### Security
- **Nginx basic auth** protects all routes (credentials in `nginx/.htpasswd` on server, gitignored)
- **No public ports** for backend (8000) or postgres (5432) — only reachable via Docker internal network
- **Non-root container** — backend runs as `firev` user (uid 1000), not root
- **HTTPS enforced** with TLS 1.2/1.3, HSTS, rate limiting (30 req/s per IP)
- **Security headers**: CSP, X-Frame-Options DENY, Referrer-Policy, Permissions-Policy, `server_tokens off`
- **CORS lockdown** — origins from `CORS_ORIGINS` env var (not hardcoded), explicit methods/headers only
- `/health/*` endpoints are exempted from auth (nginx `location /health` block with `auth_basic off`)
- To update the password: `ssh root@148.251.40.251 "openssl passwd -apr1 NEW_PASSWORD | xargs -I{} echo 'firev:{}' > /opt/firev/nginx/.htpasswd && cd /opt/firev && docker compose restart nginx"`

### Database
- **Main DB**: `postgresql://firev:${DB_PASSWORD}@postgres:5432/firev` (events, odds, bets, profiles, opportunities)
- **Market DB**: `postgresql://firev:${DB_PASSWORD}@postgres:5432/market` (trades, candles — high-frequency tick data)
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
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh rebuild backend"

# For config/env-only changes (restart is NOT enough for code changes — code is baked into Docker image):
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh restart backend"

# Check logs (no lock needed):
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh logs backend 30"

# Check deploy status + containers + disk:
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh status"

# Clean up old Docker images and build cache:
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh cleanup"

# Check extraction:
ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T backend cat /app/logs/extraction.log | tail -30"
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

1. **Always check server status before deploying**: Run `server-deploy.sh status` first
2. **Never run raw `docker compose up/restart/build`** — always use `scripts/server-deploy.sh` which acquires an exclusive `flock`. A PreToolUse hook blocks raw docker compose commands.
3. **Read-only operations are safe concurrently**: logs, status, DB queries, extraction logs
4. **Destructive operations are serialized by the lock**: rebuild, restart, git pull
5. **If the lock is held**, wait and retry — don't bypass it
6. **Coordinate git pushes**: If you're about to push + deploy, check `git log` on the server first to ensure no other agent pushed recently
7. **Use `/deploy` skill** for guided deployment with health verification
8. **Use `/server-health` skill** for quick production status checks
9. **Deploy cooldown enforced**: 5-minute minimum between rebuilds — each rebuild kills extraction for 5-10 min. Batch changes and deploy once, don't rebuild per commit.
10. **Health verification**: Deploy script waits up to 2 min for `/health` to respond after rebuild. If it fails, deploy exits non-zero — investigate before retrying.
11. **Container watchdog**: Cron checks every 5 min and auto-restarts if backend is down. Don't rely on manual monitoring.

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

## FirevSports — Local Betting Client

**Run `firevsports/firevsports.bat` to start.** Opens SSH tunnel to server API + local FastAPI + Playwright browser.

### How It Works
1. SSH tunnel to server API (port 18000 → Docker backend:8000)
2. Thin local FastAPI (port 8000): proxies `/api/*` to tunnel, serves frontend, controls Playwright browser
3. React frontend: Play, Pending, Dutch, Bankroll, Stats tabs
4. Playwright browser: headed Chromium for bet placement on provider sites

### Play Workflow
1. Select a funded provider (amber highlight)
2. Click Start → opens provider site in Playwright browser
3. Log in on the Playwright browser → detected via DOM balance scrape → green highlight
4. PlayLoop auto-navigates to bets, auto-fills stakes
5. User confirms Place/Skip for each bet
6. Bets recorded to server DB via API proxy

### Key Files
```
firevsports/
├── firevsports.bat       # Windows launcher
├── launch.py             # SSH tunnel + uvicorn + browser open
├── server.py             # Thin FastAPI: proxy + mirror router + static
├── proxy.py              # Reverse proxy to server tunnel
├── mirror/
│   ├── browser.py        # Playwright lifecycle + network interception
│   ├── play_loop.py      # Automated betting state machine
│   ├── pending_loop.py   # Settlement sync loop
│   ├── router.py         # /mirror/* endpoints
│   ├── sse.py            # Local SSE broadcaster
│   └── workflows/        # Provider DOM automation (copied from backend)
└── frontend/             # Dedicated React app (NOT the server frontend)
```

### Frontends (IMPORTANT — read carefully)
- **`frontend/`** — SERVER dashboard only (Poly, Soft, Pinnacle, Dutch, Bankroll, Stats). Deployed to Hetzner. NO Play tab.
- **`firevsports/frontend/`** — LOCAL betting client (Play, Pending, Dutch, Bankroll, Stats). Runs on your PC only.
- **`firevstocks/frontend/`** — LOCAL trading client (separate agent manages this).
- **Do NOT confuse them.** Changes to betting UI go in `firevsports/frontend/`, not `frontend/`.

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
ssh root@148.251.40.251 "cd /opt/firev && curl -X POST 'http://localhost:8000/api/extraction/run?providers=pinnacle'"

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
- **Spread/total**: Main lines only (`isAlternate=false` for Pinnacle, betOfferType 6/7 for Kambi)
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

### UI Uniformity Rule (IMPORTANT)
**All tab pages must follow the same UI patterns. When adding a feature to one page, apply it to all similar pages.**

Standard patterns:
- **FilterBar** with `MultiSelectDropdown` for provider/sport filtering (shared component in `FilterBar.tsx`)
- **Expanded rows** use `<select>` dropdown to pick provider + single bet button (not multiple per-provider buttons)
- **Accent colors** per tab: `tabValue` (orange) for Soft, `tabBonus` (purple) for Specials, `success` (green) for Dutch
- **Table structure**: compact `sq` class, consistent column naming (Event/Boost, Providers, Odds, Edge, etc.)
- **EV data**: show `edge_pct` (vs Pinnacle fair odds) wherever available, not just `boost_pct` (vs original odds)

Shared filter components in `frontend/src/components/Terminal/FilterBar.tsx`:
- `MultiSelectDropdown` — compact popover with checkboxes + search (for >6 options)
- `SingleSelectPills` — inline pill buttons for single-select categories
- `MultiSelectPills` — inline pills for multi-select (available, not yet used)
- `RangeFilter` — min/max number inputs (available, not yet used)

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
| `browser_soft` | 10 min | 888sport, interwetten, 10bet, tipwin | ~400-1000s |
| `browser_antibot` | 15 min | coolbet, comeon | ~700-1700s |
| `signal_international` | 5 min | stake, cloudbet, marathon | ~16-340s |

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
