"""
Behavioral Feature Extraction

Extracts features from betting history that may indicate
patterns bookmakers use to identify sharp bettors.

All features are normalized to 0-1 range where:
- 0.0 = Looks like recreational bettor (safe)
- 1.0 = Highly suspicious pattern (risky)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import statistics
import logging

from sqlalchemy.orm import Session

from ..db.models import Bet, Event

logger = logging.getLogger(__name__)


@dataclass
class BehavioralFeatures:
    """Normalized behavioral features for a provider (0-1, higher = more suspicious)."""

    # Stake patterns
    stake_entropy: float = 0.0  # CV of stakes + round number ratio

    # Market diversity
    market_diversity: float = 0.0  # Sports/leagues spread (low diversity = suspicious)

    # Timing patterns
    timing_regularity: float = 0.0  # Hour/day concentration

    # Correlation detection
    outcome_correlation: float = 0.0  # Hedging behavior detection

    # Bonus exploitation
    bonus_usage_ratio: float = 0.0  # Bonus bet percentage

    # CLV metrics
    clv_score: float = 0.0  # Average positive CLV (beating closing line)

    # Win rate
    win_rate_deviation: float = 0.0  # Deviation from expected win rate

    # Metadata
    bets_analyzed: int = 0
    calculation_window_days: int = 30
    calculated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "stake_entropy": round(self.stake_entropy, 3),
            "market_diversity": round(self.market_diversity, 3),
            "timing_regularity": round(self.timing_regularity, 3),
            "outcome_correlation": round(self.outcome_correlation, 3),
            "bonus_usage_ratio": round(self.bonus_usage_ratio, 3),
            "clv_score": round(self.clv_score, 3),
            "win_rate_deviation": round(self.win_rate_deviation, 3),
            "bets_analyzed": self.bets_analyzed,
            "calculation_window_days": self.calculation_window_days,
            "calculated_at": self.calculated_at.isoformat(),
        }


class FeatureExtractor:
    """
    Extracts behavioral features from betting history.

    Features are designed to detect patterns that bookmakers use to
    identify professional bettors:

    1. stake_entropy: Low variance stakes or round numbers
    2. market_diversity: Betting on few sports/leagues
    3. timing_regularity: Betting at predictable times
    4. outcome_correlation: Hedging across providers
    5. bonus_usage_ratio: Heavy bonus exploitation
    6. clv_score: Consistently beating closing lines
    7. win_rate_deviation: Win rate above expected
    """

    def __init__(self, db: Session, window_days: int = 30):
        self.db = db
        self.window_days = window_days

    def extract_for_provider(self, provider_id: str) -> BehavioralFeatures:
        """
        Extract all behavioral features for a provider.

        Args:
            provider_id: The provider to analyze

        Returns:
            BehavioralFeatures with all metrics normalized 0-1
        """
        cutoff = datetime.utcnow() - timedelta(days=self.window_days)

        # Get bets within window
        bets = (
            self.db.query(Bet)
            .filter(Bet.provider_id == provider_id)
            .filter(Bet.placed_at >= cutoff)
            .all()
        )

        if len(bets) < 5:
            # Not enough data for meaningful analysis
            return BehavioralFeatures(
                bets_analyzed=len(bets),
                calculation_window_days=self.window_days,
            )

        features = BehavioralFeatures(
            stake_entropy=self._calculate_stake_entropy(bets),
            market_diversity=self._calculate_market_diversity(bets),
            timing_regularity=self._calculate_timing_regularity(bets),
            outcome_correlation=self._calculate_outcome_correlation(bets, provider_id),
            bonus_usage_ratio=self._calculate_bonus_usage(bets),
            clv_score=self._calculate_clv_score(bets),
            win_rate_deviation=self._calculate_win_rate_deviation(bets),
            bets_analyzed=len(bets),
            calculation_window_days=self.window_days,
        )

        return features

    def _calculate_stake_entropy(self, bets: list[Bet]) -> float:
        """
        Calculate stake entropy (low entropy = suspicious).

        Combines:
        - Coefficient of variation of stakes (low CV = suspicious)
        - Round number ratio (high ratio = suspicious)

        Returns 0-1 (higher = more suspicious)
        """
        stakes = [b.stake for b in bets if b.stake > 0]
        if len(stakes) < 3:
            return 0.0

        # CV component (inverted: low CV = high suspicion)
        mean_stake = statistics.mean(stakes)
        if mean_stake == 0:
            return 0.0

        std_stake = statistics.stdev(stakes) if len(stakes) > 1 else 0
        cv = std_stake / mean_stake

        # Natural betting has CV around 0.3-0.5
        # Flat stakes (CV < 0.1) are suspicious
        cv_score = max(0, 1 - cv * 2)  # CV of 0.5+ = score 0

        # Round number ratio
        round_numbers = {10, 20, 25, 50, 100, 200, 250, 500, 1000}
        round_count = sum(1 for s in stakes if s in round_numbers or s % 10 == 0)
        round_ratio = round_count / len(stakes)

        # Combine: 60% CV, 40% round numbers
        return min(1.0, cv_score * 0.6 + round_ratio * 0.4)

    def _calculate_market_diversity(self, bets: list[Bet]) -> float:
        """
        Calculate market diversity (low diversity = suspicious).

        Recreational bettors spread across many sports.
        Sharp bettors concentrate on efficient markets.

        Returns 0-1 (higher = more suspicious / less diverse)
        """
        # Get unique sports from events
        event_ids = [b.event_id for b in bets if b.event_id]
        if not event_ids:
            return 0.0

        events = self.db.query(Event).filter(Event.id.in_(event_ids)).all()
        sports = set(e.sport for e in events if e.sport)
        leagues = set(e.league for e in events if e.league)

        # Fewer sports/leagues = higher suspicion
        sport_count = len(sports)
        league_count = len(leagues)

        # Normalize: 1 sport = 1.0, 5+ sports = 0.0
        sport_score = max(0, 1 - (sport_count - 1) / 4)

        # Normalize: 1-2 leagues = 1.0, 10+ leagues = 0.0
        league_score = max(0, 1 - (league_count - 1) / 9)

        return (sport_score * 0.5 + league_score * 0.5)

    def _calculate_timing_regularity(self, bets: list[Bet]) -> float:
        """
        Calculate timing regularity (regular patterns = suspicious).

        Sharp bettors often bet at consistent times (e.g., when lines release).
        Recreational bettors have more varied timing.

        Returns 0-1 (higher = more suspicious)
        """
        # Extract hours and days
        hours = [b.hour_of_day for b in bets if b.hour_of_day is not None]
        days = [b.day_of_week for b in bets if b.day_of_week is not None]

        # Fall back to placed_at if behavioral columns not populated
        if not hours:
            hours = [b.placed_at.hour for b in bets if b.placed_at]
        if not days:
            days = [b.placed_at.weekday() for b in bets if b.placed_at]

        if len(hours) < 5:
            return 0.0

        # Hour concentration: high entropy = recreational, low = sharp
        hour_counts = [hours.count(h) for h in range(24)]
        hour_entropy = self._calculate_entropy(hour_counts)
        # Max entropy for 24 bins is ~4.58
        hour_score = max(0, 1 - hour_entropy / 3.0)

        # Day concentration
        day_counts = [days.count(d) for d in range(7)]
        day_entropy = self._calculate_entropy(day_counts)
        # Max entropy for 7 bins is ~2.81
        day_score = max(0, 1 - day_entropy / 2.0)

        return hour_score * 0.6 + day_score * 0.4

    def _calculate_entropy(self, counts: list[int]) -> float:
        """Calculate Shannon entropy of a distribution."""
        import math

        total = sum(counts)
        if total == 0:
            return 0.0

        entropy = 0.0
        for count in counts:
            if count > 0:
                p = count / total
                entropy -= p * math.log2(p)

        return entropy

    def _calculate_outcome_correlation(self, bets: list[Bet], provider_id: str) -> float:
        """
        Detect hedging behavior (multiple bets on same event).

        High correlation indicates hedging across providers, which
        bookmakers view as sharp behavior.

        Returns 0-1 (higher = more hedging detected)
        """
        # Get all bets in window (not just this provider)
        cutoff = datetime.utcnow() - timedelta(days=self.window_days)
        all_bets = (
            self.db.query(Bet)
            .filter(Bet.placed_at >= cutoff)
            .filter(Bet.event_id.isnot(None))
            .all()
        )

        # Group bets by event
        event_bets: dict[str, list[Bet]] = {}
        for b in all_bets:
            if b.event_id:
                event_bets.setdefault(b.event_id, []).append(b)

        # Find events where this provider + other providers bet
        hedge_count = 0
        total_events = 0

        for event_id, event_bet_list in event_bets.items():
            providers_in_event = set(b.provider_id for b in event_bet_list)

            if provider_id in providers_in_event:
                total_events += 1
                if len(providers_in_event) > 1:
                    hedge_count += 1

        if total_events == 0:
            return 0.0

        # Correlation ratio
        correlation = hedge_count / total_events

        # Some hedging is normal (arbitrage), but high ratio is suspicious
        # 0-20% hedging = low suspicion, >50% = high suspicion
        return min(1.0, correlation * 2)

    def _calculate_bonus_usage(self, bets: list[Bet]) -> float:
        """
        Calculate bonus usage ratio.

        High bonus usage indicates bonus exploitation (gubbing risk).

        Returns 0-1 (higher = more bonus usage)
        """
        bonus_bets = sum(1 for b in bets if b.is_bonus)
        total_bets = len(bets)

        if total_bets == 0:
            return 0.0

        ratio = bonus_bets / total_bets

        # 0-10% bonus = fine, >40% = very suspicious
        return min(1.0, ratio * 2.5)

    def _calculate_clv_score(self, bets: list[Bet]) -> float:
        """
        Calculate closing line value (CLV) score.

        Consistently beating closing lines is the #1 indicator of
        sharp betting behavior.

        Returns 0-1 (higher = consistently beating closing line)
        """
        clv_values = [b.clv_pct for b in bets if b.clv_pct is not None]

        if len(clv_values) < 5:
            return 0.0

        avg_clv = statistics.mean(clv_values)

        # CLV > 0 = beating the line, suspicious
        # Map: -5% CLV = 0.0, +5% CLV = 1.0
        score = (avg_clv + 5) / 10
        return max(0, min(1.0, score))

    def _calculate_win_rate_deviation(self, bets: list[Bet]) -> float:
        """
        Calculate deviation from expected win rate.

        Winning more than expected indicates edge exploitation.

        Returns 0-1 (higher = winning more than expected)
        """
        settled_bets = [b for b in bets if b.result in ("won", "lost")]

        if len(settled_bets) < 10:
            return 0.0

        # Calculate expected win rate from odds
        expected_wins = sum(1 / b.odds for b in settled_bets)
        expected_rate = expected_wins / len(settled_bets)

        # Actual win rate
        actual_wins = sum(1 for b in settled_bets if b.result == "won")
        actual_rate = actual_wins / len(settled_bets)

        # Deviation: positive = winning more than expected
        deviation = actual_rate - expected_rate

        # Map: -5% deviation = 0.0, +5% deviation = 1.0
        score = (deviation + 0.05) / 0.10
        return max(0, min(1.0, score))
