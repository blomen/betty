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
                home_name=getattr(bet, "display_home", ""),
                away_name=getattr(bet, "display_away", ""),
            )
            status = result.get("status", "failed")
            return PlacementResult(
                status="placed" if status == "placed" else status,
                bet_id=bet.bet_id,
                actual_stake=result.get("amount"),
                actual_odds=result.get("price"),
                reason=result.get("error"),
                raw_response=result,
            )
        except Exception as e:
            logger.error(f"[{self.provider_id}] place_bet failed: {e}", exc_info=True)
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

        Navigates to polymarket.com/portfolio and scrapes all position rows.
        Returns list of {market, outcome_tag, avg_price, now_price, values, status, has_redeem, has_sell}.
        """
        # Navigate to portfolio
        current_url = page.url or ''
        if '/portfolio' not in current_url:
            await page.goto("https://polymarket.com/portfolio", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(4)  # Wait for client-side render

        # First, let's just dump what we can see to understand the DOM structure
        debug_info = await page.evaluate("""() => {
            const info = {
                url: window.location.href,
                title: document.title,
                buttons: [],
                text_samples: [],
            };

            // Find all buttons
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const t = (btn.textContent || '').trim();
                if (t === 'Redeem' || t === 'Sell') {
                    // Walk up to find the row container
                    let parent = btn.parentElement;
                    let rowText = '';
                    for (let i = 0; i < 8 && parent; i++) {
                        rowText = (parent.textContent || '').trim();
                        // Stop when we have enough context (market name + prices)
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
        }""")

        logger.info(f"[polymarket] Portfolio page: {debug_info.get('url')}, "
                     f"buttons found: {len(debug_info.get('buttons', []))}")

        # Now build positions from the button contexts
        positions = []
        for btn_info in debug_info.get('buttons', []):
            text = btn_info.get('row_text', '')
            btn_type = btn_info.get('type', '')

            # Determine status from text
            status = 'open'
            if 'WON' in text:
                status = 'won'
            elif 'LOST' in text:
                status = 'lost'

            # Extract price movement: "50.3¢ → 100¢" or "12¢ → 0¢"
            import re
            price_match = re.search(r'([\d.]+)¢\s*→\s*([\d.]+)¢', text)
            avg_price = float(price_match.group(1)) if price_match else None
            now_price = float(price_match.group(2)) if price_match else None

            # Extract dollar values
            dollar_values = [float(m.replace(',', '')) for m in re.findall(r'\$([\d,.]+)', text)]

            # Extract shares
            shares_match = re.search(r'([\d.]+)\s*shares', text)
            shares = float(shares_match.group(1)) if shares_match else None

            # Market name: text before the first price/number section
            market = text[:60].split('\n')[0] if text else ''
            # Clean up: remove trailing numbers/symbols
            market = re.sub(r'[\d¢$→].+', '', market).strip()

            positions.append({
                'market': market[:80],
                'full_text': text[:200],
                'avg_price': avg_price,
                'now_price': now_price,
                'values': dollar_values,
                'shares': shares,
                'status': status,
                'has_redeem': btn_type == 'Redeem',
                'has_sell': btn_type == 'Sell',
            })

        logger.info(f"[polymarket] Scraped {len(positions)} portfolio positions")
        return positions

    async def redeem_all(self, page: "Page") -> dict:
        """Click Redeem buttons ONLY for finished positions (WON or LOST).

        NEVER clicks Sell on open positions — that would exit at market price.
        Only redeems positions where the row text contains 'Won' or 'Lost'.

        Returns {redeemed: count, skipped_open: count, errors: count}.
        """
        if '/portfolio' not in (page.url or ''):
            await page.goto("https://polymarket.com/portfolio", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)

        redeemed = 0
        skipped_open = 0
        errors = 0

        # Count ONLY Redeem buttons that are in finished (Won/Lost) rows
        count = await page.evaluate("""() => {
            const btns = document.querySelectorAll('button');
            let n = 0;
            for (const btn of btns) {
                if (btn.textContent.trim() !== 'Redeem') continue;
                // Walk up to find the row and check for Won/Lost text
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
        }""")

        logger.info(f"[polymarket] Found {count} redeemable finished positions")

        for i in range(count):
            try:
                # Click the first Redeem button that's in a finished (Won/Lost) row
                clicked = await page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {
                        if (btn.textContent.trim() !== 'Redeem') continue;
                        // Verify this is a finished position
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
                }""")
                if not clicked:
                    break

                # Wait for the confirmation modal to appear
                await asyncio.sleep(2)

                # Click the confirmation button in the modal ("Redeem $X.XX")
                confirmed = await page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {
                        const t = (btn.textContent || '').trim();
                        if (t.startsWith('Redeem $') || t.startsWith('Redeem $')) {
                            btn.click();
                            return t;
                        }
                    }
                    return null;
                }""")
                if confirmed:
                    await asyncio.sleep(3)  # Wait for blockchain transaction
                    redeemed += 1
                    logger.info(f"[polymarket] Redeemed {i + 1}/{count}: {confirmed}")
                else:
                    # Modal might not have appeared — try closing any overlay
                    logger.warning(f"[polymarket] No confirm button found for redeem {i + 1}")
                    # Try to close the modal
                    await page.evaluate("""() => {
                        const close = document.querySelector('[class*="close"], [aria-label="Close"]');
                        if (close) close.click();
                    }""")
                    await asyncio.sleep(1)
                    errors += 1
            except Exception as e:
                logger.warning(f"[polymarket] Redeem {i + 1} failed: {e}")
                errors += 1

        return {"redeemed": redeemed, "skipped_open": skipped_open, "errors": errors, "total": count}

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
