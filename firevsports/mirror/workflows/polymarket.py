"""PolymarketWorkflow — API-first automation for Polymarket via py-clob-client SDK.

Uses CLOB API for: balance, prices, order placement, positions.
Uses DOM for: navigation (visual context), redeem/claim (on-chain tx).
Falls back to DOM for all methods if POLY_PRIVATE_KEY not configured.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, PositionEntry, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Lazy-loaded SDK types (only available if py-clob-client installed)
_ClobClient = None
_OrderArgs = None
_ApiCreds = None
_BalanceAllowanceParams = None
_AssetType = None
_BUY = None
_OrderType = None


def _load_sdk():
    """Lazy-load py-clob-client SDK types. Returns True if available."""
    global _ClobClient, _OrderArgs, _ApiCreds
    global _BalanceAllowanceParams, _AssetType, _BUY, _OrderType
    if _ClobClient is not None:
        return True
    try:
        from py_clob_client.client import ClobClient as _CC
        from py_clob_client.clob_types import (
            ApiCreds as _AC,
        )
        from py_clob_client.clob_types import (
            AssetType as _AT,
        )
        from py_clob_client.clob_types import (
            BalanceAllowanceParams as _BAP,
        )
        from py_clob_client.clob_types import (
            OrderArgs as _OA,
        )
        from py_clob_client.clob_types import (
            OrderType as _OT,
        )
        from py_clob_client.order_builder.constants import BUY as _B

        _ClobClient = _CC
        _OrderArgs = _OA
        _ApiCreds = _AC
        _BalanceAllowanceParams = _BAP
        _AssetType = _AT
        _BUY = _B
        _OrderType = _OT
        return True
    except ImportError:
        logger.warning("[polymarket] py-clob-client not installed — API features disabled")
        return False


class PolymarketWorkflow(ProviderWorkflow):
    platform = "polymarket"
    autonomous_placement = True  # place_bet() submits order via SDK on user confirm

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)
        self._client = None  # ClobClient instance (None if no key)
        self._pending_order = None  # Signed order awaiting submission
        self._pending_price: float = 0.0
        self._pending_size: float = 0.0
        self._tabs: dict[str, Page] = {}
        self._init_client()

    def _init_client(self):
        """Initialize CLOB client from env vars. No-op if key missing."""
        key = os.getenv("POLY_PRIVATE_KEY")
        funder = os.getenv("POLY_FUNDER_ADDRESS")
        if not key:
            logger.info("[polymarket] No POLY_PRIVATE_KEY — DOM-only mode")
            return
        if not _load_sdk():
            return
        try:
            sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))
            self._client = _ClobClient(
                host="https://clob.polymarket.com",
                key=key,
                chain_id=137,
                signature_type=sig_type,
                funder=funder,
            )
            self._client.set_api_creds(self._client.create_or_derive_api_creds())
            logger.info("[polymarket] CLOB client initialized (API mode)")
        except Exception as e:
            logger.error(f"[polymarket] CLOB client init failed: {e}")
            self._client = None

    @property
    def has_api(self) -> bool:
        """True if CLOB client is initialized and ready."""
        return self._client is not None

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: Page) -> bool:
        """Check login: API balance check if available, else DOM scrape."""
        if not self.has_api:
            return await self._check_login_dom(page)
        try:
            result = self._client.get_balance_allowance(
                params=_BalanceAllowanceParams(asset_type=_AssetType.COLLATERAL)
            )
            return result is not None and "balance" in result
        except Exception as e:
            logger.warning(f"[polymarket] API check_login failed, trying DOM: {e}")
            return await self._check_login_dom(page)

    async def sync_balance(self, page: Page) -> float:
        """Read USDC balance: API if available, else DOM scrape."""
        if not self.has_api:
            return await self._sync_balance_dom(page)
        try:
            result = self._client.get_balance_allowance(
                params=_BalanceAllowanceParams(asset_type=_AssetType.COLLATERAL)
            )
            balance = float(result.get("balance", 0))
            # If balance looks like raw wei (> 1M), convert from 6 decimals
            if balance > 1_000_000:
                balance = balance / 1e6
            logger.info(f"[polymarket] API balance: ${balance:.2f}")
            return balance
        except Exception as e:
            logger.warning(f"[polymarket] API sync_balance failed, trying DOM: {e}")
            return await self._sync_balance_dom(page)

    # ------------------------------------------------------------------
    # History sync
    # ------------------------------------------------------------------

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Sync trade history: API if available, else DOM scrape + fuzzy match."""
        if not self.has_api:
            return await self._sync_history_dom(page)

        import requests as req
        from rapidfuzz import fuzz

        from ...db.models import Bet, Event, get_session
        from ...repositories.profile_repo import ProfileRepo
        from ...services.bet_service import BetService

        address = os.getenv("POLY_FUNDER_ADDRESS", "")
        if not address:
            return await self._sync_history_dom(page)

        # Fetch trades from Data API
        try:
            resp = req.get(
                "https://data-api.polymarket.com/trades",
                params={"user": address.lower()},
                timeout=15,
            )
            resp.raise_for_status()
            trades = resp.json()
        except Exception as e:
            logger.warning(f"[polymarket] API trades failed, falling back to DOM: {e}")
            return await self._sync_history_dom(page)

        if not trades:
            logger.info("[polymarket] API: no trades found")
            return []

        logger.info(f"[polymarket] API: {len(trades)} trades fetched")

        db = get_session()
        history_results: list[HistoryEntry] = []
        try:
            profile = ProfileRepo(db).get_active()
            if not profile:
                return []

            pending = (
                db.query(Bet, Event)
                .join(Event, Bet.event_id == Event.id, isouter=True)
                .filter(
                    Bet.profile_id == profile.id,
                    Bet.provider_id == "polymarket",
                    Bet.result == "pending",
                )
                .all()
            )

            bet_service = BetService(db)
            settled_ids: set[int] = set()

            for trade in trades:
                trade_market = trade.get("market", "") or trade.get("title", "")
                trade_status = trade.get("status", "")
                trade_outcome = trade.get("outcome", "")

                if not trade_market:
                    continue

                for bet, event in pending:
                    if bet.id in settled_ids:
                        continue
                    event_name = ""
                    if event:
                        h = event.display_home or event.home_team or ""
                        a = event.display_away or event.away_team or ""
                        event_name = f"{h} vs {a}" if h and a else h or a

                    score = fuzz.token_set_ratio(trade_market.lower(), event_name.lower())
                    if score < 60:
                        continue

                    if trade_status in ("RESOLVED", "REDEEMED"):
                        payout = float(trade.get("payout", 0))
                        result_str = "won" if payout > 0 else "lost"

                        try:
                            bet_service.settle_bet(bet.id, result_str, round(payout, 2))
                            settled_ids.add(bet.id)
                            history_results.append(
                                HistoryEntry(
                                    provider_bet_id=str(bet.id),
                                    event_name=trade_market[:80],
                                    market=bet.market or "1x2",
                                    outcome=bet.outcome or trade_outcome,
                                    odds=bet.odds,
                                    stake=bet.stake,
                                    status=result_str,
                                    payout=round(payout, 2),
                                )
                            )
                            logger.info(f"[polymarket] API settled bet #{bet.id} → {result_str} (payout=${payout:.2f})")
                        except Exception as e:
                            logger.warning(f"[polymarket] settle failed for bet #{bet.id}: {e}")
                        break

            db.commit()
            logger.info(f"[polymarket] API sync_history: {len(history_results)} settled")

        except Exception as e:
            db.rollback()
            logger.error(f"[polymarket] sync_history error: {e}", exc_info=True)
        finally:
            db.close()

        return history_results

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate to market page. API mode: visual only. DOM mode: full fill."""
        if not self.has_api:
            return await self._navigate_and_fill_dom(page, bet)

        slug = getattr(bet, "event_slug", None) or getattr(bet, "market_slug", None)
        if not slug:
            logger.warning(f"[{self.provider_id}] No slug on bet")
            return False

        url = f"https://polymarket.com/event/{slug}"
        logger.info(f"[polymarket] navigate_to_event: {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector("button", timeout=10000)
            return True
        except Exception as e:
            logger.warning(f"[polymarket] navigate failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Bet preparation + placement
    # ------------------------------------------------------------------

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        """Phase 1: Build and sign order. API mode: SDK. DOM mode: returns price from click."""
        if not self.has_api:
            # DOM mode: navigate_and_fill already clicked the outcome + filled stake.
            # Return the ¢ price captured during the click so UI can show it.
            cents = getattr(self, "_last_click_cents", None)
            live_odds = round(1.0 / (cents / 100), 3) if cents and cents > 0 else None
            reason = f"{cents}¢" if cents else None
            return PlacementResult(status="prepped", bet_id=0, actual_stake=stake, actual_odds=live_odds, reason=reason)

        token_id = getattr(bet, "token_id", None)
        if not token_id:
            logger.warning("[polymarket] No token_id — cannot prep via API")
            return PlacementResult(status="failed", bet_id=0, reason="no token_id in provider_meta")

        try:
            book = self._client.get_order_book(token_id)
            asks = book.asks if hasattr(book, "asks") else book.get("asks", [])
            if not asks:
                return PlacementResult(status="failed", bet_id=0, reason="empty orderbook (no asks)")

            best_ask = float(asks[0].price if hasattr(asks[0], "price") else asks[0]["price"])
            if best_ask <= 0 or best_ask >= 1:
                return PlacementResult(status="failed", bet_id=0, reason=f"invalid ask price: {best_ask}")

            size = round(stake / best_ask, 2)

            order_args = _OrderArgs(
                price=best_ask,
                size=size,
                side=_BUY,
                token_id=token_id,
            )
            self._pending_order = self._client.create_order(order_args)
            self._pending_price = best_ask
            self._pending_size = size

            live_odds = round(1.0 / best_ask, 3)
            logger.info(f"[polymarket] Order signed: {size} shares @ {best_ask:.4f} (${stake:.2f}, odds={live_odds})")
            return PlacementResult(
                status="prepped",
                bet_id=0,
                actual_odds=live_odds,
                actual_stake=stake,
                reason=f"{size:.1f} shares @ {best_ask:.4f}",
            )
        except Exception as e:
            logger.error(f"[polymarket] prep_betslip failed: {e}", exc_info=True)
            return PlacementResult(status="failed", bet_id=0, reason=str(e))

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Submit the pre-signed order to CLOB. Called by play loop on user confirm."""
        if not self.has_api or not self._pending_order:
            logger.info(f"[polymarket] Manual placement: bet {getattr(bet, 'bet_id', '?')} stake=${stake}")
            return PlacementResult(status="placed", bet_id=0, actual_stake=stake)

        try:
            resp = self._client.post_order(self._pending_order, _OrderType.GTC)

            if isinstance(resp, dict):
                order_id = resp.get("orderID") or resp.get("id", "")
                success = resp.get("success", False)
                error_msg = resp.get("errorMsg", "")
            else:
                order_id = getattr(resp, "orderID", "") or getattr(resp, "id", "")
                success = getattr(resp, "success", False)
                error_msg = getattr(resp, "errorMsg", "")

            if success:
                actual_stake = round(self._pending_size * self._pending_price, 2)
                actual_odds = round(1.0 / self._pending_price, 3)
                logger.info(f"[polymarket] Order placed: id={order_id} stake=${actual_stake} odds={actual_odds}")
                return PlacementResult(
                    status="placed",
                    bet_id=order_id or 0,
                    actual_odds=actual_odds,
                    actual_stake=actual_stake,
                )
            else:
                logger.warning(f"[polymarket] Order rejected: {error_msg}")
                return PlacementResult(status="failed", bet_id=0, reason=error_msg or "order rejected")
        except Exception as e:
            logger.error(f"[polymarket] place_bet failed: {e}", exc_info=True)
            return PlacementResult(status="failed", bet_id=0, reason=str(e))
        finally:
            self._pending_order = None
            self._pending_price = 0.0
            self._pending_size = 0.0

    # ------------------------------------------------------------------
    # Live price
    # ------------------------------------------------------------------

    async def check_live_price(self, page: Page, bet) -> tuple[float | None, float | None]:
        """Read live odds from CLOB orderbook. Falls back to DOM if no API."""
        if not self.has_api:
            return await self._check_live_price_dom(page, bet)

        token_id = getattr(bet, "token_id", None)
        fair_odds = getattr(bet, "fair_odds", None)
        if not token_id or not fair_odds:
            return None, None

        try:
            book = self._client.get_order_book(token_id)
            asks = book.asks if hasattr(book, "asks") else book.get("asks", [])
            if not asks:
                return None, None

            best_ask = float(asks[0].price if hasattr(asks[0], "price") else asks[0]["price"])
            if best_ask <= 0 or best_ask >= 1:
                return None, None

            live_odds = round(1.0 / best_ask, 3)

            from ...analysis.value import compute_edge

            edge = compute_edge("polymarket", live_odds, fair_odds)
            return live_odds, edge
        except Exception as e:
            logger.warning(f"[polymarket] API check_live_price failed: {e}")
            return None, None

    # ------------------------------------------------------------------
    # Positions (Data API)
    # ------------------------------------------------------------------

    async def fetch_positions(self, page: Page) -> list[PositionEntry]:
        """Fetch open positions from Data API. Falls back to empty if no API."""
        if not self.has_api:
            return []

        import requests as req

        address = os.getenv("POLY_FUNDER_ADDRESS", "")
        if not address:
            return []

        try:
            resp = req.get(
                "https://data-api.polymarket.com/positions",
                params={"user": address.lower()},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            positions = []
            for p in data:
                size = float(p.get("size", 0))
                if size <= 0:
                    continue
                avg_price = float(p.get("avgPrice", 0))
                title = p.get("title", "") or p.get("market", "")
                outcome = p.get("outcome", "")

                positions.append(
                    PositionEntry(
                        provider_bet_id=p.get("asset", ""),
                        event_name=title[:80],
                        market="1x2",
                        outcome=outcome,
                        odds=round(1.0 / avg_price, 3) if avg_price > 0 else 2.0,
                        stake=round(size * avg_price, 2),
                        potential_payout=round(size, 2),
                    )
                )

            logger.info(f"[polymarket] API: {len(positions)} open positions")
            return positions
        except Exception as e:
            logger.warning(f"[polymarket] fetch_positions API failed: {e}")
            return []

    # ------------------------------------------------------------------
    # DOM fallback methods (existing implementations)
    # ------------------------------------------------------------------

    async def _check_login_dom(self, page: Page) -> bool:
        """Check if logged in: look for 'Cash $' (positive) and absence of 'Log In'/'Sign Up' (negative)."""
        try:
            result = await page.evaluate(
                """() => {
                // Negative check: if Log In / Sign Up buttons visible → NOT logged in
                const btns = document.querySelectorAll('button, a');
                for (const btn of btns) {
                    const t = (btn.textContent || '').trim();
                    if (t === 'Log In' || t === 'Sign Up') return {logged_in: false};
                }
                // Positive check: Cash/$ anywhere in nav/header, or Deposit button (only shown when logged in)
                const body = document.body.innerText || '';
                if (body.includes('Deposit') && body.includes('Withdraw')) return {logged_in: true};
                const els = document.querySelectorAll('nav *, header *, [class*="wallet"] *, [class*="user"] *');
                for (const el of els) {
                    const t = (el.textContent || '').trim();
                    if (t.includes('Cash') && t.includes('$')) return {logged_in: true, text: t};
                }
                // Check for portfolio link (only visible when logged in)
                for (const a of document.querySelectorAll('a[href*="portfolio"]')) {
                    if (a.offsetParent !== null) return {logged_in: true};
                }
                return {logged_in: false};
            }"""
            )
            return result.get("logged_in", False) if isinstance(result, dict) else False
        except Exception as e:
            logger.warning(f"[{self.provider_id}] check_login DOM failed: {e}")
            return False

    async def _sync_balance_dom(self, page: Page) -> float:
        """Scrape USDC cash balance from DOM — specifically the Cash amount, not Portfolio."""
        try:
            amount = await page.evaluate(
                r"""() => {
                // Look for leaf elements whose text starts with "Cash$" — avoids
                // parent divs like "Portfolio$44.72Cash$24.89" where the first $
                // amount is the portfolio value, not cash.
                const all = document.querySelectorAll('nav *, header *');
                for (const el of all) {
                    const t = (el.textContent || '').trim();
                    if (t.startsWith('Cash') && t.includes('$') && t.length < 30) {
                        const m = t.match(/\$(\d[\d,.]*)/);
                        if (m) return parseFloat(m[1].replace(',', ''));
                    }
                }
                return null;
            }"""
            )
            if amount is not None:
                logger.info(f"[polymarket] DOM balance: ${amount:.2f}")
            return amount if amount is not None else -1
        except Exception as e:
            logger.warning(f"[{self.provider_id}] sync_balance DOM failed: {e}")
            return -1

    async def _check_live_price_dom(self, page: Page, bet) -> tuple[float | None, float | None]:
        """DOM fallback: read prices from button text via mirror service."""
        from ...analysis.value import compute_edge

        fair_odds = getattr(bet, "fair_odds", None)
        if not fair_odds:
            return None, None

        try:
            from ...api.routes.mirror import _get_active_mirror

            mirror = _get_active_mirror()
            if mirror is None:
                return None, None

            original_outcome = getattr(bet, "original_outcome", getattr(bet, "outcome", ""))
            market_type = getattr(bet, "market", "1x2")
            btn_data = await mirror._read_btn_prices(page)
            matched = mirror._find_btn_for_market(
                btn_data,
                original_outcome,
                market_type,
                home_name=getattr(bet, "display_home", ""),
                away_name=getattr(bet, "display_away", ""),
            )
            if not matched or matched.get("price") is None:
                return None, None

            live_price = matched["price"]
            if live_price <= 0 or live_price >= 1:
                return None, None

            live_odds = 1.0 / live_price
            return round(live_odds, 3), compute_edge("polymarket", live_odds, fair_odds)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] DOM check_live_price failed: {e}")
            return None, None

    async def _navigate_and_fill_dom(self, page: Page, bet) -> bool:
        """DOM fallback: navigate + click correct outcome + type stake into Amount input."""
        slug = getattr(bet, "market_slug", None) or getattr(bet, "event_slug", None)
        if not slug:
            logger.warning(f"[{self.provider_id}] No slug on bet {getattr(bet, 'bet_id', '?')}")
            return False

        outcome = getattr(bet, "poly_outcome", None) or getattr(bet, "outcome", "")
        original_outcome = getattr(bet, "original_outcome", outcome)
        stake = getattr(bet, "stake", 0)
        home_name = (getattr(bet, "display_home", "") or getattr(bet, "poly_home", "") or "").strip()
        away_name = (getattr(bet, "display_away", "") or getattr(bet, "poly_away", "") or "").strip()

        url = f"https://polymarket.com/event/{slug}"
        logger.info(
            f"[polymarket] DOM navigate: {url} outcome={original_outcome} "
            f"home={home_name} away={away_name} stake=${stake}"
        )
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate failed: {e}")
            return False

        try:
            await page.wait_for_selector("button", timeout=10000)
        except Exception:
            await asyncio.sleep(5)

        # Click outcome in the Moneyline row ONLY — page has multiple sections
        # (Moneyline, Game 1 Winner, Handicap) each with their own ¢ buttons.
        # Extraction maps 1st outcome → home, 2nd → away within Moneyline.
        # Resolve target team name for button matching.
        # Polymarket button order doesn't always match home/away — e.g. page title
        # "Dignitas vs Cloud9" but buttons show "c9 85¢ | dig 16¢" (C9 first).
        # Match by team name instead of index.
        outcome_lower = (original_outcome or outcome).lower()
        if outcome_lower in ("home", "1"):
            target_name = home_name.lower()
        elif outcome_lower in ("away", "2"):
            target_name = away_name.lower()
        elif outcome_lower == "over":
            target_name = "over"
        elif outcome_lower == "under":
            target_name = "under"
        else:
            target_name = outcome_lower

        try:
            clicked = await page.evaluate(
                """(targetName) => {
                // Find the Moneyline section — look for text "Moneyline" then get its ¢ buttons
                const allText = document.querySelectorAll('div, span, p, h2, h3, h4');
                let moneylineContainer = null;
                for (const el of allText) {
                    const t = (el.textContent || '').trim();
                    if (t === 'Moneyline' && el.tagName !== 'BUTTON') {
                        let parent = el.parentElement;
                        for (let i = 0; i < 6 && parent; i++) {
                            const btns = parent.querySelectorAll('button');
                            let centCount = 0;
                            for (const b of btns) {
                                if (b.textContent.includes('¢')) centCount++;
                            }
                            if (centCount >= 2) {
                                moneylineContainer = parent;
                                break;
                            }
                            parent = parent.parentElement;
                        }
                        if (moneylineContainer) break;
                    }
                }

                // Collect ¢ buttons from the Moneyline section
                let centBtns = [];
                if (moneylineContainer) {
                    for (const btn of moneylineContainer.querySelectorAll('button')) {
                        const text = (btn.textContent || '').trim();
                        if (text.includes('¢') && text.length < 60) {
                            centBtns.push(btn);
                        }
                    }
                }

                // Fallback: if no Moneyline section found, use the FIRST pair of ¢ buttons
                if (centBtns.length < 2) {
                    centBtns = [];
                    for (const btn of document.querySelectorAll('button')) {
                        const text = (btn.textContent || '').trim();
                        if (text.includes('¢') && text.length < 60) {
                            centBtns.push(btn);
                        }
                        if (centBtns.length >= 2) break;
                    }
                }

                // Match by team name — button text is like "c985¢" or "dig16¢"
                // Compare against the full target name and common abbreviations.
                const tn = targetName.toLowerCase();
                let bestBtn = null;
                let bestIdx = -1;
                for (let i = 0; i < centBtns.length; i++) {
                    const btnText = centBtns[i].textContent.trim().toLowerCase();
                    // Strip the ¢ price suffix to get the team part
                    const teamPart = btnText.replace(/\\d+¢.*/, '').trim();
                    // Check: button team matches start/substring of target name
                    if (teamPart && (tn.startsWith(teamPart) || teamPart.startsWith(tn.slice(0, 3))
                        || tn.includes(teamPart) || teamPart.includes(tn.slice(0, 4)))) {
                        bestBtn = centBtns[i];
                        bestIdx = i;
                        break;
                    }
                }

                // Fallback: if no name match, use first button
                if (!bestBtn && centBtns.length > 0) {
                    bestBtn = centBtns[0];
                    bestIdx = 0;
                }

                if (bestBtn) {
                    bestBtn.scrollIntoView({block: 'center'});
                    bestBtn.click();
                    const priceMatch = bestBtn.textContent.match(/(\\d+)¢/);
                    const cents = priceMatch ? parseInt(priceMatch[1]) : null;
                    return {
                        clicked: bestBtn.textContent.trim().slice(0, 50),
                        index: bestIdx,
                        total: centBtns.length,
                        cents: cents,
                        moneyline: !!moneylineContainer,
                        targetName: targetName
                    };
                }
                return null;
            }""",
                target_name,
            )
            if clicked:
                cents = clicked.get("cents")
                logger.info(
                    f"[polymarket] DOM: Clicked '{clicked.get('clicked')}' "
                    f"(target='{target_name}', idx={clicked.get('index')}, "
                    f"moneyline={clicked.get('moneyline')}, {cents}¢)"
                )
                # Store live price for bet_ready broadcast
                if cents and cents > 0:
                    self._last_click_cents = cents
                await asyncio.sleep(1)
            else:
                logger.warning(f"[polymarket] DOM: No ¢ button matching target='{target_name}'")
        except Exception as e:
            logger.warning(f"[polymarket] DOM: Could not click outcome: {e}")

        # Fill stake by typing into the Amount input field (supports decimals)
        if stake > 0:
            stake_str = f"{stake:.2f}" if stake != int(stake) else str(int(stake))
            try:
                filled = await page.evaluate(
                    """(amount) => {
                    // Find the Amount input — it's an <input> near text "Amount"
                    const inputs = document.querySelectorAll('input[type="text"], input[type="number"], input:not([type])');
                    for (const input of inputs) {
                        // Check if this input or its parent/sibling has "Amount" text
                        const parent = input.closest('div, label, fieldset');
                        const context = parent ? parent.textContent : '';
                        if (context.includes('Amount') || input.placeholder === '$0' ||
                            input.placeholder === '$0.00' || input.placeholder === '0' ||
                            input.placeholder === 'Amount') {
                            // Clear existing value and type new amount
                            const nativeSetter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value').set;
                            nativeSetter.call(input, amount);
                            input.dispatchEvent(new Event('input', {bubbles: true}));
                            input.dispatchEvent(new Event('change', {bubbles: true}));
                            return {filled: true, value: amount};
                        }
                    }
                    // Fallback: try any visible input with $ nearby
                    for (const input of inputs) {
                        const rect = input.getBoundingClientRect();
                        if (rect.width > 50 && rect.height > 20 && rect.top > 100) {
                            const parent = input.closest('div');
                            if (parent && parent.textContent.includes('$')) {
                                const nativeSetter = Object.getOwnPropertyDescriptor(
                                    window.HTMLInputElement.prototype, 'value').set;
                                nativeSetter.call(input, amount);
                                input.dispatchEvent(new Event('input', {bubbles: true}));
                                input.dispatchEvent(new Event('change', {bubbles: true}));
                                return {filled: true, value: amount, method: 'fallback'};
                            }
                        }
                    }
                    return {filled: false};
                }""",
                    stake_str,
                )
                if filled and filled.get("filled"):
                    logger.info(f"[polymarket] DOM: Filled stake ${stake_str} into Amount input")
                else:
                    logger.warning("[polymarket] DOM: Could not find Amount input — stake not filled")
            except Exception as e:
                logger.warning(f"[polymarket] DOM: Stake fill failed: {e}")

        return True

    async def _sync_history_dom(self, page: Page) -> list[HistoryEntry]:
        """DOM fallback: scrape History tab and return entries. PendingLoop handles settlement."""
        # Navigate to History tab
        if "/portfolio" not in (page.url or "") or "tab=history" not in (page.url or ""):
            await page.goto(
                "https://polymarket.com/portfolio?tab=history",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(4)
        elif "tab=history" not in (page.url or ""):
            await page.evaluate(
                """() => {
                const tabs = document.querySelectorAll('a, button, div[role="tab"]');
                for (const t of tabs) {
                    if ((t.textContent || '').trim() === 'History') { t.click(); return true; }
                }
                return false;
            }"""
            )
            await asyncio.sleep(3)

        entries = await self.scrape_history(page)
        if not entries:
            logger.info("[polymarket] No history entries found")
            return []

        logger.info(f"[polymarket] sync_history DOM: {len(entries)} entries scraped")

        history_results: list[HistoryEntry] = []
        for entry in entries:
            activity = entry.get("activity", "")
            market = entry.get("market", "")
            value = float(entry.get("value", 0) or 0)
            shares = float(entry.get("shares", 0) or 0)
            outcome = entry.get("outcomeTag", "") or ""

            if not market or value <= 0:
                continue

            if activity == "Bought":
                status, payout = "pending", 0.0
                odds = round(1.0 / (value / shares), 4) if shares > 0 else 0.0
                stake = round(value, 2)
            elif activity == "Lost":
                status, payout = "lost", 0.0
                odds = round(1.0 / (value / shares), 4) if shares > 0 else 0.0
                stake = round(value, 2)
            elif activity == "Claimed":
                status, payout = "won", round(abs(value), 2)
                odds = 0.0  # Claimed entries have payout but not original odds/stake
                stake = 0.0
            else:
                continue

            history_results.append(
                HistoryEntry(
                    provider_bet_id="",
                    event_name=market[:120],
                    market="1x2",
                    outcome=outcome,
                    odds=odds,
                    stake=stake,
                    status=status,
                    payout=payout,
                )
            )

        return history_results

    # ------------------------------------------------------------------
    # Modal dismissal
    # ------------------------------------------------------------------

    async def _dismiss_modal(self, page: Page, max_attempts: int = 3) -> bool:
        """Dismiss Share/overlay modals that appear after Claim/Redeem."""
        for _attempt in range(max_attempts):
            dismissed = await page.evaluate(
                """() => {
                const closeSelectors = [
                    'button[aria-label="Close"]',
                    'button[aria-label="close"]',
                    '[class*="close" i]:not(a)',
                    '[class*="dismiss" i]',
                ];
                for (const sel of closeSelectors) {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) {
                        el.click();
                        return 'close_button';
                    }
                }
                const modals = document.querySelectorAll('[class*="modal" i], [class*="overlay" i], [class*="dialog" i], [role="dialog"]');
                for (const modal of modals) {
                    if (!modal.offsetParent) continue;
                    const btns = modal.querySelectorAll('button, [role="button"], svg');
                    for (const btn of btns) {
                        const rect = btn.getBoundingClientRect();
                        const modalRect = modal.getBoundingClientRect();
                        if (rect.right > modalRect.right - 80 && rect.top < modalRect.top + 80 && rect.width < 60) {
                            btn.click();
                            return 'modal_x';
                        }
                    }
                }
                return null;
            }"""
            )

            if dismissed:
                logger.info(f"[polymarket] Dismissed modal via {dismissed}")
                await asyncio.sleep(1)
                return True

            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
                still_open = await page.evaluate(
                    """() => {
                    const modals = document.querySelectorAll('[class*="modal" i], [class*="overlay" i], [role="dialog"]');
                    for (const m of modals) {
                        if (m.offsetParent !== null && m.offsetWidth > 200) return true;
                    }
                    return false;
                }"""
                )
                if not still_open:
                    logger.info("[polymarket] Dismissed modal via Escape")
                    return True
            except Exception:
                pass

            await asyncio.sleep(1)

        logger.warning("[polymarket] Could not dismiss modal after all attempts")
        return False

    # ------------------------------------------------------------------
    # Portfolio scraping + settlement (DOM-based — on-chain tx)
    # ------------------------------------------------------------------

    async def scrape_history(self, page: Page) -> list[dict]:
        """Scrape the History tab at /portfolio?tab=history."""
        current_url = page.url or ""
        if "tab=history" not in current_url:
            logger.info(f"[polymarket] Not on history tab ({current_url[:60]}), skipping scrape")
            return []

        rows = await page.evaluate(
            """() => {
            const results = [];
            const activityLabels = ['Bought', 'Lost', 'Claimed', 'Sold', 'Deposited', 'Withdrawn'];
            const allElements = document.querySelectorAll('div, span, p');

            const seen = new Set();
            for (const el of allElements) {
                const text = (el.textContent || '').trim();
                if (!activityLabels.includes(text)) continue;
                if (el.children.length > 2) continue;

                let row = el.parentElement;
                for (let i = 0; i < 6 && row; i++) {
                    if (row.offsetWidth > 500 && row.children.length >= 3) break;
                    row = row.parentElement;
                }
                if (!row) continue;

                const rowId = row.textContent.slice(0, 100);
                if (seen.has(rowId)) continue;
                seen.add(rowId);

                const activity = text;

                let market = '';
                const links = row.querySelectorAll('a, [href]');
                for (const a of links) {
                    const t = (a.textContent || '').trim();
                    if (t.length > market.length && t.length > 10 && !activityLabels.includes(t)) {
                        market = t;
                    }
                }
                if (!market) {
                    for (const child of row.querySelectorAll('span, p, div')) {
                        const t = (child.textContent || '').trim();
                        if (t.length > 20 && !t.includes('$') && !activityLabels.includes(t) && t.length > market.length) {
                            market = t.slice(0, 120);
                        }
                    }
                }

                let outcomeTag = '';
                let shares = 0;
                for (const child of row.querySelectorAll('span, div, p')) {
                    const t = (child.textContent || '').trim();
                    const tagMatch = t.match(/^(.+?)\\s+(\\d+)¢$/);
                    if (tagMatch && t.length < 50) {
                        outcomeTag = tagMatch[1];
                    }
                    const sharesMatch = t.match(/([\\d.]+)\\s*shares/);
                    if (sharesMatch) {
                        shares = parseFloat(sharesMatch[1]);
                    }
                }

                let value = 0;
                for (const child of row.querySelectorAll('span, p, div')) {
                    const t = (child.textContent || '').trim();
                    const valMatch = t.match(/^[+-]?\\$(\\d[\\d,.]*)/);
                    if (valMatch && child.children.length <= 1) {
                        value = parseFloat(valMatch[1].replace(',', ''));
                        if (t.startsWith('-')) value = -value;
                        break;
                    }
                }

                let timeAgo = '';
                for (const child of row.querySelectorAll('span, p, div')) {
                    const t = (child.textContent || '').trim();
                    if (t.match(/\\d+[hmd]\\s*ago|\\d+\\s*(hour|min|day|second)/i)) {
                        timeAgo = t;
                        break;
                    }
                }

                results.push({ activity, market: market.slice(0, 120), outcomeTag, shares, value, timeAgo });
            }
            return results;
        }"""
        )

        logger.info(f"[polymarket] Scraped {len(rows)} history entries")
        return rows

    async def scrape_portfolio(self, page: Page) -> list[dict]:
        """Scrape the portfolio/positions page and return each position."""
        current_url = page.url or ""
        if "/portfolio" not in current_url or "tab=history" in current_url:
            await page.goto(
                "https://polymarket.com/portfolio?tab=positions",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(4)

        debug_info = await page.evaluate(
            """() => {
            const info = {
                url: window.location.href,
                title: document.title,
                buttons: [],
            };

            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const t = (btn.textContent || '').trim();
                if (t === 'Redeem' || t === 'Sell') {
                    let parent = btn.parentElement;
                    let rowText = '';
                    for (let i = 0; i < 8 && parent; i++) {
                        rowText = (parent.textContent || '').trim();
                        if (rowText.length > 50 && rowText.includes('$')) break;
                        parent = parent.parentElement;
                    }
                    info.buttons.push({
                        type: t,
                        row_text: rowText.slice(0, 300),
                    });
                }
            }

            return info;
        }"""
        )

        logger.info(
            f"[polymarket] Portfolio page: {debug_info.get('url')}, buttons found: {len(debug_info.get('buttons', []))}"
        )
        for i, btn in enumerate(debug_info.get("buttons", [])):
            logger.info(f"[polymarket] Button {i}: type={btn.get('type')} text={btn.get('row_text', '')[:120]}")

        import re

        positions = []
        for btn_info in debug_info.get("buttons", []):
            text = btn_info.get("row_text", "")
            btn_type = btn_info.get("type", "")

            status = "open"
            if "WON" in text:
                status = "won"
            elif "LOST" in text:
                status = "lost"

            cent_prices = [float(m) for m in re.findall(r"([\d.]+)\s*(?:¢|\xc2\xa2|\u00a2)", text)]
            avg_price = cent_prices[0] if len(cent_prices) >= 1 else None
            now_price = cent_prices[1] if len(cent_prices) >= 2 else None

            dollar_values = [float(m.replace(",", "")) for m in re.findall(r"\$([\d,.]+)", text)]

            shares_match = re.search(r"([\d.]+)\s*shares", text)
            shares = float(shares_match.group(1)) if shares_match else None

            market = text[:60].split("\n")[0] if text else ""
            market = re.sub(r"[\d¢$→\xc2\xa2].+", "", market).strip()

            positions.append(
                {
                    "market": market[:80],
                    "full_text": text[:200],
                    "avg_price": avg_price,
                    "now_price": now_price,
                    "values": dollar_values,
                    "shares": shares,
                    "status": status,
                    "has_redeem": btn_type == "Redeem",
                    "has_sell": btn_type == "Sell",
                }
            )

        logger.info(f"[polymarket] Scraped {len(positions)} portfolio positions")
        return positions

    async def redeem_all(self, page: Page) -> dict:
        """Click Redeem buttons ONLY for finished positions (WON or LOST).

        NEVER clicks Sell on open positions — that would exit at market price.
        """
        if "/portfolio" not in (page.url or "") or "tab=history" in (page.url or ""):
            await page.goto(
                "https://polymarket.com/portfolio?tab=positions",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(3)

        redeemed = 0
        skipped_open = 0
        errors = 0

        count = await page.evaluate(
            """() => {
            const btns = document.querySelectorAll('button');
            let n = 0;
            for (const btn of btns) {
                if (btn.textContent.trim() !== 'Redeem') continue;
                let parent = btn.parentElement;
                for (let i = 0; i < 8 && parent; i++) {
                    const text = parent.textContent || '';
                    if (text.includes('Won') || text.includes('Lost') ||
                        text.includes('WON') || text.includes('LOST')) {
                        n++;
                        break;
                    }
                    parent = parent.parentElement;
                }
            }
            return n;
        }"""
        )

        logger.info(f"[polymarket] Found {count} redeemable finished positions")

        for i in range(count):
            try:
                clicked = await page.evaluate(
                    """() => {
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {
                        if (btn.textContent.trim() !== 'Redeem') continue;
                        let parent = btn.parentElement;
                        let isFinished = false;
                        for (let i = 0; i < 8 && parent; i++) {
                            const text = parent.textContent || '';
                            if (text.includes('Won') || text.includes('Lost') ||
                                text.includes('WON') || text.includes('LOST')) {
                                isFinished = true;
                                break;
                            }
                            parent = parent.parentElement;
                        }
                        if (isFinished) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }"""
                )
                if not clicked:
                    break

                await asyncio.sleep(2)

                confirmed = await page.evaluate(
                    """() => {
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {
                        const t = (btn.textContent || '').trim();
                        if (t.startsWith('Redeem $')) {
                            btn.click();
                            return t;
                        }
                    }
                    return null;
                }"""
                )
                if confirmed:
                    await asyncio.sleep(3)
                    await self._dismiss_modal(page)
                    redeemed += 1
                    logger.info(f"[polymarket] Redeemed {i + 1}/{count}: {confirmed}")
                else:
                    logger.warning(f"[polymarket] No confirm button found for redeem {i + 1}")
                    await self._dismiss_modal(page)
                    errors += 1
            except Exception as e:
                logger.warning(f"[polymarket] Redeem {i + 1} failed: {e}")
                errors += 1

        return {"redeemed": redeemed, "skipped_open": skipped_open, "errors": errors, "total": count}

    # ------------------------------------------------------------------
    # Claim banner
    # ------------------------------------------------------------------

    async def claim_banner(self, page: Page) -> dict:
        """Click the top-level Claim banner if present."""
        try:
            result = await page.evaluate(
                """() => {
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    const t = (btn.textContent || '').trim();
                    if (t === 'Claim' || t.startsWith('Claim')) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.top < 400) {
                            btn.click();
                            return {found: true, text: t};
                        }
                    }
                }
                return {found: false};
            }"""
            )

            if not result.get("found"):
                logger.info("[polymarket] No Claim banner found")
                return {"claimed": False, "amount": None}

            logger.info(f"[polymarket] Clicked Claim banner: {result.get('text')}")
            await asyncio.sleep(3)

            confirmed = await page.evaluate(
                """() => {
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    const t = (btn.textContent || '').trim();
                    if (t.startsWith('Claim $')) {
                        btn.click();
                        return t;
                    }
                }
                return null;
            }"""
            )

            if confirmed:
                await asyncio.sleep(3)
                logger.info(f"[polymarket] Claim confirmed: {confirmed}")
                await self._dismiss_modal(page)
                return {"claimed": True, "amount": confirmed}

            await self._dismiss_modal(page)
            return {"claimed": True, "amount": result.get("text")}

        except Exception as e:
            logger.warning(f"[polymarket] claim_banner failed: {e}")
            return {"claimed": False, "amount": None, "error": str(e)}

    # ------------------------------------------------------------------
    # Scan (preview only — no clicks)
    # ------------------------------------------------------------------

    async def scan_portfolio_settlements(self, page: Page) -> dict:
        """Scrape positions and match against pending bets — NO clicking."""
        from ...db.models import Bet, Event, get_session
        from ...repositories.profile_repo import ProfileRepo

        if "/portfolio" not in (page.url or "") or "tab=history" in (page.url or ""):
            await page.goto(
                "https://polymarket.com/portfolio?tab=positions",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(4)

        has_claim = await page.evaluate(
            """() => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const t = (btn.textContent || '').trim();
                if ((t === 'Claim' || t.startsWith('Claim')) && btn.getBoundingClientRect().top < 400) {
                    return t;
                }
            }
            return null;
        }"""
        )

        redeem_count = await page.evaluate(
            """() => {
            const btns = document.querySelectorAll('button');
            let n = 0;
            for (const btn of btns) {
                if (btn.textContent.trim() !== 'Redeem') continue;
                let parent = btn.parentElement;
                for (let i = 0; i < 8 && parent; i++) {
                    const text = parent.textContent || '';
                    if (text.includes('Won') || text.includes('Lost') ||
                        text.includes('WON') || text.includes('LOST')) {
                        n++;
                        break;
                    }
                    parent = parent.parentElement;
                }
            }
            return n;
        }"""
        )

        positions = await self.scrape_portfolio(page)

        db = get_session()
        try:
            profile = ProfileRepo(db).get_active()
            if not profile:
                return {"error": "no active profile"}

            pending = (
                db.query(Bet, Event)
                .join(Event, Bet.event_id == Event.id, isouter=True)
                .filter(
                    Bet.profile_id == profile.id,
                    Bet.provider_id == "polymarket",
                    Bet.result == "pending",
                )
                .all()
            )

            from ...services.fire_window import _match_polymarket_position

            matches = []
            for bet, event in pending:
                pos = _match_polymarket_position(bet, event, positions)
                if not pos:
                    continue
                status = pos.get("status", "open")
                if status not in ("won", "lost"):
                    continue

                payout = 0.0
                if status == "won":
                    vals = pos.get("values", [])
                    if vals:
                        payout = max(vals)

                event_name = ""
                if event:
                    h = event.display_home or event.home_team or ""
                    a = event.display_away or event.away_team or ""
                    event_name = f"{h} vs {a}" if h and a else h or a

                matches.append(
                    {
                        "bet_id": bet.id,
                        "event": event_name,
                        "market": bet.market,
                        "outcome": bet.outcome,
                        "odds": bet.odds,
                        "stake": bet.stake,
                        "result": status,
                        "payout": round(payout, 2),
                        "pl": round(payout - bet.stake, 2),
                    }
                )

            total_staked = sum(m["stake"] for m in matches)
            total_payout = sum(m["payout"] for m in matches)
            wins = [m for m in matches if m["result"] == "won"]
            losses = [m for m in matches if m["result"] == "lost"]

            return {
                "positions_scraped": len(positions),
                "positions": positions,
                "has_claim": has_claim,
                "redeem_count": redeem_count,
                "pending_bets": len(pending),
                "matches": matches,
                "summary": {
                    "wins": len(wins),
                    "losses": len(losses),
                    "total_staked": round(total_staked, 2),
                    "total_payout": round(total_payout, 2),
                    "net_pl": round(total_payout - total_staked, 2),
                },
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Full settle flow (execute after scan)
    # ------------------------------------------------------------------

    async def settle_all(self, page: Page) -> dict:
        """Full settlement: navigate → claim → redeem → settle DB → void ghosts."""
        from ...db.models import Bet, Event, get_session
        from ...repositories.profile_repo import ProfileRepo
        from ...services.bet_service import BetService

        if "/portfolio" not in (page.url or "") or "tab=history" in (page.url or ""):
            await page.goto(
                "https://polymarket.com/portfolio?tab=positions",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(4)

        claim_result = await self.claim_banner(page)

        if claim_result.get("claimed"):
            await asyncio.sleep(2)

        positions = await self.scrape_portfolio(page)

        db = get_session()
        settled = []
        try:
            profile = ProfileRepo(db).get_active()
            if not profile:
                return {"error": "no active profile", "claim": claim_result}

            pending = (
                db.query(Bet, Event)
                .join(Event, Bet.event_id == Event.id, isouter=True)
                .filter(
                    Bet.profile_id == profile.id,
                    Bet.provider_id == "polymarket",
                    Bet.result == "pending",
                )
                .all()
            )

            if not pending:
                imported = self._import_open_positions(db, profile, positions)
                return {
                    "claim": claim_result,
                    "positions": len(positions),
                    "imported": imported,
                    "settled": 0,
                }

            from ...services.fire_window import _match_polymarket_position

            matches = []
            for bet, event in pending:
                pos = _match_polymarket_position(bet, event, positions)
                if not pos:
                    continue
                status = pos.get("status", "open")
                if status not in ("won", "lost"):
                    continue

                payout = 0.0
                if status == "won":
                    vals = pos.get("values", [])
                    if vals:
                        payout = max(vals)

                event_name = ""
                if event:
                    h = event.display_home or event.home_team or ""
                    a = event.display_away or event.away_team or ""
                    event_name = f"{h} vs {a}" if h and a else h or a

                matches.append(
                    {
                        "bet_id": bet.id,
                        "event": event_name,
                        "market": bet.market,
                        "outcome": bet.outcome,
                        "odds": bet.odds,
                        "stake": bet.stake,
                        "result": status,
                        "payout": round(payout, 2),
                        "pl": round(payout - bet.stake, 2),
                    }
                )

            # Detect ghost bets
            matched_ids = {m["bet_id"] for m in matches}
            open_ids = set()
            for bet, event in pending:
                if bet.id in matched_ids:
                    continue
                pos = _match_polymarket_position(bet, event, positions)
                if pos and pos.get("status") == "open":
                    open_ids.add(bet.id)

            ghost_bets = []
            for bet, event in pending:
                if bet.id in matched_ids or bet.id in open_ids:
                    continue
                event_name = ""
                if event:
                    h = event.display_home or event.home_team or ""
                    a = event.display_away or event.away_team or ""
                    event_name = f"{h} vs {a}" if h and a else h or a
                ghost_bets.append(
                    {
                        "bet_id": bet.id,
                        "event": event_name,
                        "market": bet.market,
                        "outcome": bet.outcome,
                        "odds": bet.odds,
                        "stake": bet.stake,
                        "result": "void",
                        "payout": 0.0,
                        "pl": round(-bet.stake, 2),
                    }
                )
                logger.info(
                    f"[polymarket] Ghost bet #{bet.id} {event_name} — no position found, voiding (stake=${bet.stake})"
                )

            redeem_result = await self.redeem_all(page)

            bet_service = BetService(db)
            for m in matches:
                try:
                    bet_service.settle_bet(m["bet_id"], m["result"], m["payout"])
                    settled.append(m)
                    logger.info(
                        f"[polymarket] Settled bet #{m['bet_id']} {m['event']} → {m['result']} (payout=${m['payout']})"
                    )
                except Exception as e:
                    logger.warning(f"[polymarket] Failed to settle bet #{m['bet_id']}: {e}")
            for g in ghost_bets:
                try:
                    bet_service.settle_bet(g["bet_id"], "void", 0.0)
                    settled.append(g)
                except Exception as e:
                    logger.warning(f"[polymarket] Failed to void ghost bet #{g['bet_id']}: {e}")
            db.commit()

        except Exception as e:
            db.rollback()
            logger.error(f"[polymarket] settle_all DB error: {e}", exc_info=True)
            return {"error": str(e), "claim": claim_result}
        finally:
            db.close()

        new_balance = await self.sync_balance(page)

        total_staked = sum(s["stake"] for s in settled if s["result"] != "void")
        total_payout = sum(s["payout"] for s in settled if s["result"] != "void")
        wins = [s for s in settled if s["result"] == "won"]
        losses = [s for s in settled if s["result"] == "lost"]
        voids = [s for s in settled if s["result"] == "void"]

        return {
            "claim": claim_result,
            "redeem": redeem_result,
            "settled": len(settled),
            "settlements": settled,
            "summary": {
                "wins": len(wins),
                "losses": len(losses),
                "voids": len(voids),
                "total_staked": round(total_staked, 2),
                "total_payout": round(total_payout, 2),
                "net_pl": round(total_payout - total_staked, 2),
            },
            "new_balance": new_balance,
            "positions_scraped": len(positions),
        }

    # ------------------------------------------------------------------
    # Import untracked positions
    # ------------------------------------------------------------------

    def _import_open_positions(self, db, profile, positions: list[dict]) -> list[dict]:
        """Import open Polymarket positions that aren't in our DB as pending bets."""
        import re

        from ...db.models import Bet
        from ...services.bet_service import BetService

        existing = (
            db.query(Bet)
            .filter(
                Bet.profile_id == profile.id,
                Bet.provider_id == "polymarket",
                Bet.result == "pending",
            )
            .all()
        )
        existing_markets = {(b.confirmation_id or "").lower() for b in existing}

        seen_markets: set[str] = set()
        unique_positions = []
        for pos in positions:
            market = pos.get("market", "").strip()
            if not market or market in seen_markets:
                continue
            seen_markets.add(market)
            unique_positions.append(pos)

        svc = BetService(db)
        imported = []
        for pos in unique_positions:
            logger.info(
                f"[polymarket] Position: market={pos.get('market')} sell={pos.get('has_sell')} "
                f"redeem={pos.get('has_redeem')} status={pos.get('status')} "
                f"avg={pos.get('avg_price')} shares={pos.get('shares')}"
            )
            if not pos.get("has_sell"):
                continue
            if pos.get("status") in ("won", "lost"):
                continue

            market = pos.get("market", "")
            avg_price = pos.get("avg_price")
            shares = pos.get("shares")
            values = pos.get("values", [])

            slug = re.sub(r"[^a-z0-9]+", "-", market.lower()).strip("-")

            if slug.lower() in existing_markets:
                continue

            if shares and avg_price:
                stake = round(shares * avg_price / 100, 2)
            elif values:
                stake = round(values[0], 2)
            else:
                stake = 0
            odds = round(100 / avg_price, 4) if avg_price and avg_price > 0 else 2.0

            if stake <= 0:
                continue

            resp = svc.create_bet(
                event_id=None,
                provider_id="polymarket",
                market="1x2",
                outcome="home",
                odds=odds,
                stake=stake,
                bet_type="value",
            )
            if "error" not in resp:
                bet_id = resp.get("id")
                if bet_id:
                    db_bet = db.query(Bet).filter(Bet.id == bet_id).first()
                    if db_bet:
                        db_bet.confirmation_id = slug
                imported.append(
                    {
                        "bet_id": bet_id,
                        "market": market,
                        "stake": stake,
                        "odds": odds,
                        "avg_price": avg_price,
                        "shares": shares,
                    }
                )
                logger.info(f"[polymarket] Imported position: {market} stake=${stake} odds={odds}")
            else:
                logger.warning(f"[polymarket] Failed to import position: {resp['error']}")

        if imported:
            db.commit()
            logger.info(f"[polymarket] Imported {len(imported)} open positions as pending bets")

        return imported

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self, page: Page) -> None:
        """Close persistent Polymarket tabs opened during placement."""
        for _slug, tab in list(self._tabs.items()):
            try:
                if not tab.is_closed():
                    await tab.close()
            except Exception:
                pass
        self._tabs.clear()
