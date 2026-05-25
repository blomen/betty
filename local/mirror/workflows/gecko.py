"""GeckoWorkflow — API-based balance for Gecko V2 platform providers.

Covers: spelklubben, betsson, betsafe, nordicbet, bethard.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Betting page path per provider (default: /sv/odds for betsson/betsafe/nordicbet)
_INIT_PATHS: dict[str, str] = {
    "spelklubben": "/sv/betting",
    "bethard": "/sv/sports",
}

# API base URLs per provider — OBG providers use different API domains
# Format: cloud-api.{domain} for most, but some use {brand}playground.net
_API_BASES_OVERRIDE: dict[str, list[str]] = {
    "spelklubben": [
        "https://cloud-api.spelklubben.se",
        "https://d-cf.spelklubbenplayground.net",
    ],
    "bethard": [
        "https://cloud-api.bethard.com",
        "https://d-cf.bethardplayground.net",
    ],
}


def _api_bases(provider_id: str, domain: str) -> list[str]:
    """Return API base URLs to try, in priority order."""
    override = _API_BASES_OVERRIDE.get(provider_id)
    if override:
        return override
    return [f"https://cloud-api.{domain}"]


def _wallets_urls(provider_id: str, domain: str) -> list[str]:
    """Return wallets API URLs to try, in priority order."""
    return [f"{base}/wallets" for base in _api_bases(provider_id, domain)]


class GeckoWorkflow(ProviderWorkflow):
    platform = "gecko_v2"

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def _fetch_wallets(self, page: Page) -> dict | None:
        """Try wallets API URLs until one works."""
        for url in _wallets_urls(self.provider_id, self.domain):
            result = await self._evaluate_api(page, url)
            if result and "__error" not in result:
                return result
        return None

    async def check_login(self, page: Page) -> bool:
        """Check login via Gecko wallets API — must have actual balance data."""
        result = await self._fetch_wallets(page)
        if result is None:
            return False
        try:
            # Try SEK first (Swedish providers), then any currency
            for currency in ("SEK", "EUR", "USD"):
                try:
                    float(result["Balances"][currency]["Real"]["Balance"])
                    return True
                except (KeyError, TypeError, ValueError):
                    continue
            # Try first available currency
            balances = result.get("Balances", {})
            if isinstance(balances, dict):
                for _currency, wallet in balances.items():
                    if isinstance(wallet, dict) and "Real" in wallet:
                        float(wallet["Real"]["Balance"])
                        return True
        except (KeyError, TypeError, ValueError):
            pass
        return False

    async def sync_balance(self, page: Page) -> float:
        """Read balance from Gecko wallets API."""
        result = await self._fetch_wallets(page)
        if result is None:
            return -1
        try:
            # Try SEK first, then any currency
            for currency in ("SEK", "EUR", "USD"):
                try:
                    return float(result["Balances"][currency]["Real"]["Balance"])
                except (KeyError, TypeError, ValueError):
                    continue
            # Try first available currency
            balances = result.get("Balances", {})
            if isinstance(balances, dict):
                for _currency, wallet in balances.items():
                    if isinstance(wallet, dict) and "Real" in wallet:
                        try:
                            return float(wallet["Real"]["Balance"])
                        except (TypeError, ValueError):
                            continue
        except (KeyError, TypeError, ValueError):
            pass
        logger.warning(f"[{self.provider_id}] Unexpected wallets response: {result}")
        return -1

    # ------------------------------------------------------------------
    # History / navigation / placement — interceptor handles
    # ------------------------------------------------------------------

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Fetch bet history by visiting the spelhistorik views and reading the
        coupon-history response the page's iframe makes itself.

        Why we can't just re-issue the API call from Python: several Gecko V2
        tenants (Spelklubben on `cloud-api.spelklubben.se`) ship a GTM
        tracker.js that monkey-patches `window.fetch` and rejects our
        page.evaluate-issued requests. `page.context.request` also gets a
        401/400 because the auth token lives only in localStorage and is
        injected as a header at request time by the in-page JS. So the
        only call that succeeds is the one the page makes itself — and
        the browser interceptor stashes the response body on
        `provider_data[pid]["coupon_history_raw"]`. We navigate both
        views to ensure both Open + Settled bodies have been intercepted
        recently, then read them back.
        """
        all_entries: list[HistoryEntry] = []

        # Passive: don't auto-navigate. The page's own iframe calls
        # coupon-history whenever the user lands on /spelhistorik, and the
        # browser interceptor caches every body under coupon_history_by_url.
        # If the user has been on history at all this session, we have
        # something to parse — otherwise this is a no-op and we'll catch it
        # next 60s tick after they navigate themselves. Mirror only auto-navs
        # for arb event clicks.
        from ..browser import get_active_browser

        browser = get_active_browser()
        by_url: dict = {}
        if browser is not None:
            pdata = browser.provider_data.get(self.provider_id) or {}
            by_url = pdata.get("coupon_history_by_url") or {}
            # Back-compat: pre-by_url cache schema only kept the latest body.
            if not by_url and pdata.get("coupon_history_raw"):
                by_url = {"latest": pdata["coupon_history_raw"]}
        logger.debug(f"[{self.provider_id}] sync_history: {len(by_url)} cached coupon-history bodies")
        seen_coupon_ids: set[str] = set()
        for _u, body in by_url.items():
            for e in self._parse_coupon_history(body):
                cid = e.provider_bet_id or ""
                if cid and cid in seen_coupon_ids:
                    continue
                if cid:
                    seen_coupon_ids.add(cid)
                all_entries.append(e)

        logger.info(f"[{self.provider_id}] sync_history: {len(all_entries)} bets total (passive cache read)")
        return all_entries

    def _parse_coupon_history(self, data: dict) -> list[HistoryEntry]:
        """Parse Gecko V2 coupon-history response into HistoryEntry list."""
        coupons = data.get("data", {}).get("coupons", [])
        if not coupons:
            return []

        status_map = {
            "won": "won",
            "lost": "lost",
            "void": "void",
            "cancelled": "void",
            "cashedout": "cashout",
            "cashedOut": "cashout",
            "open": "pending",
            "pending": "pending",
        }

        entries: list[HistoryEntry] = []
        for coupon in coupons:
            try:
                # Resolve status, in priority order:
                # 1) betsStatus dict ({"won": N}, {"lost": N}, {"void": N}, etc.)
                #    is the most reliable signal — it's set the moment any leg
                #    is graded.
                # 2) couponStatus string ("won"/"lost"/"open"/...) for older
                #    tenants that still send it.
                # 3) status field ("Open"/"Closed"/...) — Closed implies settled
                #    but the actual win/loss has to come from betsStatus.
                # Verbatim spelklubben payload: status="Closed",
                # couponStatus=null, betsStatus={"lost": 1}. Earlier code did
                # `coupon.get("couponStatus", "open").lower()` which crashed
                # on the explicit null and made every coupon silently drop.
                bets_status = coupon.get("betsStatus") or {}
                raw_status = None
                # Use first non-zero bets_status key as the result if there is one.
                for key in ("won", "lost", "void", "cancelled", "cashedOut", "cashedout"):
                    if any(k.lower() == key.lower() for k in bets_status.keys()):
                        raw_status = key.lower()
                        break
                if raw_status is None:
                    cs = coupon.get("couponStatus")
                    if isinstance(cs, str) and cs:
                        raw_status = cs.lower()
                if raw_status is None:
                    top_status = coupon.get("status")
                    if isinstance(top_status, str):
                        # "Open"/"Pending"/"Active" → pending; "Closed"/"Settled" → fall through to betsStatus
                        if top_status.lower() in ("open", "pending", "active"):
                            raw_status = "pending"
                if raw_status is None:
                    raw_status = "pending"  # last-resort default
                mapped = status_map.get(raw_status, "pending")

                event_names = coupon.get("eventNames", [])
                event_name = event_names[0].replace(" - ", " vs ") if event_names else ""

                odds = float(coupon.get("totalOdds", 0))
                stake = float(coupon.get("stake", 0))
                payout = float(coupon.get("totalPayout", 0)) if mapped != "pending" else None

                coupon_id = str(coupon.get("couponId") or coupon.get("id") or "")

                # Try to extract market/outcome from selections
                selections = coupon.get("selections", coupon.get("legs", []))
                outcome = ""
                market = ""
                if isinstance(selections, list) and selections:
                    sel = selections[0]
                    outcome = sel.get("outcomeLabel", sel.get("selectionName", ""))
                    market = sel.get("marketName", sel.get("marketTemplate", ""))

                entries.append(
                    HistoryEntry(
                        provider_bet_id=coupon_id,
                        event_name=event_name,
                        market=market,
                        outcome=outcome,
                        odds=odds,
                        stake=stake,
                        status=mapped,
                        payout=payout,
                    )
                )
            except Exception as e:
                logger.debug(f"[{self.provider_id}] _parse_coupon_history: skipped coupon: {e}")

        logger.info(f"[{self.provider_id}] sync_history: {len(entries)} bets from coupon-history API")
        return entries

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate to Gecko V2 event page using gecko_event_id from provider_meta.

        URL pattern: {site_url}{init_path}?eventId={gecko_event_id}
        Verified: the main site passes eventId to the sportsbook iframe automatically.
        """
        gecko_eid = getattr(bet, "gecko_event_id", "")
        if not gecko_eid:
            logger.info(f"[{self.provider_id}] No gecko_event_id — user navigates manually")
            return True

        if f"eventId={gecko_eid}" in (page.url or "") or f"eventId=f-{gecko_eid}" in (page.url or ""):
            return True  # Already on this event

        init_path = _INIT_PATHS.get(self.provider_id, "/sv/odds")
        # Event IDs from the Gecko API already include the f- prefix
        eid_param = gecko_eid if gecko_eid.startswith("f-") else f"f-{gecko_eid}"
        url = f"https://www.{self.domain}{init_path}?eventId={eid_param}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1)
            logger.info(f"[{self.provider_id}] Navigated to event {gecko_eid}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate_to_event failed: {e}")
            return False

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Manual placement — user places via provider UI."""
        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="manual_placement",
        )
