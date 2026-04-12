"""
Pipeline Orchestrator

Main ExtractionPipeline class that coordinates extraction from all sources.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from ..constants import ALLOWED_SPORTS, PROVIDER_CANONICAL, SHARP_PROVIDERS
from ..db.models import DeferredEvent, Event, Odds, Provider, get_session
from ..factory import ExtractorFactory
from .broadcast import odds_broadcaster
from .pool_manager import ProviderPoolManager
from .storage import OddsBatchProcessor, store_polymarket_event, store_provider_event

logger = logging.getLogger(__name__)


class ExtractionPipeline:
    """
    Unified extraction from all sources using sports config.

    Coordinates extraction from Polymarket and all configured providers,
    performs fuzzy matching, and stores results in database.

    Usage:
        pipeline = ExtractionPipeline()
        results = await pipeline.run()
    """

    def __init__(self, db_session=None):
        """
        Initialize pipeline.

        Args:
            db_session: Optional SQLAlchemy session (creates new if not provided)
        """
        self.session = db_session or get_session()
        self.engine = ExtractorFactory.get_instance()
        self._ensure_providers()

        # Cache for ALL events to enable cross-provider fuzzy matching
        # Dict indexed by sport for O(1) sport lookup
        # {sport: {event_id: (home, away, date_str, league)}}
        # Events from Polymarket, Pinnacle, and all providers are added here
        self.event_cache = {}
        # Secondary date index for O(1) date-based candidate lookup
        # {sport: {date_str: set(event_ids)}}
        self.event_cache_by_date = {}
        # Thread-safe lock for async access to event_cache
        self._cache_lock = asyncio.Lock()

        # Pre-warmed sharp odds cache (populated after sharp extraction)
        self._sharp_odds_cache = {}

        # Aggregated set of event IDs whose odds changed during this run
        self._changed_event_ids: set[str] = set()

        # Initialize orchestrator components
        self._init_orchestrator()

    async def get_cached_sports(self) -> set:
        """Get set of sports with cached events (thread-safe)."""
        async with self._cache_lock:
            return set(self.event_cache.keys())

    def resolve_deferred(self):
        """Resolve deferred events against fresh Pinnacle data.

        Called after Pinnacle extraction + cache warm-up. Attempts to match
        buffered soft provider events that previously had no Pinnacle match.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC to match SQLite stored datetimes
        sharp_sports = set(self.event_cache.keys())

        if not sharp_sports:
            return 0, 0

        deferred = (
            self.session.query(DeferredEvent)
            .filter(
                DeferredEvent.start_time > now,
                DeferredEvent.sport.in_(sharp_sports),
            )
            .all()
        )

        if not deferred:
            # Cleanup expired only
            expired = (
                self.session.query(DeferredEvent)
                .filter((DeferredEvent.start_time <= now) | (DeferredEvent.created_at < now - timedelta(hours=6)))
                .delete()
            )
            if expired:
                self.session.commit()
            return 0, expired

        recovered = 0
        fm = self.orchestrator_config.fuzzy_match

        for de in deferred:
            event = de.to_standard_event()
            is_new, odds_processed, _ = store_provider_event(
                self.session,
                event,
                de.provider_id,
                event_cache=self.event_cache,
                fuzzy_threshold=fm.threshold,
                min_individual_score=fm.min_individual_score,
                prefix_filter_length=fm.prefix_filter_length,
                require_match=True,
                sharp_odds_cache=self._sharp_odds_cache,
                max_asymmetry_diff=fm.max_asymmetry_diff,
                min_for_asymmetry_check=fm.min_for_asymmetry_check,
                date_index=self.event_cache_by_date,
            )

            if is_new or odds_processed > 0:
                self.session.delete(de)
                recovered += 1
            else:
                de.attempt_count += 1

        # Cleanup expired or stale (>6 hours)
        expired = (
            self.session.query(DeferredEvent)
            .filter((DeferredEvent.start_time <= now) | (DeferredEvent.created_at < now - timedelta(hours=6)))
            .delete()
        )

        self.session.commit()

        if recovered or expired:
            logger.info(
                f"Deferred resolution: {recovered} recovered, "
                f"{expired} expired, {len(deferred) - recovered} still pending"
            )

        return recovered, expired

    async def clear_cache(self):
        """Clear the event cache (thread-safe)."""
        async with self._cache_lock:
            self.event_cache.clear()

    # Maximum age of events to load into cache — older events are irrelevant for matching
    _CACHE_MAX_AGE_DAYS = 14

    def _populate_cache_from_db(self):
        """Pre-populate event_cache + date index from existing DB events for fuzzy matching.

        This is critical when extracting a subset of providers (e.g., just '10bet')
        against an existing DB with Pinnacle events. Without this, the fuzzy matching
        has no candidates and events with slight name/date differences won't match.

        Only loads events from the last 14 days to cap memory usage.
        """
        from ..db.models import Event
        from .storage import _update_event_cache

        cutoff = datetime.now(timezone.utc) - timedelta(days=self._CACHE_MAX_AGE_DAYS)
        events = (
            self.session.query(Event.id, Event.sport, Event.home_team, Event.away_team, Event.start_time, Event.league)
            .filter(Event.start_time >= cutoff)
            .all()
        )
        for eid, sport, home, away, start_time, league in events:
            if hasattr(start_time, "strftime"):
                date_str = start_time.strftime("%Y%m%d")
            elif isinstance(start_time, str):
                date_str = start_time.split("T")[0].replace("-", "")
            else:
                date_str = "00000000"
            _update_event_cache(
                self.event_cache,
                self.event_cache_by_date,
                sport,
                eid,
                home,
                away,
                date_str,
                league=league or "",
            )
        total = sum(len(v) for v in self.event_cache.values())
        if total > 0:
            logger.debug(f"Pre-populated event cache from DB: {total} events across {len(self.event_cache)} sports")

    # Typical sport durations (hours) — used for time-based FT detection
    SPORT_DURATION_HOURS: dict[str, float] = {
        "football": 2.5,
        "basketball": 3.0,
        "ice_hockey": 3.0,
        "tennis": 4.0,
        "esports": 4.0,
        "handball": 2.5,
        "mma": 3.0,
    }
    DEFAULT_DURATION_HOURS = 3.0

    def _detect_finished_events(self) -> int:
        """Mark events as 'finished' when they are no longer active.

        Three detection strategies (1 & 2 use bulk UPDATE, 3 needs per-sport filter):
        1. Staleness: match_status='live' and updated_at > 3 min ago = Pinnacle dropped them
        2. Time-based (live): match_status='live' and start_time + 6 hours ago = over
        3. Time-based (never-live): pending bets, match_status NULL/prematch,
           start_time + sport duration has elapsed.

        Returns number of events marked as finished.
        """
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import or_, update

        now = datetime.now(timezone.utc)
        count = 0

        # Strategy 1 + 2: bulk UPDATE — no need to load ORM objects
        stale_threshold = now - timedelta(minutes=3)
        time_cutoff = now - timedelta(hours=6)
        bulk_count = (
            self.session.execute(
                update(Event)
                .where(
                    Event.match_status == "live",
                    or_(
                        Event.updated_at < stale_threshold,
                        Event.start_time < time_cutoff,
                    ),
                )
                .values(match_status="finished")
            )
        ).rowcount
        count += bulk_count
        if bulk_count:
            logger.info(f"[FT] Bulk-marked {bulk_count} stale/overtime live events as finished")

        # Strategy 3: never-live events with pending bets, past sport duration
        from ..db.models import Bet

        min_hours = min(self.SPORT_DURATION_HOURS.values())
        broad_cutoff = now - timedelta(hours=min_hours)
        never_live_candidates = (
            self.session.query(Event.id, Event.sport, Event.start_time, Event.home_team, Event.away_team)
            .join(Bet, Bet.event_id == Event.id)
            .filter(
                Bet.result == "pending",
                or_(Event.match_status.is_(None), Event.match_status == "prematch"),
                Event.start_time.isnot(None),
                Event.start_time < broad_cutoff,
            )
            .distinct()
            .all()
        )
        # Refine per-sport, collect IDs for batch update
        finish_ids = []
        for eid, sport, start_time, home, away in never_live_candidates:
            hours = self.SPORT_DURATION_HOURS.get(sport, self.DEFAULT_DURATION_HOURS)
            st = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
            if st < now - timedelta(hours=hours):
                finish_ids.append(eid)
                logger.info(f"[FT] {home} vs {away} -> finished (never-live, past {hours}h)")

        if finish_ids:
            self.session.execute(update(Event).where(Event.id.in_(finish_ids)).values(match_status="finished"))
            count += len(finish_ids)

        return count

    def _pre_warm_pinnacle_caches(self):
        """Pre-load Pinnacle sharp odds into cache to eliminate per-event DB queries.

        After Pinnacle extraction, this loads 1x2/moneyline odds for
        detect_and_fix_inversion, eliminating thousands of per-event DB
        round-trips across 30+ soft providers.
        """
        # Pre-warm sharp odds cache (1x2/moneyline for inversion detection)
        sharp_rows = (
            self.session.query(Odds.event_id, Odds.outcome, Odds.odds)
            .filter(
                Odds.provider_id == "pinnacle",
                Odds.outcome.in_(["home", "away"]),
                Odds.market.in_(["1x2", "moneyline"]),
            )
            .all()
        )

        self._sharp_odds_cache = {}
        for event_id, outcome, odds in sharp_rows:
            if event_id not in self._sharp_odds_cache:
                self._sharp_odds_cache[event_id] = {}
            self._sharp_odds_cache[event_id][outcome] = odds

        logger.debug(f"Pre-warmed Pinnacle caches: {len(self._sharp_odds_cache)} sharp odds entries")

    def _init_orchestrator(self):
        """Initialize orchestrator components (called from __init__)."""
        # Get orchestrator config
        orchestrator_config = self.engine.config_loader.get_orchestrator_config()
        self.orchestrator_config = orchestrator_config

        # Create provider semaphore for global concurrency control (fallback)
        self.provider_semaphore = asyncio.Semaphore(orchestrator_config.max_concurrent_providers)

        # Initialize pool manager for type-aware scheduling
        if orchestrator_config.provider_groups:
            # Use config_loader.providers which has ProviderConfig objects
            self.pool_manager = ProviderPoolManager(orchestrator_config, self.engine.config_loader.providers)
            logger.debug("[Orchestrator] Type-aware pool manager enabled")
        else:
            self.pool_manager = None
            logger.debug("[Orchestrator] Using flat semaphore (no provider groups configured)")

        # Initialize metrics collector if enabled
        if orchestrator_config.metrics.enabled:
            from .metrics import MetricsCollector

            self.metrics = MetricsCollector(max_history=orchestrator_config.metrics.retention_count)
            logger.debug("[Orchestrator] Metrics collection enabled")
        else:
            self.metrics = None

        # Initialize circuit breaker if enabled
        if orchestrator_config.circuit_breaker.enabled:
            from .circuit_breaker import CircuitBreaker

            self.circuit_breaker = CircuitBreaker(
                failure_threshold=orchestrator_config.circuit_breaker.failure_threshold,
                recovery_timeout_seconds=orchestrator_config.circuit_breaker.recovery_timeout_seconds,
                half_open_max_attempts=orchestrator_config.circuit_breaker.half_open_max_attempts,
            )
            # Inject circuit breaker into factory for transport-level 429 detection
            self.engine.set_circuit_breaker(self.circuit_breaker)
            logger.debug("[Orchestrator] Circuit breaker enabled (injected into factory)")
        else:
            self.circuit_breaker = None

        # Initialize cache if enabled
        if orchestrator_config.cache.enabled:
            from .cache import ResponseCache

            self.cache = ResponseCache(
                default_ttl_seconds=orchestrator_config.cache.ttl_seconds,
                max_entries=orchestrator_config.cache.max_entries,
                per_provider=orchestrator_config.cache.cache_per_provider,
            )
            logger.debug("[Orchestrator] Response cache enabled")
        else:
            self.cache = None

        # Load ML models from registry, fall back to disk discovery
        try:
            from src.ml.serving.predictor import get_predictor

            predictor = get_predictor()
            loaded = predictor.load_from_registry(self.session)
            if loaded == 0:
                loaded = predictor.load_from_disk()
            if loaded > 0:
                logger.info(f"Loaded {loaded} ML models")
        except Exception:
            pass

        # Initialize health checker if enabled
        if orchestrator_config.health_check.enabled:
            from .health import HealthChecker

            self.health_checker = HealthChecker(timeout_seconds=orchestrator_config.health_check.timeout_seconds)
            logger.debug("[Orchestrator] Health checker enabled")
        else:
            self.health_checker = None

        # Initialize graceful shutdown if enabled
        if orchestrator_config.graceful_shutdown.enabled:
            self._shutdown_event = asyncio.Event()
            self._shutdown_timeout = orchestrator_config.graceful_shutdown.shutdown_timeout_seconds
            self._cancel_pending = orchestrator_config.graceful_shutdown.cancel_pending_tasks
            self._register_signal_handlers()
            logger.debug("[Orchestrator] Graceful shutdown enabled")
        else:
            self._shutdown_event = None

    def _ensure_providers(self):
        """Create provider records in DB if they don't exist."""
        # Get all providers from engine (returns dict of ProviderConfig dicts)
        all_providers = self.engine.providers

        providers = [
            ("pinnacle", "Pinnacle"),
            ("polymarket", "Polymarket"),
            ("consensus", "Consensus"),
            *[(pid, (cfg.get("domain") or pid).title()) for pid, cfg in all_providers.items()],
        ]

        for pid, name in providers:
            existing = self.session.query(Provider).filter(Provider.id == pid).first()
            if not existing:
                # Note: bonus_status is now tracked per-profile in ProfileProviderBonus table
                self.session.add(
                    Provider(
                        id=pid,
                        name=name,
                    )
                )
        self.session.commit()

    def _register_signal_handlers(self):
        """Register SIGINT/SIGTERM handlers for graceful shutdown."""
        import signal
        import threading

        if threading.current_thread() is not threading.main_thread():
            logger.debug("Skipping signal handlers (not on main thread)")
            return

        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            if self._shutdown_event:
                self._shutdown_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    async def _extract_provider_with_retry(
        self,
        provider_id: str,
        kambi_sports: list[str],
        limit: int,
        sharp_sports: set | None = None,
        sharp_leagues: dict | None = None,
    ) -> dict:
        """
        Wrapper for provider extraction with retry logic.

        Args:
            provider_id: Provider identifier
            kambi_sports: List of sports to extract
            limit: Max events per sport
            sharp_sports: Pre-computed set of sports with sharp data (optional)
            sharp_leagues: {sport: set(league_names)} from Pinnacle (optional)

        Returns:
            Dictionary with extraction results

        Raises:
            Exception: If all retries exhausted
        """
        retry_config = self.orchestrator_config.retry

        if not retry_config.enabled:
            return await self._extract_provider(provider_id, kambi_sports, limit, sharp_sports, sharp_leagues)

        last_error = None

        for attempt in range(retry_config.max_retries):
            # Check for shutdown signal
            if self._shutdown_event and self._shutdown_event.is_set():
                logger.info(f"[{provider_id}] Shutdown requested, aborting extraction")
                raise asyncio.CancelledError("Shutdown requested")

            try:
                result = await self._extract_provider(provider_id, kambi_sports, limit, sharp_sports, sharp_leagues)

                # Success - record in circuit breaker
                if self.circuit_breaker:
                    self.circuit_breaker.record_success(provider_id)

                return result

            except asyncio.TimeoutError as e:
                last_error = e
                if not retry_config.retry_on_timeout:
                    raise

                # Record retry in metrics
                if self.metrics:
                    self.metrics.record_retry(provider_id)

                # Calculate backoff
                if attempt < retry_config.max_retries - 1:
                    backoff = min(
                        retry_config.initial_backoff_seconds * (retry_config.exponential_base**attempt),
                        retry_config.max_backoff_seconds,
                    )
                    logger.warning(
                        f"[{provider_id}] Timeout on attempt {attempt + 1}/{retry_config.max_retries}, "
                        f"retrying in {backoff:.1f}s..."
                    )
                    await asyncio.sleep(backoff)

            except Exception as e:
                last_error = e

                # Record retry in metrics
                if self.metrics:
                    self.metrics.record_retry(provider_id)

                if attempt < retry_config.max_retries - 1:
                    backoff = min(
                        retry_config.initial_backoff_seconds * (retry_config.exponential_base**attempt),
                        retry_config.max_backoff_seconds,
                    )
                    logger.warning(
                        f"[{provider_id}] Error on attempt {attempt + 1}/{retry_config.max_retries}: {str(e) or repr(e)}, "
                        f"retrying in {backoff:.1f}s..."
                    )
                    await asyncio.sleep(backoff)

        # All retries exhausted - record failure in circuit breaker
        if self.circuit_breaker:
            self.circuit_breaker.record_failure(provider_id)

        raise last_error

    async def run(
        self,
        polymarket: bool | None = None,
        providers: list[str] | None = None,
        max_events_per_sport: int = 9999,
        on_progress: Callable[[str], None] | None = None,
        tier_name: str | None = None,
        sequential: bool = False,
        max_concurrency: int | None = None,
    ) -> dict:
        """
        Run extraction from all sources.

        Args:
            polymarket: Extract from Polymarket (default: None, auto-detect from providers list)
            providers: List of provider IDs to extract (default: all enabled, excluding polymarket)
            max_events_per_sport: Limit events per sport (default: 9999, effectively unlimited)
            on_progress: Optional callback for progress updates

        Returns:
            Dictionary with extraction results:
            {
                "polymarket": {...},
                "providers": {provider_id: {...}},
                "total_events": int,
                "matched_events": int,
                "metrics": {...} (if enabled),
                "cache_stats": {...} (if enabled)
            }
        """
        # Polymarket is a separate source - only extract when explicitly requested
        # Auto-detect: if "polymarket" is in providers list, extract it and remove from providers
        if providers and "polymarket" in providers:
            if polymarket is None:
                polymarket = True
            providers = [p for p in providers if p != "polymarket"]
        elif polymarket is None:
            polymarket = False  # Default: don't extract polymarket in main pipeline
        # Start timing
        pipeline_start_time = time.time()

        # Reset changed event IDs for this run
        self._changed_event_ids = set()

        # Start metrics collection
        run_id = f"run_{tier_name or 'manual'}_{int(time.time())}"
        self._current_run_id = run_id
        if self.metrics:
            self.metrics.start_run(run_id)

        results = {
            "polymarket": {"events": 0, "odds": 0},
            "providers": {},
            "total_events": 0,
            "total_odds": 0,
            "matched_events": 0,
            "trigger": tier_name or "manual",
            "run_id": run_id,
        }

        def log_progress(msg: str):
            """Log with elapsed time."""
            elapsed = time.time() - pipeline_start_time
            logger.info(f"[{elapsed:6.1f}s] {msg}")
            if on_progress:
                on_progress(f"[{elapsed:.1f}s] {msg}")

        log_progress("Pipeline started")

        # Fresh session per run: avoids stale identity map from previous runs
        # and prevents unbounded ORM object accumulation across extractions.
        self.session.close()
        self.session = get_session()

        # Clear stale extractors from previous runs (browser handles, connections)
        self.engine.clear_extractor_cache()

        # Clear stale events from previous runs
        await self.clear_cache()

        # Pre-populate cache from existing DB events (enables fuzzy matching
        # when extracting a subset of providers against existing sharp data)
        self._populate_cache_from_db()

        try:
            # Check for shutdown at start
            if self._shutdown_event and self._shutdown_event.is_set():
                log_progress("Shutdown signal detected, aborting pipeline")
                return results

            # Determine target providers
            target_providers = providers if providers is not None else self.engine.get_enabled_providers()

            # Extract Pinnacle FIRST (primary sharp source for canonical events)
            # This ensures other sources can match against Pinnacle's team order
            if "pinnacle" in target_providers:
                log_progress("Extracting Pinnacle (sharp source, establishing canonical events)...")
                pinnacle_start = time.time()

                if self.metrics:
                    self.metrics.start_provider("pinnacle")

                try:
                    # Only extract sports in ALLOWED_SPORTS
                    all_sports = set(s.kambi_sport for s in self.engine.sports)
                    target_sports = sorted(s for s in all_sports if s in ALLOWED_SPORTS)

                    if self.metrics:
                        self.metrics.set_provider_total_sports("pinnacle", len(target_sports))

                    pinnacle_result = await asyncio.wait_for(
                        self._extract_provider("pinnacle", target_sports, max_events_per_sport),
                        timeout=self.orchestrator_config.provider_timeout,
                    )
                    results["providers"]["pinnacle"] = pinnacle_result

                    if self.metrics:
                        self.metrics.end_provider("pinnacle", success=True)

                    pinnacle_elapsed = time.time() - pinnacle_start
                    log_progress(
                        f"Pinnacle done: {pinnacle_result.get('events_processed', 0)} events in {pinnacle_elapsed:.1f}s"
                    )
                except asyncio.TimeoutError:
                    error_msg = f"Timed out after {self.orchestrator_config.provider_timeout}s"
                    logger.error(f"[pinnacle] {error_msg}")
                    results["providers"]["pinnacle"] = {"error": error_msg}
                    if self.metrics:
                        self.metrics.end_provider("pinnacle", success=False, error=error_msg)
                except Exception as e:
                    logger.error(f"Pinnacle extraction failed: {e}")
                    results["providers"]["pinnacle"] = {"error": str(e)}
                    if self.metrics:
                        self.metrics.end_provider("pinnacle", success=False, error=str(e))

                # Remove pinnacle from target_providers to avoid re-extraction
                target_providers = [p for p in target_providers if p != "pinnacle"]

                # Commit Pinnacle data so Polymarket can query it for inversion detection
                self.session.commit()

                # Detect finished events: previously "live" but Pinnacle no longer returned them
                finished_count = self._detect_finished_events()
                if finished_count > 0:
                    log_progress(f"Detected {finished_count} finished events (no longer live on Pinnacle)")
                    self.session.commit()

                # Pre-warm shared Pinnacle caches (eliminates thousands of per-event DB queries)
                self._pre_warm_pinnacle_caches()

                # Resolve deferred events against fresh Pinnacle data
                recovered, expired = self.resolve_deferred()
                if recovered:
                    log_progress(f"Recovered {recovered} deferred events after Pinnacle refresh")

            # Extract from Polymarket (will fuzzy match against Pinnacle events)
            if polymarket:
                log_progress("Extracting Polymarket...")
                poly_start = time.time()

                try:
                    poly_results = await asyncio.wait_for(
                        self._extract_polymarket(max_events_per_sport),
                        timeout=self.orchestrator_config.provider_timeout,
                    )
                    results["polymarket"] = poly_results

                    # Update polymarket_events counter in metrics
                    if self.metrics:
                        self.metrics.set_polymarket_stats(
                            events=poly_results.get("events_processed", 0),
                            odds=poly_results.get("odds_processed", 0),
                        )

                    poly_elapsed = time.time() - poly_start
                    log_progress(f"Polymarket done: {poly_results['events_processed']} events in {poly_elapsed:.1f}s")
                except asyncio.TimeoutError:
                    error_msg = f"Timed out after {self.orchestrator_config.provider_timeout}s"
                    logger.error(f"[polymarket] {error_msg}")
                    results["polymarket"] = {"events_processed": 0, "odds_processed": 0, "error": error_msg}

            # Extract from other providers in parallel
            # Only extract sports in ALLOWED_SPORTS
            all_sports = set(s.kambi_sport for s in self.engine.sports)
            kambi_sports = sorted(s for s in all_sports if s in ALLOWED_SPORTS)

            # Pre-compute sharp sports from Pinnacle cache for filtering
            sharp_sports = await self.get_cached_sports()

            # Only extract sports where Pinnacle has events — others are useless
            # (no fair odds = no value detection possible)
            if sharp_sports:
                skipped = sorted(set(kambi_sports) - sharp_sports)
                kambi_sports = sorted(s for s in kambi_sports if s in sharp_sports)
                if skipped:
                    logger.debug(
                        f"[Orchestrator] Skipping {len(skipped)} sports with no Pinnacle events: {', '.join(skipped)}"
                    )

            # Order sports by Pinnacle event count (most events first)
            # Browser providers that time out will at least have extracted high-value sports
            pin_event_counts = {}
            try:
                # Use a fresh session — per-tier pipelines have isolated sessions
                # that may not see data committed by the sharp tier's session.
                from sqlalchemy import func as sa_count

                fresh_session = get_session()
                rows = (
                    fresh_session.query(Event.sport, sa_count.count(Event.id))
                    .filter(Event.id.in_(fresh_session.query(Odds.event_id).filter(Odds.provider_id == "pinnacle")))
                    .group_by(Event.sport)
                    .all()
                )
                pin_event_counts = {sport: count for sport, count in rows}
                fresh_session.close()
            except Exception:
                pass

            if pin_event_counts:
                kambi_sports = sorted(kambi_sports, key=lambda s: pin_event_counts.get(s, 0), reverse=True)
                top3 = [(s, pin_event_counts.get(s, 0)) for s in kambi_sports[:3]]
                logger.debug(
                    f"[Orchestrator] Extracting {len(kambi_sports)} sports ordered by Pinnacle coverage: "
                    f"{', '.join(f'{s}({c})' for s, c in top3)}..."
                )

            # Build league lookup from Pinnacle events in DB for filtering soft books
            # Works whether Pinnacle was extracted this run or a previous one
            sharp_league_rows = (
                self.session.query(Event.sport, Event.league)
                .filter(Event.id.in_(self.session.query(Odds.event_id).filter(Odds.provider_id == "pinnacle")))
                .distinct()
                .all()
            )

            self.sharp_leagues = {}
            for sport, league in sharp_league_rows:
                if not league:
                    continue
                if sport not in self.sharp_leagues:
                    self.sharp_leagues[sport] = set()
                normalized = league.lower().strip()
                self.sharp_leagues[sport].add(normalized)
                # Also strip country prefix: "England - Premier League" → "premier league"
                if " - " in league:
                    stripped = league.split(" - ", 1)[1].lower().strip()
                    self.sharp_leagues[sport].add(stripped)

            if self.sharp_leagues:
                total_leagues = sum(len(v) for v in self.sharp_leagues.values())
                logger.debug(
                    f"[Orchestrator] Sharp league filter: {total_leagues} leagues across {len(self.sharp_leagues)} sports"
                )

            # Platform consolidation: skip non-canonical providers when their
            # canonical is also in the list (e.g., skip expekt when unibet is present).
            # If extracting a non-canonical alone (e.g., "extract expekt"), it still works.
            if target_providers:
                target_set = set(target_providers)
                consolidated = []
                skipped_consolidated = []
                consolidated_map = {}  # pid -> canonical (for report)
                for pid in target_providers:
                    canonical = PROVIDER_CANONICAL.get(pid)
                    if canonical and canonical in target_set:
                        skipped_consolidated.append(f"{pid}->{canonical}")
                        consolidated_map[pid] = canonical
                    else:
                        consolidated.append(pid)
                if skipped_consolidated:
                    log_progress(
                        f"Platform consolidation: skipped {len(skipped_consolidated)} redundant providers "
                        f"({', '.join(skipped_consolidated)})"
                    )
                    results["consolidated_providers"] = consolidated_map
                target_providers = consolidated

            if target_providers:
                # Filter providers by circuit breaker status and health checks
                available_providers = []
                for pid in target_providers:
                    # Check circuit breaker
                    if self.circuit_breaker and self.circuit_breaker.is_open(pid):
                        log_progress(f"[{pid}] SKIPPED: Circuit breaker open")
                        continue

                    # Health check if enabled (with group-aware delays)
                    # Skip health check for browser-based providers (too slow for pre-check)
                    BROWSER_TYPES = (
                        "sbtech",
                        "gecko_v2",
                        "spectate",
                        "custom",
                        "tipwin",
                        "snabbare",
                        "interwetten",
                        "coolbet",
                        "tenbet",
                        "betconstruct",
                    )
                    provider_cfg = self.engine.get_provider(pid)
                    retriever_type = getattr(provider_cfg, "retriever_type", "")

                    if (
                        self.health_checker
                        and self.orchestrator_config.health_check.check_before_extraction
                        and retriever_type not in BROWSER_TYPES
                    ):
                        # Add delay between health checks for same-API groups
                        if self.pool_manager:
                            delay = self.pool_manager.get_health_check_delay(pid)
                            if delay > 0:
                                await asyncio.sleep(delay)

                        extractor = self.engine.get_extractor(pid)
                        health = await self.health_checker.check_provider(pid, extractor)

                        if not health.healthy:
                            log_progress(f"[{pid}] SKIPPED: Health check failed - {health.error}")
                            if self.circuit_breaker:
                                self.circuit_breaker.record_failure(pid)
                            continue

                    available_providers.append(pid)

                if not available_providers:
                    log_progress("No providers available (all circuits open)")
                else:
                    # Reorder providers for optimal type mixing
                    if self.pool_manager:
                        available_providers = self.pool_manager.get_interleaved_order(available_providers)
                        log_progress(f"Extracting {len(available_providers)} providers (type-aware scheduling)...")
                    else:
                        log_progress(f"Extracting {len(available_providers)} providers...")

                # Create tasks for parallel extraction
                # Provider timeout from config (None = no timeout, run to completion)
                default_provider_timeout = self.orchestrator_config.provider_timeout

                async def extract_with_error_handling(provider_id):
                    # Per-provider timeout override (e.g., Coolbet needs longer for Camoufox)
                    provider_cfg = self.engine.get_provider(provider_id)
                    per_provider_timeout = getattr(provider_cfg, "provider_timeout", None)
                    provider_timeout = per_provider_timeout if per_provider_timeout else default_provider_timeout

                    # Start provider metrics
                    if self.metrics:
                        self.metrics.start_provider(provider_id)

                    try:
                        # Check circuit breaker before attempting call
                        if self.circuit_breaker and not self.circuit_breaker.call(provider_id):
                            raise Exception("Circuit breaker open")

                        # Determine sports for this provider:
                        # If provider has supported_sports config, use intersection with
                        # Pinnacle-available sports. Otherwise use global kambi_sports.
                        provider_supported = getattr(provider_cfg, "supported_sports", None)
                        if provider_supported:
                            # Use provider's supported sports, filtered to ALLOWED + sharp
                            provider_sports = [s for s in provider_supported if s in ALLOWED_SPORTS]
                            if sharp_sports:
                                provider_sports = [s for s in provider_sports if s in sharp_sports]
                            # Order by Pinnacle event count.
                            # Browser providers with time budgets: smallest sports first
                            # so fast sports complete before the budget cuts in.
                            # API providers: largest sports first (most value first).
                            if pin_event_counts:
                                # DOM scrapers with time budgets: smallest sports first
                                # so more sports complete before the budget cuts off.
                                # API/WS providers: largest sports first (most value first).
                                DOM_SCRAPERS = ("custom", "tipwin", "interwetten", "coolbet", "tenbet")
                                prov_retriever = getattr(provider_cfg, "retriever_type", "")
                                is_dom_scraper = prov_retriever in DOM_SCRAPERS
                                provider_sports.sort(
                                    key=lambda s: pin_event_counts.get(s, 0),
                                    reverse=not is_dom_scraper,
                                )
                        else:
                            provider_sports = kambi_sports

                        # Set total sports count for progress tracking
                        if self.metrics:
                            self.metrics.set_provider_total_sports(provider_id, len(provider_sports))

                        # Use retry wrapper with optional timeout enforcement
                        _extract_coro = self._extract_provider_with_retry(
                            provider_id,
                            provider_sports,
                            max_events_per_sport,
                            sharp_sports,
                            sharp_leagues=getattr(self, "sharp_leagues", None),
                        )
                        if provider_timeout:
                            provider_results = await asyncio.wait_for(_extract_coro, timeout=provider_timeout)
                        else:
                            provider_results = await _extract_coro

                        # End provider metrics on success
                        if self.metrics:
                            self.metrics.end_provider(provider_id, success=True)

                        return provider_id, provider_results

                    except asyncio.TimeoutError:
                        error_msg = f"Timed out after {provider_timeout}s"

                        # Recover partial results from metrics (data already stored in DB)
                        partial_events = 0
                        partial_odds = 0
                        if self.metrics:
                            current_run = self.metrics.get_current_run()
                            if current_run and provider_id in current_run.providers:
                                pm = current_run.providers[provider_id]
                                partial_events = pm.total_events
                                partial_odds = pm.total_odds

                        if partial_events > 0:
                            error_msg = f"Timed out after {provider_timeout}s (partial: {partial_events} ev, {partial_odds} odds)"
                            logger.warning(f"[{provider_id}] {error_msg}")
                        else:
                            logger.error(f"[{provider_id}] {error_msg}")

                        if self.metrics:
                            self.metrics.end_provider(provider_id, success=False, error=error_msg)
                        if self.circuit_breaker:
                            self.circuit_breaker.record_failure(provider_id)

                        return provider_id, {
                            "events_processed": partial_events,
                            "events_new": 0,
                            "odds_processed": partial_odds,
                            "odds_new": 0,
                            "error": error_msg,
                        }

                    except Exception as e:
                        # Use repr(e) — str(NotImplementedError()) is empty, hiding the cause
                        error_str = str(e) or repr(e)
                        logger.error(f"Failed to extract from {provider_id}: {error_str}", exc_info=True)

                        # End provider metrics on failure
                        if self.metrics:
                            self.metrics.end_provider(provider_id, success=False, error=error_str)

                        # Record failure in circuit breaker
                        if self.circuit_breaker:
                            self.circuit_breaker.record_failure(provider_id)

                        return provider_id, {
                            "events_processed": 0,
                            "events_new": 0,
                            "odds_processed": 0,
                            "odds_new": 0,
                            "error": error_str,
                        }

                # Wrap provider extraction with type-aware concurrency control
                async def extract_with_concurrency_limit(provider_id):
                    if self.pool_manager:
                        # Use type-aware pool manager
                        async with self.pool_manager.acquire(provider_id):
                            return await extract_with_error_handling(provider_id)
                    else:
                        # Fallback to global semaphore
                        async with self.provider_semaphore:
                            return await extract_with_error_handling(provider_id)

                # Run providers with concurrency control:
                # - max_concurrency=1: sequential (one at a time)
                # - max_concurrency=2+: limited parallel (semaphore-based)
                # - max_concurrency=None/0: full parallel with pool manager
                effective_concurrency = max_concurrency if max_concurrency else (1 if sequential else 0)

                if effective_concurrency >= 1:
                    # Limited concurrency: semaphore controls how many run at once
                    concurrency_sem = asyncio.Semaphore(effective_concurrency)

                    async def extract_with_limited_concurrency(pid):
                        async with concurrency_sem:
                            log_progress(f"[{pid}] Starting (concurrency={effective_concurrency})")
                            return await extract_with_error_handling(pid)

                    provider_tasks = [extract_with_limited_concurrency(pid) for pid in available_providers]
                    provider_results_list = await asyncio.gather(*provider_tasks, return_exceptions=True)
                else:
                    # Full parallel mode: run all providers concurrently with pool limits
                    provider_tasks = [extract_with_concurrency_limit(pid) for pid in available_providers]
                    provider_results_list = await asyncio.gather(*provider_tasks, return_exceptions=True)

                provider_results_list = [r for r in provider_results_list if not isinstance(r, Exception)]

                # Collect results and log each provider
                for provider_id, provider_result in provider_results_list:
                    results["providers"][provider_id] = provider_result

                    if "error" in provider_result:
                        log_progress(f"[{provider_id}] FAILED: {provider_result['error']}")
                    else:
                        sport_errors = provider_result.get("sport_errors", [])
                        sports_ok = provider_result.get("sports_succeeded", 0)
                        sports_total = provider_result.get("sports_attempted", 0)

                        ev = provider_result["events_processed"]
                        odds = provider_result.get("odds_processed", 0)
                        ratio = f"{odds / ev:.1f}" if ev > 0 else "-"

                        # Match rate
                        matched = provider_result.get("events_matched", 0)
                        unmatched = provider_result.get("events_unmatched", 0)
                        match_total = matched + unmatched
                        match_str = f" | match={matched}/{match_total}" if match_total > 0 else ""

                        # Market breakdown
                        mc = provider_result.get("market_counts", {})
                        ml = mc.get("1x2", 0) + mc.get("moneyline", 0)
                        spr = mc.get("spread", 0)
                        tot = mc.get("total", 0)
                        mkt_str = f" | 1x2={ml} spr={spr} tot={tot}" if odds > 0 else ""

                        status = f"{sports_ok}/{sports_total} sports"
                        if sport_errors:
                            status += f" ({len(sport_errors)} err)"

                        # Flag silent failures: provider "succeeded" but returned 0 events
                        # Skip when all sports errored (those are noisy failures, not silent)
                        if ev == 0 and sports_ok > 0 and not sport_errors:
                            logger.warning(
                                f"[{provider_id}] DEGRADED: 0 events from "
                                f"{sports_ok}/{sports_total} sports — possible silent failure"
                            )
                            # Directly update metrics to avoid duration corruption
                            # from calling end_provider() a second time
                            if self.metrics and hasattr(self.metrics, "_current_run"):
                                pm = self.metrics._current_run.providers.get(provider_id)
                                if pm:
                                    pm.success = False
                                    pm.error = "Silent failure: 0 events extracted"

                        log_progress(f"[{provider_id}] {ev} ev, {odds} odds (r={ratio}), {status}{match_str}{mkt_str}")

            self.session.commit()

            # Run opportunity analysis
            log_progress("Running opportunity analysis...")
            from .analyzer import OpportunityAnalyzer

            analyzer = OpportunityAnalyzer(self.session)
            changed_ids = self._changed_event_ids if self._changed_event_ids else None
            analysis_results = analyzer.run(changed_event_ids=changed_ids)
            results["analysis"] = analysis_results
            log_progress(f"Analysis complete: {analysis_results['value']['found']} value bets")

            # Broadcast opportunity deltas to SSE clients
            if odds_broadcaster.client_count > 0 and analysis_results:
                for opp in analysis_results.get("added_opportunities", []):
                    odds_broadcaster.publish(
                        "opportunity_added",
                        {
                            "id": opp.id,
                            "type": opp.type if hasattr(opp, "type") else "value",
                            "edge_pct": getattr(opp, "edge_pct", None),
                            "odds1": getattr(opp, "odds1", None),
                            "fair_odds": getattr(opp, "fair_odds", None),
                            "stake": getattr(opp, "stake", None),
                            "event_id": getattr(opp, "event_id", None),
                            "provider1": getattr(opp, "provider1_id", None),
                            "outcome1": getattr(opp, "outcome1", None),
                            "market": getattr(opp, "market", None),
                        },
                    )
                for opp in analysis_results.get("updated_opportunities", []):
                    odds_broadcaster.publish(
                        "opportunity_update",
                        {
                            "id": opp.id,
                            "type": opp.type if hasattr(opp, "type") else "value",
                            "edge_pct": getattr(opp, "edge_pct", None),
                            "odds1": getattr(opp, "odds1", None),
                            "fair_odds": getattr(opp, "fair_odds", None),
                            "stake": getattr(opp, "stake", None),
                        },
                    )
                for item in analysis_results.get("removed_opportunities", []):
                    if isinstance(item, tuple) and len(item) == 2:
                        opp_id, opp_type = item
                    else:
                        opp_id, opp_type = item, "value"
                    odds_broadcaster.publish(
                        "opportunity_removed",
                        {
                            "id": opp_id,
                            "type": opp_type,
                            "reason": "edge_below_threshold",
                        },
                    )
                odds_broadcaster.publish(
                    "tier_complete",
                    {
                        "changed_events": len(self._changed_event_ids),
                    },
                )
                # Invalidate opportunity response cache so next request gets fresh data
                from ..api.routes.opportunities import _opp_cache

                _opp_cache.clear()

            # Count totals
            results["total_events"] = self.session.query(Event).count()
            results["total_odds"] = self.session.query(Odds).count()
            results["matched_events"] = self._count_matched_events()

            # End metrics collection and add to results
            current_run = None
            if self.metrics:
                # Get current run BEFORE ending it
                current_run = self.metrics.get_current_run()
                self.metrics.end_run()

                # Use the saved reference (now completed)
                if current_run:
                    results["metrics"] = {
                        "run_id": run_id,
                        "duration_seconds": current_run.duration_seconds,
                        "total_events": current_run.total_events,
                        "providers_succeeded": current_run.providers_succeeded,
                        "providers_failed": current_run.providers_failed,
                        "overall_success_rate": current_run.overall_success_rate,
                    }

            # Add circuit breaker stats
            if self.circuit_breaker:
                statuses = self.circuit_breaker.get_all_statuses()
                results["circuit_breaker"] = {
                    pid: {
                        "state": status.state.value,
                        "failure_count": status.failure_count,
                        "success_count": status.success_count,
                    }
                    for pid, status in statuses.items()
                }

            # Add cache stats
            if self.cache:
                results["cache_stats"] = self.cache.get_stats()

            # Add pool manager stats
            if self.pool_manager:
                results["pool_stats"] = self.pool_manager.get_stats()

            # Generate extraction report
            total_elapsed = time.time() - pipeline_start_time
            from .extraction_report import ExtractionReport

            report = ExtractionReport().generate(
                results=results,
                metrics=current_run,
                duration=total_elapsed,
                db_session=self.session,
            )
            results["report"] = report
            logger.info(f"\n{report}")
            if on_progress:
                on_progress(report)

            # Persist metrics and report to database
            if self.metrics and current_run:
                try:
                    self.metrics.persist_to_db(current_run, self.session, report=report, tier_name=tier_name)
                except Exception as e:
                    logger.error(f"[Metrics] Failed to persist run: {e}")

            # Log Pinnacle coverage delta (M10d — always, from Day 1)
            if tier_name != "sharp":
                try:
                    from src.ml.features.pinnacle_coverage import log_coverage

                    coverage_rows = log_coverage(self.session, run_id)
                    logger.info(f"Logged {coverage_rows} Pinnacle coverage rows")
                    self.session.commit()
                except Exception as e:
                    logger.debug(f"Pinnacle coverage logging skipped: {e}")

            # Log ML extraction features (best-effort)
            try:
                from src.ml.features.extraction_features import (
                    extract_extraction_features,
                    log_extraction_run,
                    update_extraction_outcomes,
                )

                # Compute average match rate across providers
                _avg_mr = 0.0
                if current_run and current_run.providers:
                    _rates = [p.match_rate for p in current_run.providers.values() if p.match_rate > 0]
                    _avg_mr = sum(_rates) / len(_rates) if _rates else 0.0

                run_features = extract_extraction_features(
                    run_id=run_id,
                    trigger=tier_name or "manual",
                    providers_attempted=current_run.providers_attempted if current_run else 0,
                    providers_succeeded=current_run.providers_succeeded if current_run else 0,
                    providers_failed=current_run.providers_failed if current_run else 0,
                    total_events=current_run.total_events if current_run else 0,
                    total_odds=current_run.total_odds if current_run else 0,
                    avg_match_rate=_avg_mr,
                )
                log_extraction_run(self.session, run_features)

                # Backfill opportunity outcomes from analysis results
                if analysis_results:
                    value_found = analysis_results.get("value", {}).get("found", 0)
                    dutch_found = analysis_results.get("dutch", {}).get("found", 0)
                    reverse_found = analysis_results.get("reverse", {}).get("found", 0) + analysis_results.get(
                        "reverse_value", {}
                    ).get("found", 0)
                    # Compute avg edge from opportunities table for this run's timeframe
                    avg_edge = None
                    try:
                        from sqlalchemy import func

                        from ..db.models import Opportunity

                        row = (
                            self.session.query(func.avg(Opportunity.edge_pct)).filter(Opportunity.edge_pct > 0).scalar()
                        )
                        avg_edge = float(row) if row else None
                    except Exception:
                        pass

                    update_extraction_outcomes(
                        self.session,
                        run_id=run_id,
                        value_bets_found=value_found,
                        avg_edge_pct=avg_edge,
                        dutch_opportunities_found=dutch_found,
                        reverse_opportunities_found=reverse_found,
                    )

                # Log per-provider value attribution
                if current_run and current_run.providers:
                    from sqlalchemy import func as sa_func

                    from src.ml.features.extraction_features import (
                        extract_provider_value,
                        log_provider_value,
                    )

                    from ..db.models import Opportunity

                    for pid, pm in current_run.providers.items():
                        matched = sum(1 for s in pm.sports.values() if s.events_processed > 0)
                        total_sports = len(pm.sports)
                        mr = matched / total_sports if total_sports > 0 else 0.0

                        # Count value bets attributed to this provider
                        vb_count = 0
                        vb_avg_edge = None
                        try:
                            row = (
                                self.session.query(
                                    sa_func.count(Opportunity.id),
                                    sa_func.avg(Opportunity.edge_pct),
                                )
                                .filter(
                                    Opportunity.provider1_id == pid,
                                    Opportunity.edge_pct > 0,
                                    Opportunity.type == "value",
                                )
                                .first()
                            )
                            if row:
                                vb_count = row[0] or 0
                                vb_avg_edge = float(row[1]) if row[1] else None
                        except Exception:
                            pass

                        pv_features = extract_provider_value(
                            run_id=run_id,
                            provider_id=pid,
                            events_extracted=pm.total_events,
                            odds_extracted=pm.total_odds,
                            duration_seconds=pm.duration_seconds,
                            match_rate=mr,
                            spread_count=sum(s.odds_processed for s in pm.sports.values()),
                            total_count=pm.total_odds,
                            value_bets_from_provider=vb_count,
                            avg_edge_from_provider=vb_avg_edge,
                        )
                        log_provider_value(self.session, pv_features)

                self.session.commit()
            except Exception as e:
                logger.debug(f"ML extraction feature logging skipped: {e}")

            # Run extraction analytics (best-effort, never blocks extraction)
            try:
                from src.ml.analytics.engine import AnalyticsEngine

                analytics = AnalyticsEngine()
                analytics.refresh(self.session, run_id)
                self.session.commit()
            except Exception as e:
                logger.debug(f"Extraction analytics skipped: {e}")

            # Daily ML model training (best-effort)
            try:
                import time as _time

                today = _time.strftime("%Y-%m-%d")
                if getattr(self, "_ml_last_train_day", None) != today:
                    from src.ml.training.train_all import TrainingOrchestrator

                    orch = TrainingOrchestrator()
                    train_results = orch.train_all(self.session)
                    self._ml_last_train_day = today
                    for model_name, status in train_results.items():
                        if status == "trained":
                            logger.info(f"ML model trained: {model_name}")
            except Exception as e:
                logger.debug(f"ML training check skipped: {e}")

            # Resolve CLV outcomes for ML feature rows (best-effort)
            try:
                from src.ml.feature_store import resolve_clv_outcomes

                resolved = resolve_clv_outcomes(self.session)
                if resolved > 0:
                    logger.info(f"Resolved CLV for {resolved} ML feature rows")
            except Exception:
                pass

            # Store daily macro data to options_flow (M9)
            try:
                from src.market_data.macro_provider import fetch_macro_snapshot
                from src.ml.models.macro_engine import store_daily_options_flow

                macro = await fetch_macro_snapshot()
                await store_daily_options_flow(self.session, macro)
            except Exception as e:
                logger.debug(f"Daily options_flow storage skipped: {e}")

            # Resolve trading signal outcomes
            try:
                from src.ml.feature_store import resolve_trading_outcomes

                resolved = resolve_trading_outcomes(self.session)
                if resolved:
                    logger.info(f"Resolved {resolved} trading signal outcomes")
            except Exception as e:
                logger.debug(f"Trading outcome resolution skipped: {e}")

        except asyncio.CancelledError:
            log_progress("Pipeline cancelled due to shutdown signal")
            results["cancelled"] = True
            return results

        except Exception as e:
            log_progress(f"Pipeline error: {e}")
            raise

        return results

    async def _extract_polymarket(self, max_per_sport: int = 100) -> dict:
        """
        Extract from Polymarket using tag-based fetching.

        Fetches ALL game events in one API call using tag_id=100639,
        instead of per-league series_id fetching which misses events.

        Args:
            max_per_sport: Not used (kept for API compatibility)

        Returns:
            Dictionary with extraction statistics
        """
        events_processed = 0
        events_new = 0
        odds_processed = 0
        odds_new = 0

        extractor = self.engine.get_extractor("polymarket")

        # Use pre-warmed sharp odds cache (shared across all providers)
        sharp_odds_cache = self._sharp_odds_cache
        api_elapsed = 0.0
        db_elapsed = 0.0

        if self.metrics:
            self.metrics.start_provider("polymarket")
            self.metrics.set_provider_total_sports("polymarket", 1)

        # Track per-sport metrics: {sport: {"events": N, "odds": N}}
        sport_counts = {}

        async with extractor as source:
            try:
                # Fetch ALL game events in one call (limit=1000)
                api_start = time.time()
                events = await source.extract_all(limit=1000)
                api_elapsed = time.time() - api_start
                logger.info(f"[polymarket] Fetched {len(events)} events (API: {api_elapsed:.1f}s)")

                db_start = time.time()

                # Offload all DB work to thread pool to keep event loop responsive
                def _store_polymarket_events():
                    _sport_counts = {}
                    _events_processed = 0
                    _events_new = 0
                    _odds_processed = 0

                    poly_session = get_session()
                    poly_session.autoflush = False
                    try:
                        with OddsBatchProcessor(poly_session, batch_size=500) as odds_batch:
                            for event in events:
                                if event.sport not in ALLOWED_SPORTS:
                                    continue

                                s = event.sport
                                if s not in _sport_counts:
                                    _sport_counts[s] = {"events": 0, "odds": 0}

                                ev_new, ev_processed_odds, _ = store_polymarket_event(
                                    poly_session,
                                    event,
                                    event.sport,
                                    self.event_cache,
                                    odds_batch=odds_batch,
                                    sharp_odds_cache=sharp_odds_cache,
                                    date_index=self.event_cache_by_date,
                                )

                                _sport_counts[s]["events"] += 1
                                _sport_counts[s]["odds"] += ev_processed_odds
                                _events_processed += 1
                                if ev_new:
                                    _events_new += 1
                                _odds_processed += ev_processed_odds

                            _odds_new, _odds_updated = odds_batch.get_stats()

                        poly_session.commit()
                    except Exception as e:
                        poly_session.rollback()
                        # StaleDataError = cleanup deleted event mid-update (race condition)
                        # Log and return partial results instead of failing the whole run
                        if "expected to update" in str(e) and "0 were matched" in str(e):
                            logger.warning(f"[polymarket] StaleDataError (cleanup race): {e}")
                        else:
                            raise
                    finally:
                        poly_session.close()

                    return {
                        "sport_counts": _sport_counts,
                        "events_processed": _events_processed,
                        "events_new": _events_new,
                        "odds_processed": _odds_processed,
                        "odds_new": _odds_new,
                        "odds_updated": _odds_updated,
                        "changed_event_ids": odds_batch.changed_event_ids,
                        "changed_records": odds_batch.get_changed_records(),
                    }

                poly_result = await asyncio.to_thread(_store_polymarket_events)
                sport_counts = poly_result["sport_counts"]
                events_processed = poly_result["events_processed"]
                events_new = poly_result["events_new"]
                odds_processed = poly_result["odds_processed"]
                odds_new = poly_result["odds_new"]
                odds_updated = poly_result["odds_updated"]

                # Start metrics for discovered sports (must be on main thread)
                if self.metrics:
                    for s in sport_counts:
                        self.metrics.start_sport("polymarket", s)

                self._changed_event_ids |= poly_result["changed_event_ids"]
                if odds_broadcaster.client_count > 0:
                    for record in poly_result["changed_records"]:
                        odds_broadcaster.publish(
                            "odds_update",
                            {
                                "event_id": record["event_id"],
                                "provider": record.get("provider_id", record.get("provider", "")),
                                "market": record.get("market", ""),
                                "outcome": record.get("outcome", ""),
                                "point": record.get("point"),
                                "odds": record["odds"],
                                "prev_odds": record.get("prev_odds"),
                            },
                        )
                db_elapsed = time.time() - db_start

            except Exception as e:
                logger.error(f"Polymarket extraction failed: {e}")
                if self.metrics:
                    for sport in sport_counts:
                        self.metrics.end_sport("polymarket", sport, success=False, error=str(e))
                    self.metrics.end_provider("polymarket", success=False, error=str(e))

            else:
                if self.metrics:
                    for sport, counts in sport_counts.items():
                        self.metrics.end_sport(
                            "polymarket",
                            sport,
                            events_processed=counts["events"],
                            odds_processed=counts["odds"],
                            success=True,
                        )
                    self.metrics.end_provider("polymarket", success=True)

            # Final commit
            self.session.commit()

        logger.info(
            f"Polymarket complete: {events_new} new events, {odds_new} new odds "
            f"(API: {api_elapsed:.1f}s, DB: {db_elapsed:.1f}s)"
        )

        return {
            "events_processed": events_processed,
            "events_new": events_new,
            "odds_processed": odds_processed,
            "odds_new": odds_new,
        }

    async def _extract_provider(
        self,
        provider_id: str,
        sports: list[str],
        limit: int,
        sharp_sports: set | None = None,
        sharp_leagues: dict | None = None,
    ) -> dict:
        """
        Extract from a specific provider across multiple sports.

        Uses SEQUENTIAL extraction for Kambi providers (shared rate limit)
        and PARALLEL extraction for all other providers.

        Args:
            provider_id: Provider identifier
            sports: List of sport names to extract
            limit: Maximum events per sport
            sharp_sports: Pre-computed set of sports with sharp data (optional)
            sharp_leagues: {sport: set(league_names)} from Pinnacle (optional)

        Returns:
            Dictionary with extraction statistics
        """
        # Get provider config for concurrency settings
        provider_config = self.engine.get_provider(provider_id)

        # Check if this provider needs sequential sport extraction
        retriever_type = getattr(provider_config, "retriever_type", "")
        is_kambi = retriever_type == "kambi"
        # Browser-based providers share a single page — concurrent goto() causes ERR_ABORTED
        is_single_page = retriever_type in (
            "sbtech",
            "gecko_v2",
            "spectate",
            "custom",
            "tipwin",
            "snabbare",
            "interwetten",
            "coolbet",
            "tenbet",
        )

        # Kambi + browser-based: sequential (1), Others: parallel (up to 4)
        concurrent_sports = (
            1
            if (is_kambi or is_single_page)
            else getattr(
                provider_config, "concurrent_leagues", self.orchestrator_config.max_concurrent_sports_per_provider
            )
        )

        # Create semaphore for sport-level concurrency
        sport_semaphore = asyncio.Semaphore(concurrent_sports)

        # Get or create extractor
        extractor = self.engine.get_extractor(provider_id)

        # Delay between sports for rate-limited APIs (seconds)
        # 0.5s is sufficient - Kambi rate limits at request level, not sport level
        sport_delay = 0.5 if is_kambi else 0.0

        # Log sports without sharp data but extract anyway
        # (events are still useful when sharp data arrives later)
        if sharp_sports is None:
            sharp_sports = await self.get_cached_sports()
        if sharp_sports:
            no_sharp = [s for s in sports if s not in sharp_sports]
            if no_sharp:
                logger.debug(f"[{provider_id}] Sports without sharp data (extracting anyway): {', '.join(no_sharp)}")

        # Use pre-warmed sharp odds cache (shared across all providers)
        sharp_odds_cache = self._sharp_odds_cache

        # Use larger batch size for sharp sources (fresh DB = all inserts)
        is_sharp = provider_id in SHARP_PROVIDERS
        batch_size = 500 if is_sharp else self.orchestrator_config.batch_commit_size

        # Sport timeout from config (default 60s)
        # Browser-based providers need longer: page load + rendering + data extraction
        # Per-provider sport_timeout override takes precedence (e.g., 10Bet has many competitions)
        per_provider_sport_timeout = getattr(provider_config, "sport_timeout", None)
        if per_provider_sport_timeout:
            sport_timeout = per_provider_sport_timeout
        else:
            base_sport_timeout = self.orchestrator_config.sport_timeout
            sport_timeout = base_sport_timeout * 2 if is_single_page else base_sport_timeout

        # Define per-sport extraction function
        async def extract_sport(sport: str, sport_index: int):
            """Extract single sport with error handling."""
            async with sport_semaphore:
                # Add delay between sports for Kambi (rate limit recovery)
                if sport_delay > 0 and sport_index > 0:
                    logger.debug(f"[{provider_id}] Waiting {sport_delay}s before {sport}...")
                    await asyncio.sleep(sport_delay)

                sport_start_time = time.time()

                if self.metrics:
                    self.metrics.start_sport(provider_id, sport)

                try:
                    # Get target leagues for this sport (if available)
                    target_leagues = sharp_leagues.get(sport) if sharp_leagues else None
                    events = await asyncio.wait_for(
                        extractor.extract(
                            sport,
                            limit=limit,
                            target_leagues=target_leagues,
                            run_id=getattr(self, "_current_run_id", None),
                        ),
                        timeout=sport_timeout,
                    )

                    # Store events with batch processor for better performance.
                    # Offloaded to a thread to keep the event loop responsive —
                    # all DB work (flush retries, fuzzy matching, commits) runs
                    # off the event loop so SSE streams and health checks stay alive.
                    is_soft = provider_id not in SHARP_PROVIDERS
                    sport_has_sharp = sharp_sports and sport in sharp_sports

                    def _store_sport_events():
                        """Synchronous DB storage — runs in thread pool."""
                        _events_processed = 0
                        _events_new = 0
                        _odds_processed = 0
                        _events_matched = 0
                        _events_unmatched = 0

                        sport_session = get_session()
                        sport_session.autoflush = False
                        try:
                            with OddsBatchProcessor(
                                sport_session,
                                batch_size=batch_size,
                            ) as odds_batch:
                                for event in events:
                                    is_new, odds_proc, _ = store_provider_event(
                                        session=sport_session,
                                        provider=provider_id,
                                        event=event,
                                        event_cache=self.event_cache,
                                        fuzzy_threshold=self.orchestrator_config.fuzzy_match.threshold,
                                        min_individual_score=self.orchestrator_config.fuzzy_match.min_individual_score,
                                        prefix_filter_length=self.orchestrator_config.fuzzy_match.prefix_filter_length,
                                        odds_batch=odds_batch,
                                        require_match=is_soft and sport_has_sharp,
                                        sharp_odds_cache=sharp_odds_cache,
                                        max_asymmetry_diff=self.orchestrator_config.fuzzy_match.max_asymmetry_diff,
                                        min_for_asymmetry_check=self.orchestrator_config.fuzzy_match.min_for_asymmetry_check,
                                        date_index=self.event_cache_by_date,
                                    )
                                    _events_processed += 1
                                    if is_new:
                                        _events_new += 1
                                        if is_soft and sport_has_sharp:
                                            _events_unmatched += 1
                                    elif is_soft and sport_has_sharp:
                                        _events_matched += 1
                                    _odds_processed += odds_proc

                            # After `with` block so __exit__ flushes the final batch
                            _odds_new, _odds_updated = odds_batch.get_stats()
                            _market_counts = odds_batch.get_market_counts()
                            _changed_eids = odds_batch.changed_event_ids
                            _changed_recs = odds_batch.get_changed_records()

                            try:
                                sport_session.commit()
                            except Exception as e:
                                sport_session.rollback()
                                err_lower = str(e).lower()
                                if "deadlock detected" in err_lower:
                                    import time as _time

                                    logger.info(f"[{provider_id}] {sport} deadlock on commit, retrying flush+commit...")
                                    _time.sleep(0.2)
                                    try:
                                        odds_batch.flush()
                                        sport_session.commit()
                                        logger.info(f"[{provider_id}] {sport} deadlock retry succeeded")
                                    except Exception as e2:
                                        logger.warning(f"[{provider_id}] {sport} deadlock retry failed: {e2}")
                                        sport_session.rollback()
                                else:
                                    logger.warning(f"[{provider_id}] {sport} commit failed: {e}")
                        finally:
                            sport_session.close()

                        return {
                            "events_processed": _events_processed,
                            "events_new": _events_new,
                            "odds_processed": _odds_processed,
                            "events_matched": _events_matched,
                            "events_unmatched": _events_unmatched,
                            "odds_new": _odds_new,
                            "odds_updated": _odds_updated,
                            "market_counts": _market_counts,
                            "changed_event_ids": _changed_eids,
                            "changed_records": _changed_recs,
                        }

                    result = await asyncio.to_thread(_store_sport_events)
                    events_processed = result["events_processed"]
                    events_new = result["events_new"]
                    odds_processed = result["odds_processed"]
                    events_matched = result["events_matched"]
                    events_unmatched = result["events_unmatched"]
                    odds_new = result["odds_new"]
                    odds_updated = result["odds_updated"]
                    market_counts = result["market_counts"]
                    self._changed_event_ids |= result["changed_event_ids"]
                    if odds_broadcaster.client_count > 0:
                        for record in result["changed_records"]:
                            odds_broadcaster.publish(
                                "odds_update",
                                {
                                    "event_id": record["event_id"],
                                    "provider": record.get("provider_id", record.get("provider", "")),
                                    "market": record.get("market", ""),
                                    "outcome": record.get("outcome", ""),
                                    "point": record.get("point"),
                                    "odds": record["odds"],
                                    "prev_odds": record.get("prev_odds"),
                                },
                            )

                    sport_elapsed = time.time() - sport_start_time
                    # Build detailed per-sport log line
                    ml_count = market_counts.get("1x2", 0) + market_counts.get("moneyline", 0)
                    spr_count = market_counts.get("spread", 0)
                    tot_count = market_counts.get("total", 0)
                    market_info = f" | 1x2={ml_count} spr={spr_count} tot={tot_count}" if odds_processed > 0 else ""
                    match_info = ""
                    if is_soft and sport_has_sharp:
                        match_info = f" | match={events_matched}/{events_matched + events_unmatched}"
                    logger.debug(
                        f"[{provider_id}] {sport}: {len(events)} ev, {odds_processed} odds in {sport_elapsed:.1f}s{market_info}{match_info}"
                    )

                    if self.metrics:
                        self.metrics.end_sport(
                            provider_id,
                            sport,
                            events_processed=events_processed,
                            events_new=events_new,
                            events_matched=events_matched,
                            events_unmatched=events_unmatched,
                            odds_processed=odds_processed,
                            odds_new=odds_new,
                            market_counts=market_counts,
                            success=True,
                        )

                    return {
                        "sport": sport,
                        "events_processed": events_processed,
                        "events_new": events_new,
                        "events_matched": events_matched,
                        "events_unmatched": events_unmatched,
                        "odds_processed": odds_processed,
                        "odds_new": odds_new,
                        "market_counts": market_counts,
                        "error": None,
                    }

                except asyncio.TimeoutError:
                    error_msg = f"Timed out after {sport_timeout}s"
                    logger.warning(f"[{provider_id}] {sport} {error_msg}")
                    if self.metrics:
                        self.metrics.end_sport(
                            provider_id,
                            sport,
                            success=False,
                            error=error_msg,
                        )
                    return {
                        "sport": sport,
                        "events_processed": 0,
                        "events_new": 0,
                        "events_matched": 0,
                        "events_unmatched": 0,
                        "odds_processed": 0,
                        "odds_new": 0,
                        "market_counts": {},
                        "error": {"error": error_msg, "error_type": "TimeoutError"},
                    }

                except Exception as e:
                    error_str = str(e) or repr(e)
                    logger.warning(f"[{provider_id}] {sport} failed: {error_str}", exc_info=True)
                    if self.metrics:
                        self.metrics.end_sport(
                            provider_id,
                            sport,
                            success=False,
                            error=error_str,
                        )
                    return {
                        "sport": sport,
                        "events_processed": 0,
                        "events_new": 0,
                        "events_matched": 0,
                        "events_unmatched": 0,
                        "odds_processed": 0,
                        "odds_new": 0,
                        "market_counts": {},
                        "error": {"error": error_str, "error_type": type(e).__name__},
                    }

        try:
            # Sport extraction (sequential for Kambi, parallel for others)
            sport_tasks = [extract_sport(sport, i) for i, sport in enumerate(sports)]
            sport_results = await asyncio.gather(*sport_tasks, return_exceptions=True)

            # Aggregate results
            total_events_processed = 0
            total_events_new = 0
            total_events_matched = 0
            total_events_unmatched = 0
            total_odds_processed = 0
            total_odds_new = 0
            sport_errors = []
            total_market_counts: dict[str, int] = {}

            for result in sport_results:
                total_events_processed += result["events_processed"]
                total_events_new += result["events_new"]
                total_events_matched += result.get("events_matched", 0)
                total_events_unmatched += result.get("events_unmatched", 0)
                total_odds_processed += result["odds_processed"]
                total_odds_new += result["odds_new"]

                for mkt, cnt in result.get("market_counts", {}).items():
                    total_market_counts[mkt] = total_market_counts.get(mkt, 0) + cnt

                if result["error"]:
                    sport_errors.append({"sport": result["sport"], **result["error"]})

            # Final commit
            self.session.commit()

            return {
                "events_processed": total_events_processed,
                "events_new": total_events_new,
                "events_matched": total_events_matched,
                "events_unmatched": total_events_unmatched,
                "odds_processed": total_odds_processed,
                "odds_new": total_odds_new,
                "market_counts": total_market_counts,
                "sport_errors": sport_errors,
                "sports_attempted": len(sports),
                "sports_succeeded": len(sports) - len(sport_errors),
            }

        except Exception as e:
            logger.error(f"[{provider_id}] Provider extraction failed: {e}", exc_info=True)
            raise

        finally:
            # Cleanup with timeout — Playwright browsers can hang on close
            if hasattr(extractor, "close"):
                try:
                    if asyncio.iscoroutinefunction(extractor.close):
                        await asyncio.wait_for(extractor.close(), timeout=10)
                    else:
                        extractor.close()
                except (asyncio.TimeoutError, Exception) as e:
                    logger.warning(f"[{provider_id}] Extractor cleanup failed: {e}")

    def _count_matched_events(self) -> int:
        """
        Count events with odds from multiple providers.

        Returns:
            Number of events with 2+ providers
        """

        return (
            self.session.query(Event)
            .join(Odds)
            .group_by(Event.id)
            .having(func.count(func.distinct(Odds.provider_id)) > 1)
            .count()
        )

    def get_matched_events(self, limit: int = 50) -> list[dict]:
        """
        Get events with odds from multiple providers.

        Args:
            limit: Maximum events to return

        Returns:
            List of event dictionaries with odds grouped by provider
        """
        from sqlalchemy.orm import joinedload

        # Use eager loading to avoid N+1 query problem
        matched = (
            self.session.query(Event)
            .join(Odds)
            .options(joinedload(Event.odds))
            .group_by(Event.id)
            .having(func.count(func.distinct(Odds.provider_id)) > 1)
            .limit(limit)
            .all()
        )

        results = []
        for event in matched:
            odds_by_provider = {}
            for odds in event.odds:
                odds_by_provider.setdefault(odds.provider_id, []).append(
                    {
                        "market": odds.market,
                        "outcome": odds.outcome,
                        "odds": odds.odds,
                    }
                )

            results.append(
                {
                    "id": event.id,
                    "home_team": event.home_team,
                    "away_team": event.away_team,
                    "sport": event.sport,
                    "start_time": event.start_time,
                    "providers": odds_by_provider,
                }
            )

        return results
