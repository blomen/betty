"""
Extraction Scheduler

Manages per-provider extraction scheduling:
- Sharp (grouped): Pinnacle + Polymarket every 1 minute (run together)
- API soft (independent): Each provider on its own 5-minute loop (all parallel)
- Browser soft (independent): Each provider on its own 15-minute loop (parallel via pool manager)

Tuned for bare metal i7-7700 (4C/8T, 64GB RAM). All providers fire simultaneously —
browser concurrency managed by pool manager semaphores, not scheduler-level locks.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ProviderSchedule:
    """Schedule state for a single provider (or grouped sharp providers)."""

    provider_id: str  # Single provider or "sharp" for grouped
    category: str  # "sharp", "api_soft", "browser_soft", "browser_antibot"
    interval_seconds: int  # Cooldown AFTER completion
    providers: list[str] | None = None  # Only for grouped (sharp): list of providers
    running: bool = False
    task: asyncio.Task | None = field(default=None, repr=False)
    last_completed: datetime | None = None
    last_run_started: datetime | None = None
    run_count: int = 0
    last_error: str | None = None
    last_duration: float | None = None
    consecutive_failures: int = 0
    revival_attempts: int = 0
    reviving: bool = False
    stagger_delay: int = 0  # Seconds to wait before first run (prevents write stampede)


# Import update_provider_state (created in Task 3 — stub if not available yet)
try:
    from src.api.state import update_provider_state
except ImportError:

    def update_provider_state(provider_id: str, state: dict):
        """Stub — will be replaced by Task 3 implementation."""
        pass


class ExtractionScheduler:
    """
    Manages per-provider extraction scheduling.

    Each provider (or grouped sharp providers) runs on its own async loop
    with independent cooldown. Browser concurrency is managed by the pool
    manager's semaphores (max_browser_instances), not scheduler-level locks.

    Usage:
        scheduler = ExtractionScheduler()

        # Start all schedules from providers.yaml
        await scheduler.start_all()

        # Stop everything
        scheduler.stop_all()
    """

    # Schedule is considered stale if it hasn't run within this multiple of its interval.
    WATCHDOG_STALE_MULTIPLIER = 3
    MAX_REVIVAL_ATTEMPTS = 3
    REVIVAL_BACKOFFS = [1800, 7200, 21600]  # 30min, 2hr, 6hr

    def __init__(self, pipeline=None):
        self._pipeline = pipeline
        self._schedules: dict[str, ProviderSchedule] = {}
        self._boosts_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._settlement_task: asyncio.Task | None = None
        self._trading_reset_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        # Per-provider locks prevent the same provider from overlapping with itself.
        self._provider_locks: dict[str, asyncio.Lock] = {}
        # Browser concurrency controlled by pool manager semaphores (max_browser_instances=6).
        # Old _browser_lock serialized all browsers on slow Hetzner vCPU — removed for bare metal.
        # Legacy global lock kept for backward compat (manual API runs)
        self._run_lock = asyncio.Lock()
        # Sharp-ready gate: soft providers wait for sharp's first run before starting.
        # Without this, soft providers on startup find an empty DB (just purged) and extract
        # blindly with no Pinnacle baseline — wasting time on unmatchable events.
        self._sharp_ready = asyncio.Event()

    @property
    def pipeline(self):
        """Lazy-load pipeline if not provided."""
        if self._pipeline is None:
            from src.api.deps import get_pipeline

            self._pipeline = get_pipeline()
        return self._pipeline

    # ── Schedule management ────────────────────────────────────────────

    async def _start_schedule(self, schedule: ProviderSchedule):
        """Start a provider schedule loop."""
        if schedule.provider_id in self._schedules and self._schedules[schedule.provider_id].running:
            logger.warning(f"[Scheduler] Provider '{schedule.provider_id}' already running")
            return

        schedule.running = True
        self._schedules[schedule.provider_id] = schedule
        self._provider_locks[schedule.provider_id] = asyncio.Lock()

        # Register initial state so /progress shows this provider immediately
        update_provider_state(
            schedule.provider_id,
            {
                "running": False,
                "last_completed": None,
                "last_duration": None,
                "last_error": None,
                "category": schedule.category,
            },
        )

        logger.info(
            f"[Scheduler] Starting schedule '{schedule.provider_id}': "
            f"category={schedule.category}, "
            f"providers={schedule.providers or [schedule.provider_id]}, "
            f"interval={schedule.interval_seconds}s"
        )

        loop = asyncio.get_running_loop()
        schedule.task = asyncio.create_task(
            self._provider_loop(schedule),
            name=f"extraction-{schedule.provider_id}",
        )

        # Auto-restart on unexpected death
        def _on_schedule_done(task: asyncio.Task, sched=schedule, _loop=loop):
            if task.cancelled():
                return
            exc = task.exception()
            if exc and sched.running:
                logger.error(
                    f"[Scheduler:{sched.provider_id}] Schedule task died unexpectedly: {exc}. Auto-restarting in 10s..."
                )
                _loop.call_later(10, lambda: asyncio.ensure_future(self._restart_schedule(sched)))

        schedule.task.add_done_callback(_on_schedule_done)

    async def _restart_schedule(self, schedule: ProviderSchedule):
        """Restart a schedule that died unexpectedly."""
        logger.info(f"[Scheduler:{schedule.provider_id}] Restarting schedule...")
        loop = asyncio.get_running_loop()
        schedule.task = asyncio.create_task(
            self._provider_loop(schedule),
            name=f"extraction-{schedule.provider_id}",
        )

        def _on_restart_done(task: asyncio.Task, sched=schedule, _loop=loop):
            if task.cancelled():
                return
            exc = task.exception()
            if exc and sched.running:
                logger.error(f"[Scheduler:{sched.provider_id}] Schedule died again: {exc}. Auto-restarting in 30s...")
                _loop.call_later(30, lambda: asyncio.ensure_future(self._restart_schedule(sched)))

        schedule.task.add_done_callback(_on_restart_done)

    async def _attempt_revival(self, schedule: ProviderSchedule, backoff: int):
        """Attempt to revive a permanently failed provider after cooldown."""
        try:
            await asyncio.sleep(backoff)
            logger.info(
                f"[Watchdog] Attempting revival #{schedule.revival_attempts + 1} "
                f"for '{schedule.provider_id}' (backoff was {backoff}s)"
            )
            schedule.revival_attempts += 1
            schedule.running = True
            await self._restart_schedule(schedule)
        except asyncio.CancelledError:
            logger.info(f"[Watchdog] Revival cancelled for '{schedule.provider_id}'")
            schedule.reviving = False

    def _get_last_extraction_time(self, provider_ids: list[str]) -> datetime | None:
        """Check DB for the most recent Odds.updated_at for given provider(s)."""
        try:
            from sqlalchemy import func

            from src.db.models import Odds, get_session

            with get_session() as session:
                result = session.query(func.max(Odds.updated_at)).filter(Odds.provider_id.in_(provider_ids)).scalar()
                # SQLite stores naive datetimes in LOCAL time — convert to UTC
                if result and result.tzinfo is None:
                    result = result.astimezone(timezone.utc)
                return result
        except Exception as e:
            logger.warning(f"[Scheduler] Could not check last extraction time: {e}")
            return None

    def _get_last_completed_run(self, category: str, provider_ids: list[str]) -> datetime | None:
        """Check extraction_runs for the last COMPLETED run of this category.

        Only counts runs with end_time set (i.e., not interrupted).
        For grouped categories (sharp), matches by trigger=category.
        For ungrouped (api_soft), checks provider_run_metrics for the specific provider.
        """
        try:
            from sqlalchemy import text

            from src.db.models import get_session

            with get_session() as session:
                # Grouped categories (sharp) have trigger matching the category name
                row = session.execute(
                    text("SELECT MAX(end_time) FROM extraction_runs WHERE trigger = :cat AND end_time IS NOT NULL"),
                    {"cat": category},
                ).scalar()
                if row:
                    result = row if isinstance(row, datetime) else datetime.fromisoformat(str(row))
                    if result.tzinfo is None:
                        # SQLite stores naive datetimes in LOCAL time.
                        # Interpret as local then convert to UTC for correct arithmetic.
                        result = result.astimezone(timezone.utc)
                    return result

                # Fallback: check Odds.updated_at (for providers without extraction_runs)
                return self._get_last_extraction_time(provider_ids)
        except Exception as e:
            logger.warning(f"[Scheduler] Could not check last completed run: {e}")
            return self._get_last_extraction_time(provider_ids)

    async def _provider_loop(self, schedule: ProviderSchedule):
        """Extraction loop for a single provider (or grouped sharp providers)."""
        # Wait for sharp if not sharp category
        if schedule.category != "sharp" and not self._sharp_ready.is_set():
            logger.info(f"[Scheduler:{schedule.provider_id}] Waiting for sharp to complete first run...")
            try:
                await asyncio.wait_for(self._sharp_ready.wait(), timeout=120)
                logger.info(f"[Scheduler:{schedule.provider_id}] Sharp ready, starting extraction")
            except asyncio.TimeoutError:
                logger.warning(f"[Scheduler:{schedule.provider_id}] Sharp timeout (120s), starting anyway")

        # Minimal stagger to spread initial network load
        if schedule.stagger_delay > 0:
            logger.info(f"[Scheduler:{schedule.provider_id}] Staggering start by {schedule.stagger_delay}s")
            try:
                await asyncio.sleep(schedule.stagger_delay)
            except asyncio.CancelledError:
                return

        # On startup, check if the last extraction RUN completed successfully.
        # Uses extraction_runs.end_time (not Odds.updated_at) to avoid treating
        # interrupted extractions as "fresh" — a partial write shouldn't delay retry.
        providers = schedule.providers or [schedule.provider_id]
        last_completed = await asyncio.get_running_loop().run_in_executor(
            None, self._get_last_completed_run, schedule.category, providers
        )
        if last_completed:
            age_seconds = (datetime.now(timezone.utc) - last_completed).total_seconds()
            remaining = schedule.interval_seconds - age_seconds
            if remaining > 0:
                logger.info(
                    f"[Scheduler:{schedule.provider_id}] Last completed run {age_seconds:.0f}s ago, "
                    f"sleeping {remaining:.0f}s before first run"
                )
                schedule.last_completed = last_completed
                update_provider_state(
                    schedule.provider_id,
                    {
                        "running": False,
                        "last_completed": last_completed.isoformat(),
                        "category": schedule.category,
                    },
                )
                try:
                    await asyncio.sleep(remaining)
                except asyncio.CancelledError:
                    return
            else:
                logger.info(
                    f"[Scheduler:{schedule.provider_id}] Last completed run {age_seconds:.0f}s ago "
                    f"(stale), running immediately"
                )
        else:
            logger.info(f"[Scheduler:{schedule.provider_id}] No completed run found, running immediately")

        while schedule.running:
            start = datetime.now(timezone.utc)
            schedule.last_run_started = start
            try:
                async with self._provider_locks[schedule.provider_id]:
                    results = await self._run_provider_extraction(schedule)

                schedule.last_completed = datetime.now(timezone.utc)
                schedule.last_duration = (schedule.last_completed - start).total_seconds()
                schedule.last_error = None
                schedule.consecutive_failures = 0
                schedule.run_count += 1

                # Clear revival state on first successful extraction
                if schedule.reviving:
                    schedule.reviving = False
                    schedule.revival_attempts = 0
                    logger.info(f"[Scheduler:{schedule.provider_id}] Revival successful — back to normal")

                providers = schedule.providers or [schedule.provider_id]
                logger.info(
                    f"[Scheduler:{schedule.provider_id}] Run #{schedule.run_count} complete: "
                    f"{results.get('total_events', 0)} events, "
                    f"{results.get('total_odds', 0)} odds in {schedule.last_duration:.1f}s"
                )

                if schedule.category == "sharp" and not self._sharp_ready.is_set():
                    self._sharp_ready.set()
                    logger.info("[Scheduler] Sharp first run complete — soft providers unblocked")

            except asyncio.CancelledError:
                logger.info(f"[Scheduler:{schedule.provider_id}] Loop cancelled")
                break
            except Exception as e:
                schedule.last_error = str(e)
                err_lower = str(e).lower()
                is_transient_db = (
                    "deadlock" in err_lower
                    or "unique" in err_lower
                    or "database is locked" in err_lower
                    or ("expected to update" in err_lower and "0 were matched" in err_lower)
                )
                if is_transient_db:
                    # Transient DB conflicts (deadlocks, autoflush unique violations)
                    # should not accumulate toward permanent failure — they resolve
                    # on the next run when concurrent transactions no longer collide.
                    # Per-sport sessions commit independently, so most data is already
                    # persisted when this fires. Advance last_completed so the scheduler
                    # cools down normally instead of re-running immediately and
                    # amplifying the race with concurrent cleanup_stale().
                    schedule.last_completed = datetime.now(timezone.utc)
                    schedule.last_duration = (schedule.last_completed - start).total_seconds()
                    if schedule.category == "sharp" and not self._sharp_ready.is_set():
                        self._sharp_ready.set()
                        logger.info(
                            "[Scheduler] Sharp first run complete (transient DB error) — soft providers unblocked"
                        )
                    logger.warning(
                        f"[Scheduler:{schedule.provider_id}] Transient DB error (partial run treated as complete): {e}"
                    )
                else:
                    schedule.consecutive_failures += 1
                    logger.exception(f"[Scheduler:{schedule.provider_id}] Extraction failed: {e}")
            finally:
                update_provider_state(
                    schedule.provider_id,
                    {
                        "running": False,
                        "last_completed": schedule.last_completed.isoformat() if schedule.last_completed else None,
                        "last_duration": schedule.last_duration,
                        "last_error": schedule.last_error,
                        "category": schedule.category,
                    },
                )

            # Cooldown AFTER completion (full interval elapses after run finishes)
            try:
                await asyncio.sleep(schedule.interval_seconds)
            except asyncio.CancelledError:
                logger.info(f"[Scheduler:{schedule.provider_id}] Cooldown cancelled")
                break

        logger.info(f"[Scheduler:{schedule.provider_id}] Loop stopped (running={schedule.running})")

    async def _run_provider_extraction(self, schedule: ProviderSchedule) -> dict:
        """Run extraction for a provider schedule.

        Each extraction runs in a dedicated thread with its own event loop
        to keep the main async loop responsive for /health and API handlers.
        """
        from src.pipeline.orchestrator import ExtractionPipeline

        providers = schedule.providers or [schedule.provider_id]
        update_provider_state(schedule.provider_id, {"running": True, "category": schedule.category})

        def _run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            pipeline = ExtractionPipeline()
            try:
                return loop.run_until_complete(pipeline.run(providers=providers, tier_name=schedule.category))
            finally:
                with contextlib.suppress(Exception):
                    pipeline.session.close()
                loop.close()

        return await asyncio.to_thread(_run_in_thread)

    def stop_provider(self, provider_id: str):
        """Stop a specific provider schedule."""
        schedule = self._schedules.get(provider_id)
        if not schedule or not schedule.running:
            logger.warning(f"[Scheduler] Provider '{provider_id}' not running")
            return

        schedule.running = False
        if schedule.task:
            schedule.task.cancel()
            schedule.task = None
        schedule.reviving = False

        logger.info(f"[Scheduler] Stopped provider '{provider_id}' after {schedule.run_count} runs")

    # Keep stop_tier as alias for backward compat with API routes
    def stop_tier(self, tier_name: str):
        """Stop all providers in a category (backward compat)."""
        stopped = []
        for sched_id, sched in list(self._schedules.items()):
            if sched.category == tier_name or sched.provider_id == tier_name:
                self.stop_provider(sched_id)
                stopped.append(sched_id)
        if not stopped:
            logger.warning(f"No providers found for category '{tier_name}'")

    # ── Convenience: start/stop all ──────────────────────────────────

    def _load_scheduling_config(self) -> dict:
        """Load extraction scheduling config from providers.yaml."""
        from ..paths import get_config_path

        config_path = get_config_path("providers.yaml")
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return config.get("extraction_scheduling", {})

    def _get_disabled_providers(self) -> set:
        """Get providers disabled by user in settings for the active profile."""
        from ..db.models import Profile, ProviderExtractionSetting, get_session

        session = get_session()
        try:
            profile = (
                session.query(Profile)
                .filter(
                    Profile.is_active == True  # noqa: E712
                )
                .first()
            )
            if not profile:
                return set()
            return {
                s.provider_id
                for s in session.query(ProviderExtractionSetting)
                .filter(
                    ProviderExtractionSetting.profile_id == profile.id,
                    ProviderExtractionSetting.enabled == False,  # noqa: E712
                )
                .all()
            }
        finally:
            session.close()

    async def start_all(self):
        """Start all extraction schedules from providers.yaml config.

        Reads extraction_scheduling from providers.yaml — the single source of truth
        for which providers run in each category and at what interval.
        Filters out providers disabled in settings.

        For grouped categories (sharp): creates one ProviderSchedule with providers=[...].
        For ungrouped categories (api_soft, browser_soft): creates one ProviderSchedule per provider.
        """
        scheduling = self._load_scheduling_config()
        disabled = self._get_disabled_providers()
        if disabled:
            logger.info(f"[Scheduler] Disabled providers (from settings): {disabled}")

        for category_name, category_config in scheduling.items():
            providers = [p for p in category_config.get("providers", []) if p not in disabled]
            if not providers:
                logger.warning(f"[Scheduler] Category '{category_name}' has no enabled providers, skipping")
                continue

            interval_minutes = category_config.get("interval_minutes", 60)
            interval_seconds = interval_minutes * 60
            grouped = category_config.get("grouped", False)

            if grouped:
                # One schedule for all providers in category (e.g. sharp)
                schedule = ProviderSchedule(
                    provider_id=category_name,
                    category=category_name,
                    interval_seconds=interval_seconds,
                    providers=providers,
                )
                await self._start_schedule(schedule)
            else:
                # One schedule per provider (independent loops).
                # Browser-tier providers need a wider stagger (15s) because
                # Playwright/CDP driver bootstrap is racy when multiple
                # browsers launch in the same second post-restart — observed
                # 4 browser providers all hit "Connection closed while reading
                # from the driver" at the exact same UTC instant during a
                # rebuild. API tiers can stay at the original 2s since
                # PostgreSQL handles concurrent writes natively.
                browser_categories = {"browser_soft", "browser_antibot"}
                stagger_step = 15 if category_name in browser_categories else 2
                for i, provider_id in enumerate(providers):
                    schedule = ProviderSchedule(
                        provider_id=provider_id,
                        category=category_name,
                        interval_seconds=interval_seconds,
                        stagger_delay=i * stagger_step,
                    )
                    await self._start_schedule(schedule)

        # Boosts — DISABLED (noisy browser output, not needed right now)
        # await self.start_boosts_tier(interval_seconds=3600)

        # Cleanup tier — purge stale events/odds every 6 hours
        await self.start_cleanup_tier()

        # CLV snapshot tier — snapshot closing odds every 2 min
        await self.start_settlement_tier()

        # Trading daily/weekly auto-reset (checks every 60s, acts at market boundaries)
        await self.start_trading_reset_tier()

        # Provider health watchdog — logs CRITICAL when any schedule stops running
        self._start_watchdog()

    def _start_watchdog(self):
        """Start background watchdog that monitors schedule liveness."""
        if self._watchdog_task and not self._watchdog_task.done():
            return
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def _check_schedules_once(self):
        """Single watchdog tick — check all schedules for issues.

        IMPORTANT: This is a refactoring of the existing _watchdog_loop inner body.
        All existing checks are preserved.
        """
        now = datetime.now(timezone.utc)
        for provider_id, schedule in self._schedules.items():
            # ── NEW: Revival scheduling for permanently failed providers ──
            if schedule.consecutive_failures >= 3 and not schedule.running:
                if schedule.reviving:
                    continue  # Revival already in progress
                if schedule.revival_attempts >= self.MAX_REVIVAL_ATTEMPTS:
                    continue  # Exhausted all attempts
                # Schedule revival
                schedule.reviving = True
                backoff = self.REVIVAL_BACKOFFS[min(schedule.revival_attempts, len(self.REVIVAL_BACKOFFS) - 1)]
                logger.info(
                    f"[Watchdog] Scheduling revival #{schedule.revival_attempts + 1} for '{provider_id}' in {backoff}s"
                )
                asyncio.create_task(self._attempt_revival(schedule, backoff))
                continue

            # ── EXISTING (modified): Mark permanently failed after 3+ consecutive failures ──
            # Added `and not schedule.reviving` guard to prevent re-killing a reviving provider
            if schedule.consecutive_failures >= 3 and schedule.running and not schedule.reviving:
                logger.critical(
                    f"[Watchdog] Provider '{provider_id}' has {schedule.consecutive_failures} "
                    f"consecutive failures — marking as permanently failed"
                )
                schedule.running = False
                if schedule.task:
                    schedule.task.cancel()
                    schedule.task = None
                update_provider_state(
                    provider_id,
                    {
                        "running": False,
                        "permanently_failed": True,
                        "last_error": schedule.last_error,
                        "consecutive_failures": schedule.consecutive_failures,
                    },
                )
                continue

            # ── EXISTING: Check if the asyncio task is still alive ──
            if schedule.running and (schedule.task is None or schedule.task.done()):
                exc = schedule.task.exception() if schedule.task and not schedule.task.cancelled() else None
                logger.critical(
                    f"[Watchdog] Provider '{provider_id}' task is DEAD "
                    f"(running={schedule.running}, "
                    f"task_done={schedule.task.done() if schedule.task else 'None'}, "
                    f"exception={exc}). Forcing restart..."
                )
                await self._restart_schedule(schedule)
                continue

            # ── EXISTING: Check if the schedule is overdue (stale) ──
            if schedule.running and schedule.last_completed:
                stale_threshold = schedule.interval_seconds * self.WATCHDOG_STALE_MULTIPLIER
                elapsed = (now - schedule.last_completed).total_seconds()
                if elapsed > stale_threshold:
                    # Use a higher threshold before force-restarting (5x interval)
                    # to avoid killing long-but-progressing extractions
                    force_restart_threshold = schedule.interval_seconds * 5
                    # Don't kill a run that started recently. Floors are category-aware
                    # because realistic completion time differs by an order of magnitude:
                    # sharp Pinnacle naturally takes 600-800s under proxy load, signal
                    # providers (Cloudbet, Marathon) take 1700-1900s, browser tiers
                    # routinely take 2000-2500s. The previous flat 600s floor was killing
                    # Pinnacle runs *just as* they published their baseline, breaking the
                    # _sharp_ready gate and starving every soft provider.
                    run_age = (
                        (now - schedule.last_run_started).total_seconds() if schedule.last_run_started else float("inf")
                    )
                    category_floors = {
                        "sharp": 1500,  # Pinnacle worst-case ≈ 5 sport_timeouts
                        "polymarket": 1500,
                        "kalshi": 1500,
                        "signal_international": 2400,  # Cloudbet observed 1700-1900s
                        "api_soft": 1200,  # Kambi/Altenar/Gecko observed 600-700s
                        "browser_soft": 3000,  # Tipwin observed 2300s+
                        "browser_antibot": 3600,  # Coolbet/ComeOn worst-case
                    }
                    min_run_duration = max(category_floors.get(schedule.category, 1200), force_restart_threshold)
                    if elapsed > force_restart_threshold and run_age > min_run_duration:
                        logger.critical(
                            f"[Watchdog] Provider '{provider_id}' is STUCK — "
                            f"last completed {elapsed:.0f}s ago, current run {run_age:.0f}s old "
                            f"(threshold: {min_run_duration:.0f}s). "
                            f"Force-cancelling and restarting..."
                        )
                        if schedule.task and not schedule.task.done():
                            schedule.task.cancel()
                        await self._restart_schedule(schedule)
                    else:
                        logger.warning(
                            f"[Watchdog] Provider '{provider_id}' is STALE — "
                            f"last completed {elapsed:.0f}s ago (threshold: {stale_threshold:.0f}s). "
                            f"run_count={schedule.run_count}"
                        )

            # Starvation detection for browser providers
            if (
                schedule.category in ("browser_soft", "browser_antibot")
                and schedule.running
                and schedule.last_completed
            ):
                starvation_threshold = schedule.interval_seconds * 2
                elapsed = (now - schedule.last_completed).total_seconds()
                if elapsed > starvation_threshold:
                    logger.critical(
                        f"[Watchdog] Browser provider '{provider_id}' starving — "
                        f"last completed {elapsed:.0f}s ago "
                        f"(threshold: {starvation_threshold:.0f}s)"
                    )

            # ── EXISTING: Check if a schedule that should be running hasn't started yet ──
            if schedule.running and schedule.run_count == 0 and schedule.last_completed is None:
                pass  # Handled by stale check above once interval elapses

    # Python's memory allocator doesn't return pages to the OS after large
    # allocations (arena fragmentation). Over hours of extraction runs, each
    # creating ExtractionPipeline + ORM objects + browser contexts in threads,
    # RSS grows monotonically. The only reliable fix is process restart.
    # Docker restart: unless-stopped brings us back in seconds.
    MEMORY_LIMIT_GB = 40  # Exit when RSS exceeds this (Docker cap is 48GB; 8GB margin under kernel OOM)

    def _check_memory(self):
        """Check if process RSS exceeds limit and exit if so."""
        try:
            import resource

            rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # Linux: KB → bytes
            rss_gb = rss_bytes / (1024**3)
            if rss_gb > self.MEMORY_LIMIT_GB:
                logger.critical(
                    f"[Watchdog] RSS {rss_gb:.1f}GB exceeds {self.MEMORY_LIMIT_GB}GB limit. "
                    f"Exiting for Docker restart to reclaim memory."
                )
                import os

                os._exit(1)  # Hard exit — Docker restarts us
        except Exception as e:
            logger.warning(f"[Watchdog] Memory check failed: {e}")

    async def _watchdog_loop(self):
        """Periodically check that all schedules are running and not stale."""
        await asyncio.sleep(30)  # Initial delay before first check
        while True:
            try:
                await self._check_schedules_once()
                self._check_memory()
            except Exception as e:
                logger.error(f"[Watchdog] Error: {e}", exc_info=True)
            await asyncio.sleep(60)

    def stop_all(self):
        """Stop all running schedules."""
        # Cancel any pending revival tasks first
        for provider_id, schedule in self._schedules.items():
            if schedule.reviving:
                schedule.reviving = False
        for provider_id in list(self._schedules.keys()):
            if self._schedules[provider_id].running:
                self.stop_provider(provider_id)
        # Also stop boosts task
        if self._boosts_task and not self._boosts_task.done():
            self._boosts_task.cancel()
            self._boosts_task = None
        # Also stop cleanup task
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            self._cleanup_task = None
        # Also stop settlement task
        if self._settlement_task and not self._settlement_task.done():
            self._settlement_task.cancel()
            self._settlement_task = None
        # Also stop trading reset task
        if self._trading_reset_task and not self._trading_reset_task.done():
            self._trading_reset_task.cancel()
            self._trading_reset_task = None
        # Also stop watchdog
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            self._watchdog_task = None

    # ── Boosts tier (standalone, no pipeline) ─────────────────────────

    async def start_boosts_tier(self, interval_seconds: int = 3600):
        """Start the oddsboost scraper on a fixed interval.

        Runs every interval_seconds after each completion. No trigger needed —
        just a simple timer loop.
        """
        if self._boosts_task and not self._boosts_task.done():
            logger.warning("[Scheduler] Boosts tier already running")
            return

        logger.info(f"[Scheduler] Starting boosts tier: interval={interval_seconds}s")
        self._boosts_task = asyncio.create_task(self._boosts_loop(interval_seconds))

    async def _boosts_loop(self, interval_seconds: int):
        """Recurring loop for oddsboost scraping.

        Waits for sharp to complete first (Pinnacle data needed for EV enrichment),
        then runs on a fixed interval after each completion.
        """
        # Wait for sharp first (Pinnacle data needed for EV enrichment)
        if not self._sharp_ready.is_set():
            logger.info("[Scheduler:boosts] Waiting for sharp before first run...")
            try:
                await asyncio.wait_for(self._sharp_ready.wait(), timeout=120)
            except asyncio.TimeoutError:
                logger.warning("[Scheduler:boosts] Sharp timeout, starting anyway")

        while True:
            try:
                logger.info("[Scheduler:boosts] Starting boost scrape")
                await self._run_boost_scrape()
                logger.info("[Scheduler:boosts] Boost scrape complete")
                update_provider_state(
                    "boosts",
                    {"running": False, "last_completed": datetime.now(timezone.utc).isoformat(), "category": "boosts"},
                )
            except asyncio.CancelledError:
                logger.info("[Scheduler:boosts] Loop cancelled")
                update_provider_state("boosts", {"running": False, "category": "boosts"})
                break
            except Exception as e:
                logger.error(f"[Scheduler:boosts] Error: {e}", exc_info=True)
                update_provider_state("boosts", {"running": False, "last_error": str(e), "category": "boosts"})

            # Fixed cooldown after completion
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                logger.info("[Scheduler:boosts] Cooldown cancelled")
                update_provider_state("boosts", {"running": False, "category": "boosts"})
                break

    async def _run_boost_scrape(self):
        """Execute the boost scraper in a thread executor."""
        import sys
        from dataclasses import asdict

        # Ensure scripts/ package is importable (lives in backend/)
        _root = str(Path(__file__).resolve().parent.parent.parent)
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from scripts.scrape_specials import save_specials, scrape_all

        from src.analysis.ev_enrichment import (
            deduplicate_specials,
            enrich_specials_with_ev,
            filter_expired,
            store_specials_to_db,
        )
        from src.db.models import get_session

        update_provider_state("boosts", {"running": True, "category": "boosts"})

        loop = asyncio.get_running_loop()
        specials, run_log = await loop.run_in_executor(None, lambda: scrape_all(verbose=False))

        session = get_session()
        try:
            if specials:
                # JSON backup (kept for transition)
                save_specials(specials)

                # EV enrichment + DB storage (full replace: DELETE all + INSERT new)
                specials_dicts = filter_expired([asdict(s) for s in specials])
                specials_dicts = deduplicate_specials(specials_dicts)
                specials_dicts = enrich_specials_with_ev(specials_dicts, session)
                # Re-filter after event matching (matched events may now show as expired)
                specials_dicts = filter_expired(specials_dicts, db=session)

                # LLM probability research (async — Brave Search + Claude Haiku)
                from src.analysis.llm_enrichment import enrich_specials_with_llm

                specials_dicts = await enrich_specials_with_llm(specials_dicts, session)

                count = store_specials_to_db(specials_dicts, session)
                ev_count = sum(1 for s in specials_dicts if s.get("is_positive_ev"))
                llm_count = sum(1 for s in specials_dicts if s.get("llm_probability") is not None)
                logger.info(
                    f"[Scheduler:boosts] Stored {count} boosts to DB ({ev_count} +EV, {llm_count} LLM-researched)"
                )
            else:
                # Scrape returned empty — still purge expired boosts from DB
                self._purge_expired_boosts(session)
                logger.info("[Scheduler:boosts] No boosts scraped, purged expired from DB")
        except Exception as e:
            logger.error(f"[Scheduler:boosts] DB storage failed: {e}", exc_info=True)
            with contextlib.suppress(Exception):
                session.rollback()
        finally:
            with contextlib.suppress(Exception):
                session.close()

        # Persist extraction log to DB
        self._persist_boost_log(run_log)

    @staticmethod
    def _purge_expired_boosts(session):
        """Remove boosts from DB whose event has already started."""
        from src.db.models import SpecialOdds

        now_iso = datetime.now(timezone.utc).isoformat()
        deleted = (
            session.query(SpecialOdds)
            .filter(SpecialOdds.event_time.isnot(None), SpecialOdds.event_time <= now_iso)
            .delete(synchronize_session="fetch")
        )
        if deleted:
            session.commit()
            logger.info(f"[Scheduler:boosts] Purged {deleted} expired boosts from DB")

    def _persist_boost_log(self, run_log, max_runs: int = 10):
        """Persist boost extraction log to DB. Keeps last `max_runs` runs."""
        from datetime import datetime as dt
        from datetime import timezone

        from sqlalchemy import func

        from src.db.models import BoostExtractionLog, get_session

        try:
            session = get_session()
            scraped_at = dt.fromisoformat(run_log.scraped_at) if run_log.scraped_at else dt.now(timezone.utc)

            # Prune old boost runs beyond max_runs (keep N-1, adding 1 new = N total)
            # Each run shares the same run_id, so count distinct run_ids
            distinct_run_ids = (
                session.query(BoostExtractionLog.run_id, func.max(BoostExtractionLog.scraped_at).label("latest"))
                .group_by(BoostExtractionLog.run_id)
                .order_by(func.max(BoostExtractionLog.scraped_at).desc())
                .all()
            )
            if len(distinct_run_ids) >= max_runs:
                stale_run_ids = [r.run_id for r in distinct_run_ids[max_runs - 1 :]]
                session.query(BoostExtractionLog).filter(BoostExtractionLog.run_id.in_(stale_run_ids)).delete(
                    synchronize_session="fetch"
                )

            for pl in run_log.providers:
                session.add(
                    BoostExtractionLog(
                        run_id=run_log.run_id,
                        scraped_at=scraped_at,
                        provider_id=pl.provider_id,
                        scraper_type=pl.scraper_type,
                        status=pl.status,
                        duration_seconds=pl.duration_seconds,
                        boosts_found=pl.boosts_found,
                        error_message=pl.error_message,
                        run_total_boosts=run_log.total_boosts,
                        run_duration_seconds=run_log.duration_seconds,
                    )
                )

            session.commit()
            logger.info(
                f"[Scheduler:boosts] Persisted log: {len(run_log.providers)} providers, {run_log.total_boosts} boosts in {run_log.duration_seconds:.1f}s"
            )
        except Exception as e:
            logger.error(f"[Scheduler:boosts] Failed to persist log: {e}")
            with contextlib.suppress(Exception):
                session.rollback()
        finally:
            with contextlib.suppress(Exception):
                session.close()

    # ── Trading daily/weekly reset ──────────────────────────────────

    async def start_trading_reset_tier(self, check_interval: int = 60):
        """Auto-reset trading accounts at market boundaries.

        Checks every 60s. Resets daily counters once per day (after midnight UTC,
        i.e. ~7pm ET / before US pre-market). Resets weekly on Monday.
        """
        if self._trading_reset_task and not self._trading_reset_task.done():
            logger.warning("[Scheduler] Trading reset tier already running")
            return

        logger.info(f"[Scheduler] Starting trading reset tier: check_interval={check_interval}s")
        self._trading_reset_task = asyncio.create_task(self._trading_reset_loop(check_interval))

    async def _trading_reset_loop(self, check_interval: int):
        """Recurring check for daily/weekly trading resets."""
        last_daily_reset: str | None = None  # Track date of last daily reset
        last_weekly_reset: str | None = None  # Track week-string of last weekly reset

        while True:
            try:
                now = datetime.now(timezone.utc)
                today_str = now.strftime("%Y-%m-%d")
                week_str = now.strftime("%Y-W%W")

                # Daily reset — once per UTC day
                if last_daily_reset != today_str:
                    try:
                        from src.db.models import get_session
                        from src.services.trading_service import TradingService

                        session = get_session()
                        try:
                            svc = TradingService(session)
                            result = svc.auto_reset_daily()
                            last_daily_reset = today_str
                            if result["reset_accounts"] > 0:
                                logger.info(
                                    f"[Scheduler:trading_reset] Daily reset: {result['reset_accounts']} accounts"
                                )
                        finally:
                            session.close()
                    except Exception as e:
                        logger.error(f"[Scheduler:trading_reset] Daily reset failed: {e}")

                # Weekly reset — once per UTC week (Monday = weekday 0)
                if now.weekday() == 0 and last_weekly_reset != week_str:
                    try:
                        from src.db.models import get_session
                        from src.services.trading_service import TradingService

                        session = get_session()
                        try:
                            svc = TradingService(session)
                            result = svc.auto_reset_weekly()
                            last_weekly_reset = week_str
                            if result["reset_accounts"] > 0:
                                logger.info(
                                    f"[Scheduler:trading_reset] Weekly reset: {result['reset_accounts']} accounts"
                                )
                        finally:
                            session.close()
                    except Exception as e:
                        logger.error(f"[Scheduler:trading_reset] Weekly reset failed: {e}")

            except asyncio.CancelledError:
                logger.info("[Scheduler:trading_reset] Loop cancelled")
                break
            except Exception as e:
                logger.error(f"[Scheduler:trading_reset] Error: {e}", exc_info=True)

            try:
                await asyncio.sleep(check_interval)
            except asyncio.CancelledError:
                break

    # ── Cleanup tier (data retention) ────────────────────────────────

    async def start_cleanup_tier(self, interval_seconds: int = 21600):
        """Start periodic data retention cleanup (every 6 hours).

        Purges stale events, odds, and opportunities to prevent unbounded
        DB growth.  Runs independently — no pipeline lock needed.
        """
        if self._cleanup_task and not self._cleanup_task.done():
            logger.warning("[Scheduler] Cleanup tier already running")
            return

        logger.info(f"[Scheduler] Starting cleanup tier: interval={interval_seconds}s")
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(interval_seconds))

    async def _cleanup_loop(self, interval_seconds: int):
        """Recurring loop for data retention cleanup."""
        # Wait before first run — let extraction populate data first
        try:
            await asyncio.sleep(300)  # 5 min initial delay
        except asyncio.CancelledError:
            return

        while True:
            try:
                logger.info("[Scheduler:cleanup] Starting data retention cleanup")
                stats = await self._run_cleanup()
                logger.info(
                    f"[Scheduler:cleanup] Done: "
                    f"{stats.get('past_events_deleted', 0)} events, "
                    f"{stats.get('past_odds_deleted', 0)} odds, "
                    f"{stats.get('inactive', 0)} inactive opps, "
                    f"{stats.get('past_events', 0)} past-event opps removed"
                )
            except asyncio.CancelledError:
                logger.info("[Scheduler:cleanup] Loop cancelled")
                break
            except Exception as e:
                logger.error(f"[Scheduler:cleanup] Error: {e}", exc_info=True)

            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break

    async def _run_cleanup(self) -> dict:
        """Execute data retention cleanup in a thread executor."""
        from src.db.models import Bet, Event, Odds, Opportunity, get_session

        loop = asyncio.get_running_loop()

        def _do_cleanup() -> dict:
            stats = {
                "inactive": 0,
                "orphaned": 0,
                "past_events": 0,
                "past_events_deleted": 0,
                "past_odds_deleted": 0,
                "deactivated": 0,
            }
            session = get_session()
            try:
                from sqlalchemy import or_

                now = datetime.now(timezone.utc)

                # 1. Delete inactive opportunities
                stats["inactive"] = session.query(Opportunity).filter(not Opportunity.is_active).delete()

                # 2. Delete orphaned opportunities (event doesn't exist)
                valid_event_subq = session.query(Event.id).subquery()
                stats["orphaned"] = (
                    session.query(Opportunity)
                    .filter(~Opportunity.event_id.in_(session.query(valid_event_subq)))
                    .delete(synchronize_session=False)
                )

                # 3. Delete opportunities for past events (keep live/finished for settlement)
                #    Grace period: 48h after start_time to avoid racing with Polymarket
                #    which actively updates match_status on recently-started events
                past_event_subq = (
                    session.query(Event.id)
                    .filter(
                        Event.start_time < now - timedelta(hours=48),
                        or_(Event.match_status.is_(None), ~Event.match_status.in_(["live", "finished"])),
                    )
                    .subquery()
                )
                stats["past_events"] = (
                    session.query(Opportunity)
                    .filter(Opportunity.event_id.in_(session.query(past_event_subq)))
                    .delete(synchronize_session=False)
                )

                # 4. Delete past events + their odds (bulk)
                #    Preserve events that have bets OR are live/finished
                #    Grace period: 48h to avoid racing with Polymarket updates
                past_event_ids = [
                    row[0]
                    for row in session.query(Event.id)
                    .filter(
                        Event.start_time < now - timedelta(hours=48),
                        or_(Event.match_status.is_(None), ~Event.match_status.in_(["live", "finished"])),
                    )
                    .all()
                ]
                if past_event_ids:
                    # Safety: query ALL bets (not just past_event_ids) to ensure
                    # we never delete an event referenced by any bet
                    event_ids_with_bets = set(
                        row[0] for row in session.query(Bet.event_id).filter(Bet.event_id.isnot(None)).distinct().all()
                    )
                    deletable_ids = [eid for eid in past_event_ids if eid not in event_ids_with_bets]
                    if deletable_ids:
                        # Bulk delete odds first, then events (batched)
                        for i in range(0, len(deletable_ids), 500):
                            batch = deletable_ids[i : i + 500]
                            stats["past_odds_deleted"] += (
                                session.query(Odds).filter(Odds.event_id.in_(batch)).delete(synchronize_session=False)
                            )
                            stats["past_events_deleted"] += (
                                session.query(Event).filter(Event.id.in_(batch)).delete(synchronize_session=False)
                            )

                # 5. Skip blanket deactivation — the analyzer handles incremental
                #    deactivation per-event during extraction.  Deactivating all here
                #    would wipe valid data between extraction runs / across restarts.

                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

            return stats

        return await loop.run_in_executor(None, _do_cleanup)

    # ── CLV snapshot tier (closing odds for started events) ───

    async def start_settlement_tier(self, interval_seconds: int = 120):
        """Start periodic CLV snapshots (every 2 minutes).

        Snapshots closing odds for started events.
        Runs independently — no pipeline lock needed, just a DB session.
        """
        if self._settlement_task and not self._settlement_task.done():
            logger.warning("[Scheduler] Settlement tier already running")
            return

        logger.info(f"[Scheduler] Starting settlement tier: interval={interval_seconds}s")
        self._settlement_task = asyncio.create_task(self._settlement_loop(interval_seconds))

    async def _settlement_loop(self, interval_seconds: int):
        """Recurring loop for CLV snapshots."""
        # Wait before first run — let extraction populate data first
        try:
            await asyncio.sleep(120)  # 2 min initial delay
        except asyncio.CancelledError:
            return

        while True:
            try:
                self._run_settlement()
            except asyncio.CancelledError:
                logger.info("[Scheduler:settlement] Loop cancelled")
                break
            except Exception as e:
                logger.error(f"[Scheduler:settlement] Error: {e}", exc_info=True)

            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break

    def _run_settlement(self) -> dict:
        """Snapshot closing odds for CLV tracking."""
        from src.db.models import get_session
        from src.services.bet_service import BetService

        session = get_session()
        try:
            bet_service = BetService(session)
            clv_stats = bet_service.snapshot_closing_odds()
            session.commit()

            if clv_stats.get("updated", 0) > 0:
                logger.info(
                    f"[Scheduler:settlement] CLV snapshot: {clv_stats['updated']}/{clv_stats['processed']} bets updated"
                )

            return clv_stats
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Legacy interface (backwards-compatible) ────────────────────────

    async def start_continuous(
        self,
        providers: list[str] = None,
        interval_seconds: int = 300,
        on_complete: Callable[[dict], None] = None,
    ):
        """Start continuous sharp extraction (legacy interface).

        This is called from app startup. Now starts ALL schedules.
        """
        await self.start_all()

    def stop(self):
        """Stop all schedules (legacy interface)."""
        self.stop_all()

    async def run_once(self, providers: list[str] = None) -> dict:
        """Run a single extraction (for manual/on-demand)."""
        if providers is None:
            providers = self.pipeline.engine.get_enabled_providers()

        logger.info(f"[Scheduler] Running one-time extraction: {len(providers)} providers")

        # Create a temporary schedule for the one-time run
        schedule = ProviderSchedule(
            provider_id="manual",
            category="manual",
            interval_seconds=0,
            providers=providers,
        )

        async with self._run_lock:
            results = await self._run_provider_extraction(schedule)
        return results

    # ── State ──────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        """Check if any schedule is running."""
        return any(s.running for s in self._schedules.values())

    @property
    def last_run(self) -> datetime | None:
        """Get most recent run across all schedules."""
        runs = [s.last_completed for s in self._schedules.values() if s.last_completed]
        return max(runs) if runs else None

    @property
    def run_count(self) -> int:
        """Get total runs across all schedules."""
        return sum(s.run_count for s in self._schedules.values())

    @property
    def interval_seconds(self) -> int | None:
        """Get sharp schedule interval (legacy)."""
        sharp = self._schedules.get("sharp")
        return sharp.interval_seconds if sharp else None

    @property
    def providers(self) -> list[str] | None:
        """Get sharp schedule providers (legacy)."""
        sharp = self._schedules.get("sharp")
        return sharp.providers if sharp else None

    def get_status(self) -> dict:
        """Get scheduler status for all schedules, including health info."""
        now = datetime.now(timezone.utc)
        schedules = {}
        for provider_id, schedule in self._schedules.items():
            next_run = None
            seconds_since_last_run = None
            is_overdue = False

            if schedule.running and schedule.last_completed:
                next_run_dt = schedule.last_completed + timedelta(seconds=schedule.interval_seconds)
                next_run = next_run_dt.isoformat()
                seconds_since_last_run = (now - schedule.last_completed).total_seconds()
                is_overdue = seconds_since_last_run > (schedule.interval_seconds * self.WATCHDOG_STALE_MULTIPLIER)

            task_alive = schedule.task is not None and not schedule.task.done() if schedule.task else False

            schedules[provider_id] = {
                "running": schedule.running,
                "task_alive": task_alive,
                "category": schedule.category,
                "providers": schedule.providers or [schedule.provider_id],
                "interval_seconds": schedule.interval_seconds,
                "last_completed": schedule.last_completed.isoformat() if schedule.last_completed else None,
                "last_duration": schedule.last_duration,
                "last_error": schedule.last_error,
                "consecutive_failures": schedule.consecutive_failures,
                "run_count": schedule.run_count,
                "next_run": next_run,
                "seconds_since_last_run": round(seconds_since_last_run) if seconds_since_last_run is not None else None,
                "is_overdue": is_overdue,
            }

        # Build legacy tiers dict for backward compat
        tiers = {}
        for provider_id, sched_status in schedules.items():
            cat = sched_status["category"]
            if cat not in tiers:
                tiers[cat] = {
                    "running": False,
                    "providers": [],
                    "interval_seconds": sched_status["interval_seconds"],
                    "last_run": None,
                    "run_count": 0,
                }
            tier = tiers[cat]
            tier["providers"].extend(sched_status["providers"])
            tier["running"] = tier["running"] or sched_status["running"]
            tier["run_count"] += sched_status["run_count"]
            if sched_status["last_completed"]:
                if tier["last_run"] is None or sched_status["last_completed"] > tier["last_run"]:
                    tier["last_run"] = sched_status["last_completed"]

        return {
            "running": self.running,
            "schedules": schedules,
            "tiers": tiers,
            # Legacy fields
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "run_count": self.run_count,
            "interval_seconds": self.interval_seconds,
            "providers": self.providers,
            "next_run": None,  # Deprecated — use schedules[x].next_run
        }

    # ── Internal ───────────────────────────────────────────────────────

    def _clear_extraction_state(self):
        """Clear extraction state on error."""
        try:
            from src.api.state import update_extraction_state

            update_extraction_state(running=False)
        except Exception:
            pass


# Global scheduler instance
_scheduler: ExtractionScheduler | None = None


def get_scheduler() -> ExtractionScheduler:
    """Get or create global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = ExtractionScheduler()
    return _scheduler


def reset_scheduler():
    """Reset global scheduler (for testing)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.stop_all()
    _scheduler = None
