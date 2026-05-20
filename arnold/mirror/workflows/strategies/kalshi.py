"""Kalshi strategy — cookie-session web app.

Routed through GenericWorkflow + intel JSON. All authenticated calls go
through Playwright's request context, so they share the same cookies + WAF
token the browser already has from the live login. No SDK, no on-disk
private key, no env vars — auth is "user is logged into kalshi.com in the
mirror."

Discovered 2026-05-05 against api.elections.kalshi.com (full report at
docs/superpowers/specs/2026-05-05-kalshi-generic-workflow-discovery.md).

Required headers on every authed call (besides cookies):
    x-csrf-token       — localStorage["csrfToken"].value (JSON-wrapped)
    x-aws-waf-token    — verbatim copy of the aws-waf-token cookie

Re-read both on every call — the SPA refreshes them in the background and
caching causes intermittent 401 INVALID_CSRF_TOKEN.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..base import HistoryEntry, PlacementResult
from . import Strategy

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

_API = "https://api.elections.kalshi.com"

# Per-instance prep state survives between prep_betslip → place_bet within
# one provider tab. Module-level mirrors the prior strategy's pattern.
_pending: dict[str, Any] = {
    "market_id": None,
    "market_ticker": None,
    "yes_price_cents": 0,
    "count": 0,
    "side": "yes",
}


# ---------------------------------------------------------------------------
# Auth helpers — re-read on every call (CSRF + WAF rotate in the background)
# ---------------------------------------------------------------------------


async def _auth_context(page: Page) -> dict | None:
    """Pull (user_id, csrf, waf) from the live page session.

    Returns dict with keys csrf/waf/user_id, or None if any are missing —
    the caller should treat that as "not logged in" rather than retry.
    """
    try:
        ctx = await page.evaluate(
            r"""() => {
                const ck = (k) => {
                    const m = document.cookie.match(new RegExp('(?:^|; )' + k + '=([^;]+)'));
                    return m ? m[1] : null;
                };
                let csrf = null;
                try {
                    const raw = localStorage.getItem('csrfToken');
                    if (raw && raw.startsWith('{')) csrf = JSON.parse(raw).value;
                    else csrf = raw;
                } catch {}
                return { csrf, waf: ck('aws-waf-token'), user_id: ck('userId') };
            }"""
        )
    except Exception as e:
        logger.warning(f"[kalshi] _auth_context eval failed: {e}")
        return None
    if not ctx or not ctx.get("csrf") or not ctx.get("waf") or not ctx.get("user_id"):
        return None
    return ctx


def _headers(auth: dict, content_json: bool = False) -> dict:
    h = {
        "accept": "application/json",
        "x-csrf-token": auth["csrf"],
        "x-aws-waf-token": auth["waf"],
    }
    if content_json:
        h["content-type"] = "application/json"
    return h


async def _api_get(page: Page, path: str) -> Any:
    auth = await _auth_context(page)
    if auth is None:
        return {"__error": "no_auth"}
    url = _API + path.replace("<U>", auth["user_id"])
    try:
        resp = await page.context.request.get(url, headers=_headers(auth))
        if resp.status < 200 or resp.status >= 400:
            return {"__error": resp.status}
        return await resp.json()
    except Exception as e:
        logger.warning(f"[kalshi] GET {path} failed: {e}")
        return {"__error": "exception"}


async def _api_post(page: Page, path: str, body: dict) -> Any:
    auth = await _auth_context(page)
    if auth is None:
        return {"__error": "no_auth"}
    url = _API + path.replace("<U>", auth["user_id"])
    try:
        resp = await page.context.request.post(
            url,
            data=body,
            headers=_headers(auth, content_json=True),
        )
        status = resp.status
        try:
            payload = await resp.json()
        except Exception:
            payload = None
        if status < 200 or status >= 400:
            return {"__error": status, "payload": payload}
        return payload
    except Exception as e:
        logger.warning(f"[kalshi] POST {path} failed: {e}")
        return {"__error": "exception"}


# ---------------------------------------------------------------------------
# Login + balance
# ---------------------------------------------------------------------------


async def _check_login(page: Page, intel: dict | None) -> bool:
    """A successful /balance call (200 with int field) is the login proof."""
    data = await _api_get(page, "/v1/users/<U>/balance")
    if not isinstance(data, dict) or "__error" in data:
        return False
    return isinstance(data.get("balance"), int)


async def _sync_balance(page: Page, intel: dict | None) -> float:
    data = await _api_get(page, "/v1/users/<U>/balance")
    if not isinstance(data, dict) or "__error" in data:
        return 0.0
    cents = data.get("balance") or 0
    try:
        return round(float(cents) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


# fetch_balance == sync_balance — single int read, cheap. Keeps the
# READY_TO_RUN passive refresh on without DOM scraping.
_fetch_balance = _sync_balance


# ---------------------------------------------------------------------------
# History — open + settled positions, mapped to HistoryEntry
# ---------------------------------------------------------------------------


def _entry_from_position(p: dict, status: str, payout: float | None) -> HistoryEntry | None:
    market_ticker = p.get("market_ticker") or ""
    if not market_ticker:
        return None
    cost_cents = int(p.get("position_cost") or p.get("total_cost") or 0)
    qty = int(p.get("position") or 0)
    if qty == 0 and status == "pending":
        return None
    qty_abs = abs(qty) or 1
    avg_price_cents = round(cost_cents / qty_abs) if cost_cents else 0
    odds = round(100.0 / max(avg_price_cents, 1), 4) if avg_price_cents else 0.0
    stake = round(cost_cents / 100.0, 2)
    side = "yes" if qty >= 0 else "no"
    return HistoryEntry(
        provider_bet_id=str(market_ticker),
        event_name=str(p.get("event_ticker") or market_ticker),
        market=str(market_ticker),
        outcome=side,
        odds=odds,
        stake=stake,
        status=status,
        payout=payout,
    )


def _walk_positions(payload: Any) -> list[dict]:
    """Flatten the event_positions response.

    Endpoint returns either {event_positions: [{market_positions: [...]}, ...]}
    or {event_positions: [...]} where each row already has market fields.
    Walks both.
    """
    if not isinstance(payload, dict):
        return []
    out: list[dict] = []
    for ev in payload.get("event_positions") or []:
        if not isinstance(ev, dict):
            continue
        mps = ev.get("market_positions")
        if isinstance(mps, list):
            for mp in mps:
                if isinstance(mp, dict):
                    merged = {**ev, **mp}
                    out.append(merged)
        else:
            out.append(ev)
    return out


async def _sync_history(page: Page, intel: dict | None) -> list[HistoryEntry]:
    """Open + recently-settled positions → HistoryEntry list.

    Settled rows carry final_position_cost / realized_pnl which we use to
    classify won/lost; open rows are emitted as pending.
    """
    out: list[HistoryEntry] = []

    open_resp = await _api_get(page, "/v1/users/<U>/event_positions?position_status=open")
    for pos in _walk_positions(open_resp):
        e = _entry_from_position(pos, "pending", None)
        if e:
            out.append(e)

    # limit=500 instead of 100 — bets can sit 5+ days as pending in our DB
    # before reactive sync triggers; a busy week wipes the 100-cap window
    # before we see it (bet 418, 2026-05-11, never showed in 100-cap returns
    # despite definitely being settled on kalshi's side).
    settled_resp = await _api_get(
        page,
        "/v1/users/<U>/event_positions?position_status=close&settlement_status=settled&limit=500",
    )
    for pos in _walk_positions(settled_resp):
        realized_cents = int(pos.get("realized_pnl") or 0)
        cost_cents = int(pos.get("position_cost") or pos.get("total_cost") or 0)
        # realized_pnl > 0 → won; == -cost → lost; else partial / void
        if realized_cents > 0:
            status, payout = "won", round((cost_cents + realized_cents) / 100.0, 2)
        elif realized_cents <= -cost_cents and cost_cents > 0:
            status, payout = "lost", 0.0
        else:
            # Net-zero or partial — treat as void to avoid false won/lost.
            status, payout = "void", round(cost_cents / 100.0, 2)
        e = _entry_from_position(pos, status, payout)
        if e:
            out.append(e)

    return out


# ---------------------------------------------------------------------------
# Navigation — resolve event_ticker → market_id and goto market URL
# ---------------------------------------------------------------------------


def _ticker_from_bet(bet) -> str:
    """Pull the kalshi ticker from a bet — checks every field name the
    extraction / scanner / arb-opp paths use. The scanner emits it as
    `ticker` in provider_meta (which _bet_ns flattens to a top-level attr);
    older paths used `provider_market_ticker` / `provider_event_id`."""
    val = (
        getattr(bet, "provider_market_ticker", None)
        or getattr(bet, "provider_event_id", None)
        or getattr(bet, "ticker", None)
        or ""
    )
    return str(val or "").replace("kalshi_", "").upper()


def _event_ticker_from_market(market_ticker: str) -> str:
    """Derive the event ticker by stripping a trailing -OUTCOME segment.

    Kalshi event tickers are `<SERIES>-<DATE+ID>`, e.g.
    `KXBOXING-26MAY16DOHERTHATIM`. A market within an event appends one
    `-<OUTCOME>` segment:
        moneyline:    `KXBOXING-26MAY16DOHERTHATIM-HATIM`  (name fragment)
        spread/total: `KXEPLTOTAL-26MAY24BRIMUN-3` / `-ALN1` (line index)

    So any ticker with ≥3 dash-separated segments is a MARKET ticker — strip
    the last segment to get the event ticker. 2-segment tickers are already
    event-level (single-market events) and returned unchanged.

    A digit-aware heuristic used to live here, but spread/total outcome
    suffixes carry digits ("-3", "-ALN1"), so it wrongly kept them attached
    and broke navigation. The rare genuine 3-segment event ticker is covered
    by the full-ticker fallback in `_resolve_market_id`.
    """
    parts = (market_ticker or "").split("-")
    if len(parts) <= 2:
        return market_ticker
    return "-".join(parts[:-1])


def _series_from_event_ticker(event_ticker: str) -> str:
    """KXTRUMPMENTION-26APR30 → KXTRUMPMENTION."""
    return (event_ticker.split("-", 1)[0] or "").upper()


async def _resolve_market_id(page: Page, event_ticker: str, market_ticker: str | None) -> str | None:
    """Map (event_ticker, market_ticker) → market_id (UUID).

    Web order POST takes market_id, not market_ticker. /v1/cached/events/
    returns the event with markets[] each having id + ticker.
    """
    if not event_ticker:
        return None
    data = await _api_get(page, f"/v1/cached/events/?tickers={event_ticker}")
    if not isinstance(data, dict) or "__error" in data:
        print(f"[kalshi resolve] api error for event={event_ticker!r}: {data}", flush=True)
        return None
    events = data.get("events") or []
    if not events:
        print(f"[kalshi resolve] no events for ticker={event_ticker!r} (response keys={list(data.keys())})", flush=True)
        return None
    markets = (events[0] or {}).get("markets") or []
    if not markets:
        print(f"[kalshi resolve] event found but no markets — event={events[0]!r}", flush=True)
        return None
    if market_ticker:
        mt = market_ticker.upper()
        # Kalshi's /v1/cached/events response uses `ticker_name` (not the
        # documented `ticker`) for the market's ticker. Check both — older
        # endpoints may still return `ticker`.
        for m in markets:
            mt_val = (m.get("ticker_name") or m.get("ticker") or "").upper()
            if mt_val == mt:
                return m.get("id") or m.get("market_id")
    # Fall back to single-market events
    if len(markets) == 1:
        return markets[0].get("id") or markets[0].get("market_id")
    return None


def _market_yes_ask(markets: list[dict], market_ticker: str | None) -> int | None:
    """Pick yes_ask (cents) from the matching market record. Kalshi's
    cached-events response uses `ticker_name`, not `ticker`."""
    if not markets:
        return None
    if market_ticker:
        mt = market_ticker.upper()
        for m in markets:
            mt_val = (m.get("ticker_name") or m.get("ticker") or "").upper()
            if mt_val == mt:
                v = m.get("yes_ask")
                return int(v) if isinstance(v, (int, float)) and v > 0 else None
    if len(markets) == 1:
        v = markets[0].get("yes_ask")
        return int(v) if isinstance(v, (int, float)) and v > 0 else None
    return None


async def _navigate_to_event(page: Page, bet, intel: dict | None) -> bool:
    raw_ticker = _ticker_from_bet(bet)
    if not raw_ticker:
        return False

    # market_ticker is the full ticker the bet carries (may include -OUTCOME).
    # event_ticker is what /v1/cached/events expects — strip the outcome suffix.
    market_ticker = raw_ticker

    # If the bet has an explicit provider_event_id distinct from the market
    # ticker, prefer it. Otherwise derive event_ticker by stripping any
    # trailing -OUTCOME segment from the market ticker.
    pe_id = str(getattr(bet, "provider_event_id", "") or "").upper().replace("KALSHI_", "")
    if pe_id and pe_id != market_ticker:
        event_ticker = pe_id
    else:
        event_ticker = _event_ticker_from_market(market_ticker)

    series = _series_from_event_ticker(event_ticker)
    market_id = await _resolve_market_id(page, event_ticker, market_ticker)
    if not market_id:
        # Fallback: if derived event_ticker returns nothing AND it differs from
        # the raw market_ticker, try the full market_ticker as an event_ticker
        # (covers single-market events whose market_ticker IS the event_ticker
        # but had a numeric trailing segment we mis-identified as a date).
        if event_ticker != market_ticker:
            event_ticker_alt = market_ticker
            market_id = await _resolve_market_id(page, event_ticker_alt, market_ticker)
            if market_id:
                event_ticker = event_ticker_alt
                series = _series_from_event_ticker(event_ticker)
        if not market_id:
            logger.warning(f"[kalshi] Could not resolve market_id for event={event_ticker} market={market_ticker}")
            return False

    _pending["market_id"] = market_id
    _pending["market_ticker"] = market_ticker
    _pending["event_ticker"] = event_ticker

    url = f"https://kalshi.com/markets/{series.lower()}/x/{event_ticker.lower()}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        return True
    except Exception as e:
        logger.warning(f"[kalshi] goto {url} failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Betslip prep + live price
# ---------------------------------------------------------------------------


def _infer_yes_price_dollars(bet) -> float:
    odds = float(getattr(bet, "odds", 2.0))
    return max(0.01, min(0.99, round(1.0 / odds, 4)))


async def _prep_betslip(page: Page, bet, stake: float, intel: dict | None) -> PlacementResult:
    bid = getattr(bet, "bet_id", None) or getattr(bet, "id", 0)
    if not _pending.get("market_id"):
        return PlacementResult(status="failed", bet_id=bid, reason="no_market_id")

    yes_price_dollars = _infer_yes_price_dollars(bet)
    yes_price_cents = max(1, min(99, int(round(yes_price_dollars * 100))))
    count = max(1, round(stake / max(yes_price_dollars, 0.01)))
    actual_stake = round(count * yes_price_dollars, 2)

    _pending["yes_price_cents"] = yes_price_cents
    _pending["count"] = count
    _pending["side"] = "yes"

    # status="prepped" matches the convention used by polymarket / altenar
    # strategies — required so navigate-opp continues into the live-price
    # poll path. Older "ready" status was treated as prep_failed by the
    # router, which stopped polling before live odds could surface.
    return PlacementResult(
        status="prepped",
        bet_id=bid,
        actual_odds=round(1.0 / yes_price_dollars, 4),
        actual_stake=actual_stake,
    )


def _bet_is_no_side(bet) -> bool:
    """True when the bet's outcome maps to the NO side of the Kalshi contract.

    A Kalshi binary market quotes a single YES contract; the other outcome is
    its complement. `_check_live_price` reads that contract's `yes_ask`, so a
    NO-side bet must invert the price (``100 - yes_ask``) — otherwise the live
    re-check compares the YES price against the NO outcome's fair odds and the
    edge collapses sharply negative, dropping the row from the value table.

    Side resolution mirrors the extractor (backend/src/providers/kalshi.py):
      - moneyline: each side is its own YES contract  → always YES
      - total:     the YES contract is always "Over N.5" → "under" is NO
      - spread:    provider_meta carries ``yes_side``  → outcome != yes_side is NO
    """
    outcome = str(getattr(bet, "outcome", "") or "").lower().strip()
    meta = getattr(bet, "provider_meta", None) or {}
    yes_side = str(getattr(bet, "yes_side", None) or meta.get("yes_side") or "").lower().strip()
    if yes_side:  # spread — meta tells us which side the YES contract favours
        return outcome != yes_side
    return outcome == "under"  # total — "under" is the NO complement of "Over N.5"


async def _check_live_price(page: Page, bet, intel: dict | None):
    event_ticker = _pending.get("event_ticker") or _ticker_from_bet(bet)
    market_ticker = _pending.get("market_ticker")
    if not event_ticker:
        return None, None
    data = await _api_get(page, f"/v1/cached/events/?tickers={event_ticker}")
    if not isinstance(data, dict) or "__error" in data:
        return None, None
    events = data.get("events") or []
    markets = (events[0] or {}).get("markets") if events else None
    yes_ask_cents = _market_yes_ask(markets or [], market_ticker)
    if not yes_ask_cents:
        return None, None
    # NO-side outcomes (total "under", spread non-yes_side) trade at the
    # complement of the YES contract. Invert so the live odds are on the same
    # basis as the scanner edge — the extractor prices NO as 1 - yes_ask
    # (kalshi.py:_no_side_odds), so 100 - yes_ask keeps the re-check honest.
    ask_cents = (100 - yes_ask_cents) if _bet_is_no_side(bet) else yes_ask_cents
    if ask_cents <= 0:
        return None, None
    live_odds = round(100.0 / ask_cents, 4)
    fair = getattr(bet, "fair_odds", None)
    live_edge = round((live_odds / float(fair) - 1.0) * 100.0, 2) if fair else None
    return live_odds, live_edge


# ---------------------------------------------------------------------------
# Place bet — POST /v1/users/<U>/orders, then poll if not fully filled
# ---------------------------------------------------------------------------


def _classify_create_response(payload: Any) -> tuple[str, dict]:
    """Read fill state from the POST response.

    Web returns 201 with {order: {fill_count, initial_count, remaining_count, price, ...}}.
    Status field is "pending" even on full fill — trust the count fields.
    """
    if not isinstance(payload, dict):
        return "failed", {"reason": "no_payload"}
    order = payload.get("order")
    if not isinstance(order, dict):
        return "failed", {"reason": "no_order"}
    initial = int(order.get("initial_count") or 0)
    fill = int(order.get("fill_count") or 0)
    remaining = int(order.get("remaining_count") or 0)
    info = {
        "order_id": order.get("order_id"),
        "fill_count": fill,
        "initial_count": initial,
        "remaining_count": remaining,
        "price_cents": int(order.get("price") or 0),
        "fees": int(order.get("taker_fees") or 0),
    }
    if initial > 0 and fill >= initial and remaining == 0:
        return "filled", info
    if remaining > 0:
        return "resting", info
    return "failed", info


async def _poll_order(page: Page, order_id: str) -> tuple[str, dict]:
    data = await _api_get(page, f"/v1/users/<U>/orders/{order_id}")
    if not isinstance(data, dict) or "__error" in data:
        return "poll_error", {}
    return _classify_create_response(data)


async def _place_bet(page: Page, bet, stake: float, intel: dict | None) -> PlacementResult:
    bid = getattr(bet, "bet_id", None) or getattr(bet, "id", 0)
    market_id = _pending.get("market_id")
    if not market_id:
        return PlacementResult(status="failed", bet_id=bid, reason="no_market_id")

    yes_price_cents = int(_pending.get("yes_price_cents") or 0)
    count = int(_pending.get("count") or 0)
    if yes_price_cents <= 0 or count <= 0:
        return PlacementResult(status="failed", bet_id=bid, reason="prep_missing")

    body = {
        "market_id": market_id,
        "side": _pending.get("side") or "yes",
        "user_side": _pending.get("side") or "yes",
        "order_action": "buy",
        "order_type": "market",
        "time_in_force": "immediate_or_cancel",
        "count_fp": f"{count:.2f}",
        "price_dollars": f"{yes_price_cents / 100:.4f}",
        "expiration_unix_ts": 0,
        "max_cost_cents": 0,
        "sell_position_capped": False,
        "post_only": False,
        "order_source": "web",
    }

    create = await _api_post(page, "/v1/users/<U>/orders", body)
    if isinstance(create, dict) and "__error" in create:
        reason = f"http_{create['__error']}"
        return PlacementResult(status="failed", bet_id=bid, reason=reason, raw_response=create)

    state, info = _classify_create_response(create)
    if state == "filled":
        fc = info.get("fill_count") or count
        fp = info.get("price_cents") or yes_price_cents
        return PlacementResult(
            status="placed",
            bet_id=bid,
            actual_odds=round(100.0 / max(fp, 1), 4),
            actual_stake=round(fc * fp / 100.0, 2),
            raw_response=create if isinstance(create, dict) else None,
        )

    order_id = info.get("order_id")
    if not order_id:
        # No order_id and not filled → trust the create response and report.
        return PlacementResult(
            status="failed",
            bet_id=bid,
            reason=info.get("reason") or "no_order_id",
            raw_response=create if isinstance(create, dict) else None,
        )

    # Resting → poll up to 5x at 1s. Trust on poll-error after 2 consecutive fails.
    poll_errors = 0
    for _ in range(5):
        await asyncio.sleep(1.0)
        state, info = await _poll_order(page, order_id)
        if state == "poll_error":
            poll_errors += 1
            if poll_errors >= 2:
                return PlacementResult(
                    status="placed",
                    bet_id=bid,
                    actual_odds=round(100.0 / max(yes_price_cents, 1), 4),
                    actual_stake=round(count * yes_price_cents / 100.0, 2),
                    reason="poll_unavailable_trusting_create",
                    raw_response=create if isinstance(create, dict) else None,
                )
            continue
        poll_errors = 0
        if state == "filled":
            fc = info.get("fill_count") or count
            fp = info.get("price_cents") or yes_price_cents
            return PlacementResult(
                status="placed",
                bet_id=bid,
                actual_odds=round(100.0 / max(fp, 1), 4),
                actual_stake=round(fc * fp / 100.0, 2),
                raw_response=create if isinstance(create, dict) else None,
            )
        if state == "failed":
            return PlacementResult(
                status="failed",
                bet_id=bid,
                reason=info.get("reason") or "rejected",
                raw_response=create if isinstance(create, dict) else None,
            )

    # Still resting after the poll budget — cancel + report failed.
    cancel_reason = "unfilled_within_5s"
    cancel = await _api_post(page, f"/v1/users/<U>/orders/{order_id}/cancel", {})
    if isinstance(cancel, dict) and "__error" in cancel:
        cancel_reason = "unfilled_cancel_failed"
    return PlacementResult(
        status="failed",
        bet_id=bid,
        reason=cancel_reason,
        raw_response=create if isinstance(create, dict) else None,
    )


# ---------------------------------------------------------------------------
# Placement-XHR parsing — feeds the browser interceptor when the SPA itself
# posts the order (e.g. a manual user click during a logged-in session).
# ---------------------------------------------------------------------------


def _parse_placement_response(body: dict) -> str | None:
    if not isinstance(body, dict):
        return None
    order = body.get("order")
    if isinstance(order, dict) and order.get("order_id"):
        return str(order["order_id"])
    return None


def _parse_placement_status(body: dict) -> dict:
    if not isinstance(body, dict):
        return {"success": False, "error": "no_body", "max_stake": None}
    state, info = _classify_create_response(body)
    return {
        "success": state in {"filled", "resting"},
        "error": None if state in {"filled", "resting"} else info.get("reason"),
        "max_stake": None,
    }


strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    fetch_balance=_fetch_balance,
    sync_history=_sync_history,
    navigate_to_event=_navigate_to_event,
    prep_betslip=_prep_betslip,
    check_live_price=_check_live_price,
    place_bet=_place_bet,
    parse_placement_response=_parse_placement_response,
    parse_placement_status=_parse_placement_status,
)
