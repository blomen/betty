"""
EV Enrichment for Odds Boosts

Simple boost-percentage edge: edge = (boosted_odds / original_odds - 1) * 100
LLM-based probability research runs separately (see llm_enrichment.py).

Also provides deduplicate_specials(), filter_expired(), store_specials_to_db().
"""

import logging
from datetime import UTC, datetime

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from ..db.models import Event, Odds, SpecialOdds
from ..matching.normalizer import normalize_team_name
from .devig import get_fair_odds_for_outcome

logger = logging.getLogger(__name__)


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
        group.sort(
            key=lambda s: (
                s.get("original_odds") is not None,
                sum(1 for v in s.values() if v is not None and v != ""),
            ),
            reverse=True,
        )
        best = dict(group[0])

        all_providers: set[str] = set()
        for s in group:
            if s.get("provider"):
                all_providers.add(s["provider"])
            for sp in s.get("shared_providers") or []:
                if sp:
                    all_providers.add(sp)

        sorted_providers = sorted(all_providers)
        best["provider"] = sorted_providers[0]
        best["shared_providers"] = sorted_providers[1:] if len(sorted_providers) > 1 else []

        result.append(best)

    # Second pass: remove same-provider boosts with identical (title, original_odds, boosted_odds)
    # but different events — these are the same boost returned by multiple API event responses.
    seen_content: set[tuple] = set()
    deduped: list[dict] = []
    for s in result:
        content_key = (
            s.get("provider", "").lower(),
            s.get("title", "").lower().strip(),
            s.get("original_odds"),
            s.get("boosted_odds"),
        )
        if content_key in seen_content:
            continue
        seen_content.add(content_key)
        deduped.append(s)

    removed = len(specials) - len(deduped)
    if removed > 0:
        logger.info(f"Dedup: {len(specials)} → {len(deduped)} specials ({removed} duplicates merged)")

    return deduped


def _parse_boost_teams(event_name: str) -> tuple[str, str] | None:
    """Extract two team names from a boost event string like 'Arsenal vs Sunderland'."""
    if not event_name:
        return None
    for sep in (" vs ", " - ", " mot "):
        if sep in event_name:
            parts = event_name.split(sep, 1)
            if len(parts) == 2:
                return parts[0].strip(), parts[1].strip()
    return None


def _match_boosts_to_events(specials: list[dict], db: Session) -> int:
    """Cross-reference boost event names against Events table.

    Sets matched_event_id and corrects event_time from the authoritative Event.start_time.
    Returns count of matched boosts.
    """
    now = datetime.now(UTC)

    # Load upcoming events (+ events from last 2 hours to catch just-started ones)
    events = db.query(Event).filter(Event.start_time > now.replace(hour=0, minute=0, second=0)).all()
    if not events:
        return 0

    # Build lookup: normalized "home|away" -> (event_id, start_time_iso)
    event_lookup: dict[str, tuple[str, str]] = {}
    for ev in events:
        if not ev.home_team or not ev.away_team or not ev.start_time:
            continue
        h = normalize_team_name(ev.home_team)
        a = normalize_team_name(ev.away_team)
        key = f"{h}|{a}"
        start_iso = ev.start_time.isoformat() + "Z" if ev.start_time.tzinfo is None else ev.start_time.isoformat()
        event_lookup[key] = (ev.id, start_iso)
        # Also store reversed for swapped order
        event_lookup[f"{a}|{h}"] = (ev.id, start_iso)

    if not event_lookup:
        return 0

    # All normalized keys for fuzzy matching
    lookup_keys = list(event_lookup.keys())

    matched = 0
    for s in specials:
        event_name = s.get("event", "")
        teams = _parse_boost_teams(event_name)
        if not teams:
            continue

        h_norm = normalize_team_name(teams[0])
        a_norm = normalize_team_name(teams[1])
        boost_key = f"{h_norm}|{a_norm}"

        # Exact match first
        if boost_key in event_lookup:
            ev_id, start_iso = event_lookup[boost_key]
            s["matched_event_id"] = ev_id
            s["event_time"] = start_iso
            matched += 1
            continue

        # Fuzzy match
        best_score = 0
        best_key = None
        for ek in lookup_keys:
            score = fuzz.ratio(boost_key, ek)
            if score > best_score:
                best_score = score
                best_key = ek

        if best_score >= 80 and best_key:
            ev_id, start_iso = event_lookup[best_key]
            s["matched_event_id"] = ev_id
            # Override event_time if scraper time differs by > 24h (likely wrong)
            scraped_et = s.get("event_time")
            if scraped_et:
                try:
                    et = datetime.fromisoformat(scraped_et.replace("Z", "+00:00"))
                    ev_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
                    if abs((et - ev_dt).total_seconds()) > 86400:
                        s["event_time"] = start_iso
                except (ValueError, TypeError):
                    s["event_time"] = start_iso
            else:
                s["event_time"] = start_iso
            matched += 1

    return matched


def _fill_pinnacle_proxy_odds(specials: list[dict], db: Session) -> int:
    """Synthesize original_odds from Pinnacle fair odds for boosts missing them.

    Only applies to single-leg match winner bets where the boost event
    matched a Pinnacle event and we can identify the outcome from the title.
    """
    from ..analysis.llm_enrichment import _detect_legs_from_title

    needs_proxy = [
        s
        for s in specials
        if not s.get("original_odds") and s.get("matched_event_id") and _detect_legs_from_title(s.get("title", "")) == 1
    ]
    if not needs_proxy:
        return 0

    event_ids = list({s["matched_event_id"] for s in needs_proxy})
    events = db.query(Event).filter(Event.id.in_(event_ids)).all()
    event_map = {ev.id: ev for ev in events}

    pinnacle_odds = (
        db.query(Odds)
        .filter(
            Odds.event_id.in_(event_ids),
            Odds.provider_id == "pinnacle",
            Odds.market.in_(["1x2", "moneyline"]),
        )
        .all()
    )
    odds_by_event: dict[str, dict[str, float]] = {}
    for o in pinnacle_odds:
        odds_by_event.setdefault(o.event_id, {})[o.outcome] = o.odds

    count = 0
    for s in needs_proxy:
        eid = s["matched_event_id"]
        market_odds = odds_by_event.get(eid)
        if not market_odds or len(market_odds) < 2:
            continue

        ev = event_map.get(eid)
        if not ev or not ev.home_team or not ev.away_team:
            continue

        title_lower = s.get("title", "").lower()
        home_lower = ev.home_team.lower()
        away_lower = ev.away_team.lower()

        outcome = None
        if home_lower in title_lower and away_lower not in title_lower:
            outcome = "home"
        elif away_lower in title_lower and home_lower not in title_lower:
            outcome = "away"

        if not outcome or outcome not in market_odds:
            continue

        pinnacle_fair = get_fair_odds_for_outcome(outcome, market_odds)
        if pinnacle_fair and pinnacle_fair > 1.0:
            s["original_odds"] = round(pinnacle_fair, 3)
            s["fair_odds"] = round(pinnacle_fair, 3)
            count += 1

    return count


def _fill_pinnacle_fair_odds(specials: list[dict], db: Session) -> int:
    """Fill fair_odds from Pinnacle for matched single-leg boosts that already have original_odds.

    _fill_pinnacle_proxy_odds handles boosts WITHOUT original_odds (sets both original + fair).
    This function handles boosts WITH scraped original_odds — they still need Pinnacle fair_odds
    for the FAIR column display.
    """
    from ..analysis.llm_enrichment import _detect_legs_from_title

    needs_fair = [
        s
        for s in specials
        if s.get("matched_event_id")
        and s.get("original_odds")  # already has scraped original
        and not s.get("fair_odds")  # but no fair_odds yet
        and _detect_legs_from_title(s.get("title", "")) == 1
    ]
    if not needs_fair:
        return 0

    event_ids = list({s["matched_event_id"] for s in needs_fair})
    events = db.query(Event).filter(Event.id.in_(event_ids)).all()
    event_map = {ev.id: ev for ev in events}

    pinnacle_odds = (
        db.query(Odds)
        .filter(
            Odds.event_id.in_(event_ids),
            Odds.provider_id == "pinnacle",
            Odds.market.in_(["1x2", "moneyline"]),
        )
        .all()
    )
    odds_by_event: dict[str, dict[str, float]] = {}
    for o in pinnacle_odds:
        odds_by_event.setdefault(o.event_id, {})[o.outcome] = o.odds

    count = 0
    for s in needs_fair:
        eid = s["matched_event_id"]
        market_odds = odds_by_event.get(eid)
        if not market_odds or len(market_odds) < 2:
            continue

        ev = event_map.get(eid)
        if not ev or not ev.home_team or not ev.away_team:
            continue

        title_lower = s.get("title", "").lower()
        home_lower = ev.home_team.lower()
        away_lower = ev.away_team.lower()

        outcome = None
        if home_lower in title_lower and away_lower not in title_lower:
            outcome = "home"
        elif away_lower in title_lower and home_lower not in title_lower:
            outcome = "away"

        if not outcome or outcome not in market_odds:
            continue

        pinnacle_fair = get_fair_odds_for_outcome(outcome, market_odds)
        if pinnacle_fair and pinnacle_fair > 1.0:
            s["fair_odds"] = round(pinnacle_fair, 3)
            count += 1

    return count


def enrich_specials_with_ev(specials: list[dict], db: Session) -> list[dict]:
    """Compute boost edge and cross-reference boost events with Events table."""
    # 1. Cross-reference with Events table FIRST (sets matched_event_id)
    matched = _match_boosts_to_events(specials, db)
    logger.info(f"Event matching: {matched}/{len(specials)} boosts matched to events")

    # 2. Synthesize original_odds from Pinnacle for providers that don't expose them
    proxy_count = _fill_pinnacle_proxy_odds(specials, db)
    if proxy_count:
        logger.info(f"Pinnacle proxy: {proxy_count} boosts got synthesized original_odds")

    # 3. Fill Pinnacle fair_odds for matched boosts that already have scraped original_odds
    fair_count = _fill_pinnacle_fair_odds(specials, db)
    if fair_count:
        logger.info(f"Pinnacle fair odds: {fair_count} boosts got fair_odds from Pinnacle")

    # 4. Boost edge: boosted_odds / original_odds - 1
    count = 0
    for s in specials:
        boosted = s.get("boosted_odds")
        original = s.get("original_odds")
        if boosted and original and original > 1.0:
            s["edge_pct"] = round((boosted / original - 1) * 100, 2)
            s["is_positive_ev"] = s["edge_pct"] > 0
            count += 1
    logger.info(f"Boost edge: {count}/{len(specials)} computed (boosted/original)")

    return specials


# ── Expiry filter ──────────────────────────────────────────────────────


def filter_expired(specials: list[dict], db: Session | None = None) -> list[dict]:
    """Remove specials whose event has started, expires_at is past, or matched event is past."""
    now = datetime.now(UTC)

    # If we have DB access, check matched events for authoritative start_time
    matched_event_times: dict[str, datetime] = {}
    if db:
        matched_ids = [s["matched_event_id"] for s in specials if s.get("matched_event_id")]
        if matched_ids:
            events = db.query(Event).filter(Event.id.in_(matched_ids)).all()
            for ev in events:
                if ev.start_time:
                    st = ev.start_time
                    if st.tzinfo is None:
                        st = st.replace(tzinfo=UTC)
                    matched_event_times[ev.id] = st

    result = []
    for s in specials:
        # Check matched event start_time (most authoritative)
        mid = s.get("matched_event_id")
        if mid and mid in matched_event_times and matched_event_times[mid] <= now:
            continue

        # Check scraped event_time
        event_time = s.get("event_time")
        if event_time:
            try:
                et = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                if et.tzinfo is None:
                    et = et.replace(tzinfo=UTC)
                if et <= now:
                    continue
            except (ValueError, TypeError):
                pass

        # Check expires_at
        exp = s.get("expires_at")
        if not exp:
            result.append(s)
            continue
        try:
            dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
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
            # Event matching
            matched_event_id=s.get("matched_event_id"),
            # Pinnacle fair odds (de-vigged, for FAIR column display)
            fair_odds=s.get("fair_odds"),
            # Boost edge (simple: boosted/original)
            edge_pct=s.get("edge_pct"),
            is_positive_ev=s.get("is_positive_ev"),
            # LLM enrichment fields
            llm_title=s.get("llm_title"),
            llm_probability=s.get("llm_probability"),
            llm_fair_odds=s.get("llm_fair_odds"),
            llm_edge_pct=s.get("llm_edge_pct"),
            llm_reasoning=s.get("llm_reasoning"),
            llm_confidence=s.get("llm_confidence"),
        )
        session.add(row)
        count += 1

    session.commit()
    logger.info(f"Stored {count} specials to DB")
    return count
