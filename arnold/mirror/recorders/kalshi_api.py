"""Kalshi bet recorder via authenticated trade-api.

Hits https://api.elections.kalshi.com/trade-api/v2/portfolio/positions and
/markets /events with RSA-PSS-signed headers (KALSHI-ACCESS-KEY +
KALSHI-ACCESS-SIGNATURE + KALSHI-ACCESS-TIMESTAMP).

Required env vars (backend/.env):
- KALSHI_API_KEY: UUID-format access key from kalshi.com → Settings → API Keys
- KALSHI_PRIVATE_KEY: full PEM-encoded RSA private key. A multi-line PEM MUST
  be double-quoted in .env, otherwise python-dotenv reads only the first line.

Auth message: f"{timestamp_ms}{method}{path}" where `path` is the FULL request
path INCLUDING the /trade-api/v2 prefix — no body, no query string. Signing
only the bare path yields a 401 INCORRECT_API_KEY_SIGNATURE.
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

_API_PREFIX = "/trade-api/v2"
KALSHI_BASE = "https://api.elections.kalshi.com" + _API_PREFIX
_FEE_RATE = 0.07  # matches constants.KALSHI_FEE_RATE


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
    """Build the 3 required auth headers. Returns None if creds missing.

    `path` is the trade-api path WITHOUT the /trade-api/v2 prefix (e.g.
    "/portfolio/positions"); the prefix is added here because Kalshi signs the
    full URL path.
    """
    api_key = os.environ.get("KALSHI_API_KEY", "").strip()
    if not api_key:
        logger.warning("[kalshi_api] KALSHI_API_KEY env not set")
        return None
    ts_ms = str(int(time.time() * 1000))
    msg = ts_ms + method.upper() + _API_PREFIX + path
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
    """Authenticated GET. `path` excludes the /trade-api/v2 prefix. None on failure."""
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


def _kalshi_position_to_recovered(
    p: dict, market_meta: dict | None, event_meta: dict | None
) -> RecoveredPosition | None:
    """Convert a /portfolio/positions market_position row to RecoveredPosition.

    Current trade-api v2 position fields (all decimal strings):
      ticker:                  market ticker, e.g. "KXEREDIVISIETOTAL-...-1"
      position_fp:             signed fractional share count (+ = YES, - = NO)
      market_exposure_dollars: cost basis in dollars

    `market_meta` (/markets/{ticker}.market) supplies floor_strike + strike_type
    for total markets and the yes/no subtitles for moneyline. `event_meta`
    (/events/{ticker}.event) supplies the "Home vs Away: ..." title used for
    event matching.
    """
    market_meta = market_meta or {}
    event_meta = event_meta or {}

    ticker = p.get("ticker") or ""
    try:
        raw_pos = float(p.get("position_fp") or 0)
        exposure = float(p.get("market_exposure_dollars") or 0)
    except (TypeError, ValueError):
        return None
    shares = abs(raw_pos)
    if shares <= 0 or exposure <= 0:
        return None  # closed / settled / empty position

    side = "YES" if raw_pos > 0 else "NO"

    # avg_price (dollars per share) = cost basis / shares.
    avg_price = exposure / shares
    if not (0.0 < avg_price < 1.0):
        return None

    # Fee-adjusted decimal odds (same formula as the kalshi extractor).
    effective = avg_price + _FEE_RATE * avg_price * (1.0 - avg_price)
    odds = round(1.0 / effective, 4) if effective > 0 else 1.01
    stake = round(exposure, 2)

    # Event title for matching — prefer the /events title ("Ajax vs Groningen:
    # Total Goals"); fall back to the market title.
    event_title = (event_meta.get("title") or market_meta.get("title") or ticker).strip()

    floor_strike = market_meta.get("floor_strike")
    strike_type = (market_meta.get("strike_type") or "").strip().lower()

    if floor_strike is not None and strike_type in ("greater", "less"):
        # Total market. strike_type "greater" → YES resolves if total > strike
        # (YES = over, NO = under); "less" → the inverse.
        try:
            point = float(floor_strike)
        except (TypeError, ValueError):
            return None
        if strike_type == "greater":
            outcome = "over" if side == "YES" else "under"
        else:
            outcome = "under" if side == "YES" else "over"
        return RecoveredPosition(
            provider_id="kalshi",
            provider_bet_id=ticker[:60],
            event_name=event_title[:120],
            outcome_name=outcome,
            odds=odds,
            stake=stake,
            currency="USD",
            raw=p,
            market_kind="total",
            point=point,
        )

    # Moneyline market — outcome is the team subtitle for the held side.
    yes_sub = market_meta.get("yes_sub_title") or ""
    no_sub = market_meta.get("no_sub_title") or ""
    outcome_name = (yes_sub if side == "YES" else no_sub) or side
    return RecoveredPosition(
        provider_id="kalshi",
        provider_bet_id=ticker[:60],
        event_name=event_title[:120],
        outcome_name=outcome_name,
        odds=odds,
        stake=stake,
        currency="USD",
        raw=p,
        market_kind="moneyline",
    )


async def fetch_open_positions() -> list[RecoveredPosition]:
    """Fetch open positions, enrich each with market + event metadata."""
    data = await _get(
        "/portfolio/positions",
        params={"limit": 100, "settlement_status": "unsettled"},
    )
    if not data:
        return []
    positions = data.get("market_positions") or []
    if not positions:
        return []

    event_cache: dict[str, dict] = {}
    out: list[RecoveredPosition] = []
    for p in positions:
        ticker = p.get("ticker") or ""
        if not ticker:
            continue
        # Skip closed/zero positions before spending two API calls on them.
        try:
            if abs(float(p.get("position_fp") or 0)) <= 0:
                continue
        except (TypeError, ValueError):
            continue

        market_meta: dict = {}
        m_resp = await _get(f"/markets/{ticker}")
        if m_resp:
            market_meta = m_resp.get("market") or {}

        event_meta: dict = {}
        event_ticker = market_meta.get("event_ticker") or ""
        if event_ticker:
            if event_ticker not in event_cache:
                e_resp = await _get(f"/events/{event_ticker}")
                event_cache[event_ticker] = (e_resp or {}).get("event") or {}
            event_meta = event_cache[event_ticker]

        rp = _kalshi_position_to_recovered(p, market_meta, event_meta)
        if rp:
            out.append(rp)
    return out


# ── Event matching ── (reuse polymarket's name-substring helper) ──

from .polymarket_api import _match_outcome  # noqa: E402


def match_event_and_outcome(
    position: RecoveredPosition,
    events: list[dict],
) -> tuple[str | None, str | None]:
    """Find the best matching event_id + outcome for this position.

    Total markets: match the event by team names (BOTH home and away must
    appear in the title — "ajax" alone also matches "Ajax Sarkkiranta"), and
    the outcome is the already-resolved "over"/"under".

    Moneyline markets: match a team name then map it to home/away via
    _match_outcome (polymarket's shared algorithm).
    """
    haystack = f"{position.event_name} {position.outcome_name}".lower()
    if not haystack.strip():
        return None, None

    if position.market_kind == "total":
        best: tuple[int, str] | None = None
        for ev in events:
            home = (ev.get("home_team") or "").lower()
            away = (ev.get("away_team") or "").lower()
            if not home or not away:
                continue
            if home not in haystack or away not in haystack:
                continue
            score = len(home) + len(away)
            if best is None or score > best[0]:
                best = (score, ev["id"])
        return (best[1], position.outcome_name) if best else (None, None)

    # Moneyline
    best_ml: tuple[int, str, str] | None = None
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
        if best_ml is None or score > best_ml[0]:
            best_ml = (score, ev["id"], side)
    return (best_ml[1], best_ml[2]) if best_ml else (None, None)


async def fetch_settled_positions() -> list[dict]:
    """Return raw settled market_position rows.

    Each row carries `ticker` and `realized_pnl_dollars` (decimal-dollar
    string — positive = won, ≤ 0 = lost). The settlement_status=settled filter
    captures only positions where the market resolved.
    """
    data = await _get(
        "/portfolio/positions",
        params={"limit": 200, "settlement_status": "settled"},
    )
    if not data:
        return []
    return data.get("market_positions") or []


async def settle(
    api_settle,  # async callable(bet_id, result, payout) -> response
    fetch_db_pending,
) -> dict:
    """Settle DB pending kalshi bets using the settled-positions endpoint.

    For each pending bet, match by ticker (provider_bet_id) and read
    realized_pnl_dollars from the settled position:
      - realized_pnl > 0 → WON, payout = stake + realized_pnl
      - realized_pnl ≤ 0 → LOST, payout = 0
    Skips bets without a provider_bet_id (legacy rows recorded pre-API path).
    """
    out: dict[str, Any] = {"won": 0, "lost": 0, "skipped": 0, "errors": []}

    pending = await fetch_db_pending() or []
    if not pending:
        return out

    settled_rows = await fetch_settled_positions()
    by_ticker = {(r.get("ticker") or "").strip(): r for r in settled_rows if r.get("ticker")}

    for bet in pending:
        bet_id = bet.get("id")
        ticker = (bet.get("provider_bet_id") or "").strip()
        stake = float(bet.get("stake") or 0)
        if not bet_id or not ticker:
            out["skipped"] += 1
            continue

        row = by_ticker.get(ticker)
        if row is None:
            # Not in the settled set — still open or market hasn't resolved.
            continue

        try:
            realized_pnl = float(row.get("realized_pnl_dollars") or 0)
        except (TypeError, ValueError):
            realized_pnl = 0.0
        if realized_pnl > 0:
            result = "won"
            payout = round(stake + realized_pnl, 2)
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
    """Fetch kalshi positions, dedup, insert. Mirror of the polymarket sync."""
    result = RecorderResult(provider_id="kalshi")

    # Fail loudly on missing/invalid credentials. A silent fetched=0 is
    # indistinguishable from "no open positions" and hid a fully broken
    # recorder (0 bets ever) — surface it in result.errors instead.
    if _auth_headers("GET", "/portfolio/positions") is None:
        msg = "kalshi auth unavailable — KALSHI_API_KEY / KALSHI_PRIVATE_KEY missing or invalid"
        logger.warning(f"[kalshi_api] {msg}")
        result.errors.append(msg)
        return result

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
            # No arnold-event match — but this is a REAL open position, so it
            # MUST still be recorded (same fallback as the polymarket
            # recorder). Gating the insert on a match silently drops bets the
            # user placed. For totals the outcome is already known
            # ("over"/"under"); an unmatched moneyline side stays blank.
            result.skipped_unmatched += 1
            logger.info(
                f"[kalshi_api] unmatched: {pos.event_name[:60]} / "
                f"outcome={pos.outcome_name} — recording with null event_id"
            )
            event_id = ""
            outcome = pos.outcome_name if pos.market_kind == "total" else ""

        payload: dict[str, Any] = {
            "provider_id": "kalshi",
            "event_id": event_id or "",
            "market": pos.market_kind,
            "outcome": outcome or "",
            "odds": pos.odds,
            "stake": pos.stake,
            "external_placement": True,
            "boost_event": pos.event_name,
            "provider_bet_id": pos.provider_bet_id or None,
            "bet_type": "arb_counter",
        }
        if pos.point is not None:
            payload["point"] = pos.point
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
