"""Polymarket API routes: matched events, value bets, stats, mybets, rewards."""

import contextlib
import json
import logging
import re
import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...analysis.devig import devig_multiplicative
from ...bankroll.stake_calculator import BONUS_MIN_ODDS, OPTIMAL_MAX_KELLY, OPTIMAL_SINGLE_BET_CAP, StakeCalculator
from ...config import get_exchange_rate
from ...constants import SHARP_PROVIDERS
from ...db.models import Bet, Event, Odds
from ...matching.matcher import get_team_match_score
from ...matching.normalizer import generate_canonical_id, normalize_team_name
from ...repositories import ProfileRepo
from ..deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/polymarket", tags=["polymarket"])


@router.get("/value")
def get_polymarket_value(
    min_edge: float | None = Query(None, description="Minimum edge percentage (defaults to profile min_edge_pct)"),
    sport: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get Polymarket value bets from pre-computed opportunities table."""
    from ...repositories import OpportunityRepo

    opp_repo = OpportunityRepo(db)

    # Use profile min_edge if not specified
    profile_repo = ProfileRepo(db)
    profile = None
    with contextlib.suppress(Exception):
        profile = profile_repo.get_active()
    effective_min_edge = min_edge if min_edge is not None else (getattr(profile, "min_edge_pct", 2.0) or 2.0)

    # Read pre-computed opportunities (fast indexed query, no scanning)
    rows = opp_repo.find_active(
        type="value",
        provider_ids=["polymarket"],
        sport=sport,
        min_edge=effective_min_edge,
        limit=limit,
    )

    # Use total bankroll (same as Value page) — Polymarket is just another provider
    stake_calculator = None
    total_bankroll = 0.0
    bonus_status = None
    if rows:
        try:
            if not profile:
                profile = profile_repo.get_active()
            total_bankroll = profile_repo.get_total_bankroll(profile.id)
            stake_calculator = StakeCalculator(
                bankroll=total_bankroll,
                max_kelly=OPTIMAL_MAX_KELLY,
                single_bet_cap_pct=OPTIMAL_SINGLE_BET_CAP,
                min_edge=profile.min_edge_pct / 100.0,
            )
            bonus_status = profile_repo.get_bonus_status(profile.id, "polymarket")
        except Exception as e:
            logger.warning(f"Could not initialize stake calculator: {e}")

    # Batch-load provider_meta + updated_at from Odds for event_slug + poly names
    event_ids = list({opp.event_id for opp, _ in rows})
    odds_meta_map: dict[tuple, dict] = {}
    odds_updated_map: dict[tuple, str] = {}
    poly_names_map: dict[str, tuple[str | None, str | None]] = {}
    if event_ids:
        poly_odds = db.query(Odds).filter(Odds.event_id.in_(event_ids), Odds.provider_id == "polymarket").all()
        for o in poly_odds:
            key = (o.event_id, o.market, o.outcome)
            meta = o.provider_meta if isinstance(o.provider_meta, dict) else {}
            if meta.get("event_slug"):
                odds_meta_map[key] = o.provider_meta
            if o.updated_at:
                odds_updated_map[key] = o.updated_at.isoformat() + "Z"
            if o.event_id not in poly_names_map and (meta.get("poly_home") or meta.get("poly_away")):
                poly_names_map[o.event_id] = (meta.get("poly_home"), meta.get("poly_away"))

    usdc_rate = get_exchange_rate("polymarket")

    # Provider-level extraction recency
    from ...services.opportunity_service import get_provider_last_checked

    last_checked_map = get_provider_last_checked(db, ["polymarket"])
    poly_last_checked = last_checked_map.get("polymarket")

    # Build response from pre-computed opportunities
    results = []
    for opp, event in rows:
        if not event:
            continue

        poly_odds_val = opp.odds1 or 0
        fair_odds_val = opp.odds2 or 0
        price_cents = round(1 / poly_odds_val * 100) if poly_odds_val > 0 else 0
        fair_price_cents = round(1 / fair_odds_val * 100) if fair_odds_val > 0 else 0

        meta = odds_meta_map.get((opp.event_id, opp.market, opp.outcome1), {})
        event_slug = meta.get("event_slug") if isinstance(meta, dict) else None

        result = {
            "event_id": opp.event_id,
            "market": opp.market,
            "outcome": opp.outcome1,
            "polymarket_odds": poly_odds_val,
            "fair_odds": fair_odds_val,
            "fair_probability": round(1 / fair_odds_val, 4) if fair_odds_val > 0 else 0,
            "edge_pct": opp.edge_pct,
            "point": opp.point,
            "home_team": event.home_team,
            "away_team": event.away_team,
            "display_home": event.display_home,
            "display_away": event.display_away,
            "poly_home": poly_names_map.get(opp.event_id, (None, None))[0],
            "poly_away": poly_names_map.get(opp.event_id, (None, None))[1],
            "sport": event.sport,
            "league": event.league,
            "start_time": (event.start_time.isoformat() + "Z") if event.start_time else None,
            "price_cents": price_cents,
            "fair_price_cents": fair_price_cents,
            "exchange_rate_sek": usdc_rate,
            "event_slug": event_slug,
            "provider_meta": {"event_slug": event_slug} if event_slug else None,
            "updated_at": odds_updated_map.get((opp.event_id, opp.market, opp.outcome1)),
            "provider_last_checked": poly_last_checked,
        }

        # Add stake recommendation
        if stake_calculator and profile:
            try:
                # Stored polymarket odds are already net of the 2% fee (applied in
                # polymarket._price_to_odds at extraction); use directly.
                edge_raw = (poly_odds_val / fair_odds_val - 1) if fair_odds_val > 1 else 0
                min_odds = (
                    0.0
                    if (not bonus_status or bonus_status.get("is_cleared", True))
                    else bonus_status.get("min_odds", BONUS_MIN_ODDS)
                )

                stake_rec = stake_calculator.calculate(
                    edge_raw=edge_raw,
                    odds=poly_odds_val,
                    event_id=opp.event_id,
                    provider_id="polymarket",
                    min_odds=min_odds,
                )
                stake_sek = stake_rec.stake
                stake_usdc = round(stake_sek / usdc_rate, 2) if usdc_rate > 0 else 0
                shares = round(stake_usdc / (price_cents / 100), 1) if price_cents > 0 else 0
                payout_usdc = round(shares * 1.0, 2)

                result["suggested_stake"] = round(stake_rec.raw_kelly_stake, 2)
                result["final_stake"] = round(stake_sek, 2)
                result["final_stake_usdc"] = stake_usdc
                result["shares"] = shares
                result["payout_usdc"] = payout_usdc
                result["kelly_fraction"] = stake_rec.kelly_fraction
                result["skip_reason"] = stake_rec.skip_reason
                result["bankroll_needed"] = stake_rec.bankroll_needed if stake_rec.bankroll_needed > 0 else None
                result["bonus_cleared"] = bonus_status.get("is_cleared", True) if bonus_status else True
            except Exception as e:
                logger.debug(f"Stake calculation failed for {opp.event_id}: {e}")
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

    return {
        "value_bets": results,
        "count": len(results),
        "total_scanned": len(results),
        "total_bankroll": round(total_bankroll, 2),
    }


@router.get("/stats")
def get_polymarket_stats(
    db: Session = Depends(get_db),
):
    """Get Polymarket extraction statistics and data quality metrics."""
    # Total Polymarket odds and events
    poly_odds_count = (db.query(func.count(Odds.id)).filter(Odds.provider_id == "polymarket").scalar()) or 0

    poly_event_ids = db.query(Odds.event_id).filter(Odds.provider_id == "polymarket").distinct().subquery()
    poly_event_count = db.query(func.count()).select_from(poly_event_ids).scalar() or 0

    # Matched events (have both Polymarket + at least one other provider)
    matched_count = 0
    if poly_event_count > 0:
        matched_subq = (
            db.query(Odds.event_id)
            .filter(
                Odds.event_id.in_(db.query(Odds.event_id).filter(Odds.provider_id == "polymarket").distinct()),
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
def get_polymarket_matched(
    sport: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get Polymarket events matched with other providers, including Pinnacle fair odds."""

    # Find events that have Polymarket odds
    polymarket_events_subq = db.query(Odds.event_id).filter(Odds.provider_id == "polymarket").distinct().subquery()

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
                        edges.append(
                            {
                                "outcome": outcome,
                                "provider": provider_id,
                                "edge_pct": round(edge_pct, 2),
                                "provider_odds": provider_odd,
                                "polymarket_odds": poly_odd,
                            }
                        )
                        if edge_pct > best_edge:
                            best_edge = edge_pct

        edges.sort(key=lambda x: x["edge_pct"], reverse=True)

        # Calculate Polymarket edges vs Pinnacle fair odds.
        # Stored polymarket odds are already net of the 2% fee; compare directly.
        polymarket_edges = []
        for outcome, poly_odd in poly_odds_lookup.items():
            pinnacle_odd = pinnacle_odds_lookup.get(outcome)
            if pinnacle_odd and pinnacle_odd > 0 and poly_odd > 0:
                edge_pct = (poly_odd / pinnacle_odd - 1) * 100
                polymarket_edges.append(
                    {
                        "outcome": outcome,
                        "polymarket_odds": poly_odd,
                        "pinnacle_odds": pinnacle_odd,
                        "edge_pct": round(edge_pct, 2),
                    }
                )

        polymarket_edges.sort(key=lambda x: x["edge_pct"], reverse=True)

        result.append(
            {
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
            }
        )

    # Sort by best_edge descending
    result.sort(key=lambda x: x["best_edge"], reverse=True)

    return {
        "events": result,
        "count": len(result),
    }


# ──────────────────── Rewards ────────────────────

# Import SERIES_TO_SPORT from polymarket provider for sport detection
from ...providers.polymarket import SERIES_TO_SPORT

# Simple TTL cache for Gamma API reward data
_rewards_cache: dict = {"data": None, "ts": 0}
_REWARDS_CACHE_TTL = 300  # 5 minutes


def _parse_poly_teams(title: str) -> tuple[str, str]:
    """Extract home/away teams from Polymarket event title.

    Mirrors PolymarketRetriever._parse_teams() logic.
    """
    clean = title
    for suffix in [" - More Markets", " - Winner", " (Game 1)", " (Game 2)", " (Game 3)"]:
        if suffix in clean:
            clean = clean.split(suffix)[0]

    # Strip esports/tennis/MMA prefixes
    prefixes = [
        "Counter-Strike: ",
        "CS2: ",
        "League of Legends: ",
        "LoL: ",
        "Valorant: ",
        "Dota 2: ",
        "Call of Duty: ",
        "CoD: ",
        "ATP: ",
        "WTA: ",
        "Men's: ",
        "Women's: ",
    ]
    for pfx in prefixes:
        if clean.startswith(pfx):
            clean = clean[len(pfx) :]
            break

    clean = re.sub(r"^(?:UFC|Bellator|PFL|ONE)(?:\s+[\w\'\-]+)*\s*:\s*", "", clean)
    clean = re.sub(r"\s*\([^)]+\)\s*", "", clean)

    for sep in [" vs. ", " vs ", " @ "]:
        if sep in clean:
            parts = clean.split(sep)
            if len(parts) == 2:
                home = parts[0].strip()
                away = parts[1].strip()
                if " - " in away:
                    away = away.split(" - ")[0].strip()
                return home, away

    return "", ""


def _get_poly_sport_league(item: dict) -> tuple[str, str]:
    """Determine sport+league from Polymarket event series fields."""
    series_list = item.get("series", [])
    series_slug = item.get("seriesSlug", "")
    league = series_list[0].get("title", "Unknown") if series_list else "Unknown"

    sport = SERIES_TO_SPORT.get(series_slug)
    if not sport and "-20" in series_slug:
        base_slug = series_slug.rsplit("-20", 1)[0]
        sport = SERIES_TO_SPORT.get(base_slug)
    return sport or "unknown", league


async def _fetch_gamma_reward_events() -> list[dict]:
    """Fetch sport events from Gamma API with TTL cache.

    Reward fields are directly on each market object:
    - rewardsMaxSpread (float): max cents from midpoint to earn rewards
    - rewardsMinSize (float): min shares for reward eligibility
    - competitive (float 0-1): competition level (lower = less competition = more rewards/dollar)
    Note: rewardsDailyRate is NOT available from Gamma API.
    """
    now = time.time()
    if _rewards_cache["data"] is not None and (now - _rewards_cache["ts"]) < _REWARDS_CACHE_TTL:
        return _rewards_cache["data"]

    import httpx

    base_url = "https://gamma-api.polymarket.com"
    all_events: list[dict] = []
    offset = 0
    page_limit = 500

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.get(
                f"{base_url}/events",
                params={
                    "active": "true",
                    "closed": "false",
                    "tag_id": 100639,
                    "order": "startTime",
                    "ascending": "true",
                    "limit": page_limit,
                    "offset": offset,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            all_events.extend(data)
            if len(data) < page_limit:
                break
            offset += page_limit

    # Filter to events that have at least one market with rewardsMaxSpread > 0
    reward_events = []
    for ev in all_events:
        markets = ev.get("markets", [])
        for m in markets:
            max_spread = float(m.get("rewardsMaxSpread", 0) or 0)
            if max_spread > 0:
                reward_events.append(ev)
                break

    _rewards_cache["data"] = reward_events
    _rewards_cache["ts"] = now
    logger.info(f"[polymarket/rewards] Fetched {len(all_events)} events, {len(reward_events)} have rewards")
    return reward_events


@router.get("/rewards-debug")
def rewards_debug(db: Session = Depends(get_db)):
    """Debug endpoint to test DB queries."""
    pinnacle_event_ids = set(
        row[0] for row in db.query(Odds.event_id).filter(Odds.provider_id == "pinnacle").distinct().all()
    )
    excluded = SHARP_PROVIDERS | {"polymarket"}
    soft_count = (
        (
            db.query(Odds)
            .filter(
                Odds.event_id.in_(pinnacle_event_ids),
                ~Odds.provider_id.in_(excluded),
            )
            .count()
        )
        if pinnacle_event_ids
        else 0
    )
    ducks_soft = (
        db.query(Odds)
        .filter(
            Odds.event_id == "ice_hockey:ducks:blues:20260309",
            ~Odds.provider_id.in_(excluded),
            Odds.market.in_(["1x2", "moneyline"]),
        )
        .all()
    )
    return {
        "pinnacle_events": len(pinnacle_event_ids),
        "soft_odds_count": soft_count,
        "ducks_soft": [
            {"provider": o.provider_id, "market": o.market, "outcome": o.outcome, "odds": o.odds} for o in ducks_soft
        ],
    }


@router.get("/rewards")
async def get_polymarket_rewards(
    min_daily_rate: float = Query(0.0, description="Min total daily reward rate (USDC)"),
    sport: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get Polymarket sport events with liquidity rewards, matched to Pinnacle."""
    import asyncio

    # Run all DB queries in a thread to avoid blocking the event loop
    def _db_queries():
        db_events = db.query(Event).all()
        events_by_id = {e.id: e for e in db_events}

        pinnacle_event_ids = set(
            row[0] for row in db.query(Odds.event_id).filter(Odds.provider_id == "pinnacle").distinct().all()
        )

        pinnacle_odds_rows = (
            (db.query(Odds).filter(Odds.provider_id == "pinnacle", Odds.event_id.in_(pinnacle_event_ids)).all())
            if pinnacle_event_ids
            else []
        )
        pinnacle_odds_map: dict[str, dict[str, dict[str, float]]] = {}
        for o in pinnacle_odds_rows:
            pinnacle_odds_map.setdefault(o.event_id, {}).setdefault(o.market, {})[o.outcome] = o.odds

        excluded = SHARP_PROVIDERS | {"polymarket"}
        soft_odds_rows = (
            (
                db.query(Odds)
                .filter(
                    Odds.event_id.in_(pinnacle_event_ids),
                    ~Odds.provider_id.in_(excluded),
                )
                .all()
            )
            if pinnacle_event_ids
            else []
        )
        best_soft_map: dict[str, dict[str, dict[str, tuple[float, str]]]] = {}
        for o in soft_odds_rows:
            by_market = best_soft_map.setdefault(o.event_id, {}).setdefault(o.market, {})
            current = by_market.get(o.outcome)
            if current is None or o.odds > current[0]:
                by_market[o.outcome] = (o.odds, o.provider_id)

        return events_by_id, pinnacle_event_ids, pinnacle_odds_map, best_soft_map

    events_by_id, pinnacle_event_ids, pinnacle_odds_map, best_soft_map = await asyncio.to_thread(_db_queries)

    # Now fetch Gamma API (async, may take 10-30s on cold cache)
    gamma_events = await _fetch_gamma_reward_events()

    results = []
    for ev in gamma_events:
        title = ev.get("title", "")
        if " - More Markets" in title:
            continue

        home, away = _parse_poly_teams(title)
        if not home or not away:
            continue

        ev_sport, league = _get_poly_sport_league(ev)
        if ev_sport == "unknown":
            continue
        if sport and ev_sport != sport:
            continue

        # Parse start_time
        start_time_raw = ev.get("startTime")
        if isinstance(start_time_raw, (int, float)):
            from datetime import datetime, timezone

            ts = start_time_raw / 1000 if start_time_raw > 1e10 else start_time_raw
            start_time_str = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        else:
            start_time_str = start_time_raw

        # Skip started events
        if start_time_str:
            try:
                from datetime import datetime, timezone

                st = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                if st <= datetime.now(timezone.utc):
                    continue
            except Exception:
                pass

        # Try to match to DB event
        home_norm = normalize_team_name(home)
        away_norm = normalize_team_name(away)

        # Try canonical ID match first (exact)
        matched_event: Event | None = None
        if start_time_str:
            canonical_id = generate_canonical_id(ev_sport, home, away, start_time_str)
            if canonical_id in events_by_id:
                matched_event = events_by_id[canonical_id]
            else:
                # Try swapped teams
                canonical_swapped = generate_canonical_id(ev_sport, away, home, start_time_str)
                if canonical_swapped in events_by_id:
                    matched_event = events_by_id[canonical_swapped]

        # Fuzzy fallback — match against Pinnacle events only
        if not matched_event:
            best_score = 0
            for eid in pinnacle_event_ids:
                db_ev = events_by_id.get(eid)
                if not db_ev or db_ev.sport != ev_sport:
                    continue
                h_score = get_team_match_score(home, db_ev.home_team)
                a_score = get_team_match_score(away, db_ev.away_team)
                combined = min(h_score, a_score)
                if combined > best_score and combined >= 75:
                    best_score = combined
                    matched_event = db_ev
                # Also try swapped
                h_score2 = get_team_match_score(home, db_ev.away_team)
                a_score2 = get_team_match_score(away, db_ev.home_team)
                combined2 = min(h_score2, a_score2)
                if combined2 > best_score and combined2 >= 75:
                    best_score = combined2
                    matched_event = db_ev

        if not matched_event:
            continue
        if matched_event.id not in pinnacle_event_ids:
            continue

        # Extract reward info from markets (fields are directly on market object)
        markets = ev.get("markets", [])
        max_spread = 0.0
        min_size = 0.0
        competitive_val = 1.0  # default high competition
        poly_prices: dict[str, float] = {}

        for m in markets:
            ms = float(m.get("rewardsMaxSpread", 0) or 0)
            if ms > max_spread:
                max_spread = ms
            mn = float(m.get("rewardsMinSize", 0) or 0)
            if mn > min_size:
                min_size = mn
            comp = m.get("competitive")
            if comp is not None:
                competitive_val = float(comp)

            # Extract prices from outcomePrices
            outcome_prices = m.get("outcomePrices")
            question = (m.get("question") or "").lower()
            if outcome_prices:
                try:
                    prices = outcome_prices if isinstance(outcome_prices, list) else json.loads(outcome_prices)
                    group_slug = (m.get("groupItemTitle") or "").lower()
                    if group_slug:
                        # Grouped event (football 1x2): each sub-market = one outcome
                        if "draw" in question or "draw" in group_slug:
                            poly_prices["draw"] = float(prices[0])
                        elif home_norm and (home_norm in question or home_norm in group_slug):
                            poly_prices["home"] = float(prices[0])
                        elif away_norm and (away_norm in question or away_norm in group_slug):
                            poly_prices["away"] = float(prices[0])
                    elif len(prices) >= 2 and len(poly_prices) == 0:
                        # Single market (moneyline): prices[0]=home Yes, prices[1]=away Yes
                        poly_prices["home"] = float(prices[0])
                        poly_prices["away"] = float(prices[1])
                except Exception:
                    pass

        # Get Pinnacle fair odds (de-vigged)
        pinn_market_odds = pinnacle_odds_map.get(matched_event.id, {})
        # Try 1x2 first, then moneyline
        pinn_raw = pinn_market_odds.get("1x2") or pinn_market_odds.get("moneyline", {})
        pinnacle_fair: dict[str, float] = {}
        if pinn_raw:
            outcomes = sorted(pinn_raw.keys())
            odds_list = [pinn_raw[o] for o in outcomes]
            if all(o > 1 for o in odds_list):
                fair_list = devig_multiplicative(odds_list)
                for o, f in zip(outcomes, fair_list, strict=False):
                    pinnacle_fair[o] = round(f, 3)

        # Get best hedge odds (merge 1x2 + moneyline, pick best per outcome)
        soft_market = best_soft_map.get(matched_event.id, {})
        hedge_odds: dict[str, dict] = {}
        for mkt_key in ("1x2", "moneyline"):
            mkt_data = soft_market.get(mkt_key, {})
            for outcome, (odds_val, prov) in mkt_data.items():
                existing = hedge_odds.get(outcome)
                if existing is None or odds_val > existing["odds"]:
                    hedge_odds[outcome] = {"provider": prov, "odds": round(odds_val, 3)}

        event_slug = ev.get("slug", "")

        results.append(
            {
                "event_id": matched_event.id,
                "home_team": matched_event.home_team,
                "away_team": matched_event.away_team,
                "display_home": matched_event.display_home,
                "display_away": matched_event.display_away,
                "poly_home": home,
                "poly_away": away,
                "sport": matched_event.sport,
                "league": matched_event.league or league,
                "start_time": (matched_event.start_time.isoformat() + "Z")
                if matched_event.start_time
                else start_time_str,
                "rewards_daily_rate": 0.0,  # Not available from Gamma API
                "rewards_max_spread": round(max_spread, 1),
                "rewards_min_size": round(min_size, 0),
                "competitive": round(competitive_val, 4),
                "poly_prices": {k: round(v, 4) for k, v in poly_prices.items()},
                "pinnacle_fair_odds": pinnacle_fair,
                "best_hedge_odds": hedge_odds,
                "event_slug": event_slug,
                "polymarket_url": f"https://polymarket.com/event/{event_slug}" if event_slug else None,
            }
        )

    # Sort by competition (lower = less competition = more rewarding)
    results.sort(key=lambda x: x["competitive"])
    results = results[:limit]

    return {
        "rewards": results,
        "count": len(results),
    }


# ──────────────────── My Bets (Polymarket) ────────────────────


@router.get("/mybets")
def get_mybets(
    status: str | None = None,
    exclude_bonus: bool = False,
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
    if exclude_bonus:
        query = query.filter(not Bet.is_bonus)

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

        # Compute edge from fair odds at placement.
        # b.odds is the post-fee stored value; compare directly.
        edge_pct = None
        if b.fair_odds_at_placement and b.fair_odds_at_placement > 1 and b.odds > 0:
            edge_pct = round((b.odds / b.fair_odds_at_placement - 1) * 100, 2)

        bet_items.append(
            {
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
                "clv_pct": b.clv_pct,
                "closing_odds": b.closing_odds,
                "provider_closing_odds": b.provider_closing_odds,
                "provider_clv_pct": b.provider_clv_pct,
                "settlement_source": b.settlement_source,
                "home_team": event.home_team if event else None,
                "away_team": event.away_team if event else None,
                "display_home": event.display_home if event else None,
                "display_away": event.display_away if event else None,
                "sport": event.sport if event else None,
                "start_time": (event.start_time.isoformat() + "Z") if event and event.start_time else None,
            }
        )

    # Aggregate stats (same bonus filter as the list)
    stats_query = db.query(Bet).filter(
        Bet.profile_id == profile.id,
        Bet.provider_id == "polymarket",
    )
    if exclude_bonus:
        stats_query = stats_query.filter(not Bet.is_bonus)
    all_bets = stats_query.all()

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

    # Average edge at placement (b.odds already net of polymarket fee at extraction)
    edges = []
    for b in all_bets:
        if b.fair_odds_at_placement and b.fair_odds_at_placement > 1 and b.odds > 0:
            edges.append((b.odds / b.fair_odds_at_placement - 1) * 100)
    avg_edge = round(sum(edges) / len(edges), 2) if edges else 0

    # Average provider CLV (same-market, Polymarket closing price)
    provider_clvs = [b.provider_clv_pct for b in all_bets if b.provider_clv_pct is not None]
    avg_provider_clv = round(sum(provider_clvs) / len(provider_clvs), 2) if provider_clvs else None

    # Average Pinnacle CLV (cross-market edge)
    pinnacle_clvs = [b.clv_pct for b in all_bets if b.clv_pct is not None]
    avg_pinnacle_clv = round(sum(pinnacle_clvs) / len(pinnacle_clvs), 2) if pinnacle_clvs else None

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
            "avg_provider_clv": avg_provider_clv,
            "avg_pinnacle_clv": avg_pinnacle_clv,
        },
    }
