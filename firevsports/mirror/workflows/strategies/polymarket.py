"""Polymarket strategy — DOM overrides for balance (and future SDK paths).

Intel JSON drives login, navigation, betslip. DOM scraping for the Cash
balance lives here because Polymarket's nav doesn't expose a stable CSS
class on the Cash element; we scan leaf nodes starting with "Cash$".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from . import Strategy

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


async def _sync_balance(page: Page, intel: dict | None) -> float:
    """Scrape the Cash USDC amount from nav — avoids Portfolio total on combined rows."""
    try:
        amount = await page.evaluate(
            r"""() => {
                for (const el of document.querySelectorAll('nav *, header *')) {
                    const t = (el.textContent || '').trim();
                    if (t.startsWith('Cash') && t.includes('$') && t.length < 30) {
                        const m = t.match(/\$(\d[\d,.]*)/);
                        if (m) return parseFloat(m[1].replace(/,/g, ''));
                    }
                }
                return null;
            }"""
        )
        if amount is None:
            return -1.0
        logger.info(f"[polymarket] DOM balance: ${float(amount):.2f}")
        return float(amount)
    except Exception as e:
        logger.warning(f"[polymarket] sync_balance failed: {e}")
        return -1.0


strategy = Strategy(sync_balance=_sync_balance)
