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
# The signed message includes the FULL URL path (with `/trade-api/v2` prefix),
# not just the resource path. Signing the resource-only path returns 401
# INCORRECT_API_KEY_SIGNATURE — looked like bad creds but was a signing bug.
KALSHI_SIGN_PREFIX = "/trade-api/v2"


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
    msg = ts_ms + method.upper() + KALSHI_SIGN_PREFIX + path
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

    Kalshi position fields (as of API v2, 2026+):
      ticker: market ticker (e.g. "KXNBA-26MAY17PHILA-DAL")
      position_fp: signed share count as decimal string (positive = YES, negative = NO)
      market_exposure_dollars: cost basis as decimal string in dollars
      total_traded_dollars: total volume traded in dollars
      fees_paid_dollars: dollars

    Field names with `_dollars` / `_fp` suffix are the post-2025 schema.
    Pre-suffix names (`position`, `market_exposure`) return None silently.
    """
    ticker = p.get("ticker") or ""
    raw_pos_float = float(p.get("position_fp") or 0)
    shares = abs(raw_pos_float)
    exposure_dollars = float(p.get("market_exposure_dollars") or 0)
    if shares <= 0 or exposure_dollars <= 0:
        return None

    # Side: YES if position > 0, NO if < 0
    side = "YES" if raw_pos_float > 0 else "NO"

    # avg_price (in dollars per share)
    avg_price = exposure_dollars / shares
    if avg_price <= 0 or avg_price >= 1:
        return None

    # Fee-adjusted odds (same formula as kalshi extractor)
    fee_rate = 0.07  # matches constants.KALSHI_FEE_RATE
    effective = avg_price + fee_rate * avg_price * (1.0 - avg_price)
    odds = round(1.0 / effective, 4) if effective > 0 else 1.01

    # Stake in USD
    stake = round(exposure_dollars, 2)

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


async def fetch_settlements() -> list[dict]:
    """Return raw settlement rows from /portfolio/settlements (paginated).

    Settlements are the canonical record of resolved markets. Fully-resolved
    positions disappear from /portfolio/positions (Kalshi cleans up after
    payout) — they only persist in /portfolio/settlements.

    Each row carries:
      ticker:                 market ticker
      market_result:          "yes" | "no" | "scalar"
      yes_count_fp:           decimal string — YES shares held at settlement
      no_count_fp:            decimal string — NO shares held at settlement
      yes_total_cost_dollars: decimal string — total cost spent on YES side
      no_total_cost_dollars:  decimal string — total cost spent on NO side
      revenue:                INTEGER cents — gross payout received
      fee_cost:               decimal string — fees paid in dollars
      value:                  scalar payout per share (for scalar markets)
      settled_time:           ISO timestamp
    """
    out: list[dict] = []
    cursor = None
    for _ in range(20):  # bounded paginate
        params: dict = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = await _get("/portfolio/settlements", params=params)
        if not data:
            break
        rows = data.get("settlements") or []
        out.extend(rows)
        cursor = data.get("cursor")
        if not cursor or not rows:
            break
    return out


async def settle(
    api_settle,  # async callable(bet_id, result, payout) -> response
    fetch_db_pending,
) -> dict:
    """Settle DB pending kalshi bets using /portfolio/settlements.

    For each pending bet, match by ticker (provider_bet_id). Determine
    result by comparing the bet's side (YES/NO inferred from which
    `*_count_fp` field is non-zero in the settlement row) against
    `market_result`.

    Payout:
      - won  → revenue (cents → dollars)
      - lost → 0
      - scalar → revenue (Kalshi already credits partial payouts)
    """
    out = {"won": 0, "lost": 0, "skipped": 0, "errors": []}

    pending = await fetch_db_pending() or []
    if not pending:
        return out

    rows = await fetch_settlements()
    by_ticker = {(r.get("ticker") or "").strip(): r for r in rows if r.get("ticker")}

    for bet in pending:
        bet_id = bet.get("id")
        ticker = (bet.get("provider_bet_id") or "").strip()
        if not bet_id or not ticker:
            out["skipped"] += 1
            continue

        row = by_ticker.get(ticker)
        if row is None:
            # Not in settled set — still open or market hasn't resolved yet
            continue

        yes_ct = float(row.get("yes_count_fp") or 0)
        no_ct = float(row.get("no_count_fp") or 0)
        revenue_dollars = (row.get("revenue") or 0) / 100.0
        market_result = (row.get("market_result") or "").lower()

        # Infer which side the bet was on from non-zero count
        if yes_ct > 0:
            bet_side = "yes"
        elif no_ct > 0:
            bet_side = "no"
        else:
            # Both counts zero — position fully exited before settlement; skip.
            continue

        if market_result == "scalar":
            # Partial payout — revenue is gross return regardless of side label.
            result = "won" if revenue_dollars > 0 else "lost"
            payout = round(revenue_dollars, 2)
        elif bet_side == market_result:
            result = "won"
            payout = round(revenue_dollars, 2)
        else:
            result = "lost"
            payout = 0.0

        try:
            resp = await api_settle(bet_id, result, payout)
            if resp.status_code in (200, 201):
                if result == "won":
                    out["won"] += 1
                else:
                    out["lost"] += 1
                logger.info(f"[kalshi_api] settled bet {bet_id} ticker={ticker[:30]} → {result} payout=${payout:.2f}")
            else:
                msg = f"{resp.status_code}: {(resp.text or '')[:200]}"
                out["errors"].append(f"bet {bet_id}: {msg}")
        except Exception as e:
            out["errors"].append(f"bet {bet_id}: {type(e).__name__}: {e}")

    return out


async def sync(
    api_post,
    fetch_events,
    fetch_db_pending,
    fetch_known_ids=None,  # async callable() -> list[str] | None — ALL recorded tickers
) -> RecorderResult:
    """Fetch kalshi positions, dedup, insert. Mirror of polymarket sync."""
    result = RecorderResult(provider_id="kalshi")

    positions = await fetch_open_positions()
    result.fetched = len(positions)
    if not positions:
        return result

    events = await fetch_events() or []
    db_pending = await fetch_db_pending() or []
    # Dedup against ALL recorded tickers (any result), not just pending —
    # mirror of the polymarket fix. None = lookup failed → skip insert.
    if fetch_known_ids is not None:
        recorded = await fetch_known_ids()
        if recorded is None:
            logger.warning("[kalshi_api] fetch_known_ids failed — skipping insert pass (fail-closed)")
            result.errors.append("fetch_known_ids unavailable — insert skipped")
            return result
        known_ids = {c for c in recorded if c}
    else:
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
