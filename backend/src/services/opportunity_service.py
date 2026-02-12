"""Opportunity service - value bet listing, hedging, and bonus scanning."""

import logging
from sqlalchemy.orm import Session

from ..repositories import ProfileRepo, OpportunityRepo, OddsRepo
from ..analysis import find_best_hedge
from ..analysis.scanner import OpportunityScanner
from ..bankroll.stake_calculator import StakeCalculator, calculate_stake, BONUS_MIN_ODDS
from ..db.models import Provider

logger = logging.getLogger(__name__)


class OpportunityService:
    """Business logic for opportunities: listing, stake calculation, hedging."""

    def __init__(self, db: Session):
        self.db = db
        self.profile_repo = ProfileRepo(db)
        self.opp_repo = OpportunityRepo(db)
        self.odds_repo = OddsRepo(db)

    def list_opportunities(
        self,
        type: str | None = None,
        provider1: str | None = None,
        provider2: str | None = None,
        providers: str | None = None,
        market: str | None = None,
        sport: str | None = None,
        min_value: float | None = None,
    ) -> dict:
        """List active opportunities with stake recommendations for value bets."""
        provider_ids = (
            [p.strip() for p in providers.split(',')] if providers else None
        )

        rows = self.opp_repo.find_active(
            type=type,
            provider1=provider1,
            provider2=provider2,
            provider_ids=provider_ids,
            market=market,
            sport=sport,
            min_edge=min_value,
            exclude_provider1=None if provider1 else "polymarket",
        )

        # Initialize stake calculator for value bets using profile risk settings
        stake_calculator = None
        profile = None
        if type == 'value' and rows:
            try:
                profile = self.profile_repo.get_active()
                bankroll = self.profile_repo.get_total_bankroll(profile.id)
                stake_calculator = StakeCalculator(
                    bankroll=bankroll,
                    max_kelly=profile.kelly_fraction,
                    single_bet_cap_pct=profile.max_stake_pct / 100.0,
                    min_edge=profile.min_edge_pct / 100.0,
                )
            except Exception as e:
                logger.warning(f"Could not initialize stake calculator: {e}")

        # Build response
        results = []
        for opp, event in rows:
            result = {
                "id": opp.id,
                "type": opp.type,
                "event_id": opp.event_id,
                "market": opp.market,
                "point": opp.point,
                "provider1": opp.provider1_id,
                "provider2": opp.provider2_id,
                "odds1": opp.odds1,
                "odds2": opp.odds2,
                "outcome1": opp.outcome1,
                "outcome2": opp.outcome2,
                "profit_pct": opp.profit_pct,
                "edge_pct": opp.edge_pct,
                "fair_odds": opp.odds2,
                "detected_at": opp.detected_at.isoformat() if opp.detected_at else None,
                "sport": event.sport if event else None,
                "league": event.league if event else None,
                "home_team": event.home_team if event else None,
                "away_team": event.away_team if event else None,
                "starts_at": event.start_time.isoformat() if event and event.start_time else None,
            }

            # Add stake recommendations for value bets
            if type == 'value' and stake_calculator and profile and opp.odds1 and opp.odds2:
                self._add_stake_recommendation(result, opp, profile, stake_calculator)

            results.append(result)

        return {"opportunities": results, "count": len(results)}

    def find_hedge(
        self,
        event_id: str,
        market: str,
        anchor_provider: str,
        anchor_outcome: str,
        anchor_odds: float,
        anchor_stake: float,
        counterpart_providers: list[str] | None = None,
        is_free_bet: bool = False,
    ) -> dict | None:
        """Find the best hedge for a bonus bet."""
        opposing_odds = self.odds_repo.get_for_event_filtered(
            event_id=event_id,
            market=market,
            exclude_outcome=anchor_outcome,
            exclude_provider=anchor_provider,
            provider_ids=counterpart_providers,
        )

        if not opposing_odds:
            return None

        opposing_list = [
            {"provider": o.provider_id, "outcome": o.outcome, "odds": o.odds}
            for o in opposing_odds
        ]

        result = find_best_hedge(
            event_id=event_id,
            market=market,
            anchor_provider=anchor_provider,
            anchor_outcome=anchor_outcome,
            anchor_odds=anchor_odds,
            anchor_stake=anchor_stake,
            opposing_odds_list=opposing_list,
            is_free_bet=is_free_bet,
        )

        if not result:
            return None

        return {
            "event_id": result.event_id,
            "market": result.market,
            "anchor_provider": result.anchor_provider,
            "anchor_outcome": result.anchor_outcome,
            "anchor_odds": result.anchor_odds,
            "anchor_stake": result.anchor_stake,
            "hedge_provider": result.hedge_provider,
            "hedge_outcome": result.hedge_outcome,
            "hedge_odds": result.hedge_odds,
            "hedge_stake": result.hedge_stake,
            "qualifying_loss": result.qualifying_loss,
            "retention_pct": result.retention_pct,
        }

    def scan_bonus(
        self,
        anchor_provider: str,
        limit: int = 10,
        include_negative: bool = True,
    ) -> dict:
        """Scan for bonus opportunities at anchor provider vs Pinnacle."""
        scanner = OpportunityScanner(self.db)
        opportunities = scanner.scan_bonus(anchor_provider=anchor_provider, devig=True)

        if not include_negative:
            opportunities = [o for o in opportunities if o.edge_pct > 0]

        # Get bankroll and profile settings for Kelly calculation
        profile = self.profile_repo.get_active()
        total_bankroll = self.profile_repo.get_total_bankroll(profile.id)
        providers = self.db.query(Provider).filter(Provider.is_enabled == True).all()
        anchor_balance = next(
            (self.profile_repo.get_balance(profile.id, p.id) for p in providers if p.id == anchor_provider), 0
        )

        # Profile risk settings
        max_kelly = profile.kelly_fraction if profile else 0.25
        single_bet_cap_pct = (profile.max_stake_pct / 100.0) if profile else 0.03

        results = []
        for o in opportunities[:limit]:
            edge_raw = o.anchor_odds / o.fair_odds - 1 if o.fair_odds > 1 else 0

            if edge_raw > 0 and total_bankroll > 0:
                rec = calculate_stake(
                    bankroll_total=total_bankroll,
                    edge_raw=edge_raw,
                    odds=o.anchor_odds,
                    min_odds=0.0,
                    max_kelly=max_kelly,
                    single_bet_cap_pct=single_bet_cap_pct,
                )
                suggested = min(rec.stake, anchor_balance) if rec.stake > 0 else 0
                kelly_amount = rec.raw_kelly_stake
                max_amount = rec.single_bet_cap
            else:
                suggested = 0
                kelly_amount = 0
                max_amount = total_bankroll * 0.05 if total_bankroll > 0 else 0

            results.append({
                "event_id": o.event_id,
                "market": o.market,
                "outcome": o.outcome,
                "anchor_provider": o.anchor_provider,
                "anchor_odds": o.anchor_odds,
                "fair_odds": o.fair_odds,
                "edge_pct": o.edge_pct,
                "home_team": o.home_team,
                "away_team": o.away_team,
                "sport": o.sport,
                "suggested_stake": round(suggested, 2),
                "kelly_stake": round(kelly_amount, 2),
                "max_stake": round(max_amount, 2),
            })

        return {
            "opportunities": results,
            "count": len(opportunities),
            "anchor_provider": anchor_provider,
            "total_bankroll": round(total_bankroll, 2),
            "anchor_balance": round(anchor_balance, 2),
        }

    def _add_stake_recommendation(self, result: dict, opp, profile, stake_calculator: StakeCalculator):
        """Add stake recommendation fields to an opportunity result dict."""
        try:
            edge_raw = (opp.odds1 / opp.odds2 - 1) if opp.odds2 > 1 else 0
            bonus_status = self.profile_repo.get_bonus_status(profile.id, opp.provider1_id)
            min_odds = 0.0 if bonus_status.get("is_cleared", True) else bonus_status.get("min_odds", BONUS_MIN_ODDS)

            stake_rec = stake_calculator.calculate(
                edge_raw=edge_raw,
                odds=opp.odds1,
                event_id=opp.event_id,
                provider_id=opp.provider1_id,
                min_odds=min_odds,
            )
            result["suggested_stake"] = round(stake_rec.raw_kelly_stake, 2)
            result["final_stake"] = round(stake_rec.stake, 2)
            result["kelly_fraction"] = stake_rec.kelly_fraction
            result["skip_reason"] = stake_rec.skip_reason
            result["bonus_cleared"] = bonus_status.get("is_cleared", True)
        except Exception as e:
            logger.debug(f"Stake calculation failed for opp {opp.id}: {e}")
            result["suggested_stake"] = None
            result["final_stake"] = None
            result["kelly_fraction"] = None
            result["skip_reason"] = None
            result["bonus_cleared"] = None
