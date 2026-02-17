"""
EV Enrichment for Odds Boosts

Matches specials/boosts against Pinnacle fair odds and computes edge.
Used by both the scheduler (at scrape time) and the API (fallback).

Also provides store_specials_to_db() for persisting enriched specials.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import Event, Odds, SpecialOdds
from .devig import get_fair_odds_for_outcome
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


# Keywords indicating combo/prop/non-1x2 markets that can't be compared to match winner.
# Only boosts on the simple match winner (1x2/moneyline) can be EV-analyzed vs Pinnacle.
PROP_KEYWORDS = {
    # Player/team scoring props
    "målgörare", "goalscorer", "first goal", "första mål",
    "gör mål", "scores", "to score",
    "assist", "rebound", "poäng", "points",
    "skott", "shot",
    # Combo/multi-leg markers
    "båda lagen", "both teams", "btts",
    "resultat +", "result +",
    " & ",  # Combo indicator: "1x2 & BTTS"
    # Game props — different market type
    "kort", "card", "hörna", "corner",
    "tidpunkt", "time of",
    # Over/under, totals, handicaps — different market from 1x2
    "antal mål", "antal", "over", "under", "över",
    "halvtid", "fulltid", "halftime", "fulltime",
    "1:a halvlek", "first half", "halvlek",
    "handikapp", "handicap",
    "rätt resultat", "correct score",
    "båda halvlekarna", "both halves",
    "period med", "period with",
    # Clean sheet / specific player/team stats
    "nollan", "clean sheet", "håller nollan",
    "spelarens", "player",
}

# Keywords that indicate the boost IS on a match-winner selection (keep these)
MATCH_WINNER_LABELS = {
    "match result", "1x2", "to qualify", "att kvalificera",
    "vinner matchen", "to win", "att vinna",
}


def _fix_encoding(text: str) -> str:
    """Fix double-encoded UTF-8 (e.g., 'mÃ¥lgÃ¶rare' → 'målgörare').

    Tries latin-1 → utf-8 roundtrip. Only uses fixed version if it
    actually reduces the number of high-codepoint characters.
    Also handles Windows cp1252 double-encoding.
    """
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


def enrich_specials_with_ev(specials: list[dict], db: Session) -> list[dict]:
    """
    Enrich specials with edge_pct vs Pinnacle fair odds.

    For each boost, try to find the matching Pinnacle event and calculate:
      edge_pct = (boosted_odds / fair_odds - 1) * 100

    This tells the user whether a boost is actually +EV vs the sharp line,
    not just "boosted" relative to the provider's own original odds.
    """
    if not specials:
        return specials

    # Collect unique sports from specials for batch DB query
    sports = {s.get("sport") for s in specials if s.get("sport") and s.get("sport") != "unknown"}
    if not sports:
        return specials

    # Load all Pinnacle odds for relevant sports in one query
    pinnacle_odds_query = (
        db.query(Odds, Event)
        .join(Event, Odds.event_id == Event.id)
        .filter(
            Odds.provider_id == "pinnacle",
            Odds.market.in_(["1x2", "moneyline"]),
            Event.sport.in_(list(sports)),
        )
    )
    pinnacle_rows = pinnacle_odds_query.all()

    # Build lookup: {sport: {normalized_event_key: {outcome: odds}}}
    # event_key = normalized "home_vs_away"
    pinnacle_markets: dict[str, dict[str, dict[str, float]]] = {}
    event_info: dict[str, dict] = {}  # event_key -> {event_id, market}

    for odds_row, event_row in pinnacle_rows:
        sport = event_row.sport
        home_norm = normalize_team_name(event_row.home_team).lower() if event_row.home_team else ""
        away_norm = normalize_team_name(event_row.away_team).lower() if event_row.away_team else ""
        event_key = f"{home_norm}_vs_{away_norm}"

        if sport not in pinnacle_markets:
            pinnacle_markets[sport] = {}
        if event_key not in pinnacle_markets[sport]:
            pinnacle_markets[sport][event_key] = {}
            event_info[event_key] = {"event_id": event_row.id, "market": odds_row.market}

        pinnacle_markets[sport][event_key][odds_row.outcome] = odds_row.odds

    enriched_count = 0

    # Enrich each special
    for special in specials:
        boosted_odds = special.get("boosted_odds")
        event_name = _fix_encoding(special.get("event", ""))
        sport = special.get("sport", "unknown")

        if not boosted_odds or not event_name or sport == "unknown":
            continue

        # Skip combo/prop boosts — these can't be compared to 1x2/moneyline
        title_lower = _fix_encoding(
            special.get("title", "") + " " + special.get("market_label", "")
        ).lower()

        # Allow through ONLY if market label is PURELY about match winner
        # (not a combo like "1x2 & BTTS")
        market_label_lower = _fix_encoding(special.get("market_label", "")).lower()
        is_match_winner = (
            any(mw in market_label_lower for mw in MATCH_WINNER_LABELS)
            and " & " not in market_label_lower
            and ", " not in market_label_lower  # Comma-separated combos like "1x2, BTTS"
            and not any(kw in market_label_lower for kw in PROP_KEYWORDS)
        )

        if not is_match_winner and any(kw in title_lower for kw in PROP_KEYWORDS):
            continue

        # Parse event name to get teams
        parts = None
        for sep in [" vs ", " - ", " v "]:
            if sep in event_name:
                parts = event_name.split(sep, 1)
                break

        if not parts or len(parts) != 2:
            continue

        home_norm = normalize_team_name(parts[0].strip()).lower()
        away_norm = normalize_team_name(parts[1].strip()).lower()
        event_key = f"{home_norm}_vs_{away_norm}"

        # Look up Pinnacle market for this event
        sport_markets = pinnacle_markets.get(sport, {})
        pin_market = sport_markets.get(event_key)

        # Try swapped order if not found
        if not pin_market:
            swapped_key = f"{away_norm}_vs_{home_norm}"
            pin_market = sport_markets.get(swapped_key)
            if pin_market:
                event_key = swapped_key

        if not pin_market or len(pin_market) < 2:
            continue

        # The boost is on a specific selection — figure out which outcome.
        # Use original_odds if available; otherwise use Pinnacle odds + title hints.
        original_odds = special.get("original_odds")

        best_outcome = None
        best_diff = float("inf")

        if original_odds:
            # Find the outcome whose Pinnacle odds are closest to original_odds
            for outcome, pin_odds in pin_market.items():
                diff = abs(pin_odds - original_odds)
                if diff < best_diff:
                    best_diff = diff
                    best_outcome = outcome

            if not best_outcome or best_diff > 1.5:
                continue
        else:
            # No original_odds (Kambi, VBet, ComeOn) — infer outcome from title.
            # Check if title contains home or away team name.
            home_in_title = home_norm and home_norm in title_lower
            away_in_title = away_norm and away_norm in title_lower

            if home_in_title and not away_in_title:
                best_outcome = "home"
            elif away_in_title and not home_in_title:
                best_outcome = "away"
            elif "draw" in title_lower or "oavgjort" in title_lower:
                best_outcome = "draw"
            else:
                # Can't determine which outcome — skip
                continue

            if best_outcome not in pin_market:
                continue

        # De-vig to get fair odds
        fair_odds = get_fair_odds_for_outcome(best_outcome, pin_market, method="multiplicative")
        if not fair_odds or fair_odds <= 1.0:
            continue

        # Calculate edge vs fair line
        edge_pct = round((boosted_odds / fair_odds - 1) * 100, 2)

        # Sanity check: edge > 100% almost certainly means wrong match
        if edge_pct > 100:
            continue

        ev_per_unit = round(boosted_odds * (1.0 / fair_odds) - 1, 4)

        special["edge_pct"] = edge_pct
        special["fair_odds"] = round(fair_odds, 3)
        special["ev_per_unit"] = ev_per_unit
        special["is_positive_ev"] = edge_pct > 0
        special["matched_outcome"] = best_outcome
        info = event_info.get(event_key, {})
        special["matched_event_id"] = info.get("event_id")
        special["matched_market"] = info.get("market")
        enriched_count += 1

    logger.info(f"EV enrichment: {enriched_count}/{len(specials)} specials matched to Pinnacle")
    return specials


def filter_expired(specials: list[dict]) -> list[dict]:
    """Remove specials whose expires_at is in the past or event has already started."""
    now = datetime.now(timezone.utc)
    result = []
    for s in specials:
        # Filter out events that have already started (live/in-play)
        event_time = s.get("event_time")
        if event_time:
            try:
                et = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                if et.tzinfo is None:
                    et = et.replace(tzinfo=timezone.utc)
                if et <= now:
                    continue  # Event already kicked off
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


def store_specials_to_db(specials: list[dict], session: Session) -> int:
    """
    Full-replace specials in DB: delete all existing, insert new.

    Args:
        specials: List of special dicts (already enriched with EV fields).
        session: SQLAlchemy session.

    Returns:
        Number of specials stored.
    """
    # Delete all existing specials (same as JSON overwrite behavior)
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
            # EV fields (may be None if not matched)
            edge_pct=s.get("edge_pct"),
            fair_odds=s.get("fair_odds"),
            ev_per_unit=s.get("ev_per_unit"),
            is_positive_ev=s.get("is_positive_ev"),
            matched_event_id=s.get("matched_event_id"),
            matched_outcome=s.get("matched_outcome"),
            matched_market=s.get("matched_market"),
        )
        session.add(row)
        count += 1

    session.commit()
    logger.info(f"Stored {count} specials to DB")
    return count
