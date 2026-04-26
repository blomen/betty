"""PinnacleMirrorWorkflow — Playwright mirror automation for Pinnacle.

Discovery source: docs/superpowers/specs/2026-04-26-pinnacle-discovery.md

Key facts:
- Login/balance: DOM text scrape (CSP blocks injected fetch to api.arcadia.pinnacle.se)
- Slip state:    localStorage["Main:Betslip"] → Selections[0].price (American odds)
- Stake input:   input[placeholder="Stake"] via React hidden-setter pattern (verified)
- Outcome btns: button.market-btn — walk DOM up to find market label, pick by position
- Place button: text starts with "CONFIRM" (e.g. "CONFIRM 1 SINGLE BET")
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Market label text → canonical market type (Pinnacle site labels)
_MARKET_LABEL_MAP = {
    "money line": "moneyline",
    "moneyline": "moneyline",
    "1x2": "1x2",
    "spread": "spread",
    "handicap": "spread",
    "total": "total",
    "total points": "total",
    "over/under": "total",
}

# For a given market type the visual button order maps to outcomes.
# Pinnacle renders home first, then draw (1x2 only), then away.
# For totals: over first, under second.
_OUTCOME_POSITION: dict[str, dict[str, int]] = {
    "1x2": {"home": 0, "draw": 1, "away": 2},
    "moneyline": {"home": 0, "away": 1},
    "spread": {"home": 0, "away": 1},
    "total": {"over": 0, "under": 1},
}

# Accent translation table for _slugify — lifted to module scope so it's built once.
# Lowercase variants then uppercase variants — same letters, paired.
_ACCENT_SRC = "àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞ"
_ACCENT_DST = "aaaaaaeceeeeiiiidnoooooouuuuytyAAAAAAECEEEEIIIIDNOOOOOOUUUUYT"
assert len(_ACCENT_SRC) == len(_ACCENT_DST), "Pinnacle accent map mismatch"
_ACCENT_TABLE = str.maketrans(_ACCENT_SRC, _ACCENT_DST)


def _g(obj, k, d=None):
    """Get attribute or dict key — handles both shapes (used for bet dicts that
    may come in as either dataclass-like objects or plain dicts)."""
    if isinstance(obj, dict):
        return obj.get(k, d)
    return getattr(obj, k, d)


def american_to_decimal(price: float) -> float:
    """Convert American odds to decimal.

    American -133 → decimal ≈ 1.752  (price < 0: 1 + 100/abs(price))
    American +200 → decimal 3.0      (price > 0: 1 + price/100)
    Edge case: ±100 both → 2.0.
    """
    if price < 0:
        return 1.0 + 100.0 / abs(price)
    return 1.0 + price / 100.0


class PinnacleMirrorWorkflow(ProviderWorkflow):
    """Mirror workflow for Pinnacle.se.

    Placement is mirror-only — the user clicks "CONFIRM" on the Pinnacle tab
    and we intercept the resulting placement XHR.  `autonomous_placement = False`.
    """

    platform = "pinnacle_mirror"
    autonomous_placement = False

    def __init__(
        self,
        provider_id: str = "pinnacle",
        domain: str = "pinnacle.se",
        mode: WorkflowMode = WorkflowMode.GUIDED,
    ):
        super().__init__(provider_id=provider_id, domain=domain, mode=mode)

    @property
    def home_url(self) -> str:
        return f"https://www.{self.domain}/en/"

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: Page) -> bool:
        """Return True when the page shows a logged-in state via DOM text signals.

        CSP blocks injected scripts from calling api.arcadia.pinnacle.se, so we
        read the rendered page text instead.  A logged-in screen has DEPONERA
        (Swedish: "DEPOSIT") button + SEK balance amount, and no LOG IN / JOIN
        button.  Any one of these missing means not logged in.
        """
        try:
            result = await page.evaluate(
                """() => {
                    const text = document.body.innerText || '';
                    const hasLogin = /\\bLOG IN\\b/i.test(text) || /\\bJOIN\\b/i.test(text);
                    const hasBalance = /SEK\\s*[\\d,.]+/i.test(text);
                    const hasDeposit = /\\bDEPONERA\\b/i.test(text) || /\\bDEPOSIT\\b/i.test(text);
                    // Logged-in screen has DEPONERA button + balance, no LOG IN button
                    return hasBalance && hasDeposit && !hasLogin;
                }"""
            )
            return bool(result)
        except Exception:
            return False

    async def sync_balance(self, page: Page) -> float:
        """Return the available balance scraped from the top-bar DOM text.

        CSP blocks injected scripts from calling api.arcadia.pinnacle.se, so we
        parse the rendered balance amount from document.body.innerText instead.
        Matches patterns like "SEK 80.00" or "80,00 KR".
        Returns -1 on any error or when the balance is not visible.
        """
        try:
            raw = await page.evaluate(
                """() => {
                    const text = document.body.innerText || '';
                    const m = text.match(/(\\d+[,.]\\d+)\\s*KR/i) || text.match(/SEK\\s*([\\d,.]+)/i);
                    return m ? m[1].replace(',', '.') : null;
                }"""
            )
            if not raw:
                return -1.0
            return float(raw)
        except Exception:
            return -1.0

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Stub — history endpoint not observed during discovery.

        # TODO(pinnacle-history): Navigate to /en/account/bet-history/ and
        # intercept the /0.1/wagers or /0.1/bets/history XHR.  Implement once
        # the first manual visit to the history page captures the endpoint shape.
        """
        logger.info(
            f"[{self.provider_id}] sync_history stub returning [] — pending bets won't reconcile until implemented"
        )
        return []

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate to the Pinnacle event page for this bet.

        # TODO(pinnacle-event-mapping): A proper event_id → Pinnacle matchupId
        # mapping endpoint should be added.  Currently we either detect the
        # matchupId already in the page URL (no-op) or fall back to opening the
        # league page and scanning anchor text for matching home/away names.

        URL pattern (verified):
            https://www.pinnacle.se/en/{sport}/{league-slug}/{home}-vs-{away}/{matchupId}/
        """
        current_url = page.url or ""

        # Step 1: already on the event page (matchupId in URL)
        matchup_id = _g(bet, "matchup_id") or _g(bet, "provider_meta", {}) or {}
        if isinstance(matchup_id, dict):
            matchup_id = matchup_id.get("matchup_id")
        if matchup_id and str(matchup_id) in current_url:
            logger.debug(f"[{self.provider_id}] navigate_to_event: already on matchup {matchup_id}")
            return True

        # Step 2: try constructing slug-based URL when we have enough metadata
        sport = _g(bet, "sport") or ""
        league = _g(bet, "league") or ""
        display_home = _g(bet, "display_home") or ""
        display_away = _g(bet, "display_away") or ""

        if matchup_id and sport and league and display_home and display_away:
            sport_slug = _slugify(sport)
            league_slug = _slugify(league)
            home_slug = _slugify(display_home)
            away_slug = _slugify(display_away)
            url = f"https://www.{self.domain}/en/{sport_slug}/{league_slug}/{home_slug}-vs-{away_slug}/{matchup_id}/"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                if str(matchup_id) in (page.url or ""):
                    logger.info(f"[{self.provider_id}] navigate_to_event: reached matchup via slug URL")
                    return True
                logger.warning(f"[{self.provider_id}] navigate_to_event: slug URL redirected away from matchup")
            except Exception as e:
                logger.warning(f"[{self.provider_id}] navigate_to_event: slug URL failed: {e}")

        # Step 3: league-page DOM search — find link containing both team names
        if sport and league and display_home and display_away:
            sport_slug = _slugify(sport)
            league_slug = _slugify(league)
            league_url = f"https://www.{self.domain}/en/{sport_slug}/{league_slug}/matchups/"
            try:
                await page.goto(league_url, wait_until="domcontentloaded", timeout=15000)
                # Scan <a> elements for one whose text contains both team names
                home_lower = display_home.lower()
                away_lower = display_away.lower()
                links = await page.query_selector_all("a[href]")
                for link in links:
                    text = (await link.inner_text()).lower()
                    if home_lower in text and away_lower in text:
                        href = await link.get_attribute("href")
                        logger.info(f"[{self.provider_id}] navigate_to_event: found event link via DOM: {href}")
                        await link.click()
                        await page.wait_for_url(lambda url: url != league_url, timeout=8000)
                        return True
                logger.warning(f"[{self.provider_id}] navigate_to_event: no matching link found on league page")
            except Exception as e:
                logger.warning(f"[{self.provider_id}] navigate_to_event: league page search failed: {e}")

        logger.warning(
            f"[{self.provider_id}] navigate_to_event: could not navigate — "
            f"sport={sport!r} league={league!r} home={display_home!r} away={display_away!r}"
        )
        return False

    # ------------------------------------------------------------------
    # Betslip — prep / read / update
    # ------------------------------------------------------------------

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        """Click the correct outcome button, wait for localStorage slip to populate,
        then write the stake via the React hidden-setter pattern.

        Steps:
          1. Find button.market-btn matching market type + outcome
          2. Click it
          3. Wait up to 5s for localStorage["Main:Betslip"].Selections.length > 0
          4. Write stake via DOM input setter (verified in discovery)
        """
        market = (_g(bet, "market") or "moneyline").lower()
        outcome = (_g(bet, "outcome") or "home").lower()
        bet_id = _g(bet, "bet_id", 0) or 0

        # Find and click the correct market-btn
        clicked = await self._click_market_btn(page, market, outcome)
        if not clicked:
            logger.warning(
                f"[{self.provider_id}] prep_betslip: could not click outcome market={market!r} outcome={outcome!r}"
            )
            return PlacementResult(
                status="failed",
                bet_id=bet_id,
                reason="outcome_btn_not_found",
            )

        # Wait for slip to populate (up to 5s, poll every 250ms)
        slip_populated = False
        for _ in range(20):
            try:
                count = await page.evaluate(
                    """() => {
                        const raw = localStorage.getItem("Main:Betslip");
                        if (!raw) return 0;
                        try {
                            const d = JSON.parse(raw);
                            return (d?.Selections ?? []).length;
                        } catch { return 0; }
                    }"""
                )
                if count and int(count) > 0:
                    slip_populated = True
                    break
            except Exception:
                pass

            await asyncio.sleep(0.25)

        if not slip_populated:
            logger.warning(f"[{self.provider_id}] prep_betslip: slip not populated within 5s")
            return PlacementResult(
                status="failed",
                bet_id=bet_id,
                reason="slip_not_populated",
            )

        # Write stake
        await self.update_slip_stake(page, stake)

        return PlacementResult(status="prepped", bet_id=bet_id)

    async def _click_market_btn(self, page: Page, market: str, outcome: str) -> bool:
        """Click the button.market-btn matching the given market and outcome.

        Strategy: scan button.market-btn elements, walk up to find the parent
        market section label (e.g. "Money Line"), then pick by visual position
        (home=0, draw=1 for 1x2, away=last; over=0, under=1 for totals).
        """
        try:
            # Resolve canonical market type from label aliases
            canon_market = _MARKET_LABEL_MAP.get(market, market)
            position_map = _OUTCOME_POSITION.get(canon_market) or _OUTCOME_POSITION.get("moneyline", {})
            target_pos = position_map.get(outcome)
            if target_pos is None:
                logger.warning(f"[{self.provider_id}] Unknown outcome {outcome!r} for market {canon_market!r}")
                return False

            # Evaluate in-page: group buttons by market section, return the
            # index of the target button within the full button NodeList so
            # Python can click it by index.
            js = """
            (([market, outcome, pos]) => {
                const allBtns = Array.from(document.querySelectorAll('button.market-btn'));
                if (!allBtns.length) return -1;

                // Try to group by parent market section first
                // Walk up from each button to find a parent element whose text
                // contains the market label. Group consecutive buttons under
                // the same market header.
                const groups = [];
                let currentGroup = null;
                let currentHeader = null;

                for (const btn of allBtns) {
                    // Walk up to find a market-section container that has a label
                    let el = btn.parentElement;
                    let foundHeader = null;
                    for (let i = 0; i < 10 && el; i++) {
                        const t = el.textContent || "";
                        // Look for known market label keywords
                        const lower = t.toLowerCase();
                        if (lower.includes("money line") || lower.includes("1x2") ||
                            lower.includes("spread") || lower.includes("handicap") ||
                            lower.includes("total") || lower.includes("over/under")) {
                            foundHeader = t.toLowerCase();
                            break;
                        }
                        el = el.parentElement;
                    }
                    if (foundHeader !== currentHeader) {
                        currentGroup = { header: foundHeader, btns: [] };
                        groups.push(currentGroup);
                        currentHeader = foundHeader;
                    }
                    if (currentGroup) {
                        currentGroup.btns.push(btn);
                    }
                }

                // Find the group matching the target market
                const marketLower = market.toLowerCase();
                let targetGroup = null;
                for (const g of groups) {
                    const h = g.header || "";
                    if (h.includes(marketLower) ||
                        (marketLower === "moneyline" && h.includes("money line")) ||
                        (marketLower === "1x2" && h.includes("1x2")) ||
                        (marketLower === "spread" && (h.includes("spread") || h.includes("handicap"))) ||
                        (marketLower === "total" && (h.includes("total") || h.includes("over")))) {
                        targetGroup = g;
                        break;
                    }
                }

                if (!targetGroup) {
                    return -2;  // market not found
                }

                if (pos >= targetGroup.btns.length) return -3;  // pos out of range
                return allBtns.indexOf(targetGroup.btns[pos]);
            })
            """

            idx = await page.evaluate(js, [market, outcome, target_pos])
            if idx is None or idx < 0:
                logger.warning(
                    f"[{self.provider_id}] _click_market_btn: btn lookup returned {idx} "
                    f"(market={market!r} outcome={outcome!r} pos={target_pos})"
                )
                return False

            # Click by evaluating a direct click on the nth button
            await page.evaluate(f"() => document.querySelectorAll('button.market-btn')[{idx}].click()")
            logger.info(f"[{self.provider_id}] Clicked market-btn[{idx}] for {market}/{outcome}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] _click_market_btn failed: {e}")
            return False

    async def read_slip_odds(self, page: Page) -> float | None:
        """Read the current American price from localStorage["Main:Betslip"] and
        convert to decimal.

        Called ~1Hz by SlipOddsStream while a counter slip is open.
        """
        try:
            price = await page.evaluate(
                """() => {
                    const raw = localStorage.getItem("Main:Betslip");
                    if (!raw) return null;
                    try {
                        const data = JSON.parse(raw);
                        const sels = data?.Selections ?? [];
                        if (sels.length === 0) return null;
                        return sels[0].price;
                    } catch { return null; }
                }"""
            )
            if price is None:
                return None
            return american_to_decimal(float(price))
        except Exception:
            return None

    async def update_slip_stake(self, page: Page, stake: float) -> bool:
        """Write the stake to the Pinnacle slip's React-controlled input.

        Uses the HTMLInputElement.prototype.value hidden setter pattern — verified
        in browser during live discovery (discovery doc §Stake input).
        """
        try:
            result = await page.evaluate(
                """((stake) => {
                    const el = document.querySelector('input[placeholder="Stake"]');
                    if (!el) return false;
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(el, String(stake));
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                })""",
                stake,
            )
            return bool(result)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Placement — mirror; user clicks "CONFIRM" on site; we intercept XHR
    # ------------------------------------------------------------------

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Pinnacle mirror: prep betslip + return manual so user clicks CONFIRM."""
        prep = await self.prep_betslip(page, bet, stake)
        if prep.status != "prepped":
            return prep
        return PlacementResult(
            status="manual",
            bet_id=prep.bet_id,
            reason="user_confirms_on_site",
        )

    async def confirm_bet(self, page: Page) -> PlacementResult:
        """Phase 2 is user-triggered — just return manual."""
        return PlacementResult(status="manual", bet_id=0, reason="user_confirms_on_site")

    # ------------------------------------------------------------------
    # Placement response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_placement_status(body: dict) -> dict:
        """Infer success/failure from placement XHR response.

        Shape inferred from /0.1/bets/straight/quote analog — not yet observed
        from a real placement.

        # TODO(pinnacle-place-shape): validate exact response shape on first
        # real placement and tighten error extraction if needed.
        """
        if body.get("wagerNumber") or body.get("betId"):
            return {"success": True, "error": None, "max_stake": None}
        # Failure path — try to extract a max-stake hint from any of the known keys / shapes.
        max_stake = body.get("maxStake") or body.get("max_stake") or body.get("maximumStake")
        if max_stake is None:
            # /quote-style limits array: [{"amount": X, "type": "maxRiskStake"}, ...]
            for limit in body.get("limits") or []:
                if limit.get("type") == "maxRiskStake":
                    max_stake = limit.get("amount")
                    break
        return {
            "success": False,
            "error": body.get("error") or body.get("errorCode") or "unknown",
            "max_stake": max_stake,
        }

    @staticmethod
    def parse_placement_response(body: dict) -> str | None:
        """Extract provider_bet_id from Pinnacle placement response.

        Tries wagerNumber first (inferred primary), then betId.
        """
        bid = body.get("wagerNumber") or body.get("betId")
        return str(bid) if bid else None


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _slugify(s: str) -> str:
    """Pinnacle URL slug: lowercase, dehyphenated, accent-stripped."""
    if not s:
        return ""
    out = s.translate(_ACCENT_TABLE).lower()
    return "-".join(out.split())
