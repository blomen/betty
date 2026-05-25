"""Cloudbet strategy — cookie-session web app.

Routed through GenericWorkflow + intel JSON like Polymarket / Pinnacle.
All authenticated calls use Playwright's request context so the session
cookie set by the web login is reused without us having to harvest it.

Discovery (2026-05-05) captured these endpoints on www.cloudbet.com:
  GET  /iam-me                            — login check + me.currency
  POST /iam-balances                      — multi-wallet balances + fiatAggregated USD
  GET  /sports-betting/v4/bets/positions  — bets (filter by status=ACCEPTED|COMPLETED)
  GET  /sports-api/v6/sports/events/{id}  — event detail (legacy)
  GET  /sports-api/v7/events/{id}         — event detail (current)

PLACEMENT ENDPOINT NOT YET DISCOVERED — placeholder TODO at the bottom.
The history `items[]` shape is also speculative (the discovery account had
zero historical bets); the parser is defensive and skips rows it can't read.
Both gaps unblock once the user places a probe bet on cloudbet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..base import HistoryEntry
from . import Strategy

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

_BASE = "https://www.cloudbet.com"
# /iam-me requires the requested_attributes[] hints — without them it 400s.
_IAM_ME_URL = (
    _BASE
    + "/iam-me"
    + "?requested_attributes[]=cf_ipcountry"
    + "&requested_attributes[]=cf_regioncode"
    + "&requested_attributes[]=email"
    + "&requested_attributes[]=currency"
)
_IAM_BALANCES_URL = _BASE + "/iam-balances"
_POSITIONS_URL = _BASE + "/sports-betting/v4/bets/positions"


def _common_headers() -> dict:
    return {
        "Accept": "application/json",
        "Origin": _BASE,
        "Referer": _BASE + "/en/",
    }


async def _api_get(page: Page, url: str) -> Any:
    try:
        resp = await page.context.request.get(url, headers=_common_headers())
        if resp.status < 200 or resp.status >= 400:
            return {"__error": resp.status}
        return await resp.json()
    except Exception as e:
        logger.warning(f"[cloudbet] GET {url} failed: {e}")
        return None


async def _api_post(page: Page, url: str, body: dict | None = None) -> Any:
    try:
        headers = {**_common_headers(), "Content-Type": "application/json"}
        resp = await page.context.request.post(url, data=body or {}, headers=headers)
        if resp.status < 200 or resp.status >= 400:
            return {"__error": resp.status}
        return await resp.json()
    except Exception as e:
        logger.warning(f"[cloudbet] POST {url} failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def _check_login(page: Page, intel: dict | None) -> bool:
    """Logged in iff /iam-me returns 200 with a populated me.id."""
    data = await _api_get(page, _IAM_ME_URL)
    if not isinstance(data, dict):
        return False
    if data.get("__error"):
        return False
    me = data.get("me") or {}
    me_id = me.get("id")
    if not isinstance(me_id, int) or me_id <= 0:
        return False
    logger.info(f"[cloudbet] Login confirmed: id={me_id} currency={me.get('currency') or '?'}")
    return True


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------


async def _sync_balance(page: Page, intel: dict | None) -> float:
    """Return USD-equivalent of all wallets (fiatAggregated).

    Cloudbet is multi-currency crypto — `me.currency` selects the user's
    primary wallet (BTC / ETH / USDT / etc) but their bankroll display
    uses the aggregated USD figure. The bankroll API stores currency-agnostic
    floats so fiatAggregated is the right one to surface.
    """
    data = await _api_post(page, _IAM_BALANCES_URL, body={})
    if not isinstance(data, dict) or data.get("__error"):
        return -1.0
    raw = data.get("fiatAggregated")
    try:
        bal = float(raw) if raw is not None else -1.0
        logger.info(f"[cloudbet] Balance (fiatAggregated USD): ${bal:.2f}")
        return bal
    except (TypeError, ValueError):
        logger.warning(f"[cloudbet] Cannot parse fiatAggregated: {raw!r}")
        return -1.0


async def _fetch_balance(page: Page, intel: dict | None) -> float | None:
    """Passive ready-state refresh — same as sync_balance, returns None on failure."""
    bal = await _sync_balance(page, intel)
    return bal if bal >= 0 else None


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

# Discovery account had zero bets — items[] was always empty. Field names
# below are best-guess based on Cloudbet's public Trading API docs and
# the URL ergonomics. _parse_position_item is defensive: anything it can't
# read drops to a sane default rather than throwing.
_STATUS_MAP = {
    "ACCEPTED": "pending",
    "PENDING": "pending",
    "OPEN": "pending",
    "WON": "won",
    "LOST": "lost",
    "VOID": "void",
    "REFUNDED": "void",
    "CASHED_OUT": "cashout",
    "CASHOUT": "cashout",
    "PUSH": "void",
    # Lower-case variants in case the API normalises:
    "accepted": "pending",
    "pending": "pending",
    "won": "won",
    "lost": "lost",
}


def _parse_position_item(raw: dict) -> HistoryEntry | None:
    """Best-effort mapping of one /positions item to a HistoryEntry.

    Cloudbet returns `items[]` whose exact shape we haven't verified yet
    (no historical bets on the discovery account). When we get a real
    capture, refine this — until then we accept multiple plausible field
    names and log+skip rows that can't be parsed at all.
    """
    try:
        bet_id = raw.get("id") or raw.get("betId") or raw.get("ticketId") or raw.get("referenceNumber") or ""
        # Selections / legs — single-leg bets typical for our 1x2/spread/total scope.
        sels = raw.get("selections") or raw.get("legs") or raw.get("outcomes") or []
        first = sels[0] if isinstance(sels, list) and sels else {}
        event_name = raw.get("eventName") or raw.get("event") or first.get("eventName") or first.get("event") or ""
        if not event_name:
            home = raw.get("home") or first.get("home") or ""
            away = raw.get("away") or first.get("away") or ""
            if home and away:
                event_name = f"{home} vs {away}"

        odds_raw = raw.get("price") or raw.get("odds") or first.get("price") or first.get("odds") or 0
        stake_raw = raw.get("stake") or raw.get("amount") or raw.get("stakeAmount") or 0
        payout_raw = raw.get("payout") or raw.get("returnAmount") or raw.get("winAmount")

        raw_status = str(raw.get("status") or raw.get("state") or "").strip()
        status = _STATUS_MAP.get(raw_status, raw_status.lower() or "pending")

        odds = float(odds_raw or 0)
        stake = float(stake_raw or 0)
        payout = float(payout_raw) if payout_raw is not None else None

        return HistoryEntry(
            provider_bet_id=str(bet_id),
            event_name=event_name[:120],
            market="",
            outcome="",
            odds=odds,
            stake=stake,
            status=status,
            payout=payout,
        )
    except (TypeError, ValueError, KeyError) as e:
        logger.debug(f"[cloudbet] skip unparseable position row ({e}): {str(raw)[:120]}")
        return None


async def _sync_history(page: Page, intel: dict | None) -> list[HistoryEntry]:
    """Fetch open + completed bets from /sports-betting/v4/bets/positions.

    Two-call merge: ACCEPTED for currently-open, COMPLETED for settled.
    Currency forced to USD so the response uses fiat-equivalents — matches
    what we record at placement time.
    """
    from datetime import datetime, timedelta, timezone

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    start_iso = start.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    open_url = f"{_POSITIONS_URL}?status=ACCEPTED"
    settled_url = f"{_POSITIONS_URL}?status=COMPLETED&from={start_iso}&limit=100&offset=0&currency=USD"

    entries: list[HistoryEntry] = []
    seen_ids: set[str] = set()
    for url in (open_url, settled_url):
        data = await _api_get(page, url)
        if not isinstance(data, dict) or data.get("__error"):
            continue
        items = data.get("items")
        if not isinstance(items, list):
            continue
        for raw in items:
            entry = _parse_position_item(raw if isinstance(raw, dict) else {})
            if entry is None:
                continue
            if entry.provider_bet_id and entry.provider_bet_id in seen_ids:
                continue
            if entry.provider_bet_id:
                seen_ids.add(entry.provider_bet_id)
            entries.append(entry)

    logger.info(f"[cloudbet] sync_history: {len(entries)} entries")
    return entries


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


async def _navigate_to_event(page: Page, bet, intel: dict | None) -> bool:
    """Navigate to a cloudbet event page.

    Prefers the URL stamped on the bet at extraction time
    (CloudbetRetriever sets event.url = /en/sports/{sport}/event/{event_id}),
    falls back to building the same template from bet attributes.
    """

    def _g(attr: str) -> str:
        if isinstance(bet, dict):
            val = bet.get(attr)
            if val is None:
                val = (bet.get("provider_meta") or {}).get(attr)
            return str(val or "")
        val = getattr(bet, attr, None)
        if val is None:
            meta = getattr(bet, "provider_meta", None) or {}
            if isinstance(meta, dict):
                val = meta.get(attr)
        return str(val or "")

    url = _g("url")
    if not url:
        sport = _g("sport") or "soccer"
        event_id = _g("event_id") or _g("provider_event_id")
        if not event_id:
            logger.warning(f"[cloudbet] No event_id on bet — cannot navigate ({bet!r})")
            return False
        url = f"{_BASE}/en/sports/{sport}/event/{event_id}"

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        logger.info(f"[cloudbet] Navigated to {url}")
        return True
    except Exception as e:
        logger.warning(f"[cloudbet] Navigate to {url} failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Placement — TODO: capture endpoint + request/response via probe bet, then
# implement parse_placement_response / parse_placement_status. Until then
# cloudbet runs in MANUAL mode (user clicks Place on the site, the
# interceptor catches it via _BET_PLACEMENT_KEYWORDS in browser.py).
# ---------------------------------------------------------------------------


strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    fetch_balance=_fetch_balance,
    sync_history=_sync_history,
    navigate_to_event=_navigate_to_event,
)
