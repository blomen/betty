"""PolymarketWorkflow — full DOM automation for Polymarket."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class PolymarketWorkflow(ProviderWorkflow):
    platform = "polymarket"

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.AUTONOMOUS):
        super().__init__(provider_id, domain, mode)
        self._tabs: dict[str, "Page"] = {}

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: "Page") -> bool:
        """Check if logged in by looking for 'Cash $XXX' in the nav."""
        try:
            text = await page.evaluate("""() => {
                const els = document.querySelectorAll('nav *');
                for (const el of els) {
                    const t = (el.textContent || '').trim();
                    if (t.startsWith('Cash') && t.includes('$')) return t;
                }
                return null;
            }""")
            return text is not None
        except Exception as e:
            logger.warning(f"[{self.provider_id}] check_login failed: {e}")
            return False

    async def sync_balance(self, page: "Page") -> float:
        """Scrape USDC cash balance from DOM nav text ('Cash$101.51')."""
        try:
            amount = await page.evaluate("""() => {
                const els = document.querySelectorAll('nav *');
                for (const el of els) {
                    const t = (el.textContent || '').trim();
                    if (t.startsWith('Cash') && t.includes('$')) {
                        const m = t.match(/\\$(\\d[\\d,.]*)/);
                        return m ? parseFloat(m[1].replace(',', '')) : null;
                    }
                }
                return null;
            }""")
            return amount if amount is not None else -1
        except Exception as e:
            logger.warning(f"[{self.provider_id}] sync_balance failed: {e}")
            return -1

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """No-op — Gamma API handles settlement separately."""
        return []

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Navigate to the Polymarket event page for this bet."""
        slug = getattr(bet, "market_slug", None)
        if not slug:
            logger.warning(f"[{self.provider_id}] No market_slug on bet {bet.bet_id}")
            return False

        url = f"https://polymarket.com/event/{slug}"
        logger.info(f"[{self.provider_id}] Navigating to {url}")

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Wait for trading buttons (React hydration)
            try:
                await page.wait_for_selector("button.trading-button", timeout=15000)
            except Exception:
                await asyncio.sleep(5)
            # Track persistent tab
            self._tabs[slug] = page
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate_to_event failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Bet placement
    # ------------------------------------------------------------------

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Place a bet by delegating to MirrorService._place_single_polymarket_bet."""
        from ...api.routes.mirror import _get_active_mirror

        mirror = _get_active_mirror()
        if mirror is None:
            return PlacementResult(
                status="failed",
                bet_id=bet.bet_id,
                reason="no_active_mirror",
            )

        slug = getattr(bet, "market_slug", "")
        outcome = getattr(bet, "poly_outcome", None) or getattr(bet, "outcome", "")
        original_outcome = getattr(bet, "original_outcome", outcome)
        market_type = getattr(bet, "market", "1x2")
        expected_price = 1.0 / getattr(bet, "odds", 2.0) if getattr(bet, "odds", 0) > 0 else 0.5

        try:
            result = await mirror._place_single_polymarket_bet(
                page=page,
                bet_id=bet.bet_id,
                slug=slug,
                outcome=outcome,
                amount=stake,
                expected_price=expected_price,
                max_slippage=0.05,
                original_outcome=original_outcome,
                market_type=market_type,
            )
            status = result.get("status", "failed")
            return PlacementResult(
                status="placed" if status == "success" else "failed",
                bet_id=bet.bet_id,
                actual_stake=result.get("amount"),
                actual_odds=result.get("price"),
                reason=result.get("error"),
                raw_response=result,
            )
        except Exception as e:
            logger.error(f"[{self.provider_id}] place_bet failed: {e}")
            return PlacementResult(
                status="failed",
                bet_id=bet.bet_id,
                reason=str(e),
            )

    # ------------------------------------------------------------------
    # Live price
    # ------------------------------------------------------------------

    async def check_live_price(self, page: "Page", bet) -> float | None:
        """Read live odds from DOM and compute edge vs fair odds."""
        from ...api.routes.mirror import _get_active_mirror
        from ...analysis.value import compute_edge

        mirror = _get_active_mirror()
        if mirror is None:
            return None

        original_outcome = getattr(bet, "original_outcome", getattr(bet, "outcome", ""))
        market_type = getattr(bet, "market", "1x2")
        fair_odds = getattr(bet, "fair_odds", None)
        if not fair_odds:
            return None

        try:
            btn_data = await mirror._read_btn_prices(page)
            home_name = getattr(bet, "display_home", "")
            away_name = getattr(bet, "display_away", "")
            matched = mirror._find_btn_for_market(
                btn_data, original_outcome, market_type,
                home_name=home_name, away_name=away_name,
            )
            if not matched or matched.get("price") is None:
                return None

            live_price = matched["price"]
            if live_price <= 0 or live_price >= 1:
                return None

            live_odds = 1.0 / live_price
            return compute_edge("polymarket", live_odds, fair_odds)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] check_live_price failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Portfolio scraping + settlement
    # ------------------------------------------------------------------

    async def scrape_portfolio(self, page: "Page") -> list[dict]:
        """Scrape the portfolio/positions page and return each position.

        Must be on polymarket.com/portfolio or the main page showing positions.
        Returns list of {market, outcome, avg_price, now_price, traded, to_win, value, status, shares}.
        """
        # Navigate to portfolio if not already there
        if '/portfolio' not in (page.url or ''):
            await page.goto("https://polymarket.com/portfolio", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)  # Wait for positions to load

        positions = await page.evaluate("""() => {
            const rows = document.querySelectorAll('[data-testid="position-row"], tr, [class*="position"], [class*="PortfolioRow"]');
            const results = [];

            // Try table rows approach
            const allRows = document.querySelectorAll('table tbody tr, [class*="row"]');
            for (const row of allRows) {
                const cells = row.querySelectorAll('td, [class*="cell"], > div');
                if (cells.length < 3) continue;

                const text = row.textContent || '';

                // Detect status: WON, LOST, or active (Sell button present)
                let status = 'open';
                if (text.includes('WON')) status = 'won';
                else if (text.includes('LOST')) status = 'lost';

                // Check for Redeem or Sell button
                const buttons = row.querySelectorAll('button');
                let hasRedeem = false;
                let hasSell = false;
                for (const btn of buttons) {
                    const bt = (btn.textContent || '').trim();
                    if (bt === 'Redeem') hasRedeem = true;
                    if (bt === 'Sell') hasSell = true;
                }

                // Extract market name (first cell, usually has the market title)
                const marketEl = row.querySelector('a, [class*="title"], [class*="market"]');
                const market = marketEl ? marketEl.textContent.trim() : '';

                // Extract outcome tag (colored pill like "Bigetron by Vitality 50.3¢")
                const tagEl = row.querySelector('[class*="tag"], [class*="badge"], [class*="pill"], span[style]');
                const outcomeTag = tagEl ? tagEl.textContent.trim() : '';

                // Extract price info: "50.3¢ → 100¢" pattern
                const priceMatch = text.match(/([\d.]+)¢\\s*→\\s*([\d.]+)¢/);
                const avgPrice = priceMatch ? parseFloat(priceMatch[1]) : null;
                const nowPrice = priceMatch ? parseFloat(priceMatch[2]) : null;

                // Extract dollar values
                const dollarValues = [...text.matchAll(/\\$([\d,.]+)/g)].map(m => parseFloat(m[1].replace(',', '')));

                if (market || outcomeTag) {
                    results.push({
                        market: market.slice(0, 80),
                        outcome_tag: outcomeTag,
                        avg_price: avgPrice,
                        now_price: nowPrice,
                        values: dollarValues,
                        status,
                        has_redeem: hasRedeem,
                        has_sell: hasSell,
                    });
                }
            }
            return results;
        }""")

        logger.info(f"[polymarket] Scraped {len(positions)} portfolio positions")
        return positions

    async def redeem_all(self, page: "Page") -> dict:
        """Click all 'Redeem' buttons on the portfolio page to free cash.

        Returns {redeemed: count, errors: count}.
        """
        if '/portfolio' not in (page.url or ''):
            await page.goto("https://polymarket.com/portfolio", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)

        # Find all Redeem buttons and click them one by one
        redeemed = 0
        errors = 0

        # Count redeem buttons first
        count = await page.evaluate("""() => {
            const btns = document.querySelectorAll('button');
            let n = 0;
            for (const btn of btns) {
                if (btn.textContent.trim() === 'Redeem') n++;
            }
            return n;
        }""")

        logger.info(f"[polymarket] Found {count} Redeem buttons")

        for i in range(count):
            try:
                # Click the first visible Redeem button (they shift after each click)
                clicked = await page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {
                        if (btn.textContent.trim() === 'Redeem') {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }""")
                if clicked:
                    await asyncio.sleep(2)  # Wait for transaction
                    redeemed += 1
                    logger.info(f"[polymarket] Redeemed position {i + 1}/{count}")
                else:
                    break
            except Exception as e:
                logger.warning(f"[polymarket] Redeem {i + 1} failed: {e}")
                errors += 1

        return {"redeemed": redeemed, "errors": errors, "total": count}

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self, page: "Page") -> None:
        """Close persistent Polymarket tabs opened during placement."""
        for slug, tab in list(self._tabs.items()):
            try:
                if not tab.is_closed():
                    await tab.close()
            except Exception:
                pass
        self._tabs.clear()
