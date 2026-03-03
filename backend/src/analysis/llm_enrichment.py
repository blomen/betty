"""
LLM-based probability estimation for odds boosts.

Uses Claude Haiku + Brave Search to research player stats, team form,
and historical data to estimate true probabilities for boosted bets.

Called after the simple boost-edge enrichment (ev_enrichment.py) as a
separate async pass. Skipped gracefully if API keys are not configured.
"""

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Optional

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────

LLM_MODEL = "claude-haiku-4-5-20251001"
LLM_MAX_TOKENS = 1024
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
CACHE_TTL_SECONDS = 4 * 3600  # 4 hours
MAX_CONCURRENT_LLM = 5
MAX_BOOSTS_PER_RUN = 40
BRAVE_RATE_LIMIT_DELAY = 1.1  # seconds between Brave requests (free: 1 req/sec)
MIN_EDGE_PCT = 20  # Only research boosts with >= 20% boost edge


# ── In-memory cache ────────────────────────────────────────────────────

_llm_cache: dict[str, tuple[float, dict]] = {}  # key -> (timestamp, result)
_brave_last_call: float = 0.0  # timestamp of last Brave API call
_brave_lock: asyncio.Lock | None = None  # lazy-init per event loop


def _cache_key(title: str, boosted_odds: float) -> str:
    raw = f"{title.strip().lower()}|{boosted_odds}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(key: str) -> Optional[dict]:
    entry = _llm_cache.get(key)
    if entry and (time.time() - entry[0]) < CACHE_TTL_SECONDS:
        return entry[1]
    if entry:
        del _llm_cache[key]
    return None


# ── DB carry-forward ──────────────────────────────────────────────────

def _load_existing_llm_data(db: Session) -> dict[str, dict]:
    """Load existing LLM results from DB, keyed by cache_key(title, boosted_odds)."""
    from src.db.models import SpecialOdds
    rows = db.query(SpecialOdds).filter(SpecialOdds.llm_probability.isnot(None)).all()
    existing = {}
    for r in rows:
        key = _cache_key(r.title or "", r.boosted_odds or 0)
        existing[key] = {
            "llm_probability": r.llm_probability,
            "llm_fair_odds": r.llm_fair_odds,
            "llm_edge_pct": r.llm_edge_pct,
            "llm_reasoning": r.llm_reasoning,
            "llm_confidence": r.llm_confidence,
        }
    return existing


def _carry_forward_llm(specials: list[dict], existing: dict[str, dict]) -> int:
    """Apply existing LLM data to matching specials. Returns count carried forward."""
    count = 0
    for s in specials:
        key = _cache_key(s.get("title", ""), s.get("boosted_odds", 0))
        prev = existing.get(key)
        if prev and prev.get("llm_probability"):
            for field in ("llm_probability", "llm_fair_odds", "llm_edge_pct", "llm_reasoning", "llm_confidence"):
                s[field] = prev[field]
            count += 1
    return count


# ── Candidate filtering ────────────────────────────────────────────────

def _is_llm_candidate(special: dict) -> bool:
    """Check if a boost should be sent to LLM for probability research."""
    if not special.get("boosted_odds"):
        return False
    edge = special.get("edge_pct")
    if edge is not None and edge >= MIN_EDGE_PCT:
        return True
    # No edge computed (no original_odds) — use boost_pct as fallback
    boost = special.get("boost_pct") or 0
    if edge is None and boost >= MIN_EDGE_PCT:
        return True
    return False


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

    queries = []
    if event:
        teams = event.replace(" vs ", " ").replace(" - ", " ")
        ctx = f"{league} " if league else f"{sport} "
        queries.append(f"{teams} {ctx}match prediction odds 2026")
    if title and title != event:
        # For player props or combo bets, search the specific market
        queries.append(f"{title} {sport} statistics probability")
    return queries[:2]


# ── LLM prompt ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a sports betting probability analyst. Given a boosted odds offer and search results about the event, estimate the TRUE probability of the outcome occurring.

RULES:
- Base your estimate on statistics, recent form, and market context
- Be CONSERVATIVE — overestimating probability loses money in betting
- For combo bets (multiple outcomes combined), multiply independent probabilities
- For player props (goalscorer, assists, etc.), use base rates and player statistics
- Express your probability as a decimal between 0.01 and 0.99

OUTPUT FORMAT (strict — follow exactly):
PROBABILITY: 0.XX
CONFIDENCE: low|medium|high
REASONING: 2-3 bullet points, each max 10 words. Key stats/facts only. Example:
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

    parts = [
        f"BOOST TITLE: {title}",
        f"EVENT: {event}",
        f"SPORT: {sport}" + (f" ({league})" if league else ""),
    ]
    if market_label:
        parts.append(f"MARKET: {market_label}")
    parts.append(f"BOOSTED ODDS: {boosted_odds}")
    if original_odds:
        parts.append(f"ORIGINAL ODDS: {original_odds} (bookmaker implied: {100/original_odds:.0f}%)")
    if boost_pct:
        parts.append(f"BOOST PERCENTAGE: +{boost_pct:.0f}%")

    if search_results:
        parts.append(f"\nSEARCH RESULTS:\n{search_results}")
    else:
        parts.append("\n(No search results available — use your knowledge)")

    parts.append("\nEstimate the true probability of this outcome occurring.")
    return "\n".join(parts)


# ── Response parsing ───────────────────────────────────────────────────

_PROB_RE = re.compile(r'PROBABILITY:\s*(0\.\d+)', re.IGNORECASE)
_CONF_RE = re.compile(r'CONFIDENCE:\s*(low|medium|high)', re.IGNORECASE)
_REASONING_RE = re.compile(r'REASONING:\s*(.+)', re.IGNORECASE | re.DOTALL)


def _parse_llm_response(text: str) -> Optional[dict]:
    prob_match = _PROB_RE.search(text)
    if not prob_match:
        return None
    probability = float(prob_match.group(1))
    if probability < 0.01 or probability > 0.99:
        return None

    conf_match = _CONF_RE.search(text)
    confidence = conf_match.group(1).lower() if conf_match else "low"

    reasoning_match = _REASONING_RE.search(text)
    reasoning = reasoning_match.group(1).strip()[:500] if reasoning_match else ""

    return {
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
            # Check cache
            cache_key = _cache_key(
                special.get("title", ""),
                special.get("boosted_odds", 0),
            )
            cached = _get_cached(cache_key)
            if cached:
                logger.debug(f"LLM cache hit: {title}")
                return cached

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
            parsed = _parse_llm_response(text)

            if parsed:
                _llm_cache[cache_key] = (time.time(), parsed)
                logger.debug(f"LLM researched: {title} → p={parsed['probability']:.2f} ({parsed['confidence']})")
            else:
                logger.warning(f"LLM parse failed for: {title}")

            return parsed

        except Exception as e:
            logger.warning(f"LLM research failed for '{title}': {e}")
            return None


# ── Main entry point ───────────────────────────────────────────────────

async def enrich_specials_with_llm(specials: list[dict], db: Optional[Session] = None) -> list[dict]:
    """LLM-based probability estimation for boosts.

    Runs AFTER enrich_specials_with_ev() (which sets edge_pct = boost_pct).
    Only processes boosts meeting the edge/boost threshold.
    Carries forward existing LLM data from DB to avoid re-researching.
    Skipped if ANTHROPIC_API_KEY is not set.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("LLM enrichment skipped: ANTHROPIC_API_KEY not set")
        return specials

    # Carry forward existing LLM results from DB (avoid re-calling LLM)
    carried = 0
    if db:
        existing = _load_existing_llm_data(db)
        carried = _carry_forward_llm(specials, existing)

    # Only send boosts that still need LLM research
    candidates = [s for s in specials if _is_llm_candidate(s) and s.get("llm_probability") is None]
    if not candidates:
        logger.info(f"LLM enrichment: 0 new candidates ({carried} carried from DB)")
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
        f"{carried} carried from DB (brave={'yes' if has_brave else 'no'})"
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

            special["llm_probability"] = round(probability, 4)
            special["llm_fair_odds"] = fair_odds
            special["llm_edge_pct"] = edge_pct
            special["llm_reasoning"] = result["reasoning"]
            special["llm_confidence"] = result["confidence"]
            enriched_count += 1

    logger.info(
        f"LLM enrichment: {enriched_count}/{len(candidates)} "
        f"successfully researched"
    )
    return specials
