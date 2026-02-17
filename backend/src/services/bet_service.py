"""Bet service - bet recording and settlement with risk management."""

import logging
from datetime import datetime
from sqlalchemy.orm import Session

from ..repositories import ProfileRepo, BetRepo
from ..db.models import Provider, Bet, Event, ProviderRiskProfile, Odds, ProfileProviderBonus
from ..constants import SHARP_PROVIDERS

logger = logging.getLogger(__name__)


class BetService:
    """Business logic for bet recording, settlement, and balance adjustments."""

    def __init__(self, db: Session):
        self.db = db
        self.profile_repo = ProfileRepo(db)
        self.bet_repo = BetRepo(db)

    def _check_cooldown(self, provider_id: str) -> str | None:
        """Check if provider is on cooldown. Returns reason string or None."""
        risk_profile = self.db.query(ProviderRiskProfile).filter(
            ProviderRiskProfile.provider_id == provider_id
        ).first()
        if not risk_profile or not risk_profile.is_on_cooldown:
            return None
        if risk_profile.cooldown_until and risk_profile.cooldown_until < datetime.utcnow():
            # Cooldown expired — clear it
            risk_profile.is_on_cooldown = False
            risk_profile.cooldown_until = None
            risk_profile.cooldown_reason = None
            return None
        reason = risk_profile.cooldown_reason or "Provider is on cooldown"
        until = risk_profile.cooldown_until.isoformat() if risk_profile.cooldown_until else "unknown"
        return f"{reason} (until {until})"

    def _get_risk_score(self, provider_id: str) -> float:
        """Get current risk score for provider, or 0.0 if none."""
        risk_profile = self.db.query(ProviderRiskProfile).filter(
            ProviderRiskProfile.provider_id == provider_id
        ).first()
        return risk_profile.risk_score if risk_profile else 0.0

    def create_bet(
        self,
        event_id: str | None,
        provider_id: str,
        market: str | None,
        outcome: str | None,
        odds: float,
        stake: float,
        is_bonus: bool = False,
        bonus_type: str | None = None,
        utility_score: float | None = None,
        selection_probability: float | None = None,
        stake_noise_applied: float | None = None,
    ) -> dict:
        """Record a placed bet for active profile with risk tracking."""
        profile = self.profile_repo.get_active()

        # Verify provider exists
        provider = self.db.query(Provider).filter(Provider.id == provider_id).first()
        if not provider:
            return {"error": f"Provider {provider_id} not found"}

        # Check cooldown
        cooldown_reason = self._check_cooldown(provider_id)
        if cooldown_reason:
            return {"error": f"Bet blocked: {cooldown_reason}"}

        # Validate sufficient balance (unless free bet)
        current_balance = self.profile_repo.get_balance(profile.id, provider_id)
        if not is_bonus and current_balance < stake:
            return {
                "error": f"Insufficient balance: {current_balance:.2f} available, {stake:.2f} required"
            }

        # Populate behavioral fields
        now = datetime.utcnow()
        risk_score = self._get_risk_score(provider_id)
        is_round = stake == round(stake) and stake % 5 == 0 and stake >= 10

        bet = self.bet_repo.create(
            profile_id=profile.id,
            event_id=event_id,
            provider_id=provider_id,
            market=market,
            outcome=outcome,
            odds=odds,
            stake=stake,
            is_bonus=is_bonus,
            bonus_type=bonus_type,
            # Behavioral tracking
            hour_of_day=now.hour,
            day_of_week=now.weekday(),
            stake_rounded=is_round,
            stake_noise_applied=stake_noise_applied,
            risk_score_at_bet=risk_score,
            utility_score=utility_score,
            selection_probability=selection_probability,
        )

        # Deduct stake from balance (unless free bet)
        if not is_bonus:
            self.profile_repo.adjust_balance(profile.id, provider_id, -stake)

        # Record wagering progress
        wagering_status = self.profile_repo.record_wagering(profile.id, provider_id, stake, odds)

        return {
            "success": True,
            "bet_id": bet.id,
            "profile_id": profile.id,
            "risk_score": risk_score,
            "bonus_wagering": wagering_status if wagering_status.get("status") == "in_progress" else None,
        }

    def settle_bet(self, bet_id: int, result: str, payout: float) -> dict:
        """Settle a bet with result and CLV tracking."""
        bet = self.bet_repo.get_by_id(bet_id)
        if not bet:
            return {"error": f"Bet {bet_id} not found"}

        bet.result = result
        bet.payout = payout
        bet.settled_at = datetime.utcnow()

        # Calculate CLV (Closing Line Value)
        clv_pct = self._calculate_clv(bet)
        if clv_pct is not None:
            bet.clv_pct = clv_pct

        # Add payout to balance
        if bet.profile_id and payout > 0:
            self.profile_repo.adjust_balance(bet.profile_id, bet.provider_id, payout)

        # Auto-advance freebet: if trigger bet settled, unlock the freebet
        if bet.profile_id:
            bonus = self.db.query(ProfileProviderBonus).filter(
                ProfileProviderBonus.profile_id == bet.profile_id,
                ProfileProviderBonus.provider_id == bet.provider_id,
                ProfileProviderBonus.bonus_status == "trigger_needed",
            ).first()
            if (bonus and bet.odds >= (bonus.min_odds or 1.80)
                    and bet.stake >= (bonus.bonus_amount or 0)):
                bonus.bonus_status = "freebet_available"
                bonus.wagered_amount = bet.stake
                bonus.updated_at = datetime.utcnow()

        return {
            "success": True,
            "profit": bet.profit,
            "profile_id": bet.profile_id,
            "clv_pct": clv_pct,
        }

    def _calculate_clv(self, bet: Bet) -> float | None:
        """
        Calculate Closing Line Value for a settled bet.

        CLV = (bet_odds / current_pinnacle_odds - 1) * 100

        Positive CLV means the bet was placed at better odds than the
        closing line — the #1 indicator of sharp betting skill.
        """
        if not bet.event_id or not bet.outcome or not bet.market:
            return None

        # Look up current Pinnacle odds for same event/market/outcome
        pinnacle_odds = self.db.query(Odds).filter(
            Odds.event_id == bet.event_id,
            Odds.provider_id.in_(SHARP_PROVIDERS),
            Odds.market == bet.market,
            Odds.outcome == bet.outcome,
        ).first()

        if not pinnacle_odds or pinnacle_odds.odds <= 1.0:
            return None

        # Store closing odds for reference
        bet.closing_odds = pinnacle_odds.odds

        # CLV% = (bet_odds / closing_odds - 1) * 100
        clv = (bet.odds / pinnacle_odds.odds - 1) * 100
        return round(clv, 2)

    def snapshot_closing_odds(self) -> dict:
        """
        For all pending bets on events that have already started (start_time <= now),
        snapshot the current Pinnacle odds as closing_odds and compute CLV.

        This should be called periodically (e.g., during extraction cleanup) to
        capture CLV before the odds/events are cleaned up from the database.

        Returns: {"processed": int, "updated": int}
        """
        now = datetime.utcnow()

        # Find pending bets where closing_odds is not yet set,
        # joined with events that have already started
        pending_bets = (
            self.db.query(Bet)
            .join(Event, Event.id == Bet.event_id)
            .filter(
                Bet.result == "pending",
                Bet.closing_odds.is_(None),
                Bet.event_id.isnot(None),
                Event.start_time.isnot(None),
                Event.start_time <= now,
            )
            .all()
        )

        processed = 0
        updated = 0

        for bet in pending_bets:
            processed += 1
            if not bet.outcome or not bet.market:
                continue

            pinnacle_odds = self.db.query(Odds).filter(
                Odds.event_id == bet.event_id,
                Odds.provider_id.in_(SHARP_PROVIDERS),
                Odds.market == bet.market,
                Odds.outcome == bet.outcome,
            ).first()

            if not pinnacle_odds or pinnacle_odds.odds <= 1.0:
                continue

            bet.closing_odds = pinnacle_odds.odds
            bet.clv_pct = round((bet.odds / pinnacle_odds.odds - 1) * 100, 2)
            updated += 1

        if updated > 0:
            logger.info(f"[BetService] Snapshot closing odds: {updated}/{processed} bets updated")

        return {"processed": processed, "updated": updated}
