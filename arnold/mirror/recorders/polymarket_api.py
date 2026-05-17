"""Polymarket bet recorder via public data-api.

Hits https://data-api.polymarket.com/positions?user=<wallet>&sizeThreshold=.1
which returns user's open positions WITHOUT auth (wallet address is the key).

For each position:
1. Compute decimal odds from avgPrice using the same fee formula as the
   polymarket extractor (so stored odds are POST-fee).
2. Compute USDC stake = avgPrice × size.
3. Match the position's market title against arnold's events table to find
   event_id + map outcome to home/away.
4. POST to /api/bets with external_placement=True (skips balance check).

Replaces the DOM-scraping flow in workflows/strategies/polymarket._scrape_portfolio.
Far more reliable: JSON response is stable, no React hydration race, outcome
arrives as a team name (not "Yes"/"No").
"""

from __future__ import annotations

import logging
import re

import httpx

from .types import RecorderResult, RecoveredPosition

logger = logging.getLogger(__name__)

POLY_API = "https://data-api.polymarket.com/positions"
POLY_FEE_RATE = 0.02
DEFAULT_SIZE_THRESHOLD = 0.1
DEFAULT_LIMIT = 50


def _fee_adjusted_odds(price: float) -> float:
    """Same formula as backend.providers.polymarket._price_to_odds."""
    if price <= 0.01 or price >= 0.99:
        return 1.01
    raw = 1.0 / price
    return round(1 + (raw - 1) * (1 - POLY_FEE_RATE), 4)


async def fetch_open_positions(wallet: str) -> list[RecoveredPosition]:
    """Hit poly data-api and parse into RecoveredPosition list."""
    url = f"{POLY_API}?user={wallet}&sizeThreshold={DEFAULT_SIZE_THRESHOLD}&limit={DEFAULT_LIMIT}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        try:
            r = await client.get(url)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            logger.warning(f"[polymarket_api] positions fetch failed: {type(e).__name__}: {e}")
            return []

    out: list[RecoveredPosition] = []
    for p in payload or []:
        try:
            avg = float(p.get("avgPrice") or 0)
            size = float(p.get("size") or 0)
            if avg <= 0 or size <= 0:
                continue
            out.append(
                RecoveredPosition(
                    provider_id="polymarket",
                    provider_bet_id=(p.get("conditionId") or "")[:60],
                    event_name=(p.get("title") or "")[:120],
                    outcome_name=p.get("outcome") or "",
                    odds=_fee_adjusted_odds(avg),
                    stake=round(avg * size, 2),
                    currency="USDC",
                    raw=p,
                )
            )
        except Exception as e:
            logger.warning(f"[polymarket_api] skipped position {p.get('title', '')[:40]}: {e}")
    return out


# ── Event matching ──
# Given a polymarket market title + outcome name, find matching arnold event_id
# and map outcome to home/away. Uses team-name fuzzy match: both teams must
# appear in the title, then outcome_name is compared to home/away to pick side.

_STOP = {"vs", "v", "the", "fc", "cf", "sc", "fk", "ec", "esports"}


def _tokens(s: str) -> set[str]:
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return {t for t in s.split() if t and len(t) >= 3 and t not in _STOP}


def _match_outcome(outcome_name: str, home: str, away: str) -> str | None:
    """Map outcome_name to 'home' or 'away' based on team-name substring."""
    on = (outcome_name or "").lower()
    h, a = (home or "").lower(), (away or "").lower()
    if not on or not h or not a:
        return None
    # Exact substring (either direction)
    if h and (h in on or on in h):
        return "home"
    if a and (a in on or on in a):
        return "away"
    # Token overlap fallback
    ton = _tokens(outcome_name)
    th, ta = _tokens(home), _tokens(away)
    if th and ton & th:
        return "home"
    if ta and ton & ta:
        return "away"
    return None


def match_event_and_outcome(
    position: RecoveredPosition,
    events: list[dict],
) -> tuple[str | None, str | None]:
    """Find best-matching event_id + outcome side for this position.

    events: list of dicts with {id, home_team, away_team}. Pre-filtered by
    caller to recent/upcoming events to keep the search space small.
    """
    title = (position.event_name or "").lower()
    if not title:
        return None, None

    best: tuple[int, str, str] | None = None
    for ev in events:
        home = (ev.get("home_team") or "").lower()
        away = (ev.get("away_team") or "").lower()
        if not home or not away:
            continue
        # Title must contain BOTH team names (anchor)
        if home not in title or away not in title:
            continue
        side = _match_outcome(position.outcome_name, home, away)
        if not side:
            continue
        score = len(home) + len(away)
        if best is None or score > best[0]:
            best = (score, ev["id"], side)

    if best:
        return best[1], best[2]
    return None, None


# ── End-to-end sync ──


async def sync(
    wallet: str,
    api_post,  # async callable(payload: dict) -> response
    fetch_events,  # async callable() -> list[{id, home_team, away_team}]
    fetch_db_pending,  # async callable() -> list[{provider_bet_id, event_id, outcome, odds, stake}]
) -> RecorderResult:
    """Full sync: fetch poly positions, dedup against DB, insert new ones."""
    result = RecorderResult(provider_id="polymarket")

    positions = await fetch_open_positions(wallet)
    result.fetched = len(positions)
    if not positions:
        return result

    events = await fetch_events() or []
    db_pending = await fetch_db_pending() or []

    known_ids = {b.get("provider_bet_id") for b in db_pending if b.get("provider_bet_id")}
    known_sigs = {
        (b.get("event_id"), b.get("outcome")): b for b in db_pending if b.get("event_id") and b.get("outcome")
    }

    for pos in positions:
        # Dedup by conditionId (preferred — stable provider id)
        if pos.provider_bet_id and pos.provider_bet_id in known_ids:
            result.skipped_dup += 1
            continue

        event_id, outcome = match_event_and_outcome(pos, events)
        if not event_id or not outcome:
            result.skipped_unmatched += 1
            logger.info(
                f"[polymarket_api] unmatched position: {pos.event_name[:60]} / "
                f"outcome={pos.outcome_name} — inserted with empty event_id"
            )

        # Dedup by (event_id, outcome) — same market same side
        if event_id and outcome and (event_id, outcome) in known_sigs:
            result.skipped_dup += 1
            continue

        payload = {
            "provider_id": "polymarket",
            "event_id": event_id or "",
            "market": "moneyline",
            "outcome": outcome or "",
            "odds": pos.odds,
            "stake": pos.stake,
            "external_placement": True,
            "boost_event": pos.event_name,
            "provider_bet_id": pos.provider_bet_id or None,
            "bet_type": "arb_counter",  # Polymarket positions in your stack are arb counters
        }

        try:
            resp = await api_post(payload)
            if resp.status_code in (200, 201):
                result.inserted += 1
            else:
                msg = f"{resp.status_code}: {(resp.text or '')[:200]}"
                result.errors.append(f"{pos.event_name[:40]}: {msg}")
                logger.warning(f"[polymarket_api] insert failed {pos.event_name[:40]}: {msg}")
        except Exception as e:
            result.errors.append(f"{pos.event_name[:40]}: {type(e).__name__}: {e}")
            logger.warning(f"[polymarket_api] insert exception {pos.event_name[:40]}: {e}")

    logger.info(f"[polymarket_api] {result.summary()}")
    return result
