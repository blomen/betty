"""
EV Enrichment for Odds Boosts

Three-pass enrichment pipeline:
1. Pinnacle direct match: De-vig Pinnacle 1x2/total/spread for pure single-market boosts
2. Combo decomposition: Parse multi-leg combos, price each leg from Pinnacle
3. Consensus pricing: Use soft provider consensus as fallback for remaining boosts

Also provides store_specials_to_db() for persisting enriched specials.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import Event, Odds, SpecialOdds
from .devig import get_fair_odds_for_outcome, compute_consensus_fair_odds
from .combo_decomposition import (
    classify_boost, parse_combo_legs, price_combo_legs,
    DECOMPOSABLE_TYPES,
)
from ..matching.normalizer import normalize_team_name
from ..matching.matcher import get_team_match_score
from ..constants import PLATFORM_MAP

logger = logging.getLogger(__name__)


# ── Boost classification keywords ──────────────────────────────────────

# Keywords that indicate the boost IS on a match-winner selection
MATCH_WINNER_LABELS = {
    "match result", "1x2", "to qualify", "att kvalificera",
    "vinner matchen", "to win", "att vinna", "matchresultat",
}

# Minimum boost percentage to show unmeasurable boosts (no Pinnacle, no consensus)
_UNVERIFIED_MIN_BOOST_PCT = 50


def _fix_encoding(text: str) -> str:
    """Fix double-encoded UTF-8 (e.g., 'mÃ¥lgÃ¶rare' → 'målgörare')."""
    for encoding in ("latin-1", "cp1252"):
        try:
            fixed = text.encode(encoding).decode("utf-8")
            high_orig = sum(1 for c in text if ord(c) > 127)
            high_fixed = sum(1 for c in fixed if ord(c) > 127)
            if high_fixed < high_orig:
                return fixed
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    return text


def deduplicate_specials(specials: list[dict]) -> list[dict]:
    """Merge duplicate boosts across providers into single rows.

    Dedup key: (title, boosted_odds, event) — case-insensitive, stripped.
    All providers from duplicates are collected into provider + shared_providers.
    """
    if not specials:
        return specials

    from collections import defaultdict

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for s in specials:
        key = (
            s.get("title", "").lower().strip(),
            s.get("boosted_odds"),
            s.get("event", "").lower().strip(),
        )
        groups[key].append(s)

    result = []
    for group in groups.values():
        group.sort(key=lambda s: (
            s.get("original_odds") is not None,
            sum(1 for v in s.values() if v is not None and v != ""),
        ), reverse=True)
        best = dict(group[0])

        all_providers: set[str] = set()
        for s in group:
            if s.get("provider"):
                all_providers.add(s["provider"])
            for sp in (s.get("shared_providers") or []):
                if sp:
                    all_providers.add(sp)

        sorted_providers = sorted(all_providers)
        best["provider"] = sorted_providers[0]
        best["shared_providers"] = sorted_providers[1:] if len(sorted_providers) > 1 else []

        result.append(best)

    removed = len(specials) - len(result)
    if removed > 0:
        logger.info(f"Dedup: {len(specials)} → {len(result)} specials ({removed} duplicates merged)")

    return result


# ── Pinnacle data loading ──────────────────────────────────────────────

def _load_pinnacle_data(db: Session, sports: set[str]) -> tuple[dict, dict]:
    """Load all Pinnacle odds (1x2, moneyline, total, spread, enrichment markets).

    Returns:
        pinnacle_markets: {sport: {event_key: {market_type: market_data}}}
        event_info: {event_key: {event_id, home_team, away_team, home_norm, away_norm}}
    """
    # Load all Pinnacle odds for relevant sports
    pinnacle_rows = (
        db.query(Odds, Event)
        .join(Event, Odds.event_id == Event.id)
        .filter(
            Odds.provider_id == "pinnacle",
            Odds.market.in_(["1x2", "moneyline", "total", "spread",
                             "team_total", "1x2_1h", "moneyline_1h", "total_1h"]),
            Event.sport.in_(list(sports)),
        )
    ).all()

    # Build multi-market lookup:
    # {sport: {event_key: {"1x2": {outcome: odds}, "total": {point: {outcome: odds}}, ...}}}
    pinnacle_markets: dict[str, dict[str, dict]] = {}
    event_info: dict[str, dict] = {}

    for odds_row, event_row in pinnacle_rows:
        sport = event_row.sport
        home_norm = normalize_team_name(event_row.home_team).lower() if event_row.home_team else ""
        away_norm = normalize_team_name(event_row.away_team).lower() if event_row.away_team else ""
        event_key = f"{home_norm}_vs_{away_norm}"

        if sport not in pinnacle_markets:
            pinnacle_markets[sport] = {}
        if event_key not in pinnacle_markets[sport]:
            pinnacle_markets[sport][event_key] = {}
            event_info[event_key] = {
                "event_id": event_row.id,
                "home_team": event_row.home_team or "",
                "away_team": event_row.away_team or "",
                "home_norm": home_norm,
                "away_norm": away_norm,
            }

        mkt = pinnacle_markets[sport][event_key]
        market = odds_row.market

        # Point-based markets (total, spread, team_total, 1h variants)
        if market in ("total", "spread", "team_total", "total_1h"):
            point = odds_row.point
            if point is not None:
                if market not in mkt:
                    mkt[market] = {}
                if point not in mkt[market]:
                    mkt[market][point] = {}
                mkt[market][point][odds_row.outcome] = odds_row.odds
        else:
            # Simple markets (1x2, moneyline, 1x2_1h, moneyline_1h)
            if market not in mkt:
                mkt[market] = {}
            mkt[market][odds_row.outcome] = odds_row.odds

    return pinnacle_markets, event_info


def _load_soft_odds_for_event(db: Session, event_id: str) -> dict[str, dict[str, list[dict]]]:
    """Load all soft provider odds for an event, grouped by market+outcome.

    Returns: {market_key: {outcome: [{provider, odds}, ...]}}
    where market_key is "1x2", "moneyline", "total_2.5", "spread_-1.5", etc.
    """
    rows = (
        db.query(Odds)
        .filter(
            Odds.event_id == event_id,
            Odds.provider_id != "pinnacle",
            Odds.provider_id != "polymarket",
        )
        .all()
    )

    grouped: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        market_key = row.market if row.point is None else f"{row.market}_{row.point}"
        if market_key not in grouped:
            grouped[market_key] = {}
        if row.outcome not in grouped[market_key]:
            grouped[market_key][row.outcome] = []
        grouped[market_key][row.outcome].append({
            "provider": row.provider_id,
            "odds": row.odds,
        })

    return grouped


def _find_pinnacle_event(
    sport: str,
    home_norm: str,
    away_norm: str,
    pinnacle_markets: dict,
    event_info: dict,
) -> tuple[Optional[dict], Optional[str]]:
    """Find a Pinnacle event using exact match then fuzzy fallback.

    Returns:
        (pinnacle_event_data, event_key) or (None, None)
    """
    sport_markets = pinnacle_markets.get(sport, {})
    if not sport_markets:
        return None, None

    # Exact match (fast path)
    event_key = f"{home_norm}_vs_{away_norm}"
    if event_key in sport_markets:
        return sport_markets[event_key], event_key

    # Swapped exact match
    swapped_key = f"{away_norm}_vs_{home_norm}"
    if swapped_key in sport_markets:
        return sport_markets[swapped_key], swapped_key

    # Fuzzy fallback — iterate Pinnacle events for this sport
    best_key = None
    best_avg = 0.0

    for pin_key, pin_info in event_info.items():
        if pin_key not in sport_markets:
            continue

        pin_home = pin_info.get("home_norm", "")
        pin_away = pin_info.get("away_norm", "")

        # Try direct orientation
        score_h = get_team_match_score(home_norm, pin_home)
        score_a = get_team_match_score(away_norm, pin_away)
        avg = (score_h + score_a) / 2

        # Try swapped orientation
        score_h_swap = get_team_match_score(home_norm, pin_away)
        score_a_swap = get_team_match_score(away_norm, pin_home)
        avg_swap = (score_h_swap + score_a_swap) / 2

        # Take best orientation
        if avg_swap > avg:
            avg = avg_swap
            score_h, score_a = score_h_swap, score_a_swap

        # Require: avg >= 85, both individual >= 80, no asymmetry
        if avg >= 85 and min(score_h, score_a) >= 80:
            if abs(score_h - score_a) < 25 or min(score_h, score_a) >= 85:
                if avg > best_avg:
                    best_avg = avg
                    best_key = pin_key

    if best_key:
        return sport_markets[best_key], best_key

    return None, None


def _parse_boost_teams(event_name: str) -> Optional[tuple[str, str]]:
    """Parse home/away team names from boost event string."""
    event_name = _fix_encoding(event_name)
    for sep in [" vs ", " - ", " v "]:
        if sep in event_name:
            parts = event_name.split(sep, 1)
            if len(parts) == 2:
                return parts[0].strip(), parts[1].strip()
    return None


def _set_enrichment(special: dict, edge_pct: float, fair_odds: float,
                    boosted_odds: float, method: str,
                    outcome: Optional[str] = None,
                    event_key: Optional[str] = None,
                    event_info: Optional[dict] = None,
                    market: Optional[str] = None):
    """Apply enrichment fields to a special dict."""
    special["edge_pct"] = edge_pct
    special["fair_odds"] = round(fair_odds, 3)
    special["ev_per_unit"] = round(boosted_odds * (1.0 / fair_odds) - 1, 4)
    special["is_positive_ev"] = edge_pct > 0
    special["enrichment_method"] = method
    if outcome:
        special["matched_outcome"] = outcome
    if market:
        special["matched_market"] = market
    if event_key and event_info:
        info = event_info.get(event_key, {})
        special["matched_event_id"] = info.get("event_id")


# ── Point value parsing ────────────────────────────────────────────────

import re
_POINT_RE = re.compile(r'(\d+[,.]?\d*)', re.IGNORECASE)


def _extract_point_from_title(title: str) -> Optional[float]:
    """Extract a point/line value from a boost title (e.g., 'över 2.5 mål' → 2.5)."""
    match = _POINT_RE.search(title)
    if match:
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            pass
    return None


# ── Main enrichment function ───────────────────────────────────────────

def enrich_specials_with_ev(specials: list[dict], db: Session) -> list[dict]:
    """
    Three-pass enrichment pipeline for specials/boosts.

    Pass 1: Pinnacle direct match (1x2, total, spread)
    Pass 2: Combo decomposition (multi-leg boosts)
    Pass 3: Consensus pricing from soft providers (fallback)
    """
    if not specials:
        return specials

    sports = {s.get("sport") for s in specials if s.get("sport") and s.get("sport") != "unknown"}
    if not sports:
        return specials

    # Load all Pinnacle data
    pinnacle_markets, event_info = _load_pinnacle_data(db, sports)

    pinnacle_count = 0
    combo_count = 0

    # ══════════════════════════════════════════════════════════════════
    # PASS 1: Pinnacle direct match (pure 1x2, total, spread boosts)
    # ══════════════════════════════════════════════════════════════════

    for special in specials:
        boosted_odds = special.get("boosted_odds")
        event_name = special.get("event", "")
        sport = special.get("sport", "unknown")

        if not boosted_odds or not event_name or sport == "unknown":
            continue

        market_label = _fix_encoding(special.get("market_label", ""))
        title = _fix_encoding(special.get("title", ""))

        # Classify this boost
        boost_type = classify_boost(market_label, title)

        # Only process pure single-market types in this pass
        if boost_type not in ("pure_1x2", "pure_total", "pure_spread"):
            continue

        # Parse teams
        teams = _parse_boost_teams(event_name)
        if not teams:
            continue
        home_norm = normalize_team_name(teams[0]).lower()
        away_norm = normalize_team_name(teams[1]).lower()

        # Find Pinnacle event
        pin_data, pin_key = _find_pinnacle_event(
            sport, home_norm, away_norm, pinnacle_markets, event_info
        )
        if not pin_data:
            continue

        # Always record matched event (even if market pricing fails, for consensus fallback)
        if pin_key and event_info:
            info = event_info.get(pin_key, {})
            if info.get("event_id"):
                special["matched_event_id"] = info["event_id"]

        title_lower = title.lower()
        original_odds = special.get("original_odds")

        # ── Pure 1x2 ──
        if boost_type == "pure_1x2":
            pin_market = pin_data.get("1x2") or pin_data.get("moneyline")
            if not pin_market or len(pin_market) < 2:
                continue

            best_outcome = _infer_outcome(
                original_odds, pin_market, title_lower,
                home_norm, away_norm
            )
            if not best_outcome or best_outcome not in pin_market:
                continue

            fair_odds = get_fair_odds_for_outcome(best_outcome, pin_market, method="multiplicative")
            if not fair_odds or fair_odds <= 1.0:
                continue

            edge_pct = round((boosted_odds / fair_odds - 1) * 100, 2)
            if edge_pct > 100:
                continue

            # Sanity: original_odds vs fair_odds ratio
            if original_odds and fair_odds:
                ratio = original_odds / fair_odds
                if ratio > 1.6 or ratio < 0.5:
                    continue

            _set_enrichment(special, edge_pct, fair_odds, boosted_odds,
                            "pinnacle_1x2", best_outcome, pin_key, event_info,
                            market="1x2")
            pinnacle_count += 1

        # ── Pure total ──
        elif boost_type == "pure_total":
            totals = pin_data.get("total", {})
            if not totals:
                continue

            # Extract point from title
            point = _extract_point_from_title(title)
            if point is None:
                continue

            point_market = totals.get(point)
            if not point_market or len(point_market) < 2:
                continue

            # Determine over/under from title
            if any(kw in title_lower for kw in ("över", "over")):
                outcome = "over"
            elif "under" in title_lower:
                outcome = "under"
            else:
                continue

            fair_odds = get_fair_odds_for_outcome(outcome, point_market, method="multiplicative")
            if not fair_odds or fair_odds <= 1.0:
                continue

            edge_pct = round((boosted_odds / fair_odds - 1) * 100, 2)
            if edge_pct > 100:
                continue

            _set_enrichment(special, edge_pct, fair_odds, boosted_odds,
                            "pinnacle_total", outcome, pin_key, event_info,
                            market="total")
            pinnacle_count += 1

        # ── Pure spread ──
        elif boost_type == "pure_spread":
            spreads = pin_data.get("spread", {})
            if not spreads:
                continue

            point = _extract_point_from_title(title)
            if point is None:
                continue

            # Try both signs for spread point
            for try_point in (point, -point):
                point_market = spreads.get(try_point)
                if point_market and len(point_market) >= 2:
                    outcome = _infer_outcome(
                        original_odds, point_market, title_lower,
                        home_norm, away_norm
                    )
                    if outcome and outcome in point_market:
                        fair_odds = get_fair_odds_for_outcome(
                            outcome, point_market, method="multiplicative"
                        )
                        if fair_odds and fair_odds > 1.0:
                            edge_pct = round((boosted_odds / fair_odds - 1) * 100, 2)
                            if edge_pct <= 100:
                                _set_enrichment(
                                    special, edge_pct, fair_odds, boosted_odds,
                                    "pinnacle_spread", outcome, pin_key, event_info,
                                    market="spread"
                                )
                                pinnacle_count += 1
                                break

    logger.info(f"EV enrichment pass 1: {pinnacle_count}/{len(specials)} matched to Pinnacle")

    # ══════════════════════════════════════════════════════════════════
    # PASS 2: Combo decomposition
    # ══════════════════════════════════════════════════════════════════

    for special in specials:
        if special.get("edge_pct") is not None:
            continue  # Already enriched

        boosted_odds = special.get("boosted_odds")
        event_name = special.get("event", "")
        sport = special.get("sport", "unknown")

        if not boosted_odds or not event_name or sport == "unknown":
            continue

        market_label = _fix_encoding(special.get("market_label", ""))
        title = _fix_encoding(special.get("title", ""))

        boost_type = classify_boost(market_label, title)
        if boost_type not in DECOMPOSABLE_TYPES:
            continue

        # Parse teams
        teams = _parse_boost_teams(event_name)
        if not teams:
            continue
        home_norm = normalize_team_name(teams[0]).lower()
        away_norm = normalize_team_name(teams[1]).lower()

        # Find Pinnacle event
        pin_data, pin_key = _find_pinnacle_event(
            sport, home_norm, away_norm, pinnacle_markets, event_info
        )
        if not pin_data:
            continue

        # Always record matched event for consensus fallback
        if pin_key and event_info:
            info = event_info.get(pin_key, {})
            if info.get("event_id"):
                special["matched_event_id"] = info["event_id"]

        # Parse legs
        legs = parse_combo_legs(market_label, title, event_name)
        if not legs or len(legs) < 2:
            continue

        # Price the combo
        fair_odds, method = price_combo_legs(legs, pin_data, margin_fallback=True)
        if not fair_odds or fair_odds <= 1.0:
            continue

        edge_pct = round((boosted_odds / fair_odds - 1) * 100, 2)

        # Sanity: edge > 100% means bad match or parlay pricing error
        if edge_pct > 100:
            continue

        # Sanity: original_odds vs fair_odds ratio — combo pricing uses
        # BTTS/total proxies that can be wildly off; reject extreme ratios
        original_odds = special.get("original_odds")
        if original_odds and fair_odds:
            ratio = original_odds / fair_odds
            if ratio > 1.6 or ratio < 0.35:
                logger.debug(
                    f"Skipping combo '{special.get('title', '')[:50]}': "
                    f"orig/fair ratio={ratio:.2f} (orig={original_odds:.2f} fair={fair_odds:.2f})"
                )
                continue

        _set_enrichment(special, edge_pct, fair_odds, boosted_odds,
                        method, None, pin_key, event_info,
                        market="combo")
        combo_count += 1

    logger.info(f"EV enrichment pass 2: {combo_count} specials priced via combo decomposition")

    # ══════════════════════════════════════════════════════════════════
    # PASS 3: Consensus pricing from soft providers
    # ══════════════════════════════════════════════════════════════════

    consensus_count = 0
    unverified_count = 0

    # Cache soft odds per event to avoid repeated DB queries
    _soft_odds_cache: dict[str, dict] = {}

    for special in specials:
        if special.get("edge_pct") is not None:
            continue  # Already enriched by Pass 1 or 2

        boosted_odds = special.get("boosted_odds")
        if not boosted_odds:
            continue

        matched_event_id = special.get("matched_event_id")
        if not matched_event_id:
            # No event match — mark as unverified if huge boost
            boost_pct = special.get("boost_pct") or 0
            if boost_pct >= _UNVERIFIED_MIN_BOOST_PCT:
                special["enrichment_method"] = "unverified"
                unverified_count += 1
            continue

        # Classify boost type — consensus only works for markets we extract
        market_label = _fix_encoding(special.get("market_label", ""))
        title = _fix_encoding(special.get("title", ""))
        title_lower = title.lower()
        boost_type = classify_boost(market_label, title)

        # Load soft odds (cached per event)
        if matched_event_id not in _soft_odds_cache:
            _soft_odds_cache[matched_event_id] = _load_soft_odds_for_event(db, matched_event_id)
        soft_odds = _soft_odds_cache[matched_event_id]

        if not soft_odds:
            boost_pct = special.get("boost_pct") or 0
            if boost_pct >= _UNVERIFIED_MIN_BOOST_PCT:
                special["enrichment_method"] = "unverified"
                unverified_count += 1
            continue

        # Determine which market to look up for consensus
        candidate_market_key = None
        if boost_type == "pure_1x2":
            # Try 1x2 then moneyline
            for mk in ("1x2", "moneyline"):
                if mk in soft_odds:
                    candidate_market_key = mk
                    break
        elif boost_type == "pure_total":
            point = _extract_point_from_title(title)
            if point is not None:
                candidate_market_key = f"total_{point}"
        elif boost_type == "pure_spread":
            point = _extract_point_from_title(title)
            if point is not None:
                # Try both signs
                for try_key in (f"spread_{point}", f"spread_{-point}"):
                    if try_key in soft_odds:
                        candidate_market_key = try_key
                        break
        # Combo, prop, unknown — can't do consensus

        if not candidate_market_key or candidate_market_key not in soft_odds:
            boost_pct = special.get("boost_pct") or 0
            if boost_pct >= _UNVERIFIED_MIN_BOOST_PCT:
                special["enrichment_method"] = "unverified"
                unverified_count += 1
            continue

        odds_by_outcome = soft_odds[candidate_market_key]

        # Infer which outcome this boost targets
        # Build a flat odds dict for _infer_outcome (avg odds per outcome)
        avg_odds: dict[str, float] = {}
        for out, provs in odds_by_outcome.items():
            if provs:
                avg_odds[out] = sum(p["odds"] for p in provs) / len(provs)

        original_odds = special.get("original_odds")
        event_name = special.get("event", "")
        teams = _parse_boost_teams(event_name)
        home_norm = normalize_team_name(teams[0]).lower() if teams else ""
        away_norm = normalize_team_name(teams[1]).lower() if teams else ""

        # For totals, infer over/under from title
        if boost_type == "pure_total":
            if any(kw in title_lower for kw in ("över", "over")):
                outcome = "over"
            elif "under" in title_lower:
                outcome = "under"
            else:
                outcome = None
        else:
            outcome = _infer_outcome(original_odds, avg_odds, title_lower, home_norm, away_norm)

        if not outcome or outcome not in odds_by_outcome:
            boost_pct = special.get("boost_pct") or 0
            if boost_pct >= _UNVERIFIED_MIN_BOOST_PCT:
                special["enrichment_method"] = "unverified"
                unverified_count += 1
            continue

        # Compute consensus fair odds (min_platforms=3 for boost fallback)
        consensus_result = compute_consensus_fair_odds(
            outcome, odds_by_outcome, PLATFORM_MAP, min_platforms=3,
        )
        if not consensus_result:
            boost_pct = special.get("boost_pct") or 0
            if boost_pct >= _UNVERIFIED_MIN_BOOST_PCT:
                special["enrichment_method"] = "unverified"
                unverified_count += 1
            continue

        fair_odds, n_platforms = consensus_result
        edge_pct = round((boosted_odds / fair_odds - 1) * 100, 2)

        # Sanity checks
        if edge_pct > 100:
            continue
        if original_odds and fair_odds:
            ratio = original_odds / fair_odds
            if ratio > 1.6 or ratio < 0.5:
                continue

        _set_enrichment(special, edge_pct, fair_odds, boosted_odds,
                        "consensus", outcome, market=candidate_market_key.split("_")[0])
        special["matched_event_id"] = matched_event_id
        consensus_count += 1

    logger.info(
        f"EV enrichment pass 3: {consensus_count} specials priced via consensus, "
        f"{unverified_count} marked unverified"
    )
    logger.info(
        f"EV enrichment total: pinnacle={pinnacle_count} combo={combo_count} "
        f"consensus={consensus_count} unverified={unverified_count} "
        f"none={len(specials) - pinnacle_count - combo_count - consensus_count - unverified_count}"
    )

    return specials


# ── Outcome inference ──────────────────────────────────────────────────

def _infer_outcome(
    original_odds: Optional[float],
    pin_market: dict[str, float],
    title_lower: str,
    home_norm: str,
    away_norm: str,
) -> Optional[str]:
    """Infer which outcome a boost is on.

    Strategy:
    1. If original_odds available, find closest Pinnacle outcome by odds proximity
    2. Otherwise, check title for team names, draw keywords, W1/W2 patterns
    """
    if original_odds:
        best_outcome = None
        best_diff = float("inf")
        for outcome, pin_odds in pin_market.items():
            diff = abs(pin_odds - original_odds)
            if diff < best_diff:
                best_diff = diff
                best_outcome = outcome

        if not best_outcome or best_diff > 1.5:
            return None
        return best_outcome

    # No original_odds — infer from title
    home_in_title = home_norm and home_norm in title_lower
    away_in_title = away_norm and away_norm in title_lower

    if home_in_title and not away_in_title:
        return "home"
    if away_in_title and not home_in_title:
        return "away"
    if "draw" in title_lower or "oavgjort" in title_lower:
        return "draw"

    # W1/W2 format (VBet)
    if any(p in title_lower for p in ("w1", ": 1", "matchresultat: 1")):
        return "home"
    if any(p in title_lower for p in ("w2", ": 2", "matchresultat: 2")):
        return "away"

    return None


# ── Expiry filter ──────────────────────────────────────────────────────

def filter_expired(specials: list[dict]) -> list[dict]:
    """Remove specials whose expires_at is in the past or event has already started."""
    now = datetime.now(timezone.utc)
    result = []
    for s in specials:
        event_time = s.get("event_time")
        if event_time:
            try:
                et = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                if et.tzinfo is None:
                    et = et.replace(tzinfo=timezone.utc)
                if et <= now:
                    continue
            except (ValueError, TypeError):
                pass

        exp = s.get("expires_at")
        if not exp:
            result.append(s)
            continue
        try:
            dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt > now:
                result.append(s)
        except (ValueError, TypeError):
            result.append(s)
    return result


# ── DB storage ─────────────────────────────────────────────────────────

def store_specials_to_db(specials: list[dict], session: Session) -> int:
    """Full-replace specials in DB: delete all existing, insert new."""
    if not specials:
        logger.warning("store_specials_to_db called with empty list — skipping to preserve existing data")
        return 0
    session.query(SpecialOdds).delete()

    count = 0
    for s in specials:
        row = SpecialOdds(
            provider=s.get("provider", ""),
            title=s.get("title", ""),
            description=s.get("description", ""),
            original_odds=s.get("original_odds"),
            boosted_odds=s.get("boosted_odds"),
            boost_pct=s.get("boost_pct"),
            max_stake=s.get("max_stake"),
            category=s.get("category", "boost"),
            sport=s.get("sport", "unknown"),
            league=s.get("league", ""),
            event=s.get("event", ""),
            event_time=s.get("event_time"),
            expires_at=s.get("expires_at"),
            url=s.get("url", ""),
            source=s.get("source", ""),
            market_label=s.get("market_label", ""),
            shared_providers=s.get("shared_providers"),
            scraped_at=s.get("scraped_at", ""),
            # EV fields
            edge_pct=s.get("edge_pct"),
            fair_odds=s.get("fair_odds"),
            ev_per_unit=s.get("ev_per_unit"),
            is_positive_ev=s.get("is_positive_ev"),
            matched_event_id=s.get("matched_event_id"),
            matched_outcome=s.get("matched_outcome"),
            matched_market=s.get("matched_market"),
            enrichment_method=s.get("enrichment_method"),
        )
        session.add(row)
        count += 1

    session.commit()
    logger.info(f"Stored {count} specials to DB")
    return count
