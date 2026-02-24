"""
Extraction Scheduler

Manages tiered extraction:
- Sharp (continuous): Pinnacle + Polymarket every 3 minutes
- API soft: Kambi, Altenar, Gecko V2, Vbet every 60 minutes
- Browser soft: Spectate, ComeOn, Snabbare, 10Bet, Interwetten, Coolbet, Tipwin every 120 minutes

All tiers run on startup, then repeat at their configured interval.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class TierState:
    """Tracks state for a single extraction tier."""
    name: str
    providers: list[str]
    interval_seconds: int
    running: bool = False
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    last_run: Optional[datetime] = None
    run_count: int = 0


class ExtractionScheduler:
    """
    Manages tiered extraction scheduling.

    Supports multiple named tiers running concurrently, each with its own
    provider list and interval. Tiers are independent — a slow browser
    extraction doesn't block the next sharp refresh.

    Usage:
        scheduler = ExtractionScheduler()

        # Start all tiers
        await scheduler.start_all()

        # Or start individual tiers
        await scheduler.start_tier("sharp", ["pinnacle", "polymarket"], 180)
        await scheduler.start_tier("api_soft", [...], 3600)

        # Stop everything
        scheduler.stop_all()
    """

    def __init__(self, pipeline=None):
        self._pipeline = pipeline
        self._tiers: dict[str, TierState] = {}
        self._boosts_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._settlement_task: Optional[asyncio.Task] = None
        self._trading_reset_task: Optional[asyncio.Task] = None
        # Lock ensures only one tier runs at a time — the pipeline shares
        # a single DB session and event_cache, so concurrent runs corrupt state.
        self._run_lock = asyncio.Lock()

    @property
    def pipeline(self):
        """Lazy-load pipeline if not provided."""
        if self._pipeline is None:
            from src.api.deps import get_pipeline
            self._pipeline = get_pipeline()
        return self._pipeline

    # ── Tier management ────────────────────────────────────────────────

    async def start_tier(
        self,
        name: str,
        providers: list[str],
        interval_seconds: int,
        run_immediately: bool = True,
    ):
        """Start a named extraction tier.

        Args:
            name: Tier name (e.g. "sharp", "api_soft", "browser_soft")
            providers: Provider IDs to extract
            interval_seconds: Seconds between runs
            run_immediately: Run first extraction immediately (default: True)
        """
        if name in self._tiers and self._tiers[name].running:
            logger.warning(f"[Scheduler] Tier '{name}' already running")
            return

        tier = TierState(
            name=name,
            providers=providers,
            interval_seconds=interval_seconds,
            running=True,
        )
        self._tiers[name] = tier

        logger.info(
            f"[Scheduler] Starting tier '{name}': "
            f"providers={providers}, interval={interval_seconds}s, "
            f"run_immediately={run_immediately}"
        )

        tier.task = asyncio.create_task(
            self._tier_loop(tier, run_immediately)
        )

    async def _tier_loop(self, tier: TierState, run_immediately: bool):
        """Extraction loop for a single tier."""
        # Optionally wait before first run (e.g. stagger browser tier)
        if not run_immediately:
            logger.info(f"[Scheduler:{tier.name}] Waiting {tier.interval_seconds}s before first run")
            await asyncio.sleep(tier.interval_seconds)

        while tier.running:
            try:
                start_time = datetime.now(timezone.utc)
                tier.run_count += 1

                logger.info(
                    f"[Scheduler:{tier.name}] Starting run #{tier.run_count} "
                    f"({len(tier.providers)} providers)"
                )

                # Acquire lock — only one tier can use the pipeline at a time
                async with self._run_lock:
                    results = await self._run_with_state_updates(tier.providers, tier_name=tier.name)

                tier.last_run = datetime.now(timezone.utc)
                elapsed = (tier.last_run - start_time).total_seconds()

                logger.info(
                    f"[Scheduler:{tier.name}] Run #{tier.run_count} complete: "
                    f"{results.get('total_events', 0)} events, "
                    f"{results.get('total_odds', 0)} odds in {elapsed:.1f}s"
                )

                # Wait remaining interval time
                wait_time = max(0, tier.interval_seconds - elapsed)
                if wait_time > 0:
                    logger.debug(f"[Scheduler:{tier.name}] Waiting {wait_time:.0f}s until next run")
                    await asyncio.sleep(wait_time)

            except asyncio.CancelledError:
                logger.info(f"[Scheduler:{tier.name}] Loop cancelled")
                break
            except Exception as e:
                logger.error(f"[Scheduler:{tier.name}] Error: {e}", exc_info=True)
                self._clear_extraction_state()
                # Wait before retry on error
                await asyncio.sleep(60)

        logger.info(f"[Scheduler:{tier.name}] Loop stopped")

    def stop_tier(self, name: str):
        """Stop a specific tier."""
        tier = self._tiers.get(name)
        if not tier or not tier.running:
            logger.warning(f"[Scheduler] Tier '{name}' not running")
            return

        tier.running = False
        if tier.task:
            tier.task.cancel()
            tier.task = None

        logger.info(f"[Scheduler] Stopped tier '{name}' after {tier.run_count} runs")

    # ── Convenience: start/stop all tiers ──────────────────────────────

    async def start_all(self):
        """Start all extraction tiers with default config.

        Tier config:
        - sharp: Pinnacle + Polymarket every 3 min (immediate)
        - api_soft: Kambi (8) + Altenar (6) + Gecko V2 (4) + Vbet every 60 min (immediate)
        - browser_soft: Spectate + ComeOn + Snabbare + 10Bet + Interwetten + Coolbet + Tipwin every 120 min (immediate)
        - boosts: Oddsboost scraping every 120 min (immediate)
        """
        # Sharp tier — reference odds + live score capture
        await self.start_tier(
            name="sharp",
            providers=["polymarket", "pinnacle"],
            interval_seconds=60,  # 1 min — fast polling for live scores + FT detection
            run_immediately=True,
        )

        # API soft tier — fast REST/WS extractors
        await self.start_tier(
            name="api_soft",
            providers=[
                # Kambi API (8)
                "unibet", "leovegas", "expekt", "betmgm",
                "speedybet", "x3000", "goldenbull", "1x2",
                # Altenar API (6)
                "betinia", "campobet", "swiper", "lodur", "dbet", "quickcasino",
                # Gecko V2 (4) — API interception, not pure REST, but fast
                "betsson", "nordicbet", "spelklubben", "bethard",
                # BetConstruct (1) — WebSocket API
                "vbet",
            ],
            interval_seconds=3600,  # 60 min
            run_immediately=True,
        )

        # Browser soft tier — heavy browser-based extractors
        await self.start_tier(
            name="browser_soft",
            providers=[
                # Spectate (2)
                "mrgreen", "888sport",
                # ComeOn Group (3)
                "comeon", "hajper", "lyllo",
                # Snabbare (1)
                "snabbare",
                # 10Bet (1)
                "10bet",
                # Interwetten (1)
                "interwetten",
                # Coolbet (1)
                "coolbet",
                # Tipwin (1)
                "tipwin",
            ],
            interval_seconds=7200,  # 120 min
            run_immediately=True,
        )

        # Boosts tier — oddsboost scraping (standalone, no pipeline lock needed)
        await self.start_boosts_tier()

        # Cleanup tier — purge stale events/odds every 6 hours
        await self.start_cleanup_tier()

        # Settlement tier — auto-settle bets from Pinnacle live scores every 2 min
        await self.start_settlement_tier()

        # Trading daily/weekly auto-reset (checks every 60s, acts at market boundaries)
        await self.start_trading_reset_tier()

    def stop_all(self):
        """Stop all running tiers."""
        for name in list(self._tiers.keys()):
            if self._tiers[name].running:
                self.stop_tier(name)
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

    # ── Boosts tier (standalone, no pipeline) ─────────────────────────

    async def start_boosts_tier(self, interval_seconds: int = 7200):
        """Start the oddsboost scraper on a recurring schedule.

        Runs independently of extraction tiers — doesn't need the pipeline
        lock since it uses its own Playwright browser and saves to JSON.
        """
        if self._boosts_task and not self._boosts_task.done():
            logger.warning("[Scheduler] Boosts tier already running")
            return

        logger.info(f"[Scheduler] Starting boosts tier: interval={interval_seconds}s")
        self._boosts_task = asyncio.create_task(
            self._boosts_loop(interval_seconds)
        )

    async def _boosts_loop(self, interval_seconds: int):
        """Recurring loop for oddsboost scraping."""
        from src.api.state import update_tier_state

        while True:
            try:
                logger.info("[Scheduler:boosts] Starting boost scrape")
                await self._run_boost_scrape()
                logger.info("[Scheduler:boosts] Boost scrape complete")
            except asyncio.CancelledError:
                logger.info("[Scheduler:boosts] Loop cancelled")
                update_tier_state("boosts", running=False)
                break
            except Exception as e:
                logger.error(f"[Scheduler:boosts] Error: {e}", exc_info=True)
            finally:
                update_tier_state("boosts", running=False, completed_providers=1, last_run=datetime.now(timezone.utc).isoformat())

            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break

    async def _run_boost_scrape(self):
        """Execute the boost scraper in a thread executor."""
        import sys
        from dataclasses import asdict
        from src.api.state import update_tier_state
        from src.paths import get_bundle_dir
        # Ensure scripts/ package is importable (lives in bundle root / backend/)
        _root = str(get_bundle_dir())
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from scripts.scrape_specials import scrape_all, save_specials
        from src.analysis.ev_enrichment import enrich_specials_with_ev, filter_expired, store_specials_to_db
        from src.db.models import get_session

        update_tier_state("boosts", running=True, start_time=datetime.now(timezone.utc), total_providers=1, completed_providers=0)

        loop = asyncio.get_running_loop()
        specials, run_log = await loop.run_in_executor(None, lambda: scrape_all(verbose=False))
        if specials:
            # JSON backup (kept for transition)
            save_specials(specials)

            # EV enrichment + DB storage
            session = get_session()
            try:
                specials_dicts = filter_expired([asdict(s) for s in specials])
                specials_dicts = enrich_specials_with_ev(specials_dicts, session)
                count = store_specials_to_db(specials_dicts, session)
                ev_count = sum(1 for s in specials_dicts if s.get("is_positive_ev"))
                logger.info(f"[Scheduler:boosts] Stored {count} boosts to DB ({ev_count} +EV)")
            except Exception as e:
                logger.error(f"[Scheduler:boosts] DB storage failed: {e}", exc_info=True)
                try:
                    session.rollback()
                except Exception:
                    pass
            finally:
                try:
                    session.close()
                except Exception:
                    pass
        else:
            logger.info("[Scheduler:boosts] No boosts found")

        # Persist extraction log to DB
        self._persist_boost_log(run_log)

    def _persist_boost_log(self, run_log, max_runs: int = 10):
        """Persist boost extraction log to DB. Keeps last `max_runs` runs."""
        from src.db.models import BoostExtractionLog, get_session
        from datetime import datetime as dt
        from sqlalchemy import func

        try:
            session = get_session()
            scraped_at = dt.fromisoformat(run_log.scraped_at) if run_log.scraped_at else dt.utcnow()

            # Prune old boost runs beyond max_runs (keep N-1, adding 1 new = N total)
            # Each run shares the same run_id, so count distinct run_ids
            distinct_run_ids = (
                session.query(BoostExtractionLog.run_id, func.max(BoostExtractionLog.scraped_at).label('latest'))
                .group_by(BoostExtractionLog.run_id)
                .order_by(func.max(BoostExtractionLog.scraped_at).desc())
                .all()
            )
            if len(distinct_run_ids) >= max_runs:
                stale_run_ids = [r.run_id for r in distinct_run_ids[max_runs - 1:]]
                session.query(BoostExtractionLog).filter(
                    BoostExtractionLog.run_id.in_(stale_run_ids)
                ).delete(synchronize_session='fetch')

            for pl in run_log.providers:
                session.add(BoostExtractionLog(
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
                ))

            session.commit()
            logger.info(f"[Scheduler:boosts] Persisted log: {len(run_log.providers)} providers, {run_log.total_boosts} boosts in {run_log.duration_seconds:.1f}s")
        except Exception as e:
            logger.error(f"[Scheduler:boosts] Failed to persist log: {e}")
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            try:
                session.close()
            except Exception:
                pass

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
        self._trading_reset_task = asyncio.create_task(
            self._trading_reset_loop(check_interval)
        )

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
                        from src.services.trading_service import TradingService
                        from src.db.models import get_session

                        session = get_session()
                        try:
                            svc = TradingService(session)
                            result = svc.auto_reset_daily()
                            last_daily_reset = today_str
                            if result["reset_accounts"] > 0:
                                logger.info(f"[Scheduler:trading_reset] Daily reset: {result['reset_accounts']} accounts")
                        finally:
                            session.close()
                    except Exception as e:
                        logger.error(f"[Scheduler:trading_reset] Daily reset failed: {e}")

                # Weekly reset — once per UTC week (Monday = weekday 0)
                if now.weekday() == 0 and last_weekly_reset != week_str:
                    try:
                        from src.services.trading_service import TradingService
                        from src.db.models import get_session

                        session = get_session()
                        try:
                            svc = TradingService(session)
                            result = svc.auto_reset_weekly()
                            last_weekly_reset = week_str
                            if result["reset_accounts"] > 0:
                                logger.info(f"[Scheduler:trading_reset] Weekly reset: {result['reset_accounts']} accounts")
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
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(interval_seconds)
        )

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
        from src.db.models import Event, Odds, Opportunity, Bet, get_session

        loop = asyncio.get_running_loop()

        def _do_cleanup() -> dict:
            stats = {
                "inactive": 0, "orphaned": 0, "past_events": 0,
                "past_events_deleted": 0, "past_odds_deleted": 0,
                "deactivated": 0,
            }
            session = get_session()
            try:
                from sqlalchemy import or_
                now = datetime.now(timezone.utc)

                # 1. Delete inactive opportunities
                stats["inactive"] = session.query(Opportunity).filter(
                    Opportunity.is_active == False
                ).delete()

                # 2. Delete orphaned opportunities (event doesn't exist)
                valid_event_subq = session.query(Event.id).subquery()
                stats["orphaned"] = session.query(Opportunity).filter(
                    ~Opportunity.event_id.in_(session.query(valid_event_subq))
                ).delete(synchronize_session=False)

                # 3. Delete opportunities for past events (keep live/finished for settlement)
                past_event_subq = session.query(Event.id).filter(
                    Event.start_time < now,
                    or_(Event.match_status.is_(None), ~Event.match_status.in_(["live", "finished"])),
                ).subquery()
                stats["past_events"] = session.query(Opportunity).filter(
                    Opportunity.event_id.in_(session.query(past_event_subq))
                ).delete(synchronize_session=False)

                # 4. Delete past events + their odds (bulk)
                #    Preserve events that have bets OR are live/finished
                past_event_ids = [
                    row[0] for row in session.query(Event.id).filter(
                        Event.start_time < now,
                        or_(Event.match_status.is_(None), ~Event.match_status.in_(["live", "finished"])),
                    ).all()
                ]
                if past_event_ids:
                    event_ids_with_bets = set(
                        row[0] for row in session.query(Bet.event_id).filter(
                            Bet.event_id.in_(past_event_ids)
                        ).all()
                        if row[0]
                    )
                    deletable_ids = [
                        eid for eid in past_event_ids
                        if eid not in event_ids_with_bets
                    ]
                    if deletable_ids:
                        # Bulk delete odds first, then events (batched)
                        for i in range(0, len(deletable_ids), 500):
                            batch = deletable_ids[i:i + 500]
                            stats["past_odds_deleted"] += session.query(Odds).filter(
                                Odds.event_id.in_(batch)
                            ).delete(synchronize_session=False)
                            stats["past_events_deleted"] += session.query(Event).filter(
                                Event.id.in_(batch)
                            ).delete(synchronize_session=False)

                # 5. Deactivate remaining opportunities (will be refreshed during next scan)
                stats["deactivated"] = session.query(Opportunity).filter(
                    Opportunity.is_active == True
                ).update({"is_active": False})

                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

            return stats

        return await loop.run_in_executor(None, _do_cleanup)

    # ── Settlement tier (auto-settle bets from Pinnacle live scores) ───

    async def start_settlement_tier(self, interval_seconds: int = 120):
        """Start periodic auto-settlement (every 2 minutes).

        Settles pending bets on events that Pinnacle marked as finished.
        Also snapshots closing odds for started events.
        Runs independently — no pipeline lock needed, just a DB session.
        """
        if self._settlement_task and not self._settlement_task.done():
            logger.warning("[Scheduler] Settlement tier already running")
            return

        logger.info(f"[Scheduler] Starting settlement tier: interval={interval_seconds}s")
        self._settlement_task = asyncio.create_task(
            self._settlement_loop(interval_seconds)
        )

    async def _settlement_loop(self, interval_seconds: int):
        """Recurring loop for auto-settlement."""
        # Wait before first run — let extraction populate data first
        try:
            await asyncio.sleep(120)  # 2 min initial delay
        except asyncio.CancelledError:
            return

        while True:
            try:
                logger.info("[Scheduler:settlement] Starting auto-settlement")
                stats = self._run_settlement()
                logger.info(
                    f"[Scheduler:settlement] Done: "
                    f"{stats.get('settled', 0)}/{stats.get('checked', 0)} bets settled "
                    f"({stats.get('skipped', 0)} skipped)"
                )
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
        """Snapshot closing odds for CLV tracking on started events."""
        from src.services.bet_service import BetService
        from src.db.models import get_session

        session = get_session()
        try:
            bet_service = BetService(session)
            clv_stats = bet_service.snapshot_closing_odds()
            session.commit()

            if clv_stats.get("updated", 0) > 0:
                logger.info(
                    f"[Scheduler:settlement] CLV snapshot: "
                    f"{clv_stats['updated']}/{clv_stats['processed']} bets updated"
                )

            return {"settled": 0, "checked": 0, "skipped": 0, **clv_stats}
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

        This is called from app startup. Now starts ALL tiers.
        """
        await self.start_all()

    def stop(self):
        """Stop all tiers (legacy interface)."""
        self.stop_all()

    async def run_once(self, providers: list[str] = None) -> dict:
        """Run a single extraction (for manual/on-demand)."""
        if providers is None:
            providers = self.pipeline.engine.get_enabled_providers()

        logger.info(f"[Scheduler] Running one-time extraction: {len(providers)} providers")
        async with self._run_lock:
            results = await self._run_with_state_updates(providers)
        return results

    # ── State ──────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        """Check if any tier is running."""
        return any(t.running for t in self._tiers.values())

    @property
    def last_run(self) -> Optional[datetime]:
        """Get most recent run across all tiers."""
        runs = [t.last_run for t in self._tiers.values() if t.last_run]
        return max(runs) if runs else None

    @property
    def run_count(self) -> int:
        """Get total runs across all tiers."""
        return sum(t.run_count for t in self._tiers.values())

    @property
    def interval_seconds(self) -> Optional[int]:
        """Get sharp tier interval (legacy)."""
        sharp = self._tiers.get("sharp")
        return sharp.interval_seconds if sharp else None

    @property
    def providers(self) -> Optional[list[str]]:
        """Get sharp tier providers (legacy)."""
        sharp = self._tiers.get("sharp")
        return sharp.providers if sharp else None

    def get_status(self) -> dict:
        """Get scheduler status for all tiers."""
        tiers = {}
        for name, tier in self._tiers.items():
            next_run = None
            if tier.running and tier.last_run:
                next_run_dt = tier.last_run + timedelta(seconds=tier.interval_seconds)
                next_run = next_run_dt.isoformat()

            tiers[name] = {
                "running": tier.running,
                "providers": tier.providers,
                "interval_seconds": tier.interval_seconds,
                "last_run": tier.last_run.isoformat() if tier.last_run else None,
                "run_count": tier.run_count,
                "next_run": next_run,
            }

        return {
            "running": self.running,
            "tiers": tiers,
            # Legacy fields
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "run_count": self.run_count,
            "interval_seconds": self.interval_seconds,
            "providers": self.providers,
            "next_run": None,  # Deprecated — use tiers[x].next_run
        }

    # ── Internal ───────────────────────────────────────────────────────

    async def _run_with_state_updates(self, providers: list[str], tier_name: str = "default") -> dict:
        """Run extraction with UI state updates (both global and per-tier)."""
        from src.api.state import update_extraction_state, extraction_state, update_tier_state
        from src.api.routes.extraction import _build_final_state

        now = datetime.now(timezone.utc)

        # Initialize global extraction state
        update_extraction_state(
            running=True,
            start_time=now,
            total_events=0,
            total_odds=0,
            providers={},
            current_provider=None,
            completed_providers=0,
            total_providers=len(providers),
        )

        # Initialize per-tier state
        update_tier_state(tier_name,
            running=True,
            start_time=now,
            total_events=0,
            total_odds=0,
            providers={},
            current_provider=None,
            completed_providers=0,
            total_providers=len(providers),
            elapsed_seconds=0,
        )

        _results = None
        try:
            # Start metrics polling task
            stop_event = asyncio.Event()
            polling_task = asyncio.create_task(
                self._poll_metrics_loop(stop_event, tier_name=tier_name, tier_providers=providers)
            )

            try:
                _results = await self.pipeline.run(providers=providers, tier_name=tier_name)
            finally:
                stop_event.set()
                try:
                    await polling_task
                except Exception:
                    pass

        finally:
            # Final state update in finally block (guaranteed to run)
            if _results:
                try:
                    from sqlalchemy import func
                    from src.db.models import Odds, get_session as _get_session

                    final = _build_final_state(_results)
                    now_iso = datetime.now(timezone.utc).isoformat()

                    # Global state uses pipeline totals
                    update_extraction_state(
                        total_events=_results.get("total_events", 0),
                        total_odds=_results.get("total_odds", 0),
                        providers=final["providers"],
                        completed_providers=final["completed_providers"],
                        total_providers=final["total_providers"],
                        current_provider=None,
                        last_run=now_iso,
                    )

                    # Per-tier state: only events matched with Pinnacle
                    tier_events = 0
                    tier_odds = 0
                    db = _get_session()
                    try:
                        # Events that have odds from BOTH this tier's providers AND pinnacle
                        from sqlalchemy import and_
                        pin_event_ids = db.query(Odds.event_id).filter(
                            Odds.provider_id == "pinnacle"
                        ).distinct().subquery()
                        row = db.query(
                            func.count(func.distinct(Odds.event_id)),
                            func.count(Odds.id),
                        ).filter(
                            Odds.provider_id.in_(providers),
                            Odds.event_id.in_(db.query(pin_event_ids)),
                        ).first()
                        if row:
                            tier_events = row[0] or 0
                            tier_odds = row[1] or 0
                    finally:
                        db.close()

                    update_tier_state(tier_name,
                        total_events=tier_events,
                        total_odds=tier_odds,
                        providers=final["providers"],
                        completed_providers=final["completed_providers"],
                        total_providers=final["total_providers"],
                        current_provider=None,
                        last_run=now_iso,
                    )
                except Exception:
                    pass
            # Compute final elapsed time before clearing running flag
            start = extraction_state.get("start_time")
            if start:
                final_elapsed = (datetime.now(timezone.utc) - start).total_seconds()
                update_extraction_state(elapsed_seconds=final_elapsed)
                update_tier_state(tier_name, elapsed_seconds=final_elapsed)
            update_extraction_state(running=False)
            update_tier_state(tier_name, running=False)

        return _results or {}

    async def _poll_metrics_loop(
        self,
        stop_event: asyncio.Event,
        tier_name: str = "default",
        tier_providers: list[str] | None = None,
    ):
        """Poll metrics and update extraction state (both global and per-tier).

        Per-tier state uses provider-filtered counts so each tier shows
        only its own events/odds. Global state uses full DB counts.
        """
        from sqlalchemy import func
        from src.api.state import update_extraction_state, update_tier_state
        from src.db.models import Event, Odds, get_session

        db = get_session()
        try:
            while not stop_event.is_set():
                if not self.pipeline.metrics:
                    await asyncio.sleep(0.5)
                    continue

                current_run = self.pipeline.metrics.get_current_run()
                if not current_run:
                    await asyncio.sleep(0.5)
                    continue

                # Build provider states from metrics
                providers_state = {}
                completed_count = 0
                current_provider = None

                for pid, pm in current_run.providers.items():
                    status = "pending"
                    if pm.is_complete:
                        status = "completed" if pm.success else "failed"
                        completed_count += 1
                    elif pm.start_time and not pm.is_complete:
                        status = "running"
                        current_provider = pid

                    providers_state[pid] = {
                        "status": status,
                        "events": pm.total_events,
                        "odds": pm.total_odds,
                        "duration_seconds": round(time.time() - pm.start_time, 1) if not pm.is_complete else pm.duration_seconds,
                        "error": pm.error,
                        "sports_completed": pm.sports_succeeded,
                        "sports_total": pm.total_sports_configured or pm.sports_attempted,
                        "current_sport": None,
                        "sports": {},
                    }

                    # Build per-sport breakdown from SportMetrics
                    for sport_name, sm in pm.sports.items():
                        if not sm.is_complete:
                            providers_state[pid]["current_sport"] = sport_name
                        providers_state[pid]["sports"][sport_name] = {
                            "status": "completed" if sm.is_complete else "running",
                            "success": sm.success if sm.is_complete else None,
                            "events": sm.events_processed,
                            "odds": sm.odds_processed,
                            "duration": round(sm.duration_seconds, 1) if sm.is_complete else round(time.time() - sm.start_time, 1),
                        }

                db.expire_all()

                # Global counts (all providers)
                total_events = db.query(Event).count()
                total_odds = db.query(Odds).count()

                # Per-tier counts: only events matched with Pinnacle
                tier_events = 0
                tier_odds = 0
                if tier_providers:
                    pin_event_ids = db.query(Odds.event_id).filter(
                        Odds.provider_id == "pinnacle"
                    ).distinct().subquery()
                    row = db.query(
                        func.count(func.distinct(Odds.event_id)),
                        func.count(Odds.id),
                    ).filter(
                        Odds.provider_id.in_(tier_providers),
                        Odds.event_id.in_(db.query(pin_event_ids)),
                    ).first()
                    if row:
                        tier_events = row[0] or 0
                        tier_odds = row[1] or 0
                else:
                    tier_events = total_events
                    tier_odds = total_odds

                # Update global state
                update_extraction_state(
                    total_events=total_events,
                    total_odds=total_odds,
                    providers=providers_state,
                    current_provider=current_provider,
                    completed_providers=completed_count,
                )

                # Update per-tier state with tier-specific counts
                update_tier_state(tier_name,
                    total_events=tier_events,
                    total_odds=tier_odds,
                    providers=providers_state,
                    current_provider=current_provider,
                    completed_providers=completed_count,
                )

                await asyncio.sleep(0.5)
        finally:
            db.close()

    def _clear_extraction_state(self):
        """Clear extraction state on error."""
        try:
            from src.api.state import update_extraction_state
            update_extraction_state(running=False)
        except Exception:
            pass


# Global scheduler instance
_scheduler: Optional[ExtractionScheduler] = None


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
