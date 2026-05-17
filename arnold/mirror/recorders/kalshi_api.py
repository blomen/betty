"""Kalshi bet recorder via authenticated trade-api.

Hits https://api.elections.kalshi.com/trade-api/v2/portfolio/positions and /fills
with RSA-PSS-signed headers (KALSHI-ACCESS-KEY + KALSHI-ACCESS-SIGNATURE +
KALSHI-ACCESS-TIMESTAMP).

Required env vars (backend/.env):
- KALSHI_API_KEY: UUID-format access key from kalshi.com → Settings → API Keys
- KALSHI_PRIVATE_KEY: full PEM-encoded RSA private key (with BEGIN/END markers)

Auth message: f"{timestamp_ms}{method}{path}" — no body, only path (no query).
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any

import httpx

from .types import RecorderResult, RecoveredPosition

logger = logging.getLogger(__name__)

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _load_private_key():
    """Lazy-load + cache the private key. Returns None if missing/invalid."""
    global _PRIVATE_KEY
    try:
        return _PRIVATE_KEY  # type: ignore[name-defined]
    except NameError:
        pass

    pem = os.environ.get("KALSHI_PRIVATE_KEY", "").strip()
    if not pem:
        logger.warning("[kalshi_api] KALSHI_PRIVATE_KEY env not set")
        _PRIVATE_KEY = None
        return None

    # The .env file may store the key with literal \n escape sequences (a single
    # line). Normalize to real newlines so cryptography can parse.
    if "\\n" in pem and "\n" not in pem:
        pem = pem.replace("\\n", "\n")

    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        key = load_pem_private_key(pem.encode("utf-8"), password=None)
    except Exception as e:
        logger.warning(f"[kalshi_api] failed to load private key: {type(e).__name__}: {e}")
        key = None

    _PRIVATE_KEY = key  # type: ignore[name-defined]
    return key


def _sign_message(msg: str) -> str | None:
    """Sign `msg` with RSA-PSS SHA256, return base64. None if key unavailable."""
    key = _load_private_key()
    if key is None:
        return None
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        sig = key.sign(
            msg.encode("utf-8"),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode("ascii")
    except Exception as e:
        logger.warning(f"[kalshi_api] sign failed: {type(e).__name__}: {e}")
        return None


def _auth_headers(method: str, path: str) -> dict[str, str] | None:
    """Build the 3 required auth headers. Returns None if creds missing."""
    api_key = os.environ.get("KALSHI_API_KEY", "").strip()
    if not api_key:
        logger.warning("[kalshi_api] KALSHI_API_KEY env not set")
        return None
    ts_ms = str(int(time.time() * 1000))
    msg = ts_ms + method.upper() + path
    sig = _sign_message(msg)
    if sig is None:
        return None
    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-SIGNATURE": sig,
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        "Accept": "application/json",
    }


async def _get(path: str, params: dict[str, Any] | None = None) -> dict | None:
    """Authenticated GET. Returns parsed JSON or None on failure."""
    headers = _auth_headers("GET", path)
    if headers is None:
        return None
    url = KALSHI_BASE + path
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        try:
            r = await client.get(url, headers=headers, params=params)
            if r.status_code != 200:
                logger.warning(f"[kalshi_api] GET {path} → {r.status_code}: {(r.text or '')[:200]}")
                return None
            return r.json()
        except Exception as e:
            logger.warning(f"[kalshi_api] GET {path} raised: {type(e).__name__}: {e}")
            return None


# ── Position fetch + parse ──


def _kalshi_position_to_recovered(p: dict, ticker_meta: dict | None) -> RecoveredPosition | None:
    """Convert a /portfolio/positions row to RecoveredPosition.

    Kalshi position fields:
      ticker: market ticker (e.g. "KXNBA-26MAY17PHILA-DAL")
      position: signed share count (positive = YES, negative = NO)
      market_exposure: cost basis in cents
      total_traded: total volume traded
      fees_paid: cents
    """
    ticker = p.get("ticker") or ""
    shares = abs(int(p.get("position") or 0))
    exposure_cents = int(p.get("market_exposure") or 0)
    if shares <= 0 or exposure_cents <= 0:
        return None

    # Side: YES if position > 0, NO if < 0
    raw_pos = int(p.get("position") or 0)
    side = "YES" if raw_pos > 0 else "NO"

    # avg_price (in dollars) = exposure / shares / 100
    avg_price = exposure_cents / shares / 100.0
    if avg_price <= 0 or avg_price >= 1:
        return None

    # Fee-adjusted odds (same formula as kalshi extractor)
    fee_rate = 0.07  # matches constants.KALSHI_FEE_RATE
    effective = avg_price + fee_rate * avg_price * (1.0 - avg_price)
    odds = round(1.0 / effective, 4) if effective > 0 else 1.01

    # Stake in USD = exposure_cents / 100
    stake = round(exposure_cents / 100.0, 2)

    # Event/outcome name from ticker_meta if available
    title = (ticker_meta or {}).get("title") or ticker
    yes_subtitle = (ticker_meta or {}).get("yes_sub_title") or ""
    no_subtitle = (ticker_meta or {}).get("no_sub_title") or ""
    outcome_name = yes_subtitle if side == "YES" else no_subtitle
    if not outcome_name:
        outcome_name = side  # fallback: literal "YES"/"NO"

    return RecoveredPosition(
        provider_id="kalshi",
        provider_bet_id=ticker[:60],  # use ticker as stable id
        event_name=title[:120],
        outcome_name=outcome_name,
        odds=odds,
        stake=stake,
        currency="USD",
        raw=p,
    )


async def fetch_open_positions() -> list[RecoveredPosition]:
    """Fetch all open positions, enrich with market metadata for outcome names."""
    data = await _get("/portfolio/positions", params={"limit": 100, "settlement_status": "unsettled"})
    if not data:
        return []
    positions = data.get("market_positions") or []
    if not positions:
        return []

    # Enrich each with /markets/{ticker} for the yes/no subtitle (real team names)
    out: list[RecoveredPosition] = []
    for p in positions:
        ticker = p.get("ticker") or ""
        meta = None
        if ticker:
            meta_resp = await _get(f"/markets/{ticker}")
            if meta_resp:
                meta = meta_resp.get("market") or {}
        rp = _kalshi_position_to_recovered(p, meta)
        if rp:
            out.append(rp)
    return out


# ── Event matching ── (reuse polymarket's by string fuzzy) ──

from .polymarket_api import _match_outcome  # noqa: E402


def match_event_and_outcome(
    position: RecoveredPosition,
    events: list[dict],
) -> tuple[str | None, str | None]:
    """Find best matching event_id + side. Same algorithm as polymarket.

    Kalshi titles look like 'Will the Philadelphia 76ers beat the Dallas Mavericks?'
    or 'Miomir Kecmanovic to win match'. We try BOTH the title and the outcome
    name (yes_sub_title) for team-name matches.
    """
    haystack = f"{position.event_name} {position.outcome_name}".lower()
    if not haystack.strip():
        return None, None

    best: tuple[int, str, str] | None = None
    for ev in events:
        home = (ev.get("home_team") or "").lower()
        away = (ev.get("away_team") or "").lower()
        if not home or not away:
            continue
        if home not in haystack and away not in haystack:
            continue
        side = _match_outcome(position.outcome_name, home, away)
        if not side:
            continue
        score = len(home) + len(away)
        if best is None or score > best[0]:
            best = (score, ev["id"], side)
    return (best[1], best[2]) if best else (None, None)


async def sync(
    api_post,
    fetch_events,
    fetch_db_pending,
) -> RecorderResult:
    """Fetch kalshi positions, dedup, insert. Mirror of polymarket sync."""
    result = RecorderResult(provider_id="kalshi")

    positions = await fetch_open_positions()
    result.fetched = len(positions)
    if not positions:
        return result

    events = await fetch_events() or []
    db_pending = await fetch_db_pending() or []
    known_ids = {b.get("provider_bet_id") for b in db_pending if b.get("provider_bet_id")}

    for pos in positions:
        if pos.provider_bet_id and pos.provider_bet_id in known_ids:
            result.skipped_dup += 1
            continue
        event_id, outcome = match_event_and_outcome(pos, events)
        if not event_id or not outcome:
            result.skipped_unmatched += 1
            logger.info(f"[kalshi_api] unmatched: {pos.event_name[:60]} / outcome={pos.outcome_name} — skipping")
            continue
        payload = {
            "provider_id": "kalshi",
            "event_id": event_id or "",
            "market": "moneyline",
            "outcome": outcome or "",
            "odds": pos.odds,
            "stake": pos.stake,
            "external_placement": True,
            "boost_event": pos.event_name,
            "provider_bet_id": pos.provider_bet_id or None,
            "bet_type": "arb_counter",
        }
        try:
            resp = await api_post(payload)
            if resp.status_code in (200, 201):
                result.inserted += 1
            else:
                result.errors.append(f"{pos.event_name[:40]}: {resp.status_code}: {(resp.text or '')[:200]}")
        except Exception as e:
            result.errors.append(f"{pos.event_name[:40]}: {type(e).__name__}: {e}")

    logger.info(f"[kalshi_api] {result.summary()}")
    return result
