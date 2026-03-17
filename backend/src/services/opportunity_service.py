"""Opportunity service - value bet listing, hedging, and bonus scanning."""

import logging
from sqlalchemy.orm import Session

from ..repositories import ProfileRepo, OpportunityRepo, OddsRepo
from ..analysis import find_best_hedge
from ..analysis.scanner import OpportunityScanner
from ..bankroll.stake_calculator import StakeCalculator, calculate_stake, BONUS_MIN_ODDS, dynamic_min_stake
from ..constants import PROVIDER_CANONICAL, CANONICAL_MEMBERS, MAJOR_LEAGUES_FLAT
from ..db.models import Event, Provider, Odds
from ..risk.allocator import ProviderAllocator

logger = logging.getLogger(__name__)


def _get_dutch_legs(outcomes) -> list:
    """Extract legs list from outcomes JSON (handles legacy list and new dict format)."""
    if isinstance(outcomes, list):
        return outcomes
    if isinstance(outcomes, dict):
        return outcomes.get("legs", [])
    return []


def _get_arb_data(outcomes) -> tuple:
    """Extract arb data from outcomes JSON. Returns (arb_profit_pct, arb_legs)."""
    if isinstance(outcomes, dict):
        return outcomes.get("arb_profit_pct"), outcomes.get("arb_legs")
    return None, None


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
        provider_ids = None
        if providers:
            raw_ids = [p.strip() for p in providers.split(',')]
            # Expand to include canonical providers so dutch/reverse stored
            # under canonical names are matched when filtering by member alias
            expanded = set(raw_ids)
            for pid in raw_ids:
                canon = PROVIDER_CANONICAL.get(pid)
                if canon:
                    expanded.add(canon)
            provider_ids = list(expanded)

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

        # Batch pre-fetch provider_meta and odds_updated_at for all opportunities (avoid N+1)
        meta_cache = self._batch_lookup_provider_meta(rows)
        updated_at_cache = self._batch_lookup_odds_updated_at(rows)

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
                "display_home": event.display_home if event else None,
                "display_away": event.display_away if event else None,
                "starts_at": (event.start_time.isoformat() + "Z") if event and event.start_time else None,
            }

            # Attach provider_meta from pre-fetched cache
            meta_key = (opp.event_id, opp.provider1_id, opp.market, opp.outcome1, opp.point)
            meta = meta_cache.get(meta_key)
            result["provider_meta"] = meta

            # Attach odds freshness timestamp
            result["odds_updated_at"] = updated_at_cache.get(meta_key)

            # Expose provider's own team names (for copy-paste between app and sportsbook)
            if isinstance(meta, dict):
                result["prov_home"] = meta.get("prov_home")
                result["prov_away"] = meta.get("prov_away")
            else:
                result["prov_home"] = None
                result["prov_away"] = None

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

        # Compute provider allocation scores (daily caps + wagering priority)
        if type == 'value' and profile and results:
            try:
                allocator = ProviderAllocator(self.db, profile.id)
                allocator.preload_daily_bets()
                allocator.preload_wagering()
                allocator.preload_balances()
                for result in results:
                    alloc = allocator.score_provider(result["provider1"])
                    result["allocation_score"] = alloc.score
                    result["allocation_reason"] = alloc.reason
                    result["daily_bets_group"] = alloc.daily_bets_group
                    result["daily_cap"] = alloc.daily_cap
                    result["is_daily_capped"] = alloc.is_capped
            except Exception as e:
                logger.warning(f"Allocation scoring failed: {e}")

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

    def scan_dutch_workflow(
        self,
        anchor_providers: list[str],
        major_only: bool = False,
        counterpart_providers: list[str] | None = None,
        limit: int = 50,
    ) -> dict:
        """Live-scan dutch opportunities forcing anchor providers into legs.

        Scans the odds DB for each anchor provider and returns dutch
        opportunities sorted by edge (including negative edge).
        Optionally restricts counterpart (non-anchor) legs to specific providers.
        """
        scanner = OpportunityScanner(self.db)

        # Resolve counterpart canonical IDs and build reverse map
        counterpart_canonical = None
        counterpart_alias_map: dict[str, str] = {}  # canonical → requested alias
        if counterpart_providers:
            for cp in counterpart_providers:
                canon = PROVIDER_CANONICAL.get(cp, cp)
                counterpart_alias_map[canon] = cp
            counterpart_canonical = list(counterpart_alias_map.keys())

        # Scan for each anchor provider
        seen: dict[str, dict] = {}  # key = "event_id|market" -> best opp dict
        for provider_id in anchor_providers:
            canonical = PROVIDER_CANONICAL.get(provider_id, provider_id)
            opps = scanner.scan_dutch_for_provider(canonical, counterpart_providers=counterpart_canonical)
            for opp in opps:
                key = f"{opp.event_id}|{opp.market}"
                # Replace provider in legs from canonical → requested alias
                legs = []
                for leg in opp.legs:
                    if leg["provider"] == canonical and canonical != provider_id:
                        legs.append({**leg, "provider": provider_id})
                    elif leg["provider"] in counterpart_alias_map:
                        legs.append({**leg, "provider": counterpart_alias_map[leg["provider"]]})
                    else:
                        legs.append(leg)

                entry = {
                    "event_id": opp.event_id,
                    "market": opp.market,
                    "sport": opp.sport,
                    "league": opp.league,
                    "home_team": opp.home_team,
                    "away_team": opp.away_team,
                    "starts_at": opp.start_time,
                    "combined_edge_pct": opp.combined_edge_pct,
                    "guaranteed_profit_pct": opp.guaranteed_profit_pct,
                    "legs": legs,
                    "arb_profit_pct": opp.arb_profit_pct,
                    "arb_legs": opp.arb_legs,
                }

                # Keep best edge per event+market
                if key not in seen or entry["combined_edge_pct"] > seen[key]["combined_edge_pct"]:
                    seen[key] = entry

        results = list(seen.values())

        # Filter to major leagues if requested
        if major_only:
            results = [r for r in results if r.get("league") in MAJOR_LEAGUES_FLAT]

        # Sort by edge desc
        results.sort(key=lambda x: x["combined_edge_pct"], reverse=True)

        # Batch-load display names and provider team names for all events
        event_ids = list({r["event_id"] for r in results[:limit]})
        events_map: dict[str, Event] = {}
        prov_names_map: dict[tuple[str, str], tuple[str | None, str | None]] = {}  # (event_id, provider_id) → names
        if event_ids:
            events_list = self.db.query(Event).filter(Event.id.in_(event_ids)).all()
            events_map = {e.id: e for e in events_list}

            # Collect all (event_id, provider_id) pairs from legs
            leg_pairs = set()
            for r in results[:limit]:
                for leg in r.get("legs", []):
                    canonical = PROVIDER_CANONICAL.get(leg["provider"], leg["provider"])
                    leg_pairs.add((r["event_id"], canonical))

            if leg_pairs:
                leg_event_ids = list({p[0] for p in leg_pairs})
                leg_provider_ids = list({p[1] for p in leg_pairs})
                odds_rows = (
                    self.db.query(Odds.event_id, Odds.provider_id, Odds.provider_meta)
                    .filter(
                        Odds.event_id.in_(leg_event_ids),
                        Odds.provider_id.in_(leg_provider_ids),
                        Odds.provider_meta.isnot(None),
                    )
                    .all()
                )
                for eid, pid, meta in odds_rows:
                    if isinstance(meta, dict) and (meta.get("prov_home") or meta.get("prov_away")):
                        key = (eid, pid)
                        if key not in prov_names_map:
                            prov_names_map[key] = (meta.get("prov_home"), meta.get("prov_away"))

        # Format for API response (DutchOpp-compatible)
        formatted = []
        for i, r in enumerate(results[:limit]):
            # Extract point from market key
            point_value = None
            clean_market = r["market"]
            if "_" in r["market"] and r["market"].split("_")[-1].replace(".", "").replace("-", "").isdigit():
                parts = r["market"].rsplit("_", 1)
                if len(parts) == 2:
                    try:
                        point_value = float(parts[-1])
                        clean_market = parts[0]
                    except ValueError:
                        pass

            ev = events_map.get(r["event_id"])

            # Pick provider names from the first non-sharp leg (the anchor)
            prov_home, prov_away = None, None
            for leg in r.get("legs", []):
                if not leg.get("is_sharp"):
                    canonical = PROVIDER_CANONICAL.get(leg["provider"], leg["provider"])
                    names = prov_names_map.get((r["event_id"], canonical))
                    if names:
                        prov_home, prov_away = names
                    break

            formatted.append({
                "id": i + 1,
                "type": "dutch",
                "event_id": r["event_id"],
                "market": clean_market,
                "point": point_value,
                "profit_pct": r["guaranteed_profit_pct"],
                "edge_pct": r["combined_edge_pct"],
                "guaranteed_profit_pct": r["guaranteed_profit_pct"],
                "sport": r["sport"],
                "league": r["league"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "display_home": ev.display_home if ev else None,
                "display_away": ev.display_away if ev else None,
                "prov_home": prov_home,
                "prov_away": prov_away,
                "starts_at": r["starts_at"],
                "legs": r["legs"],
                "total_stake": 0,  # Frontend sets via anchor stake
                "arb_profit_pct": r.get("arb_profit_pct"),
                "arb_legs": r.get("arb_legs"),
            })

        # Include wagering info for anchor providers
        anchor_wagering = {}
        profile = self.profile_repo.get_active()
        if profile:
            for pid in anchor_providers:
                bonus = self.profile_repo.get_bonus_status(profile.id, pid)
                if bonus and bonus.get("status") in ("in_progress", "trigger_needed", "freebet_available"):
                    anchor_wagering[pid] = {
                        "status": bonus["status"],
                        "wagered": bonus.get("wagered_amount", 0),
                        "requirement": bonus.get("wagering_requirement", 0),
                        "remaining": max(0, (bonus.get("wagering_requirement", 0) or 0) - (bonus.get("wagered_amount", 0) or 0)),
                        "progress_pct": bonus.get("progress_pct", 0),
                        "min_odds": bonus.get("min_odds", 0),
                        "bonus_amount": bonus.get("bonus_amount", 0),
                        "bonus_type": bonus.get("bonus_type"),
                        "days_remaining": bonus.get("days_remaining"),
                    }

        return {
            "opportunities": formatted,
            "count": len(results),
            "anchor_providers": anchor_providers,
            "anchor_wagering": anchor_wagering,
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
            bonus_type = bonus_status.get("bonus_type")
            bonus_amount = bonus_status.get("bonus_amount", 0)
            # Bonusdeposit triggers look like normal bets (no TRG badge)
            show_bonus = bs in ("trigger_needed", "freebet_available")
            if bs == "trigger_needed" and bonus_type == "bonusdeposit":
                show_bonus = False
            result["bonus_status"] = bs if show_bonus else None
            result["bonus_amount"] = bonus_amount if show_bonus else None
            result["min_odds_applied"] = min_odds if min_odds > 0 else None

            # Bonusdeposit triggers use normal Kelly stakes — no override needed
            # Only freebets and freebet triggers get stake overrides
            if bs == "trigger_needed" and bonus_type == "bonusdeposit":
                pass  # Keep Kelly stake, bets count toward trigger wagering naturally
            elif bs in ("trigger_needed", "freebet_available") and bonus_amount > 0:
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
            legs_data = _get_dutch_legs(opp.outcomes)
            arb_profit_pct, arb_legs = _get_arb_data(opp.outcomes)
            guaranteed_profit_pct = opp.profit_pct or 0
            total_stake = 0.0

            # Check if any leg's provider is in freebet trigger/available mode
            # (bonusdeposit triggers play normally, no stake override needed)
            trigger_provider = None
            trigger_amount = 0.0
            for leg in legs_data:
                pid = leg.get("provider_id") or leg.get("provider", "")
                if pid:
                    bs = self.profile_repo.get_bonus_status(profile.id, pid)
                    bst = bs.get("status")
                    btype = bs.get("bonus_type")
                    if bst == "trigger_needed" and btype == "bonusdeposit":
                        continue  # Normal Kelly for bonusdeposit triggers
                    if bst in ("trigger_needed", "freebet_available"):
                        trigger_provider = pid
                        trigger_amount = bs.get("bonus_amount", 0)
                        break

            if trigger_provider and trigger_amount > 0 and legs_data:
                # Scale total dutch stake so the trigger provider's leg equals trigger_amount
                total_inv = sum(1.0 / leg["odds"] for leg in legs_data)
                # Find the trigger leg's inverse-odds weight
                trigger_leg_inv = 0.0
                for leg in legs_data:
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
                ev_legs = [l for l in legs_data if l.get("edge_pct", 0) > 0]
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
            if legs_data and total_stake > 0:
                total_inv = sum(1.0 / leg["odds"] for leg in legs_data)
                for leg in legs_data:
                    leg_stake = round(total_stake * (1.0 / leg["odds"]) / total_inv, 2)
                    leg_return = round(leg_stake * leg["odds"], 2)
                    legs_with_stakes.append({
                        **leg,
                        "stake": leg_stake,
                        "potential_return": leg_return,
                    })

            result["guaranteed_profit_pct"] = guaranteed_profit_pct
            result["total_stake"] = round(total_stake, 2)
            result["legs"] = legs_with_stakes or legs_data
            result["trigger_provider"] = trigger_provider
            result["arb_profit_pct"] = arb_profit_pct
            result["arb_legs"] = arb_legs
        except Exception as e:
            logger.debug(f"Dutch stake calculation failed for opp {opp.id}: {e}")
            result["guaranteed_profit_pct"] = opp.profit_pct or 0
            result["total_stake"] = 0
            result["legs"] = _get_dutch_legs(opp.outcomes)
            result["trigger_provider"] = None
            result["arb_profit_pct"] = None
            result["arb_legs"] = None

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

    def _batch_lookup_odds_updated_at(self, rows) -> dict:
        """Batch-load odds updated_at timestamps for all opportunities in one query."""
        if not rows:
            return {}

        lookup_pairs = set()
        for opp, _ in rows:
            canonical = PROVIDER_CANONICAL.get(opp.provider1_id, opp.provider1_id)
            lookup_pairs.add((opp.event_id, canonical))

        if not lookup_pairs:
            return {}

        event_ids = list({p[0] for p in lookup_pairs})
        provider_ids = list({p[1] for p in lookup_pairs})

        odds_rows = (
            self.db.query(Odds.event_id, Odds.provider_id, Odds.market, Odds.outcome, Odds.point, Odds.updated_at)
            .filter(
                Odds.event_id.in_(event_ids),
                Odds.provider_id.in_(provider_ids),
            )
            .all()
        )

        updated_index = {}
        for eid, pid, market, outcome, point, updated_at in odds_rows:
            updated_index[(eid, pid, market, outcome, point)] = (
                updated_at.isoformat() + "Z" if updated_at else None
            )

        result = {}
        for opp, _ in rows:
            canonical = PROVIDER_CANONICAL.get(opp.provider1_id, opp.provider1_id)
            key = (opp.event_id, canonical, opp.market, opp.outcome1, opp.point)
            orig_key = (opp.event_id, opp.provider1_id, opp.market, opp.outcome1, opp.point)
            result[orig_key] = updated_index.get(key)

        return result
