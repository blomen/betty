"""
Mug Bet Automator

Provides mug bet placement capability for account health when needed manually.
Value betting already has natural losses (~45% of bets) providing cover,
so automatic mug bets are disabled - they waste edge. Use manually if an
account seems at risk.

Usage:
    automator = MugBetAutomator(session, profile_id)

    # Check account health stats (informational only)
    status = automator.get_all_provider_status()

    # Manually place mug bets when you decide it's needed
    results = automator.auto_place("unibet", count=3)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import logging
import random

from sqlalchemy.orm import Session
from sqlalchemy import func

from ..db.models import (
    Bet, Provider, Profile, ProviderRiskProfile, RiskConfig,
)
from .mug_scanner import MugBetScanner, MugBetOpportunity

logger = logging.getLogger(__name__)


@dataclass
class MugBetRequirement:
    """Assessment of whether a provider needs mug bets."""

    provider_id: str
    needs_mug_bets: bool
    reason: Optional[str]  # "warmup", "ratio_balance", "ongoing", None

    # Details
    count_needed: int
    account_age_days: Optional[int]
    total_bets: int
    ev_bets: int
    mug_bets: int
    ev_quality_ratio: float  # % of bets that are +EV (high = suspicious)

    # Message for UI
    message: str


@dataclass
class PlacedMugBet:
    """A mug bet that was (or would be) placed."""

    provider_id: str
    event_id: str
    outcome: str
    odds: float
    stake: float
    edge_pct: float
    reason: str  # "warmup", "ratio_balance", "ongoing"

    # For display
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    sport: Optional[str] = None

    # Result (only set if actually placed)
    bet_id: Optional[int] = None
    placed: bool = False


class MugBetAutomator:
    """
    Manual mug bet placement for account health when desired.

    Value betting already has natural losses (~45%) providing cover,
    so automatic mug bets are disabled. Use manually if an account seems at risk.

    Usage:
        automator = MugBetAutomator(session, profile_id)

        # Check account health stats (informational only)
        status = automator.get_all_provider_status()

        # Manually place mug bets when you decide it's needed
        results = automator.auto_place("unibet", count=3)
    """

    def __init__(self, session: Session, profile_id: int = 1):
        self.session = session
        self.profile_id = profile_id
        self._scanner = MugBetScanner(session)

    def _get_risk_config(self) -> RiskConfig:
        """Get risk config for current profile, creating default if needed."""
        config = (
            self.session.query(RiskConfig)
            .filter(RiskConfig.profile_id == self.profile_id)
            .first()
        )
        if not config:
            config = RiskConfig(profile_id=self.profile_id)
            self.session.add(config)
            self.session.commit()
        return config

    def _get_profile(self) -> Profile:
        """Get current profile."""
        return self.session.query(Profile).filter(Profile.id == self.profile_id).first()

    def assess_provider(self, provider_id: str) -> MugBetRequirement:
        """
        Get account health stats for a provider.

        Returns informational stats only - mug bets are manual.
        Value betting already has natural losses (~45%) providing cover.
        """
        # Get provider's risk profile
        risk_profile = (
            self.session.query(ProviderRiskProfile)
            .filter(ProviderRiskProfile.provider_id == provider_id)
            .first()
        )

        # Count bets at this provider
        total_bets = (
            self.session.query(func.count(Bet.id))
            .filter(Bet.provider_id == provider_id)
            .scalar() or 0
        )

        ev_bets = (
            self.session.query(func.count(Bet.id))
            .filter(Bet.provider_id == provider_id)
            .filter(Bet.is_mug_bet == False)
            .filter(Bet.ev_at_placement != None)
            .filter(Bet.ev_at_placement > 0)
            .scalar() or 0
        )

        mug_bets = (
            self.session.query(func.count(Bet.id))
            .filter(Bet.provider_id == provider_id)
            .filter(Bet.is_mug_bet == True)
            .scalar() or 0
        )

        # Calculate account age
        account_age_days = None
        if risk_profile and risk_profile.first_bet_date:
            age = datetime.utcnow() - risk_profile.first_bet_date
            account_age_days = age.days

        # Calculate EV quality ratio (% of bets that are +EV)
        ev_quality_ratio = ev_bets / total_bets if total_bets > 0 else 0.0

        # No automatic triggers - mug bets are manual only
        return MugBetRequirement(
            provider_id=provider_id,
            needs_mug_bets=False,  # Always false - manual only
            reason=None,
            count_needed=0,
            account_age_days=account_age_days,
            total_bets=total_bets,
            ev_bets=ev_bets,
            mug_bets=mug_bets,
            ev_quality_ratio=round(ev_quality_ratio, 3),
            message="Mug bets are manual only. Use mug-place if needed.",
        )

    def get_all_provider_status(self) -> list[MugBetRequirement]:
        """Get mug bet status for all providers with balances."""
        providers = self.session.query(Provider).filter(Provider.balance > 0).all()

        statuses = []
        for provider in providers:
            status = self.assess_provider(provider.id)
            statuses.append(status)

        # Sort: providers needing mug bets first
        statuses.sort(key=lambda x: (not x.needs_mug_bets, -x.count_needed))

        return statuses

    def auto_place(
        self,
        provider_id: str,
        count: Optional[int] = None,
        dry_run: bool = False,
    ) -> list[PlacedMugBet]:
        """
        Auto-place mug bets for a provider.

        Args:
            provider_id: Provider to place mug bets at
            count: Number of mug bets to place (None = auto-detect)
            dry_run: If True, don't actually place bets

        Returns:
            List of placed (or would-be-placed) mug bets
        """
        config = self._get_risk_config()
        profile = self._get_profile()

        # Determine how many to place
        if count is None:
            requirement = self.assess_provider(provider_id)
            if not requirement.needs_mug_bets:
                logger.info(f"[MugAutomator] No mug bets needed for {provider_id}")
                return []
            count = requirement.count_needed
            reason = requirement.reason
        else:
            reason = "manual"

        # Get provider balance
        provider = self.session.query(Provider).filter(Provider.id == provider_id).first()
        if not provider or provider.balance <= 0:
            logger.warning(f"[MugAutomator] Provider {provider_id} has no balance")
            return []

        # Scan for mug bet opportunities
        opportunities = self._scanner.scan_mug_bets(
            provider_id=provider_id,
            max_edge_pct=config.mug_bet_max_edge_pct,
            min_edge_pct=config.mug_bet_min_edge_pct,
            min_implied_prob=config.mug_bet_min_implied_prob,
            limit=count * 2,  # Get extras in case some fail
        )

        if not opportunities:
            logger.warning(f"[MugAutomator] No mug bet opportunities found for {provider_id}")
            return []

        # Calculate base stake
        bankroll = profile.bankroll if profile else 1000.0
        base_stake = bankroll * (config.mug_bet_stake_pct / 100.0)

        results = []

        for opp in opportunities[:count]:
            # Add noise to stake (5-10% random variation)
            noise = random.uniform(0.95, 1.10)
            stake = round(base_stake * noise, 2)

            # Ensure stake doesn't exceed provider balance
            if stake > provider.balance:
                stake = round(provider.balance * 0.9, 2)

            if stake < 1.0:
                logger.warning(f"[MugAutomator] Stake too low for {provider_id}, skipping")
                continue

            placed_bet = PlacedMugBet(
                provider_id=provider_id,
                event_id=opp.event_id,
                outcome=opp.outcome,
                odds=opp.provider_odds,
                stake=stake,
                edge_pct=opp.edge_pct,
                reason=reason,
                home_team=opp.home_team,
                away_team=opp.away_team,
                sport=opp.sport,
            )

            if not dry_run:
                # Create actual bet record
                bet = Bet(
                    event_id=opp.event_id,
                    provider_id=provider_id,
                    market=opp.market,
                    outcome=opp.outcome,
                    odds=opp.provider_odds,
                    stake=stake,
                    is_mug_bet=True,
                    mug_bet_reason=reason,
                    ev_at_placement=opp.edge_pct,
                    hour_of_day=datetime.utcnow().hour,
                    day_of_week=datetime.utcnow().weekday(),
                )
                self.session.add(bet)

                # Update provider balance
                provider.balance -= stake

                self.session.commit()

                placed_bet.bet_id = bet.id
                placed_bet.placed = True

                logger.info(
                    f"[MugAutomator] Placed mug bet #{bet.id} at {provider_id}: "
                    f"{opp.outcome}@{opp.provider_odds:.2f} for {stake:.2f} (edge: {opp.edge_pct:+.1f}%)"
                )
            else:
                logger.info(
                    f"[MugAutomator] [DRY RUN] Would place mug bet at {provider_id}: "
                    f"{opp.outcome}@{opp.provider_odds:.2f} for {stake:.2f} (edge: {opp.edge_pct:+.1f}%)"
                )

            results.append(placed_bet)

        return results

    def auto_place_all(self, dry_run: bool = False) -> dict[str, list[PlacedMugBet]]:
        """
        Auto-place mug bets for ALL providers that need them.

        Args:
            dry_run: If True, don't actually place bets

        Returns:
            Dict mapping provider_id -> list of placed bets
        """
        statuses = self.get_all_provider_status()

        results = {}
        for status in statuses:
            if status.needs_mug_bets:
                placed = self.auto_place(
                    provider_id=status.provider_id,
                    count=status.count_needed,
                    dry_run=dry_run,
                )
                if placed:
                    results[status.provider_id] = placed

        total_placed = sum(len(bets) for bets in results.values())
        logger.info(
            f"[MugAutomator] Auto-placed {total_placed} mug bets across {len(results)} providers"
            + (" (DRY RUN)" if dry_run else "")
        )

        return results

    def get_mug_bet_history(
        self,
        provider_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[Bet]:
        """Get history of placed mug bets."""
        query = (
            self.session.query(Bet)
            .filter(Bet.is_mug_bet == True)
            .order_by(Bet.placed_at.desc())
        )

        if provider_id:
            query = query.filter(Bet.provider_id == provider_id)

        return query.limit(limit).all()


# Quick test
if __name__ == "__main__":
    from ..db.models import get_session

    session = get_session()
    automator = MugBetAutomator(session)

    # Check all provider status
    statuses = automator.get_all_provider_status()
    print("Provider Mug Bet Status:")
    for status in statuses:
        print(f"  {status.provider_id}: {status.message}")

    session.close()
