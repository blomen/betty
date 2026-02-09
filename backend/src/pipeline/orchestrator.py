"""
Pipeline Orchestrator

Main ExtractionPipeline class that coordinates extraction from all sources.
"""

import asyncio
import logging
import time
from typing import Callable

from ..factory import ExtractorFactory
from ..db.models import get_session, Event, Odds, Provider
from .storage import store_polymarket_event, store_provider_event, OddsBatchProcessor
from .pool_manager import ProviderPoolManager
from ..constants import SHARP_PROVIDERS, ALLOWED_SPORTS

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
        # {sport: [(id, home, away, date_str), ...]}
        # Events from Polymarket, Pinnacle, and all providers are added here
        self.event_cache = {}
        # Thread-safe lock for async access to event_cache
        self._cache_lock = asyncio.Lock()

        # Initialize orchestrator components
        self._init_orchestrator()

    async def get_cached_sports(self) -> set:
        """Get set of sports with cached events (thread-safe)."""
        async with self._cache_lock:
            return set(self.event_cache.keys())

    async def clear_cache(self):
        """Clear the event cache (thread-safe)."""
        async with self._cache_lock:
            self.event_cache.clear()

    def _init_orchestrator(self):
        """Initialize orchestrator components (called from __init__)."""
        # Get orchestrator config
        orchestrator_config = self.engine.config_loader.get_orchestrator_config()
        self.orchestrator_config = orchestrator_config

        # Create provider semaphore for global concurrency control (fallback)
        self.provider_semaphore = asyncio.Semaphore(
            orchestrator_config.max_concurrent_providers
        )

        # Initialize pool manager for type-aware scheduling
        if orchestrator_config.provider_groups:
            # Use config_loader.providers which has ProviderConfig objects
            self.pool_manager = ProviderPoolManager(
                orchestrator_config,
                self.engine.config_loader.providers
            )
            logger.info("[Orchestrator] Type-aware pool manager enabled")
        else:
            self.pool_manager = None
            logger.info("[Orchestrator] Using flat semaphore (no provider groups configured)")

        # Initialize metrics collector if enabled
        if orchestrator_config.metrics.enabled:
            from .metrics import MetricsCollector
            self.metrics = MetricsCollector(
                max_history=orchestrator_config.metrics.retention_count
            )
            logger.info("[Orchestrator] Metrics collection enabled")
        else:
            self.metrics = None

        # Initialize circuit breaker if enabled
        if orchestrator_config.circuit_breaker.enabled:
            from .circuit_breaker import CircuitBreaker
            self.circuit_breaker = CircuitBreaker(
                failure_threshold=orchestrator_config.circuit_breaker.failure_threshold,
                recovery_timeout_seconds=orchestrator_config.circuit_breaker.recovery_timeout_seconds,
                half_open_max_attempts=orchestrator_config.circuit_breaker.half_open_max_attempts
            )
            # Inject circuit breaker into factory for transport-level 429 detection
            self.engine.set_circuit_breaker(self.circuit_breaker)
            logger.info("[Orchestrator] Circuit breaker enabled (injected into factory)")
        else:
            self.circuit_breaker = None

        # Initialize cache if enabled
        if orchestrator_config.cache.enabled:
            from .cache import ResponseCache
            self.cache = ResponseCache(
                default_ttl_seconds=orchestrator_config.cache.ttl_seconds,
                max_entries=orchestrator_config.cache.max_entries,
                per_provider=orchestrator_config.cache.cache_per_provider
            )
            logger.info("[Orchestrator] Response cache enabled")
        else:
            self.cache = None

        # Initialize health checker if enabled
        if orchestrator_config.health_check.enabled:
            from .health import HealthChecker
            self.health_checker = HealthChecker(
                timeout_seconds=orchestrator_config.health_check.timeout_seconds
            )
            logger.info("[Orchestrator] Health checker enabled")
        else:
            self.health_checker = None

        # Initialize graceful shutdown if enabled
        if orchestrator_config.graceful_shutdown.enabled:
            import signal
            self._shutdown_event = asyncio.Event()
            self._shutdown_timeout = orchestrator_config.graceful_shutdown.shutdown_timeout_seconds
            self._cancel_pending = orchestrator_config.graceful_shutdown.cancel_pending_tasks
            self._register_signal_handlers()
            logger.info("[Orchestrator] Graceful shutdown enabled")
        else:
            self._shutdown_event = None

    def _ensure_providers(self):
        """Create provider records in DB if they don't exist."""
        # Get all providers from engine (returns dict of ProviderConfig dicts)
        all_providers = self.engine.providers

        providers = [
            ("polymarket", "Polymarket"),
            *[(pid, (cfg.get("domain") or pid).title()) for pid, cfg in all_providers.items()]
        ]

        for pid, name in providers:
            existing = self.session.query(Provider).filter(Provider.id == pid).first()
            if not existing:
                # Note: bonus_status is now tracked per-profile in ProfileProviderBonus table
                self.session.add(Provider(
                    id=pid,
                    name=name,
                ))
        self.session.commit()

    def _register_signal_handlers(self):
        """Register SIGINT/SIGTERM handlers for graceful shutdown."""
        import signal

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
                        retry_config.initial_backoff_seconds * (retry_config.exponential_base ** attempt),
                        retry_config.max_backoff_seconds
                    )
                    logger.warning(
                        f"[{provider_id}] Timeout on attempt {attempt+1}/{retry_config.max_retries}, "
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
                        retry_config.initial_backoff_seconds * (retry_config.exponential_base ** attempt),
                        retry_config.max_backoff_seconds
                    )
                    logger.warning(
                        f"[{provider_id}] Error on attempt {attempt+1}/{retry_config.max_retries}: {e}, "
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

        # Start metrics collection
        run_id = f"run_{int(time.time())}"
        if self.metrics:
            self.metrics.start_run(run_id)

        results = {
            "polymarket": {"events": 0, "odds": 0},
            "providers": {},
            "total_events": 0,
            "total_odds": 0,
            "matched_events": 0,
        }

        def log_progress(msg: str):
            """Log with elapsed time."""
            elapsed = time.time() - pipeline_start_time
            logger.info(f"[{elapsed:6.1f}s] {msg}")
            if on_progress:
                on_progress(f"[{elapsed:.1f}s] {msg}")

        def log(msg: str):
            """Legacy log function for compatibility."""
            logger.info(msg)
            if on_progress:
                on_progress(msg)

        log_progress("Pipeline started")

        # Clear stale events from previous runs
        await self.clear_cache()

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
                    pinnacle_result = await self._extract_provider(
                        "pinnacle",
                        target_sports,
                        max_events_per_sport
                    )
                    results["providers"]["pinnacle"] = pinnacle_result

                    if self.metrics:
                        self.metrics.end_provider("pinnacle", success=True)

                    pinnacle_elapsed = time.time() - pinnacle_start
                    log_progress(
                        f"Pinnacle done: {pinnacle_result.get('events_processed', 0)} events in {pinnacle_elapsed:.1f}s"
                    )
                except Exception as e:
                    logger.error(f"Pinnacle extraction failed: {e}")
                    results["providers"]["pinnacle"] = {"error": str(e)}
                    if self.metrics:
                        self.metrics.end_provider("pinnacle", success=False, error=str(e))

                # Remove pinnacle from target_providers to avoid re-extraction
                target_providers = [p for p in target_providers if p != "pinnacle"]

                # Commit Pinnacle data so Polymarket can query it for inversion detection
                self.session.commit()


            # Extract from Polymarket (will fuzzy match against Pinnacle events)
            if polymarket:
                log_progress("Extracting Polymarket...")
                poly_start = time.time()

                poly_results = await self._extract_polymarket(max_events_per_sport)
                results["polymarket"] = poly_results

                poly_elapsed = time.time() - poly_start
                log_progress(
                    f"Polymarket done: {poly_results['events_processed']} events in {poly_elapsed:.1f}s"
                )

            # Extract from other providers in parallel
            # Only extract sports in ALLOWED_SPORTS
            all_sports = set(s.kambi_sport for s in self.engine.sports)
            kambi_sports = sorted(s for s in all_sports if s in ALLOWED_SPORTS)

            # Pre-compute sharp sports from Pinnacle cache for filtering
            sharp_sports = await self.get_cached_sports()

            # Build league lookup from Pinnacle events in DB for filtering soft books
            # Works whether Pinnacle was extracted this run or a previous one
            sharp_league_rows = self.session.query(
                Event.sport, Event.league
            ).filter(
                Event.id.in_(
                    self.session.query(Odds.event_id).filter(Odds.provider_id == 'pinnacle')
                )
            ).distinct().all()

            self.sharp_leagues = {}
            for sport, league in sharp_league_rows:
                if not league:
                    continue
                if sport not in self.sharp_leagues:
                    self.sharp_leagues[sport] = set()
                normalized = league.lower().strip()
                self.sharp_leagues[sport].add(normalized)
                # Also strip country prefix: "England - Premier League" → "premier league"
                if ' - ' in league:
                    stripped = league.split(' - ', 1)[1].lower().strip()
                    self.sharp_leagues[sport].add(stripped)

            if self.sharp_leagues:
                total_leagues = sum(len(v) for v in self.sharp_leagues.values())
                logger.info(f"[Orchestrator] Sharp league filter: {total_leagues} leagues across {len(self.sharp_leagues)} sports")

            if target_providers:
                # Filter providers by circuit breaker status and health checks
                available_providers = []
                for pid in target_providers:
                    # Check circuit breaker
                    if self.circuit_breaker and self.circuit_breaker.is_open(pid):
                        log_progress(f"[{pid}] SKIPPED: Circuit breaker open")
                        continue

                    # Health check if enabled (with group-aware delays)
                    if (self.health_checker and
                        self.orchestrator_config.health_check.check_before_extraction):

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
                async def extract_with_error_handling(provider_id):
                    # Start provider metrics
                    if self.metrics:
                        self.metrics.start_provider(provider_id)

                    try:
                        # Check circuit breaker before attempting call
                        if self.circuit_breaker and not self.circuit_breaker.call(provider_id):
                            raise Exception("Circuit breaker open")

                        # Use retry wrapper (pass pre-computed sharp_sports and sharp_leagues)
                        provider_results = await self._extract_provider_with_retry(
                            provider_id, kambi_sports, max_events_per_sport, sharp_sports,
                            sharp_leagues=getattr(self, 'sharp_leagues', None),
                        )

                        # End provider metrics on success
                        if self.metrics:
                            self.metrics.end_provider(provider_id, success=True)

                        return provider_id, provider_results
                    except Exception as e:
                        logger.error(f"Failed to extract from {provider_id}: {e}", exc_info=True)

                        # End provider metrics on failure
                        if self.metrics:
                            self.metrics.end_provider(provider_id, success=False, error=str(e))

                        # Record failure in circuit breaker
                        if self.circuit_breaker:
                            self.circuit_breaker.record_failure(provider_id)

                        return provider_id, {
                            "events_processed": 0,
                            "events_new": 0,
                            "odds_processed": 0,
                            "odds_new": 0,
                            "error": str(e)
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

                # Run all available providers with concurrency limit
                provider_tasks = [extract_with_concurrency_limit(pid) for pid in available_providers]
                provider_results_list = await asyncio.gather(*provider_tasks)

                # Collect results and log each provider
                for provider_id, provider_result in provider_results_list:
                    results["providers"][provider_id] = provider_result

                    if "error" in provider_result:
                        log_progress(f"[{provider_id}] FAILED: {provider_result['error']}")
                    else:
                        sport_errors = provider_result.get("sport_errors", [])
                        sports_ok = provider_result.get("sports_succeeded", 0)
                        sports_total = provider_result.get("sports_attempted", 0)

                        status = f"{sports_ok}/{sports_total} sports"
                        if sport_errors:
                            status += f" ({len(sport_errors)} failed)"

                        log_progress(
                            f"[{provider_id}] {provider_result['events_processed']} events, {status}"
                        )

            self.session.commit()

            # Run opportunity analysis
            log_progress("Running opportunity analysis...")
            from .analyzer import OpportunityAnalyzer
            analyzer = OpportunityAnalyzer(self.session)
            analysis_results = analyzer.run()
            results["analysis"] = analysis_results
            log_progress(
                f"Analysis complete: {analysis_results['value']['found']} value bets"
            )

            # Count totals
            results["total_events"] = self.session.query(Event).count()
            results["total_odds"] = self.session.query(Odds).count()
            results["matched_events"] = self._count_matched_events()

            # End metrics collection and add to results
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

        except asyncio.CancelledError:
            log_progress("Pipeline cancelled due to shutdown signal")
            results["cancelled"] = True
            return results

        except Exception as e:
            log_progress(f"Pipeline error: {e}")
            raise

        finally:
            total_elapsed = time.time() - pipeline_start_time
            log_progress(f"Pipeline complete in {total_elapsed:.1f}s")
            log_progress(f"Total events in DB: {results['total_events']}")
            log_progress(f"Matched events: {results['matched_events']}")

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

        # Caches for Pinnacle data (static during a run)
        pinnacle_points_cache = {}
        sharp_odds_cache = {}
        api_elapsed = 0.0
        db_elapsed = 0.0

        if self.metrics:
            self.metrics.start_provider("polymarket")

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
                with OddsBatchProcessor(self.session, batch_size=500) as odds_batch:
                    for event in events:
                        # Skip sports not in ALLOWED_SPORTS
                        if event.sport not in ALLOWED_SPORTS:
                            continue

                        sport = event.sport
                        if sport not in sport_counts:
                            sport_counts[sport] = {"events": 0, "odds": 0}
                            if self.metrics:
                                self.metrics.start_sport("polymarket", sport)

                        ev_new, ev_processed_odds, _ = store_polymarket_event(
                            self.session,
                            event,
                            event.sport,
                            self.event_cache,
                            odds_batch=odds_batch,
                            pinnacle_points_cache=pinnacle_points_cache,
                            sharp_odds_cache=sharp_odds_cache,
                        )

                        sport_counts[sport]["events"] += 1
                        sport_counts[sport]["odds"] += ev_processed_odds
                        events_processed += 1
                        if ev_new:
                            events_new += 1
                        odds_processed += ev_processed_odds

                    # Get actual insert/update counts from batch processor
                    odds_new, odds_updated = odds_batch.get_stats()

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
                            "polymarket", sport,
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
            "odds_new": odds_new
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
        retriever_type = getattr(provider_config, 'retriever_type', '')
        is_kambi = retriever_type == 'kambi'
        # Browser-based providers share a single page — concurrent goto() causes ERR_ABORTED
        is_single_page = retriever_type in ('sbtech', 'gecko_v2', 'spectate', 'custom', 'tipwin', 'snabbare', 'interwetten', 'coolbet', 'tenbet')

        # Kambi + browser-based: sequential (1), Others: parallel (up to 4)
        concurrent_sports = 1 if (is_kambi or is_single_page) else getattr(
            provider_config,
            'concurrent_leagues',
            self.orchestrator_config.max_concurrent_sports_per_provider
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
                logger.info(f"[{provider_id}] Sports without sharp data (extracting anyway): {', '.join(no_sharp)}")

        # Caches for Pinnacle data (static during a run, shared across sports)
        pinnacle_points_cache = {}
        sharp_odds_cache = {}

        # Use larger batch size for sharp sources (fresh DB = all inserts)
        is_sharp = provider_id in SHARP_PROVIDERS
        batch_size = 500 if is_sharp else self.orchestrator_config.batch_commit_size

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
                    events = await extractor.extract(sport, limit=limit, target_leagues=target_leagues)

                    # Store events with batch processor for better performance
                    events_processed = 0
                    events_new = 0
                    odds_processed = 0

                    with OddsBatchProcessor(
                        self.session,
                        batch_size=batch_size,
                    ) as odds_batch:
                        is_soft = provider_id not in SHARP_PROVIDERS
                        sport_has_sharp = sharp_sports and sport in sharp_sports
                        events_matched = 0
                        events_unmatched = 0
                        for event in events:
                            is_new, odds_proc, _ = store_provider_event(
                                session=self.session,
                                provider=provider_id,
                                event=event,
                                event_cache=self.event_cache,
                                fuzzy_threshold=self.orchestrator_config.fuzzy_match.threshold,
                                prefix_filter_length=self.orchestrator_config.fuzzy_match.prefix_filter_length,
                                odds_batch=odds_batch,
                                require_match=is_soft and sport_has_sharp,
                                pinnacle_points_cache=pinnacle_points_cache,
                                sharp_odds_cache=sharp_odds_cache,
                            )
                            events_processed += 1
                            if is_new:
                                events_new += 1
                                if is_soft and sport_has_sharp:
                                    events_unmatched += 1
                            elif is_soft and sport_has_sharp:
                                events_matched += 1
                            odds_processed += odds_proc

                        # Get actual insert/update counts from batch processor
                        odds_new, odds_updated = odds_batch.get_stats()

                    sport_elapsed = time.time() - sport_start_time
                    match_info = ""
                    if is_soft and sport_has_sharp:
                        match_info = f" (matched: {events_matched}, unmatched: {events_unmatched})"
                    logger.info(
                        f"[{provider_id}] {sport}: {len(events)} events in {sport_elapsed:.1f}s{match_info}"
                    )

                    if self.metrics:
                        self.metrics.end_sport(
                            provider_id, sport,
                            events_processed=events_processed,
                            odds_processed=odds_processed,
                            success=True,
                        )

                    return {
                        "sport": sport,
                        "events_processed": events_processed,
                        "events_new": events_new,
                        "odds_processed": odds_processed,
                        "odds_new": odds_new,
                        "error": None
                    }

                except Exception as e:
                    logger.warning(f"[{provider_id}] {sport} failed: {e}", exc_info=True)
                    if self.metrics:
                        self.metrics.end_sport(
                            provider_id, sport,
                            success=False,
                            error=str(e),
                        )
                    return {
                        "sport": sport,
                        "events_processed": 0,
                        "events_new": 0,
                        "odds_processed": 0,
                        "odds_new": 0,
                        "error": {"error": str(e), "error_type": type(e).__name__}
                    }

        try:
            # Sport extraction (sequential for Kambi, parallel for others)
            sport_tasks = [extract_sport(sport, i) for i, sport in enumerate(sports)]
            sport_results = await asyncio.gather(*sport_tasks)

            # Aggregate results
            total_events_processed = 0
            total_events_new = 0
            total_odds_processed = 0
            total_odds_new = 0
            sport_errors = []

            for result in sport_results:
                total_events_processed += result["events_processed"]
                total_events_new += result["events_new"]
                total_odds_processed += result["odds_processed"]
                total_odds_new += result["odds_new"]

                if result["error"]:
                    sport_errors.append({
                        "sport": result["sport"],
                        **result["error"]
                    })

            # Final commit
            self.session.commit()

            return {
                "events_processed": total_events_processed,
                "events_new": total_events_new,
                "odds_processed": total_odds_processed,
                "odds_new": total_odds_new,
                "sport_errors": sport_errors,
                "sports_attempted": len(sports),
                "sports_succeeded": len(sports) - len(sport_errors)
            }

        except Exception as e:
            logger.error(f"[{provider_id}] Provider extraction failed: {e}", exc_info=True)
            raise

        finally:
            # Cleanup
            if hasattr(extractor, 'close'):
                if asyncio.iscoroutinefunction(extractor.close):
                    await extractor.close()
                else:
                    extractor.close()

    def _count_matched_events(self) -> int:
        """
        Count events with odds from multiple providers.

        Returns:
            Number of events with 2+ providers
        """
        from sqlalchemy import func

        return self.session.query(Event).join(Odds).group_by(Event.id).having(
            func.count(func.distinct(Odds.provider_id)) > 1
        ).count()

    def get_matched_events(self, limit: int = 50) -> list[dict]:
        """
        Get events with odds from multiple providers.

        Args:
            limit: Maximum events to return

        Returns:
            List of event dictionaries with odds grouped by provider
        """
        from sqlalchemy import func
        from sqlalchemy.orm import joinedload

        # Use eager loading to avoid N+1 query problem
        matched = self.session.query(Event)\
            .join(Odds)\
            .options(joinedload(Event.odds))\
            .group_by(Event.id)\
            .having(func.count(func.distinct(Odds.provider_id)) > 1)\
            .limit(limit)\
            .all()

        results = []
        for event in matched:
            odds_by_provider = {}
            for odds in event.odds:
                odds_by_provider.setdefault(odds.provider_id, []).append({
                    "market": odds.market,
                    "outcome": odds.outcome,
                    "odds": odds.odds,
                })

            results.append({
                "id": event.id,
                "home_team": event.home_team,
                "away_team": event.away_team,
                "sport": event.sport,
                "start_time": event.start_time,
                "providers": odds_by_provider,
            })

        return results
