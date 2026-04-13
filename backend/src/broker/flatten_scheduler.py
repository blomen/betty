"""Auto-flatten scheduler — closes all positions before market close."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
ET = ZoneInfo("US/Eastern")


class FlattenScheduler:
    """Background task that auto-flattens at a configured ET time."""

    def __init__(self, adapter, flatten_et: str = "15:55") -> None:
        self._adapter = adapter
        h, m = flatten_et.split(":")
        self._flatten_time = time(int(h), int(m))
        self._verify_time = time(int(h), min(int(m) + 4, 59))
        self._flattened_today = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        self._task.set_name("flatten-scheduler")

    async def _loop(self) -> None:
        while True:
            now_et = datetime.now(ET)
            current_time = now_et.time()

            # Reset flag at midnight
            if current_time < time(0, 5):
                self._flattened_today = False
                self._adapter.reset_session()
                log.info("Flatten scheduler: new day, session reset")

            # Flatten at scheduled time
            if not self._flattened_today and current_time >= self._flatten_time:
                log.info("Flatten scheduler: %s ET — closing all positions", self._flatten_time)
                try:
                    result = await self._adapter.flatten("eod_flatten")
                    self._flattened_today = True
                    # Halt the adapter so no new signals can open positions after close
                    self._adapter.halt("eod_flatten")
                    log.info("EOD flatten complete: session P&L=$%.2f", result.get("session_pnl", 0))
                except Exception:
                    log.exception("EOD flatten failed!")

            # Safety verify at +4 min — skip if adapter is already halted (EOD done)
            if self._flattened_today and current_time >= self._verify_time:
                if not self._adapter._halted and not self._adapter.tracker.is_flat:
                    log.error("SAFETY: Still not flat at %s — forcing liquidation!", current_time)
                    await self._adapter.flatten("safety_verify")

            await asyncio.sleep(30)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
