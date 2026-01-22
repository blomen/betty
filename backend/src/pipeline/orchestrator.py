"""
Pipeline Orchestrator

Main ExtractionPipeline class that coordinates extraction from all sources.
"""

import asyncio
import logging
from typing import Callable

from ..factory import ExtractorFactory
from ..db.models import get_session, Event, Odds, Provider
from .storage import store_polymarket_event, store_provider_event

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

        # Cache for Polymarket events to enable fuzzy matching
        # Dict indexed by sport for O(1) sport lookup
        # {sport: [(id, home, away, date_str), ...]}
        self.polymarket_events = {}

    def _ensure_providers(self):
        """Create provider records in DB if they don't exist."""
        # Get all providers from engine (returns dict of ProviderConfig dicts)
        all_providers = self.engine.providers

        providers = [
            ("polymarket", "Polymarket"),
            *[(pid, (cfg.get("domain") or pid).title()) for pid, cfg in all_providers.items()]
        ]

        for pid, name in providers:
            if not self.session.query(Provider).filter(Provider.id == pid).first():
                self.session.add(Provider(id=pid, name=name, balance=0))
        self.session.commit()

    async def run(
        self,
        polymarket: bool = True,
        providers: list[str] | None = None,
        max_events_per_sport: int = 100,
        on_progress: Callable[[str], None] | None = None,
    ) -> dict:
        """
        Run extraction from all sources.

        Args:
            polymarket: Extract from Polymarket (default: True)
            providers: List of provider IDs to extract (default: all enabled)
            max_events_per_sport: Limit events per sport (default: 100)
            on_progress: Optional callback for progress updates

        Returns:
            Dictionary with extraction results:
            {
                "polymarket": {...},
                "providers": {provider_id: {...}},
                "total_events": int,
                "matched_events": int
            }
        """
        results = {
            "polymarket": {"events": 0, "odds": 0},
            "providers": {},
            "total_events": 0,
            "matched_events": 0,
        }

        def log(msg: str):
            logger.info(msg)
            if on_progress:
                on_progress(msg)

        # Extract from Polymarket
        if polymarket:
            log("Extracting from Polymarket...")
            poly_results = await self._extract_polymarket(max_events_per_sport)
            results["polymarket"] = poly_results
            log(f"  Polymarket: {poly_results['events_processed']} processed ({poly_results['events_new']} new), {poly_results['odds_new']} new odds")

        # Extract from other providers
        target_providers = providers if providers is not None else self.engine.get_enabled_providers()
        kambi_sports = sorted(list(set(s.kambi_sport for s in self.engine.sports)))

        for provider_id in target_providers:
            log(f"Extracting from {provider_id}...")
            try:
                provider_results = await self._extract_provider(provider_id, kambi_sports, max_events_per_sport)
                results["providers"][provider_id] = provider_results
                log(f"  {provider_id}: {provider_results['events_processed']} processed ({provider_results['events_new']} new), {provider_results['odds_new']} new odds")
            except Exception as e:
                logger.error(f"Failed to extract from {provider_id}: {e}", exc_info=True)
                results["providers"][provider_id] = {
                    "events_processed": 0,
                    "events_new": 0,
                    "odds_processed": 0,
                    "odds_new": 0,
                    "error": str(e)
                }

        self.session.commit()

        # Count totals
        results["total_events"] = self.session.query(Event).count()
        results["matched_events"] = self._count_matched_events()

        log(f"Total events in DB: {results['total_events']}")
        log(f"Matched events: {results['matched_events']}")

        return results

    async def _extract_polymarket(self, max_per_sport: int = 100) -> dict:
        """
        Extract from Polymarket using sports config.

        Args:
            max_per_sport: Maximum events per sport

        Returns:
            Dictionary with extraction statistics
        """
        events_processed = 0
        events_new = 0
        odds_processed = 0
        odds_new = 0

        # Use the generic extractor factory for Polymarket too
        extractor = self.engine.get_extractor("polymarket")

        async with extractor as source:
            # Iterate through configured sports and extract
            for sport_config in self.engine.sports:
                try:
                    events = await source.extract(sport_config.name, limit=max_per_sport)

                    for event in events:
                        # event is now StandardEvent
                        ev_new, ev_processed_odds, ev_new_odds = store_polymarket_event(
                            self.session,
                            event,
                            sport_config.kambi_sport,
                            self.polymarket_events,
                        )

                        events_processed += 1
                        if ev_new:
                            events_new += 1

                        odds_processed += ev_processed_odds
                        odds_new += ev_new_odds

                except Exception as e:
                    logger.debug(f"Polymarket {sport_config.name}: {e}")

            self.session.commit()

        logger.info(f"Polymarket extraction complete. New events: {events_new}, New odds: {odds_new}")

        return {
            "events_processed": events_processed,
            "events_new": events_new,
            "odds_processed": odds_processed,
            "odds_new": odds_new
        }

    async def _extract_provider(self, provider_id: str, sports: list[str], limit: int) -> dict:
        """
        Extract from a specific provider.

        Args:
            provider_id: Provider identifier
            sports: List of sport names to extract
            limit: Maximum events per sport

        Returns:
            Dictionary with extraction statistics
        """
        events_processed = 0
        events_new = 0
        odds_processed = 0
        odds_new = 0

        extractor = self.engine.get_extractor(provider_id)

        for sport in sports:
            try:
                events = await extractor.extract(sport, limit=limit)
                logger.info(f"DEBUG: {provider_id} - {sport}: {len(events)} events")

                for event in events:
                    ev_new, ev_processed_odds, ev_new_odds = store_provider_event(
                        self.session,
                        event,
                        provider_id,
                        self.polymarket_events,
                    )
                    logger.debug(f"DEBUG: {provider_id} stored event {event.id}: new={ev_new}, odds={ev_new_odds}")

                    events_processed += 1
                    if ev_new:
                        events_new += 1

                    odds_processed += ev_processed_odds
                    odds_new += ev_new_odds

                self.session.commit()

                # Close if it needs closing (e.g. Spectate/Playwright)
                if hasattr(extractor, 'close'):
                    if asyncio.iscoroutinefunction(extractor.close):
                        await extractor.close()
                    else:
                        extractor.close()

            except Exception as e:
                logger.debug(f"Provider {provider_id}/{sport}: {e}")
                # Ensure cleanup on error
                if hasattr(extractor, 'close'):
                    if asyncio.iscoroutinefunction(extractor.close):
                        await extractor.close()

        return {
            "events_processed": events_processed,
            "events_new": events_new,
            "odds_processed": odds_processed,
            "odds_new": odds_new
        }

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
