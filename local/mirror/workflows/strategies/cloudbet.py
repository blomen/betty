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


async def _api_get(page: Page, url: str) -> Any:
    # Use page.evaluate(fetch) instead of page.context.request so the request
    # inherits the page's full document context — cookies, browser-set headers
    # (User-Agent, sec-ch-*, accept-language), and any cf-clearance fingerprint
    # binding. Playwright's APIRequestContext shares cookies but strips most
    # browser headers, which caused cloudbet's auth checks to 4xx for sessions
    # whose cookies are valid (regression observed 2026-05-28).
    try:
        result = await page.evaluate(
            """async (url) => {
                try {
                    const r = await fetch(url, {
                        credentials: 'include',
                        headers: { 'Accept': 'application/json' }
                    });
                    const text = await r.text();
                    if (!r.ok) return { __error: r.status, __body: text.slice(0, 300) };
                    try { return JSON.parse(text); } catch (e) { return { __error: 'parse', __body: text.slice(0, 300) }; }
                } catch (e) { return { __error: 'fetch', __msg: e.message || String(e) }; }
            }""",
            url,
        )
        if isinstance(result, dict) and "__error" in result:
            logger.info(
                f"[cloudbet] GET {url[:80]} → __error={result.get('__error')!r} "
                f"body={result.get('__body', '')[:120]!r} msg={result.get('__msg', '')!r}"
            )
        return result
    except Exception as e:
        logger.warning(f"[cloudbet] GET {url} failed: {e}")
        return None


async def _api_post(page: Page, url: str, body: dict | None = None) -> Any:
    try:
        result = await page.evaluate(
            """async ({url, body}) => {
                try {
                    const r = await fetch(url, {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Accept': 'application/json',
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(body || {})
                    });
                    const text = await r.text();
                    if (!r.ok) return { __error: r.status, __body: text.slice(0, 300) };
                    try { return JSON.parse(text); } catch (e) { return { __error: 'parse', __body: text.slice(0, 300) }; }
                } catch (e) { return { __error: 'fetch', __msg: e.message || String(e) }; }
            }""",
            {"url": url, "body": body or {}},
        )
        if isinstance(result, dict) and "__error" in result:
            logger.info(
                f"[cloudbet] POST {url[:80]} → __error={result.get('__error')!r} "
                f"body={result.get('__body', '')[:120]!r} msg={result.get('__msg', '')!r}"
            )
        return result
    except Exception as e:
        logger.warning(f"[cloudbet] POST {url} failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def _check_login(page: Page, intel: dict | None) -> bool:
    """Two-signal login proof (mirrors the kalshi pattern):

    1. /iam-me returns 200 with ANY populated identifier — id / uuid / email
       / currency. The id is a UUID string (the JWT carries the same uuid),
       not an int, so the previous `isinstance(me_id, int)` check was
       rejecting every real login. Accept any non-empty value on either the
       root or the nested `me` object.
    2. If /iam-me 4xxs (cookie missing a requested_attribute, edge cache
       miss, etc.), fall back to /iam-balances — a successful balance call
       is itself proof of an authenticated session.
    """
    data = await _api_get(page, _IAM_ME_URL)
    if isinstance(data, dict) and not data.get("__error"):
        me = data.get("me") if isinstance(data.get("me"), dict) else data
        for key in ("id", "uuid", "email", "currency"):
            val = me.get(key) if isinstance(me, dict) else None
            if val not in (None, "", 0):
                logger.info(
                    f"[cloudbet] Login confirmed via /iam-me ({key}={val!s:.40} "
                    f"currency={(me or {}).get('currency') or '?'})"
                )
                return True

    bal = await _api_post(page, _IAM_BALANCES_URL, body={})
    if isinstance(bal, dict) and not bal.get("__error"):
        logger.info("[cloudbet] Login confirmed via /iam-balances fallback")
        return True
    return False


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
        bet_id = (
            raw.get("id")
            or raw.get("betId")
            or raw.get("ticketId")
            or raw.get("referenceNumber")
            or ""
        )
        # Selections / legs — single-leg bets typical for our 1x2/spread/total scope.
        sels = raw.get("selections") or raw.get("legs") or raw.get("outcomes") or []
        first = sels[0] if isinstance(sels, list) and sels else {}
        event_name = (
            raw.get("eventName")
            or raw.get("event")
            or first.get("eventName")
            or first.get("event")
            or ""
        )
        if not event_name:
            home = raw.get("home") or first.get("home") or ""
            away = raw.get("away") or first.get("away") or ""
            if home and away:
                event_name = f"{home} vs {away}"

        odds_raw = (
            raw.get("price")
            or raw.get("odds")
            or first.get("price")
            or first.get("odds")
            or 0
        )
        stake_raw = raw.get("stake") or raw.get("amount") or raw.get("stakeAmount") or 0
        payout_raw = (
            raw.get("payout") or raw.get("returnAmount") or raw.get("winAmount")
        )

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
        logger.debug(
            f"[cloudbet] skip unparseable position row ({e}): {str(raw)[:120]}"
        )
        return None


async def _sync_history(page: Page, intel: dict | None) -> list[HistoryEntry]:
    """Fetch open + completed bets from /sports-betting/v4/bets/positions.

    Two-call merge: ACCEPTED for currently-open, COMPLETED for settled.
    Currency is left unset so the response uses the wallet's native unit —
    forcing USD reshapes amounts and we only need status for settlement.

    Logs the raw response keys + first item on the first call so the
    parser can be tightened if the shape differs from the spec.
    """
    from datetime import datetime, timedelta, timezone

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    start_iso = start.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    open_url = f"{_POSITIONS_URL}?status=ACCEPTED"
    settled_url = (
        f"{_POSITIONS_URL}?status=COMPLETED&from={start_iso}&limit=100&offset=0"
    )

    entries: list[HistoryEntry] = []
    seen_ids: set[str] = set()
    for label, url in (("open", open_url), ("settled", settled_url)):
        data = await _api_get(page, url)
        if not isinstance(data, dict) or data.get("__error"):
            logger.warning(
                f"[cloudbet] sync_history {label} request failed: "
                f"{data.get('__error') if isinstance(data, dict) else data!r}"
            )
            continue
        items = data.get("items")
        if not isinstance(items, list):
            # Some Cloudbet endpoints use `positions` / `bets` / `data` instead.
            for alt in ("positions", "bets", "data", "results"):
                if isinstance(data.get(alt), list):
                    items = data[alt]
                    logger.debug(
                        f"[cloudbet] sync_history {label}: items under '{alt}'"
                    )
                    break
        if not isinstance(items, list):
            logger.warning(
                f"[cloudbet] sync_history {label}: no items[] — response keys={list(data.keys())[:10]}"
            )
            continue
        if items:
            logger.debug(
                f"[cloudbet] sync_history {label}: {len(items)} raw items; sample={str(items[0])[:300]}"
            )
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


async def _navigate_to_event(page: Page, bet, intel: dict | None) -> bool | str:
    """Navigate to a cloudbet event page.

    Reads the routing URL stamped at extraction time as
    `provider_meta.cloudbet_event_url` — canonical shape
    `/en/sports/{sportKey}/{competitionSlug}/{numericEventId}`
    (e.g. `/en/sports/baseball/usa-mlb/34690236`).

    Returns True on success, or a str failure reason on failure. The
    GenericWorkflow wrapper translates a str return into False + stashes
    the reason as `workflow.last_nav_error`, which the router includes
    in the 502 detail surfaced to the UI. Does NOT construct a fallback
    URL — the old `/en/sports/{sport}/event/{event_id}` template was
    malformed on every front (no /event/ segment, slug-style event_id
    instead of numeric web id). Better to skip than land on a 404.
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

    url = _g("cloudbet_event_url") or _g("url")
    if not url:
        meta = (
            bet.get("provider_meta")
            if isinstance(bet, dict)
            else getattr(bet, "provider_meta", None)
        ) or {}
        logger.warning(
            f"[cloudbet] No event URL stamped on bet — cannot navigate "
            f"(provider_meta keys: {list(meta.keys())[:8]})"
        )
        return "no event URL stamped on bet"

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        logger.warning(f"[cloudbet] Navigate to {url} failed: {e}")
        return f"goto failed: {type(e).__name__}"

    # Cloudbet renders 404 client-side as a "WHAT ARE THE ODDS?" SPA shell —
    # page.goto returns 200 even when the event is gone. Without this check
    # the runner would mark the bet ready and the user could click Place on
    # a page that has no market. See the 2026-05-28 incident: a stamped URL
    # using the legacy slug-style event_id pointed at a removed event, the
    # nav succeeded, but the page showed "Looks like the page you wanted
    # doesn't exist."
    if await _is_cloudbet_404(page):
        logger.warning(
            f"[cloudbet] Landed on 404 shell after goto {url} — event missing or URL stale"
        )
        return "landed on cloudbet 404 page (event missing or URL stale)"

    logger.info(f"[cloudbet] Navigated to {url}")
    return True


async def _is_cloudbet_404(page: Page) -> bool:
    """Detect cloudbet's client-rendered 404 page.

    The shell is the same regardless of which path 404s — a "404" hero with
    "Looks like the page you wanted doesn't exist." and a "Return to lobby"
    button. Matched on the body text marker since the URL stays whatever the
    user navigated to.
    """
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const txt = (document.body && document.body.innerText) || '';
                    return txt.includes("Looks like the page you wanted doesn't exist");
                }"""
            )
        )
    except Exception:
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
