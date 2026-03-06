"""Bet service - bet recording and settlement with risk management."""

import logging
from datetime import datetime
from sqlalchemy.orm import Session

from ..repositories import ProfileRepo, BetRepo
from ..db.models import Provider, Bet, Event, ProviderRiskProfile, Odds, ProfileProviderBonus
from ..analysis.devig import get_fair_odds_for_outcome
from ..constants import SHARP_PROVIDERS
from ..config import get_exchange_rate, get_provider_currency

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
        point: float | None = None,
        is_bonus: bool = False,
        bonus_type: str | None = None,
        utility_score: float | None = None,
        selection_probability: float | None = None,
        stake_noise_applied: float | None = None,
        fair_odds_at_placement: float | None = None,
        boost_event: str | None = None,
        boost_title: str | None = None,
    ) -> dict:
        """Record a placed bet for active profile with risk tracking."""
        profile = self.profile_repo.get_active()

        # Verify provider exists
        provider = self.db.query(Provider).filter(Provider.id == provider_id).first()
        if not provider:
            return {"error": f"Provider {provider_id} not found"}

        # Block duplicate: same event + market + outcome + point already has a pending bet (any provider)
        if event_id and market and outcome:
            dup_query = self.db.query(Bet).filter(
                Bet.profile_id == profile.id,
                Bet.event_id == event_id,
                Bet.market == market,
                Bet.outcome == outcome,
                Bet.result == "pending",
            )
            if point is not None:
                dup_query = dup_query.filter(Bet.point == point)
            else:
                dup_query = dup_query.filter(Bet.point.is_(None))
            existing = dup_query.first()
            if existing:
                point_str = f" {point}" if point is not None else ""
                return {"error": f"Already have a pending bet on this market ({market} {outcome}{point_str}) at {existing.provider_id}"}

        # Check cooldown
        cooldown_reason = self._check_cooldown(provider_id)
        if cooldown_reason:
            return {"error": f"Bet blocked: {cooldown_reason}"}

        # Validate sufficient balance (unless free bet)
        # Stake is in native currency (USD for Polymarket, SEK for others)
        # Balance is also in native currency
        currency = get_provider_currency(provider_id)
        current_balance = self.profile_repo.get_balance(profile.id, provider_id)
        if not is_bonus and current_balance < stake:
            unit = "$" if currency != "SEK" else " kr"
            fmt = f"${current_balance:.2f}" if currency != "SEK" else f"{current_balance:.0f} kr"
            fmt_req = f"${stake:.2f}" if currency != "SEK" else f"{stake:.0f} kr"
            return {
                "error": f"Insufficient balance: {fmt} available, {fmt_req} required"
            }

        # Populate behavioral fields
        now = datetime.utcnow()
        risk_score = self._get_risk_score(provider_id)
        is_round = stake == round(stake) and stake % 5 == 0 and stake >= 10

        # Compute fair odds at placement from current Pinnacle odds (or use passed value for boosts)
        if fair_odds_at_placement is None and event_id and market and outcome:
            pin_rows = (
                self.db.query(Odds)
                .filter(
                    Odds.event_id == event_id,
                    Odds.provider_id == "pinnacle",
                    Odds.market == market,
                )
                .all()
            )
            pin_market = {row.outcome: row.odds for row in pin_rows}
            if len(pin_market) >= 2 and outcome in pin_market:
                fair = get_fair_odds_for_outcome(outcome, pin_market, method="multiplicative")
                if fair and fair > 1.0:
                    fair_odds_at_placement = round(fair, 4)

        # For Polymarket bets, save the event_slug from odds.provider_meta
        # so we can look up the Gamma event for settlement even after odds are cleaned up
        confirmation_id = None
        if provider_id == "polymarket" and event_id:
            odds_row = (
                self.db.query(Odds)
                .filter(Odds.event_id == event_id, Odds.provider_id == "polymarket")
                .first()
            )
            if odds_row and odds_row.provider_meta:
                import json as _json
                try:
                    meta = _json.loads(odds_row.provider_meta)
                    confirmation_id = meta.get("event_slug")
                except (ValueError, TypeError):
                    pass

        bet = self.bet_repo.create(
            profile_id=profile.id,
            event_id=event_id,
            provider_id=provider_id,
            market=market,
            outcome=outcome,
            odds=odds,
            point=point,
            stake=stake,
            currency=currency,
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
            fair_odds_at_placement=fair_odds_at_placement,
            boost_event=boost_event,
            boost_title=boost_title,
            confirmation_id=confirmation_id,
        )

        # Deduct stake from balance (unless free bet)
        # Both stake and balance are in provider's native currency — no conversion
        if not is_bonus:
            self.profile_repo.adjust_balance(profile.id, provider_id, -stake)

        # Auto-advance freebet: mark as completed when freebet is used
        if is_bonus:
            bonus = self.db.query(ProfileProviderBonus).filter(
                ProfileProviderBonus.profile_id == profile.id,
                ProfileProviderBonus.provider_id == provider_id,
                ProfileProviderBonus.bonus_status == "freebet_available",
            ).first()
            if bonus:
                bonus.bonus_status = "completed"
                bonus.updated_at = datetime.utcnow()
                logger.info(f"[BetService] Auto-completed freebet for {provider_id}")

        # Check current wagering status (but don't record — wagering counts on settlement)
        wagering_status = self.profile_repo.get_bonus_status(profile.id, provider_id)

        return {
            "success": True,
            "bet_id": bet.id,
            "profile_id": profile.id,
            "risk_score": risk_score,
            "bonus_wagering": wagering_status if wagering_status.get("status") in ("in_progress", "trigger_needed") else None,
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

        # Add payout to balance (payout is in bet's native currency — no conversion)
        if bet.profile_id and payout > 0:
            self.profile_repo.adjust_balance(bet.profile_id, bet.provider_id, payout)

        # Record wagering progress on settlement (not placement)
        wagering_status = None
        if bet.profile_id and result in ("won", "lost", "void"):
            wagering_status = self.profile_repo.record_wagering(
                bet.profile_id, bet.provider_id, bet.stake, bet.odds
            )

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
            "bonus_wagering": wagering_status if wagering_status and wagering_status.get("status") in ("in_progress", "trigger_needed") else None,
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

        # Don't overwrite if snapshot_closing_odds already captured better data
        if bet.closing_odds is not None:
            clv = (bet.odds / bet.closing_odds - 1) * 100
            return round(clv, 2)

        # Look up current Pinnacle odds for same event/market/outcome
        query = self.db.query(Odds).filter(
            Odds.event_id == bet.event_id,
            Odds.provider_id.in_(SHARP_PROVIDERS),
            Odds.market == bet.market,
            Odds.outcome == bet.outcome,
        )
        # For spread/total, match the point to avoid comparing wrong lines
        if bet.market in ("spread", "total") and bet.point is not None:
            query = query.filter(Odds.point == bet.point)

        pinnacle_odds = query.first()

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

            query = self.db.query(Odds).filter(
                Odds.event_id == bet.event_id,
                Odds.provider_id.in_(SHARP_PROVIDERS),
                Odds.market == bet.market,
                Odds.outcome == bet.outcome,
            )
            # For spread/total, match the point to avoid comparing wrong lines
            if bet.market in ("spread", "total") and bet.point is not None:
                query = query.filter(Odds.point == bet.point)

            pinnacle_odds = query.first()

            if not pinnacle_odds or pinnacle_odds.odds <= 1.0:
                continue

            bet.closing_odds = pinnacle_odds.odds
            bet.clv_pct = round((bet.odds / pinnacle_odds.odds - 1) * 100, 2)
            updated += 1

        if updated > 0:
            logger.info(f"[BetService] Snapshot closing odds: {updated}/{processed} bets updated")

        return {"processed": processed, "updated": updated}

    def edit_bet(
        self,
        bet_id: int,
        stake: float | None = None,
        odds: float | None = None,
        result: str | None = None,
    ) -> dict:
        """Edit a settled bet to correct stake/odds/result.

        Recalculates payout and adjusts provider balance accordingly.
        Used when auto-stake was wrong and user needs to correct it post-settlement.
        """
        bet = self.bet_repo.get_by_id(bet_id)
        if not bet:
            return {"error": f"Bet {bet_id} not found"}

        old_stake = bet.stake
        old_payout = bet.payout
        old_result = bet.result

        # Apply changes
        if stake is not None:
            bet.stake = stake
        if odds is not None:
            bet.odds = odds
        if result is not None:
            bet.result = result

        # Recalculate payout based on (possibly new) result and stake/odds
        if bet.result == "won":
            bet.payout = bet.stake * bet.odds
        elif bet.result == "void":
            bet.payout = bet.stake
        elif bet.result == "lost":
            bet.payout = 0.0

        # Adjust balance: reverse old payout+stake, apply new payout+stake
        # All amounts are in bet's native currency — no conversion needed
        if bet.profile_id:
            # net correction = (new_payout - old_payout) - (new_stake - old_stake)
            balance_delta = (bet.payout - old_payout) - (bet.stake - old_stake)
            if balance_delta != 0:
                self.profile_repo.adjust_balance(bet.profile_id, bet.provider_id, balance_delta)

        # Recalculate CLV if closing odds exist
        if bet.closing_odds and bet.closing_odds > 1.0:
            bet.clv_pct = round((bet.odds / bet.closing_odds - 1) * 100, 2)

        self.db.commit()

        logger.info(
            f"[BetService] Edited bet #{bet_id}: "
            f"stake {old_stake}->{bet.stake}, result {old_result}->{bet.result}, "
            f"payout {old_payout}->{bet.payout}"
        )

        return {
            "success": True,
            "bet_id": bet_id,
            "stake": bet.stake,
            "odds": bet.odds,
            "result": bet.result,
            "payout": bet.payout,
            "profit": bet.profit,
            "balance_adjustment": (bet.payout - old_payout) - (bet.stake - old_stake),
        }
