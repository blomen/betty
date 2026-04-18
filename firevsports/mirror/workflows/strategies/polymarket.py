"""Polymarket strategy — DOM overrides for balance and history.

Intel JSON drives login, navigation, betslip. DOM scraping lives here
for things that need custom JS logic (scanning leaf nodes, row walking)
rather than a single CSS selector.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from . import Strategy
from ..base import HistoryEntry

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


_HISTORY_SCRAPE_JS = r"""() => {
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
        for (const a of row.querySelectorAll('a, [href]')) {
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
            const tagMatch = t.match(/^(.+?)\s+(\d+)¢$/);
            if (tagMatch && t.length < 50) outcomeTag = tagMatch[1];
            const sharesMatch = t.match(/([\d.]+)\s*shares/);
            if (sharesMatch) shares = parseFloat(sharesMatch[1]);
        }
        let value = 0;
        for (const child of row.querySelectorAll('span, p, div')) {
            const t = (child.textContent || '').trim();
            const valMatch = t.match(/^[+-]?\$(\d[\d,.]*)/);
            if (valMatch && child.children.length <= 1) {
                value = parseFloat(valMatch[1].replace(',', ''));
                if (t.startsWith('-')) value = -value;
                break;
            }
        }
        results.push({ activity, market: market.slice(0, 120), outcomeTag, shares, value });
    }
    return results;
}"""


async def _sync_history(page: Page, intel: dict | None) -> list[HistoryEntry]:
    """Navigate to history tab, scrape activity rows, return HistoryEntry list.

    Bought → status=pending (open bet)
    Lost   → status=lost   (payout 0)
    Claimed→ status=won    (payout = $value)
    PendingLoop does the matching against DB pending bets.
    """
    current_url = page.url or ""
    if "/portfolio" not in current_url or "tab=history" not in current_url:
        try:
            await page.goto(
                "https://polymarket.com/portfolio?tab=history",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(4)
        except Exception as e:
            logger.warning(f"[polymarket] navigate to history failed: {e}")
            return []

    try:
        raw = await page.evaluate(_HISTORY_SCRAPE_JS)
    except Exception as e:
        logger.warning(f"[polymarket] history scrape failed: {e}")
        return []

    if not raw:
        return []

    entries: list[HistoryEntry] = []
    for r in raw:
        activity = r.get("activity", "")
        market = r.get("market", "")
        value = float(r.get("value", 0) or 0)
        shares = float(r.get("shares", 0) or 0)
        outcome = r.get("outcomeTag", "") or ""
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
            odds, stake = 0.0, 0.0
        else:
            continue
        entries.append(
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

    logger.info(f"[polymarket] sync_history: {len(entries)} entries (from {len(raw)} scraped)")
    return entries


strategy = Strategy(sync_balance=_sync_balance, sync_history=_sync_history)
