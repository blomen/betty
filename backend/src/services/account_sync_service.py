"""
AccountSyncService — bet settlement and balance sync.

Supports two modes:
  1. Browser-based (Kambi etc): CDP Chrome → navigate → scrape DOM
  2. API-based (Polymarket): REST API call with wallet address — no browser

Both modes produce an AccountSyncResult which is processed through the same
matching/settlement pipeline.
"""

import logging
from typing import Optional

try:
    from patchright.async_api import async_playwright, Page
except ImportError:
    from playwright.async_api import async_playwright, Page

from sqlalchemy.orm import Session

from ..constants import PLATFORM_MAP
from ..db.models import Bet, Event, Odds
from ..placement.slip_filler import dismiss_cookie_banner, ensure_logged_in
from ..placement.url_builder import PROVIDER_LANDING_URLS
from ..recorder.chrome_launcher import get_chrome_launcher
from ..repositories import BetRepo, ProfileRepo
from ..services.bet_service import BetService

logger = logging.getLogger(__name__)


def _normalize_team(name: str) -> str:
    """Normalize a team name for fuzzy comparison."""
    return (
        name.lower()
        .replace(".", "")
        .replace("-", " ")
        .replace("  ", " ")
        .strip()
    )


class AccountSyncService:
    """Orchestrates account sync: settle bets + update balance."""

    def __init__(self, db: Session):
        self.db = db
        self._strategies: dict[str, object] = {}
        self.profile_repo = ProfileRepo(db)
        self.bet_repo = BetRepo(db)
        self.bet_service = BetService(db)

    def register_strategy(self, platform: str, strategy: object):
        """Register a platform-specific sync strategy."""
        self._strategies[platform] = strategy

    async def sync_provider(self, provider_id: str) -> dict:
        """
        Sync a provider's bets and balance.

        Automatically chooses between browser-based (CDP) and API-based sync
        based on the strategy's `requires_browser` flag.

        Returns dict with:
          - settled_count, settled_bets[], unmatched[]
          - balance, balance_updated
          - pending_remaining
          - error (if any)
        """
        profile = self.profile_repo.get_active()

        # 1. Resolve platform and strategy
        platform = PLATFORM_MAP.get(provider_id, "")
        strategy = self._strategies.get(platform)
        if not strategy:
            return {"error": f"No sync strategy for platform '{platform}' (provider: {provider_id})"}

        # 2. Route to API-based or browser-based sync
        if getattr(strategy, 'requires_browser', True) is False:
            return await self._sync_api(strategy, provider_id, profile)
        else:
            return await self._sync_browser(strategy, provider_id, profile)

    # ──────────────────── API-based sync (Polymarket) ────────────────────

    async def _sync_api(self, strategy, provider_id: str, profile) -> dict:
        """Sync via REST API — no browser needed."""
        try:
            sync_result = await strategy.sync(page=None, provider_id=provider_id)

            if sync_result.error:
                logger.warning(f"[AccountSync] API strategy error: {sync_result.error}")

            result = self._process_sync_result(sync_result, provider_id, profile, source="api_sync")
            self.db.commit()
            return result

        except Exception as e:
            logger.error(f"[AccountSync] API sync error: {e}", exc_info=True)
            self.db.rollback()
            return {"error": f"Sync failed: {e}"}

    # ──────────────────── Browser-based sync (Kambi etc) ────────────────────

    async def _sync_browser(self, strategy, provider_id: str, profile) -> dict:
        """Sync via CDP Chrome — navigate, scrape DOM."""
        chrome = get_chrome_launcher()

        # Auto-start CDP Chrome if not running
        if not await chrome._is_cdp_available():
            logger.info("CDP Chrome not running — auto-starting...")
            started = await chrome.start()
            if not started:
                return {"error": "Failed to auto-start CDP Chrome"}

        # Connect to CDP Chrome via Playwright
        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.connect_over_cdp(chrome.cdp_url)
        except Exception as e:
            if playwright:
                try:
                    await playwright.stop()
                except Exception:
                    pass
            return {"error": f"CDP connect failed: {e}"}

        try:
            # Open a new tab
            context = browser.contexts[0]
            page = await context.new_page()

            # Navigate to landing and ensure logged in
            landing = PROVIDER_LANDING_URLS.get(provider_id)
            if not landing:
                return {"error": f"No landing URL for {provider_id}"}

            await page.goto(landing, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)

            await dismiss_cookie_banner(page)
            logged_in = await ensure_logged_in(page, landing)
            if not logged_in:
                return {
                    "error": "Not logged in — BankID authentication required. Open Chrome and log in manually.",
                }

            # Execute the platform strategy
            sync_result = await strategy.sync(page, provider_id)

            if sync_result.error:
                logger.warning(f"[AccountSync] Strategy error: {sync_result.error}")

            result = self._process_sync_result(sync_result, provider_id, profile, source="browser_sync")
            self.db.commit()
            return result

        except Exception as e:
            logger.error(f"[AccountSync] Error: {e}", exc_info=True)
            self.db.rollback()
            return {"error": f"Sync failed: {e}"}

        finally:
            # Disconnect Playwright handle — Chrome tab stays open for user
            try:
                if browser:
                    await browser.close()
            except Exception:
                pass
            try:
                if playwright:
                    await playwright.stop()
            except Exception:
                pass

    # ──────────────────── Shared settlement pipeline ────────────────────

    def _process_sync_result(self, sync_result, provider_id: str, profile, source: str = "browser_sync") -> dict:
        """
        Process an AccountSyncResult: match bets, settle, update balance.

        Shared by both API and browser sync paths.
        """
        pending_bets = self.bet_repo.get_pending_for_provider(provider_id, profile.id)
        settled_bets = []
        unmatched = []

        for scraped in sync_result.scraped_bets:
            match = self._find_best_match(scraped, pending_bets)
            if match:
                # Settle the matched bet
                settle_result = self.bet_service.settle_bet(
                    bet_id=match.id,
                    result=scraped.result,
                    payout=scraped.payout,
                )
                # Set settlement source and confirmation ID
                match.settlement_source = source
                if scraped.coupon_id:
                    match.confirmation_id = scraped.coupon_id

                settled_bets.append({
                    "bet_id": match.id,
                    "result": scraped.result,
                    "payout": scraped.payout,
                    "odds": scraped.odds,
                    "coupon_id": scraped.coupon_id,
                    "event_text": scraped.event_text,
                    "profit": settle_result.get("profit"),
                })
                # Remove from pending so it can't match again
                pending_bets = [b for b in pending_bets if b.id != match.id]
                logger.info(
                    f"[AccountSync] Settled bet #{match.id}: {scraped.result} "
                    f"(payout={scraped.payout}, coupon={scraped.coupon_id})"
                )
            else:
                unmatched.append({
                    "result": scraped.result,
                    "odds": scraped.odds,
                    "stake": scraped.stake,
                    "payout": scraped.payout,
                    "event_text": scraped.event_text,
                    "coupon_id": scraped.coupon_id,
                })

        # Update balance (absolute set — no race conditions)
        balance_updated = False
        if sync_result.balance is not None:
            self.profile_repo.set_balance(profile.id, provider_id, sync_result.balance)
            balance_updated = True
            logger.info(f"[AccountSync] Balance updated: {sync_result.balance:.2f}")

        return {
            "success": True,
            "provider_id": provider_id,
            "settled_count": len(settled_bets),
            "settled_bets": settled_bets,
            "unmatched": unmatched,
            "balance": sync_result.balance,
            "bonus_balance": sync_result.bonus_balance,
            "balance_updated": balance_updated,
            "pending_remaining": len(pending_bets),
            "error": sync_result.error or None,
        }

    def _find_best_match(self, scraped, pending_bets: list[Bet]) -> Optional[Bet]:
        """
        Find the best matching pending bet for a scraped bet using scoring.

        Scoring:
          - Coupon/condition ID match: +100 pts (instant match for Polymarket)
          - Odds match (±0.015): +40 pts
          - Stake match (±1.5 kr): +30 pts
          - Freebet flag match: +10 pts
          - Team name match (substring): +20 pts

        Threshold: ≥70 pts = confident match.
        """
        best_match = None
        best_score = 0

        for bet in pending_bets:
            score = 0

            # Direct condition_id match via Odds.clob_token_id (Polymarket)
            if scraped.coupon_id and bet.event_id:
                odds_row = self.db.query(Odds).filter(
                    Odds.event_id == bet.event_id,
                    Odds.provider_id == bet.provider_id,
                    Odds.outcome == bet.outcome,
                ).first()
                if odds_row and odds_row.clob_token_id:
                    # condition_id is parent of clob_token_id
                    if scraped.coupon_id == odds_row.clob_token_id or scraped.coupon_id in (odds_row.provider_meta or {}):
                        score += 100

            # Odds match (±0.015)
            if scraped.odds > 0 and bet.odds > 0:
                if abs(scraped.odds - bet.odds) <= 0.015:
                    score += 40

            # Stake match (±1.5 kr)
            if scraped.stake > 0 and bet.stake > 0:
                if abs(scraped.stake - bet.stake) <= 1.5:
                    score += 30

            # Freebet flag match
            if scraped.is_freebet == (bet.is_bonus or False):
                score += 10

            # Team name fuzzy match
            if scraped.event_text and bet.event_id:
                event = self.db.query(Event).filter(Event.id == bet.event_id).first()
                if event:
                    scraped_norm = _normalize_team(scraped.event_text)
                    home_norm = _normalize_team(event.home_team or "")
                    away_norm = _normalize_team(event.away_team or "")
                    if home_norm and home_norm in scraped_norm:
                        score += 10
                    if away_norm and away_norm in scraped_norm:
                        score += 10

            if score > best_score:
                best_score = score
                best_match = bet

        if best_score >= 70:
            return best_match

        return None
