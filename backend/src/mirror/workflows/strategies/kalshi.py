"""Kalshi strategy — API-first via kalshi-python SDK.

Routed through GenericWorkflow + intel JSON like Polymarket. The intel
file flips `autonomous_placement: true` and `login.method: balance_api`,
so login detection runs through `sync_balance > 0`.

The Playwright tab at https://kalshi.com/markets/<ticker> exists for
visual context only — no DOM automation. All real work is REST API.

SDK lifecycle is held at module scope (single client per process) instead
of per-instance so a stale workflow object doesn't bypass already-loaded
creds. `_init_client()` re-runs every check_login until creds + SDK are
both available, recovering from `.env` load order issues without a
process restart.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from ..base import HistoryEntry, PlacementResult

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level SDK state
# ---------------------------------------------------------------------------

_KalshiClient = None
_PortfolioApi = None
_MarketsApi = None
_client = None
_portfolio = None
_markets = None
_balance_cache: float | None = None
_last_init_error: str | None = None
_pending: dict = {"ticker": None, "yes_price_cents": 0, "count": 0}


def _load_sdk() -> bool:
    global _KalshiClient, _PortfolioApi, _MarketsApi
    if _KalshiClient is not None:
        return True
    try:
        from kalshi_python import KalshiClient, MarketsApi, PortfolioApi  # type: ignore

        _KalshiClient = KalshiClient
        _PortfolioApi = PortfolioApi
        _MarketsApi = MarketsApi
        return True
    except ImportError:
        logger.warning("[kalshi] kalshi-python SDK not installed — API features disabled")
        return False


def _key_path(pem_body: str):
    """Materialize the PEM to a stable on-disk path the SDK can open by file path."""
    try:
        from ....paths import get_data_dir  # type: ignore

        base = get_data_dir()
    except ImportError:
        import pathlib

        base = pathlib.Path(__file__).resolve().parents[3] / "data"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "kalshi_key.pem"
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else None
    except OSError:
        existing = None
    if existing != pem_body:
        path.write_text(pem_body, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _init_client() -> None:
    global _client, _portfolio, _markets, _last_init_error
    if _portfolio is not None and _markets is not None:
        return
    key_id = os.getenv("KALSHI_API_KEY_ID")
    key_pem = os.getenv("KALSHI_PRIVATE_KEY_PEM")
    if not (key_id and key_pem):
        _last_init_error = "no_creds"
        return
    if not _load_sdk():
        _last_init_error = "sdk_load_failed"
        return
    try:
        pem_body = key_pem.replace("\\n", "\n")
        path = str(_key_path(pem_body))
        _client = _KalshiClient()
        _client.set_kalshi_auth(key_id=key_id, private_key_path=path)
        _portfolio = _PortfolioApi(api_client=_client)
        _markets = _MarketsApi(api_client=_client)
        _last_init_error = None
        logger.info("[kalshi] SDK client initialized (API mode)")
    except Exception as e:
        _last_init_error = f"{type(e).__name__}: {e}"
        logger.error(f"[kalshi] client init failed: {_last_init_error}")
        _client = None
        _portfolio = None
        _markets = None


def _has_api() -> bool:
    return _portfolio is not None and _markets is not None


# ---------------------------------------------------------------------------
# Strategy methods
# ---------------------------------------------------------------------------


async def _check_login(page: Page, intel: dict | None) -> bool:
    """API auth is independent of web session — presence of an authed client
    plus a successful balance call confirms 'logged in'. Retry init in case
    creds + SDK became available after first import."""
    if not _has_api():
        _init_client()
    if not _has_api():
        return False
    # Verify by actually calling the API; surfaces revoked-key / network errors
    # instead of falsely claiming logged-in.
    try:
        _portfolio.get_balance()
        return True
    except Exception as e:
        logger.warning(f"[kalshi] check_login API call failed: {e}")
        return False


async def _sync_balance(page: Page, intel: dict | None) -> float:
    global _balance_cache
    if not _has_api():
        _init_client()
    if not _has_api():
        return 0.0
    try:
        resp = _portfolio.get_balance()
        cents = getattr(resp, "balance", None) or 0
        value = round(float(cents) / 100.0, 2)
        _balance_cache = value
        return value
    except Exception as e:
        logger.warning(f"[kalshi] sync_balance failed: {e}")
        if _balance_cache is not None:
            return _balance_cache
        return 0.0


async def _sync_history(page: Page, intel: dict | None) -> list[HistoryEntry]:
    if not _has_api():
        _init_client()
    if not _has_api():
        return []
    try:
        fills_resp = _portfolio.get_fills(limit=200)
        fills = getattr(fills_resp, "fills", None) or []
    except Exception as e:
        logger.warning(f"[kalshi] get_fills failed: {e}")
        return []

    # Best-effort: a positions failure degrades to all-pending rather than
    # blocking fill sync entirely.
    # SDK Position carries `ticker` + `market_result` (verified against
    # kalshi_python.models.Position 2026-04-30 — neither `market_ticker`
    # nor `result` exist on this model).
    positions_by_ticker: dict[str, str] = {}
    try:
        pos_resp = _portfolio.get_positions()
        positions = getattr(pos_resp, "positions", None) or []
        for p in positions:
            ticker = getattr(p, "ticker", "") or ""
            result = (getattr(p, "market_result", "") or "").lower()
            if ticker and result in {"yes", "no", "void"}:
                positions_by_ticker[ticker] = result
    except Exception as e:
        logger.warning(f"[kalshi] get_positions failed (settlement merge skipped): {e}")

    out: list[HistoryEntry] = []
    for f in fills:
        ticker = getattr(f, "ticker", "") or ""
        side = getattr(f, "side", "") or ""
        count = int(getattr(f, "count", 0) or 0)
        price_cents = int(getattr(f, "price", 0) or 0)
        order_id = getattr(f, "order_id", None) or getattr(f, "fill_id", None) or ""
        odds = round(100.0 / max(price_cents, 1), 4) if price_cents else 0.0
        stake = round(count * price_cents / 100.0, 2)

        status = "pending"
        payout: float | None = None
        settled = positions_by_ticker.get(ticker)
        if settled == "yes":
            status, payout = "won", round(count * 1.0, 2)
        elif settled == "no":
            status, payout = "lost", 0.0
        elif settled == "void":
            status, payout = "void", stake

        out.append(
            HistoryEntry(
                provider_bet_id=str(order_id),
                event_name=ticker,
                market=ticker,
                outcome=side,
                odds=odds,
                stake=stake,
                status=status,
                payout=payout,
            )
        )
    return out


async def _navigate_to_event(page: Page, bet, intel: dict | None) -> bool:
    ticker = getattr(bet, "provider_market_ticker", None) or getattr(bet, "provider_event_id", "") or ""
    ticker = ticker.replace("kalshi_", "")
    if not ticker:
        return False
    url = f"https://kalshi.com/markets/{ticker}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        return True
    except Exception as e:
        logger.warning(f"[kalshi] navigate failed: {e}")
        return False


def _infer_yes_price(bet) -> float:
    odds = float(getattr(bet, "odds", 2.0))
    return max(0.01, min(0.99, round(1.0 / odds, 4)))


async def _prep_betslip(page: Page, bet, stake: float, intel: dict | None) -> PlacementResult:
    bid = getattr(bet, "bet_id", None)
    if bid is None:
        bid = getattr(bet, "id", 0)

    ticker = getattr(bet, "provider_market_ticker", None) or getattr(bet, "provider_event_id", None)
    if not ticker:
        return PlacementResult(status="failed", bet_id=bid, reason="no_ticker")

    yes_price_dollars = _infer_yes_price(bet)
    yes_price_cents = max(1, min(99, int(round(yes_price_dollars * 100))))
    # round-nearest, not floor: floor systematically under-stakes
    count = max(1, round(stake / max(yes_price_dollars, 0.01)))
    actual_stake = round(count * yes_price_dollars, 2)

    _pending["ticker"] = ticker
    _pending["yes_price_cents"] = yes_price_cents
    _pending["count"] = count

    return PlacementResult(
        status="ready",
        bet_id=bid,
        actual_odds=round(1.0 / yes_price_dollars, 4),
        actual_stake=actual_stake,
    )


async def _check_live_price(page: Page, bet, intel: dict | None):
    if not _has_api() or not _pending["ticker"]:
        return None, None
    try:
        resp = _markets.get_market(_pending["ticker"])
        mkt = getattr(resp, "market", None)
        if mkt is None:
            return None, None
        yad = getattr(mkt, "yes_ask_dollars", None)
        if yad is not None and float(yad) > 0:
            yes_ask_cents = float(yad) * 100.0
        else:
            yes_ask_cents = float(getattr(mkt, "yes_ask", 0) or 0)
        if yes_ask_cents <= 0:
            return None, None
        live_odds = round(100.0 / yes_ask_cents, 4)
        fair = getattr(bet, "fair_odds", None)
        live_edge = round((live_odds / float(fair) - 1.0) * 100.0, 2) if fair else None
        return live_odds, live_edge
    except Exception as e:
        logger.warning(f"[kalshi] check_live_price failed: {e}")
        return None, None


def _classify_order_state(resp) -> tuple[str, dict]:
    """Centralizes SDK status field names so a future SDK change touches one spot."""
    order = getattr(resp, "order", None) or resp
    status = (getattr(order, "status", "") or "").lower()
    if status in {"executed", "filled"}:
        return "filled", {
            "fill_count": int(getattr(order, "fill_count", 0) or 0),
            "fill_price": int(getattr(order, "fill_price", 0) or 0),
        }
    if status in {"canceled", "cancelled"}:
        return "canceled", {"reason": getattr(order, "reason", None) or "canceled"}
    if status in {"failed", "rejected"}:
        return "failed", {"reason": getattr(order, "reason", None) or status}
    return "resting", {}


async def _place_bet(page: Page, bet, stake: float, intel: dict | None) -> PlacementResult:
    import asyncio

    bid = getattr(bet, "bet_id", None)
    if bid is None:
        bid = getattr(bet, "id", 0)

    if not _has_api() or not _pending["ticker"]:
        return PlacementResult(status="failed", bet_id=bid, reason="no_client")

    ticker = _pending["ticker"]
    yes_price_cents = _pending["yes_price_cents"]
    count = _pending["count"]

    try:
        create_resp = _portfolio.create_order(
            ticker=ticker,
            action="buy",
            side="yes",
            type="limit",
            yes_price=yes_price_cents,
            count=count,
            expiration_ts=int(time.time()) + 60,
        )
    except Exception as e:
        logger.error(f"[kalshi] create_order failed: {e}")
        return PlacementResult(status="failed", bet_id=bid, reason=str(e))

    order_id = getattr(create_resp, "order_id", None)
    raw = create_resp.to_dict() if hasattr(create_resp, "to_dict") else None

    # No order_id in the create response means we can't poll or cancel.
    # Trust create instead of silently mis-reporting "unfilled" later.
    if not order_id:
        logger.warning("[kalshi] create_order returned no order_id — trusting create response")
        return PlacementResult(
            status="placed",
            bet_id=bid,
            actual_odds=round(100.0 / max(yes_price_cents, 1), 4),
            actual_stake=round(count * yes_price_cents / 100.0, 2),
            reason="no_order_id_trusting_create",
            raw_response=raw,
        )

    # Poll up to 5x at 1s intervals. After 2 consecutive polling errors,
    # trust the create response — a flaky GET shouldn't double-cancel a real fill.
    poll_errors = 0
    for _ in range(5):
        await asyncio.sleep(1.0)
        try:
            poll_resp = _portfolio.get_order(order_id)
            poll_errors = 0
        except Exception as e:
            poll_errors += 1
            logger.warning(f"[kalshi] get_order poll failed: {e}")
            if poll_errors >= 2:
                return PlacementResult(
                    status="placed",
                    bet_id=bid,
                    actual_odds=round(100.0 / max(yes_price_cents, 1), 4),
                    actual_stake=round(count * yes_price_cents / 100.0, 2),
                    reason="poll_unavailable_trusting_create",
                    raw_response=raw,
                )
            continue
        state, info = _classify_order_state(poll_resp)
        if state == "filled":
            fc = info.get("fill_count") or count
            fp = info.get("fill_price") or yes_price_cents
            return PlacementResult(
                status="placed",
                bet_id=bid,
                actual_odds=round(100.0 / max(fp, 1), 4),
                actual_stake=round(fc * fp / 100.0, 2),
                raw_response=raw,
            )
        if state in {"canceled", "failed"}:
            return PlacementResult(
                status="failed",
                bet_id=bid,
                reason=info.get("reason") or state,
                raw_response=raw,
            )

    # Still resting after the poll budget — cancel and report failed.
    cancel_reason = "unfilled_within_5s"
    try:
        _portfolio.cancel_order(order_id)
    except Exception as e:
        logger.error(f"[kalshi] cancel_order on resting timeout failed: {e}")
        cancel_reason = "unfilled_cancel_failed"
    return PlacementResult(
        status="failed",
        bet_id=bid,
        reason=cancel_reason,
        raw_response=raw,
    )


from . import Strategy  # noqa: E402

strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    sync_history=_sync_history,
    navigate_to_event=_navigate_to_event,
    prep_betslip=_prep_betslip,
    check_live_price=_check_live_price,
    place_bet=_place_bet,
)
