"""Market scan scheduler — runs periodic analysis during RTH."""

import asyncio
import logging
from datetime import datetime, time

logger = logging.getLogger(__name__)


class MarketScanScheduler:
    """Runs market compute + scan on an interval during RTH hours."""

    def __init__(self, interval_minutes: int = 5, rth_open: str = "09:30", rth_close: str = "16:00"):
        self.interval = interval_minutes * 60
        h_open, m_open = map(int, rth_open.split(":"))
        h_close, m_close = map(int, rth_close.split(":"))
        self.rth_open = time(h_open, m_open)
        self.rth_close = time(h_close, m_close)
        self._task: asyncio.Task | None = None

    def start(self):
        """Start the scheduler loop."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("Market scan scheduler started (every %ds during RTH)", self.interval)

    def stop(self):
        """Stop the scheduler."""
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("Market scan scheduler stopped")

    def _is_rth(self) -> bool:
        """Check if current time is within Regular Trading Hours (Eastern)."""
        now = datetime.now().time()
        return self.rth_open <= now <= self.rth_close

    async def _loop(self):
        """Main scheduler loop."""
        while True:
            try:
                if self._is_rth():
                    await self._run_scan()
                else:
                    logger.debug("Outside RTH, skipping scan")
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Market scan failed: %s", e, exc_info=True)
                await asyncio.sleep(self.interval)

    async def _run_scan(self):
        """Execute a single scan cycle."""
        from ..db.models import get_session
        from ..services.market_service import MarketService

        db = get_session()
        try:
            svc = MarketService(db)
            await svc.compute_session()
            signals = await svc.run_scan()
            logger.info("Scheduled scan: %d signals", len(signals))
        except Exception as e:
            logger.error("Scheduled scan error: %s", e)
            db.rollback()
        finally:
            db.close()
