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
        self._username = os.environ.get("TOPSTEPX_PAPER_USERNAME", "") or os.environ.get("TOPSTEPX_USERNAME", "")
        self._api_key = os.environ.get("TOPSTEPX_PAPER_API_KEY", "") or os.environ.get("TOPSTEPX_API_KEY", "")
        self._contract_id = os.environ.get("TOPSTEPX_CONTRACT", "CON.F.US.ENQ.M26")
        self._symbol = "NQ"
        self._last_candle_ts: datetime | None = None
        self._running = False
        self._client: httpx.AsyncClient | None = None

    async def start(self):
        """Entry point — backfill gaps then poll forever."""
        if not self._username or not self._api_key:
            logger.warning("TopstepXPoller: missing TOPSTEPX_USERNAME or TOPSTEPX_API_KEY — disabled")
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
        # Token still valid
        if self._token and (time.time() - self._token_time) < self.TOKEN_REFRESH_AGE:
            return True

        # Try refresh via validate
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

        # Full auth
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
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
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
            chunk_end = min(cursor + timedelta(days=14), now)
            bars = await self._fetch_bars(cursor, chunk_end, limit=self.BACKFILL_CHUNK)
            if not bars:
                break
            count = self._persist_bars(bars)
            total += count
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
            start = now - timedelta(minutes=5)
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
