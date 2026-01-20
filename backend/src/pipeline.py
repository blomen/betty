"""
Unified Extraction Pipeline

Extracts events from all configured sources using sports.py as the single source of truth.
Stores events with canonical IDs for cross-provider matching.
Uses fuzzy matching for improved team name resolution.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Callable

from src.config.sports import SPORTS_CONFIG, get_kambi_sports, POLYMARKET_GAME_BETS_TAG_ID
from src.sources.polymarket import PolymarketSource, PolymarketEvent
from src.extractors.kambi import get_extractor, KambiEvent, KAMBI_PROVIDERS
from src.db.models import init_db, get_session, Event, Odds, Provider
from src.utils.matching import (
    normalize_team_name,
    fuzzy_match_teams,
    get_sport_from_league,
    LEAGUE_TO_SPORT,
)

logger = logging.getLogger(__name__)


# ============ Team Name Parsing ============

# Tournament/league prefixes to strip from team names
TOURNAMENT_PREFIXES = [
    # Tennis Grand Slams
    "australian open mens ", "australian open womens ", "australian open ",
    "us open mens ", "us open womens ", "us open ",
    "french open mens ", "french open womens ", "french open ",
    "wimbledon mens ", "wimbledon womens ", "wimbledon ",
    # Tennis tours
    "atp tour ", "atp ", "wta tour ", "wta ",
    "itf mens ", "itf womens ", "itf ",
    # Football competitions
    "uefa champions league ", "champions league ",
    "uefa europa league ", "europa league ",
    "uefa europa conference league ", "conference league ",
    "fifa world cup ", "world cup ",
    "copa america ", "euro 2024 ", "euro 2028 ",
    "african cup of nations ", "afcon ",
    # Leagues with common prefixes
    "english premier league ", "premier league ",
    "spanish la liga ", "la liga ",
    "german bundesliga ", "bundesliga ",
    "italian serie a ", "serie a ",
    "french ligue 1 ", "ligue 1 ",
    # US Sports
    "nba ", "nfl ", "nhl ", "mlb ",
    "ncaa ", "college ",
    # Generic
    "mens ", "womens ", "women's ", "men's ",
]

def strip_tournament_prefix(title: str) -> str:
    """Strip tournament/league prefixes from event title recursively."""
    title_lower = title.lower()
    for prefix in TOURNAMENT_PREFIXES:
        if title_lower.startswith(prefix):
            # Recurse to handle multiple prefixes (e.g. "Australian Open" then "Mens")
            return strip_tournament_prefix(title[len(prefix):].strip())
    return title


def parse_teams_from_title(title: str) -> tuple[str, str] | None:
    """Parse home and away teams from event title."""
    # Remove "More Markets" suffix
    title = re.sub(r'\s*-\s*More Markets$', '', title)
    
    # Strip tournament prefixes (e.g., "Australian Open Mens" from tennis)
    title = strip_tournament_prefix(title)
    
    for sep in [' vs. ', ' vs ', ' @ ']:
        if sep in title:
            parts = title.split(sep, 1)
            if len(parts) == 2:
                return (parts[0].strip(), parts[1].strip())
    return None


def generate_canonical_id(sport: str, home: str, away: str, start_time: datetime | str) -> str:
    """Generate canonical event ID for cross-provider matching."""
    home_norm = normalize_team_name(home)
    away_norm = normalize_team_name(away)
    
    if isinstance(start_time, datetime):
        date_str = start_time.strftime('%Y%m%d')
    elif isinstance(start_time, str):
        try:
            dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            date_str = dt.strftime('%Y%m%d')
        except:
            date_str = 'unknown'
    else:
        date_str = 'unknown'
    
    # Use kambi_sport for consistency
    return f"{sport}:{home_norm}:{away_norm}:{date_str}"


# ============ Extraction Pipeline ============

class ExtractionPipeline:
    """
    Unified extraction from all sources using sports.py config.
    
    Usage:
        pipeline = ExtractionPipeline()
        await pipeline.run()
    """
    
    def __init__(self, db_session=None):
        self.session = db_session or get_session()
        self._ensure_providers()
        
        # Cache for Polymarket events to enable fuzzy matching
        # List of (id, sport, home, away, date_str)
        self.polymarket_events = []
    
    def _ensure_providers(self):
        """Create provider records if they don't exist."""
        providers = [
            ("polymarket", "Polymarket"),
            *[(pid, pid.title()) for pid in KAMBI_PROVIDERS.keys()]
        ]
        
        for pid, name in providers:
            if not self.session.query(Provider).filter(Provider.id == pid).first():
                self.session.add(Provider(id=pid, name=name, balance=0))
        self.session.commit()
    
    async def run(
        self,
        polymarket: bool = True,
        kambi_providers: list[str] | None = None,
        max_events_per_sport: int = 100,
        on_progress: Callable[[str], None] | None = None,
    ) -> dict:
        """
        Run extraction from all sources.
        
        Args:
            polymarket: Extract from Polymarket
            kambi_providers: List of Kambi providers (default: all)
            max_events_per_sport: Limit events per sport
            on_progress: Callback for progress updates
        """
        results = {
            "polymarket": {"events": 0, "odds": 0},
            "kambi": {},
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
        
        # Extract from Kambi providers
        providers = kambi_providers or list(KAMBI_PROVIDERS.keys())
        kambi_sports = get_kambi_sports()
        
        for provider in providers:
            log(f"Extracting from {provider}...")
            try:
                provider_results = await self._extract_kambi(provider, kambi_sports)
                results["kambi"][provider] = provider_results
                log(f"  {provider}: {provider_results['events_processed']} processed ({provider_results['events_new']} new), {provider_results['odds_new']} new odds")
            except Exception as e:
                logger.error(f"Failed to extract from {provider}: {e}")
                results["kambi"][provider] = {"events_processed": 0, "events_new": 0, "odds_processed": 0, "odds_new": 0, "error": str(e)}
        
        self.session.commit()
        
        # Count totals
        results["total_events"] = self.session.query(Event).count()
        results["matched_events"] = self._count_matched_events()
        
        log(f"Total events in DB: {results['total_events']}")
        log(f"Matched events: {results['matched_events']}")
        
        return results
    
    async def _extract_polymarket(self, max_per_sport: int = 100) -> dict:
        """Extract from Polymarket using sports config."""
        events_processed = 0
        events_new = 0
        odds_processed = 0
        odds_new = 0
        
        async with PolymarketSource() as source:
            for sport in SPORTS_CONFIG:
                try:
                    events = await source.get_game_events(
                        series_id=sport.polymarket_series_id,
                        sport_name=sport.name,
                        limit=max_per_sport,
                    )
                    
                    for event in events:
                        ev_new, ev_processed_odds, ev_new_odds = self._store_polymarket_event(event, sport.kambi_sport)
                        
                        events_processed += 1
                        if ev_new:
                            events_new += 1
                        
                        odds_processed += ev_processed_odds
                        odds_new += ev_new_odds
                            
                except Exception as e:
                    logger.debug(f"Polymarket {sport.name}: {e}")
            
            self.session.commit()
        
        return {
            "events_processed": events_processed,
            "events_new": events_new, 
            "odds_processed": odds_processed, 
            "odds_new": odds_new
        }
    
    async def _extract_kambi(self, provider: str, sports: list[str]) -> dict:
        """Extract from a Kambi provider."""
        events_processed = 0
        events_new = 0
        odds_processed = 0
        odds_new = 0
        
        extractor = get_extractor(provider)
        
        for sport in sports:
            try:
                events = await extractor.extract(sport, max_groups=500)  # Extract all
                
                for event in events:
                    ev_new, ev_processed_odds, ev_new_odds = self._store_kambi_event(event, provider)
                    
                    events_processed += 1
                    if ev_new:
                        events_new += 1
                    
                    odds_processed += ev_processed_odds
                    odds_new += ev_new_odds
                
                self.session.commit()
                        
            except Exception as e:
                logger.debug(f"Kambi {provider}/{sport}: {e}")
        
        return {
            "events_processed": events_processed,
            "events_new": events_new, 
            "odds_processed": odds_processed, 
            "odds_new": odds_new
        }

    def _store_polymarket_event(self, event: PolymarketEvent, kambi_sport: str) -> tuple[bool, int, int]:
        """
        Store Polymarket event.
        Returns: (is_new_event, odds_processed, odds_new)
        """
        teams = parse_teams_from_title(event.title)
        if not teams:
            logger.warning(f"Failed to parse teams from: {event.title}")
            return False, 0, 0
        
        home_team, away_team = teams
        canonical_id = generate_canonical_id(kambi_sport, home_team, away_team, event.start_time)
        
        # Cache for fuzzy matching
        if isinstance(event.start_time, datetime):
            date_str = event.start_time.strftime("%Y%m%d")
        else:
            date_str = str(event.start_time)[:10].replace('-', '') if event.start_time else "00000000"
            
        self.polymarket_events.append((canonical_id, kambi_sport, home_team, away_team, date_str))
        
        # Create/get event
        db_event = self.session.query(Event).filter(Event.id == canonical_id).first()
        is_new_event = False
        
        if not db_event:
            db_event = Event(
                id=canonical_id,
                sport=kambi_sport,
                league=event.sport,
                home_team=home_team,
                away_team=away_team,
                start_time=event.start_time,
            )
            self.session.add(db_event)
            is_new_event = True
        
        # Store odds
        odds_processed = 0
        odds_new = 0
        
        for market in event.markets:
            if not market.get("is_active"):
                continue
            
            market_type = self._normalize_market(market.get("question", ""))
            outcomes = market.get("outcomes", [])
            odds_values = market.get("decimal_odds", [])
            
            for outcome, odds in zip(outcomes, odds_values):
                if odds <= 1 or odds > 100:
                    continue
                
                odds_processed += 1
                outcome_norm = self._normalize_outcome(outcome, home_team, away_team)
                odds_new += self._upsert_odds(canonical_id, "polymarket", market_type, outcome_norm, odds)
        
        return is_new_event, odds_processed, odds_new
    
    def _store_kambi_event(self, event: KambiEvent, provider: str) -> tuple[bool, int, int]:
        """
        Store Kambi event.
        Returns: (is_new_event, odds_processed, odds_new)
        """
        # Generate default ID
        default_id = generate_canonical_id(
            event.sport, event.home_team, event.away_team, event.start_time
        )
        
        # Check for fuzzy match against existing Polymarket events
        matched_id = None
        
        # 1. Check if default ID exists ( Exact match)
        if self.session.query(Event.id).filter(Event.id == default_id).first():
            matched_id = default_id
        else:
            # 2. Fuzzy match against memory cache
            from src.utils.matching import match_events
            
            event_date = event.start_time.split('T')[0].replace('-', '')
            
            for poly_id, p_sport, p_home, p_away, p_date in self.polymarket_events:
                if p_sport != event.sport:
                    continue
                
                # Check match
                result = match_events(
                    p_home, p_away, p_date,
                    event.home_team, event.away_team, event_date,
                    event.sport,
                    threshold=85 # Strict threshold
                )
                
                if result.matched:
                    matched_id = poly_id
                    # Only log NEW matches to avoid spam
                    # logger.info(f"MATCH: {provider} '{event.home_team}' -> Poly '{p_home}' ({poly_id})")
                    break
        
        # Use matched ID or fall back to default
        final_id = matched_id or default_id
        
        # Parse start time
        try:
            start_dt = datetime.fromisoformat(event.start_time.replace('Z', '+00:00'))
        except:
            start_dt = None
        
        # Create/get event
        db_event = self.session.query(Event).filter(Event.id == final_id).first()
        is_new_event = False
        
        if not db_event:
            db_event = Event(
                id=final_id,
                sport=event.sport,
                league=event.league,
                home_team=event.home_team,
                away_team=event.away_team,
                start_time=start_dt,
            )
            self.session.add(db_event)
            is_new_event = True
        
        # Store odds
        odds_processed = 0
        odds_new = 0
        
        for market in event.markets:
            market_type = self._normalize_market(market.get('type', ''))
            
            for outcome in market.get('outcomes', []):
                outcome_name = self._normalize_outcome(
                    outcome.get('name', ''), event.home_team, event.away_team
                )
                odds_value = outcome.get('odds', 0)
                
                if odds_value <= 1:
                    continue
                
                odds_processed += 1
                odds_new += self._upsert_odds(final_id, provider, market_type, outcome_name, odds_value)
        
        return is_new_event, odds_processed, odds_new
    
    def _upsert_odds(self, event_id: str, provider: str, market: str, outcome: str, odds: float) -> int:
        """Insert or update odds, returns 1 if new."""
        existing = self.session.query(Odds).filter(
            Odds.event_id == event_id,
            Odds.provider_id == provider,
            Odds.market == market,
            Odds.outcome == outcome,
        ).first()
        
        if existing:
            existing.odds = odds
            existing.updated_at = datetime.now(timezone.utc)
            return 0
        else:
            self.session.add(Odds(
                event_id=event_id,
                provider_id=provider,
                market=market,
                outcome=outcome,
                odds=odds,
            ))
            return 1
    
    def _normalize_market(self, market: str) -> str:
        """Normalize market type."""
        market = market.lower().strip()
        
        if '1x2' in market or 'full time' in market or ('will' in market and 'win' in market):
            return '1x2'
        if 'over' in market and 'under' in market or 'o/u' in market:
            return 'over_under'
        if 'spread' in market or 'handicap' in market:
            return 'spread'
        if 'draw' in market:
            return '1x2'
        
        return market.replace(' ', '_')[:30]
    
    def _normalize_outcome(self, outcome: str, home: str = "", away: str = "") -> str:
        """Normalize outcome name."""
        outcome = outcome.lower().strip()
        home_norm = normalize_team_name(home)
        away_norm = normalize_team_name(away)
        outcome_norm = normalize_team_name(outcome)
        
        if home_norm and outcome_norm == home_norm:
            return 'home'
        if away_norm and outcome_norm == away_norm:
            return 'away'
        
        if outcome in ['1', 'home', 'hemma', 'yes']:
            return 'home'
        if outcome in ['x', 'draw', 'oavgjort']:
            return 'draw'
        if outcome in ['2', 'away', 'borta', 'no']:
            return 'away'
        if 'over' in outcome:
            return 'over'
        if 'under' in outcome:
            return 'under'
        
        return outcome[:20]
    
    def _count_matched_events(self) -> int:
        """Count events with odds from multiple providers."""
        from sqlalchemy import func
        
        return self.session.query(Event).join(Odds).group_by(Event.id).having(
            func.count(func.distinct(Odds.provider_id)) > 1
        ).count()
    
    def get_matched_events(self, limit: int = 50) -> list[dict]:
        """Get events with odds from multiple providers."""
        from sqlalchemy import func
        
        matched = self.session.query(Event).join(Odds).group_by(Event.id).having(
            func.count(func.distinct(Odds.provider_id)) > 1
        ).limit(limit).all()
        
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


# ============ CLI ============

async def main():
    """Run extraction pipeline."""
    logging.basicConfig(level=logging.DEBUG, format='%(levelname)s - %(message)s')
    
    init_db()
    pipeline = ExtractionPipeline()
    
    print("=" * 70)
    print("UNIFIED EXTRACTION PIPELINE")
    print("=" * 70)
    
    # Run with limited providers for testing
    results = await pipeline.run(
        polymarket=True,
        kambi_providers=["unibet"],  # Start with one provider
        max_events_per_sport=500,    # INCREASED LIMIT to capture everything
    )
    
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    
    poly = results['polymarket']
    print(f"Polymarket: {poly['events_processed']} processed ({poly['events_new']} new)")
    print(f"            {poly['odds_processed']} odds ({poly['odds_new']} new)")
    
    for provider, data in results['kambi'].items():
        print(f"{provider}: {data.get('events_processed', 0)} processed ({data.get('events_new', 0)} new)")
        print(f"{' '*len(provider)}  {data.get('odds_processed', 0)} odds ({data.get('odds_new', 0)} new)")
        if 'error' in data:
            print(f"  ERROR: {data['error']}")
            
    print(f"\nTotal events: {results['total_events']}")
    print(f"Matched events: {results['matched_events']}")
    
    # Show some matched events
    if results['matched_events'] > 0:
        print("\n" + "=" * 70)
        print("SAMPLE MATCHED EVENTS")
        print("=" * 70)
        
        matched = pipeline.get_matched_events(limit=5)
        for event in matched:
            print(f"\n{event['home_team']} vs {event['away_team']}")
            print(f"  Sport: {event['sport']}")
            for provider, odds in event['providers'].items():
                print(f"  {provider}: {len(odds)} odds")


if __name__ == "__main__":
    asyncio.run(main())
