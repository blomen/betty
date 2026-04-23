"""
LLM-based probability estimation for odds boosts.

Uses Claude Haiku to estimate true probabilities for boosted bets
based on its training knowledge of sports statistics and betting markets.

Called after the simple boost-edge enrichment (ev_enrichment.py) as a
separate async pass. Skipped gracefully if ANTHROPIC_API_KEY is not set.

LLM results are persisted in the `llm_boost_cache` table so each boost
is only researched once, surviving backend restarts and specials purges.
"""

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────

LLM_MODEL = "claude-haiku-4-5-20251001"
LLM_MAX_TOKENS = 1024
MAX_CONCURRENT_LLM = 10
MAX_BOOSTS_PER_RUN = 500
CACHE_TTL_HOURS = 48


# ── LLM health status (surfaced to frontend) ─────────────────────────

_llm_health: dict = {
    "status": "unknown",  # ok | error | skipped
    "anthropic_status": None,  # ok | usage_limit | auth_error | rate_limited | missing_key | error
    "last_error": None,  # human-readable error message
    "last_success_at": None,  # ISO timestamp of last successful enrichment
    "last_run_at": None,  # ISO timestamp of last run attempt
    "enriched_count": 0,  # boosts enriched in last run
    "carried_count": 0,  # boosts carried from cache in last run
    "candidate_count": 0,  # boosts that needed enrichment in last run
}


def get_llm_health() -> dict:
    """Return current LLM enrichment health status for API consumers."""
    return dict(_llm_health)


def _update_health(**kwargs) -> None:
    """Update LLM health status fields."""
    _llm_health.update(kwargs)


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
            "created_at": r.created_at,
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
        db.add(
            LlmBoostCache(
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
            )
        )
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
    now = datetime.now(timezone.utc)
    for s in specials:
        key = _cache_key(s.get("title", ""), s.get("boosted_odds", 0), s.get("event", ""))
        prev = cache.get(key)
        if prev and prev.get("llm_probability"):
            # TTL check — skip stale entries so they get re-researched
            created_str = prev.get("created_at")
            if created_str:
                try:
                    created_dt = datetime.fromisoformat(created_str)
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    age_hours = (now - created_dt).total_seconds() / 3600
                    if age_hours > CACHE_TTL_HOURS:
                        continue
                except (ValueError, TypeError):
                    pass  # Can't parse — carry forward anyway
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
            # Apply bookmaker-anchor sanity check to cached results too
            _apply_bookmaker_anchor(s)
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


# ── LLM prompt ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a sports betting probability analyst. Given a boosted odds offer, estimate the TRUE probability of the outcome occurring using your knowledge. Also identify when the event takes place.

RULES:
- Base your estimate on statistics, recent form, and market context
- Be CONSERVATIVE — overestimating probability loses money in betting. When uncertain, lean toward lower probability
- For player props (goalscorer, assists, etc.), use base rates and player statistics
- Express your probability as a decimal between 0.01 and 0.99
- Determine the event start time from event context or league schedules
- HOME/AWAY CONTEXT: When HOME TEAM and AWAY TEAM are provided, use them to correctly assess home advantage. The boost SELECTION team may be home or away — check which one before adding/subtracting home advantage. Getting this wrong flips your estimate by ~10-15pp
- BOOKMAKER ANCHOR: The ORIGINAL ODDS reflect the bookmaker's probability estimate (with ~5-10% margin). Your estimate should NOT differ from the bookmaker implied probability by more than 15 percentage points. If you estimate 65% but the bookmaker implies 30%, you are almost certainly wrong — recheck your analysis
- STAT DISAMBIGUATION: Read market labels carefully. "Shots on target" (skott på mål) ≠ "total shots" — shots on target is typically 30-40% of total shots. "Corners" ≠ "goals". Always verify you are analyzing the EXACT stat in the market title

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


def _build_user_prompt(special: dict) -> str:
    title = special.get("title", "")
    event = special.get("event", "")
    sport = special.get("sport", "")
    league = special.get("league", "")
    market_label = special.get("market_label", "")
    boosted_odds = special.get("boosted_odds", 0)
    original_odds = special.get("original_odds")
    boost_pct = special.get("boost_pct")
    event_time = special.get("event_time")

    matched_event_id = special.get("matched_event_id") or ""

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = [
        f"TODAY'S DATE: {today}",
        f"BOOST TITLE: {title}",
        f"EVENT: {event}",
        f"SPORT: {sport}" + (f" ({league})" if league else ""),
    ]
    # Extract home/away from matched event ID (format: sport:home:away:date)
    if matched_event_id:
        eid_parts = matched_event_id.split(":")
        if len(eid_parts) >= 3:
            home_team = eid_parts[1].replace("_", " ").title()
            away_team = eid_parts[2].replace("_", " ").title()
            parts.append(f"HOME TEAM: {home_team}")
            parts.append(f"AWAY TEAM: {away_team}")
    if market_label:
        parts.append(f"MARKET: {market_label}")
    if event_time:
        parts.append(f"EVENT TIME (from scraper): {event_time}")
    parts.append(f"BOOSTED ODDS: {boosted_odds}")
    if original_odds:
        parts.append(f"ORIGINAL ODDS: {original_odds} (bookmaker implied: {100 / original_odds:.0f}%)")
    if boost_pct:
        parts.append(f"BOOST PERCENTAGE: +{boost_pct:.0f}%")

    parts.append("\nEstimate the true probability and identify the event start time.")
    return "\n".join(parts)


# ── Response parsing ───────────────────────────────────────────────────

_TITLE_RE = re.compile(r"TITLE:\s*(.+)", re.IGNORECASE)
_EVENT_TIME_RE = re.compile(r"EVENT_TIME:\s*(\S+)", re.IGNORECASE)
_LEGS_RE = re.compile(r"LEGS:\s*(\d+)", re.IGNORECASE)
_PROB_RE = re.compile(r"PROBABILITY:\s*(0\.\d+)", re.IGNORECASE)
_CONF_RE = re.compile(r"CONFIDENCE:\s*(low|medium|high)", re.IGNORECASE)
_LEG_PROB_RE = re.compile(r"Leg\s*\d+:.*?p\s*=\s*(0\.\d+)", re.IGNORECASE)
# Fallback: catch free-form multiplication patterns like "~0.65 × 0.70" or "0.55 * 0.48"
_MULT_PROB_RE = re.compile(r"~?(0\.\d+)\s*[×x\*]\s*~?(0\.\d+)", re.IGNORECASE)
_REASONING_RE = re.compile(r"REASONING:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _parse_event_time(raw: str) -> str | None:
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

    # Fallback: extract from free-form multiplication patterns (e.g. "~0.65 × 0.70")
    if len(leg_probs) < 2:
        mult_match = _MULT_PROB_RE.search(text)
        if mult_match:
            leg_probs = [float(mult_match.group(1)), float(mult_match.group(2))]

    if len(leg_probs) < 2:
        # No structured legs found — check if probability seems too high for a combo.
        # A 2-leg combo should rarely exceed 0.35; 3-leg rarely exceed 0.15.
        max_reasonable = {2: 0.35, 3: 0.15}.get(legs, 0.12)
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


# ── Bookmaker-anchor sanity check ─────────────────────────────────────

# Maximum allowed deviation (in percentage points) between LLM implied
# probability and bookmaker implied probability.  If the LLM says 65%
# but the bookmaker implies 30%, the gap is 35pp — way over the limit.
_MAX_PROB_DEVIATION_PP = 15  # percentage points

# Hard cap on edge — anything above this is almost certainly a bad estimate.
_MAX_EDGE_PCT = 80.0


def _apply_bookmaker_anchor(special: dict) -> dict:
    """Clamp LLM probability / edge using the bookmaker's original odds as anchor.

    If the LLM's implied probability deviates too far from the bookmaker's,
    blend toward the bookmaker estimate and downgrade confidence.
    Returns the (possibly modified) special dict.
    """
    llm_prob = special.get("llm_probability")
    original_odds = special.get("original_odds")
    boosted_odds = special.get("boosted_odds")

    if not llm_prob or not original_odds or original_odds <= 1.0 or not boosted_odds:
        return special

    bookie_implied = 1.0 / original_odds  # includes margin
    deviation_pp = (llm_prob - bookie_implied) * 100

    if deviation_pp > _MAX_PROB_DEVIATION_PP:
        # LLM thinks it's much more likely than the bookmaker — almost certainly wrong.
        # Blend: take the bookmaker implied + half the max allowed deviation.
        clamped_prob = bookie_implied + (_MAX_PROB_DEVIATION_PP / 100)
        clamped_prob = min(clamped_prob, 0.95)

        logger.info(
            f"Bookmaker anchor: '{special.get('title', '')[:50]}' "
            f"LLM prob {llm_prob:.2f} >> bookie implied {bookie_implied:.2f} "
            f"(gap {deviation_pp:.0f}pp) — clamping to {clamped_prob:.3f}"
        )
        special["llm_probability"] = round(clamped_prob, 4)
        special["llm_fair_odds"] = round(1 / clamped_prob, 3)
        special["llm_edge_pct"] = round((boosted_odds / (1 / clamped_prob) - 1) * 100, 2)
        special["llm_confidence"] = "low"

    # Hard cap on edge regardless
    if special.get("llm_edge_pct") and special["llm_edge_pct"] > _MAX_EDGE_PCT:
        # Re-derive from max allowed edge
        max_fair = boosted_odds / (1 + _MAX_EDGE_PCT / 100)
        capped_prob = 1.0 / max_fair
        logger.info(
            f"Edge cap: '{special.get('title', '')[:50]}' "
            f"edge {special['llm_edge_pct']:.0f}% > {_MAX_EDGE_PCT}% — "
            f"capping to {_MAX_EDGE_PCT}%"
        )
        special["llm_probability"] = round(capped_prob, 4)
        special["llm_fair_odds"] = round(max_fair, 3)
        special["llm_edge_pct"] = round(_MAX_EDGE_PCT, 2)
        special["llm_confidence"] = "low"

    return special


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
    if re.search(r"1x2:.*,\s*(totalt|båda)", title_lower):
        return 2

    # Explicit conjunctions: " & ", " och ", " and "
    conjunction_count = title_lower.count(" & ") + title_lower.count(" och ") + title_lower.count(" and ")
    if conjunction_count > 0:
        return conjunction_count + 1

    return 1


def _parse_llm_response(text: str, boost_title: str = "") -> dict | None:
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
            title = title[: title.index(marker)].strip()

    event_time_match = _EVENT_TIME_RE.search(text)
    event_time = _parse_event_time(event_time_match.group(1)) if event_time_match else None

    # Determine number of legs — title heuristic takes precedence over LLM declaration
    # because the LLM often says LEGS: 1 for combo bets (treating them as single outcomes)
    title_legs = _detect_legs_from_title(boost_title)
    if title_legs > 1:
        legs = title_legs
    else:
        legs_match = _LEGS_RE.search(text)
        legs = int(legs_match.group(1)) if legs_match else 1

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

_anthropic_dead = False  # short-circuit when Anthropic API is confirmed dead
_anthropic_error_msg: str | None = None  # error message from API


async def _research_single_boost(
    special: dict,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    global _anthropic_dead, _anthropic_error_msg
    async with semaphore:
        if _anthropic_dead:
            return None
        title = special.get("title", "")[:60]
        try:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                _anthropic_dead = True
                _update_health(anthropic_status="missing_key", last_error="ANTHROPIC_API_KEY not set")
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
                    "messages": [{"role": "user", "content": _build_user_prompt(special)}],
                },
                timeout=30.0,
            )

            if response.status_code != 200:
                # Parse error details from response
                try:
                    err_data = response.json()
                    err_msg = err_data.get("error", {}).get("message", f"HTTP {response.status_code}")
                except Exception:
                    err_msg = f"HTTP {response.status_code}"

                # Detect fatal errors — stop wasting API calls
                if response.status_code == 400 and "usage limit" in err_msg.lower():
                    _anthropic_dead = True
                    _anthropic_error_msg = err_msg
                    _update_health(anthropic_status="usage_limit", last_error=err_msg)
                    logger.error(f"Anthropic API usage limit reached — disabling LLM enrichment: {err_msg}")
                elif response.status_code in (401, 403):
                    _anthropic_dead = True
                    _anthropic_error_msg = err_msg
                    _update_health(anthropic_status="auth_error", last_error=err_msg)
                    logger.error(f"Anthropic API auth error — disabling: {err_msg}")
                elif response.status_code == 429:
                    _anthropic_dead = True
                    _anthropic_error_msg = err_msg
                    _update_health(anthropic_status="rate_limited", last_error=err_msg)
                    logger.warning(f"Anthropic API rate limited — disabling: {err_msg}")
                else:
                    logger.warning(f"LLM API error {response.status_code} for: {title}")

                return None

            _update_health(anthropic_status="ok")
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
    """Delete cached LLM results for combo boosts where probability is too high,
    or where the LLM probability deviates too far from bookmaker implied odds.

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
        prob = prev["llm_probability"]

        # Check 1: combo probability too high
        legs = _detect_legs_from_title(title)
        if legs > 1:
            max_reasonable = {2: 0.35, 3: 0.15}.get(legs, 0.12)
            if prob > max_reasonable:
                logger.info(
                    f"Invalidating cached combo: '{title[:50]}' p={prob:.2f} (>{max_reasonable} for {legs}-leg combo)"
                )
                keys_to_delete.append(key)
                if key in cache:
                    del cache[key]
                continue

        # Check 2: LLM probability deviates too far from bookmaker
        original_odds = s.get("original_odds")
        if original_odds and original_odds > 1.0:
            bookie_implied = 1.0 / original_odds
            deviation_pp = (prob - bookie_implied) * 100
            if deviation_pp > _MAX_PROB_DEVIATION_PP:
                logger.info(
                    f"Invalidating cached anchor-breach: '{title[:50]}' "
                    f"LLM p={prob:.2f} vs bookie {bookie_implied:.2f} "
                    f"(gap {deviation_pp:.0f}pp)"
                )
                keys_to_delete.append(key)
                if key in cache:
                    del cache[key]
                continue

    if keys_to_delete:
        try:
            db.query(LlmBoostCache).filter(LlmBoostCache.cache_key.in_(keys_to_delete)).delete(
                synchronize_session=False
            )
            db.commit()
            logger.info(f"Invalidated {len(keys_to_delete)} bad cache entries (combo + anchor)")
        except Exception:
            db.rollback()


# ── Main entry point ───────────────────────────────────────────────────


async def enrich_specials_with_llm(specials: list[dict], db: Session | None = None) -> list[dict]:
    """LLM-based probability estimation and event time extraction for boosts.

    Runs AFTER enrich_specials_with_ev() (which sets edge_pct = boost_pct).
    Processes ALL boosts with valid boosted_odds. Also extracts event_time
    when not available from the scraper.

    Uses the persistent `llm_boost_cache` table to avoid re-researching
    boosts that have already been analyzed. Each boost is researched once
    and the result is saved permanently.

    Skipped if ANTHROPIC_API_KEY is not set.
    """
    global _anthropic_dead, _anthropic_error_msg
    now_iso = datetime.now(timezone.utc).isoformat()

    # Reset per-run short-circuit flags (allow retry each scrape cycle)
    _anthropic_dead = False
    _anthropic_error_msg = None

    _update_health(last_run_at=now_iso)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("LLM enrichment skipped: ANTHROPIC_API_KEY not set")
        _update_health(status="skipped", anthropic_status="missing_key", last_error="ANTHROPIC_API_KEY not set")
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
        _update_health(status="ok", carried_count=carried, enriched_count=0, candidate_count=0)
        if carried > 0:
            _update_health(last_success_at=now_iso)
        return specials

    # Prioritize highest edge/boost, cap to MAX_BOOSTS_PER_RUN
    candidates.sort(
        key=lambda s: s.get("edge_pct") or s.get("boost_pct") or 0,
        reverse=True,
    )
    candidates = candidates[:MAX_BOOSTS_PER_RUN]

    logger.info(f"LLM enrichment: researching {len(candidates)} new boosts, {carried} carried from cache")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM)
    enriched_count = 0

    async with httpx.AsyncClient() as client:
        tasks = [_research_single_boost(s, client, semaphore) for s in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for special, result in zip(candidates, results, strict=False):
            if isinstance(result, Exception) or result is None:
                continue

            probability = result["probability"]
            confidence = result.get("confidence", "low")
            boosted_odds = special.get("boosted_odds", 0)

            # ML boost calibration (M4) — best-effort
            try:
                from src.ml.serving.predictor import get_predictor

                predictor = get_predictor()
                if predictor.is_loaded("boost_calibrator"):
                    from src.ml.features.boost_features import extract_boost_features

                    num_legs = _detect_legs_from_title(special.get("title", ""))
                    cal_features = extract_boost_features(
                        llm_raw_probability=probability,
                        llm_confidence=confidence,
                        boost_type="combo" if num_legs > 1 else "single",
                        sport=special.get("sport", ""),
                        league=special.get("league", ""),
                        num_legs=num_legs,
                        has_pinnacle_match=False,
                        pinnacle_implied_prob=None,
                        original_odds=special.get("original_odds") or 0,
                        boosted_odds=boosted_odds,
                        provider=special.get("provider", ""),
                        hours_to_event=0,
                        llm_reasoning_length=len(result.get("reasoning") or ""),
                    )
                    calibrated = predictor.predict("boost_calibrator", cal_features)
                    if calibrated is not None:
                        probability = calibrated
            except Exception:
                pass

            # Log features for M4 training (best-effort)
            try:
                from src.ml.feature_store import log_features as _log_ml_features
                from src.ml.features.boost_features import extract_boost_features as _extract_bf

                _num_legs = _detect_legs_from_title(special.get("title", ""))
                _cal_features = _extract_bf(
                    llm_raw_probability=result["probability"],
                    llm_confidence=confidence,
                    boost_type="combo" if _num_legs > 1 else "single",
                    sport=special.get("sport", ""),
                    league=special.get("league", ""),
                    num_legs=_num_legs,
                    has_pinnacle_match=False,
                    pinnacle_implied_prob=None,
                    original_odds=special.get("original_odds") or 0,
                    boosted_odds=boosted_odds,
                    provider=special.get("provider", ""),
                    hours_to_event=0,
                    llm_reasoning_length=len(result.get("reasoning") or ""),
                )
                if db:
                    _log_ml_features(db, "betting", str(special.get("title", "")), "boost", _cal_features)
            except Exception:
                pass

            fair_odds = round(1 / probability, 3)

            if fair_odds <= 1.0 or boosted_odds <= 1.0:
                continue

            edge_pct = round((boosted_odds / fair_odds - 1) * 100, 2)

            special["llm_title"] = result.get("title") or ""
            special["llm_probability"] = round(probability, 4)
            special["llm_fair_odds"] = fair_odds
            special["llm_edge_pct"] = edge_pct
            special["llm_reasoning"] = result["reasoning"]
            special["llm_confidence"] = result["confidence"]
            # Apply bookmaker-anchor sanity check
            _apply_bookmaker_anchor(special)
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

    # Update health status based on results
    _update_health(
        enriched_count=enriched_count,
        carried_count=carried,
        candidate_count=len(candidates),
    )
    if enriched_count > 0:
        _update_health(status="ok", last_success_at=now_iso, last_error=None)
    elif _anthropic_dead:
        _update_health(status="error", last_error=_anthropic_error_msg or "Anthropic API unavailable")
    elif enriched_count == 0 and len(candidates) > 0:
        _update_health(status="error", last_error=f"0/{len(candidates)} boosts enriched — all API calls failed")
    else:
        # No candidates and no errors — clear any stale error from previous run
        _update_health(status="ok", last_error=None)
    return specials
