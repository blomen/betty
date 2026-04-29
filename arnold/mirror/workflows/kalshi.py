"""KalshiWorkflow — API-first automation for Kalshi via kalshi-python SDK.

Uses REST API for: balance, prices, order placement, history/fills.
Playwright tab is opened to https://kalshi.com/markets/<ticker> for visual
context only — no DOM automation.

Falls back to a no-op stub if KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PEM are
absent, or if kalshi-python is not installed. The stub never succeeds at
placement; it exists so missing creds don't crash the registry.

SDK reality check (vs. original plan):
    - Package:  `kalshi-python` (v2.1.4). Official OpenAPI-generated client.
    - Client:   `KalshiClient` (base) + per-resource `PortfolioApi` / `MarketsApi`.
                The plan's `ExchangeClient` name does not exist in this SDK.
    - Auth:     `client.set_kalshi_auth(key_id, private_key_path)` takes a FILE
                path, not a PEM string — so we materialize KALSHI_PRIVATE_KEY_PEM
                to a stable file under the project's data directory (not a
                per-process tempfile, which previously leaked a PEM per
                instantiation under %TEMP%).
    - Responses are pydantic v2 models, accessed via attributes (not `.get()`).
    - There is no `now_ts()` helper — use `int(time.time())`.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

_KalshiClient = None
_PortfolioApi = None
_MarketsApi = None


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


def _kalshi_key_path(pem_body: str):
    """Return the stable on-disk path for the Kalshi private key.

    The SDK's `set_kalshi_auth` takes a file path, not a PEM string. We
    materialize KALSHI_PRIVATE_KEY_PEM to `<data_dir>/kalshi_key.pem`
    (0600) and re-use it across runs instead of creating a new tempfile
    per workflow instantiation (which previously leaked under %TEMP%).

    Writes the file only when missing or out-of-date to avoid unnecessary
    churn on the filesystem.
    """
    # Import is deferred so importing this module doesn't force the paths
    # package to initialize when the SDK isn't even installed.
    try:
        from ...paths import get_data_dir  # type: ignore
    except ImportError:
        # Fallback for arnoldsports copy, which has no ...paths — use a
        # sibling `data/` folder next to the mirror root.
        import pathlib

        base = pathlib.Path(__file__).resolve().parents[2] / "data"
        base.mkdir(parents=True, exist_ok=True)
        key_path = base / "kalshi_key.pem"
    else:
        key_path = get_data_dir() / "kalshi_key.pem"

    # Write only if the body has changed (or the file is absent).
    try:
        existing = key_path.read_text(encoding="utf-8") if key_path.exists() else None
    except OSError:
        existing = None
    if existing != pem_body:
        key_path.write_text(pem_body, encoding="utf-8")
    # Restrict permissions to owner read/write (best-effort; on Windows
    # chmod is a no-op but the call is harmless).
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key_path


class KalshiWorkflow(ProviderWorkflow):
    platform = "kalshi"
    autonomous_placement = True

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)
        self._client = None  # KalshiClient (base, holds auth + http)
        self._portfolio = None  # PortfolioApi (balance, fills, orders)
        self._markets = None  # MarketsApi (get_market for live prices)
        self._key_path: str | None = None  # temp file path for the private key
        self._pending_ticker: str | None = None
        self._pending_count: int = 0
        self._pending_yes_price_cents: int = 0
        self._balance_cache: float | None = None
        self._init_client()

    def _init_client(self) -> None:
        key_id = os.getenv("KALSHI_API_KEY_ID")
        key_pem = os.getenv("KALSHI_PRIVATE_KEY_PEM")
        if not (key_id and key_pem):
            logger.info("[kalshi] No KALSHI_API_KEY_ID/PEM — API stub only")
            return
        if not _load_sdk():
            return
        try:
            # SDK requires a PEM **file path**, not a PEM string. Write to a
            # stable location under the project's data dir (reused across
            # runs) with 0600 perms — avoids the tempfile leak that
            # accumulated one file per workflow instantiation.
            pem_body = key_pem.replace("\\n", "\n")
            self._key_path = str(_kalshi_key_path(pem_body))

            self._client = _KalshiClient()
            self._client.set_kalshi_auth(key_id=key_id, private_key_path=self._key_path)
            self._portfolio = _PortfolioApi(api_client=self._client)
            self._markets = _MarketsApi(api_client=self._client)
            logger.info("[kalshi] SDK client initialized (API mode)")
        except Exception as e:
            logger.error(f"[kalshi] client init failed: {e}")
            self._client = None
            self._portfolio = None
            self._markets = None

    @property
    def has_api(self) -> bool:
        return self._portfolio is not None and self._markets is not None

    # ---------- Login / balance ----------

    async def check_login(self, page: Page) -> bool:
        # API auth is independent of web session; presence of a client is enough.
        return self.has_api

    async def sync_balance(self, page: Page) -> float:
        if not self.has_api:
            return 0.0
        try:
            resp = self._portfolio.get_balance()
            cents = getattr(resp, "balance", None) or 0
            value = round(float(cents) / 100.0, 2)
            self._balance_cache = value
            return value
        except Exception as e:
            logger.warning(f"[kalshi] sync_balance failed: {e}")
            if self._balance_cache is not None:
                return self._balance_cache
            return 0.0

    # ---------- History sync (for settlement reconciliation) ----------

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        if not self.has_api:
            return []
        try:
            resp = self._portfolio.get_fills(limit=200)
            fills = getattr(resp, "fills", None) or []
        except Exception as e:
            logger.warning(f"[kalshi] get_fills failed: {e}")
            return []
        out: list[HistoryEntry] = []
        for f in fills:
            ticker = getattr(f, "ticker", "") or ""
            side = getattr(f, "side", "") or ""
            count = int(getattr(f, "count", 0) or 0)
            price_cents = int(getattr(f, "price", 0) or 0)
            order_id = getattr(f, "order_id", None) or getattr(f, "fill_id", None) or ""
            odds = round(100.0 / max(price_cents, 1), 4) if price_cents else 0.0
            stake = round(count * price_cents / 100.0, 2)
            # The Fill model carries no settlement flag — settlement comes from
            # the Market/result endpoint, not Fills. Mark all as pending and let
            # a future pass reconcile via market settlement status.
            out.append(
                HistoryEntry(
                    provider_bet_id=str(order_id),
                    event_name=ticker,
                    market=ticker,
                    outcome=side,
                    odds=odds,
                    stake=stake,
                    status="pending",
                    payout=None,
                )
            )
        return out

    # ---------- Navigation (visual context only) ----------

    async def navigate_to_event(self, page: Page, bet) -> bool:
        ticker = getattr(bet, "provider_event_id", "") or ""
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

    # ---------- Placement ----------

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        # No DOM interaction; stash the order params for place_bet().
        bid = getattr(bet, "bet_id", None)
        if bid is None:
            bid = getattr(bet, "id", 0)
        self._pending_ticker = getattr(bet, "provider_market_ticker", None) or getattr(bet, "provider_event_id", None)
        if not self._pending_ticker:
            return PlacementResult(status="failed", bet_id=bid, reason="no_ticker")
        yes_price_dollars = self._infer_yes_price(bet)
        self._pending_yes_price_cents = max(1, min(99, int(round(yes_price_dollars * 100))))
        # round-nearest, not floor: floor systematically under-stakes
        self._pending_count = max(1, round(stake / max(yes_price_dollars, 0.01)))
        actual_stake = round(self._pending_count * yes_price_dollars, 2)
        return PlacementResult(
            status="ready",
            bet_id=bid,
            actual_odds=round(1.0 / yes_price_dollars, 4),
            actual_stake=actual_stake,
        )

    def _infer_yes_price(self, bet) -> float:
        # Bet carries the decimal odds we computed in extraction;
        # convert back to a YES-contract price target.
        odds = float(getattr(bet, "odds", 2.0))
        return max(0.01, min(0.99, round(1.0 / odds, 4)))

    async def check_live_price(self, page: Page, bet) -> tuple[float | None, float | None]:
        if not self.has_api or not self._pending_ticker:
            return None, None
        try:
            resp = self._markets.get_market(self._pending_ticker)
            mkt = getattr(resp, "market", None)
            if mkt is None:
                return None, None
            # SDK ships both yes_ask (cents int) and yes_ask_dollars (float 0-1).
            # Prefer dollars (newer field), fall back to cents.
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

    @staticmethod
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

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        import asyncio

        bid = getattr(bet, "bet_id", None)
        if bid is None:
            bid = getattr(bet, "id", 0)

        if not self.has_api or not self._pending_ticker:
            return PlacementResult(status="failed", bet_id=bid, reason="no_client")

        try:
            create_resp = self._portfolio.create_order(
                ticker=self._pending_ticker,
                action="buy",
                side="yes",
                type="limit",
                yes_price=self._pending_yes_price_cents,
                count=self._pending_count,
                expiration_ts=int(time.time()) + 60,
            )
        except Exception as e:
            logger.error(f"[kalshi] create_order failed: {e}")
            return PlacementResult(status="failed", bet_id=bid, reason=str(e))

        order_id = getattr(create_resp, "order_id", None)
        raw = create_resp.to_dict() if hasattr(create_resp, "to_dict") else None

        # Poll up to 5x at 1s intervals. After 2 consecutive polling errors,
        # trust the create response — a flaky GET shouldn't double-cancel a real fill.
        poll_errors = 0
        if order_id:
            for _ in range(5):
                await asyncio.sleep(1.0)
                try:
                    poll_resp = self._portfolio.get_order(order_id)
                    poll_errors = 0
                except Exception as e:
                    poll_errors += 1
                    logger.warning(f"[kalshi] get_order poll failed: {e}")
                    if poll_errors >= 2:
                        return PlacementResult(
                            status="placed",
                            bet_id=bid,
                            actual_odds=round(100.0 / max(self._pending_yes_price_cents, 1), 4),
                            actual_stake=round(self._pending_count * self._pending_yes_price_cents / 100.0, 2),
                            reason="poll_unavailable_trusting_create",
                            raw_response=raw,
                        )
                    continue
                state, info = self._classify_order_state(poll_resp)
                if state == "filled":
                    fc = info.get("fill_count") or self._pending_count
                    fp = info.get("fill_price") or self._pending_yes_price_cents
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
        if order_id:
            try:
                self._portfolio.cancel_order(order_id)
            except Exception as e:
                logger.error(f"[kalshi] cancel_order on resting timeout failed: {e}")
                cancel_reason = "unfilled_cancel_failed"
        return PlacementResult(
            status="failed",
            bet_id=bid,
            reason=cancel_reason,
            raw_response=raw,
        )
