"""Auto-flatten scheduler — closes all positions before market close."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
ET = ZoneInfo("US/Eastern")


class FlattenScheduler:
    """Background task that auto-flattens at a configured ET time.

    Tracks by calendar date (not a per-run boolean) so a restart after
    15:55 ET (e.g. Sunday evening Globex open) does NOT re-trigger EOD
    flatten.  Only fires on weekdays (Mon–Fri).
    """

    def __init__(self, adapter, flatten_et: str = "15:55") -> None:
        self._adapter = adapter
        h, m = flatten_et.split(":")
        self._flatten_time = time(int(h), int(m))
        self._verify_time = time(int(h), min(int(m) + 4, 59))
        # Only flatten within a 20-minute window after the scheduled time.
        # Restarts at 19:00, Sunday evening, etc. will not re-trigger.
        _end_m = int(m) + 20
        self._flatten_end_time = time(int(h) + _end_m // 60, _end_m % 60)
        self._flattened_date: date | None = None  # date the last EOD flatten ran
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        self._task.set_name("flatten-scheduler")

    async def _loop(self) -> None:
        while True:
            now_et = datetime.now(ET)
            current_time = now_et.time()
            today = now_et.date()

            # Reset session at midnight on a new trading day (Mon–Fri)
            if current_time < time(0, 5) and today.weekday() < 5:
                if self._flattened_date != today:
                    self._adapter.reset_session()
                    log.info("Flatten scheduler: new trading day %s, session reset", today)

            # EOD flatten — only on weekdays, only within the 20-min window after
            # the scheduled time, only once per calendar date.
            # The time window prevents evening restarts from re-triggering the halt.
            if (
                today.weekday() < 5
                and self._flattened_date != today
                and self._flatten_time <= current_time < self._flatten_end_time
            ):
                log.info("Flatten scheduler: %s ET — closing all positions", self._flatten_time)
                try:
                    result = await self._adapter.flatten("eod_flatten")
                    self._flattened_date = today
                    # Halt the adapter so no new signals can open positions after close
                    self._adapter.halt("eod_flatten")
                    log.info("EOD flatten complete: session P&L=$%.2f", result.get("session_pnl", 0))
                except Exception:
                    log.exception("EOD flatten failed!")

            # Safety verify at +4 min — skip if adapter is already halted (EOD done)
            if self._flattened_date == today and current_time >= self._verify_time:
                if not self._adapter._halted and not self._adapter.tracker.is_flat:
                    log.error("SAFETY: Still not flat at %s — forcing liquidation!", current_time)
                    await self._adapter.flatten("safety_verify")

            await asyncio.sleep(30)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
