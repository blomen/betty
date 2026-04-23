"""InterwettenWorkflow — DOM-based workflow for Interwetten (Sportsbook Software GmbH).

Proprietary platform. All interaction is DOM-based (Cloudflare blocks API).
Balance from header, history from /en/journal/bets, betslip via data-betting attributes.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

_BALANCE_RE = re.compile(r"([\d\s]+[,.][\d]{2})\s*SEK")

_EVENT_RE = re.compile(r"^(.+?)\s*\((\w[\w\s]*?)\s*\)\s*->\s*(.+?)\s*/\s*([\d,.]+)\s*$")

# Map our market names to Interwetten market_type values in data-betting
_MARKET_TYPE_MAP = {
    "1x2": ["Match"],
    "moneyline": ["Match"],
    "spread": ["Handicap", "Asian Handicap"],
    "total": ["How many goals", "Over/Under"],
}

# Map our outcome names to Interwetten tip values
_OUTCOME_TIP_MAP = {
    "home": "1",
    "draw": "X",
    "away": "2",
    "over": " ",
    "under": " ",
}


def _parse_event_text(text: str) -> dict:
    """Parse 'Team A - Team B (Market) -> Outcome / Odds' format."""
    m = _EVENT_RE.match(text.strip())
    if m:
        return {
            "event_name": m.group(1).strip(),
            "market": m.group(2).strip().lower(),
            "outcome": m.group(3).strip(),
            "odds_text": m.group(4).strip(),
        }
    if "->" in text:
        parts = text.split("->", 1)
        event_part = parts[0].strip()
        outcome_part = parts[1].strip() if len(parts) > 1 else ""
        event_name = re.sub(r"\s*\([^)]*\)\s*$", "", event_part)
        return {
            "event_name": event_name,
            "market": "1x2",
            "outcome": outcome_part.split("/")[0].strip() if "/" in outcome_part else outcome_part,
            "odds_text": outcome_part.split("/")[-1].strip() if "/" in outcome_part else "",
        }
    return {"event_name": text, "market": "1x2", "outcome": "", "odds_text": ""}


def _parse_odds(odds_text: str) -> float:
    """Parse Interwetten odds string with comma decimal: '4,6' → 4.6."""
    try:
        return float(odds_text.replace(",", ".").replace(" ", ""))
    except (ValueError, AttributeError):
        return 0.0


def _parse_balance(text: str) -> float:
    """Parse Swedish balance format: '816,11 SEK' or '1 234,56 SEK' → 816.11."""
    m = _BALANCE_RE.search(text)
    if not m:
        return -1.0
    raw = m.group(1)
    # Remove spaces (thousand separators), convert comma decimal to dot
    raw = raw.replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return -1.0


def _g(obj, key, default=None):
    """Get attribute from object or dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class InterwettenWorkflow(ProviderWorkflow):
    """DOM-based workflow for Interwetten.

    Interwetten uses a proprietary platform (Sportsbook Software GmbH).
    All betslip interaction uses `data-betting` JSON attributes on market/outcome elements.
    Cloudflare protects the API so all reads are DOM-based.
    """

    platform = "interwetten"
    autonomous_placement = False

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    @property
    def home_url(self) -> str:
        return f"https://{self.domain}/en/sportsbook"

    # ------------------------------------------------------------------
    # check_login
    # ------------------------------------------------------------------

    async def check_login(self, page: Page) -> bool:
        """Check if user is logged in by looking for balance in header."""
        try:
            # Primary: balance element with SEK amount visible in header
            balance_text = await page.evaluate("""
                () => {
                    const selectors = [
                        '.user-balance', '.balance', '[class*="balance"]',
                        '.header-balance', '[data-testid="balance"]',
                        '.account-balance', '.wallet-balance'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && el.textContent && el.textContent.includes('SEK')) {
                            return el.textContent.trim();
                        }
                    }
                    // Fallback: any element containing SEK with digits
                    const all = document.querySelectorAll('*');
                    for (const el of all) {
                        if (el.children.length === 0 && el.textContent) {
                            const t = el.textContent.trim();
                            if (/\\d[\\d\\s]*[,.][\\d]{2}\\s*SEK/.test(t)) {
                                return t;
                            }
                        }
                    }
                    return null;
                }
            """)
            if balance_text:
                logger.debug(f"[{self.provider_id}] Login detected via balance: {balance_text!r}")
                return True

            # Fallback: "Last Login" or logout link present
            has_session = await page.evaluate("""
                () => {
                    const body = document.body.innerText || '';
                    if (body.includes('Last Login') || body.includes('Last login')) return true;
                    if (document.querySelector('a[href*="logout"]')) return true;
                    if (document.querySelector('a[href*="signout"]')) return true;
                    if (document.querySelector('[class*="logout"]')) return true;
                    if (document.querySelector('[class*="account-menu"]')) return true;
                    return false;
                }
            """)
            return bool(has_session)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] check_login error: {e}")
            return False

    # ------------------------------------------------------------------
    # sync_balance
    # ------------------------------------------------------------------

    async def sync_balance(self, page: Page) -> float:
        """Read balance from header. Returns amount in SEK."""
        try:
            balance_text = await page.evaluate("""
                () => {
                    const selectors = [
                        '.user-balance', '.balance', '[class*="balance"]',
                        '.header-balance', '[data-testid="balance"]',
                        '.account-balance', '.wallet-balance'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && el.textContent) {
                            return el.textContent.trim();
                        }
                    }
                    // Fallback: scan for SEK amount in header area
                    const header = document.querySelector('header') || document.body;
                    const walker = document.createTreeWalker(header, NodeFilter.SHOW_TEXT);
                    while (walker.nextNode()) {
                        const t = walker.currentNode.textContent.trim();
                        if (/[\\d][\\d\\s]*[,.][\\d]{2}\\s*SEK/.test(t)) {
                            return t;
                        }
                    }
                    return null;
                }
            """)
            if balance_text:
                amount = _parse_balance(balance_text)
                if amount >= 0:
                    logger.info(f"[{self.provider_id}] Balance: {amount:.2f} SEK")
                    return amount
        except Exception as e:
            logger.warning(f"[{self.provider_id}] sync_balance error: {e}")
        return -1.0

    # ------------------------------------------------------------------
    # sync_history
    # ------------------------------------------------------------------

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Navigate to /en/journal/bets and parse bet history table."""
        entries: list[HistoryEntry] = []
        try:
            journal_url = f"https://{self.domain}/en/journal/bets"
            logger.info(f"[{self.provider_id}] Navigating to journal: {journal_url}")
            await page.goto(journal_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2000)

            # Wait for table to appear (or detect empty state)
            try:
                await page.wait_for_selector(
                    "table a[href*='/journal/betdetail/'], .bet-history-table, .journal-table",
                    timeout=10_000,
                )
            except Exception:
                logger.info(f"[{self.provider_id}] No bet history table found (may be empty)")
                return entries

            rows_data = await page.evaluate("""
                () => {
                    const rows = [];
                    // Find all rows with a betdetail link
                    const links = document.querySelectorAll('a[href*="/journal/betdetail/"]');
                    const processedRows = new Set();

                    for (const link of links) {
                        const row = link.closest('tr');
                        if (!row || processedRows.has(row)) continue;
                        processedRows.add(row);

                        const cells = Array.from(row.querySelectorAll('td'));
                        if (cells.length < 8) continue;

                        // Extract bet_id from link href or date cell text
                        const href = link.getAttribute('href') || '';
                        const idMatch = href.match(/(\\d{6,})/);
                        const bet_id = idMatch ? idMatch[1] : '';

                        // Column layout (0-indexed):
                        // 0=icon, 1=Date(ID), 2=Type, 3=EVENT, 4=Matchday, 5=RESULT, 6=TIP, 7=Odds, 8=STAKE, 9=PROFIT
                        const dateText = cells[1] ? cells[1].textContent.trim() : '';
                        const eventText = cells[3] ? cells[3].textContent.trim() : '';
                        const resultText = cells[5] ? cells[5].textContent.trim() : '';
                        const tipText = cells[6] ? cells[6].textContent.trim() : '';
                        const oddsText = cells[7] ? cells[7].textContent.trim() : '';
                        const stakeText = cells[8] ? cells[8].textContent.trim() : '';
                        const profitText = cells[9] ? cells[9].textContent.trim() : '';

                        // Icon/result cell title for won/lost
                        const iconEl = cells[0] ? cells[0].querySelector('[title]') : null;
                        const iconTitle = iconEl ? iconEl.getAttribute('title') : '';
                        // Also check result cell
                        const resultIconEl = cells[5] ? cells[5].querySelector('[title]') : null;
                        const resultIconTitle = resultIconEl ? resultIconEl.getAttribute('title') : '';

                        rows.push({
                            bet_id,
                            dateText,
                            eventText,
                            resultText,
                            tipText,
                            oddsText,
                            stakeText,
                            profitText,
                            iconTitle,
                            resultIconTitle,
                        });
                    }
                    return rows;
                }
            """)

            for row in rows_data or []:
                try:
                    bet_id = row.get("bet_id", "")
                    if not bet_id:
                        # Try extracting 6+ digit number from date cell
                        m = re.search(r"\d{6,}", row.get("dateText", ""))
                        if m:
                            bet_id = m.group(0)
                    if not bet_id:
                        continue

                    event_text = row.get("eventText", "")
                    parsed = _parse_event_text(event_text)
                    event_name = parsed["event_name"]
                    market = parsed["market"] or "1x2"
                    outcome = parsed["outcome"] or row.get("tipText", "")

                    odds = _parse_odds(row.get("oddsText", "0"))
                    stake_raw = row.get("stakeText", "0").replace(" ", "").replace(",", ".")
                    stake = 0.0
                    with contextlib.suppress(ValueError):
                        stake = float(re.sub(r"[^\d.]", "", stake_raw))

                    profit_text = row.get("profitText", "").strip()

                    # Determine status
                    icon_title = (row.get("iconTitle", "") + " " + row.get("resultIconTitle", "")).lower()
                    if profit_text in ("---", "-", ""):
                        status = "pending"
                        payout = None
                    elif "won" in icon_title or "win" in icon_title or "correct" in icon_title:
                        status = "won"
                        profit_clean = profit_text.replace(" ", "").replace(",", ".")
                        try:
                            payout = float(re.sub(r"[^\d.]", "", profit_clean))
                        except ValueError:
                            payout = 0.0
                    elif "lost" in icon_title or "loss" in icon_title or "incorrect" in icon_title:
                        status = "lost"
                        payout = 0.0
                    elif "void" in icon_title or "cancel" in icon_title or "refund" in icon_title:
                        status = "void"
                        payout = stake
                    else:
                        # Infer from profit value
                        profit_clean = profit_text.replace(" ", "").replace(",", ".")
                        profit_digits = re.sub(r"[^\d.-]", "", profit_clean)
                        try:
                            profit_val = float(profit_digits)
                            if profit_val > 0:
                                status = "won"
                                payout = profit_val
                            elif profit_val == 0:
                                status = "void"
                                payout = stake
                            else:
                                status = "lost"
                                payout = 0.0
                        except ValueError:
                            status = "pending"
                            payout = None

                    entries.append(
                        HistoryEntry(
                            provider_bet_id=str(bet_id),
                            event_name=event_name,
                            market=market,
                            outcome=outcome,
                            odds=odds,
                            stake=stake,
                            status=status,
                            payout=payout,
                        )
                    )
                except Exception as row_err:
                    logger.warning(f"[{self.provider_id}] Failed to parse history row: {row_err} — {row}")

            logger.info(f"[{self.provider_id}] sync_history: {len(entries)} entries")
        except Exception as e:
            logger.error(f"[{self.provider_id}] sync_history failed: {e}")
        return entries

    # ------------------------------------------------------------------
    # navigate_to_event
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate to the event page via /en/sportsbook/e/{event_id}/{slug}.

        Falls back to search-by-team-name if no interwetten_event_id available
        (e.g. odds extracted before provider_meta was added).
        """
        try:
            event_id = _g(bet, "interwetten_event_id") or _g(bet, "provider_event_id")
            home = _g(bet, "display_home") or _g(bet, "home_team") or ""
            away = _g(bet, "display_away") or _g(bet, "away_team") or ""

            if event_id:
                # Direct navigation by event ID
                slug_raw = f"{home}-{away}" if home and away else "event"
                slug = re.sub(r"[^a-zA-Z0-9-]", "-", slug_raw).lower().strip("-")
                slug = re.sub(r"-+", "-", slug)
                url = f"https://{self.domain}/en/sportsbook/e/{event_id}/{slug}"
                logger.info(f"[{self.provider_id}] Navigating to event: {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            elif home:
                # Fallback: search by home team name
                logger.info(f"[{self.provider_id}] No event_id — searching by team: {home}")
                found = await self._search_and_navigate(page, home, away)
                if not found:
                    return False
            else:
                logger.warning(f"[{self.provider_id}] No event_id or team names for navigation")
                return False

            try:
                await page.wait_for_selector(".s-market-grid", timeout=10_000)
                logger.info(f"[{self.provider_id}] Market grid loaded")
                return True
            except Exception:
                current_url = page.url
                if "/sportsbook/e/" in current_url:
                    logger.info(f"[{self.provider_id}] On event page but no market grid yet: {current_url}")
                    return True
                logger.warning(f"[{self.provider_id}] Market grid not found")
                return False
        except Exception as e:
            logger.error(f"[{self.provider_id}] navigate_to_event failed: {e}")
            return False

    async def _search_and_navigate(self, page: Page, home: str, away: str) -> bool:
        """Search for event by team name using Interwetten's search page."""
        try:
            import asyncio

            # Use Interwetten's search URL pattern
            query = home.split()[-1] if home else ""  # Use last word (surname) for best match
            if not query:
                return False
            search_url = f"https://{self.domain}/en/sportsbook?search={query}"
            logger.info(f"[{self.provider_id}] Searching: {search_url}")
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15_000)
            await asyncio.sleep(2)

            # Click first matching event link
            home_lower = home.lower()
            away_lower = away.lower() if away else ""
            result = await page.evaluate(
                """
                (args) => {
                    const { homeLower, awayLower } = args;
                    const links = document.querySelectorAll('a[href*="/sportsbook/e/"]');
                    for (const link of links) {
                        const text = (link.textContent || '').toLowerCase();
                        if (text.includes(homeLower) || (awayLower && text.includes(awayLower))) {
                            const href = link.getAttribute('href');
                            link.click();
                            return { found: true, href };
                        }
                    }
                    return { found: false, count: links.length };
                }
            """,
                {"homeLower": home_lower, "awayLower": away_lower},
            )

            if result and result.get("found"):
                logger.info(f"[{self.provider_id}] Found event via search: {result.get('href')}")
                await asyncio.sleep(1)
                return True

            logger.warning(
                f"[{self.provider_id}] Event not found via search: {home} vs {away} "
                f"({result.get('count', 0)} links on page)"
            )
            return False
        except Exception as e:
            logger.warning(f"[{self.provider_id}] _search_and_navigate error: {e}")
            return False

    # ------------------------------------------------------------------
    # _find_outcome_element
    # ------------------------------------------------------------------

    async def _find_outcome_element(self, page: Page, bet) -> dict | None:
        """Scan .s-market-grid elements to find the matching outcome.

        data-betting on market: [market_id, event_id, "short_name", "market_type", ...]
        data-betting on outcome: [outcome_id, "tip", "outcome_name", "outcome_name", "odds_comma", ...]

        Returns dict with outcome_id, odds, element handle info, or None.
        """
        market = (_g(bet, "market") or "1x2").lower()
        outcome = (_g(bet, "outcome") or "").lower()
        spread_point = _g(bet, "point")  # e.g. -1.5 for home spread

        target_market_types = _MARKET_TYPE_MAP.get(market, ["Match"])

        try:
            result = await page.evaluate(
                """
                (args) => {
                    const { targetMarketTypes, market, outcome, spreadPoint } = args;

                    const grids = document.querySelectorAll('.s-market-grid');
                    const candidates = [];

                    for (const grid of grids) {
                        const gridBetting = grid.getAttribute('data-betting');
                        if (!gridBetting) continue;

                        let gridData;
                        try { gridData = JSON.parse(gridBetting); } catch { continue; }

                        // gridData[3] = market_type string
                        const marketType = gridData[3] || '';
                        const isTargetMarket = targetMarketTypes.some(t =>
                            marketType.toLowerCase().includes(t.toLowerCase())
                        );
                        if (!isTargetMarket) continue;

                        // For spread: parse handicap from "Handicap H:A" (e.g. "Handicap 0:1" = away +1)
                        let gridSpreadPoint = null;
                        if (market === 'spread') {
                            const colonMatch = marketType.match(/(\\d+):(\\d+)/);
                            if (colonMatch) {
                                gridSpreadPoint = parseInt(colonMatch[1]) - parseInt(colonMatch[2]);
                            } else {
                                const numMatch = marketType.match(/([+-]?[\\d.]+)/);
                                if (numMatch) gridSpreadPoint = parseFloat(numMatch[1]);
                            }
                        } else if (market === 'total') {
                            const numMatch = marketType.match(/([\\d.]+)/);
                            if (numMatch) gridSpreadPoint = parseFloat(numMatch[1]);
                        }

                        // Find outcome elements within this grid
                        const outcomeEls = grid.querySelectorAll('[data-betting]');
                        for (const el of outcomeEls) {
                            if (el === grid) continue;
                            const elBetting = el.getAttribute('data-betting');
                            if (!elBetting) continue;

                            let elData;
                            try { elData = JSON.parse(elBetting); } catch { continue; }

                            // elData: [outcome_id, "tip", "outcome_name", "outcome_name", "odds_comma", ...]
                            if (!Array.isArray(elData) || elData.length < 5) continue;

                            const outcomeId = elData[0];
                            const tip = (elData[1] || '').toLowerCase();
                            const outcomeName = (elData[2] || '').toLowerCase();
                            const oddsText = elData[4] || '';

                            let matched = false;

                            if (market === '1x2' || market === 'moneyline') {
                                // Match by tip: home->1, draw->X, away->2
                                const tipMap = { home: '1', draw: 'x', away: '2' };
                                const expectedTip = tipMap[outcome] || outcome;
                                matched = tip === expectedTip.toLowerCase() ||
                                          outcomeName.includes(outcome);
                            } else if (market === 'spread') {
                                // Match by outcome (home/away) and optionally spread point
                                const isHome = outcome === 'home';
                                const isAway = outcome === 'away';
                                if (isHome && (tip === '1' || outcomeName.includes('home') || tip === 'home')) {
                                    matched = spreadPoint === null || gridSpreadPoint === null ||
                                              Math.abs(gridSpreadPoint - spreadPoint) < 0.26;
                                } else if (isAway && (tip === '2' || outcomeName.includes('away') || tip === 'away')) {
                                    matched = spreadPoint === null || gridSpreadPoint === null ||
                                              Math.abs(gridSpreadPoint + spreadPoint) < 0.26;
                                }
                            } else if (market === 'total') {
                                const isOver = outcome === 'over';
                                const isUnder = outcome === 'under';
                                if (isOver && (outcomeName.includes('over') || tip.includes('over') || tip === 'o')) {
                                    matched = spreadPoint === null || gridSpreadPoint === null ||
                                              Math.abs(gridSpreadPoint - spreadPoint) < 0.26;
                                } else if (isUnder && (outcomeName.includes('under') || tip.includes('under') || tip === 'u')) {
                                    matched = spreadPoint === null || gridSpreadPoint === null ||
                                              Math.abs(gridSpreadPoint - spreadPoint) < 0.26;
                                }
                            }

                            if (matched) {
                                candidates.push({
                                    outcomeId: String(outcomeId),
                                    oddsText: oddsText,
                                    tip: tip,
                                    outcomeName: elData[2],
                                    marketType: marketType,
                                    gridSpreadPoint: gridSpreadPoint,
                                    isSelected: el.classList.contains('s-outcome-selected'),
                                });
                            }
                        }
                    }
                    return candidates.length > 0 ? candidates[0] : null;
                }
            """,
                {
                    "targetMarketTypes": target_market_types,
                    "market": market,
                    "outcome": outcome,
                    "spreadPoint": spread_point,
                },
            )

            return result
        except Exception as e:
            logger.warning(f"[{self.provider_id}] _find_outcome_element error: {e}")
            return None

    # ------------------------------------------------------------------
    # prep_betslip
    # ------------------------------------------------------------------

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        """Auto-select outcome, fill stake, return prepped result."""
        bet_id = _g(bet, "id") or 0
        fair_odds = _g(bet, "fair_odds") or 0.0

        try:
            # Clear any existing betslip selections
            selected_count = await page.evaluate("""
                () => {
                    const selected = document.querySelectorAll('.s-outcome-selected');
                    for (const el of selected) el.click();
                    return selected.length;
                }
            """)
            if selected_count:
                logger.debug(f"[{self.provider_id}] Cleared {selected_count} existing betslip selections")
                await page.wait_for_timeout(500)

            # Find the matching outcome element
            outcome_info = await self._find_outcome_element(page, bet)
            if not outcome_info:
                logger.warning(f"[{self.provider_id}] Could not find outcome element for bet {bet_id}")
                return PlacementResult(
                    status="failed",
                    bet_id=bet_id,
                    reason="outcome_not_found",
                )

            outcome_id = outcome_info["outcomeId"]
            odds_text = outcome_info["oddsText"]
            live_odds = _parse_odds(odds_text) if odds_text else 0.0

            logger.info(
                f"[{self.provider_id}] Found outcome {outcome_id} "
                f"({outcome_info['outcomeName']}) @ {live_odds} "
                f"(market: {outcome_info['marketType']})"
            )

            # Check live edge — skip if negative EV
            if live_odds > 0 and fair_odds > 0:
                live_edge = (live_odds / fair_odds - 1) * 100
                if live_edge < 0:
                    logger.info(f"[{self.provider_id}] Live edge {live_edge:.1f}% < 0, auto-skipping bet {bet_id}")
                    return PlacementResult(
                        status="skipped",
                        bet_id=bet_id,
                        actual_odds=live_odds,
                        reason=f"negative_edge_{live_edge:.1f}pct",
                    )

            # Click the outcome element to add it to betslip
            # Use data-betting attribute selector to find the specific element
            clicked = await page.evaluate(
                """
                (outcomeId) => {
                    const els = document.querySelectorAll('[data-betting]');
                    for (const el of els) {
                        const db = el.getAttribute('data-betting');
                        try {
                            const data = JSON.parse(db);
                            if (Array.isArray(data) && String(data[0]) === String(outcomeId)) {
                                el.click();
                                return true;
                            }
                        } catch { continue; }
                    }
                    return false;
                }
            """,
                outcome_id,
            )

            if not clicked:
                logger.warning(f"[{self.provider_id}] Could not click outcome {outcome_id}")
                return PlacementResult(
                    status="failed",
                    bet_id=bet_id,
                    reason="click_failed",
                )

            await page.wait_for_timeout(800)

            # Fill stake input — try ID first, then generic placeholder
            stake_filled = await page.evaluate(
                """
                (args) => {
                    const { outcomeId, stake } = args;
                    // Try specific stake input by ID
                    let input = document.querySelector('#amount_' + outcomeId);
                    if (!input) {
                        // Generic stake input in betslip
                        input = document.querySelector("input[placeholder='Stake']");
                    }
                    if (!input) {
                        // Any visible stake input in betslip area
                        const betslip = document.querySelector('#betslip, .betslip, [class*="betslip"]');
                        if (betslip) {
                            input = betslip.querySelector('input[type="text"], input[type="number"]');
                        }
                    }
                    if (!input) return false;

                    // Clear and fill
                    input.focus();
                    input.value = '';
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.value = String(stake);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            """,
                {"outcomeId": outcome_id, "stake": stake},
            )

            if not stake_filled:
                logger.warning(f"[{self.provider_id}] Could not fill stake input for outcome {outcome_id}")
                # Not a hard failure — user can fill manually
                return PlacementResult(
                    status="prepped",
                    bet_id=bet_id,
                    actual_odds=live_odds,
                    reason="stake_fill_failed_manual_required",
                )

            logger.info(f"[{self.provider_id}] Prepped betslip: outcome {outcome_id}, stake {stake}, odds {live_odds}")
            return PlacementResult(
                status="prepped",
                bet_id=bet_id,
                actual_odds=live_odds,
            )

        except Exception as e:
            logger.error(f"[{self.provider_id}] prep_betslip failed: {e}")
            return PlacementResult(status="failed", bet_id=bet_id, reason=str(e))

    # ------------------------------------------------------------------
    # confirm_bet
    # ------------------------------------------------------------------

    async def confirm_bet(self, page: Page) -> PlacementResult:
        """Click the submit button and verify the betslip clears (success) or error appeared."""
        try:
            # Click the submit button
            submitted = await page.evaluate("""
                () => {
                    const btn = document.querySelector('#BS_Button_Submit');
                    if (btn) { btn.click(); return true; }
                    // Fallback selectors
                    const fallbacks = [
                        'button[type="submit"][class*="betslip"]',
                        'button[class*="place-bet"]',
                        'button[class*="confirm"]',
                        '.betslip-submit',
                        '[data-testid="place-bet"]',
                    ];
                    for (const sel of fallbacks) {
                        const el = document.querySelector(sel);
                        if (el) { el.click(); return true; }
                    }
                    return false;
                }
            """)

            if not submitted:
                logger.warning(f"[{self.provider_id}] Could not find submit button")
                return PlacementResult(status="failed", bet_id=0, reason="submit_button_not_found")

            await page.wait_for_timeout(2000)

            # Check outcome: betslip cleared = success, error element = failure
            result = await page.evaluate("""
                () => {
                    // Success: betslip emptied (selection count went to 0)
                    const selections = document.querySelectorAll('.s-outcome-selected');
                    if (selections.length === 0) {
                        // Confirm no error message visible
                        const errorEl = document.querySelector(
                            '.error-message, .alert-error, [class*="error"]:not([class*="btn"])'
                        );
                        if (!errorEl || !errorEl.textContent.trim()) {
                            return { success: true, error: null };
                        }
                        return { success: false, error: errorEl.textContent.trim() };
                    }
                    // Check for explicit error element
                    const errorEl = document.querySelector(
                        '.error-message, .alert-error, [class*="bet-error"], [class*="placement-error"]'
                    );
                    if (errorEl && errorEl.textContent.trim()) {
                        return { success: false, error: errorEl.textContent.trim() };
                    }
                    // Betslip still has selections — ambiguous, treat as pending
                    return { success: null, error: null };
                }
            """)

            if result["success"] is True:
                logger.info(f"[{self.provider_id}] Bet placed successfully (betslip cleared)")
                return PlacementResult(status="placed", bet_id=0)
            elif result["success"] is False:
                error_msg = result.get("error", "unknown_error")
                logger.warning(f"[{self.provider_id}] Bet placement error: {error_msg}")
                return PlacementResult(status="failed", bet_id=0, reason=error_msg)
            else:
                # Ambiguous — betslip still showing, no error
                logger.info(f"[{self.provider_id}] Bet submit ambiguous — betslip still active")
                return PlacementResult(status="manual", bet_id=0, reason="awaiting_confirmation")

        except Exception as e:
            logger.error(f"[{self.provider_id}] confirm_bet failed: {e}")
            return PlacementResult(status="failed", bet_id=0, reason=str(e))

    # ------------------------------------------------------------------
    # place_bet
    # ------------------------------------------------------------------

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Full placement: prep betslip then confirm."""
        prep_result = await self.prep_betslip(page, bet, stake)
        if prep_result.status not in ("prepped",):
            return prep_result
        return await self.confirm_bet(page)

    # ------------------------------------------------------------------
    # check_live_price
    # ------------------------------------------------------------------

    async def check_live_price(self, page: Page, bet) -> tuple[float | None, float | None]:
        """Read live odds for the target outcome and compute edge vs fair_odds."""
        try:
            outcome_info = await self._find_outcome_element(page, bet)
            if not outcome_info:
                return None, None

            odds_text = outcome_info.get("oddsText", "")
            live_odds = _parse_odds(odds_text) if odds_text else None
            if not live_odds or live_odds <= 1.0:
                return None, None

            fair_odds = _g(bet, "fair_odds")
            live_edge = (live_odds / fair_odds - 1) * 100 if fair_odds and fair_odds > 0 else None

            return live_odds, live_edge
        except Exception as e:
            logger.warning(f"[{self.provider_id}] check_live_price error: {e}")
            return None, None
