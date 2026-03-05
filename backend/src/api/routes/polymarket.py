"""Polymarket API routes: matched events, value bets, stats, mybets."""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from ...db.models import Event, Odds, Bet
from ...repositories import ProfileRepo, BetRepo
from ...analysis.scanner import OpportunityScanner
from ...bankroll.stake_calculator import StakeCalculator, BONUS_MIN_ODDS
from ...config import get_exchange_rate
from ..deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/polymarket", tags=["polymarket"])


@router.get("/value")
async def get_polymarket_value(
    min_edge: float = Query(3.0, description="Minimum edge percentage"),
    sport: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Get Polymarket value bets vs de-vigged Pinnacle odds."""
    scanner = OpportunityScanner(db)
    all_values = scanner.scan_value(min_edge_pct=min_edge)

    # Filter to polymarket only
    poly_values = [vb for vb in all_values if vb.provider == "polymarket"]

    # Use total bankroll (same as Value page) — Polymarket is just another provider
    stake_calculator = None
    profile = None
    profile_repo = None
    total_bankroll = 0.0
    if poly_values:
        try:
            profile_repo = ProfileRepo(db)
            profile = profile_repo.get_active()
            total_bankroll = profile_repo.get_total_bankroll(profile.id)
            stake_calculator = StakeCalculator(
                bankroll=total_bankroll,
                max_kelly=profile.kelly_fraction,
                single_bet_cap_pct=profile.max_stake_pct / 100.0,
                min_edge=profile.min_edge_pct / 100.0,
            )
        except Exception as e:
            logger.warning(f"Could not initialize stake calculator: {e}")

    # Batch-load all events (avoid N+1)
    event_ids = list({vb.event_id for vb in poly_values})
    events_map = {}
    if event_ids:
        events_list = db.query(Event).filter(Event.id.in_(event_ids)).all()
        events_map = {e.id: e for e in events_list}

    # Batch-load provider_meta + updated_at from Odds for event_slug (needed for deep links)
    # Key: (event_id, market, outcome) → provider_meta dict
    odds_meta_map: dict[tuple, dict] = {}
    odds_updated_map: dict[tuple, str] = {}
    if event_ids:
        poly_odds = (
            db.query(Odds)
            .filter(Odds.event_id.in_(event_ids), Odds.provider_id == "polymarket")
            .all()
        )
        for o in poly_odds:
            key = (o.event_id, o.market, o.outcome)
            if o.provider_meta and "event_slug" in (o.provider_meta if isinstance(o.provider_meta, dict) else {}):
                odds_meta_map[key] = o.provider_meta
            if o.updated_at:
                odds_updated_map[key] = o.updated_at.isoformat()

    # Enrich with event context
    results = []
    for vb in poly_values:
        event = events_map.get(vb.event_id)
        if not event:
            continue

        if sport and event.sport != sport:
            continue

        # Polymarket price = implied probability (1/odds), shown in whole cents
        price_cents = round(1 / vb.provider_odds * 100) if vb.provider_odds > 0 else 0
        fair_price_cents = round(1 / vb.fair_odds * 100) if vb.fair_odds > 0 else 0
        usdc_rate = get_exchange_rate("polymarket")  # USDC → SEK

        # Look up provider_meta for deep link URL
        meta = odds_meta_map.get((vb.event_id, vb.market, vb.outcome), {})
        event_slug = meta.get("event_slug") if isinstance(meta, dict) else None

        result = {
            "event_id": vb.event_id,
            "market": vb.market,
            "outcome": vb.outcome,
            "polymarket_odds": vb.provider_odds,
            "fair_odds": vb.fair_odds,
            "fair_probability": vb.fair_probability,
            "edge_pct": vb.edge_pct,
            "point": vb.point,
            "home_team": event.home_team,
            "away_team": event.away_team,
            "display_home": event.display_home,
            "display_away": event.display_away,
            "sport": event.sport,
            "league": event.league,
            "start_time": (event.start_time.isoformat() + "Z") if event.start_time else None,
            # Polymarket-native fields
            "price_cents": price_cents,
            "fair_price_cents": fair_price_cents,
            "exchange_rate_sek": usdc_rate,
            # Navigation — event_slug for deep linking to polymarket.com/event/{slug}
            "event_slug": event_slug,
            "provider_meta": {"event_slug": event_slug} if event_slug else None,
            "updated_at": odds_updated_map.get((vb.event_id, vb.market, vb.outcome)),
        }

        # Add stake recommendation
        if stake_calculator and profile:
            try:
                edge_raw = (vb.provider_odds / vb.fair_odds - 1) if vb.fair_odds > 1 else 0
                bonus_status = profile_repo.get_bonus_status(profile.id, "polymarket")
                min_odds = 0.0 if bonus_status.get("is_cleared", True) else bonus_status.get("min_odds", BONUS_MIN_ODDS)

                stake_rec = stake_calculator.calculate(
                    edge_raw=edge_raw,
                    odds=vb.provider_odds,
                    event_id=vb.event_id,
                    provider_id="polymarket",
                    min_odds=min_odds,
                )
                stake_sek = stake_rec.stake
                stake_usdc = round(stake_sek / usdc_rate, 2) if usdc_rate > 0 else 0
                shares = round(stake_usdc / (price_cents / 100), 1) if price_cents > 0 else 0
                payout_usdc = round(shares * 1.0, 2)  # Each share pays $1 if correct

                result["suggested_stake"] = round(stake_rec.raw_kelly_stake, 2)
                result["final_stake"] = round(stake_sek, 2)  # SEK for internal tracking
                result["final_stake_usdc"] = stake_usdc
                result["shares"] = shares
                result["payout_usdc"] = payout_usdc
                result["kelly_fraction"] = stake_rec.kelly_fraction
                result["skip_reason"] = stake_rec.skip_reason
                result["bankroll_needed"] = stake_rec.bankroll_needed if stake_rec.bankroll_needed > 0 else None
                result["bonus_cleared"] = bonus_status.get("is_cleared", True)
            except Exception as e:
                logger.debug(f"Stake calculation failed for {vb.event_id}: {e}")
                result["suggested_stake"] = None
                result["final_stake"] = None
                result["final_stake_usdc"] = None
                result["shares"] = None
                result["payout_usdc"] = None
                result["kelly_fraction"] = None
                result["skip_reason"] = None
                result["bankroll_needed"] = None
                result["bonus_cleared"] = None

        results.append(result)

        if len(results) >= limit:
            break

    return {
        "value_bets": results,
        "count": len(results),
        "total_scanned": len(all_values),
        "total_bankroll": round(total_bankroll, 2),
    }


@router.get("/stats")
async def get_polymarket_stats(
    db: Session = Depends(get_db),
):
    """Get Polymarket extraction statistics and data quality metrics."""
    # Total Polymarket odds and events
    poly_odds_count = (
        db.query(func.count(Odds.id))
        .filter(Odds.provider_id == "polymarket")
        .scalar()
    ) or 0

    poly_event_ids = (
        db.query(Odds.event_id)
        .filter(Odds.provider_id == "polymarket")
        .distinct()
        .subquery()
    )
    poly_event_count = db.query(func.count()).select_from(poly_event_ids).scalar() or 0

    # Matched events (have both Polymarket + at least one other provider)
    matched_count = 0
    if poly_event_count > 0:
        matched_subq = (
            db.query(Odds.event_id)
            .filter(
                Odds.event_id.in_(
                    db.query(Odds.event_id)
                    .filter(Odds.provider_id == "polymarket")
                    .distinct()
                ),
                Odds.provider_id != "polymarket",
            )
            .distinct()
            .subquery()
        )
        matched_count = db.query(func.count()).select_from(matched_subq).scalar() or 0

    match_rate = round(matched_count / poly_event_count * 100, 1) if poly_event_count > 0 else 0

    # Outcome normalization rate
    normalized_count = (
        db.query(func.count(Odds.id))
        .filter(
            Odds.provider_id == "polymarket",
            Odds.outcome.in_(["home", "away", "draw"]),
        )
        .scalar()
    ) or 0
    normalization_rate = round(normalized_count / poly_odds_count * 100, 1) if poly_odds_count > 0 else 0

    # Sport breakdown
    sport_rows = (
        db.query(Event.sport, func.count(Event.id))
        .join(Odds, Event.id == Odds.event_id)
        .filter(Odds.provider_id == "polymarket")
        .group_by(Event.sport)
        .order_by(func.count(Event.id).desc())
        .all()
    )
    sports = [{"sport": row[0], "count": row[1]} for row in sport_rows]

    return {
        "total_odds": poly_odds_count,
        "total_events": poly_event_count,
        "matched_events": matched_count,
        "match_rate": match_rate,
        "normalization_rate": normalization_rate,
        "sports": sports,
    }


@router.get("/matched")
async def get_polymarket_matched(
    sport: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get Polymarket events matched with other providers, including Pinnacle fair odds."""

    # Find events that have Polymarket odds
    polymarket_events_subq = (
        db.query(Odds.event_id)
        .filter(Odds.provider_id == "polymarket")
        .distinct()
        .subquery()
    )

    # Find events that also have odds from other providers
    matched_events_subq = (
        db.query(Odds.event_id)
        .filter(
            Odds.event_id.in_(polymarket_events_subq),
            Odds.provider_id != "polymarket",
        )
        .distinct()
        .subquery()
    )

    # Query events with sport filter
    query = db.query(Event).filter(Event.id.in_(matched_events_subq))
    if sport:
        query = query.filter(Event.sport == sport)

    events = query.order_by(Event.start_time).limit(limit).all()

    # Batch-load all odds for matched events (avoid N+1)
    matched_event_ids = [e.id for e in events]
    all_event_odds = {}
    if matched_event_ids:
        odds_rows = db.query(Odds).filter(Odds.event_id.in_(matched_event_ids)).all()
        for o in odds_rows:
            all_event_odds.setdefault(o.event_id, []).append(o)

    result = []
    for event in events:
        # Get all odds for this event from pre-loaded batch
        all_odds = all_event_odds.get(event.id, [])

        # Separate Polymarket, Pinnacle, and other provider odds
        polymarket_odds = []
        pinnacle_odds_lookup = {}
        other_providers = {}

        for o in all_odds:
            odds_entry = {
                "outcome": o.outcome,
                "odds": o.odds,
            }

            if o.provider_id == "polymarket":
                polymarket_odds.append(odds_entry)
            elif o.provider_id == "pinnacle":
                pinnacle_odds_lookup[o.outcome] = o.odds
            else:
                if o.provider_id not in other_providers:
                    other_providers[o.provider_id] = []
                other_providers[o.provider_id].append(odds_entry)

        # Create Polymarket odds lookup
        poly_odds_lookup = {o["outcome"]: o["odds"] for o in polymarket_odds}

        # Calculate edges for other providers vs Polymarket
        edges = []
        best_edge = 0.0

        for provider_id, provider_odds in other_providers.items():
            for po in provider_odds:
                outcome = po["outcome"]
                provider_odd = po["odds"]
                poly_odd = poly_odds_lookup.get(outcome)

                if poly_odd and poly_odd > 0:
                    edge_pct = (provider_odd / poly_odd - 1) * 100
                    if edge_pct > 0:
                        edges.append({
                            "outcome": outcome,
                            "provider": provider_id,
                            "edge_pct": round(edge_pct, 2),
                            "provider_odds": provider_odd,
                            "polymarket_odds": poly_odd,
                        })
                        if edge_pct > best_edge:
                            best_edge = edge_pct

        edges.sort(key=lambda x: x["edge_pct"], reverse=True)

        # Calculate Polymarket edges vs Pinnacle fair odds
        polymarket_edges = []
        for outcome, poly_odd in poly_odds_lookup.items():
            pinnacle_odd = pinnacle_odds_lookup.get(outcome)
            if pinnacle_odd and pinnacle_odd > 0 and poly_odd > 0:
                edge_pct = (poly_odd / pinnacle_odd - 1) * 100
                polymarket_edges.append({
                    "outcome": outcome,
                    "polymarket_odds": poly_odd,
                    "pinnacle_odds": pinnacle_odd,
                    "edge_pct": round(edge_pct, 2),
                })

        polymarket_edges.sort(key=lambda x: x["edge_pct"], reverse=True)

        result.append({
            "id": event.id,
            "sport": event.sport,
            "league": event.league,
            "home_team": event.home_team,
            "away_team": event.away_team,
            "display_home": event.display_home,
            "display_away": event.display_away,
            "start_time": (event.start_time.isoformat() + "Z") if event.start_time else None,
            "polymarket_odds": polymarket_odds,
            "other_providers": other_providers,
            "edges": edges[:10],
            "best_edge": round(best_edge, 2),
            "polymarket_edges": polymarket_edges,
        })

    # Sort by best_edge descending
    result.sort(key=lambda x: x["best_edge"], reverse=True)

    return {
        "events": result,
        "count": len(result),
    }


# ──────────────────── My Bets (Polymarket) ────────────────────


@router.get("/mybets")
async def get_mybets(
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get Polymarket bet history with P&L stats."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    usdc_rate = get_exchange_rate("polymarket")

    query = db.query(Bet).filter(
        Bet.profile_id == profile.id,
        Bet.provider_id == "polymarket",
    )
    if status:
        query = query.filter(Bet.result == status)

    bets = query.order_by(Bet.placed_at.desc()).limit(limit).all()

    # Batch-load events
    event_ids = list({b.event_id for b in bets if b.event_id})
    events_map = {}
    if event_ids:
        events_list = db.query(Event).filter(Event.id.in_(event_ids)).all()
        events_map = {e.id: e for e in events_list}

    bet_items = []
    for b in bets:
        event = events_map.get(b.event_id) if b.event_id else None
        stake_usdc = round(b.stake / usdc_rate, 2) if usdc_rate > 0 else b.stake
        profit_usdc = round(b.profit / usdc_rate, 2) if usdc_rate > 0 else b.profit
        payout_usdc = round(b.payout / usdc_rate, 2) if usdc_rate > 0 else b.payout

        # Compute edge from fair odds at placement
        edge_pct = None
        if b.fair_odds_at_placement and b.fair_odds_at_placement > 1 and b.odds > 0:
            edge_pct = round((b.odds / b.fair_odds_at_placement - 1) * 100, 2)

        bet_items.append({
            "id": b.id,
            "event_id": b.event_id,
            "market": b.market,
            "outcome": b.outcome,
            "odds": b.odds,
            "stake_sek": b.stake,
            "stake_usdc": stake_usdc,
            "result": b.result,
            "payout_sek": b.payout,
            "payout_usdc": payout_usdc,
            "profit_sek": b.profit,
            "profit_usdc": profit_usdc,
            "placed_at": b.placed_at.isoformat() + "Z" if b.placed_at else None,
            "edge_pct": edge_pct,
            "fair_odds": b.fair_odds_at_placement,
            "settlement_source": b.settlement_source,
            "home_team": event.home_team if event else None,
            "away_team": event.away_team if event else None,
            "display_home": event.display_home if event else None,
            "display_away": event.display_away if event else None,
            "sport": event.sport if event else None,
            "start_time": (event.start_time.isoformat() + "Z") if event and event.start_time else None,
        })

    # Aggregate stats
    all_bets = db.query(Bet).filter(
        Bet.profile_id == profile.id,
        Bet.provider_id == "polymarket",
    ).all()

    settled = [b for b in all_bets if b.result in ("won", "lost", "void")]
    wins = sum(1 for b in settled if b.result == "won")
    losses = sum(1 for b in settled if b.result == "lost")
    voids = sum(1 for b in settled if b.result == "void")
    pending = sum(1 for b in all_bets if b.result == "pending")
    total_staked = sum(b.stake for b in all_bets)
    total_profit = sum(b.profit for b in all_bets)
    total_staked_usdc = round(total_staked / usdc_rate, 2) if usdc_rate > 0 else total_staked
    total_profit_usdc = round(total_profit / usdc_rate, 2) if usdc_rate > 0 else total_profit
    roi_pct = round(total_profit / total_staked * 100, 2) if total_staked > 0 else 0
    win_rate = round(wins / len(settled) * 100, 1) if settled else 0

    # Average edge at placement (computed from fair_odds_at_placement)
    edges = []
    for b in all_bets:
        if b.fair_odds_at_placement and b.fair_odds_at_placement > 1 and b.odds > 0:
            edges.append((b.odds / b.fair_odds_at_placement - 1) * 100)
    avg_edge = round(sum(edges) / len(edges), 2) if edges else 0

    return {
        "bets": bet_items,
        "count": len(bet_items),
        "stats": {
            "total_bets": len(all_bets),
            "pending": pending,
            "wins": wins,
            "losses": losses,
            "voids": voids,
            "win_rate": win_rate,
            "total_staked_sek": round(total_staked, 2),
            "total_staked_usdc": total_staked_usdc,
            "total_profit_sek": round(total_profit, 2),
            "total_profit_usdc": total_profit_usdc,
            "roi_pct": roi_pct,
            "avg_edge": avg_edge,
        },
    }
