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
        """Sync full Polymarket bet history to DB.

        Navigates to History tab → scrapes all entries → reconciles with DB:
        1. "Bought" entries → create missing bets (ones not already in DB)
        2. "Lost"/"Claimed" entries → settle matching pending bets
        3. Returns HistoryEntry list for settled bets

        This is the generic settle-first workflow: sync ALL history before playing.
        """
        from ...db.models import Bet, Event, get_session
        from ...repositories.profile_repo import ProfileRepo
        from ...services.bet_service import BetService
        from rapidfuzz import fuzz

        # Navigate to History tab
        if '/portfolio' not in (page.url or '') or 'tab=history' not in (page.url or ''):
            await page.goto(
                "https://polymarket.com/portfolio?tab=history",
                wait_until="domcontentloaded", timeout=15000,
            )
            await asyncio.sleep(4)
        elif 'tab=history' not in (page.url or ''):
            # On portfolio but wrong tab — click History
            await page.evaluate("""() => {
                const tabs = document.querySelectorAll('a, button, div[role="tab"]');
                for (const t of tabs) {
                    if ((t.textContent || '').trim() === 'History') { t.click(); return true; }
                }
                return false;
            }""")
            await asyncio.sleep(3)

        # Scrape history entries
        entries = await self.scrape_history(page)
        if not entries:
            logger.info("[polymarket] No history entries found")
            return []

        logger.info(f"[polymarket] sync_history: {len(entries)} entries scraped")

        db = get_session()
        history_results: list[HistoryEntry] = []
        try:
            profile = ProfileRepo(db).get_active()
            if not profile:
                logger.warning("[polymarket] sync_history: no active profile")
                return []

            # Get ALL polymarket bets (pending + settled) for dedup
            all_bets = (
                db.query(Bet, Event)
                .join(Event, Bet.event_id == Event.id, isouter=True)
                .filter(
                    Bet.profile_id == profile.id,
                    Bet.provider_id == "polymarket",
                )
                .all()
            )

            pending = [(b, e) for b, e in all_bets if b.result == "pending"]
            settled_ids = {b.id for b, _ in all_bets if b.result != "pending"}

            bet_service = BetService(db)
            new_bets = 0
            settled_bets = 0

            for entry in entries:
                activity = entry.get("activity", "")
                market = entry.get("market", "")
                value = entry.get("value", 0)
                shares = entry.get("shares", 0)

                if not market:
                    continue

                if activity == "Bought":
                    # Check if this buy is already in DB (by fuzzy matching market name + shares/stake)
                    # Stake ≈ shares * avg_price_cents / 100
                    already_exists = False
                    for bet, event in all_bets:
                        event_name = ""
                        if event:
                            h = event.display_home or event.home_team or ""
                            a = event.display_away or event.away_team or ""
                            event_name = f"{h} vs {a}" if h and a else h or a
                        score = fuzz.token_set_ratio(market.lower(), event_name.lower())
                        if score >= 70:
                            already_exists = True
                            break
                    if not already_exists and value > 0:
                        # Record as unknown bet — we only know market name, stake, shares
                        # Can't match to an event_id since we only have the display name
                        logger.info(
                            f"[polymarket] sync_history: new bet from history — "
                            f"{market[:60]} stake=${value} shares={shares}"
                        )
                        # Create a minimal bet record
                        result = bet_service.create_bet(
                            event_id=None,
                            provider_id="polymarket",
                            market="1x2",  # Default — history doesn't tell us
                            outcome=entry.get("outcomeTag", "unknown"),
                            odds=round(1.0 / (value / shares), 4) if shares > 0 and value > 0 else 2.0,
                            stake=round(value, 2),
                            bet_type="polymarket",
                        )
                        if "error" not in result:
                            new_bets += 1

                elif activity in ("Lost", "Claimed"):
                    # Find matching pending bet and settle it
                    result_str = "lost" if activity == "Lost" else "won"
                    payout = abs(value) if activity == "Claimed" else 0.0

                    best_match = None
                    best_score = 0
                    for bet, event in pending:
                        if bet.id in settled_ids:
                            continue
                        event_name = ""
                        if event:
                            h = event.display_home or event.home_team or ""
                            a = event.display_away or event.away_team or ""
                            event_name = f"{h} vs {a}" if h and a else h or a
                        s1 = fuzz.partial_ratio(market.lower(), event_name.lower())
                        s2 = fuzz.token_set_ratio(market.lower(), event_name.lower())
                        score = max(s1, s2)
                        if score > best_score and score >= 60:
                            best_score = score
                            best_match = bet

                    if best_match:
                        try:
                            bet_service.settle_bet(best_match.id, result_str, round(payout, 2))
                            settled_ids.add(best_match.id)
                            settled_bets += 1
                            logger.info(
                                f"[polymarket] sync_history: settled bet #{best_match.id} "
                                f"→ {result_str} (payout=${payout:.2f}) via {market[:50]}"
                            )
                            history_results.append(HistoryEntry(
                                provider_bet_id=str(best_match.id),
                                event_name=market[:80],
                                market=best_match.market or "1x2",
                                outcome=best_match.outcome or "",
                                odds=best_match.odds,
                                stake=best_match.stake,
                                status=result_str,
                                payout=round(payout, 2),
                            ))
                        except Exception as e:
                            logger.warning(f"[polymarket] sync_history settle failed: {e}")

            db.commit()
            logger.info(
                f"[polymarket] sync_history complete: "
                f"{new_bets} new bets recorded, {settled_bets} bets settled"
            )

        except Exception as e:
            db.rollback()
            logger.error(f"[polymarket] sync_history error: {e}", exc_info=True)
        finally:
            db.close()

        return history_results

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Navigate to event AND prepare betslip (click outcome, fill amount).

        After this returns, the Polymarket tab shows the event with the
        correct outcome selected and amount filled. User can visually verify
        before clicking Confirm, which calls place_bet → just clicks Buy.

        Works standalone — no mirror service dependency.
        """
        slug = getattr(bet, "market_slug", None)
        if not slug:
            logger.warning(f"[{self.provider_id}] No market_slug on bet {bet.bet_id}")
            return False

        outcome = getattr(bet, "poly_outcome", None) or getattr(bet, "outcome", "")
        original_outcome = getattr(bet, "original_outcome", outcome)
        stake = int(getattr(bet, "stake", 0))
        home_name = getattr(bet, "display_home", "") or ""
        away_name = getattr(bet, "display_away", "") or ""

        # 1. Navigate to market page
        url = f"https://polymarket.com/event/{slug}"
        logger.info(f"[polymarket] navigate_to_event: {url} outcome={outcome} stake=${stake}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate failed: {e}")
            return False

        # Wait for trading buttons
        try:
            await page.wait_for_selector('button', timeout=10000)
        except Exception:
            await asyncio.sleep(5)

        # 2. Click the correct outcome button
        outcome_lower = (original_outcome or outcome).lower()
        if outcome_lower in ("home", "over"):
            target = home_name.lower()[:3] if home_name else ""
        elif outcome_lower in ("away", "under"):
            target = away_name.lower()[:3] if away_name else ""
        elif outcome_lower == "draw":
            target = "draw"
        else:
            target = outcome.lower()[:3]

        try:
            clicked = await page.evaluate("""(target) => {
                const btns = [...document.querySelectorAll('button')];
                for (const btn of btns) {
                    const text = (btn.textContent || '').toLowerCase();
                    if (target && text.includes(target) && text.includes('¢')) {
                        btn.scrollIntoView({block: 'center'});
                        btn.click();
                        return btn.textContent.trim().slice(0, 40);
                    }
                }
                return null;
            }""", target)
            if clicked:
                logger.info(f"[polymarket] Clicked outcome: '{clicked}' (target='{target}')")
                await asyncio.sleep(1)
            else:
                logger.warning(f"[polymarket] No outcome button matching '{target}'")
        except Exception as e:
            logger.warning(f"[polymarket] Could not click outcome: {e}")

        # 3. Fill amount via quick-add buttons (+$1, +$5, +$10, +$100)
        if stake > 0:
            remaining = stake
            for btn_val in [100, 10, 5, 1]:
                while remaining >= btn_val:
                    ok = await page.evaluate(f"""() => {{
                        const btns = document.querySelectorAll('button');
                        for (const btn of btns) {{
                            if (btn.textContent.trim() === '+${btn_val}') {{
                                btn.click(); return true;
                            }}
                        }}
                        return false;
                    }}""")
                    if ok:
                        remaining -= btn_val
                        await asyncio.sleep(0.15)
                    else:
                        break
            if remaining > 0:
                logger.warning(f"[polymarket] Partial fill: ${stake - remaining}/${stake}")
            else:
                logger.info(f"[polymarket] Filled ${stake} via quick-add buttons")

        return True

    # ------------------------------------------------------------------
    # Bet placement
    # ------------------------------------------------------------------

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Record bet as placed. User clicks Buy manually on Polymarket.

        navigate_to_event already prepared the betslip (outcome + amount).
        This just records the placement — never auto-clicks Buy.
        """
        logger.info(f"[polymarket] Recording manual placement: bet {bet.bet_id} stake=${stake}")
        return PlacementResult(status="placed", bet_id=bet.bet_id, actual_stake=stake)

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
    # Modal dismissal
    # ------------------------------------------------------------------

    async def _dismiss_modal(self, page: "Page", max_attempts: int = 3) -> bool:
        """Dismiss Share/overlay modals that appear after Claim/Redeem.

        Polymarket shows a "Share your winnings" modal with an X close button.
        Tries multiple strategies: X button, click outside, Escape key.
        """
        for attempt in range(max_attempts):
            dismissed = await page.evaluate("""() => {
                // Strategy 1: X/close button (SVG inside button, aria-label, class)
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
                // Strategy 2: Find any small clickable element near top-right of a modal
                const modals = document.querySelectorAll('[class*="modal" i], [class*="overlay" i], [class*="dialog" i], [role="dialog"]');
                for (const modal of modals) {
                    if (!modal.offsetParent) continue;
                    // Look for X/close inside the modal
                    const btns = modal.querySelectorAll('button, [role="button"], svg');
                    for (const btn of btns) {
                        const rect = btn.getBoundingClientRect();
                        const modalRect = modal.getBoundingClientRect();
                        // Top-right corner = close button
                        if (rect.right > modalRect.right - 80 && rect.top < modalRect.top + 80 && rect.width < 60) {
                            btn.click();
                            return 'modal_x';
                        }
                    }
                }
                return null;
            }""")

            if dismissed:
                logger.info(f"[polymarket] Dismissed modal via {dismissed}")
                await asyncio.sleep(1)
                return True

            # Strategy 3: Escape key
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
                # Check if modal is gone
                still_open = await page.evaluate("""() => {
                    const modals = document.querySelectorAll('[class*="modal" i], [class*="overlay" i], [role="dialog"]');
                    for (const m of modals) {
                        if (m.offsetParent !== null && m.offsetWidth > 200) return true;
                    }
                    return false;
                }""")
                if not still_open:
                    logger.info("[polymarket] Dismissed modal via Escape")
                    return True
            except Exception:
                pass

            await asyncio.sleep(1)

        logger.warning("[polymarket] Could not dismiss modal after all attempts")
        return False

    # ------------------------------------------------------------------
    # Portfolio scraping + settlement
    # ------------------------------------------------------------------

    async def scrape_history(self, page: "Page") -> list[dict]:
        """Scrape the History tab at /portfolio?tab=history.

        Returns list of {activity, market, outcome_tag, shares, value, time_ago}.
        Activity is 'Bought', 'Lost', 'Claimed', 'Deposited', etc.
        """
        current_url = page.url or ''
        if 'tab=history' not in current_url:
            logger.info(f"[polymarket] Not on history tab ({current_url[:60]}), skipping scrape")
            return []

        rows = await page.evaluate("""() => {
            const results = [];
            // Each history row is a container with Activity, Market info, Value, Time
            // Walk all rows by looking for activity labels
            const activityLabels = ['Bought', 'Lost', 'Claimed', 'Sold', 'Deposited', 'Withdrawn'];
            const allElements = document.querySelectorAll('div, span, p');

            // Strategy: find elements that contain exactly an activity label,
            // then walk up to the row container and extract siblings
            const seen = new Set();
            for (const el of allElements) {
                const text = (el.textContent || '').trim();
                if (!activityLabels.includes(text)) continue;
                // Must be a leaf-ish element (not a huge container)
                if (el.children.length > 2) continue;

                // Walk up to find the row container
                let row = el.parentElement;
                for (let i = 0; i < 6 && row; i++) {
                    // Row usually has multiple columns and is wide
                    if (row.offsetWidth > 500 && row.children.length >= 3) break;
                    row = row.parentElement;
                }
                if (!row) continue;

                // Dedup by row element
                const rowId = row.textContent.slice(0, 100);
                if (seen.has(rowId)) continue;
                seen.add(rowId);

                const rowText = row.textContent || '';

                // Extract activity
                const activity = text;

                // Extract market name — usually the longest text chunk
                let market = '';
                const links = row.querySelectorAll('a, [href]');
                for (const a of links) {
                    const t = (a.textContent || '').trim();
                    if (t.length > market.length && t.length > 10 && !activityLabels.includes(t)) {
                        market = t;
                    }
                }
                if (!market) {
                    // Fallback: grab text that's not the activity or value
                    for (const child of row.querySelectorAll('span, p, div')) {
                        const t = (child.textContent || '').trim();
                        if (t.length > 20 && !t.includes('$') && !activityLabels.includes(t) && t.length > market.length) {
                            market = t.slice(0, 120);
                        }
                    }
                }

                // Extract outcome tag (colored badge like "Team Solid 26¢")
                let outcomeTag = '';
                let shares = 0;
                for (const child of row.querySelectorAll('span, div, p')) {
                    const t = (child.textContent || '').trim();
                    // Outcome tag pattern: "Name XX¢"
                    const tagMatch = t.match(/^(.+?)\\s+(\\d+)¢$/);
                    if (tagMatch && t.length < 50) {
                        outcomeTag = tagMatch[1];
                    }
                    // Shares pattern: "XX.X shares"
                    const sharesMatch = t.match(/([\\d.]+)\\s*shares/);
                    if (sharesMatch) {
                        shares = parseFloat(sharesMatch[1]);
                    }
                }

                // Extract value ($XX.XX) — look for elements with $ sign
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

                // Time
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
        }""")

        logger.info(f"[polymarket] Scraped {len(rows)} history entries")
        return rows

    async def scrape_portfolio(self, page: "Page") -> list[dict]:
        """Scrape the portfolio/positions page and return each position.

        Navigates to polymarket.com/portfolio and scrapes all position rows.
        Returns list of {market, outcome_tag, avg_price, now_price, values, status, has_redeem, has_sell}.
        """
        # Navigate to portfolio positions tab
        current_url = page.url or ''
        if '/portfolio' not in current_url or 'tab=history' in current_url:
            await page.goto("https://polymarket.com/portfolio?tab=positions", wait_until="domcontentloaded", timeout=15000)
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
        for i, btn in enumerate(debug_info.get('buttons', [])):
            logger.info(f"[polymarket] Button {i}: type={btn.get('type')} text={btn.get('row_text', '')[:120]}")

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

            import re
            # Extract cent prices — handle both ¢ and encoded variants (Â¢)
            cent_prices = [float(m) for m in re.findall(r'([\d.]+)\s*(?:¢|\xc2\xa2|\u00a2)', text)]
            avg_price = cent_prices[0] if len(cent_prices) >= 1 else None
            now_price = cent_prices[1] if len(cent_prices) >= 2 else None

            # Extract dollar values
            dollar_values = [float(m.replace(',', '')) for m in re.findall(r'\$([\d,.]+)', text)]

            # Extract shares
            shares_match = re.search(r'([\d.]+)\s*shares', text)
            shares = float(shares_match.group(1)) if shares_match else None

            # Market name: text before the first price/number section
            market = text[:60].split('\n')[0] if text else ''
            market = re.sub(r'[\d¢$→\xc2\xa2].+', '', market).strip()

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
        if '/portfolio' not in (page.url or '') or 'tab=history' in (page.url or ''):
            await page.goto("https://polymarket.com/portfolio?tab=positions", wait_until="domcontentloaded", timeout=15000)
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
                    # Dismiss "Share your winnings" modal that appears after redeem
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

    async def claim_banner(self, page: "Page") -> dict:
        """Click the top-level Claim banner if present (green 'Claim' button).

        This is the banner that appears when you have uncollected winnings,
        separate from per-row Redeem buttons.

        Returns {claimed: bool, amount: str | None}.
        """
        try:
            result = await page.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    const t = (btn.textContent || '').trim();
                    // Match "Claim" button (not "Claimed" or other variants)
                    if (t === 'Claim' || t.startsWith('Claim')) {
                        // Verify it's in a banner/header area (not deep in a table row)
                        const rect = btn.getBoundingClientRect();
                        // Banner buttons are typically near the top of the page
                        if (rect.top < 400) {
                            btn.click();
                            return {found: true, text: t};
                        }
                    }
                }
                return {found: false};
            }""")

            if not result.get("found"):
                logger.info("[polymarket] No Claim banner found")
                return {"claimed": False, "amount": None}

            logger.info(f"[polymarket] Clicked Claim banner: {result.get('text')}")
            await asyncio.sleep(3)  # Wait for blockchain transaction

            # Check for a confirmation modal button (e.g. "Claim $47.33")
            confirmed = await page.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    const t = (btn.textContent || '').trim();
                    if (t.startsWith('Claim $') || t.startsWith('Claim $')) {
                        btn.click();
                        return t;
                    }
                }
                return null;
            }""")

            if confirmed:
                await asyncio.sleep(3)  # Wait for blockchain tx
                logger.info(f"[polymarket] Claim confirmed: {confirmed}")
                # Dismiss "Share your winnings" modal
                await self._dismiss_modal(page)
                return {"claimed": True, "amount": confirmed}

            # Banner click may have been sufficient (no modal)
            # Still try to dismiss any modal that appeared
            await self._dismiss_modal(page)
            return {"claimed": True, "amount": result.get("text")}

        except Exception as e:
            logger.warning(f"[polymarket] claim_banner failed: {e}")
            return {"claimed": False, "amount": None, "error": str(e)}

    # ------------------------------------------------------------------
    # Scan (preview only — no clicks)
    # ------------------------------------------------------------------

    async def scan_portfolio_settlements(self, page: "Page") -> dict:
        """Scrape positions and match against pending bets — NO clicking.

        Returns a preview of what settle_all would do:
        - positions scraped
        - matched settlements (bet_id, event, result, payout, pl)
        - claim banner detected
        - redeemable count

        The user reviews this before confirming execution.
        """
        from ...db.models import Bet, Event, get_session
        from ...repositories.profile_repo import ProfileRepo

        # Navigate to portfolio positions
        if '/portfolio' not in (page.url or '') or 'tab=history' in (page.url or ''):
            await page.goto(
                "https://polymarket.com/portfolio?tab=positions",
                wait_until="domcontentloaded", timeout=15000,
            )
            await asyncio.sleep(4)

        # Check for Claim banner (don't click)
        has_claim = await page.evaluate("""() => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const t = (btn.textContent || '').trim();
                if ((t === 'Claim' || t.startsWith('Claim')) && btn.getBoundingClientRect().top < 400) {
                    return t;
                }
            }
            return null;
        }""")

        # Count redeemable positions (don't click)
        redeem_count = await page.evaluate("""() => {
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
        }""")

        # Scrape positions
        positions = await self.scrape_portfolio(page)

        # Match against pending bets
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

                matches.append({
                    "bet_id": bet.id,
                    "event": event_name,
                    "market": bet.market,
                    "outcome": bet.outcome,
                    "odds": bet.odds,
                    "stake": bet.stake,
                    "result": status,
                    "payout": round(payout, 2),
                    "pl": round(payout - bet.stake, 2),
                })

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

    async def settle_all(self, page: "Page") -> dict:
        """Full settlement: navigate → claim → redeem → settle DB → void ghosts.

        1. Navigate to portfolio positions page
        2. Click Claim banner if present (dismiss share modal)
        3. Scrape all positions
        4. Match against pending bets in DB
        5. Detect ghost bets (pending in DB, no position on Polymarket) → void
        6. Click Redeem buttons for WON/LOST positions (dismiss share modals)
        7. Settle matched bets + void ghosts in DB
        8. Re-scrape balance

        Returns full summary with P&L breakdown.
        """
        from ...db.models import Bet, Event, get_session
        from ...repositories.profile_repo import ProfileRepo
        from ...services.bet_service import BetService

        # 1. Navigate to portfolio positions
        if '/portfolio' not in (page.url or '') or 'tab=history' in (page.url or ''):
            await page.goto(
                "https://polymarket.com/portfolio?tab=positions",
                wait_until="domcontentloaded", timeout=15000,
            )
            await asyncio.sleep(4)

        # 2. Click Claim banner (handles modal dismissal)
        claim_result = await self.claim_banner(page)

        # 3. Wait for page to settle after claim, then scrape
        if claim_result.get("claimed"):
            await asyncio.sleep(2)

        positions = await self.scrape_portfolio(page)

        # 4. Match against pending bets in DB
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
                # Import open positions as pending bets
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

                matches.append({
                    "bet_id": bet.id,
                    "event": event_name,
                    "market": bet.market,
                    "outcome": bet.outcome,
                    "odds": bet.odds,
                    "stake": bet.stake,
                    "result": status,
                    "payout": round(payout, 2),
                    "pl": round(payout - bet.stake, 2),
                })

            # 5. Detect ghost bets — pending in DB but no position on Polymarket
            matched_ids = {m["bet_id"] for m in matches}
            # Bets with a position still open (not won/lost) are live, not ghosts
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
                # No position at all → ghost bet (never placed or already redeemed)
                event_name = ""
                if event:
                    h = event.display_home or event.home_team or ""
                    a = event.display_away or event.away_team or ""
                    event_name = f"{h} vs {a}" if h and a else h or a
                ghost_bets.append({
                    "bet_id": bet.id,
                    "event": event_name,
                    "market": bet.market,
                    "outcome": bet.outcome,
                    "odds": bet.odds,
                    "stake": bet.stake,
                    "result": "void",
                    "payout": 0.0,
                    "pl": round(-bet.stake, 2),
                })
                logger.info(
                    f"[polymarket] Ghost bet #{bet.id} {event_name} — "
                    f"no position found, voiding (stake=${bet.stake})"
                )

            # 6. Click Redeem buttons (handles modal dismissal per redeem)
            redeem_result = await self.redeem_all(page)

            # 7. Settle in DB (matched + ghosts)
            bet_service = BetService(db)
            for m in matches:
                try:
                    bet_service.settle_bet(m["bet_id"], m["result"], m["payout"])
                    settled.append(m)
                    logger.info(
                        f"[polymarket] Settled bet #{m['bet_id']} {m['event']} "
                        f"→ {m['result']} (payout=${m['payout']})"
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

        # 8. Re-scrape balance
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
        """Import open Polymarket positions that aren't in our DB as pending bets.

        Deduplicates by market name, only imports positions with has_sell=True (open).
        """
        from ...services.bet_service import BetService
        from ...db.models import Bet
        import re

        # Get existing pending polymarket bets to avoid duplicates
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

        # Deduplicate positions (scraper returns collapsed + expanded)
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
            logger.info(f"[polymarket] Position: market={pos.get('market')} sell={pos.get('has_sell')} "
                        f"redeem={pos.get('has_redeem')} status={pos.get('status')} "
                        f"avg={pos.get('avg_price')} shares={pos.get('shares')}")
            if not pos.get("has_sell"):
                continue  # Only open positions
            if pos.get("status") in ("won", "lost"):
                continue

            market = pos.get("market", "")
            avg_price = pos.get("avg_price")
            shares = pos.get("shares")
            values = pos.get("values", [])

            # Extract slug from market name
            slug = re.sub(r'[^a-z0-9]+', '-', market.lower()).strip('-')

            # Skip if already tracked
            if slug.lower() in existing_markets:
                continue

            # Calculate stake: prefer avg_price * shares, fallback to first dollar value
            if shares and avg_price:
                stake = round(shares * avg_price / 100, 2)
            elif values:
                stake = round(values[0], 2)  # First $ value is typically cost basis
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
                # Save slug as confirmation_id
                bet_id = resp.get("id")
                if bet_id:
                    db_bet = db.query(Bet).filter(Bet.id == bet_id).first()
                    if db_bet:
                        db_bet.confirmation_id = slug
                imported.append({
                    "bet_id": bet_id,
                    "market": market,
                    "stake": stake,
                    "odds": odds,
                    "avg_price": avg_price,
                    "shares": shares,
                })
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

    async def cleanup(self, page: "Page") -> None:
        """Close persistent Polymarket tabs opened during placement."""
        for slug, tab in list(self._tabs.items()):
            try:
                if not tab.is_closed():
                    await tab.close()
            except Exception:
                pass
        self._tabs.clear()
