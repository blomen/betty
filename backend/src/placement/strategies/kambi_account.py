"""
KambiAccountStrategy — DOM scraper for Kambi-powered sportsbooks.

Reads balance from the header and scrapes settled bets from the bet history
page (/betting/sports/bethistory). Used by AccountSyncService to auto-settle
bets and sync balances.

Swedish locale:
  - "Vinst" = won, "Förlust" = lost
  - "Insats: 100,00 kr" = stake 100
  - "Utbetalning: 235,00 kr" = payout 235
  - "Kupong-id: 12345678" = coupon ID
  - "Gratisspel" = freebet
  - "Saldo 1 100,00 kr" = balance 1100
"""

import logging
import re
from dataclasses import dataclass, field

try:
    from patchright.async_api import Page
except ImportError:
    from playwright.async_api import Page

from ..url_builder import PROVIDER_MY_BETS_URLS

logger = logging.getLogger(__name__)


def _parse_swedish_number(text: str) -> float:
    """Parse Swedish-formatted number: '1 100,00' → 1100.0"""
    cleaned = text.replace("\xa0", " ").strip()
    cleaned = re.sub(r"\s+", "", cleaned)  # Remove spaces (thousand sep)
    cleaned = cleaned.replace(",", ".")     # Decimal comma → dot
    return float(cleaned)


@dataclass
class ScrapedBet:
    """A single bet scraped from the provider's bet history."""
    result: str              # "won" | "lost" | "void"
    stake: float
    payout: float
    odds: float
    is_freebet: bool
    event_text: str          # Raw event name text (e.g. "Anaheim Ducks - Calgary Flames")
    coupon_id: str = ""


@dataclass
class AccountSyncResult:
    """Result of scraping a provider's account page."""
    provider_id: str
    balance: float | None = None
    bonus_balance: float | None = None
    scraped_bets: list[ScrapedBet] = field(default_factory=list)
    error: str = ""


class KambiAccountStrategy:
    """Kambi-specific account sync: balance reading + settled bet scraping."""

    async def sync(self, page: Page, provider_id: str) -> AccountSyncResult:
        """Read balance and scrape settled bets from Kambi bet history."""
        result = AccountSyncResult(provider_id=provider_id)

        # 1. Read balance from header (available on any page)
        try:
            result.balance = await self._read_balance(page)
            if result.balance is not None:
                logger.info(f"[KambiAccount] Balance: {result.balance:.2f} kr")
        except Exception as e:
            logger.warning(f"[KambiAccount] Failed to read balance: {e}")

        # 2. Read bonus balance
        try:
            result.bonus_balance = await self._read_bonus_balance(page)
            if result.bonus_balance is not None:
                logger.info(f"[KambiAccount] Bonus balance: {result.bonus_balance:.2f} kr")
        except Exception:
            pass

        # 3. Navigate to bet history
        my_bets_url = PROVIDER_MY_BETS_URLS.get(provider_id)
        if not my_bets_url:
            result.error = f"No bet history URL configured for {provider_id}"
            return result

        try:
            await page.goto(my_bets_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)  # Wait for Kambi widget
        except Exception as e:
            result.error = f"Failed to navigate to bet history: {e}"
            return result

        # 4. Click "Avgjorda" (Settled) tab
        try:
            await self._click_settled_tab(page)
        except Exception as e:
            result.error = f"Failed to click settled tab: {e}"
            return result

        # 5. Scrape settled bets
        try:
            result.scraped_bets = await self._scrape_settled_bets(page)
            logger.info(f"[KambiAccount] Scraped {len(result.scraped_bets)} settled bets")
        except Exception as e:
            logger.error(f"[KambiAccount] Scrape error: {e}", exc_info=True)
            result.error = f"Failed to scrape bets: {e}"

        return result

    async def _read_balance(self, page: Page) -> float | None:
        """Read main balance from header: 'Saldo X XXX,XX kr'."""
        try:
            content = await page.content()
            # Match "Saldo" followed by Swedish number and "kr"
            match = re.search(r"Saldo\s+([\d\s]+,\d+)\s*kr", content)
            if match:
                return _parse_swedish_number(match.group(1))

            # Try aria-label or text content approach
            balance_el = page.locator("text=/Saldo/")
            if await balance_el.count() > 0:
                text = await balance_el.first.text_content()
                if text:
                    match = re.search(r"([\d\s]+,\d+)", text)
                    if match:
                        return _parse_swedish_number(match.group(1))
        except Exception:
            pass
        return None

    async def _read_bonus_balance(self, page: Page) -> float | None:
        """Read bonus balance: 'Bonussaldo X XXX,XX kr'."""
        try:
            content = await page.content()
            match = re.search(r"Bonussaldo\s+([\d\s]+,\d+)\s*kr", content)
            if match:
                return _parse_swedish_number(match.group(1))
        except Exception:
            pass
        return None

    async def _click_settled_tab(self, page: Page) -> None:
        """Click the 'Avgjorda' (Settled) tab in Kambi bet history widget."""
        # Try role-based selector first
        tab = page.get_by_role("tab", name="Avgjorda")
        try:
            if await tab.is_visible(timeout=5000):
                await tab.click()
                await page.wait_for_timeout(2000)  # Wait for content to load
                return
        except Exception:
            pass

        # Fallback: text-based
        tab = page.locator("text=Avgjorda").first
        try:
            if await tab.is_visible(timeout=3000):
                await tab.click()
                await page.wait_for_timeout(2000)
                return
        except Exception:
            pass

        logger.warning("[KambiAccount] Could not find 'Avgjorda' tab — may already be on settled view")

    async def _scrape_settled_bets(self, page: Page) -> list[ScrapedBet]:
        """Scrape all visible settled bets from the Kambi bet history."""
        bets = []

        # Kambi bet history entries contain "Kupong-id" text
        # Each bet is a collapsible section with result, stake, payout, odds
        entries = page.locator("[class*='bethistory'] [class*='coupon'], [class*='bet-history'] [class*='coupon']")
        count = await entries.count()

        if count == 0:
            # Fallback: look for any element containing "Kupong-id"
            entries = page.locator("text=/Kupong-id/").locator("..")
            count = await entries.count()

        if count == 0:
            # Try broader: get all text content and parse
            return await self._scrape_settled_bets_fulltext(page)

        for i in range(count):
            try:
                entry = entries.nth(i)
                text = await entry.text_content() or ""
                bet = self._parse_bet_text(text)
                if bet:
                    bets.append(bet)
            except Exception as e:
                logger.debug(f"[KambiAccount] Failed to parse entry {i}: {e}")

        return bets

    async def _scrape_settled_bets_fulltext(self, page: Page) -> list[ScrapedBet]:
        """Fallback: scrape by getting full page text and splitting on coupon markers."""
        try:
            # Get all text from the Kambi widget area
            content = await page.content()

            # Split on coupon ID markers
            sections = re.split(r"(?=Kupong-id:\s*\d+)", content)
            bets = []

            for section in sections:
                if "Kupong-id:" not in section:
                    continue
                # Extract visible text (strip HTML)
                text = re.sub(r"<[^>]+>", " ", section)
                text = re.sub(r"\s+", " ", text).strip()
                bet = self._parse_bet_text(text)
                if bet:
                    bets.append(bet)

            return bets
        except Exception as e:
            logger.error(f"[KambiAccount] Fulltext scrape failed: {e}")
            return []

    def _parse_bet_text(self, text: str) -> ScrapedBet | None:
        """Parse a bet entry's text content into a ScrapedBet."""
        if not text:
            return None

        # Result: "Vinst" (won) or "Förlust" (lost)
        result = "unknown"
        text_upper = text.upper()
        if "VINST" in text_upper:
            result = "won"
        elif "FÖRLUST" in text_upper:
            result = "lost"
        elif "ÅTERBETALNING" in text_upper or "VOID" in text_upper:
            result = "void"

        if result == "unknown":
            return None

        # Odds: "@ 2,35" or "@ 2.35"
        odds = 0.0
        odds_match = re.search(r"@\s*([\d]+[.,]\d+)", text)
        if odds_match:
            odds = _parse_swedish_number(odds_match.group(1))

        # Stake: "Insats: 100,00 kr" or "Insats: 1 000,00 kr"
        stake = 0.0
        stake_match = re.search(r"Insats:?\s*([\d\s]+,\d+)\s*kr", text)
        if stake_match:
            stake = _parse_swedish_number(stake_match.group(1))

        # Payout: "Utbetalning: 235,00 kr"
        payout = 0.0
        payout_match = re.search(r"Utbetalning:?\s*([\d\s]+,\d+)\s*kr", text)
        if payout_match:
            payout = _parse_swedish_number(payout_match.group(1))

        # Coupon ID
        coupon_id = ""
        cid_match = re.search(r"Kupong-id:?\s*(\d+)", text)
        if cid_match:
            coupon_id = cid_match.group(1)

        # Freebet indicator
        is_freebet = "gratisspel" in text.lower() or "free bet" in text.lower()

        # Event text: try to extract team names
        # Usually the line before the odds, like "Anaheim Ducks - Calgary Flames"
        event_text = ""
        # Look for "Team A - Team B" pattern (with various dash types)
        event_match = re.search(r"([A-ZÀ-Ö][\w\s.]+?)\s*[-–—]\s*([A-ZÀ-Ö][\w\s.]+?)(?:\s*@|\s*Insats|\s*Vinst|\s*Förlust)", text)
        if event_match:
            event_text = f"{event_match.group(1).strip()} - {event_match.group(2).strip()}"

        return ScrapedBet(
            result=result,
            stake=stake,
            payout=payout,
            odds=odds,
            is_freebet=is_freebet,
            event_text=event_text,
            coupon_id=coupon_id,
        )
