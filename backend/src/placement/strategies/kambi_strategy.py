"""
KambiSlipStrategy — automates bet slip filling on Kambi-powered sites.

Kambi brands: unibet, leovegas, speedybet, x3000, goldenbull, 1x2
Deep link: /betting/sports/event/{event_id}

Flow:
1. Navigate to deep-linked event page
2. Wait for Kambi widget to render odds buttons
3. Click the correct odds button (by outcome position or odds text)
4. Wait for bet slip to appear
5. Fill stake in the bet slip input
6. Read actual odds from the slip
7. Return READY — user confirms manually
"""

import asyncio
import logging
import re
from typing import Optional

try:
    from patchright.async_api import Page
except ImportError:
    from playwright.async_api import Page

from ..slip_filler import SlipStrategy, SlipRequest, SlipResult, SlipStatus

logger = logging.getLogger(__name__)

# Outcome positions within a bet offer row
# Kambi renders outcomes in consistent left-to-right order
_POS_1X2 = {"home": 0, "draw": 1, "away": 2}
_POS_ML = {"home": 0, "away": 1}
_POS_TOTAL = {"over": 0, "under": 1}
_POS_SPREAD = {"home": 0, "away": 1}

# Selectors for Kambi widget DOM elements
_ODDS_BUTTON_SELECTORS = [
    '[class*="outcome__odds"]',
    '[class*="mod-outcome"] button',
    'button[class*="outcome"]',
    '[data-test-id*="outcome"]',
]

_OFFER_SECTION_SELECTORS = [
    '[class*="bet-offer__outcomes"]',
    '[class*="betoffer__outcomes"]',
    '[class*="KambiBC-bet-offer"]',
]

_STAKE_INPUT_SELECTORS = [
    'input[data-test-id*="stake"]',
    'input[data-test-id*="amount"]',
    'input[placeholder*="insats" i]',
    'input[placeholder*="Insats"]',
    'input[placeholder*="0.00"]',
    'input[placeholder*="kr" i]',
    'input[aria-label*="stake" i]',
    'input[aria-label*="insats" i]',
    '[class*="betslip"] input[type="text"]',
    '[class*="betslip"] input[type="number"]',
    '[class*="stake"] input',
]

_SLIP_ODDS_SELECTORS = [
    '[class*="betslip"] [class*="odds"]',
    '[class*="betslip"] [class*="price"]',
    '[data-test-id*="slip-odds"]',
    '[class*="KambiBC-betslip"] [class*="odds"]',
]


class KambiSlipStrategy(SlipStrategy):
    """Fill bet slip on Kambi-powered sportsbooks."""

    async def fill(self, page: Page, request: SlipRequest, url: str) -> SlipResult:
        # 1. Navigate to event page
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            return SlipResult(
                status=SlipStatus.ERROR,
                message=f"Navigation failed: {e}",
            )

        # 2. Wait for the Kambi widget to render odds buttons
        combined_sel = ", ".join(_ODDS_BUTTON_SELECTORS)
        try:
            await page.wait_for_selector(combined_sel, timeout=12000)
            # Extra settle time for SPA rendering
            await asyncio.sleep(1.0)
        except Exception:
            return SlipResult(
                status=SlipStatus.NAVIGATED_ONLY,
                message="Page loaded but odds buttons did not appear within timeout.",
            )

        # 3. Find and click the correct odds button
        clicked = await self._click_odds(page, request)
        if not clicked:
            return SlipResult(
                status=SlipStatus.NAVIGATED_ONLY,
                message=f"Could not find odds button for {request.outcome} in {request.market}.",
            )

        # 4. Wait for bet slip to appear
        await asyncio.sleep(0.8)

        # 5. Fill the stake
        filled = await self._fill_stake(page, request.stake)
        if not filled:
            return SlipResult(
                status=SlipStatus.NAVIGATED_ONLY,
                message="Odds clicked but could not find stake input.",
            )

        # 6. Read actual odds from the slip
        actual_odds = await self._read_slip_odds(page)

        return SlipResult(
            status=SlipStatus.READY,
            message="Bet slip ready. Review and confirm on the site.",
            actual_odds=actual_odds,
        )

    # ------------------------------------------------------------------
    # Odds button clicking — 3 fallback strategies
    # ------------------------------------------------------------------

    async def _click_odds(self, page: Page, request: SlipRequest) -> bool:
        """Find and click the correct odds button."""
        # Determine target position based on market/outcome
        positions = self._get_positions(request.market)
        if not positions:
            return False
        target_pos = positions.get(request.outcome)
        if target_pos is None:
            return False

        # Strategy A: Click by outcome_id from provider_meta (most precise)
        if await self._click_by_outcome_id(page, request):
            return True

        # Strategy B: Click by position in bet offer section
        if await self._click_by_position(page, request, target_pos):
            return True

        # Strategy C: Click by matching odds text (last resort)
        if await self._click_by_odds_text(page, request):
            return True

        return False

    async def _click_by_outcome_id(self, page: Page, request: SlipRequest) -> bool:
        """Strategy A: Use outcome_id from provider_meta for precise targeting."""
        if not request.provider_meta:
            return False

        outcome_id = request.provider_meta.get("outcome_id")
        if not outcome_id:
            return False

        try:
            # Kambi may use data attributes with the outcome ID
            for sel in [
                f'[data-outcome-id="{outcome_id}"]',
                f'[id*="{outcome_id}"]',
                f'button[data-outcome-id="{outcome_id}"]',
            ]:
                elem = await page.query_selector(sel)
                if elem:
                    await elem.click()
                    logger.debug(f"Clicked odds by outcome_id: {outcome_id}")
                    return True
        except Exception as e:
            logger.debug(f"Strategy A (outcome_id) failed: {e}")

        return False

    async def _click_by_position(self, page: Page, request: SlipRequest, target_pos: int) -> bool:
        """Strategy B: Find bet offer section, click Nth outcome button."""
        try:
            # Find all bet offer sections
            section_sel = ", ".join(_OFFER_SECTION_SELECTORS)
            sections = await page.query_selector_all(section_sel)

            for section in sections:
                # Get outcome buttons in this section
                button_sel = ", ".join(_ODDS_BUTTON_SELECTORS)
                buttons = await section.query_selector_all(button_sel)
                if not buttons:
                    continue

                # For spread/total: check section text contains matching point
                if request.market in ("spread", "total") and request.point is not None:
                    section_text = await section.inner_text()
                    point_str = f"{request.point:g}"
                    if point_str not in section_text:
                        continue

                # Validate button count matches market layout
                expected = 3 if request.market == "1x2" else 2
                if len(buttons) >= expected and target_pos < len(buttons):
                    await buttons[target_pos].click()
                    logger.debug(f"Clicked odds by position: {target_pos} in section with {len(buttons)} buttons")
                    return True

        except Exception as e:
            logger.debug(f"Strategy B (position) failed: {e}")

        return False

    async def _click_by_odds_text(self, page: Page, request: SlipRequest) -> bool:
        """Strategy C: Find a button matching the expected odds text."""
        try:
            odds_text = f"{request.expected_odds:.2f}"
            buttons = await page.query_selector_all(f'button:has-text("{odds_text}")')

            if len(buttons) == 1:
                await buttons[0].click()
                logger.debug(f"Clicked odds by text match: {odds_text}")
                return True
            elif len(buttons) > 1:
                # Multiple matches — ambiguous, don't click
                logger.debug(f"Multiple buttons match odds {odds_text}, skipping")
        except Exception as e:
            logger.debug(f"Strategy C (odds text) failed: {e}")

        return False

    # ------------------------------------------------------------------
    # Stake filling
    # ------------------------------------------------------------------

    async def _fill_stake(self, page: Page, stake: float) -> bool:
        """Find the stake input in the bet slip and fill it.

        Uses press_sequentially (character-by-character typing) instead of fill()
        because Kambi's React widget listens for keydown/keyup events to update
        its internal state. fill() bypasses these handlers, leaving the payout
        display at "0.00 kr" and the "Lägg spel" button disabled.
        """
        for sel in _STAKE_INPUT_SELECTORS:
            try:
                elem = await page.query_selector(sel)
                if elem:
                    await elem.click()
                    # Triple-click to select all existing text, then delete
                    await elem.click(click_count=3)
                    await page.keyboard.press("Backspace")
                    stake_str = str(int(stake)) if stake == int(stake) else f"{stake:.0f}"
                    await elem.press_sequentially(stake_str, delay=50)
                    logger.debug(f"Filled stake: {stake_str} kr")
                    return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # Odds reading
    # ------------------------------------------------------------------

    async def _read_slip_odds(self, page: Page) -> Optional[float]:
        """Try to read the actual odds from the bet slip."""
        for sel in _SLIP_ODDS_SELECTORS:
            try:
                elem = await page.query_selector(sel)
                if elem:
                    text = await elem.inner_text()
                    # Parse decimal odds from text like "2.15" or "@ 2.15"
                    match = re.search(r"(\d+[.,]\d{2,3})", text.replace(",", "."))
                    if match:
                        return float(match.group(1))
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_positions(market: str) -> dict[str, int] | None:
        """Get outcome position map for the given market type."""
        if market == "1x2":
            return _POS_1X2
        if market == "moneyline":
            return _POS_ML
        if market == "total":
            return _POS_TOTAL
        if market == "spread":
            return _POS_SPREAD
        return None
