"""
Mug Bet Scanner

Finds opportunities for "mug bets" - intentionally negative-edge bets
on favorites to appear recreational to bookmakers.

Criteria for good mug bets:
- Negative edge: -1% to -10% vs fair odds (losing bets on purpose)
- Favorites: Implied probability > 60% (odds < 1.67)
- Popular leagues: Premier League, NBA, etc. (what casual bettors bet on)

Why negative edge? Bookmakers flag accounts that ONLY take +EV.
Mixing in -EV bets on favorites makes you look recreational.
"""

from dataclasses import dataclass
from typing import Optional
import logging

from sqlalchemy.orm import Session

from ..db.models import Event, Odds
from .scanner import OpportunityScanner
from ..constants import SHARP_PROVIDERS, EXCLUDED_FROM_SCANS

logger = logging.getLogger(__name__)

# Popular leagues that casual bettors bet on
POPULAR_LEAGUES = frozenset({
    # Football (Soccer)
    "premier_league", "premier league", "english premier league",
    "la_liga", "la liga", "spanish la liga",
    "serie_a", "serie a", "italian serie a",
    "bundesliga", "german bundesliga",
    "ligue_1", "ligue 1", "french ligue 1",
    "champions_league", "champions league", "uefa champions league",
    "europa_league", "europa league", "uefa europa league",
    "world_cup", "world cup", "fifa world cup",
    "euro", "european championship",
    # US Sports
    "nba", "national basketball association",
    "nfl", "national football league",
    "mlb", "major league baseball",
    "nhl", "national hockey league",
    "mls", "major league soccer",
    # Tennis
    "atp", "wta", "grand slam",
    "australian open", "french open", "wimbledon", "us open",
})


@dataclass
class MugBetOpportunity:
    """An opportunity for a mug bet (intentional -EV for account health)."""

    event_id: str
    market: str
    outcome: str

    # Bet details
    provider: str
    provider_odds: float

    # Fair odds from Pinnacle (de-vigged)
    fair_odds: float

    # The edge (should be negative for mug bets)
    edge_pct: float

    # Implied probability from provider odds (higher = heavier favorite)
    implied_prob: float

    # Recreational score (higher = better camouflage)
    # Based on: favorite-ness, league popularity, moderate negative edge
    recreational_score: float

    # Event context
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    sport: Optional[str] = None
    league: Optional[str] = None


class MugBetScanner:
    """
    Scans for mug bet opportunities (negative-edge favorites).

    Reuses OpportunityScanner.scan_bonus() which returns ALL edges including negative,
    then filters for mug bet criteria and scores for "recreational look".

    Usage:
        scanner = MugBetScanner(session)
        opps = scanner.scan_mug_bets("unibet")
        # Returns sorted by recreational_score (best camouflage first)
    """

    def __init__(self, session: Session):
        self.session = session
        self._opp_scanner = OpportunityScanner(session)

    def scan_mug_bets(
        self,
        provider_id: str,
        max_edge_pct: float = -1.0,    # Only negative edge (max = closest to 0)
        min_edge_pct: float = -10.0,   # Not too negative (min = most negative)
        min_implied_prob: float = 0.60, # Favorites only (60%+ implied prob)
        limit: int = 50,
    ) -> list[MugBetOpportunity]:
        """
        Find mug bet opportunities for a provider.

        Args:
            provider_id: Provider to find mug bets at
            max_edge_pct: Maximum edge (should be negative, -1.0 = at most 1% negative)
            min_edge_pct: Minimum edge (should be more negative, -10.0 = at least 10% negative)
            min_implied_prob: Minimum implied probability (0.60 = 60% = favorites)
            limit: Maximum opportunities to return

        Returns:
            List of MugBetOpportunity sorted by recreational_score (best first)
        """
        # Get ALL opportunities from bonus scanner (no edge threshold)
        # This returns positive AND negative edges
        bonus_opps = self._opp_scanner.scan_bonus(
            anchor_provider=provider_id,
            counterpart_providers=["pinnacle"],
            devig=True,
        )

        mug_opps = []

        for opp in bonus_opps:
            # Filter: Only negative edge (within range)
            if opp.edge_pct > max_edge_pct:
                continue  # Too close to neutral or positive
            if opp.edge_pct < min_edge_pct:
                continue  # Too negative (wasting money)

            # Calculate implied probability from provider odds
            implied_prob = 1.0 / opp.anchor_odds

            # Filter: Only favorites
            if implied_prob < min_implied_prob:
                continue  # Not a favorite

            # Calculate recreational score (higher = better camouflage)
            rec_score = self._calculate_recreational_score(
                edge_pct=opp.edge_pct,
                implied_prob=implied_prob,
                league=opp.league,
                sport=opp.sport,
            )

            mug_opps.append(MugBetOpportunity(
                event_id=opp.event_id,
                market=opp.market,
                outcome=opp.outcome,
                provider=opp.anchor_provider,
                provider_odds=opp.anchor_odds,
                fair_odds=opp.fair_odds,
                edge_pct=opp.edge_pct,
                implied_prob=round(implied_prob, 3),
                recreational_score=round(rec_score, 2),
                home_team=opp.home_team,
                away_team=opp.away_team,
                sport=opp.sport,
                league=opp.league,
            ))

        # Sort by recreational score (highest = best camouflage)
        mug_opps.sort(key=lambda x: x.recreational_score, reverse=True)

        logger.info(
            f"[MugScanner] Found {len(mug_opps)} mug bet opportunities for {provider_id} "
            f"(edge: {min_edge_pct}% to {max_edge_pct}%, implied_prob >= {min_implied_prob*100:.0f}%)"
        )

        return mug_opps[:limit]

    def _calculate_recreational_score(
        self,
        edge_pct: float,
        implied_prob: float,
        league: Optional[str],
        sport: Optional[str],
    ) -> float:
        """
        Calculate how "recreational" a bet looks.

        Higher score = better camouflage for value betting activity.

        Factors:
        1. Favorite-ness (heavier favorites look more recreational)
        2. League popularity (popular leagues = what casuals bet on)
        3. Moderate negative edge (not too negative = believable loss)
        """
        score = 0.0

        # Factor 1: Favorite-ness (0-40 points)
        # implied_prob of 0.60 = 0 points, 0.80+ = 40 points
        favorite_score = min(40, max(0, (implied_prob - 0.60) * 200))
        score += favorite_score

        # Factor 2: League popularity (0-30 points)
        league_score = 0
        if league:
            league_lower = league.lower()
            if any(pop in league_lower for pop in POPULAR_LEAGUES):
                league_score = 30
            elif sport and sport.lower() in ["football", "soccer", "basketball", "tennis"]:
                league_score = 15  # Popular sport, less popular league
        score += league_score

        # Factor 3: Moderate negative edge (0-30 points)
        # Best edge is around -3% to -5% (believable loss, not wasteful)
        # Too close to 0: suspicious, too negative: wasteful
        if -5.0 <= edge_pct <= -2.0:
            edge_score = 30  # Sweet spot
        elif -7.0 <= edge_pct < -2.0 or -2.0 < edge_pct <= -1.0:
            edge_score = 20  # Acceptable
        else:
            edge_score = 10  # Either too close to breakeven or too negative

        score += edge_score

        return score


# Quick test
if __name__ == "__main__":
    from ..db.models import get_session

    session = get_session()
    scanner = MugBetScanner(session)

    # Test with a hypothetical provider
    opps = scanner.scan_mug_bets("unibet")
    print(f"Found {len(opps)} mug bet opportunities")

    for opp in opps[:5]:
        print(f"  {opp.home_team} vs {opp.away_team}")
        print(f"    {opp.outcome}@{opp.provider_odds:.2f} (edge: {opp.edge_pct:+.1f}%)")
        print(f"    implied_prob: {opp.implied_prob:.0%}, rec_score: {opp.recreational_score}")

    session.close()
