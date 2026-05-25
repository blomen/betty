# Strip Trading from Arnold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert Arnold from hybrid sports-betting + NQ-futures-trading to pure sports-betting by deleting all trading code, DB tables, frontend, config, and docs.

**Architecture:** Hard cutover. Order matters — strip imports in surviving code BEFORE deleting the imported modules, otherwise `python -c "import backend.src.app"` blows up mid-strip. Each task commits independently so a partial run can be reverted.

**Tech Stack:** Python 3.10+ / FastAPI / SQLAlchemy / PostgreSQL / React / Vite / TypeScript / Docker.

**Spec:** [docs/superpowers/specs/2026-05-25-strip-trading-from-arnold-design.md](../specs/2026-05-25-strip-trading-from-arnold-design.md)

---

## File Structure

### Deletions (whole directories)
- `backend/src/{stocks,broker,rithmic,market_data,edge,rl}/`
- `backend/src/ml/{level_touch,macro}/`
- `arnold/tv_overlay/`
- `arnold/frontend/src/pages/stocks/`

### Deletions (single files)
- `backend/src/services/{trading_service,market_service}.py`
- `backend/src/db/{trading,ml}.py`
- `backend/src/api/routes/{stocks,trading,market,signals_ws,postmortem}.py`
- `backend/src/ml/{feature_store,migrations}.py`
- `backend/src/ml/training/train_all.py`
- `backend/src/ml/features/{candle_features,level_touch_features,trading_features}.py`
- `backend/src/ml/models/{level_classifier,macro_engine}.py`
- `arnold/stocks_runtime.py`
- `arnold/frontend/src/hooks/useDashboardWS.ts` (or wherever it lives)
- Trading scripts under `backend/scripts/`

### Modifications
- `backend/src/api/__init__.py` — strip ~180 trading refs (imports, lifespan tasks, router includes, helpers)
- `backend/src/api/routes/__init__.py` — drop trading router exports
- `backend/src/db/models.py` (or wherever migrations live) — drop trading model imports, add `DROP TABLE` migrations
- `backend/src/db/__init__.py` — drop `from .trading import *` / `from .ml import *`
- `backend/src/db/base.py` — no change expected
- `arnold/server.py` — drop `/stocks/*` router mount + dashboard route
- `arnold/launch.py` — drop TopstepX env + stocks startup
- `arnold/frontend/src/pages/BankrollPage.tsx` — drop Trading sub-tab
- `arnold/frontend/src/pages/StatsPage.tsx` — drop Trading sub-tab
- `arnold/frontend/src/App.tsx` (or main router file) — drop Stocks tab + ErrorBoundary
- `.env.docker` — drop trading vars
- `backend/pyproject.toml` — drop `torch`/`lightgbm`/`joblib` if unused after strip
- `scripts/server-deploy.sh` — drop `wait_for_rl_training`, open-position gate, RL `taskset`
- `CLAUDE.md` — drop trading sections
- `C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\MEMORY.md` — drop trading entries

---

## Task 1: Pre-flight — verify no live position + revoke broker

**Files:** none modified. Manual gate.

- [ ] **Step 1: User flattens any open TopstepX position**

User action on TopstepX dashboard:
- Close any open NQ position
- Set account `canTrade=false` (or pause the API key)

- [ ] **Step 2: Confirm flat via deployed server's status endpoint**

```bash
ssh root@148.251.40.251 "curl -sf http://localhost:8000/api/stocks/runtime-status | python3 -m json.tool"
```

Expected: `position` field is `null` or shows `size: 0`. Also visually confirm
on the TopstepX web dashboard.

If non-empty, halt — flatten manually first via the TopstepX UI.

- [ ] **Step 3: Create working branch**

```bash
git checkout -b strip-trading
git status
```

Expected: existing staged deletions visible (RL agent/signal files from previous work).

- [ ] **Step 4: Stash existing staged deletions for clean re-apply later**

```bash
git stash push --include-untracked -m "pre-strip-uncommitted"
```

Expected: stash created. Repo clean.

- [ ] **Step 5: Commit**

```bash
git commit --allow-empty -m "chore: begin strip-trading work (preflight)"
```

---

## Task 2: Strip api/__init__.py — remove all trading imports + lifespan tasks

**Files:**
- Modify: `backend/src/api/__init__.py`

This is the largest single edit. The file has ~180 trading refs. Strategy: open the file, locate each block, delete it. Run import smoke after.

- [ ] **Step 1: Read the file end-to-end to map all trading regions**

```bash
grep -n "stocks\|RL\|rl_\|broker\|trading\|topstepx\|level_monitor\|databento\|rithmic\|specials_router" backend/src/api/__init__.py | head -100
```

Expected: numbered list of lines. Use these line numbers to plan edits below.

- [ ] **Step 2: Remove the route-router imports**

In `backend/src/api/__init__.py`, find the `from .routes import (` block (~line 26) and remove trading-side names:

OLD (lines around 26-65):
```python
from .routes import (
    bankroll_router,
    bets_router,
    chat_router,
    events_router,
    extraction_router,
    fire_window_router,
    limits_router,
    market_router,
    metrics_router,
    mirror_router,
    mirror_state_router,
    mirror_stream_router,
    monitoring_router,
    opportunities_router,
    polymarket_router,
    postmortem_router,
    profiles_router,
    providers_router,
    risk_router,
    settings_router,
    signals_ws_router,
    slip_odds_router,
    specials_router,
    stocks_router,
    trading_router,
)
```

NEW:
```python
from .routes import (
    bankroll_router,
    bets_router,
    chat_router,
    events_router,
    extraction_router,
    fire_window_router,
    limits_router,
    metrics_router,
    mirror_router,
    mirror_state_router,
    mirror_stream_router,
    monitoring_router,
    opportunities_router,
    polymarket_router,
    profiles_router,
    providers_router,
    risk_router,
    settings_router,
    slip_odds_router,
    specials_router,
)
```

(Removed: `market_router`, `postmortem_router`, `signals_ws_router`, `stocks_router`, `trading_router`.)

- [ ] **Step 3: Remove `_build_rl_context_from_session` helper**

Find the function `def _build_rl_context_from_session(...)` (around line 92) and delete the entire function. It returns rl_context for level_monitor + observation extractors — both gone.

- [ ] **Step 4: Remove RL turbo + stocks_mode + RL daemon + trading service startup blocks**

In the lifespan handler (around lines 351-465):
- Delete the `_stocks_mode = bool(os.environ.get("ARNOLD_STOCKS_MODE"))` check and its branch
- Delete the `turbo_flag` check that skips extraction when RL turbo is active
- Delete the `_start_rl_daemon` function + `threading.Thread(target=_start_rl_daemon...)` call
- Delete the `_start_trading_service` function + `threading.Thread(target=_start_trading_service...)` call
- Delete the `_start_trading_features` async function (around lines 485-720) — this is the giant Rithmic/Databento/Stocks bootstrap

Keep all sports/extraction/mirror startup.

- [ ] **Step 5: Remove the stocks bootstrap task at lines ~919-1134**

Delete the entire block starting around line 919 (`from ..market_data.level_monitor import LevelMonitor`) through line 1134 (`_stocks_bootstrap_task.add_done_callback(_background_tasks.discard)`). This is the TopstepX server bootstrap.

- [ ] **Step 6: Remove level_monitor access in request helpers**

Around line 1334:
```python
lm = getattr(request.app.state, "level_monitor", None)
```

Delete any function that uses `lm` (or `level_monitor`) — should be a `/status` or `/levels` endpoint. Search:

```bash
grep -n "level_monitor\|lm = " backend/src/api/__init__.py
```

Delete each function that uses it.

- [ ] **Step 7: Remove trading router includes at end**

Around line 1610:
```python
app.include_router(stocks_router)
```

Plus any nearby:
```python
app.include_router(trading_router)
app.include_router(market_router)
app.include_router(postmortem_router)
app.include_router(signals_ws_router)
```

Delete each.

- [ ] **Step 8: Remove WebSocket endpoints `/ws/signals` and `/ws/dashboard`**

```bash
grep -n "@app.websocket\|websocket(" backend/src/api/__init__.py
```

Delete any handler whose path contains `/signals` or `/dashboard`.

- [ ] **Step 9: Verify no remaining trading refs**

```bash
grep -n "stocks\|RL\|rl_\|broker\|trading\|topstepx\|level_monitor\|databento\|rithmic\|signals_ws\|market_router\|postmortem_router" backend/src/api/__init__.py
```

Expected: 0 matches (or only comments/strings that mention these words harmlessly — eyeball each remaining hit).

- [ ] **Step 10: Import smoke**

```bash
cd backend && python -c "from src.api import app; print('OK')"
```

Expected: `OK` printed. If ImportError, fix the missing import / undefined name in place before continuing.

- [ ] **Step 11: Commit**

```bash
git add backend/src/api/__init__.py
git commit -m "refactor(api): strip stocks/broker/RL/trading from lifespan + routes

Removes ~180 references to trading subsystem. Surviving code is
sports-extraction + mirror only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Strip api/routes/__init__.py + delete trading route files

**Files:**
- Modify: `backend/src/api/routes/__init__.py`
- Delete: `backend/src/api/routes/{stocks,trading,market,signals_ws,postmortem}.py`

- [ ] **Step 1: Edit `backend/src/api/routes/__init__.py`**

Remove these import + `__all__` lines:
```python
from .signals_ws import router as signals_ws_router
from .stocks import router as stocks_router
from .trading import router as trading_router
from .market import router as market_router
from .postmortem import router as postmortem_router
```
And drop `"signals_ws_router"`, `"stocks_router"`, `"trading_router"`, `"market_router"`, `"postmortem_router"` from `__all__`.

- [ ] **Step 2: Delete the trading route files**

```bash
git rm backend/src/api/routes/stocks.py backend/src/api/routes/trading.py backend/src/api/routes/market.py backend/src/api/routes/signals_ws.py backend/src/api/routes/postmortem.py
```

Expected: 5 files deleted.

- [ ] **Step 3: Import smoke**

```bash
cd backend && python -c "from src.api import app; print('OK')"
```

Expected: `OK`. If failure, find the leftover import.

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/__init__.py
git commit -m "refactor(api): drop stocks/trading/market/signals_ws/postmortem route files

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Delete trading services (trading_service, market_service)

**Files:**
- Delete: `backend/src/services/trading_service.py`, `backend/src/services/market_service.py`

- [ ] **Step 1: Verify no surviving code imports them**

```bash
grep -rn "from src.services.trading_service\|from src.services.market_service\|from .trading_service\|from .market_service\|trading_service\.\|market_service\." backend/src/ | grep -v "^backend/src/services/"
```

Expected: 0 hits outside `backend/src/services/` itself.

- [ ] **Step 2: Delete the files**

```bash
git rm backend/src/services/trading_service.py backend/src/services/market_service.py
```

- [ ] **Step 3: Check services/__init__.py for stale exports**

```bash
grep -n "trading_service\|market_service\|TradingService\|MarketService" backend/src/services/__init__.py
```

If any matches, delete those lines from `backend/src/services/__init__.py` and `git add` it.

- [ ] **Step 4: Import smoke**

```bash
cd backend && python -c "from src.api import app; print('OK')"
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git commit -am "refactor(services): drop trading_service + market_service

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Delete backend trading directories

**Files:** delete `backend/src/{stocks,broker,rithmic,market_data,edge,rl}/`

By Task 4 there should be no surviving imports of these modules. Verify, then nuke.

- [ ] **Step 1: Verify no surviving imports**

```bash
grep -rn "from src.stocks\|from src.broker\|from src.rithmic\|from src.market_data\|from src.edge\|from src.rl\|from ..stocks\|from ..broker\|from ..rithmic\|from ..market_data\|from ..edge\|from ..rl" backend/src/
```

Expected: 0 hits. If any, edit the file to remove the import (the line is dead — surrounding code already gone).

- [ ] **Step 2: Delete the directories**

```bash
git rm -r backend/src/stocks backend/src/broker backend/src/rithmic backend/src/market_data backend/src/edge backend/src/rl
```

Expected: hundreds of files deleted.

- [ ] **Step 3: Import smoke**

```bash
cd backend && python -c "from src.api import app; print('OK')"
```

Expected: `OK`. If ImportError, the import is somewhere not yet caught — fix and re-run.

- [ ] **Step 4: Commit**

```bash
git commit -am "refactor: delete backend/src/{stocks,broker,rithmic,market_data,edge,rl}/

Hard cutover — trading subsystem removed entirely.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Strip trading slice of ml/

**Files:** delete trading-specific `ml/` files; leave sports-side ML intact.

- [ ] **Step 1: Delete trading sub-dirs**

```bash
git rm -r backend/src/ml/level_touch backend/src/ml/macro
```

- [ ] **Step 2: Delete trading single files**

```bash
git rm backend/src/ml/feature_store.py backend/src/ml/migrations.py backend/src/ml/training/train_all.py backend/src/ml/features/candle_features.py backend/src/ml/features/level_touch_features.py backend/src/ml/features/trading_features.py backend/src/ml/models/level_classifier.py backend/src/ml/models/macro_engine.py
```

- [ ] **Step 3: Check ml/__init__.py for stale exports**

```bash
grep -n "level_touch\|macro\|feature_store\|candle\|level_classifier\|macro_engine\|trading_features" backend/src/ml/__init__.py backend/src/ml/features/__init__.py backend/src/ml/models/__init__.py backend/src/ml/training/__init__.py 2>/dev/null
```

For each match, delete that line. Also check for `from .level_touch import ...` style imports inside the remaining `ml/` files:

```bash
grep -rn "from .level_touch\|from .macro\|from .feature_store\|from .migrations\|from .features.candle\|from .features.level_touch_features\|from .features.trading_features\|from .models.level_classifier\|from .models.macro_engine" backend/src/ml/
```

For each hit, remove the import (the symbol was only used by other trading code, now gone).

- [ ] **Step 4: Import smoke**

```bash
cd backend && python -c "from src.api import app; print('OK')"
cd backend && python -c "from src.ml import *; print('ml OK')" 2>&1 | tail -5
```

Expected: both `OK`. If `ml OK` fails, fix the broken import (likely a sibling file that referenced the deleted one).

- [ ] **Step 5: Commit**

```bash
git add -A backend/src/ml/
git commit -m "refactor(ml): drop trading slice (level_touch, macro, candle/trading features, level_classifier, macro_engine)

Sports-side ML (adaptive_kelly, boost_calibrator, setup_scorer, optimizer/, analytics/, serving/) untouched.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Drop db/trading.py + db/ml.py + add DROP TABLE migrations

**Files:**
- Delete: `backend/src/db/trading.py`, `backend/src/db/ml.py`
- Modify: `backend/src/db/__init__.py`, `backend/src/db/models.py` (or wherever `_run_pg_migrations` lives)

- [ ] **Step 1: Verify no surviving imports of db.trading / db.ml symbols**

```bash
grep -rn "from src.db.trading\|from .db.trading\|from ..db.trading\|from src.db.ml\|from .db.ml\|from ..db.ml\|TradingAccount\|BrokerTrade\|StockSignal\|TradeEvent\|Postmortem\|PendingEpisode\|LiveEpisode\|MlFeature\|CandleSnapshot" backend/src/ | grep -v "^backend/src/db/"
```

Expected: 0 hits outside `backend/src/db/`.

- [ ] **Step 2: Read `backend/src/db/__init__.py` and remove trading + ml imports**

```bash
grep -n "trading\|ml" backend/src/db/__init__.py
```

Remove every `from .trading import *` and `from .ml import *` (and the corresponding `__all__` entries).

- [ ] **Step 3: Locate `_run_pg_migrations` and add DROP TABLE statements**

```bash
grep -rn "_run_pg_migrations\|def run_migrations\|ALTER TABLE\|CREATE TABLE IF NOT EXISTS" backend/src/db/models.py backend/src/db/ 2>/dev/null | head -20
```

In the migrations function, append (idempotent):

```python
# Strip trading subsystem (2026-05-25)
session.execute(text("DROP TABLE IF EXISTS broker_trades CASCADE"))
session.execute(text("DROP TABLE IF EXISTS stock_signals CASCADE"))
session.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
session.execute(text("DROP TABLE IF EXISTS trade_events CASCADE"))
session.execute(text("DROP TABLE IF EXISTS postmortems CASCADE"))
session.execute(text("DROP TABLE IF EXISTS pending_episodes CASCADE"))
session.execute(text("DROP TABLE IF EXISTS live_episodes CASCADE"))
session.execute(text("DROP TABLE IF EXISTS candle_snapshots CASCADE"))
session.execute(text("DROP TABLE IF EXISTS ml_features CASCADE"))
session.execute(text("DROP TABLE IF EXISTS trading_accounts CASCADE"))
session.execute(text("DROP TABLE IF EXISTS provider_risk_profile CASCADE")) if False else None  # KEEP — sports
```

Verify table list against `db/trading.py` + `db/ml.py` before committing — every `__tablename__` in those files should appear in the DROP list. Read each file:

```bash
grep -n "__tablename__" backend/src/db/trading.py backend/src/db/ml.py
```

Add any missing tables to the DROP list above.

- [ ] **Step 4: Delete db/trading.py + db/ml.py**

```bash
git rm backend/src/db/trading.py backend/src/db/ml.py
```

- [ ] **Step 5: Import smoke + migration dry-run**

```bash
cd backend && python -c "from src.api import app; print('OK')"
cd backend && python -c "from src.db.models import Base; print('Tables:', sorted(Base.metadata.tables.keys()))"
```

Expected: no trading table names in the printed list (no `broker_trades`, `stock_signals`, etc.).

- [ ] **Step 6: Commit**

```bash
git add -A backend/src/db/
git commit -m "feat(db): drop trading SQLAlchemy models + add DROP TABLE migrations

Tables dropped server-side on next deploy: broker_trades, stock_signals,
trades, trade_events, postmortems, pending_episodes, live_episodes,
candle_snapshots, ml_features, trading_accounts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Strip backend trading scripts + tests

**Files:** delete trading-only scripts and tests.

- [ ] **Step 1: List trading scripts to delete**

```bash
ls backend/scripts/ | grep -E "^(rl_|audit_|train_ft|run_sim|backtest_shadow|shadow_daily|trading_service)" | head -30
```

Expected list: `rl_train_daemon.sh`, `rl_train_pipeline.sh`, `audit_gbt_*`, `train_ft_v1.py`, `run_sim.py`, `backtest_shadow_models.py`, `shadow_daily_report.py`, `audit_dead_dims_diagnosis.py`, `trading_service.py`.

- [ ] **Step 2: Delete them**

```bash
git rm $(ls backend/scripts/ | grep -E "^(rl_|audit_|train_ft|run_sim|backtest_shadow|shadow_daily|trading_service)" | sed 's|^|backend/scripts/|')
```

- [ ] **Step 3: Delete trading tests**

```bash
git ls-files backend/tests/ | grep -E "(test_rl_|test_dqn|test_size_model|test_early_exit|test_hybrid_pipeline|test_live_inference|test_broker|test_topstepx|test_level_monitor|test_zone|test_orderflow|test_market_data|test_stocks)" | xargs -r git rm
```

Expected: trading-test files deleted (many already in staged deletes per pre-task git status).

- [ ] **Step 4: Import smoke + pytest collection**

```bash
cd backend && python -c "from src.api import app; print('OK')"
cd backend && pytest --collect-only 2>&1 | tail -15
```

Expected: import OK. Collection should show only sports/mirror tests; no collection errors. If errors, fix the import in the broken test file.

- [ ] **Step 5: Commit**

```bash
git add -A backend/scripts backend/tests
git commit -m "chore: delete trading scripts + tests

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Strip arnold local client backend (server.py, launch.py, stocks_runtime, tv_overlay)

**Files:**
- Modify: `arnold/server.py`, `arnold/launch.py`
- Delete: `arnold/stocks_runtime.py`, `arnold/tv_overlay/`

- [ ] **Step 1: Edit `arnold/server.py` — remove `/stocks/*` mount + dashboard**

```bash
grep -n "stocks\|dashboard\|tv_overlay\|signal_relay" arnold/server.py | head -30
```

For each hit, remove the line/block. Specifically:
- `from .stocks_runtime import ...`
- `app.include_router(stocks_router)` or any `/stocks/*` route
- `@app.get("/stocks/dashboard")` or static dashboard mount
- `@app.websocket("/stocks/ws/dashboard")` handler
- Any reference to `tv_overlay/`

- [ ] **Step 2: Edit `arnold/launch.py` — remove TopstepX env validation + stocks startup**

```bash
grep -n "stocks\|TOPSTEPX\|STOCKS_AUTONOMOUS\|stocks_runtime\|tv_overlay" arnold/launch.py | head -30
```

Delete each line. The launcher should now only:
1. Open SSH tunnel to server API (port 18000)
2. Start local FastAPI (port 8000)
3. Open browser

Remove anything about TopstepX session, signal relay heartbeat, stocks env, etc.

- [ ] **Step 3: Delete arnold/stocks_runtime.py + arnold/tv_overlay/**

```bash
git rm -r arnold/stocks_runtime.py arnold/tv_overlay
```

- [ ] **Step 4: Import smoke — local arnold venv**

```bash
.venv\Scripts\python.exe -c "from arnold import server; print('OK')"
```

Expected: `OK`. The local arnold runs from repo-root `.venv` (per memory). If ImportError, fix the leftover import.

- [ ] **Step 5: Commit**

```bash
git add -A arnold/
git commit -m "refactor(arnold): drop stocks_runtime + tv_overlay + /stocks routes

Local arnold launcher is now sports-only: SSH tunnel + FastAPI proxy + browser.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Strip arnold frontend — delete stocks pages + Trading sub-tabs + nav

**Files:**
- Delete: `arnold/frontend/src/pages/stocks/` (entire), `arnold/frontend/src/hooks/useDashboardWS.ts` (or wherever)
- Modify: `arnold/frontend/src/pages/BankrollPage.tsx`, `arnold/frontend/src/pages/StatsPage.tsx`, `arnold/frontend/src/App.tsx` (or main nav file)

- [ ] **Step 1: Find the App router and the useDashboardWS hook**

```bash
ls arnold/frontend/src/App.tsx arnold/frontend/src/main.tsx 2>/dev/null
grep -rn "useDashboardWS\|StocksPage\|/stocks\|SignalsPage" arnold/frontend/src/ | head -20
```

Note the file paths returned. The hook may be at `arnold/frontend/src/hooks/useDashboardWS.ts` or inline in `App.tsx`.

- [ ] **Step 2: Delete stocks pages**

```bash
git rm -r arnold/frontend/src/pages/stocks
```

- [ ] **Step 3: Delete useDashboardWS hook**

```bash
git rm arnold/frontend/src/hooks/useDashboardWS.ts 2>/dev/null || echo "Hook not in expected location — check grep output from Step 1"
```

If not at that path, delete it wherever Step 1's grep found it.

- [ ] **Step 4: Edit `arnold/frontend/src/App.tsx` — drop Stocks tab + ErrorBoundary**

In App.tsx (or wherever the top-level tab nav lives):
- Remove the `<Tab name="Stocks" ...>` (or React Router route) for stocks
- Remove the `<ErrorBoundary>` wrapper that surrounded the stocks tab
- Remove the `import { SignalsPage, BankrollPage as StocksBankrollPage, StatsPage as StocksStatsPage } from './pages/stocks/...'` imports
- Remove the `useDashboardWS()` call

- [ ] **Step 5: Edit `arnold/frontend/src/pages/BankrollPage.tsx` — drop Trading sub-tab**

The page currently has a sub-tab switcher between Sportbets and Trading. Remove the switcher entirely. Render Sportbets content as the page body. Drop all `subTab === 'trading'` branches.

```bash
grep -n "Trading\|subTab" arnold/frontend/src/pages/BankrollPage.tsx | head -10
```

For each hit, edit accordingly.

- [ ] **Step 6: Edit `arnold/frontend/src/pages/StatsPage.tsx` — drop Trading sub-tab**

Same pattern as Step 5 but for Stats page. Sportbets becomes the only view; remove Trading sub-tab + branches.

- [ ] **Step 7: Verify no remaining stocks imports**

```bash
grep -rn "stocks\|/stocks\|useDashboardWS\|SignalsPage\|tradeId\|broker_trades\|stock_signals" arnold/frontend/src/
```

Expected: 0 hits (or only CSS/comment hits — eyeball each).

- [ ] **Step 8: Frontend build**

```bash
cd arnold/frontend && npm run build
```

Expected: build succeeds, no TypeScript errors.

- [ ] **Step 9: Commit**

```bash
git add -A arnold/frontend/
git commit -m "refactor(arnold/frontend): drop stocks pages + Trading sub-tabs + dashboard WS

Bankroll and Stats become single-view sports-only pages.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Strip config — env vars, pyproject, server-deploy script, cron

**Files:**
- Modify: `.env.docker` (server-side — edit via SSH), `.env.docker.example` if exists, `backend/pyproject.toml`, `scripts/server-deploy.sh`
- Cron: edit via SSH

- [ ] **Step 1: List trading env vars in `.env.docker` template**

```bash
ls .env.docker .env.docker.example .env.example 2>/dev/null
grep -rn "STOCKS_\|TOPSTEPX_\|RITHMIC_\|RECKLESS_LEARNING_MODE\|ENABLE_PER_TICK_REVERSAL\|ENABLE_EARLY_EXIT_LOCK\|ALLOW_OPEN_POSITION_DEPLOY\|ZONE_COOLDOWN_S\|MIN_TRADE_INTERVAL_S\|ARNOLD_STOCKS_MODE" .env* 2>/dev/null
```

Local `.env*` files are typically gitignored — note any in-repo `.example` templates that exist and edit those.

- [ ] **Step 2: Edit any in-repo env templates**

For each `.example` file that has trading vars, delete those lines.

- [ ] **Step 3: Edit `backend/pyproject.toml` — drop unused trading deps**

```bash
grep -n "torch\|lightgbm\|joblib\|databento\|signalr\|tsxapi4py" backend/pyproject.toml
```

For each dep listed, grep the surviving code for its use:

```bash
grep -rn "^import torch\|^from torch\|^import lightgbm\|^from lightgbm\|^import joblib\|^from joblib\|^import databento" backend/src/
```

If 0 hits, remove the dep from `pyproject.toml`. Keep deps that still have hits.

- [ ] **Step 4: Edit `scripts/server-deploy.sh` — drop RL + position gate**

```bash
grep -n "rl_train\|wait_for_rl_training\|Position/searchOpen\|ALLOW_OPEN_POSITION_DEPLOY\|taskset\|nice -n 19\|stocks_running" scripts/server-deploy.sh | head -20
```

Delete:
- The `wait_for_rl_training` function and its callers
- The `Position/searchOpen` curl check (open-position gate)
- Any `taskset -c 0,1,4,5 nice -n 19` invocations
- Any `stocks_running` / `STOCKS_AUTONOMOUS` env reads

Keep the lock, git pull, docker build, restart, and `/health` wait logic.

- [ ] **Step 5: Local arnold + backend smoke**

```bash
cd backend && python -c "from src.api import app; print('OK')"
.venv\Scripts\python.exe -c "from arnold import server; print('arnold OK')"
```

Expected: both `OK`.

- [ ] **Step 4b: SSH to server — strip env vars + cron entries**

`.env.docker` lives on the server (gitignored locally). Edit via SSH:

```bash
ssh root@148.251.40.251 "cd /opt/arnold && cp .env.docker .env.docker.bak-\$(date +%Y%m%d) && grep -vE '^(STOCKS_|TOPSTEPX_|RITHMIC_|RECKLESS_LEARNING_MODE|ENABLE_PER_TICK_REVERSAL|ENABLE_EARLY_EXIT_LOCK|ALLOW_OPEN_POSITION_DEPLOY|ZONE_COOLDOWN_S|MIN_TRADE_INTERVAL_S|ARNOLD_STOCKS_MODE)' .env.docker.bak-\$(date +%Y%m%d) > .env.docker"
```

Expected: backup made, new `.env.docker` has trading vars removed.

Then remove the RL daemon + rl-backup cron entries:

```bash
ssh root@148.251.40.251 "crontab -l | grep -vE 'rl_train_daemon|rl_train_pipeline|rl-backup' | crontab -"
ssh root@148.251.40.251 "crontab -l"
```

Expected: container watchdog cron remains; RL + rl-backup entries gone.

- [ ] **Step 6: Commit**

```bash
git add -A .env* backend/pyproject.toml scripts/server-deploy.sh 2>/dev/null
git commit -m "chore: drop trading env vars, deps, and deploy-script gates

- pyproject: remove torch/lightgbm/joblib if unused
- server-deploy.sh: remove RL wait + open-position gate + CPU isolation
- .env templates: remove TOPSTEPX/STOCKS/RITHMIC vars
- server-side .env.docker + cron updated separately via SSH

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Trim CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Identify sections to delete**

```bash
grep -n "^##\|^###" CLAUDE.md | grep -iE "stock|trading|topstepx|rl |level|zone|broker|trade lifecycle|phase 1|phase 2|signal|feedback loop|cpu isolation|tradingview|chart|model conventions"
```

Expected: list of section headers. Note line numbers.

- [ ] **Step 2: Delete each trading section**

Use Edit to remove each section heading + body, including:
- "Stocks autonomous trading"
- "Stocks — Trade Lifecycle (Phase 1 / Phase 2 state machine)"
- "Stocks — Chart & Model Conventions"
- "Live trade → training feedback loop"
- "RL CPU isolation"
- "Stocks-aware rebuild rules"
- Stocks columns/rows in the "Two Programs" table
- Stocks portions of the architecture diagram
- "Memory budget" → keep, but drop RL-related sub-bullets
- "How to Deploy Changes" → drop the open-position gate paragraph
- "Multi-Agent Coordination" → drop rule 16 (stocks-aware rebuild rules)
- Any other reference to TopstepX, Rithmic, NQ, zones, DQN, GBT, level_monitor

- [ ] **Step 3: Update intro + "Two Programs" table**

Edit the top of CLAUDE.md so it describes Arnold as a sports-betting platform only. Drop the "Stocks" column/row from the "Two Programs" table; the table becomes just the Server + Arnold (local sports client).

- [ ] **Step 4: Verify**

```bash
grep -in "stocks\|topstepx\|trading\|broker\|rl train\|level_monitor\|tradingview\|dqn\|gbt" CLAUDE.md | head -20
```

Expected: 0 hits (or only hits in code-comment examples that talk about general patterns — eyeball each).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): drop trading sections — pure sports platform

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Trim auto-memory

**Files:**
- Modify: `C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\MEMORY.md`
- Delete: trading entries under `C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\*.md`

This is OUTSIDE the git repo. Changes here are not committed; they're machine-local memory.

- [ ] **Step 1: List all memory files**

```bash
ls "C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\" | sort
```

- [ ] **Step 2: Identify trading entries via grep**

Use the Grep tool on the memory dir to surface candidate files:

```
Grep: pattern="stocks|topstepx|broker|level_monitor|zone|orderflow|dqn|gbt|episode|candle|tradingview|trail|rithmic|trading_account|reckless|phase 1|phase 2|of_score|rl_v|databento", path="C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory", output_mode="files_with_matches", -i=true
```

Save the resulting file list mentally (or in scratch). Then cross-reference
against the KEEP list in Step 3.

- [ ] **Step 3: Eyeball each file in the list — REMOVE from delete list any sports-relevant ones**

Open each file in `/tmp/trading-memory-files.txt` and read its first 5 lines. Sports-relevant memories that mention "trade" or "signal" in passing (e.g., bet placement, mirror signals) should be KEPT.

Known KEEP (do NOT delete):
- `feedback_never_mix_currencies.md`
- `proxy_architecture.md`
- `reference_pinnacle_proxy.md`
- `user_rasmus.md`
- `project_pinnacle_wiring.md`
- `extraction_audit_2026_04_07.md`
- `project_international_providers.md`
- `project_generic_mirror_workflow.md`
- `feedback_provider_discovery_first.md`
- `feedback_provider_onboarding.md`
- `feedback_altenar_wasm.md`
- `project_leovegas_mirror.md`
- `project_kalshi_smarkets_integration.md`
- `project_pinnacle_cleanup_race.md`
- `feedback_no_deploy_for_local_frontend.md`
- `project_comeon_dom_fingerprint.md`
- `feedback_capability_matrix_lies.md`
- `feedback_keep_all_data_sources.md`
- `feedback_polymarket_only_redeem_at_100.md`
- `feedback_ruff_autofix_strips_imports.md`
- `project_dbet_ghost_tab_fix.md`
- `feedback_asyncio_task_strong_ref.md`
- `project_altenar_status_parser_gotcha.md`
- `project_manual_bet_recorder.md`
- `project_dom_scrape_team_name_match.md`
- `project_pending_row_ui_contract.md`
- `reference_arnold_venv.md`
- `project_reactive_sync_architecture.md`
- `project_kalshi_recorder_fixes_2026_05_23.md`
- `feedback_rebuild_frontend.md`
- `feedback_no_paid_data_feeds.md`
- `feedback_no_more_databento_backfill.md`
- `project_bankroll_deploy_pending.md`
- `project_deploy_queue_needed.md`

Anything else from the grep that's purely about trading goes.

- [ ] **Step 4: Delete trading memory files**

For each file confirmed as trading-only, delete:
```powershell
Remove-Item "C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\<file>.md"
```

- [ ] **Step 5: Update MEMORY.md index**

Edit `C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\MEMORY.md` and delete every line that points to a now-deleted file.

- [ ] **Step 6: Add a memory entry for the strip itself**

Create `C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\project_trading_stripped_2026_05_25.md`:

```markdown
---
name: trading-stripped-2026-05-25
description: Arnold stripped of NQ-futures-trading subsystem on 2026-05-25 — pure sports-betting platform now
metadata:
  type: project
---

Arnold became a sports-betting platform on 2026-05-25 (commit chain at that
date). Deleted: backend/src/{stocks,broker,rithmic,market_data,edge,rl}/,
backend/src/services/{trading,market}_service.py, backend/src/db/{trading,ml}.py,
backend/src/api/routes/{stocks,trading,market,signals_ws,postmortem}.py,
backend/src/ml/{level_touch,macro,feature_store,migrations}.py +
features/{candle,level_touch,trading}_features.py +
models/{level_classifier,macro_engine}.py, arnold/stocks_runtime.py +
arnold/tv_overlay/, arnold/frontend/src/pages/stocks/, the Trading sub-tabs
on Bankroll + Stats, TopstepX/Rithmic env vars, the deploy-script
open-position gate and RL CPU isolation.

DB tables dropped on the deploy after this commit chain: broker_trades,
stock_signals, trades, trade_events, postmortems, pending_episodes,
live_episodes, candle_snapshots, ml_features, trading_accounts.

**Why:** user explicit decision; trading was a separate concern that grew
complex enough to warrant its own codebase if revisited.

**How to apply:** if any future request references stocks/trading/RL/zones/
TopstepX, the platform no longer supports any of it. Re-introducing trading
means a fresh design — old commits in git history are the only reference.
The TopstepX subscription needs to be cancelled separately by the user.
```

Then add a line to `MEMORY.md`:
```
- [Trading stripped 2026-05-25](project_trading_stripped_2026_05_25.md) — Arnold became sports-only; all NQ-futures code, DB tables, frontend gone
```

- [ ] **Step 7: No commit — memory lives outside the repo**

---

## Task 14: Final verification + deploy

**Files:** none modified directly. Validation only.

- [ ] **Step 1: Final repo-wide grep — should be clean**

```bash
grep -rn "from src.stocks\|from src.broker\|from src.market_data\|from src.rl\|from src.rithmic\|from src.edge" backend/ arnold/ 2>/dev/null | head
```

Expected: 0 hits.

```bash
grep -rn "TopstepX\|topstepx\|broker_adapter\|level_monitor\|stocks_runtime" backend/src/ arnold/ 2>/dev/null | head
```

Expected: 0 hits (or only string-literal mentions in docs/comments).

- [ ] **Step 2: Backend import + pytest**

```bash
cd backend && python -c "from src.api import app; print('app OK')"
cd backend && pytest -x --tb=short 2>&1 | tail -25
```

Expected: `app OK` + pytest passes (or only skipped trading tests, which should already be deleted).

- [ ] **Step 3: Frontend build**

```bash
cd arnold/frontend && npm run lint && npm run build
```

Expected: lint passes (or only pre-existing warnings), build succeeds.

- [ ] **Step 4: Local arnold smoke**

```bash
.venv\Scripts\python.exe -c "from arnold import server, launch; print('arnold OK')"
```

Expected: `arnold OK`.

- [ ] **Step 5: Verify branch + push**

```bash
git log --oneline main..strip-trading | head -20
git fetch origin
git log origin/main..strip-trading --oneline
```

Expected: clean chain of commits from this plan, fast-forwardable onto `origin/main`.

- [ ] **Step 6: Merge to main**

```bash
git checkout main
git merge --ff-only strip-trading
git push origin main
```

Expected: fast-forward push succeeds.

- [ ] **Step 7: Deploy to Hetzner**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```

Expected: deploy completes (no `wait_for_rl_training` block since we stripped it). `/health` returns OK after rebuild.

- [ ] **Step 8: Verify deploy**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose ps && curl -sf http://localhost:8000/health | python3 -m json.tool"
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend cat /app/logs/extraction.log | tail -20"
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"\\dt\" | grep -E 'broker_trades|stock_signals|trades$|trade_events|postmortems|pending_episodes|live_episodes|candle_snapshots|trading_accounts'"
```

Expected:
- Containers running (backend + postgres + nginx)
- `/health` returns 200 + green
- Extraction logs show normal pinnacle/altenar/etc. runs
- Last query returns 0 rows (tables dropped)

- [ ] **Step 9: Launch local arnold and verify UI**

```powershell
arnold.bat
```

Expected:
- Browser opens to `https://localhost:8000`
- Tabs visible: Sports, Bankroll, Stats — no Stocks
- Bankroll page renders single Sportbets view, no Trading sub-tab
- Stats page renders single Betting view, no Trading sub-tab
- No console errors related to `useDashboardWS` or stocks WS

- [ ] **Step 10: Delete strip-trading branch (cleanup)**

```bash
git branch -d strip-trading
```

---

## Done

After Task 14:
- Backend is sports-only
- Frontend is sports-only
- DB has only sports tables
- Local arnold has only sports UI
- Config has no trading env vars
- CLAUDE.md describes a pure sports platform
- Memory has no stale trading entries

Remaining manual cleanup (not blocking):
- User cancels TopstepX subscription ($14.50/mo)
- User runs `ssh root@148.251.40.251 "rm -rf /app/data/rl"` if disk space is needed (not required — data is dormant)
- User deletes the trading specs/plans in `docs/superpowers/{specs,plans}/` if archival doesn't matter (left in by design)
