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

from ..base import HistoryEntry
from . import Strategy

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
    // History row schema (verified 2026-04-27):
    //   Loss  → bet lost, no payout. Row text: "Loss<market>0.0 shares-<time> ago..."
    //   Redeemed → bet won + already redeemed. Row text: "Redeem<market>+$<value><time> ago..."
    //   Lost / Claimed → legacy labels (older Polymarket UI), kept for back-compat
    //   Bought → open position purchased (informational)
    // Row container: width 600-1200, kids 2-4. Dedup by getBoundingClientRect().top.
    // Value regex anchored to \d+\.\d{2} (USDC always shows 2 decimals) to avoid
    // greedy-matching into adjacent time strings (e.g. "+$27.7515h ago").
    const results = [];
    const labels = ['Loss', 'Redeemed', 'Bought', 'Sold', 'Lost', 'Claimed'];
    const seenTops = new Set();
    for (const el of document.querySelectorAll('div, span, p')) {
        const text = (el.textContent || '').trim();
        if (!labels.includes(text)) continue;
        if (el.children.length > 2) continue;
        let row = el.parentElement;
        let chosen = null;
        for (let i = 0; i < 10 && row; i++) {
            const w = row.offsetWidth;
            const k = row.children.length;
            if (w >= 600 && w <= 1200 && k >= 2 && k <= 4) { chosen = row; break; }
            row = row.parentElement;
        }
        if (!chosen) continue;
        const top = Math.round(chosen.getBoundingClientRect().top);
        if (seenTops.has(top)) continue;
        seenTops.add(top);
        const rowText = (chosen.textContent || '').trim();

        // Value: anchored to exactly 2 decimal places to avoid greedy match into trailing digits.
        const valMatch = rowText.match(/([+-])\$(\d+\.\d{2})/);
        let value = 0;
        if (valMatch) {
            value = parseFloat(valMatch[2]);
            if (valMatch[1] === '-') value = -value;
        }
        const sharesMatch = rowText.match(/(\d+(?:\.\d+)?)\s*shares/);
        const shares = sharesMatch ? parseFloat(sharesMatch[1]) : 0;
        const outcomeMatch = rowText.match(/(Yes|No)\s+\d+¢/);
        const outcomeTag = outcomeMatch ? outcomeMatch[1] : '';

        // Market name: prefer link text, fallback to splitting row text on label tokens.
        let market = '';
        for (const a of chosen.querySelectorAll('a, [href]')) {
            const t = (a.textContent || '').trim();
            if (t.length > market.length && t.length > 10 && !labels.includes(t)) {
                market = t;
            }
        }
        if (!market) {
            let body = rowText;
            for (const lbl of labels) body = body.split(lbl).join('|');
            const chunks = body.split(/\|/).map(s => s.trim()).filter(s =>
                s.length > 15
                && !s.match(/^[+-]?\$\d/)
                && !s.match(/^\d+\s*shares/)
                && !s.match(/^\d+[hmd]\s*ago$/)
                && !s.includes('Position closedView'));
            if (chunks.length > 0) market = chunks[0];
        }
        results.push({ activity: text, market: market.slice(0, 150), outcomeTag, shares, value });
    }
    return results;
}"""


async def _sync_history(page: Page, intel: dict | None) -> list[HistoryEntry]:
    """Navigate to history tab, scrape activity rows, return HistoryEntry list.

    Activity → status mapping (current Polymarket DOM as of 2026-04-27):
      Loss / Lost      → status="lost"   payout=0
      Redeemed/Claimed → status="won"    payout=value
      Bought           → status="pending" (open position)
      Sold             → ignored (manual exit; not a settlement)

    Loss rows have value=0 (no payout) — must NOT be skipped on value≤0.
    Redeemed rows carry the realized USDC payout in `value`.
    Reconcile/_match_polymarket_settlements does the fuzzy match against pending DB bets.
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

    _STATUS_MAP = {
        "Loss": ("lost", 0.0),
        "Lost": ("lost", 0.0),
        "Redeemed": ("won", None),  # payout = value
        "Claimed": ("won", None),
        "Bought": ("pending", 0.0),
    }

    entries: list[HistoryEntry] = []
    for r in raw:
        activity = r.get("activity", "")
        market = r.get("market", "")
        if not market or activity not in _STATUS_MAP:
            continue

        value = float(r.get("value", 0) or 0)
        shares = float(r.get("shares", 0) or 0)
        outcome = r.get("outcomeTag", "") or ""

        status, payout_override = _STATUS_MAP[activity]
        payout = round(abs(value), 2) if payout_override is None else payout_override

        # Loss rows show "0.0 shares" — odds/stake aren't recoverable from the row alone.
        # Bought rows give cost basis; downstream matching cares about market+status.
        if activity == "Bought" and shares > 0 and value > 0:
            odds = round(1.0 / (value / shares), 4)
            stake = round(value, 2)
        else:
            odds, stake = 0.0, 0.0

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


async def _claim_banner(page: Page, intel: dict | None) -> dict:
    """Click the 'You won $X Claim' banner via Playwright locator + confirm modal button."""
    try:
        # Probe first: is there a visible Claim button in the banner region (top < 500)?
        banner = await page.evaluate(
            r"""() => {
                for (const btn of document.querySelectorAll('button')) {
                    const t = (btn.textContent || '').trim();
                    if (t !== 'Claim') continue;
                    const r = btn.getBoundingClientRect();
                    if (btn.offsetParent === null || r.width === 0) continue;
                    if (r.top > 500) continue;
                    const row = btn.closest('div')?.parentElement;
                    const rowText = (row?.textContent || '').trim().slice(0, 120);
                    return { found: true, row_text: rowText };
                }
                return { found: false };
            }"""
        )
        if not banner.get("found"):
            return {"claimed": False, "amount": None}

        row_text = banner.get("row_text", "")
        logger.info(f"[polymarket] Claim banner visible: {row_text}")

        # Use Playwright's locator click — dispatches real pointer events, scrolls into view.
        # Filter to the banner-region Claim (top<500) by chaining a count check.
        claim_locator = page.get_by_role("button", name="Claim", exact=True).first
        try:
            await claim_locator.scroll_into_view_if_needed(timeout=3000)
            await claim_locator.click(timeout=5000)
            logger.info("[polymarket] Clicked Claim banner via locator")
        except Exception as e:
            logger.warning(f"[polymarket] locator.click failed on Claim banner: {e}")
            return {"claimed": False, "amount": None, "error": "locator_click_failed"}

        # Wait for confirm modal — look for 'Claim $X.XX' button inside any dialog/modal.
        for _ in range(6):
            await asyncio.sleep(1)
            confirm_info = await page.evaluate(
                r"""() => {
                    for (const btn of document.querySelectorAll('button')) {
                        const t = (btn.textContent || '').trim();
                        if (!t.match(/^Claim\s+\$[\d,.]+/)) continue;
                        if (btn.offsetParent === null) continue;
                        return { found: true, text: t };
                    }
                    return { found: false };
                }"""
            )
            if confirm_info.get("found"):
                try:
                    confirm_locator = page.get_by_role("button", name=re.compile(r"^Claim\s+\$"))
                    await confirm_locator.first.click(timeout=5000)
                    await asyncio.sleep(3)
                    await _dismiss_modal(page)
                    logger.info(f"[polymarket] Claim confirmed: {confirm_info.get('text')}")
                    return {"claimed": True, "amount": confirm_info.get("text")}
                except Exception as e:
                    logger.warning(f"[polymarket] confirm click failed: {e}")
                    return {"claimed": False, "amount": None, "error": f"confirm_failed:{e}"}

        # No confirm button appeared — banner click may have failed, or auto-confirmed.
        # Re-check if banner is gone (auto-success) vs still there (click ignored).
        still_there = await page.evaluate(
            r"""() => {
                for (const btn of document.querySelectorAll('button')) {
                    if ((btn.textContent || '').trim() !== 'Claim') continue;
                    const r = btn.getBoundingClientRect();
                    if (btn.offsetParent !== null && r.width > 0 && r.top < 500) return true;
                }
                return false;
            }"""
        )
        if still_there:
            logger.warning("[polymarket] Claim banner still visible — click didn't register")
            return {"claimed": False, "amount": None, "error": "banner_still_visible"}
        logger.info("[polymarket] Claim banner gone — assumed auto-confirmed")
        return {"claimed": True, "amount": row_text}
    except Exception as e:
        logger.warning(f"[polymarket] claim_banner failed: {e}")
        return {"claimed": False, "amount": None, "error": str(e)}


async def _redeem_all(page: Page, intel: dict | None) -> dict:
    """Click Redeem on every FINISHED position (Won/Lost) via Playwright locators."""
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

    # Enumerate Won/Lost rows that have a visible Redeem button.
    rows_info = await page.evaluate(
        r"""() => {
            const out = [];
            const seen = new Set();
            for (const btn of document.querySelectorAll('button')) {
                if (btn.textContent.trim() !== 'Redeem') continue;
                const r = btn.getBoundingClientRect();
                if (btn.offsetParent === null || r.width === 0) continue;
                let p = btn.parentElement;
                let finished = false;
                let rowText = '';
                for (let i = 0; i < 8 && p; i++) {
                    const t = p.textContent || '';
                    if (/Won|Lost|WON|LOST/.test(t)) { finished = true; rowText = t.slice(0, 120); break; }
                    p = p.parentElement;
                }
                if (!finished) continue;
                const key = rowText.slice(0, 80);
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({ row_text: rowText, top: Math.round(r.top) });
            }
            return out;
        }"""
    )

    total = len(rows_info)
    logger.info(f"[polymarket] Found {total} finished positions with Redeem buttons")

    redeemed, errors = 0, 0
    for i, row in enumerate(rows_info):
        try:
            # Playwright can match button by text + ancestor-has-text; we use role locator
            # and index the i-th visible Redeem (since seen.add dedupes we're OK).
            redeem_buttons = page.get_by_role("button", name="Redeem", exact=True)
            # Re-fetch visible count (DOM may have changed after prior clicks)
            live_count = await redeem_buttons.count()
            if live_count == 0:
                logger.info(f"[polymarket] No more Redeem buttons after {redeemed} clicks")
                break
            # Click the first visible one — subsequent iterations get the next first
            # because the clicked one becomes non-finished after confirm.
            target = redeem_buttons.first
            try:
                await target.scroll_into_view_if_needed(timeout=3000)
                await target.click(timeout=5000)
            except Exception as e:
                logger.warning(f"[polymarket] Redeem #{i + 1} click failed: {e}")
                errors += 1
                continue

            # Wait up to 6s for the confirm modal with 'Redeem $X.XX' button
            confirmed = None
            for _ in range(6):
                await asyncio.sleep(1)
                info = await page.evaluate(
                    r"""() => {
                        for (const btn of document.querySelectorAll('button')) {
                            const t = (btn.textContent || '').trim();
                            if (!t.match(/^Redeem\s+\$[\d,.]+/)) continue;
                            if (btn.offsetParent === null) continue;
                            return t;
                        }
                        return null;
                    }"""
                )
                if info:
                    confirmed = info
                    break
            if confirmed:
                try:
                    confirm_locator = page.get_by_role("button", name=re.compile(r"^Redeem\s+\$"))
                    await confirm_locator.first.click(timeout=5000)
                    await asyncio.sleep(3)
                    await _dismiss_modal(page)
                    redeemed += 1
                    logger.info(f"[polymarket] Redeemed {i + 1}/{total}: {confirmed}")
                except Exception as e:
                    logger.warning(f"[polymarket] Redeem confirm failed: {e}")
                    errors += 1
                    await _dismiss_modal(page)
            else:
                logger.warning(f"[polymarket] Redeem #{i + 1}: no confirm button appeared")
                await _dismiss_modal(page)
                errors += 1
        except Exception as e:
            logger.warning(f"[polymarket] Redeem #{i + 1} failed: {e}")
            errors += 1

    return {"redeemed": redeemed, "skipped_open": 0, "errors": errors, "total": total}


_FILL_JS = r"""(amount) => {
    const inputs = document.querySelectorAll('input[type="text"], input[type="number"], input:not([type])');
    for (const input of inputs) {
        const parent = input.closest('div, label, fieldset');
        const ctx = parent ? parent.textContent : '';
        if (ctx.includes('Amount') || input.placeholder === '$0' ||
            input.placeholder === '$0.00' || input.placeholder === '0' ||
            input.placeholder === 'Amount') {
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            setter.call(input, amount);
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
            return { filled: true, value: amount };
        }
    }
    return { filled: false };
}"""


_LOCATE_TARGET_JS = r"""(args) => {
    // Locate the target ¢ button on a polymarket event page across ALL market
    // types: moneyline (1x2), spread (Game Handicap), total (Total Games).
    //
    // args = { targetName, market, point, outcome }
    //   targetName — team name (display_home / display_away) lowercased
    //   market — 'moneyline' / '1x2' / 'spread' / 'total'
    //   point — line value for spread/total (number or null)
    //   outcome — 'home' / 'away' / 'over' / 'under' (for total disambiguation)
    //
    // DOM layout (verified 2026-04-29):
    //   Moneyline block: header "Moneyline" + 2 cent buttons "<TEAM> <cents>¢"
    //   Game Handicap (spread): header "Game Handicap" / "Spread" + 2 buttons
    //     "<TEAM> +/-<point> <cents>¢"
    //   Total Games (total): header "Total Games" / "Total" / "Total Maps" +
    //     2 buttons "O <point> <cents>¢" / "U <point> <cents>¢"
    const targetName = (args.targetName || '').toLowerCase();
    const market = (args.market || '').toLowerCase();
    const point = args.point;
    const outcome = (args.outcome || '').toLowerCase();

    // Map market type → header text candidates. Polymarket sometimes uses
    // sport-specific labels; try each in order.
    const HEADERS = {
        moneyline: ['Moneyline'],
        '1x2': ['Moneyline', '1X2', '1x2'],
        spread: ['Game Handicap', 'Spread', 'Handicap', 'Run Line', 'Puck Line'],
        total: ['Total Games', 'Total Maps', 'Total Goals', 'Total', 'Over/Under'],
    };
    const headerCandidates = HEADERS[market] || ['Moneyline'];

    // Step 1: find the market block by header text.
    let block = null;
    for (const headerText of headerCandidates) {
        for (const el of document.querySelectorAll('div, span, p, h2, h3, h4')) {
            const t = (el.textContent || '').trim();
            if (t !== headerText || el.tagName === 'BUTTON') continue;
            // Walk up to find an ancestor with at least 2 cent buttons.
            let p = el.parentElement;
            for (let i = 0; i < 6 && p; i++) {
                const btns = p.querySelectorAll('button');
                let cc = 0;
                for (const b of btns) if (b.textContent.includes('¢')) cc++;
                if (cc >= 2) { block = p; break; }
                p = p.parentElement;
            }
            if (block) break;
        }
        if (block) break;
    }

    // Collect cent buttons in the block.
    let centBtns = [];
    if (block) {
        for (const b of block.querySelectorAll('button')) {
            const t = (b.textContent || '').trim();
            if (t.includes('¢') && t.length < 60) centBtns.push(b);
        }
    }
    // Fallback to global scan if block lookup failed (older / different layout).
    if (centBtns.length < 2) {
        centBtns = [];
        for (const b of document.querySelectorAll('button')) {
            const t = (b.textContent || '').trim();
            if (t.includes('¢') && t.length < 60) centBtns.push(b);
        }
    }

    // Step 2: pick the right button based on market + outcome + point.
    const initials = targetName.split(/\s+/).filter(w => w.length > 0).map(w => w[0]).join('');
    const teamMatch = (text) => {
        const team = text.replace(/[-+]?\d+(?:\.\d+)?\s*¢.*/, '').trim();
        if (!team) return false;
        return targetName.startsWith(team)
            || team.startsWith(targetName.slice(0, 3))
            || targetName.includes(team)
            || (team.length >= 2 && team === initials)
            || (team.length >= 2 && initials.startsWith(team))
            || (team.length >= 3 && targetName.split(/\s+/).some(w => w.startsWith(team)));
    };

    for (const b of centBtns) {
        const bt = (b.textContent || '').trim().toLowerCase();

        if (market === 'total') {
            // Match Over/Under prefix + (optional) point. Polymarket uses
            // "O 2.5 51¢" / "U 2.5 50¢" or sometimes "Over 2.5" / "Under 2.5".
            const wantOver = outcome === 'over';
            const wantUnder = outcome === 'under';
            const isOver = /^o(?:ver)?\s/.test(bt) || bt.startsWith('o ');
            const isUnder = /^u(?:nder)?\s/.test(bt) || bt.startsWith('u ');
            if (wantOver && !isOver) continue;
            if (wantUnder && !isUnder) continue;
            if (point != null) {
                // Require the point to appear in the button text.
                const pStr = String(point);
                if (!bt.includes(pStr)) continue;
            }
            const m = b.textContent.match(/(\d+(?:\.\d+)?)¢/);
            return {
                full_text: b.textContent.trim(),
                cents: m ? parseFloat(m[1]) : null,
                market_block: !!block,
                market: market,
            };
        }

        if (market === 'spread') {
            // Match team name + signed point. e.g. "ktc -1.5 25¢" matches
            // KT Rolster Challengers with point=-1.5.
            if (!teamMatch(bt)) continue;
            if (point != null) {
                // Polymarket renders points with explicit sign for both sides
                // (DNSC +1.5 / KTC -1.5). Match by absolute value to dodge
                // sign-flipping variations across sports.
                const absPoint = Math.abs(point);
                const absStr = absPoint % 1 === 0 ? String(absPoint) : absPoint.toString();
                if (!bt.includes(absStr)) continue;
            }
            const m = b.textContent.match(/(\d+(?:\.\d+)?)¢/);
            return {
                full_text: b.textContent.trim(),
                cents: m ? parseFloat(m[1]) : null,
                market_block: !!block,
                market: market,
            };
        }

        // moneyline / 1x2 / default — match team name only.
        if (!teamMatch(bt)) continue;
        const m = b.textContent.match(/(\d+(?:\.\d+)?)¢/);
        return {
            full_text: b.textContent.trim(),
            cents: m ? parseFloat(m[1]) : null,
            market_block: !!block,
            market: market || 'moneyline',
        };
    }

    // Fallback: only return first cent button if market is moneyline-ish.
    // For spread/total we'd rather fail than click the wrong outcome.
    if (centBtns.length > 0 && (market === 'moneyline' || market === '1x2' || market === '')) {
        const b = centBtns[0];
        const m = b.textContent.match(/(\d+(?:\.\d+)?)¢/);
        return {
            full_text: b.textContent.trim(),
            cents: m ? parseFloat(m[1]) : null,
            market_block: !!block,
            market: market || 'moneyline',
            fallback: true,
        };
    }
    return null;
}"""


async def _prep_betslip(page: Page, bet, stake: float, intel: dict | None):
    """Click the correct outcome (via Playwright locator for real pointer events) + fill Amount."""
    from ..base import PlacementResult

    def _g(attr: str) -> str:
        if isinstance(bet, dict):
            val = bet.get(attr)
            if val is None:
                val = (bet.get("provider_meta") or {}).get(attr)
            return str(val or "")
        val = getattr(bet, attr, None)
        if val is None:
            meta = getattr(bet, "provider_meta", None) or {}
            if isinstance(meta, dict):
                val = meta.get(attr)
        return str(val or "")

    outcome = _g("outcome").lower()
    market = _g("market").lower()
    home = (_g("display_home") or _g("poly_home")).strip().lower()
    away = (_g("display_away") or _g("poly_away")).strip().lower()
    bet_id = getattr(bet, "bet_id", 0) if not isinstance(bet, dict) else bet.get("bet_id", 0)
    # Spread/total bets carry a `point` (line value). Fetch it from the bet
    # so the locator can disambiguate handicap buttons (e.g. KTC -1.5 vs +1.5)
    # and Over/Under buttons by their line.
    point_val = None
    if isinstance(bet, dict):
        point_val = bet.get("point")
    else:
        point_val = getattr(bet, "point", None)

    if outcome in ("home", "1"):
        target = home
    elif outcome in ("away", "2"):
        target = away
    elif outcome == "over":
        target = "over"
    elif outcome == "under":
        target = "under"
    else:
        target = outcome

    try:
        await page.wait_for_selector("button", timeout=10000)
    except Exception:
        await asyncio.sleep(3)

    # Step 1: identify the target button text via JS — market-aware locator
    # picks the right block (Moneyline / Game Handicap / Total Games) AND
    # the right button within it (team for ML/spread, O/U for total). For
    # spread + total, the line value is matched too so e.g. SPR -1.5 doesn't
    # accidentally pick the +1.5 side.
    target_info = None
    try:
        target_info = await page.evaluate(
            _LOCATE_TARGET_JS,
            {"targetName": target, "market": market, "point": point_val, "outcome": outcome},
        )
    except Exception as e:
        logger.warning(f"[polymarket] prep locate failed: {e}")

    if not target_info:
        return PlacementResult(
            status="failed",
            bet_id=bet_id,
            reason=f"no_cent_button_matched (market={market}, target={target}, point={point_val})",
        )

    full_text = target_info["full_text"]
    cents = target_info.get("cents")
    logger.info(
        f"[polymarket] Target outcome: '{full_text}' (market={market}, target='{target}', "
        f"point={point_val}, cents={cents}, block={target_info.get('market_block')}"
        f"{', fallback' if target_info.get('fallback') else ''})"
    )

    # Step 2: click via Playwright locator (real pointer events fire React handlers)
    try:
        locator = page.get_by_role("button", name=full_text, exact=True).first
        await locator.scroll_into_view_if_needed(timeout=3000)
        await locator.click(timeout=5000)
        logger.info(f"[polymarket] Clicked '{full_text}' via locator")
    except Exception as e:
        logger.warning(f"[polymarket] locator click failed: {e}")
        return PlacementResult(status="failed", bet_id=bet_id, reason=f"click_failed:{e}")

    live_odds = round(1.0 / (cents / 100.0), 3) if cents and cents > 0 else None

    # Step 3: fill Amount input.
    # Polymarket's betslip mounts ~1.5-3s AFTER the outcome click (React hydrates
    # the trade form). The previous fixed 1.5s sleep was too short, leaving the
    # input empty. Worse: Polymarket's controlled-input wraps the value in React
    # state, so a single setter+input-event sometimes gets clobbered by a delayed
    # re-render. Strategy: wait for the input to actually exist, then fill, then
    # verify the value stuck — retry up to 3 times before giving up.
    if stake > 0:
        stake_str = f"{stake:.2f}" if stake != int(stake) else str(int(stake))
        try:
            await page.wait_for_selector(
                'input[placeholder="$0"], input[placeholder="$0.00"], input[placeholder="0"], input[placeholder="Amount"]',
                timeout=8000,
                state="visible",
            )
        except Exception:
            # Fall through to the fill attempt anyway — some Polymarket pages
            # use a non-standard placeholder we can still find via context.
            await asyncio.sleep(2.0)

        for attempt in range(3):
            try:
                filled = await page.evaluate(_FILL_JS, stake_str)
            except Exception as e:
                logger.warning(f"[polymarket] stake fill attempt {attempt + 1} failed: {e}")
                filled = None
            if filled and filled.get("filled"):
                # Verify the value actually stuck (controlled-input race).
                await asyncio.sleep(0.6)
                try:
                    current = await page.evaluate(
                        r"""() => {
                            const inputs = document.querySelectorAll('input');
                            for (const inp of inputs) {
                                if (inp.placeholder === '$0' || inp.placeholder === '$0.00' ||
                                    inp.placeholder === '0' || inp.placeholder === 'Amount') {
                                    return inp.value || '';
                                }
                            }
                            return '';
                        }"""
                    )
                except Exception:
                    current = ""
                if current and current.replace(",", ".") == stake_str:
                    logger.info(f"[polymarket] Filled Amount input: ${stake_str} (attempt {attempt + 1})")
                    break
                logger.warning(
                    f"[polymarket] Fill cleared (attempt {attempt + 1}): set='{stake_str}' got='{current}' — retrying"
                )
            else:
                logger.warning(f"[polymarket] Amount input not found (attempt {attempt + 1})")
            await asyncio.sleep(0.8)
        else:
            logger.warning(f"[polymarket] Amount fill failed after 3 attempts (stake=${stake_str})")

    return PlacementResult(
        status="prepped",
        bet_id=bet_id,
        actual_odds=live_odds,
        actual_stake=stake,
        reason=f"{cents}¢" if cents else None,
    )


async def _check_live_price(page: Page, bet, intel: dict | None = None):
    """Read the current ¢ price for the target outcome and compute (live_odds, live_edge).

    Reuses _LOCATE_TARGET_JS so cent reading is market-aware (correctly reads
    spread / total cents instead of always landing on the moneyline price).
    """

    def _g(attr: str) -> str:
        if isinstance(bet, dict):
            val = bet.get(attr)
            if val is None:
                val = (bet.get("provider_meta") or {}).get(attr)
            return str(val or "")
        val = getattr(bet, attr, None)
        if val is None:
            meta = getattr(bet, "provider_meta", None) or {}
            if isinstance(meta, dict):
                val = meta.get(attr)
        return str(val or "")

    fair_odds = getattr(bet, "fair_odds", None) if not isinstance(bet, dict) else bet.get("fair_odds")
    if not fair_odds:
        return None, None

    outcome = _g("outcome").lower()
    market = _g("market").lower()
    home = (_g("display_home") or _g("poly_home")).strip().lower()
    away = (_g("display_away") or _g("poly_away")).strip().lower()
    point_val = bet.get("point") if isinstance(bet, dict) else getattr(bet, "point", None)

    if outcome in ("home", "1"):
        target = home
    elif outcome in ("away", "2"):
        target = away
    elif outcome == "over":
        target = "over"
    elif outcome == "under":
        target = "under"
    else:
        target = outcome

    try:
        info = await page.evaluate(
            _LOCATE_TARGET_JS,
            {"targetName": target, "market": market, "point": point_val, "outcome": outcome},
        )
    except Exception:
        return None, None

    if not info:
        return None, None
    cents = info.get("cents")
    if not cents or cents <= 0 or cents >= 100:
        return None, None

    live_odds = round(100.0 / cents, 3)
    live_edge = (live_odds / float(fair_odds) - 1.0) * 100.0
    return live_odds, round(live_edge, 2)


async def restore_amount_if_cleared(page: Page, stake: float) -> bool:
    """Re-fill the betslip Amount input if Polymarket's React clobbered it.

    Polymarket's controlled-input occasionally wipes the Amount value after the
    initial prep — typically when the betslip re-renders on a price tick or
    focus event. Without this, the user clicks "Buy" with $0 staked and either
    sees an error or places a wrong-size order.

    Idempotent: reads current value, only fills if empty/zero.
    Returns True if a fill happened, False if no action needed.
    """
    if stake <= 0:
        return False
    try:
        current = await page.evaluate(
            r"""() => {
                const inputs = document.querySelectorAll('input');
                for (const inp of inputs) {
                    if (inp.placeholder === '$0' || inp.placeholder === '$0.00' ||
                        inp.placeholder === '0' || inp.placeholder === 'Amount') {
                        return inp.value || '';
                    }
                }
                return null;
            }"""
        )
    except Exception:
        return False
    # `null` → input not present (betslip closed). `''` or '0' → cleared.
    if current is None:
        return False
    if current.strip() not in ("", "0", "0.00"):
        return False  # already populated, leave alone
    stake_str = f"{stake:.2f}" if stake != int(stake) else str(int(stake))
    try:
        result = await page.evaluate(_FILL_JS, stake_str)
    except Exception as e:
        logger.debug(f"[polymarket] amount-keeper fill raised: {e}")
        return False
    if result and result.get("filled"):
        logger.info(f"[polymarket] Amount auto-restored to ${stake_str} (was '{current}')")
        return True
    return False


strategy = Strategy(
    sync_balance=_sync_balance,
    sync_history=_sync_history,
    prep_betslip=_prep_betslip,
    check_live_price=_check_live_price,
    scrape_portfolio=_scrape_portfolio,
    claim_banner=_claim_banner,
    redeem_all=_redeem_all,
)
# Module-level export so provider_runner can import without going through the
# Strategy dataclass (which would require adding yet another field).
strategy.restore_amount_if_cleared = restore_amount_if_cleared  # type: ignore[attr-defined]
