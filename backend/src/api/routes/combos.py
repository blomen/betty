"""Combo Profit Boost API routes — combo/parlay recommendations from +EV value bets."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...analysis.scanner import OpportunityScanner
from ...analysis.combo_optimizer import ComboOptimizer
from ...repositories import ProfileRepo
from ..deps import get_db
from .providers import load_combo_boost_configs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/combos", tags=["combos"])


@router.get("")
async def get_combo_recommendations(
    provider: Optional[str] = Query(None, description="Filter to specific provider"),
    min_legs: int = Query(3, ge=3, le=20, description="Minimum legs per combo"),
    max_legs: int = Query(15, ge=3, le=20, description="Maximum legs per combo"),
    min_edge_pct: float = Query(2.0, ge=0, description="Min edge for value bet scan"),
    db: Session = Depends(get_db),
):
    """
    Get combo profit boost recommendations.

    Runs the value scanner, then for each provider with a combo_boost config,
    builds optimal combos from the +EV selections and applies the boost table.
    """
    boost_configs = load_combo_boost_configs()
    if not boost_configs:
        return {"providers": [], "total_combos": 0, "value_bets_scanned": 0}

    if provider:
        boost_configs = {k: v for k, v in boost_configs.items() if k == provider}
        if not boost_configs:
            return {"providers": [], "total_combos": 0, "value_bets_scanned": 0}

    # Run scanner once — shared across all providers
    scanner = OpportunityScanner(db)
    value_bets = scanner.scan_value(min_edge_pct=min_edge_pct)

    if not value_bets:
        return {"providers": [], "total_combos": 0, "value_bets_scanned": 0}

    # Get bankroll for stake sizing
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    bankroll = profile_repo.get_total_bankroll(profile.id)

    results = []
    total_combos = 0

    for pid, config in boost_configs.items():
        # Clamp max_legs to provider's config
        provider_max = config.get("max_legs", 20)
        effective_max = min(max_legs, provider_max)
        clamped_config = {**config, "max_legs": effective_max}

        optimizer = ComboOptimizer(
            boost_config=clamped_config,
            bankroll=bankroll,
            max_kelly=profile.kelly_fraction,
        )

        combos = optimizer.optimize(
            value_bets=value_bets,
            provider=pid,
            min_legs=min_legs,
        )

        if not combos:
            continue

        provider_combos = []
        for combo in combos:
            provider_combos.append({
                "provider": combo.provider,
                "num_legs": combo.num_legs,
                "legs": [
                    {
                        "event_id": leg.event_id,
                        "market": leg.market,
                        "outcome": leg.outcome,
                        "provider_odds": round(leg.provider_odds, 3),
                        "fair_odds": round(leg.fair_odds, 3),
                        "edge_pct": round(leg.edge_pct, 2),
                        "home_team": leg.home_team,
                        "away_team": leg.away_team,
                        "sport": leg.sport,
                        "start_time": leg.start_time,
                        "point": leg.point,
                    }
                    for leg in combo.legs
                ],
                "combined_offered_odds": round(combo.combined_offered_odds, 3),
                "combined_fair_odds": round(combo.combined_fair_odds, 3),
                "boost_pct": combo.boost_pct,
                "effective_odds": round(combo.effective_odds, 3),
                "edge_pct": round(combo.edge_pct, 2),
                "win_probability": round(combo.win_probability, 6),
                "ev_per_unit": round(combo.ev_per_unit, 4),
                "kelly_fraction": round(combo.kelly_fraction, 6),
                "recommended_stake": combo.recommended_stake,
                "skip_reason": combo.skip_reason,
            })

        results.append({
            "provider": pid,
            "config": {
                "min_odds_per_leg": config.get("min_odds_per_leg", 1.40),
                "boost_table": {
                    str(k): v for k, v in config.get("boost_table", {}).items()
                },
            },
            "combos": provider_combos,
            "combo_count": len(provider_combos),
        })
        total_combos += len(provider_combos)

    return {
        "providers": results,
        "total_combos": total_combos,
        "value_bets_scanned": len(value_bets),
    }
