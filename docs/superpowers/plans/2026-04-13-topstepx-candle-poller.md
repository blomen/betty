# TopstepX Candle Poller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Continuous 24/5 NQ 1m candle data in `market_candles` via TopstepX REST API polling from the server.

**Architecture:** A single async background task inside the existing backend container polls `POST /api/History/retrieveBars` every 60s, upserts closed 1m bars into the existing `market_candles` table. On startup, backfills any gaps since the last known candle. Gated by `TOPSTEPX_POLLER_ENABLED` env var.

**Tech Stack:** Python 3.10+, httpx (already in deps), SQLAlchemy (existing MarketRepo), FastAPI lifespan (existing pattern).

---

### Task 1: TopstepX Poller — Auth + Poll + Persist

**Files:**
- Create: `backend/src/market_data/topstepx_poller.py`

- [ ] **Step 1: Create the poller module**

```python
"""TopstepX REST candle poller — fetches 1m bars and persists to market_candles.

Runs as an async background task inside the FastAPI backend.
Polls POST /api/History/retrieveBars every 60s.
No WebSocket, no trading session — stateless REST only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_ET = ZoneInfo("US/Eastern")
_API_BASE = "https://api.topstepx.com"


def _is_globex_open(now_et: datetime) -> bool:
    """Check if CME Globex is open (Sun 18:00 ET → Fri 17:00 ET, halt 17-18 daily)."""
    wd = now_et.weekday()  # 0=Mon ... 6=Sun
    hour = now_et.hour

    # Saturday: always closed
    if wd == 5:
        return False
    # Sunday: open only after 18:00
    if wd == 6:
        return hour >= 18
    # Friday: open until 17:00
    if wd == 4:
        return hour < 17
    # Mon-Thu: closed during 17:00-18:00 daily halt
    return not (hour == 17)


class TopstepXPoller:
    """Polls TopstepX REST API for 1m candles and writes to market_candles."""

    POLL_INTERVAL = 60  # seconds
    TOKEN_REFRESH_AGE = 23 * 3600  # refresh after 23 hours
    BACKFILL_CHUNK = 20_000  # max bars per request
    BACKFILL_DELAY = 1.0  # seconds between chunk fetches

    def __init__(self, db_session_factory):
        self._db_factory = db_session_factory
        self._token: str | None = None
        self._token_time: float = 0.0
        self._username = os.environ.get("TOPSTEPX_PAPER_USERNAME", "")
        self._api_key = os.environ.get("TOPSTEPX_PAPER_API_KEY", "")
        self._contract_id = os.environ.get("TOPSTEPX_CONTRACT", "CON.F.US.ENQ.M26")
        self._symbol = "NQ"
        self._last_candle_ts: datetime | None = None
        self._running = False
        self._client: httpx.AsyncClient | None = None

    async def start(self):
        """Entry point — backfill gaps then poll forever."""
        if not self._username or not self._api_key:
            logger.warning("TopstepXPoller: missing TOPSTEPX_PAPER_USERNAME or TOPSTEPX_PAPER_API_KEY — disabled")
            return

        self._running = True
        self._client = httpx.AsyncClient(timeout=30.0)
        logger.info("TopstepXPoller starting (contract=%s)", self._contract_id)

        try:
            if not await self._authenticate():
                logger.error("TopstepXPoller: initial auth failed — will retry in poll loop")

            await self._backfill()
            await self._poll_loop()
        except asyncio.CancelledError:
            logger.info("TopstepXPoller cancelled")
        except Exception:
            logger.exception("TopstepXPoller crashed")
        finally:
            self._running = False
            if self._client:
                await self._client.aclose()

    async def _authenticate(self) -> bool:
        """Get or refresh JWT token."""
        # Try refresh first if we have a token
        if self._token and (time.time() - self._token_time) < self.TOKEN_REFRESH_AGE:
            return True

        if self._token:
            try:
                r = await self._client.post(
                    f"{_API_BASE}/api/Auth/validate",
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                if r.status_code == 200:
                    data = r.json()
                    if data.get("token"):
                        self._token = data["token"]
                        self._token_time = time.time()
                        logger.info("TopstepXPoller: token refreshed")
                        return True
            except Exception:
                pass  # fall through to full auth

        try:
            r = await self._client.post(
                f"{_API_BASE}/api/Auth/loginKey",
                json={"userName": self._username, "apiKey": self._api_key},
            )
            if r.status_code == 200:
                data = r.json()
                self._token = data.get("token")
                self._token_time = time.time()
                if self._token:
                    logger.info("TopstepXPoller: authenticated as %s", self._username)
                    return True
            logger.error("TopstepXPoller: auth failed (status=%d)", r.status_code)
        except Exception as e:
            logger.error("TopstepXPoller: auth error: %s", e)
        return False

    async def _fetch_bars(self, start: datetime, end: datetime, limit: int = 10) -> list[dict]:
        """Fetch 1m bars from TopstepX REST API."""
        if not await self._authenticate():
            return []

        try:
            r = await self._client.post(
                f"{_API_BASE}/api/History/retrieveBars",
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "contractId": self._contract_id,
                    "live": False,
                    "startTime": start.isoformat(),
                    "endTime": end.isoformat(),
                    "unit": 2,  # Minute
                    "unitNumber": 1,
                    "limit": limit,
                    "includePartialBar": False,
                },
            )
            if r.status_code == 429:
                logger.warning("TopstepXPoller: rate limited, backing off")
                return []
            if r.status_code != 200:
                logger.warning("TopstepXPoller: retrieveBars status=%d", r.status_code)
                return []
            data = r.json()
            return data.get("bars", [])
        except Exception as e:
            logger.warning("TopstepXPoller: fetch error: %s", e)
            return []

    def _persist_bars(self, bars: list[dict]) -> int:
        """Upsert bars into market_candles. Returns count persisted."""
        if not bars:
            return 0

        from ..repositories.market_repo import MarketRepo

        db = self._db_factory()
        try:
            repo = MarketRepo(db)
            count = 0
            for bar in bars:
                ts_str = bar.get("t", "")
                if not ts_str:
                    continue
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                v = int(bar.get("v", 0))
                if v <= 0:
                    continue  # skip empty bars
                repo.upsert_candle(
                    symbol=self._symbol,
                    interval="1m",
                    ts=ts,
                    o=float(bar["o"]),
                    h=float(bar["h"]),
                    l=float(bar["l"]),
                    c=float(bar["c"]),
                    v=v,
                )
                count += 1
                self._last_candle_ts = ts
            return count
        except Exception as e:
            logger.warning("TopstepXPoller: persist error: %s", e)
            return 0
        finally:
            db.close()

    async def _backfill(self):
        """Fill gaps from last known candle to now."""
        from ..db.models import MarketCandle

        db = self._db_factory()
        try:
            row = (
                db.query(MarketCandle.ts)
                .filter_by(symbol=self._symbol, interval="1m")
                .order_by(MarketCandle.ts.desc())
                .first()
            )
            if row:
                last_ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=timezone.utc)
                self._last_candle_ts = last_ts
            else:
                last_ts = None
        finally:
            db.close()

        now = datetime.now(timezone.utc)

        if last_ts is None:
            # No data at all — fetch last 7 days
            start = now - timedelta(days=7)
            logger.info("TopstepXPoller: no candles found, backfilling 7 days")
        else:
            gap_minutes = (now - last_ts).total_seconds() / 60
            if gap_minutes <= 2:
                logger.info("TopstepXPoller: candles up to date (latest %s)", last_ts)
                return
            start = last_ts
            logger.info("TopstepXPoller: backfilling gap %s → %s (%.0f min)", last_ts, now, gap_minutes)

        # Paginate in chunks
        cursor = start
        total = 0
        while cursor < now:
            chunk_end = min(cursor + timedelta(days=14), now)  # ~20k bars at 1m = ~14 days
            bars = await self._fetch_bars(cursor, chunk_end, limit=self.BACKFILL_CHUNK)
            if not bars:
                break
            count = self._persist_bars(bars)
            total += count
            # Advance cursor past last bar
            last_bar_ts = datetime.fromisoformat(bars[-1]["t"].replace("Z", "+00:00"))
            if last_bar_ts <= cursor:
                break  # no progress
            cursor = last_bar_ts + timedelta(minutes=1)
            await asyncio.sleep(self.BACKFILL_DELAY)

        if total > 0:
            logger.info("TopstepXPoller: backfilled %d candles", total)

    async def _poll_loop(self):
        """Main loop — fetch latest bars every 60 seconds."""
        logger.info("TopstepXPoller: entering poll loop (every %ds)", self.POLL_INTERVAL)

        while self._running:
            now_et = datetime.now(_ET)

            if not _is_globex_open(now_et):
                await asyncio.sleep(self.POLL_INTERVAL)
                continue

            now = datetime.now(timezone.utc)
            start = now - timedelta(minutes=5)  # fetch last 5 min to catch any missed
            bars = await self._fetch_bars(start, now, limit=10)
            count = self._persist_bars(bars)
            if count > 0:
                logger.debug("TopstepXPoller: persisted %d bars (latest %s)", count, self._last_candle_ts)

            await asyncio.sleep(self.POLL_INTERVAL)

    def get_status(self) -> dict:
        """Return poller status for health endpoint."""
        return {
            "enabled": True,
            "running": self._running,
            "last_candle": self._last_candle_ts.isoformat() if self._last_candle_ts else None,
            "authenticated": self._token is not None,
            "contract": self._contract_id,
        }
```

- [ ] **Step 2: Verify module imports correctly**

Run:
```bash
cd backend && python -c "from src.market_data.topstepx_poller import TopstepXPoller; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/market_data/topstepx_poller.py
git commit -m "feat(market-data): add TopstepX REST candle poller"
```

---

### Task 2: Wire Poller into Backend Startup

**Files:**
- Modify: `backend/src/api/__init__.py`

- [ ] **Step 1: Find the insertion point**

The poller should start after the Databento/stocks-mode block, near the end of the lifespan function before `yield`. Search for the line:

```python
if _stocks_mode and not _mirror_only:
```

The TopstepX poller is independent of stocks mode — it runs on the server alongside extraction. Add it after the scheduler block but before the `yield`.

- [ ] **Step 2: Add the poller startup code**

Find this block near the end of the startup section (after all the stocks_mode and databento blocks, before `yield`):

```python
    # ── Yield: app is ready ──
    yield
```

Add the following **before** that `yield`:

```python
    # ── TopstepX candle poller (REST-based, no WebSocket session) ──
    _topstepx_poller = None
    if os.environ.get("TOPSTEPX_POLLER_ENABLED", "").lower() in ("1", "true", "yes"):
        from ..db.models import get_market_session as _poller_db
        from ..market_data.topstepx_poller import TopstepXPoller

        _topstepx_poller = TopstepXPoller(db_session_factory=_poller_db)
        app.state.topstepx_poller = _topstepx_poller

        _poller_task = asyncio.create_task(_topstepx_poller.start())
        _poller_task.set_name("topstepx-poller")
        _background_tasks.add(_poller_task)
        _poller_task.add_done_callback(_background_tasks.discard)
        logger.info("[Startup] TopstepX candle poller enabled")
    else:
        logger.info("[Startup] TopstepX candle poller disabled (set TOPSTEPX_POLLER_ENABLED=true)")
```

- [ ] **Step 3: Add health status to /health endpoint**

Find the `health()` function and add poller status. Modify the return dict:

```python
@app.get("/health")
async def health():
    result = {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "boot_id": _boot_id,
        "uptime": round(time.time() - _startup_time) if _startup_time else 0,
    }
    poller = getattr(app.state, "topstepx_poller", None)
    if poller:
        result["market_data_poller"] = poller.get_status()
    return result
```

- [ ] **Step 4: Verify backend starts without the env var set**

Run:
```bash
cd backend && python -c "
import os
os.environ['TOPSTEPX_POLLER_ENABLED'] = ''
from src.api import create_app
app = create_app()
print('App created OK — poller disabled')
"
```

Expected: App creates without error, log shows "TopstepX candle poller disabled"

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/__init__.py
git commit -m "feat(startup): wire TopstepX candle poller into backend lifespan"
```

---

### Task 3: Add Environment Variables to Server

**Files:**
- Modify: `.env.docker` (on server only — this file is gitignored)

- [ ] **Step 1: SSH to server and add env vars**

```bash
ssh root@148.251.40.251 "cat >> /opt/firev/.env.docker << 'EOF'

# TopstepX candle poller (REST API, paper account)
TOPSTEPX_POLLER_ENABLED=true
TOPSTEPX_PAPER_USERNAME=<paper-account-username>
TOPSTEPX_PAPER_API_KEY=<paper-account-api-key>
TOPSTEPX_CONTRACT=CON.F.US.ENQ.M26
EOF"
```

Replace `<paper-account-username>` and `<paper-account-api-key>` with actual credentials.

- [ ] **Step 2: Verify env vars are set**

```bash
ssh root@148.251.40.251 "grep TOPSTEPX_POLLER /opt/firev/.env.docker"
```

Expected: Shows the 4 env vars.

---

### Task 4: Deploy and Verify

**Files:** None (deployment only)

- [ ] **Step 1: Push to main**

```bash
git push origin main
```

- [ ] **Step 2: Deploy via server-deploy.sh**

```bash
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh rebuild backend"
```

Wait for health check to pass (~2 min).

- [ ] **Step 3: Check logs for poller startup**

```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose logs --tail=30 backend 2>&1 | grep -i topstep"
```

Expected: Lines containing "TopstepX candle poller enabled", "TopstepXPoller starting", "authenticated as ...", and backfill messages.

- [ ] **Step 4: Verify candles are being written**

```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T postgres psql -U firev -d market -c \"
SELECT COUNT(*) as recent, MAX(ts) as latest
FROM market_candles
WHERE symbol='NQ' AND interval='1m' AND ts > NOW() - INTERVAL '10 minutes';
\""
```

Expected: `recent` > 0, `latest` within last few minutes.

- [ ] **Step 5: Check health endpoint**

```bash
ssh root@148.251.40.251 "curl -s http://localhost:8000/health | python3 -m json.tool"
```

Expected: `market_data_poller` section showing `running: true`, `authenticated: true`, recent `last_candle`.

- [ ] **Step 6: Verify chart has no gaps**

Open firevstocks locally, check the chart. Current session candles should flow continuously without gaps.
