"""Polymarket strategy — DOM overrides for balance, history, positions + claim/redeem.

Intel JSON drives login, navigation, betslip. DOM scraping lives here
for things that need custom JS logic (scanning leaf nodes, row walking)
rather than a single CSS selector. The settlement flow delegates to this
strategy via the `scrape_portfolio + claim_banner + redeem_all` triple,
matching what the old dedicated PolymarketWorkflow class exposed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from . import Strategy
from ..base import HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


async def _dismiss_modal(page: Page, max_attempts: int = 3) -> bool:
    """Dismiss Share/overlay modals that appear after Claim/Redeem."""
    for _ in range(max_attempts):
        dismissed = await page.evaluate(
            r"""() => {
                const sels = ['button[aria-label="Close"]', 'button[aria-label="close"]',
                              '[class*="close" i]:not(a)', '[class*="dismiss" i]'];
                for (const s of sels) {
                    const el = document.querySelector(s);
                    if (el && el.offsetParent !== null) { el.click(); return true; }
                }
                return false;
            }"""
        )
        if dismissed:
            await asyncio.sleep(0.5)
            return True
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.4)
        except Exception:
            pass
    return False


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


async def _scrape_portfolio(page: Page, intel: dict | None) -> list[dict]:
    """Scrape /portfolio?tab=positions — each open/settled row with Redeem or Sell button."""
    current_url = page.url or ""
    if "/portfolio" not in current_url or "tab=history" in current_url:
        try:
            await page.goto(
                "https://polymarket.com/portfolio?tab=positions",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(4)
        except Exception as e:
            logger.warning(f"[polymarket] portfolio nav failed: {e}")
            return []

    raw = await page.evaluate(
        r"""() => {
            const out = { buttons: [] };
            for (const btn of document.querySelectorAll('button')) {
                const t = (btn.textContent || '').trim();
                if (t !== 'Redeem' && t !== 'Sell') continue;
                let parent = btn.parentElement;
                let rowText = '';
                for (let i = 0; i < 8 && parent; i++) {
                    rowText = (parent.textContent || '').trim();
                    if (rowText.length > 50 && rowText.includes('$')) break;
                    parent = parent.parentElement;
                }
                out.buttons.push({ type: t, row_text: rowText.slice(0, 300) });
            }
            return out;
        }"""
    )

    positions = []
    for b in raw.get("buttons", []):
        text = b.get("row_text", "")
        btn_type = b.get("type", "")
        status = "open"
        if "WON" in text:
            status = "won"
        elif "LOST" in text:
            status = "lost"
        cents = [float(m) for m in re.findall(r"([\d.]+)\s*¢", text)]
        avg_price = cents[0] if cents else None
        now_price = cents[1] if len(cents) >= 2 else None
        dollar_values = [float(m.replace(",", "")) for m in re.findall(r"\$([\d,.]+)", text)]
        shares_match = re.search(r"([\d.]+)\s*shares", text)
        shares = float(shares_match.group(1)) if shares_match else None
        market = text[:60].split("\n")[0] if text else ""
        market = re.sub(r"[\d¢$→].+", "", market).strip()
        positions.append({
            "market": market[:80],
            "full_text": text[:200],
            "avg_price": avg_price,
            "now_price": now_price,
            "values": dollar_values,
            "shares": shares,
            "status": status,
            "has_redeem": btn_type == "Redeem",
            "has_sell": btn_type == "Sell",
        })

    logger.info(f"[polymarket] Scraped {len(positions)} portfolio positions")
    return positions


async def _claim_banner(page: Page, intel: dict | None) -> dict:
    """Click the top-level Claim banner if present + confirm in modal."""
    try:
        result = await page.evaluate(
            r"""() => {
                for (const btn of document.querySelectorAll('button')) {
                    const t = (btn.textContent || '').trim();
                    if (t === 'Claim' || t.startsWith('Claim')) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.top < 400) { btn.click(); return {found: true, text: t}; }
                    }
                }
                return {found: false};
            }"""
        )
        if not result.get("found"):
            return {"claimed": False, "amount": None}

        await asyncio.sleep(3)
        confirmed = await page.evaluate(
            r"""() => {
                for (const btn of document.querySelectorAll('button')) {
                    const t = (btn.textContent || '').trim();
                    if (t.startsWith('Claim $')) { btn.click(); return t; }
                }
                return null;
            }"""
        )
        if confirmed:
            await asyncio.sleep(3)
            await _dismiss_modal(page)
            logger.info(f"[polymarket] Claim confirmed: {confirmed}")
            return {"claimed": True, "amount": confirmed}
        await _dismiss_modal(page)
        return {"claimed": True, "amount": result.get("text")}
    except Exception as e:
        logger.warning(f"[polymarket] claim_banner failed: {e}")
        return {"claimed": False, "amount": None, "error": str(e)}


async def _redeem_all(page: Page, intel: dict | None) -> dict:
    """Click Redeem on every FINISHED position (Won/Lost) — never Sell on open ones."""
    if "/portfolio" not in (page.url or "") or "tab=history" in (page.url or ""):
        try:
            await page.goto(
                "https://polymarket.com/portfolio?tab=positions",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning(f"[polymarket] redeem nav failed: {e}")
            return {"redeemed": 0, "skipped_open": 0, "errors": 1, "total": 0}

    redeemed, errors = 0, 0
    count = await page.evaluate(
        r"""() => {
            let n = 0;
            for (const btn of document.querySelectorAll('button')) {
                if (btn.textContent.trim() !== 'Redeem') continue;
                let p = btn.parentElement;
                for (let i = 0; i < 8 && p; i++) {
                    const t = p.textContent || '';
                    if (/Won|Lost|WON|LOST/.test(t)) { n++; break; }
                    p = p.parentElement;
                }
            }
            return n;
        }"""
    )

    for i in range(count):
        try:
            clicked = await page.evaluate(
                r"""() => {
                    for (const btn of document.querySelectorAll('button')) {
                        if (btn.textContent.trim() !== 'Redeem') continue;
                        let p = btn.parentElement;
                        let finished = false;
                        for (let i = 0; i < 8 && p; i++) {
                            const t = p.textContent || '';
                            if (/Won|Lost|WON|LOST/.test(t)) { finished = true; break; }
                            p = p.parentElement;
                        }
                        if (finished) { btn.click(); return true; }
                    }
                    return false;
                }"""
            )
            if not clicked:
                break
            await asyncio.sleep(2)
            confirmed = await page.evaluate(
                r"""() => {
                    for (const btn of document.querySelectorAll('button')) {
                        const t = (btn.textContent || '').trim();
                        if (t.startsWith('Redeem $')) { btn.click(); return t; }
                    }
                    return null;
                }"""
            )
            if confirmed:
                await asyncio.sleep(3)
                await _dismiss_modal(page)
                redeemed += 1
                logger.info(f"[polymarket] Redeemed {i + 1}/{count}: {confirmed}")
            else:
                await _dismiss_modal(page)
                errors += 1
        except Exception as e:
            logger.warning(f"[polymarket] Redeem {i + 1} failed: {e}")
            errors += 1

    return {"redeemed": redeemed, "skipped_open": 0, "errors": errors, "total": count}


strategy = Strategy(
    sync_balance=_sync_balance,
    sync_history=_sync_history,
    scrape_portfolio=_scrape_portfolio,
    claim_banner=_claim_banner,
    redeem_all=_redeem_all,
)
