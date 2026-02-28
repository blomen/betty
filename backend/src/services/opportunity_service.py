"""Opportunity service - value bet listing, hedging, and bonus scanning."""

import logging
from sqlalchemy.orm import Session

from ..repositories import ProfileRepo, OpportunityRepo, OddsRepo
from ..analysis import find_best_hedge
from ..analysis.scanner import OpportunityScanner
from ..bankroll.stake_calculator import StakeCalculator, calculate_stake, BONUS_MIN_ODDS, dynamic_min_stake
from ..constants import PROVIDER_CANONICAL
from ..db.models import Provider, Odds

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
        limit: int = 2000,
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
            limit=limit,
        )

        # Initialize stake calculator for value/dutch/reverse/reverse_value bets using profile risk settings
        stake_calculator = None
        profile = None
        if type in ('value', 'dutch', 'reverse', 'reverse_value') and rows:
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

        # Batch pre-fetch provider_meta for all opportunities (avoid N+1)
        meta_cache = self._batch_lookup_provider_meta(rows)

        # Batch pre-fetch bonus statuses for all providers (avoid N+1)
        bonus_cache = {}
        if profile and type == 'value':
            provider_ids = list({opp.provider1_id for opp, _ in rows if opp.provider1_id})
            for pid in provider_ids:
                try:
                    bonus_cache[pid] = self.profile_repo.get_bonus_status(profile.id, pid)
                except Exception:
                    bonus_cache[pid] = {"is_cleared": True}

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

            # Attach provider_meta from pre-fetched cache
            meta_key = (opp.event_id, opp.provider1_id, opp.market, opp.outcome1, opp.point)
            result["provider_meta"] = meta_cache.get(meta_key)

            # Add stake recommendations for value bets
            if type == 'value' and stake_calculator and profile and opp.odds1 and opp.odds2:
                self._add_stake_recommendation(result, opp, profile, stake_calculator, bonus_cache)

            # Add dutch/reverse-specific fields
            if type in ('dutch', 'reverse') and stake_calculator and profile:
                self._add_dutch_recommendation(result, opp, profile, stake_calculator)

            # Add stake recommendations for reverse value bets (Pinnacle vs consensus)
            if type == 'reverse_value' and stake_calculator and profile and opp.odds1 and opp.odds2:
                self._add_reverse_value_recommendation(result, opp, stake_calculator)

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
                    min_stake=dynamic_min_stake(total_bankroll),
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

    def _add_stake_recommendation(self, result: dict, opp, profile, stake_calculator: StakeCalculator, bonus_cache: dict | None = None):
        """Add stake recommendation fields to an opportunity result dict."""
        try:
            edge_raw = (opp.odds1 / opp.odds2 - 1) if opp.odds2 > 1 else 0
            bonus_status = (bonus_cache or {}).get(opp.provider1_id) or self.profile_repo.get_bonus_status(profile.id, opp.provider1_id)
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
            result["bankroll_needed"] = stake_rec.bankroll_needed if stake_rec.bankroll_needed > 0 else None
            result["bonus_cleared"] = bonus_status.get("is_cleared", True)

            # Freebet phase overrides: force stake to bonus_amount
            bs = bonus_status.get("status")
            bonus_amount = bonus_status.get("bonus_amount", 0)
            result["bonus_status"] = bs if bs in ("trigger_needed", "freebet_available") else None
            result["bonus_amount"] = bonus_amount if result["bonus_status"] else None
            result["min_odds_applied"] = min_odds if min_odds > 0 else None

            if bs in ("trigger_needed", "freebet_available") and bonus_amount > 0:
                # Check if a pending trigger bet already exists for this provider
                if bs == "trigger_needed":
                    from ..db.models import Bet
                    pending_trigger = self.db.query(Bet).filter(
                        Bet.profile_id == profile.id,
                        Bet.provider_id == opp.provider1_id,
                        Bet.result == "pending",
                        Bet.stake >= bonus_amount,
                    ).first()
                    if pending_trigger:
                        result["final_stake"] = 0
                        result["skip_reason"] = "trigger_placed"
                        return

                # Override stake to the exact freebet/trigger amount
                # but only if odds qualify and balance is sufficient
                balance = self.profile_repo.get_balance(profile.id, opp.provider1_id)
                is_freebet = bs == "freebet_available"
                has_balance = is_freebet or balance >= bonus_amount  # freebets don't need balance

                if opp.odds1 >= min_odds and has_balance:
                    result["final_stake"] = bonus_amount
                    result["skip_reason"] = None
                elif opp.odds1 >= min_odds and not has_balance:
                    result["final_stake"] = 0
                    result["skip_reason"] = "no_balance"
                # If odds < min_odds, keep skip_reason and final_stake=0

        except Exception as e:
            logger.debug(f"Stake calculation failed for opp {opp.id}: {e}")
            result["suggested_stake"] = None
            result["final_stake"] = None
            result["kelly_fraction"] = None
            result["skip_reason"] = None
            result["bankroll_needed"] = None
            result["bonus_cleared"] = None
            result["bonus_status"] = None
            result["bonus_amount"] = None
            result["min_odds_applied"] = None

    def _add_dutch_recommendation(self, result: dict, opp, profile, stake_calculator: StakeCalculator):
        """Add dutch stake recommendation fields to an opportunity result dict."""
        try:
            guaranteed_profit_pct = opp.profit_pct or 0
            total_stake = 0.0

            # Check if any leg's provider is in trigger_needed or freebet_available mode
            trigger_provider = None
            trigger_amount = 0.0
            for leg in (opp.outcomes or []):
                pid = leg.get("provider_id") or leg.get("provider", "")
                if pid:
                    bs = self.profile_repo.get_bonus_status(profile.id, pid)
                    if bs.get("status") in ("trigger_needed", "freebet_available"):
                        trigger_provider = pid
                        trigger_amount = bs.get("bonus_amount", 0)
                        break

            if trigger_provider and trigger_amount > 0 and opp.outcomes:
                # Scale total dutch stake so the trigger provider's leg equals trigger_amount
                total_inv = sum(1.0 / leg["odds"] for leg in opp.outcomes)
                # Find the trigger leg's inverse-odds weight
                trigger_leg_inv = 0.0
                for leg in opp.outcomes:
                    pid = leg.get("provider_id") or leg.get("provider", "")
                    if pid == trigger_provider:
                        trigger_leg_inv += 1.0 / leg["odds"]
                if trigger_leg_inv > 0:
                    # trigger_leg_stake = total_stake * (trigger_leg_inv / total_inv) = trigger_amount
                    total_stake = trigger_amount * total_inv / trigger_leg_inv
            elif guaranteed_profit_pct > 0:
                # Guaranteed profit dutch: use max single bet cap (no Kelly needed, it's riskless)
                total_stake = stake_calculator.bankroll * stake_calculator.single_bet_cap_pct
            else:
                # Partial coverage: use Kelly on the best EV leg's edge
                ev_legs = [l for l in (opp.outcomes or []) if l.get("edge_pct", 0) > 0]
                if ev_legs:
                    best_edge = max(l["edge_pct"] for l in ev_legs) / 100.0
                    best_odds = next(l["odds"] for l in ev_legs if l["edge_pct"] == max(l["edge_pct"] for l in ev_legs))
                    stake_rec = stake_calculator.calculate(
                        edge_raw=best_edge,
                        odds=best_odds,
                        event_id=opp.event_id,
                    )
                    total_stake = stake_rec.stake

            # Split into per-leg stakes using dutch formula
            legs_with_stakes = []
            if opp.outcomes and total_stake > 0:
                total_inv = sum(1.0 / leg["odds"] for leg in opp.outcomes)
                for leg in opp.outcomes:
                    leg_stake = round(total_stake * (1.0 / leg["odds"]) / total_inv, 2)
                    leg_return = round(leg_stake * leg["odds"], 2)
                    legs_with_stakes.append({
                        **leg,
                        "stake": leg_stake,
                        "potential_return": leg_return,
                    })

            result["guaranteed_profit_pct"] = guaranteed_profit_pct
            result["total_stake"] = round(total_stake, 2)
            result["legs"] = legs_with_stakes or opp.outcomes or []
            result["trigger_provider"] = trigger_provider
        except Exception as e:
            logger.debug(f"Dutch stake calculation failed for opp {opp.id}: {e}")
            result["guaranteed_profit_pct"] = opp.profit_pct or 0
            result["total_stake"] = 0
            result["legs"] = opp.outcomes or []
            result["trigger_provider"] = None

    def _add_reverse_value_recommendation(self, result: dict, opp, stake_calculator: StakeCalculator):
        """Add stake recommendation for reverse value bets (Pinnacle vs consensus)."""
        try:
            edge_raw = (opp.odds1 / opp.odds2 - 1) if opp.odds2 > 1 else 0

            stake_rec = stake_calculator.calculate(
                edge_raw=edge_raw,
                odds=opp.odds1,
                event_id=opp.event_id,
                provider_id="pinnacle",
            )
            result["suggested_stake"] = round(stake_rec.raw_kelly_stake, 2)
            result["final_stake"] = round(stake_rec.stake, 2)
            result["kelly_fraction"] = stake_rec.kelly_fraction
            result["skip_reason"] = stake_rec.skip_reason
            result["bankroll_needed"] = stake_rec.bankroll_needed if stake_rec.bankroll_needed > 0 else None

        except Exception as e:
            logger.debug(f"Reverse value stake calculation failed for opp {opp.id}: {e}")
            result["suggested_stake"] = None
            result["final_stake"] = None
            result["kelly_fraction"] = None
            result["skip_reason"] = None
            result["bankroll_needed"] = None

    def _batch_lookup_provider_meta(self, rows) -> dict:
        """Batch-load provider_meta for all opportunities in one query."""
        if not rows:
            return {}

        # Collect unique (event_id, canonical_provider_id) pairs
        lookup_pairs = set()
        for opp, _ in rows:
            canonical = PROVIDER_CANONICAL.get(opp.provider1_id, opp.provider1_id)
            lookup_pairs.add((opp.event_id, canonical))

        if not lookup_pairs:
            return {}

        # Batch query all relevant odds rows
        event_ids = list({p[0] for p in lookup_pairs})
        provider_ids = list({p[1] for p in lookup_pairs})

        odds_rows = (
            self.db.query(Odds.event_id, Odds.provider_id, Odds.market, Odds.outcome, Odds.point, Odds.provider_meta)
            .filter(
                Odds.event_id.in_(event_ids),
                Odds.provider_id.in_(provider_ids),
                Odds.provider_meta.isnot(None),
            )
            .all()
        )

        # Build lookup dict keyed by (event_id, provider_id, market, outcome, point)
        meta_index = {}
        for eid, pid, market, outcome, point, meta in odds_rows:
            meta_index[(eid, pid, market, outcome, point)] = meta

        # Map back to original provider_ids (non-canonical)
        result = {}
        for opp, _ in rows:
            canonical = PROVIDER_CANONICAL.get(opp.provider1_id, opp.provider1_id)
            key = (opp.event_id, canonical, opp.market, opp.outcome1, opp.point)
            orig_key = (opp.event_id, opp.provider1_id, opp.market, opp.outcome1, opp.point)
            result[orig_key] = meta_index.get(key)

        return result

    def _lookup_provider_meta(
        self,
        event_id: str,
        provider_id: str,
        market: str,
        outcome: str,
        point: float | None,
    ) -> dict | None:
        """Look up provider_meta from the Odds table for browser navigation.

        Handles platform consolidation: if provider_id is non-canonical (e.g. 'expekt'),
        the Odds row is stored under the canonical provider ('unibet').
        """
        try:
            canonical = PROVIDER_CANONICAL.get(provider_id, provider_id)
            q = self.db.query(Odds.provider_meta).filter(
                Odds.event_id == event_id,
                Odds.provider_id == canonical,
                Odds.market == market,
                Odds.outcome == outcome,
            )
            if point is not None:
                q = q.filter(Odds.point == point)
            else:
                q = q.filter(Odds.point.is_(None))

            row = q.first()
            return row[0] if row and row[0] else None
        except Exception:
            return None
