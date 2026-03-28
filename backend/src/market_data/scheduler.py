"""Market scan scheduler — runs periodic analysis continuously."""

import asyncio
import logging

logger = logging.getLogger(__name__)


class MarketScanScheduler:
    """Runs market compute + scan on a fixed interval while the backend is up."""

    def __init__(self, interval_minutes: int = 5):
        self.interval = interval_minutes * 60
        self._task: asyncio.Task | None = None

    def start(self):
        """Start the scheduler loop."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("Market scan scheduler started (every %ds, always-on)", self.interval)

    def stop(self):
        """Stop the scheduler."""
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("Market scan scheduler stopped")

    # When market is closed, sleep this long between checks instead of self.interval.
    # Avoids waking every 5 min to do nothing.
    _CLOSED_MARKET_SLEEP = 300  # 5 min — check periodically for market open

    async def _loop(self):
        """Main scheduler loop — runs continuously."""
        # Run immediately on startup, then every interval
        while True:
            try:
                ran = await self._run_scan()
                # Sleep longer when market is closed to conserve resources
                sleep_time = self.interval if ran else self._CLOSED_MARKET_SLEEP
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Market scan failed: %s", e, exc_info=True)
                await asyncio.sleep(self.interval)

    async def _run_scan(self):
        """Execute a single scan cycle.

        Returns True if scan ran, False if skipped (market closed).
        """
        from ..db.models import get_session
        from ..services.market_service import MarketService

        # Skip during weekend close — no new data to process
        if MarketService._is_globex_closed():
            logger.debug("Globex closed — skipping scheduled scan")
            return False

        db = get_session()
        try:
            svc = MarketService(db)
            await svc.compute_session()
            signals = await svc.run_scan()
            logger.info("Scheduled scan: %d signals", len(signals))
            return True
        except Exception as e:
            logger.error("Scheduled scan error: %s", e)
            db.rollback()
            return False
        finally:
            db.close()
