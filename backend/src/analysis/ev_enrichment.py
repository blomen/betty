"""
EV Enrichment for Odds Boosts

Simple boost-percentage edge: edge = (boosted_odds / original_odds - 1) * 100
LLM-based probability research runs separately (see llm_enrichment.py).

Also provides deduplicate_specials(), filter_expired(), store_specials_to_db().
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..db.models import SpecialOdds

logger = logging.getLogger(__name__)


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


def enrich_specials_with_ev(specials: list[dict], db: Session) -> list[dict]:
    """Compute boost edge = (boosted_odds / original_odds - 1) * 100."""
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
            # Boost edge (simple: boosted/original)
            edge_pct=s.get("edge_pct"),
            is_positive_ev=s.get("is_positive_ev"),
            # LLM enrichment fields
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
