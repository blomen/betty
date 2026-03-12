"""
LLM-based probability estimation for odds boosts.

Uses Claude Haiku + Brave Search to research player stats, team form,
and historical data to estimate true probabilities for boosted bets.

Called after the simple boost-edge enrichment (ev_enrichment.py) as a
separate async pass. Skipped gracefully if API keys are not configured.

LLM results are persisted in the `llm_boost_cache` table so each boost
is only researched once, surviving backend restarts and specials purges.
"""

import asyncio
import hashlib
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────

LLM_MODEL = "claude-haiku-4-5-20251001"
LLM_MAX_TOKENS = 1024
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
MAX_CONCURRENT_LLM = 10
MAX_BOOSTS_PER_RUN = 500
BRAVE_RATE_LIMIT_DELAY = 1.1  # seconds between Brave requests (free: 1 req/sec)


# ── In-memory rate-limit state ────────────────────────────────────────

_brave_last_call: float = 0.0  # timestamp of last Brave API call
_brave_lock: asyncio.Lock | None = None  # lazy-init per event loop


def _cache_key(title: str, boosted_odds: float, event: str = "") -> str:
    raw = f"{title.strip().lower()}|{boosted_odds}|{event.strip().lower()}"
    return hashlib.md5(raw.encode()).hexdigest()


# ── Persistent DB cache ──────────────────────────────────────────────

def _load_cache_from_db(db: Session) -> dict[str, dict]:
    """Load ALL LLM results from the persistent llm_boost_cache table."""
    from src.db.models import LlmBoostCache
    rows = db.query(LlmBoostCache).all()
    cache = {}
    for r in rows:
        cache[r.cache_key] = {
            "llm_title": r.llm_title or "",
            "llm_probability": r.llm_probability,
            "llm_fair_odds": r.llm_fair_odds,
            "llm_reasoning": r.llm_reasoning,
            "llm_confidence": r.llm_confidence,
            "llm_event_time": getattr(r, "llm_event_time", None),
        }
    logger.debug(f"Loaded {len(cache)} LLM results from persistent cache")
    return cache


def _save_result_to_cache(db: Session, key: str, title: str, boosted_odds: float, result: dict) -> None:
    """Save a single LLM result to the persistent cache table (upsert)."""
    from src.db.models import LlmBoostCache
    now = datetime.now(timezone.utc).isoformat()
    existing = db.query(LlmBoostCache).filter_by(cache_key=key).first()
    if existing:
        existing.llm_title = result.get("title") or ""
        existing.llm_probability = result["probability"]
        existing.llm_fair_odds = round(1 / result["probability"], 3) if result["probability"] > 0 else None
        existing.llm_confidence = result.get("confidence", "low")
        existing.llm_reasoning = result.get("reasoning", "")
        existing.llm_event_time = result.get("event_time")
        existing.last_used_at = now
    else:
        db.add(LlmBoostCache(
            cache_key=key,
            title=title,
            boosted_odds=boosted_odds,
            llm_title=result.get("title") or "",
            llm_probability=result["probability"],
            llm_fair_odds=round(1 / result["probability"], 3) if result["probability"] > 0 else None,
            llm_confidence=result.get("confidence", "low"),
            llm_reasoning=result.get("reasoning", ""),
            llm_event_time=result.get("event_time"),
            created_at=now,
            last_used_at=now,
        ))
    try:
        db.commit()
    except Exception:
        db.rollback()


def _touch_cache_entries(db: Session, keys: list[str]) -> None:
    """Update last_used_at for cache entries that were carried forward."""
    if not keys:
        return
    from src.db.models import LlmBoostCache
    now = datetime.now(timezone.utc).isoformat()
    try:
        db.query(LlmBoostCache).filter(LlmBoostCache.cache_key.in_(keys)).update(
            {"last_used_at": now}, synchronize_session=False
        )
        db.commit()
    except Exception:
        db.rollback()


def _carry_forward_from_cache(specials: list[dict], cache: dict[str, dict]) -> tuple[int, list[str]]:
    """Apply cached LLM data to matching specials. Returns (count, list of used keys)."""
    count = 0
    used_keys = []
    for s in specials:
        key = _cache_key(s.get("title", ""), s.get("boosted_odds", 0), s.get("event", ""))
        prev = cache.get(key)
        if prev and prev.get("llm_probability"):
            probability = prev["llm_probability"]
            fair_odds = round(1 / probability, 3) if probability > 0 else None
            boosted_odds = s.get("boosted_odds", 0)

            s["llm_title"] = prev.get("llm_title", "")
            s["llm_probability"] = probability
            s["llm_fair_odds"] = fair_odds
            s["llm_reasoning"] = prev.get("llm_reasoning", "")
            s["llm_confidence"] = prev.get("llm_confidence", "low")
            # Recompute edge from current boosted_odds (may have changed)
            if fair_odds and fair_odds > 1.0 and boosted_odds > 1.0:
                s["llm_edge_pct"] = round((boosted_odds / fair_odds - 1) * 100, 2)
            # Apply LLM event_time if scraped event_time is missing
            llm_et = prev.get("llm_event_time")
            if llm_et and not s.get("event_time"):
                s["event_time"] = llm_et
            count += 1
            used_keys.append(key)
    return count, used_keys


# ── Candidate filtering ────────────────────────────────────────────────

def _is_llm_candidate(special: dict) -> bool:
    """Check if a boost should be sent to LLM for probability research.

    All boosts with valid boosted_odds are candidates.
    """
    return bool(special.get("boosted_odds"))


# ── Brave Search ───────────────────────────────────────────────────────

async def _brave_search(query: str, client: httpx.AsyncClient) -> str:
    """Run a Brave Search query, return top 5 result snippets as text."""
    global _brave_last_call, _brave_lock
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return ""
    if _brave_lock is None:
        _brave_lock = asyncio.Lock()
    try:
        # Rate limit: 1 request/sec on free plan
        async with _brave_lock:
            elapsed = time.time() - _brave_last_call
            if elapsed < BRAVE_RATE_LIMIT_DELAY:
                await asyncio.sleep(BRAVE_RATE_LIMIT_DELAY - elapsed)
            _brave_last_call = time.time()
        response = await client.get(
            BRAVE_SEARCH_ENDPOINT,
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            params={"q": query, "count": 5, "freshness": "pw"},
            timeout=10.0,
        )
        if response.status_code != 200:
            logger.debug(f"Brave search failed ({response.status_code}) for: {query[:80]}")
            return ""
        data = response.json()
        results = data.get("web", {}).get("results", [])
        snippets = []
        for r in results[:5]:
            title = r.get("title", "")
            desc = r.get("description", "")
            if title or desc:
                snippets.append(f"- {title}: {desc}")
        return "\n".join(snippets)
    except Exception as e:
        logger.debug(f"Brave search error: {e}")
        return ""


def _build_search_queries(special: dict) -> list[str]:
    """Build 1-2 targeted search queries for a boost."""
    title = special.get("title", "")
    event = special.get("event", "")
    sport = special.get("sport", "unknown")
    league = special.get("league", "")

    today = datetime.now(timezone.utc).strftime("%Y")
    queries = []
    if event:
        teams = event.replace(" vs ", " ").replace(" - ", " ")
        ctx = f"{league} " if league else f"{sport} "
        queries.append(f"{teams} {ctx}match prediction odds {today}")
    if title and title != event:
        queries.append(f"{title} {sport} statistics probability")
    return queries[:2]


# ── LLM prompt ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a sports betting probability analyst. Given a boosted odds offer and search results about the event, estimate the TRUE probability of the outcome occurring. Also identify when the event takes place.

RULES:
- Base your estimate on statistics, recent form, and market context
- Be CONSERVATIVE — overestimating probability loses money in betting
- For player props (goalscorer, assists, etc.), use base rates and player statistics
- Express your probability as a decimal between 0.01 and 0.99
- Determine the event start time from search results, event context, or league schedules

COMBO BET RULES (CRITICAL — most boosts are combos):
- A combo bet combines 2+ outcomes (e.g. "Team wins & over 2.5 goals")
- You MUST estimate each leg separately, then multiply them together
- The PROBABILITY field MUST be the FINAL COMBINED probability (the product), NOT a single leg
- Example: if leg1=0.55 and leg2=0.48, then PROBABILITY: 0.26 (because 0.55 × 0.48 = 0.264)
- Legs in the same match are NOT fully independent — adjust for correlation:
  - "Win & over X goals": positive correlation (winning teams tend to score more). Multiply then add ~5-10%
  - "Win & BTTS yes": mild positive for favorites. Multiply then add ~0-5%
  - "HT/FT same team": NOT independent — conditional probability (team leading at HT has ~70-80% to win FT)
- DOUBLE-CHECK: your PROBABILITY value must be LOWER than each individual leg probability

SCANDINAVIAN BOOST TITLE FORMATS:
- "Halvtid/fulltid: TeamA/TeamB" means TeamA leads at halftime AND TeamB wins at full-time. The teams after the colon are the SELECTIONS, not the event participants. E.g. for "Leipzig vs Augsburg", title "Halvtid/fulltid: FC Augsburg/FC Augsburg" = Augsburg leads HT & Augsburg wins FT.
- "1x2 & båda lagen gör mål: X & Y" means match result X AND both teams score Y (ja=yes, nej=no). "Oavgjort" = draw.
- "Halvtid/fulltid - rätt resultat: X Y+" means HT/FT correct score X with Y+ total goals.
- Always interpret the SELECTION text literally — do NOT flip teams.

OUTPUT FORMAT (strict — follow exactly):
TITLE: A short, clear English title for this bet (max 8 words). Translate any non-English terms. Use the SELECTION teams from the original title, not the event home team. Examples: "Arsenal wins & both teams score", "Real Sociedad leads HT & wins FT", "Man Utd wins & over 2.5 goals"
EVENT_TIME: ISO 8601 datetime with timezone (e.g. 2026-03-05T20:00:00+01:00). Use UNKNOWN if you cannot determine it.
LEGS: number of distinct outcomes in this bet (1 for single bets, 2+ for combos)
PROBABILITY: 0.XX (MUST be the final combined probability for combos — NOT a single leg)
CONFIDENCE: low|medium|high
REASONING:
For single bets (LEGS: 1): 2-3 bullet points with key stats, each max 10 words.
For combo bets (LEGS: 2+): show each leg then the combined calculation.
- Leg 1: [outcome] p=[0.XX] — [brief justification]
- Leg 2: [outcome] p=[0.XX] — [brief justification]
- Combined: [0.XX] × [0.XX] = [0.XX] (adjusted [up/down] for [correlation reason])

Example for combo:
- Leg 1: Arsenal win p=0.62 — strong home form, 8W in last 10
- Leg 2: Over 2.5 goals p=0.58 — league avg 2.8 goals/game
- Combined: 0.62 × 0.58 = 0.36 (adjusted up to 0.38, positive correlation)

Example for single:
- Team A won 8 of last 10 home games
- Player B: 0.4 goals/game this season
- H2H: 3-1 in last 4 meetings"""


def _build_user_prompt(special: dict, search_results: str) -> str:
    title = special.get("title", "")
    event = special.get("event", "")
    sport = special.get("sport", "")
    league = special.get("league", "")
    market_label = special.get("market_label", "")
    boosted_odds = special.get("boosted_odds", 0)
    original_odds = special.get("original_odds")
    boost_pct = special.get("boost_pct")
    event_time = special.get("event_time")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = [
        f"TODAY'S DATE: {today}",
        f"BOOST TITLE: {title}",
        f"EVENT: {event}",
        f"SPORT: {sport}" + (f" ({league})" if league else ""),
    ]
    if market_label:
        parts.append(f"MARKET: {market_label}")
    if event_time:
        parts.append(f"EVENT TIME (from scraper): {event_time}")
    parts.append(f"BOOSTED ODDS: {boosted_odds}")
    if original_odds:
        parts.append(f"ORIGINAL ODDS: {original_odds} (bookmaker implied: {100/original_odds:.0f}%)")
    if boost_pct:
        parts.append(f"BOOST PERCENTAGE: +{boost_pct:.0f}%")

    if search_results:
        parts.append(f"\nSEARCH RESULTS:\n{search_results}")
    else:
        parts.append("\n(No search results available — use your knowledge)")

    parts.append("\nEstimate the true probability and identify the event start time.")
    return "\n".join(parts)


# ── Response parsing ───────────────────────────────────────────────────

_TITLE_RE = re.compile(r'TITLE:\s*(.+)', re.IGNORECASE)
_EVENT_TIME_RE = re.compile(r'EVENT_TIME:\s*(\S+)', re.IGNORECASE)
_LEGS_RE = re.compile(r'LEGS:\s*(\d+)', re.IGNORECASE)
_PROB_RE = re.compile(r'PROBABILITY:\s*(0\.\d+)', re.IGNORECASE)
_CONF_RE = re.compile(r'CONFIDENCE:\s*(low|medium|high)', re.IGNORECASE)
_LEG_PROB_RE = re.compile(r'Leg\s*\d+:.*?p\s*=\s*(0\.\d+)', re.IGNORECASE)
_REASONING_RE = re.compile(r'REASONING:\s*(.+)', re.IGNORECASE | re.DOTALL)


def _parse_event_time(raw: str) -> Optional[str]:
    """Parse and validate an ISO 8601 datetime from LLM output."""
    if not raw or raw.upper() == "UNKNOWN":
        return None
    try:
        dt = datetime.fromisoformat(raw)
        # Ensure timezone-aware (assume UTC if naive)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return None


def _validate_combo_probability(probability: float, legs: int, text: str) -> float:
    """Validate and auto-correct combo bet probabilities.

    If the LLM declared multiple legs but returned a probability higher than
    any individual leg (i.e. forgot to multiply), recompute from leg probs.
    """
    if legs <= 1:
        return probability

    # Extract individual leg probabilities from reasoning
    leg_probs = [float(m) for m in _LEG_PROB_RE.findall(text)]

    if len(leg_probs) < 2:
        # No structured legs found — check if probability seems too high for a combo.
        # A 2-leg combo should rarely exceed 0.45; 3-leg rarely exceed 0.25.
        max_reasonable = {2: 0.45, 3: 0.25}.get(legs, 0.20)
        if probability > max_reasonable:
            logger.warning(
                f"Combo ({legs} legs) probability {probability:.2f} exceeds "
                f"reasonable max {max_reasonable} but no leg probs to auto-correct — rejecting"
            )
            return -1.0  # Signal rejection
        return probability

    # Compute expected combined probability from legs
    combined = 1.0
    for lp in leg_probs:
        combined *= lp

    # Allow modest correlation adjustment (up to +30% of product)
    max_combined = combined * 1.3
    min_combined = combined * 0.7

    if probability > max(leg_probs):
        # LLM clearly returned a single-leg probability instead of the product
        logger.warning(
            f"Combo probability {probability:.2f} > max leg {max(leg_probs):.2f} — "
            f"auto-correcting to product {combined:.3f}"
        )
        return round(combined, 4)

    if probability > max_combined:
        logger.warning(
            f"Combo probability {probability:.2f} too high vs product {combined:.3f} "
            f"(max allowed {max_combined:.3f}) — clamping"
        )
        return round(max_combined, 4)

    if probability < min_combined:
        logger.warning(
            f"Combo probability {probability:.2f} too low vs product {combined:.3f} "
            f"(min allowed {min_combined:.3f}) — clamping"
        )
        return round(min_combined, 4)

    return probability


def _detect_legs_from_title(title: str) -> int:
    """Heuristic: detect if a boost title implies a combo bet."""
    title_lower = title.lower()

    # HT/FT bets are always 2 legs
    if "halvtid/fulltid" in title_lower or "ht/ft" in title_lower:
        return 2

    # "1:a halvlek - 1x2 & totalt:" = HT result + HT total (2 legs)
    if "1x2 & totalt" in title_lower or "1x2 & båda" in title_lower:
        return 2

    # "Resultat + Antal mål" / "Resultat + Båda lagen" = result + goals/BTTS (2 legs)
    if "resultat + " in title_lower:
        return 2

    # "1x2: Team, Totalt antal mål: Under X" / "1x2: Team, Båda lagen gör mål: Ja"
    if re.search(r'1x2:.*,\s*(totalt|båda)', title_lower):
        return 2

    # Explicit conjunctions: " & ", " och ", " and "
    conjunction_count = (
        title_lower.count(" & ")
        + title_lower.count(" och ")
        + title_lower.count(" and ")
    )
    if conjunction_count > 0:
        return conjunction_count + 1

    return 1


def _parse_llm_response(text: str, boost_title: str = "") -> Optional[dict]:
    prob_match = _PROB_RE.search(text)
    if not prob_match:
        return None
    probability = float(prob_match.group(1))
    if probability < 0.01 or probability > 0.99:
        return None

    title_match = _TITLE_RE.search(text)
    title = title_match.group(1).strip()[:100] if title_match else ""
    # Clean: stop at next field marker if present
    for marker in ("EVENT_TIME:", "LEGS:", "PROBABILITY:", "CONFIDENCE:", "REASONING:"):
        if marker in title:
            title = title[:title.index(marker)].strip()

    event_time_match = _EVENT_TIME_RE.search(text)
    event_time = _parse_event_time(event_time_match.group(1)) if event_time_match else None

    # Determine number of legs (prefer LLM declaration, fallback to title heuristic)
    legs_match = _LEGS_RE.search(text)
    legs = int(legs_match.group(1)) if legs_match else _detect_legs_from_title(boost_title)

    # Validate combo probability
    probability = _validate_combo_probability(probability, legs, text)
    if probability < 0:
        return None  # Rejected by validation

    conf_match = _CONF_RE.search(text)
    confidence = conf_match.group(1).lower() if conf_match else "low"

    reasoning_match = _REASONING_RE.search(text)
    reasoning = reasoning_match.group(1).strip()[:500] if reasoning_match else ""

    return {
        "title": title,
        "event_time": event_time,
        "probability": probability,
        "confidence": confidence,
        "reasoning": reasoning,
    }


# ── Single-boost research ─────────────────────────────────────────────

async def _research_single_boost(
    special: dict,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    async with semaphore:
        title = special.get("title", "")[:60]
        try:
            # Brave searches
            queries = _build_search_queries(special)
            search_texts = []
            for q in queries:
                result = await _brave_search(q, client)
                if result:
                    search_texts.append(result)
            search_combined = "\n\n".join(search_texts)

            # Claude Haiku call
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                return None

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": LLM_MODEL,
                    "max_tokens": LLM_MAX_TOKENS,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": _build_user_prompt(special, search_combined)}],
                },
                timeout=30.0,
            )

            if response.status_code != 200:
                logger.warning(f"LLM API error {response.status_code} for: {title}")
                return None

            data = response.json()
            text = data.get("content", [{}])[0].get("text", "")
            boost_title = special.get("title", "")
            parsed = _parse_llm_response(text, boost_title=boost_title)

            if parsed:
                logger.debug(f"LLM researched: {title} → p={parsed['probability']:.2f} ({parsed['confidence']})")
            else:
                logger.warning(f"LLM parse failed for: {title}")

            return parsed

        except Exception as e:
            logger.warning(f"LLM research failed for '{title}': {e}")
            return None


# ── Cache invalidation for bad combo probabilities ────────────────────

def _invalidate_bad_combo_cache(specials: list[dict], cache: dict[str, dict], db: Session) -> None:
    """Delete cached LLM results for combo boosts where probability is too high.

    After improving the prompt to handle combos properly, old cache entries
    may have single-leg probabilities instead of combined. Detect and purge them
    so they get re-researched with the improved prompt.
    """
    from src.db.models import LlmBoostCache
    keys_to_delete = []
    for s in specials:
        key = _cache_key(s.get("title", ""), s.get("boosted_odds", 0), s.get("event", ""))
        prev = cache.get(key)
        if not prev or not prev.get("llm_probability"):
            continue
        title = s.get("title", "")
        legs = _detect_legs_from_title(title)
        if legs <= 1:
            continue
        prob = prev["llm_probability"]
        max_reasonable = {2: 0.45, 3: 0.25}.get(legs, 0.20)
        if prob > max_reasonable:
            logger.info(
                f"Invalidating cached combo: '{title[:50]}' p={prob:.2f} "
                f"(>{max_reasonable} for {legs}-leg combo)"
            )
            keys_to_delete.append(key)
            del cache[key]  # Remove from in-memory cache too

    if keys_to_delete:
        try:
            db.query(LlmBoostCache).filter(
                LlmBoostCache.cache_key.in_(keys_to_delete)
            ).delete(synchronize_session=False)
            db.commit()
            logger.info(f"Invalidated {len(keys_to_delete)} bad combo cache entries")
        except Exception:
            db.rollback()


# ── Main entry point ───────────────────────────────────────────────────

async def enrich_specials_with_llm(specials: list[dict], db: Optional[Session] = None) -> list[dict]:
    """LLM-based probability estimation and event time extraction for boosts.

    Runs AFTER enrich_specials_with_ev() (which sets edge_pct = boost_pct).
    Processes ALL boosts with valid boosted_odds. Also extracts event_time
    when not available from the scraper.

    Uses the persistent `llm_boost_cache` table to avoid re-researching
    boosts that have already been analyzed. Each boost is researched once
    and the result is saved permanently.

    Skipped if ANTHROPIC_API_KEY is not set.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("LLM enrichment skipped: ANTHROPIC_API_KEY not set")
        return specials

    # Carry forward existing LLM results from persistent cache
    carried = 0
    if db:
        cache = _load_cache_from_db(db)
        # Invalidate cached combo boosts with suspiciously high probabilities
        _invalidate_bad_combo_cache(specials, cache, db)
        carried, used_keys = _carry_forward_from_cache(specials, cache)
        # Update last_used_at for carried entries
        _touch_cache_entries(db, used_keys)

    # Only send boosts that still need LLM research
    candidates = [s for s in specials if _is_llm_candidate(s) and s.get("llm_probability") is None]
    if not candidates:
        logger.info(f"LLM enrichment: 0 new candidates ({carried} carried from cache)")
        return specials

    # Prioritize highest edge/boost, cap to MAX_BOOSTS_PER_RUN
    candidates.sort(
        key=lambda s: s.get("edge_pct") or s.get("boost_pct") or 0,
        reverse=True,
    )
    candidates = candidates[:MAX_BOOSTS_PER_RUN]

    has_brave = bool(os.environ.get("BRAVE_API_KEY"))
    logger.info(
        f"LLM enrichment: researching {len(candidates)} new boosts, "
        f"{carried} carried from cache (brave={'yes' if has_brave else 'no'})"
    )

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM)
    enriched_count = 0

    async with httpx.AsyncClient() as client:
        tasks = [
            _research_single_boost(s, client, semaphore)
            for s in candidates
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for special, result in zip(candidates, results):
            if isinstance(result, Exception) or result is None:
                continue

            probability = result["probability"]
            fair_odds = round(1 / probability, 3)
            boosted_odds = special.get("boosted_odds", 0)

            if fair_odds <= 1.0 or boosted_odds <= 1.0:
                continue

            edge_pct = round((boosted_odds / fair_odds - 1) * 100, 2)

            special["llm_title"] = result.get("title") or ""
            special["llm_probability"] = round(probability, 4)
            special["llm_fair_odds"] = fair_odds
            special["llm_edge_pct"] = edge_pct
            special["llm_reasoning"] = result["reasoning"]
            special["llm_confidence"] = result["confidence"]
            # Apply LLM event_time if scraped event_time is missing
            llm_et = result.get("event_time")
            if llm_et and not special.get("event_time"):
                special["event_time"] = llm_et
            enriched_count += 1

            # Save to persistent cache immediately
            if db:
                key = _cache_key(special.get("title", ""), boosted_odds, special.get("event", ""))
                _save_result_to_cache(db, key, special.get("title", ""), boosted_odds, result)

    logger.info(
        f"LLM enrichment: {enriched_count}/{len(candidates)} "
        f"successfully researched (total cached: {carried + enriched_count})"
    )
    return specials
