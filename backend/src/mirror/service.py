"""MirrorService — orchestrates bet interception, parsing, storage, and notification."""

import asyncio
import contextlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ..db.models import Bet, BetTrace, get_session
from ..services.bet_service import BetService
from .event_router import EventRouter
from .interceptor import BetInterceptor
from .parsers.gecko import GeckoBetParser
from .parsers.polymarket import PolymarketParser
from .recipes import NotificationRecipe, load_recipes, save_recipes

logger = logging.getLogger(__name__)


class MirrorService:
    """Coordinates BetInterceptor + parsing + BetService + Broadcaster."""

    def __init__(self, broadcaster=None, provider_id: str | None = None, discovery: bool = True):
        self.broadcaster = broadcaster
        self.provider_id = provider_id
        self.discovery = discovery
        self.parser = GeckoBetParser()
        self.polymarket_parser = PolymarketParser()
        # Cache: gecko_event_id → {home_team, away_team, event_name}
        self._event_cache: dict[str, dict[str, str]] = {}
        # Pending settlements awaiting confirmation
        self._pending_settlements: list[dict] = []
        # Notification mute recipes
        self._recipes_dir: Path | None = None  # Set in tests; defaults to data dir
        self._recipes: list[NotificationRecipe] = []
        self._muted_providers: set[str] = set()
        self._load_notification_recipes()
        # Persistent tabs for Polymarket live edge: {slug: page}
        self._poly_tabs: dict[str, any] = {}
        # Providers confirmed logged in (balance API returned 200)
        self._logged_in_providers: set[str] = set()
        self.event_router = EventRouter()
        self.interceptor = BetInterceptor(
            on_bet_response=self._handle_bet_response,
            on_event_data=self._handle_event_data,
            on_bet_history=self._handle_bet_history,
            on_financial_data=self._handle_financial_data,
            on_provider_detected=self._handle_provider_detected,
            on_notification_settings=self._handle_notification_settings,
            on_page_navigated=self._handle_page_navigated,
        )

    async def start(self, site_url: str | None = None):
        """Start the mirror browser."""
        await self.interceptor.start()

    async def stop(self):
        """Stop the mirror browser."""
        await self.interceptor.stop()

    # Verified SSR bet history — need DOM scraping, not API interception
    # Only unibet confirmed; other Kambi operators may have XHR — verify before adding
    # Kambi providers — bet history is SSR (DOM scrape, not API interception)
    _SSR_PROVIDERS = frozenset(
        {
            "unibet",
            "leovegas",
            "expekt",
            "888sport",
            "speedybet",
            "x3000",
            "goldenbull",
            "1x2",
            "betmgm",
        }
    )

    async def _handle_provider_detected(self, provider_id: str):
        """Fires when user navigates to a known provider site.

        Only broadcasts provider_opened (amber) — NOT sync_available (green).
        sync_available fires later when login is confirmed:
        - Polymarket: DOM balance scrape
        - Soft providers: intercepted API response with balance
        """
        logger.info(f"[mirror] Provider opened: {provider_id}")
        self._notify("provider_opened", {"provider": provider_id})
        # Auto-mute notifications if we have a recipe
        await self._replay_notification_mute(provider_id)
        # Polymarket: scrape cash balance from DOM to verify login
        if provider_id == "polymarket":
            asyncio.ensure_future(self._scrape_polymarket_balance())
        # Auto-scrape bet history for SSR providers when pending bets exist
        if provider_id in self._SSR_PROVIDERS:
            info = await asyncio.to_thread(self._get_provider_sync_info, provider_id)
            if info["pending_bets"] > 0:
                asyncio.ensure_future(self._auto_scrape_bet_history(provider_id))
        # Pinnacle: auto-settle via API on detection (doesn't need interception)
        if provider_id == "pinnacle":
            info = await asyncio.to_thread(self._get_provider_sync_info, provider_id)
            if info["pending_bets"] > 0:
                logger.info(f"[mirror] Pinnacle detected with {info['pending_bets']} pending — auto-settling via DOM")
                asyncio.ensure_future(self._auto_settle_pinnacle())
        # Auto-discover for generic (unwired) providers with no intel
        from .workflows import get_workflow
        from .workflows.generic import GenericWorkflow

        wf = get_workflow(provider_id)
        if isinstance(wf, GenericWorkflow) and wf.intel is None:
            context = self.interceptor.context
            if context:
                page = await wf.find_tab(context)
                if page:
                    asyncio.ensure_future(self._run_auto_discovery(wf, page, provider_id))

    async def _run_auto_discovery(self, wf, page, provider_id: str):
        """Run auto-discovery in background, notify frontend when done."""
        await asyncio.sleep(3)  # Wait for page to settle
        success = await wf.auto_discover(page)
        if success:
            self._notify(
                "discovery_complete",
                {
                    "provider": provider_id,
                    "capabilities": wf.intel.get("capabilities", {}),
                },
            )

    async def _scrape_polymarket_balance(self):
        """Scrape USDC cash balance from Polymarket DOM.

        The cash balance is rendered client-side (not from a single API endpoint).
        The nav shows "Cash$101.51" — we extract the dollar amount from that text.
        """
        await asyncio.sleep(3)  # Wait for page to render

        context = self.interceptor.context
        if not context or not context.pages:
            return

        # Find the Polymarket page by URL — no fallback to other pages
        page = None
        for p in context.pages:
            if "polymarket.com" in (p.url or ""):
                page = p
                break
        if page is None:
            return  # No Polymarket tab open
        # Verify we're actually on Polymarket (page may have been reused)
        if "polymarket.com" not in (page.url or ""):
            return
        try:
            balance_text = await page.evaluate(
                "() => {"
                "  const els = document.querySelectorAll('button, a, span, div');"
                "  for (const el of els) {"
                "    const text = el.textContent || '';"
                "    const match = text.match(/Cash\\s*\\$([\\d,.]+)/);"
                "    if (match) return match[1];"
                "  }"
                "  return null;"
                "}"
            )
            if balance_text:
                balance = float(balance_text.replace(",", ""))
                logger.info(f"[mirror] Polymarket logged in — cash balance: ${balance}")
                self._logged_in_providers.add("polymarket")
                await asyncio.to_thread(self._sync_balance, "polymarket", balance)
                # Check for pending bets to populate the banner count
                info = await asyncio.to_thread(self._get_provider_sync_info, "polymarket")
                self._notify(
                    "sync_available",
                    {
                        "provider": "polymarket",
                        "balance": balance,
                        "pending_bets": info["pending_bets"],
                        "pending_stake": info["pending_stake"],
                    },
                )
                # Start periodic settle loop if pending bets exist
                if info["pending_bets"] > 0 and not getattr(self, "_poly_settle_task", None):
                    logger.info(
                        f"[mirror] Polymarket has {info['pending_bets']} pending — starting periodic settle loop"
                    )
                    self._poly_settle_task = asyncio.ensure_future(self._poly_settle_loop())
            else:
                logger.info("[mirror] Polymarket detected but not logged in (no cash balance in DOM)")
        except Exception as e:
            logger.warning(f"[mirror] Could not scrape Polymarket balance: {e}")

    async def _poly_settle_loop(self, interval: int = 300):
        """Periodic Polymarket settlement: positions page → claim → redeem → DB settle.

        Runs every 5 minutes while pending bets exist with passed start_time.
        Uses the PolymarketWorkflow.settle_all() which navigates to the Positions
        tab (not History), clicks Claim banner, clicks Redeem buttons, and settles
        matched bets in the database.
        """
        from datetime import datetime, timezone

        from .workflows.polymarket import PolymarketWorkflow

        await asyncio.sleep(10)  # Initial delay — let page fully load

        while True:
            try:
                # Check if any pending poly bets have passed start_time
                pending = await asyncio.to_thread(self._get_pending_poly_bets_sync)
                if not pending:
                    logger.info("[mirror:poly-settle] No pending Polymarket bets — stopping loop")
                    break

                now = datetime.now(timezone.utc)
                has_finished = False
                for pb in pending:
                    st = pb.get("start_time")
                    if st:
                        try:
                            if isinstance(st, str):
                                st = datetime.fromisoformat(st.replace("Z", "+00:00"))
                            if st.tzinfo is None:
                                st = st.replace(tzinfo=timezone.utc)
                            if st < now:
                                has_finished = True
                                break
                        except Exception:
                            pass

                if not has_finished:
                    logger.debug("[mirror:poly-settle] No finished pending bets yet — waiting")
                    await asyncio.sleep(interval)
                    continue

                # Find Polymarket page
                context = self.interceptor.context
                if not context or not context.pages:
                    logger.debug("[mirror:poly-settle] No browser context — waiting")
                    await asyncio.sleep(interval)
                    continue

                page = None
                for p in context.pages:
                    if "polymarket.com" in (p.url or ""):
                        page = p
                        break

                if not page:
                    logger.debug("[mirror:poly-settle] No Polymarket tab open — waiting")
                    await asyncio.sleep(interval)
                    continue

                # Scan positions (no clicks) and notify frontend for user confirmation
                logger.info(f"[mirror:poly-settle] Scanning portfolio ({len(pending)} pending bets)")
                workflow = PolymarketWorkflow(provider_id="polymarket", domain="polymarket.com")
                scan = await workflow.scan_portfolio_settlements(page)

                matches = scan.get("matches", [])
                has_claim = scan.get("has_claim")
                redeem_count = scan.get("redeem_count", 0)

                if not matches and not has_claim and redeem_count == 0:
                    # Nothing in positions — try history tab for full reconciliation
                    try:
                        history_entries = await workflow.sync_history(page)
                        if history_entries:
                            logger.info(f"[mirror:poly-settle] History sync settled {len(history_entries)} bets")
                    except Exception as he:
                        logger.debug(f"[mirror:poly-settle] History sync: {he}")
                    await asyncio.sleep(interval)
                    continue

                summary = scan.get("summary", {})
                logger.info(
                    f"[mirror:poly-settle] Found: {len(matches)} matches, claim={has_claim}, redeemable={redeem_count}"
                )

                # Send as settlements_pending with source field so frontend
                # shows the confirm/reject banner and routes confirm to settle-all
                self._notify(
                    "settlements_pending",
                    {
                        "source": "polymarket_portfolio",
                        "provider": "polymarket",
                        "count": len(matches),
                        "wins": summary.get("wins", 0),
                        "losses": summary.get("losses", 0),
                        "total_staked": summary.get("total_staked", 0),
                        "total_payout": summary.get("total_payout", 0),
                        "net": summary.get("net_pl", 0),
                        "has_claim": has_claim,
                        "redeem_count": redeem_count,
                        "settlements": matches,
                    },
                )

                if "error" in scan:
                    logger.warning(f"[mirror:poly-settle] Scan error: {scan['error']}")

            except asyncio.CancelledError:
                logger.info("[mirror:poly-settle] Loop cancelled")
                break
            except Exception as e:
                logger.error(f"[mirror:poly-settle] Unexpected error: {e}", exc_info=True)

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

        self._poly_settle_task = None

    async def _auto_settle_via_history(self, provider_id: str):
        """Generic settle-first: navigate provider's tab to bet history page.

        Works for any provider with a workflow that implements sync_history().
        The interceptor catches bet history API responses (widgetBetHistory,
        coupon-history, etc.) and stages settlements automatically.
        """
        if not hasattr(self, "_settle_checked"):
            self._settle_checked = set()

        await asyncio.sleep(4)  # Wait for page to fully load

        context = self.interceptor.context
        if not context or not context.pages:
            return

        try:
            from .workflows import get_workflow

            workflow = get_workflow(provider_id)
            page = await workflow.find_tab(context)
            if not page:
                logger.debug(f"[mirror] No tab found for {provider_id} — skipping auto-settle")
                return

            logger.info(f"[mirror] Auto-sync: navigating {provider_id} to bet history")
            await workflow.sync_history(page)
            self._settle_checked.add(provider_id)
            logger.info(f"[mirror] Auto-sync complete for {provider_id}")
        except Exception as e:
            logger.warning(f"[mirror] Auto-sync failed for {provider_id}: {e}")

    async def _handle_page_navigated(self, provider_id: str, url: str):
        """Fires on every page navigation to a known provider.

        Detects bet history pages and auto-scrapes for settlement:
        - Polymarket: /portfolio?tab=history
        - Kambi: /betting/sports/bethistory
        - Altenar/Gecko: bet history API responses handled by _handle_bet_history
        """
        # Polymarket portfolio — trigger settle via positions page
        if provider_id == "polymarket" and "/portfolio" in url:
            info = await asyncio.to_thread(self._get_provider_sync_info, "polymarket")
            if info["pending_bets"] > 0:
                logger.info(
                    f"[mirror] Polymarket portfolio detected — running settle_all for {info['pending_bets']} pending bets"
                )
                await asyncio.sleep(4)  # Wait for DOM render
                try:
                    from .workflows.polymarket import PolymarketWorkflow

                    context = self.interceptor.context
                    page = None
                    for p in context.pages:
                        if "polymarket.com" in (p.url or ""):
                            page = p
                            break
                    if page:
                        workflow = PolymarketWorkflow(provider_id="polymarket", domain="polymarket.com")
                        result = await workflow.settle_all(page)
                        logger.info(f"[mirror] Polymarket settle_all: settled={result.get('settled', 0)}")
                except Exception as e:
                    logger.warning(f"[mirror] Polymarket settle failed: {e}")
            return

        # Pinnacle bet history page — use API sync
        if provider_id == "pinnacle" and ("bets/history" in url or "spelhistorik" in url):
            info = await asyncio.to_thread(self._get_provider_sync_info, "pinnacle")
            if info["pending_bets"] > 0:
                logger.info(
                    f"[mirror] Pinnacle history page detected — syncing {info['pending_bets']} pending bets via API"
                )
                await asyncio.sleep(2)
                try:
                    await self._settle_via_workflow("pinnacle")
                except Exception as e:
                    logger.warning(f"[mirror] Pinnacle history sync failed: {e}")
            return

        # Kambi bet history page (SSR — needs DOM scrape)
        if provider_id in self._SSR_PROVIDERS:
            bet_history_paths = ("/betting/sports/bethistory",)
            if any(p in url for p in bet_history_paths):
                info = await asyncio.to_thread(self._get_provider_sync_info, provider_id)
                if info["pending_bets"] > 0:
                    logger.info(f"[mirror] {provider_id} bet history page detected — scraping")
                    await asyncio.sleep(3)
                    context = self.interceptor.context
                    if context:
                        for page in context.pages:
                            if provider_id in (page.url or "").lower() or any(
                                p in (page.url or "") for p in bet_history_paths
                            ):
                                await self._scrape_ssr_bet_history(provider_id, page)
                                return

    async def _auto_settle_pinnacle(self):
        """Auto-settle Pinnacle: navigate to history page, scrape DOM, settle matched bets."""
        import asyncio

        await asyncio.sleep(5)  # Wait for page to fully load

        context = self.interceptor.context
        if not context:
            return

        from .workflows import get_workflow

        workflow = get_workflow("pinnacle")
        page = await workflow.find_tab(context)
        if not page:
            logger.warning("[mirror] Pinnacle auto-settle: no tab found")
            return

        try:
            result = await workflow.settle_all(page)
            logger.info(f"[mirror] Pinnacle auto-settle: settled={result.get('settled', 0)}")
        except Exception as e:
            logger.warning(f"[mirror] Pinnacle auto-settle failed: {e}")

    async def _settle_via_workflow(self, provider_id: str):
        """Use a provider workflow's sync_history API to settle pending bets.

        Works for providers with REST API bet history (Pinnacle, etc.).
        Fetches settled bets via API, matches against pending DB bets by odds+stake.
        """
        from .workflows import get_workflow

        context = self.interceptor.context
        if not context or not context.pages:
            return

        workflow = get_workflow(provider_id)

        # Find tab: try workflow.find_tab, then fallback to domain/provider name match
        page = await workflow.find_tab(context)
        if not page:
            # Fallback: search by known domains from interceptor
            from .interceptor import BetInterceptor

            for p in context.pages:
                url = (p.url or "").lower()
                if provider_id in url:
                    page = p
                    break
                for domain, pid in BetInterceptor._PROVIDER_DOMAINS.items():
                    if pid == provider_id and domain in url:
                        page = p
                        break
                if page:
                    break
        if not page:
            logger.warning(f"[mirror] No {provider_id} tab found for history sync")
            return

        entries = await workflow.sync_history(page)
        if not entries:
            logger.info(f"[mirror] No history entries from {provider_id} API")
            return

        # Filter to settled only
        settled_entries = [e for e in entries if e.status in ("won", "lost", "void")]
        if not settled_entries:
            logger.info(f"[mirror] No settled entries from {provider_id}")
            return

        logger.info(f"[mirror] {provider_id} API returned {len(settled_entries)} settled bets")

        # Get pending bets from DB
        pending = await asyncio.to_thread(self._get_pending_bets_sync, provider_id)
        if not pending:
            return

        # Build full history of ALL bets from provider (settled + pending on provider)
        all_provider_bets = entries  # Everything the provider shows

        staged = []
        matched_db_ids = set()
        for entry in settled_entries:
            for pb in pending:
                # Match by odds (within 2%) and stake (within 2 units)
                odds_match = abs(entry.odds - pb["odds"]) / max(pb["odds"], 0.01) < 0.02
                stake_match = abs(entry.stake - pb["stake"]) < 2
                if not (odds_match and stake_match):
                    continue

                payout = entry.payout if entry.payout is not None else 0.0

                staged.append(
                    {
                        "bet_id": pb["id"],
                        "provider": provider_id,
                        "event": entry.event_name or "Unknown",
                        "odds": pb["odds"],
                        "stake": pb["stake"],
                        "result": entry.status,
                        "payout": round(payout, 2),
                    }
                )
                matched_db_ids.add(pb["id"])
                pending.remove(pb)
                break

        # Any remaining pending DB bets that DON'T appear in provider history = ghost bets
        # Only check bets where start_time has passed (future bets may not show in history yet)
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        for pb in pending:
            # Skip future bets — they won't be in history yet
            start = pb.get("start_time")
            if start:
                if hasattr(start, "tzinfo") and start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if isinstance(start, str):
                    try:
                        from datetime import datetime as dt

                        start = dt.fromisoformat(start.replace("Z", "+00:00"))
                    except Exception:
                        continue
                if start > now:
                    continue  # Event hasn't started — not a ghost

            found_on_provider = False
            for entry in all_provider_bets:
                odds_match = abs(entry.odds - pb["odds"]) / max(pb["odds"], 0.01) < 0.05
                stake_match = abs(entry.stake - pb["stake"]) < 5
                if odds_match and stake_match:
                    found_on_provider = True
                    break
            if not found_on_provider:
                staged.append(
                    {
                        "bet_id": pb["id"],
                        "provider": provider_id,
                        "event": f"[NOT FOUND ON {provider_id.upper()}]",
                        "odds": pb["odds"],
                        "stake": pb["stake"],
                        "result": "void",
                        "payout": 0.0,
                    }
                )

        if staged:
            self._pending_settlements = staged
            wins = [s for s in staged if s["result"] == "won"]
            losses = [s for s in staged if s["result"] == "lost"]
            voids = [s for s in staged if s["result"] == "void"]
            total_staked = sum(s["stake"] for s in staged)
            total_payout = sum(s["payout"] for s in staged)
            logger.info(
                f"[mirror] {provider_id}: {len(staged)} settlement(s) — "
                f"{len(wins)}W {len(losses)}L {len(voids)}V, net={total_payout - total_staked:+.0f}"
            )
            self._notify(
                "settlements_pending",
                {
                    "provider": provider_id,
                    "count": len(staged),
                    "wins": len(wins),
                    "losses": len(losses),
                    "total_staked": total_staked,
                    "total_payout": total_payout,
                    "net": total_payout - total_staked,
                    "settlements": staged,
                },
            )

    async def _auto_scrape_bet_history(self, provider_id: str):
        """Wait for page to load, then navigate to bet history and scrape."""
        # Wait for page to fully load
        await asyncio.sleep(5)

        context = self.interceptor.context
        if not context or not context.pages:
            return

        page = context.pages[0]
        current_url = page.url

        # Navigate to bet history if not already there
        bet_history_paths = {
            "unibet": "/betting/sports/bethistory",
            "leovegas": "/betting/sports/bethistory",
            "expekt": "/betting/sports/bethistory",
            "888sport": "/betting/sports/bethistory",
            "speedybet": "/betting/sports/bethistory",
            "x3000": "/betting/sports/bethistory",
            "goldenbull": "/betting/sports/bethistory",
            "betmgm": "/betting/sports/bethistory",
        }
        hist_path = bet_history_paths.get(provider_id)
        if not hist_path:
            return

        if hist_path not in current_url:
            # Navigate to bet history
            try:
                from urllib.parse import urlparse

                origin = urlparse(current_url)
                hist_url = f"{origin.scheme}://{origin.netloc}{hist_path}"
                logger.info(f"[mirror] Auto-navigating to bet history: {hist_url}")
                await page.goto(hist_url, wait_until="networkidle", timeout=15000)
                await asyncio.sleep(3)  # Let JS render
            except Exception as e:
                logger.warning(f"[mirror] Could not navigate to bet history: {e}")
                return

        # Scrape the page
        await self._scrape_ssr_bet_history(provider_id, page)

    async def _scrape_ssr_bet_history(self, provider_id: str, page):
        """Scrape SSR bet history from current page and stage settlements."""
        import re

        try:
            raw_text = await page.evaluate("() => document.body.innerText")
        except Exception as e:
            logger.warning(f"[mirror] Could not read page text: {e}")
            return

        # Parse Kambi bet history format (Swedish)
        bet_pattern = re.compile(
            r"Singel\s*@\s*([\d.]+)\s+"
            r"(Vinst|F.rlust|Oavgjord|Cashout)\s+"
            r"(\d+ \w+ \d{4})\s*.\s*([\d:]+)\s+"
            r"Kupong-Id:\s*(\d+)\s+"
            r"(.*?)"
            r"Insats:\s*([\d.,]+)\s*kr"
            r"(?:\s*Utbetalning:\s*([\d.,]+)\s*kr)?",
            re.DOTALL,
        )

        scraped = []
        seen = set()
        for m in bet_pattern.finditer(raw_text):
            cid = m.group(5)
            if cid in seen:
                continue
            seen.add(cid)
            result_raw = m.group(2)
            if "rlust" in result_raw:
                result = "lost"
            elif result_raw == "Vinst":
                result = "won"
            elif result_raw == "Oavgjord":
                result = "void"
            else:
                result = "cashout"
            scraped.append(
                {
                    "odds": float(m.group(1)),
                    "result": result,
                    "stake": float(m.group(7).replace(",", ".")),
                    "payout": float(m.group(8).replace(",", ".")) if m.group(8) else 0,
                    "event": m.group(6).strip().replace("\n", " ")[:80],
                }
            )

        if not scraped:
            logger.info(f"[mirror] SSR scrape found 0 bets for {provider_id}")
            return

        logger.info(f"[mirror] SSR scrape found {len(scraped)} bets for {provider_id}")

        # Match against pending bets
        pending = await asyncio.to_thread(self._get_pending_bets_sync, provider_id)
        staged = []
        for pb in pending:
            for sb in scraped:
                if abs(sb["odds"] - pb["odds"]) < 0.02 and abs(sb["stake"] - pb["stake"]) < 0.02:
                    staged.append(
                        {
                            "bet_id": pb["id"],
                            "provider": provider_id,
                            "event": sb["event"],
                            "odds": sb["odds"],
                            "stake": sb["stake"],
                            "result": sb["result"],
                            "payout": sb["payout"],
                        }
                    )
                    break

        if staged:
            self._pending_settlements = staged
            wins = [s for s in staged if s["result"] == "won"]
            losses = [s for s in staged if s["result"] == "lost"]
            logger.info(
                f"[mirror] Staged {len(staged)} SSR settlement(s) from {provider_id}: {len(wins)}W {len(losses)}L"
            )
            self._notify(
                "settlements_pending",
                {
                    "provider": provider_id,
                    "count": len(staged),
                    "wins": len(wins),
                    "losses": len(losses),
                    "total_staked": sum(s["stake"] for s in staged),
                    "total_payout": sum(s["payout"] for s in staged),
                    "net": sum(s["payout"] for s in staged) - sum(s["stake"] for s in staged),
                    "settlements": staged,
                },
            )

    def _get_pending_bets_sync(self, provider_id: str) -> list[dict]:
        """Get pending bets for a provider."""
        from ..repositories.profile_repo import ProfileRepo

        db = get_session()
        try:
            profile = ProfileRepo(db).get_active()
            pending = (
                db.query(Bet)
                .filter(
                    Bet.profile_id == profile.id,
                    Bet.provider_id == provider_id,
                    Bet.result == "pending",
                )
                .all()
            )
            return [{"id": b.id, "odds": b.odds, "stake": b.stake, "start_time": b.start_time} for b in pending]
        finally:
            db.close()

    def _get_provider_sync_info(self, provider_id: str) -> dict:
        """Get current balance + pending bet count for a provider."""
        from ..repositories.profile_repo import ProfileRepo

        db = get_session()
        try:
            repo = ProfileRepo(db)
            profile = repo.get_active()
            balance = repo.get_balance(profile.id, provider_id)
            pending = (
                db.query(Bet)
                .filter(
                    Bet.provider_id == provider_id,
                    Bet.result == "pending",
                )
                .all()
            )
            return {
                "balance": balance or 0,
                "pending_bets": len(pending),
                "pending_stake": sum(b.stake for b in pending),
            }
        except Exception as e:
            logger.debug(f"[mirror] Could not get sync info for {provider_id}: {e}")
            return {"balance": 0, "pending_bets": 0, "pending_stake": 0}
        finally:
            db.close()

    def get_status(self) -> dict[str, Any]:
        """Get current mirror status."""
        status = self.interceptor.get_status()
        status["logged_in_providers"] = sorted(self._logged_in_providers)
        return status

    async def _handle_event_data(self, url: str, response_body: str):
        """Cache event data from events-table or GetEventDetails responses."""
        # Altenar GetEventDetails — cache for live price reading
        if "GetEventDetails" in url:
            try:
                data = json.loads(response_body)
                eid = str(data.get("id", ""))
                if eid:
                    from .workflows.strategies.altenar import cache_event_details

                    cache_event_details(eid, data)
                    logger.debug(f"[mirror] Cached GetEventDetails for event {eid}")
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"[mirror] Could not parse GetEventDetails: {e}")
            return

        try:
            data = json.loads(response_body)
            events = data.get("data", {}).get("events", [])
            for event in events:
                event_id = event.get("id", "")
                participants = event.get("participants", [])
                if len(participants) >= 2:
                    participants.sort(key=lambda p: p.get("side", 0))
                    from ..matching.normalizer import normalize_team_name

                    home_label = participants[0].get("label", "")
                    away_label = participants[1].get("label", "")
                    self._event_cache[event_id] = {
                        "home_team": normalize_team_name(home_label),
                        "away_team": normalize_team_name(away_label),
                        "event_name": f"{home_label} vs {away_label}",
                    }
            if events:
                logger.debug(f"[mirror] Cached {len(events)} event(s) from events-table response")
        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"[mirror] Could not parse event data: {e}")

    async def _handle_bet_history(self, url: str, response_body: str, request_body: str | None = None):
        """Auto-settle pending bets from bet history responses.

        Supports:
        - Altenar: {"bets": [...]} with status codes 1=won, 2=lost, 3=void, 4=cashout
        - Gecko V2 coupon-history: {"data": {"coupons": [...]}} with couponStatus "Won"/"Lost"/"Void"
        """
        try:
            data = json.loads(response_body)
        except json.JSONDecodeError:
            return

        provider_id = None

        # Gecko V2 coupon-history format: normalize to Altenar-compatible format
        if "coupon-history" in url:
            coupons = data.get("data", {}).get("coupons", [])
            # Always store trace for debugging
            provider_id = self._detect_provider(url)
            await asyncio.to_thread(self._store_trace_sync, provider_id, url, request_body, response_body, "history")
            if not coupons:
                return
            # Gecko V2 coupon-history: betsStatus dict has {"won": N} or {"lost": N} etc.
            bets = []
            for c in coupons:
                bs = c.get("betsStatus", {})
                if "won" in bs:
                    status_code = 1
                elif "lost" in bs:
                    status_code = 2
                elif "void" in bs or "cancelled" in bs:
                    status_code = 3
                elif "cashedOut" in bs:
                    status_code = 4
                else:
                    continue
                event_names = c.get("eventNames", [])
                event_name = event_names[0] if event_names else ""
                # Normalize "Home - Away" to "Home vs Away"
                event_name = event_name.replace(" - ", " vs ")
                bets.append(
                    {
                        "status": status_code,
                        "totalStake": c.get("stake", 0),
                        "totalOdds": c.get("totalOdds", 0),
                        "totalWin": c.get("totalPayout", 0),
                        "eventName": event_name,
                    }
                )
        # Pinnacle: GET /0.1/bets → array of bet objects
        elif "arcadia.pinnacle" in url and isinstance(data, list):
            provider_id = "pinnacle"
            bets = []
            for b in data:
                status_str = b.get("settledAt")  # settled if has settledAt
                if not status_str:
                    continue  # unsettled — skip for settlement
                # Determine result from payout vs stake
                risk_amount = float(b.get("riskAmount", 0))
                win_amount = float(b.get("winAmount", 0))
                if win_amount > 0:
                    status_code = 1  # won
                elif risk_amount > 0:
                    status_code = 2  # lost
                else:
                    status_code = 3  # void
                sels = b.get("selections", [])
                event_name = ""
                if sels:
                    event_name = sels[0].get("matchup_id", "")
                bets.append(
                    {
                        "status": status_code,
                        "totalStake": risk_amount,
                        "totalOdds": float(b.get("price", 0)),
                        "totalWin": win_amount,
                        "eventName": str(event_name),
                        "confirmation_id": str(b.get("id", "")),
                    }
                )
        else:
            bets = data.get("bets", [])

        if not bets:
            return

        if not provider_id:
            provider_id = self._detect_provider_from_request(request_body) or self._detect_provider(url)
        logger.info(f"[mirror] Bet history intercepted: {len(bets)} bets from {provider_id}")

        staged = await asyncio.to_thread(self._stage_settlements_sync, bets, provider_id)
        if staged:
            self._pending_settlements = staged  # Replace — latest history is most accurate
            wins = [s for s in staged if s["result"] == "won"]
            losses = [s for s in staged if s["result"] == "lost"]
            total_staked = sum(s["stake"] for s in staged)
            total_payout = sum(s["payout"] for s in staged)
            net = total_payout - total_staked
            logger.info(
                f"[mirror] Staged {len(staged)} settlement(s) from {provider_id}: "
                f"{len(wins)}W {len(losses)}L, net={net:+.0f} SEK — confirm via API"
            )
            _pending_payload = {
                "provider": provider_id,
                "count": len(staged),
                "wins": len(wins),
                "losses": len(losses),
                "total_staked": total_staked,
                "total_payout": total_payout,
                "net": net,
                "settlements": staged,
            }
            self._notify("settlements_pending", _pending_payload)
            asyncio.ensure_future(self.event_router.broadcast_sync("settlement_pending", _pending_payload))

        # Record any untracked open bets (status 0) as pending
        open_bets = [b for b in bets if b.get("status") == 0]
        if open_bets and provider_id:
            recorded = await asyncio.to_thread(self._record_open_bets_sync, open_bets, provider_id)
            if recorded:
                logger.info(f"[mirror] Recorded {recorded} untracked open bet(s) from {provider_id} history")

        # Also store trace for audit
        await asyncio.to_thread(self._store_trace_sync, provider_id, url, request_body, response_body, "history")

    def _record_open_bets_sync(self, open_bets: list[dict], provider_id: str) -> int:
        """Record untracked open bets from history as pending in DB."""
        from ..db.models import Odds
        from ..repositories.profile_repo import ProfileRepo
        from ..services.bet_service import BetService

        db = get_session()
        recorded = 0
        try:
            for hb in open_bets:
                confirmation_id = str(hb.get("id", ""))
                if not confirmation_id:
                    continue

                stake = float(hb.get("totalStake", 0))
                odds = float(hb.get("totalOdds", 0))

                # Skip if already tracked (by confirmation_id OR by provider+odds+stake)
                if confirmation_id:
                    existing = (
                        db.query(Bet)
                        .filter(
                            Bet.confirmation_id == confirmation_id,
                            Bet.provider_id == provider_id,
                        )
                        .first()
                    )
                    if existing:
                        continue
                from sqlalchemy import func as sa_func

                existing = (
                    db.query(Bet)
                    .filter(
                        Bet.provider_id == provider_id,
                        sa_func.abs(Bet.odds - odds) < 0.02,
                        sa_func.abs(Bet.stake - stake) < 0.02,
                    )
                    .first()
                )
                if existing:
                    # Update confirmation_id if missing
                    if confirmation_id and not existing.confirmation_id:
                        existing.confirmation_id = confirmation_id
                        db.commit()
                    continue
                event_name = hb.get("eventName", "")
                if not stake or not odds:
                    continue

                # Match by odds + future event + name similarity
                from datetime import datetime, timedelta

                from sqlalchemy import func

                from ..db.models import Event

                now = datetime.utcnow()
                # Find odds matching this provider + odds value + future events only
                candidates = (
                    db.query(Odds, Event)
                    .join(Event, Odds.event_id == Event.id)
                    .filter(
                        Odds.provider_id == provider_id,
                        func.abs(Odds.odds - odds) < 0.02,
                        Event.start_time > now - timedelta(hours=6),
                    )
                    .all()
                )

                if not candidates:
                    logger.warning(f"[mirror] Could not match open bet to odds: {provider_id} {event_name} @ {odds}")
                    continue

                # Score by name similarity if event_name available
                best_row = None
                if event_name and len(candidates) > 1:
                    from rapidfuzz import fuzz

                    best_score = 0
                    for odds_row, ev in candidates:
                        db_name = f"{ev.home_team} vs {ev.away_team}"
                        score = fuzz.token_set_ratio(event_name.lower(), db_name.lower())
                        if score > best_score:
                            best_score = score
                            best_row = odds_row
                    logger.debug(f"[mirror] Best name match: {best_score}% for {event_name}")
                else:
                    best_row = candidates[0][0]

                if not best_row:
                    continue

                event_id = best_row.event_id
                market = best_row.market
                outcome = best_row.outcome

                # Record via BetService
                svc = BetService(db)
                resp = svc.create_bet(
                    event_id=event_id,
                    provider_id=provider_id,
                    market=market,
                    outcome=outcome,
                    odds=odds,
                    stake=stake,
                    bet_type="value",
                )
                if "error" in resp:
                    logger.warning(f"[mirror] Open bet recording failed: {resp['error']}")
                    continue

                bet_id = resp.get("id")
                if bet_id and confirmation_id:
                    bet_obj = db.query(Bet).filter(Bet.id == bet_id).first()
                    if bet_obj:
                        bet_obj.confirmation_id = confirmation_id

                db.commit()

                # Deduct balance
                repo = ProfileRepo(db)
                profile = repo.get_active()
                if profile:
                    current = repo.get_balance(profile.id, provider_id)
                    repo.set_balance(profile.id, provider_id, max(0, current - stake))
                    db.commit()

                recorded += 1
                logger.info(
                    f"[mirror] Recovered open bet from history: {provider_id} "
                    f"{event_name} @ {odds} × {stake} (id={confirmation_id}) → {event_id}"
                )
        except Exception as e:
            logger.error(f"[mirror] Error recording open bets: {e}", exc_info=True)
        finally:
            db.close()
        return recorded

    def _stage_settlements_sync(self, history_bets: list[dict], provider_id: str) -> list[dict]:
        """Match bet history against pending bets — stage for confirmation, don't commit.

        Matching priority:
        1. confirmation_id (Altenar bet id) — exact match, no ambiguity
        2. odds + stake — fallback when confirmation_id unavailable
        3. Skip history bets whose id already matches a settled bet (prevent cross-match)
        """
        STATUS_MAP = {1: "won", 2: "lost", 3: "void", 4: "cashout"}

        db = get_session()
        staged: list[dict] = []
        try:
            pending = (
                db.query(Bet)
                .filter(
                    Bet.result == "pending",
                    Bet.provider_id == provider_id,
                )
                .all()
            )
            if not pending:
                return []

            for hb in history_bets:
                result = STATUS_MAP.get(hb.get("status"))
                if not result:
                    continue

                stake = float(hb.get("totalStake", 0))
                odds = float(hb.get("totalOdds", 0))
                payout = float(hb.get("totalWin", 0))
                event_name = hb.get("eventName", "")
                history_id = str(hb.get("id", "")) or hb.get("confirmation_id", "")

                # Skip if this history bet's id matches an already-settled bet
                if history_id:
                    already_settled = (
                        db.query(Bet)
                        .filter(
                            Bet.confirmation_id == history_id,
                            Bet.provider_id == provider_id,
                            Bet.result != "pending",
                        )
                        .first()
                    )
                    if already_settled:
                        continue

                matched_bet = None

                # Priority 1: match by confirmation_id
                if history_id:
                    for bet in pending:
                        if bet.confirmation_id == history_id:
                            matched_bet = bet
                            break

                # Priority 2: match by odds + stake (only if no confirmation_id match)
                if not matched_bet:
                    for bet in pending:
                        if bet.result != "pending":
                            continue
                        if abs(bet.stake - stake) > 0.01:
                            continue
                        if abs(bet.odds - odds) > 0.01:
                            continue
                        # If bet has a confirmation_id, it must match the history id
                        if bet.confirmation_id and history_id and bet.confirmation_id != history_id:
                            continue
                        matched_bet = bet
                        break

                if not matched_bet:
                    continue

                pending.remove(matched_bet)
                fair = matched_bet.fair_odds_at_placement
                edge = round((odds / fair - 1) * 100, 1) if fair and fair > 0 else None
                pl = (payout - stake) if result == "won" else (-stake if result == "lost" else 0)
                staged.append(
                    {
                        "bet_id": matched_bet.id,
                        "provider": provider_id,
                        "event": event_name,
                        "odds": odds,
                        "stake": stake,
                        "result": result,
                        "payout": payout,
                        "edge": edge,
                        "pl": round(pl, 2),
                    }
                )

        except Exception as e:
            logger.error(f"[mirror] Error matching bets: {e}", exc_info=True)
        finally:
            db.close()
        return staged

    def confirm_settlements(self) -> dict:
        """Apply all pending settlements to the database. Returns summary."""
        if not self._pending_settlements:
            return {"settled": 0, "error": "No pending settlements"}

        provider_id = self._pending_settlements[0].get("provider", "unknown")
        db = get_session()
        settled = 0
        try:
            bet_service = BetService(db)
            for s in self._pending_settlements:
                bet_service.settle_bet(s["bet_id"], s["result"], s["payout"])
                settled += 1
                logger.info(
                    f"[mirror] Confirmed: bet #{s['bet_id']} {s['event']} → {s['result']} (payout={s['payout']})"
                )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror] Error confirming settlements: {e}", exc_info=True)
            return {"settled": settled, "error": str(e)}
        finally:
            db.close()

        summary = self._pending_settlements.copy()
        self._pending_settlements.clear()

        # Notify frontend to refresh bankroll
        _confirmed_payload = {
            "provider": provider_id,
            "settled": settled,
        }
        self._notify("settlements_confirmed", _confirmed_payload)
        asyncio.get_event_loop().call_soon_threadsafe(
            lambda d=_confirmed_payload: asyncio.ensure_future(
                self.event_router.broadcast_sync("settlement_confirmed", d)
            )
        )
        # Reset provider detection so balance re-syncs on next page visit
        self.interceptor.reset_detected_providers()
        # Allow poly settle loop to re-trigger on next provider detection
        task = getattr(self, "_poly_settle_task", None)
        if task and not task.done():
            task.cancel()
        self._poly_settle_task = None

        return {"settled": settled, "provider": provider_id, "settlements": summary}

    def reject_settlements(self) -> dict:
        """Discard all pending settlements."""
        count = len(self._pending_settlements)
        self._pending_settlements.clear()
        return {"rejected": count}

    async def scrape_polymarket_settlements(self) -> list[dict]:
        """Scrape Polymarket History tab and match against pending bets.

        History tab shows:
        - "Lost" rows = resolved to $0 → lost
        - "Claimed" rows = resolved, payout received → won or void
        - "Bought" rows = original purchase (skip for settlement)

        Matching: by market name fuzzy match + stake/shares alignment.
        """
        from rapidfuzz import fuzz

        context = self.interceptor.context
        if not context or not context.pages:
            logger.warning("[mirror] No browser context for portfolio scrape")
            return []

        # Find polymarket portfolio page — prefer /portfolio URL
        page = None
        fallback = None
        for p in context.pages:
            url = p.url or ""
            if "polymarket.com" not in url:
                continue
            if "/portfolio" in url:
                page = p
                break
            if not fallback:
                fallback = p
        if not page:
            page = fallback
        if not page:
            logger.warning("[mirror] No Polymarket tab open")
            return []
        logger.info(f"[mirror] Poly scrape using page: {page.url[:80]}")

        # Navigate to portfolio if not there
        if "/portfolio" not in (page.url or ""):
            try:
                await page.goto("https://polymarket.com/portfolio", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(3)
                logger.info("[mirror] Navigated to Polymarket portfolio")
            except Exception as e:
                logger.warning(f"[mirror] Could not navigate to portfolio: {e}")
                return []

        # Ensure we're on the History tab — click it if needed
        if "/portfolio" in (page.url or ""):
            try:
                # Check if History tab content is visible
                has_history = await page.evaluate("""() => {
                    const text = document.body.innerText || '';
                    return text.includes('Lost') || text.includes('Claimed');
                }""")
                if not has_history:
                    # Click the History tab
                    await page.evaluate("""() => {
                        const tabs = document.querySelectorAll('a, button, div[role="tab"]');
                        for (const t of tabs) {
                            if ((t.textContent || '').trim() === 'History') {
                                t.click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                    await asyncio.sleep(3)  # Wait for tab content to load
                    logger.info("[mirror] Clicked History tab on Polymarket portfolio")
            except Exception as e:
                logger.debug(f"[mirror] History tab click attempt: {e}")

        # Parse flat DOM text — more reliable than element-based scraping
        try:
            raw = await page.evaluate("() => document.body.innerText")
        except Exception as e:
            logger.warning(f"[mirror] Could not read Polymarket DOM: {e}")
            return []

        if not raw:
            return []

        # Extract Lost/Claimed entries from flat text
        # Format: "Claimed\nMarket Name\n+$15.01\n2h ago" or "Lost\nMarket Name\nYes 16¢ 55.7 shares\n-\n6h ago"
        import re

        settle_entries = []
        lines = raw.split("\n")
        for i, line in enumerate(lines):
            activity = line.strip()
            if activity not in ("Lost", "Claimed"):
                continue
            # Next non-empty line(s) are the market name
            market = ""
            value = 0.0
            shares = 0.0
            for j in range(i + 1, min(i + 6, len(lines))):
                l = lines[j].strip()
                if not l:
                    continue
                # Dollar value: "+$15.01" or "-$9.00" or "-"
                val_match = re.match(r"^[+-]?\$([\d,.]+)$", l)
                if val_match:
                    value = float(val_match.group(1).replace(",", ""))
                    if l.startswith("-"):
                        value = -value
                    continue
                # Shares: "55.7 shares"
                shares_match = re.search(r"([\d.]+)\s*shares", l)
                if shares_match:
                    shares = float(shares_match.group(1))
                    continue
                # Time: "2h ago", "19h ago"
                if re.match(r"\d+[hmd]\s*ago", l):
                    break
                # Skip dash placeholder
                if l == "-":
                    continue
                # Skip tags like "Yes 16¢" or "Team Solid 26¢" (outcome badge + price)
                if re.search(r"\d+\s*[¢c\xc2]", l) and len(l) < 40:
                    continue
                # Market name — first substantial text
                if not market and len(l) > 10:
                    market = l

            if market:
                settle_entries.append(
                    {
                        "activity": activity,
                        "market": market[:120],
                        "value": abs(value),
                        "shares": shares,
                    }
                )

        if not settle_entries:
            logger.info("[mirror] No Lost/Claimed entries in Polymarket history")
            return []

        for se in settle_entries:
            logger.info(f"[mirror] Poly history entry: {se['activity']} | {se['market'][:60]} | val={se['value']}")

        # Get pending Polymarket bets from DB (with event names for matching)
        pending = await asyncio.to_thread(self._get_pending_poly_bets_sync)
        if not pending:
            logger.info("[mirror] No pending Polymarket bets to settle")
            return []

        for pb in pending:
            logger.info(
                f"[mirror] Poly pending DB: id={pb['id']} | {pb['event_name'][:60]} | odds={pb['odds']} stake={pb['stake']}"
            )

        staged = []
        for entry in settle_entries:
            activity = entry.get("activity", "")
            market = entry.get("market", "")
            value = abs(entry.get("value", 0))
            shares = entry.get("shares", 0)

            if activity == "Lost":
                result = "lost"
                payout = 0.0
            elif activity == "Claimed":
                result = "won"  # May be void — check below
                payout = value
            else:
                continue

            # Match against pending bets by market name — require high confidence
            best_match = None
            best_score = 0
            for pb in pending:
                event_name = pb.get("event_name", "")
                s1 = fuzz.partial_ratio(market.lower(), event_name.lower())
                s2 = fuzz.token_set_ratio(market.lower(), event_name.lower())
                # Match home team name
                s3 = 0
                home = event_name.split(" vs ")[0].strip() if " vs " in event_name else ""
                if home and len(home) > 3:
                    s3 = fuzz.partial_ratio(home.lower(), market.lower())
                # Match away team name
                s4 = 0
                away = event_name.split(" vs ")[1].strip() if " vs " in event_name else ""
                if away and len(away) > 3:
                    s4 = fuzz.partial_ratio(away.lower(), market.lower())
                score = max(s1, s2, s3, s4)
                if score > best_score and score >= 75:
                    best_score = score
                    best_match = pb

            if not best_match:
                logger.debug(f"[mirror] No DB match for: {market[:60]} (best={best_score})")
                continue
            logger.info(f"[mirror] Matched: {market[:40]} -> {best_match['event_name'][:40]} (score={best_score})")

            # For Claimed: check if payout ≈ stake → void (got money back, no profit)
            if result == "won" and best_match["stake"] > 0:
                profit_ratio = payout / best_match["stake"]
                if 0.85 <= profit_ratio <= 1.15:
                    # Payout ≈ stake → void (push)
                    result = "void"
                    payout = best_match["stake"]

            staged.append(
                {
                    "bet_id": best_match["id"],
                    "provider": "polymarket",
                    "event": market[:80] or "Polymarket",
                    "odds": best_match["odds"],
                    "stake": best_match["stake"],
                    "result": result,
                    "payout": round(payout, 2),
                }
            )
            pending.remove(best_match)

        if staged:
            self._pending_settlements = staged
            wins = [s for s in staged if s["result"] == "won"]
            losses = [s for s in staged if s["result"] == "lost"]
            voids = [s for s in staged if s["result"] == "void"]
            total_staked = sum(s["stake"] for s in staged)
            total_payout = sum(s["payout"] for s in staged)
            logger.info(
                f"[mirror] Polymarket history: {len(staged)} settlement(s) — "
                f"{len(wins)}W {len(losses)}L {len(voids)}V, net={total_payout - total_staked:+.2f} USDC"
            )
            self._notify(
                "settlements_pending",
                {
                    "provider": "polymarket",
                    "count": len(staged),
                    "wins": len(wins),
                    "losses": len(losses),
                    "total_staked": total_staked,
                    "total_payout": total_payout,
                    "net": total_payout - total_staked,
                    "settlements": staged,
                },
            )

        return staged

    def _get_pending_poly_bets_sync(self) -> list[dict]:
        """Get pending Polymarket bets with event names for matching."""
        from ..db.models import Bet, Event
        from ..repositories.profile_repo import ProfileRepo

        db = get_session()
        try:
            profile = ProfileRepo(db).get_active()
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
            result = []
            for bet, event in pending:
                event_name = ""
                if event:
                    h = event.display_home or event.home_team or ""
                    a = event.display_away or event.away_team or ""
                    event_name = f"{h} vs {a}" if h and a else h or a
                result.append(
                    {
                        "id": bet.id,
                        "odds": bet.odds,
                        "stake": bet.stake,
                        "event_name": event_name,
                        "event_id": bet.event_id,
                        "outcome": bet.outcome,
                        "market": bet.market,
                        "start_time": bet.start_time,
                    }
                )
            return result
        finally:
            db.close()

    def settle_polymarket_bets(self) -> list[dict]:
        """Check for resolved Polymarket markets and stage settlements for pending bets.

        Uses the Gamma API (via PolymarketRetriever.fetch_resolved) to find finished events,
        then matches against pending Polymarket bets.
        """
        from ..db.models import Bet, Event, Odds, get_session
        from ..repositories.profile_repo import ProfileRepo

        db = get_session()
        staged = []
        try:
            profile = ProfileRepo(db).get_active()
            pending = (
                db.query(Bet)
                .filter(
                    Bet.profile_id == profile.id,
                    Bet.provider_id == "polymarket",
                    Bet.result == "pending",
                )
                .all()
            )

            if not pending:
                return []

            # For each pending bet, check if its event has resolved
            for bet in pending:
                if not bet.event_id:
                    continue

                # Check if the event is finished
                event = db.get(Event, bet.event_id)
                if not event or event.status != "finished":
                    continue

                # Look at resolved odds for this bet's market/outcome
                odds = (
                    db.query(Odds)
                    .filter(
                        Odds.event_id == bet.event_id,
                        Odds.provider == "polymarket",
                        Odds.market == bet.market,
                        Odds.outcome == bet.outcome,
                    )
                    .first()
                )

                if not odds or not odds.provider_meta:
                    continue

                # Determine result from the event resolution
                # Binary market: resolved price of ~1.0 means won, ~0.0 means lost
                result = "pending"
                payout = 0.0

                if odds.odds and odds.odds <= 1.01:
                    # This outcome resolved to $1 — won
                    result = "won"
                    payout = bet.stake / (1 / odds.odds) if odds.odds > 0 else 0
                elif odds.odds and odds.odds >= 50.0:
                    # Extreme odds = resolved to $0 — lost
                    result = "lost"
                    payout = 0

                if result != "pending":
                    staged.append(
                        {
                            "bet_id": bet.id,
                            "provider": "polymarket",
                            "event": (event.home_team or "") + " vs " + (event.away_team or "")
                            if event.home_team
                            else "Unknown",
                            "odds": bet.odds,
                            "stake": bet.stake,
                            "result": result,
                            "payout": payout,
                        }
                    )

        except Exception as e:
            logger.error(f"[mirror] Polymarket settlement check failed: {e}", exc_info=True)
        finally:
            db.close()

        if staged:
            self._pending_settlements.extend(staged)
            self._notify(
                "settlements_pending",
                {
                    "provider": "polymarket",
                    "count": len(staged),
                    "wins": len([s for s in staged if s["result"] == "won"]),
                    "losses": len([s for s in staged if s["result"] == "lost"]),
                    "total_staked": sum(s["stake"] for s in staged),
                    "total_payout": sum(s["payout"] for s in staged),
                    "net": sum(s["payout"] for s in staged) - sum(s["stake"] for s in staged),
                    "settlements": staged,
                },
            )

        return staged

    def get_pending_settlements(self) -> list[dict]:
        """Return current pending settlements for review."""
        return self._pending_settlements

    async def _handle_financial_data(self, url: str, response_body: str):
        """Auto-sync balance from intercepted financial data."""
        try:
            data = json.loads(response_body)
        except json.JSONDecodeError:
            return

        provider_id = self._detect_provider(url)
        if provider_id == "unknown":
            return

        balance = self._extract_balance(provider_id, data)
        if balance is not None:
            # Polymarket: /value returns portfolio value (positions), not cash.
            # Cash balance has no API — scrape from DOM every time we see /value traffic.
            if provider_id == "polymarket":
                await self._scrape_polymarket_balance()
            else:
                await asyncio.to_thread(self._sync_balance, provider_id, balance)
                # Login confirmed — fire sync_available (green)
                self._logged_in_providers.add(provider_id)
                info = await asyncio.to_thread(self._get_provider_sync_info, provider_id)
                logger.info(f"[mirror] {provider_id} logged in — balance: {balance}, pending: {info['pending_bets']}")
                self._notify(
                    "sync_available",
                    {
                        "provider": provider_id,
                        "balance": balance,
                        "pending_bets": info["pending_bets"],
                        "pending_stake": info["pending_stake"],
                    },
                )
                # Auto-navigate to bet history on first login if pending bets exist
                # Always sync bet history on first login (catch unknown bets + settle)
                if not hasattr(self, "_settle_checked"):
                    self._settle_checked = set()
                if provider_id not in self._settle_checked:
                    asyncio.ensure_future(self._auto_settle_via_history(provider_id))

        # Polymarket: store deposit trace from Swapped widget
        if "swapped.com" in url and "create_order" in url:
            deposit = self.polymarket_parser.parse_deposit(url, response_body)
            if deposit:
                logger.info(f"[mirror] Polymarket deposit initiated: ${deposit['amount']} {deposit['currency']}")
                self._notify(
                    "deposit_initiated",
                    {
                        "provider": "polymarket",
                        "amount": deposit["amount"],
                        "currency": deposit["currency"],
                        "order_id": deposit["order_id"],
                    },
                )

        # Polymarket: parse and broadcast open orders
        if "clob.polymarket.com/data/orders" in url:
            orders = self.polymarket_parser.parse_orders(response_body)
            if orders:
                self._notify(
                    "polymarket_orders",
                    {
                        "orders": orders,
                        "count": len(orders),
                        "open": len([o for o in orders if o["status"] == "live"]),
                    },
                )

        # Store trace for audit
        await asyncio.to_thread(self._store_trace_sync, provider_id, url, None, response_body, "balance")

    def _extract_balance(self, provider_id: str, data: dict) -> float | None:
        """Extract total balance (cash + bonus) from provider-specific response format."""
        try:
            # Polymarket: [{"user": "0x...", "value": 123.45}]
            if isinstance(data, list):
                if data and "user" in data[0] and "value" in data[0]:
                    return float(data[0]["value"])
                # No other balance format uses a bare list — fall through to GraphQL relay below
                if not data:
                    return None

            # Kambi / Unibet: {"balance": {"cash": 384.10, "bonus": 0, ...}}
            if "balance" in data and isinstance(data["balance"], dict):
                bal = data["balance"]
                total = float(bal.get("cash", 0)) + float(bal.get("bonus", 0))
                if total > 0:
                    return total
                if "total" in bal:
                    return float(bal["total"])

            # Altenar (quickcasino, betinia, etc.):
            # {"result": {"cash": {"total": 243.5}, "bonus": {"total": 500}}}
            result = data.get("result", {})
            if isinstance(result, dict) and "cash" in result:
                cash = result["cash"]
                cash_val = float(cash.get("total", cash.get("available", 0))) if isinstance(cash, dict) else float(cash)
                bonus = result.get("bonus", {})
                bonus_val = float(bonus.get("total", 0)) if isinstance(bonus, dict) else 0
                return cash_val + bonus_val

            # Pinnacle: {"amount": 535.0, "currency": "SEK"}
            if "amount" in data and "currency" in data:
                amt = float(data["amount"])
                if amt >= 0:
                    return amt

            # Gecko V2 / Spelklubben:
            # {"Balances": {"SEK": {"Real": {"Balance": 907}, "Bonus": {"Balance": 500}}}}
            balances = data.get("Balances", {})
            for _currency, parts in balances.items():
                if isinstance(parts, dict):
                    real = parts.get("Real", parts.get("Total", {}))
                    if isinstance(real, dict) and "Balance" in real:
                        real_bal = float(real["Balance"])
                        bonus_part = parts.get("Bonus", {})
                        bonus_bal = float(bonus_part.get("Balance", 0)) if isinstance(bonus_part, dict) else 0
                        return real_bal + bonus_bal

            # GraphQL relay (LeoVegas):
            # {"data":{"viewer":{"user":{"balance":{"amount":1076,"totalAmount":1076,"currency":"SEK"}}}}}
            # Also handles array response: [{"data":{"viewer":{"user":{"balance":...}}}}]
            relay = data
            if isinstance(data, list) and data:
                relay = data[0]
            viewer = relay.get("data", {}).get("viewer", {})
            user = viewer.get("user", {})
            bal = user.get("balance", {}) if isinstance(user, dict) else {}
            if isinstance(bal, dict) and "totalAmount" in bal:
                return float(bal["totalAmount"])

        except (TypeError, ValueError, KeyError) as e:
            logger.debug(f"[mirror] Could not extract balance for {provider_id}: {e}")
        return None

    def _sync_balance(self, provider_id: str, balance: float):
        """Update profile balance for the given provider."""
        from ..repositories.profile_repo import ProfileRepo

        db = get_session()
        try:
            repo = ProfileRepo(db)
            profile = repo.get_active()
            old_balance = repo.get_balance(profile.id, provider_id)
            repo.set_balance(profile.id, provider_id, balance)
            db.commit()
            if abs((old_balance or 0) - balance) > 0.01:
                logger.info(f"[mirror] Balance synced: {provider_id} {old_balance:.2f} → {balance:.2f} SEK")
                delta = balance - (old_balance or 0)
                event_data = {
                    "provider": provider_id,
                    "balance": balance,
                    "previous": old_balance,
                    "delta": round(delta, 2),
                }
                self._notify("balance_synced", event_data)
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda d=event_data: asyncio.ensure_future(self.event_router.broadcast_sync("balance_update", d))
                )
                # Positive delta = deposit detected
                if delta > 0.01:
                    self._notify("deposit_detected", event_data)
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror] Balance sync failed for {provider_id}: {e}")
        finally:
            db.close()

    async def _handle_bet_response(
        self, url: str, request_body: str | None, response_body: str, page_url: str | None = None
    ):
        """Process an intercepted bet placement response — any platform."""
        provider_id = self._detect_provider_from_request(request_body) or self._detect_provider(url)

        try:
            body = json.loads(response_body)
        except json.JSONDecodeError:
            logger.warning(f"[mirror] Invalid JSON response from {url}")
            await asyncio.to_thread(self._store_trace_sync, provider_id, url, request_body, response_body, "failed")
            return

        # Try to extract basic bet info from any platform
        bet_info = self._extract_bet_info(url, body, request_body)

        # Store the raw trace
        await asyncio.to_thread(self._store_trace_sync, provider_id, url, request_body, response_body, "bet_placed")

        # Pass page URL for event matching (Polymarket uses URL slug)
        bet_info["page_url"] = page_url or ""

        # Try to get event name from page title if missing
        if not bet_info.get("event_name") and page_url:
            try:
                for p in self.interceptor.context.pages:
                    if page_url and page_url in (p.url or ""):
                        title = await p.title()
                        if title and len(title) > 5:
                            bet_info["event_name"] = title.split("|")[0].strip()[:60]
                        break
            except Exception:
                pass

        # Record bet to DB
        recorded = await asyncio.to_thread(self._record_intercepted_bet, provider_id, bet_info)

        # Toast notification
        toast = {
            "status": "ok",
            "provider": provider_id,
            "event": bet_info.get("event_name", "Unknown event"),
            "market": bet_info.get("market"),
            "outcome": bet_info.get("outcome"),
            "odds": bet_info.get("odds"),
            "stake": bet_info.get("stake"),
            "matched": recorded,
        }
        logger.info(
            f"[mirror] Bet {'recorded' if recorded else 'captured'}: {provider_id} — "
            f"{toast['event']} @ {toast['odds']} × {toast['stake']}"
        )
        self._notify("bet_mirrored", toast)
        if recorded:
            asyncio.ensure_future(self.event_router.broadcast_action("bet_placed", toast))

    def _extract_bet_info(self, url: str, body: dict, request_body: str | None) -> dict:
        """Best-effort extraction of bet info from any platform response.

        Returns dict with whatever fields could be extracted:
        event_name, odds, stake, market, outcome, confirmation_id
        """
        info: dict[str, Any] = {}
        req: dict = {}
        if request_body:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                req = json.loads(request_body) if isinstance(request_body, str) else request_body

        url_lower = url.lower()

        # --- Altenar (placeWidget) ---
        if "placewidget" in url_lower:
            # Skip error responses (stake adjustment) — only process successful placements
            if "error" in body and "bets" not in body:
                return info  # Empty — will be skipped by _record_intercepted_bet

            bets = body.get("bets", [])
            if bets:
                b = bets[0]
                info["confirmation_id"] = str(b.get("id", ""))
                info["odds"] = b.get("totalOdds")
                info["stake"] = b.get("totalStake")
                sels = b.get("selections", [])
                if sels:
                    s = sels[0]
                    info["event_name"] = s.get("eventName", "")
                    info["outcome"] = s.get("name", "")
                    info["market"] = s.get("marketName", "")
                    if s.get("eventId"):
                        info["altenar_event_id"] = str(s["eventId"])
                    # Map Altenar marketTypeId to canonical market
                    mt = s.get("marketTypeId", 0)
                    _ALTENAR_MARKET_MAP = {
                        1: "1x2",
                        186: "moneyline",
                        219: "moneyline",
                        251: "moneyline",
                        406: "moneyline",
                        30001: "moneyline",
                        18: "total",
                        189: "total",
                        225: "total",
                        238: "total",
                        258: "total",
                        412: "total",
                        16: "spread",
                        187: "spread",
                        223: "spread",
                        237: "spread",
                        256: "spread",
                        410: "spread",
                    }
                    if mt in _ALTENAR_MARKET_MAP:
                        info["market"] = _ALTENAR_MARKET_MAP[mt]
                    # Map outcome via selectionTypeId: 1=home, 2=draw, 3=away, 12=over, 13=under
                    st = s.get("selectionTypeId", 0)
                    _ALTENAR_OUTCOME_MAP = {
                        1: "home",
                        2: "draw",
                        3: "away",
                        12: "over",
                        13: "under",
                        1714: "home",
                        1715: "away",
                    }
                    if st in _ALTENAR_OUTCOME_MAP:
                        info["designation"] = _ALTENAR_OUTCOME_MAP[st]
            # Request has richer data
            markets = req.get("betMarkets", [])
            if markets and not info.get("event_name"):
                m = markets[0]
                info["event_name"] = m.get("eventName", "")
                odds_list = m.get("odds", [])
                if odds_list:
                    info["outcome"] = odds_list[0].get("selectionName", "")
                    if not info.get("market"):
                        info["market"] = odds_list[0].get("marketName", "")
            stakes = req.get("stakes", [])
            if stakes and not info.get("stake"):
                info["stake"] = stakes[0]
            return info

        # --- Gecko V2 (coupons) ---
        if "/api/sb/" in url_lower and "coupon" in url_lower:
            coupon = body.get("couponStatus", {})
            info["confirmation_id"] = str(coupon.get("couponId", ""))
            # Odds/stake/market from request
            bets = req.get("bets", [])
            if bets:
                bet = bets[0]
                info["stake"] = bet.get("stake")
                sels = bet.get("betSelections", [])
                if sels:
                    info["odds"] = sels[0].get("odds")
            return info

        # --- Kambi (coupon.json) ---
        if "coupon" in url_lower and "kambi" in url_lower:
            coupon = body.get("coupon", body)
            info["confirmation_id"] = str(coupon.get("couponRef", ""))
            # Kambi uses integer milliodds (1840 = 1.84) and centistake (140000 = 1400.00)
            bets = coupon.get("bets", [])
            if bets:
                info["odds"] = bets[0].get("betOdds", 0) / 1000
                info["stake"] = bets[0].get("stake", 0) / 100
            events = coupon.get("events", [])
            if events:
                e = events[0]
                info["event_name"] = e.get("eventName", "")
                info["home_team"] = e.get("homeName")
                info["away_team"] = e.get("awayName")
            outcomes = coupon.get("outcomes", [])
            if outcomes:
                info["outcome"] = outcomes[0].get("label", "")
            bet_offers = coupon.get("betOffers", [])
            if bet_offers:
                info["market"] = bet_offers[0].get("criterion", "")
            return info

        # --- Pinnacle (bets/straight) ---
        if "bets/straight" in url_lower and "quote" not in url_lower:
            info["confirmation_id"] = str(body.get("id", ""))
            info["odds"] = body.get("price")
            # matchup_id is in the REQUEST selections, not response
            req_sels = req.get("selections", [])
            if req_sels:
                rs = req_sels[0]
                info["matchup_id"] = str(rs.get("matchupId", rs.get("matchup_id", "")))
                info["designation"] = rs.get("designation", "")
                # Derive market from marketKey: s;0;m=moneyline, s;0;s=spread, s;0;ou=total
                mk = rs.get("marketKey", "")
                if mk.startswith("s;0;m"):
                    info["market"] = "moneyline"
                elif mk.startswith("s;0;s"):
                    info["market"] = "spread"
                elif mk.startswith("s;0;ou"):
                    info["market"] = "total"
            # Response selections may have extra info
            resp_sels = body.get("selections", [])
            if resp_sels and not info.get("designation"):
                info["designation"] = resp_sels[0].get("designation", "")
            # Stake from request
            info["stake"] = req.get("stake") or req.get("riskAmount")
            return info

        # --- Polymarket (clob.polymarket.com/order) ---
        if "clob.polymarket" in url_lower and "order" in url_lower:
            # Response: {orderID, status, ...}
            # Request: {tokenID, price, size, side, ...}
            info["confirmation_id"] = str(body.get("orderID", body.get("id", "")))
            # Price from request (decimal, e.g. 0.44 = 44¢)
            price = req.get("price")
            if price:
                info["odds"] = round(1 / float(price), 2) if float(price) > 0 else 0
            # Size/amount from request
            info["stake"] = req.get("amount") or req.get("size")
            # Token ID for matching
            info["token_id"] = req.get("tokenID", "")
            return info

        # --- Generic fallback: scan for common field names ---
        for key in ("totalStake", "stake", "amount"):
            if key in body:
                info["stake"] = body[key]
                break
            if key in req:
                info["stake"] = req[key]
                break
        for key in ("totalOdds", "odds", "price"):
            if key in body:
                info["odds"] = body[key]
                break
        for key in ("eventName", "event_name", "matchName"):
            if key in body:
                info["event_name"] = body[key]
                break

        return info

    def _extract_teams_from_page_url(self, page_url: str, parsed: dict):
        """Extract team names from Gecko V2 event page URL slug.

        Gecko event page URLs typically look like:
          /sport/fotboll/.../team1-vs-team2-{eventId}
          /sport/football/.../team1-v-team2-{eventId}
        """
        try:
            path = unquote(urlparse(page_url).path)
            # Match "team1-vs-team2" or "team1-v-team2" patterns in the URL
            match = re.search(r"/([^/]+?)(?:-vs?-|%20vs?%20)([^/]+?)(?:-[a-zA-Z0-9_]{10,})?/?$", path, re.IGNORECASE)
            if match:
                from ..matching.normalizer import normalize_team_name

                home = match.group(1).replace("-", " ").strip()
                away = match.group(2).replace("-", " ").strip()
                if len(home) > 2 and len(away) > 2:
                    parsed["home_team"] = normalize_team_name(home)
                    parsed["away_team"] = normalize_team_name(away)
                    parsed["event_name"] = f"{home} vs {away}"
                    logger.info(f"[mirror] Resolved from page URL: {parsed['event_name']}")
        except Exception as e:
            logger.debug(f"[mirror] Could not parse teams from page URL: {e}")

    async def _enrich_from_page_title(self, parsed: dict):
        """Extract team names from the browser page title as last resort."""
        context = self.interceptor.context
        if not context or not context.pages:
            logger.debug("[mirror] No browser pages available for title extraction")
            return

        try:
            page = context.pages[0]
            title = await page.title()
            if not title:
                return

            # Page titles often look like "Team1 - Team2 | Spelklubben" or "Team1 vs Team2"
            for sep in [" - ", " vs ", " vs. ", " v "]:
                if sep in title:
                    parts = title.split(sep, 1)
                    home = parts[0].strip()
                    # Strip trailing site name after | or –
                    away = re.split(r"\s*[|–—]\s*", parts[1])[0].strip()
                    if len(home) > 2 and len(away) > 2:
                        from ..matching.normalizer import normalize_team_name

                        parsed["home_team"] = normalize_team_name(home)
                        parsed["away_team"] = normalize_team_name(away)
                        parsed["event_name"] = f"{home} vs {away}"
                        logger.info(f"[mirror] Resolved from page title: {parsed['event_name']}")
                        return
        except Exception as e:
            logger.debug(f"[mirror] Could not read page title: {e}")

    async def _enrich_from_gecko_api(self, bet_url: str, parsed: dict):
        """Fetch event details from Gecko API to resolve team names."""
        gecko_id = parsed.get("gecko_event_id", "")
        if not gecko_id:
            return

        # Derive API base from the bet URL domain
        origin = urlparse(bet_url)
        api_base = f"{origin.scheme}://{origin.netloc}"

        # Use the interceptor's browser context to make the API call
        context = self.interceptor.context
        if not context:
            logger.warning("[mirror] No browser context for Gecko API enrichment")
            return
        if not context.pages:
            logger.warning("[mirror] No browser pages open for Gecko API enrichment")
            return

        try:
            # Include required params that the real site uses
            api_url = (
                f"{api_base}/api/sb/v1/widgets/events-table/v2"
                f"?categoryIds=1&eventIds=f-{gecko_id}"
                f"&eventPhase=Prematch&eventSortBy=Popularity"
                f"&maxMarketCount=2&priceFormats=1"
            )
            logger.debug(f"[mirror] Enrichment API call: {api_url}")
            resp = await context.request.get(api_url, timeout=5000)
            if resp.status != 200:
                logger.warning(f"[mirror] Gecko event lookup returned HTTP {resp.status} for gecko_id={gecko_id}")
                return

            data = await resp.json()
            events = data.get("data", {}).get("events", [])
            if not events:
                logger.warning(f"[mirror] No events returned for gecko_id={gecko_id} (empty response)")
                return

            event = events[0]
            participants = event.get("participants", [])
            if len(participants) < 2:
                logger.warning(f"[mirror] Event {gecko_id} has <2 participants: {participants}")
                return

            participants.sort(key=lambda p: p.get("side", 0))
            from ..matching.normalizer import normalize_team_name

            parsed["home_team"] = normalize_team_name(participants[0].get("label", ""))
            parsed["away_team"] = normalize_team_name(participants[1].get("label", ""))
            parsed["event_name"] = f"{participants[0].get('label', '')} vs {participants[1].get('label', '')}"
            logger.info(f"[mirror] Resolved via Gecko API: {parsed['event_name']}")

            # Also cache for future bets on same event
            self._event_cache[gecko_id] = {
                "home_team": parsed["home_team"],
                "away_team": parsed["away_team"],
                "event_name": parsed["event_name"],
            }

        except Exception as e:
            logger.error(f"[mirror] Gecko API enrichment failed for gecko_id={gecko_id}: {e}", exc_info=True)

    # Altenar integration codes → our provider IDs
    _ALTENAR_INTEGRATION_MAP = {
        "campose": "campobet",
        "quickcasinose": "quickcasino",
        "betiniase2": "betinia",
        "lodurse": "lodur",
        "dbetse": "dbet",
        "swiperse": "swiper",
    }

    def _detect_provider_from_request(self, request_body: str | None) -> str | None:
        """Extract provider from Altenar integration field in request body."""
        if not request_body:
            return None
        try:
            req = json.loads(request_body)
            integration = req.get("integration", "").lower()
            if integration:
                # Exact match first
                if integration in self._ALTENAR_INTEGRATION_MAP:
                    return self._ALTENAR_INTEGRATION_MAP[integration]
                # Fuzzy fallback — check if any known provider name is a substring
                for keyword in self._ALTENAR_INTEGRATION_MAP.values():
                    if keyword in integration:
                        return keyword
        except (json.JSONDecodeError, Exception):
            pass
        return None

    # Kambi operator codes in API paths → provider ID
    _KAMBI_OPERATOR_MAP = {
        "ubse": "unibet",
        "ubdk": "unibet",
        "ubno": "unibet",
        "ubfi": "unibet",
        "888se": "888sport",
        "888dk": "888sport",
        "leose": "leovegas",
        "expse": "expekt",
        "speedyse": "speedybet",
        "x3000se": "x3000",
        "gbse": "goldenbull",
        "1x2se": "1x2",
        "betmgmse": "betmgm",
    }

    def _detect_provider(self, url: str) -> str:
        """Best-effort provider detection from URL domain or path."""
        url_lower = url.lower()
        # Direct domain matches — keyword in URL → provider_id
        domain_map = {
            # Altenar
            "campobet": "campobet",
            "quickcasino": "quickcasino",
            "betinia": "betinia",
            "swiper": "swiper",
            "lodur": "lodur",
            "dbet": "dbet",
            # Gecko V2
            "spelklubben": "spelklubben",
            "betsson": "betsson",
            "betsafe": "betsafe",
            "nordicbet": "nordicbet",
            "bethard": "bethard",
            "hajper": "hajper",
            # Kambi
            "unibet": "unibet",
            "leovegas": "leovegas",
            "expekt": "expekt",
            "888sport": "888sport",
            "speedybet": "speedybet",
            "x3000": "x3000",
            "goldenbull": "goldenbull",
            "1x2": "1x2",
            "betmgm": "betmgm",
            # Custom / other
            "comeon": "comeon",
            "lyllocasino": "lyllo",
            "snabbare": "snabbare",
            "10bet": "10bet",
            "mrgreen": "mrgreen",
            "vbet": "vbet",
            "interwetten": "interwetten",
            "coolbet": "coolbet",
            "tipwin": "tipwin",
            # Sharp
            "pinnacle": "pinnacle",
            # Polymarket
            "polymarket": "polymarket",
        }
        for keyword, provider_id in domain_map.items():
            if keyword in url_lower:
                return provider_id

        # Polymarket data API
        if "polymarket" in url_lower or "swapped.com" in url_lower:
            return "polymarket"

        # Kambi operator code in URL path (e.g. /ubse/coupon.json)
        if "kambi" in url_lower:
            for code, provider_id in self._KAMBI_OPERATOR_MAP.items():
                if f"/{code}/" in url_lower:
                    return provider_id

        # Altenar shared gateway — check for integration in URL params
        if "altenar" in url_lower or "biahosted" in url_lower:
            for keyword in self._KNOWN_PROVIDERS:
                if keyword in url_lower:
                    return keyword

        return "unknown"

    def _process_bet_sync(
        self, provider_id: str, url: str, request_body: str | None, response_body: str, parsed: dict
    ) -> dict[str, Any]:
        """Synchronous: create bet + store trace (runs in thread)."""
        db = get_session()
        try:
            confirmation_id = parsed["confirmation_id"]

            # Dedup
            existing = db.query(Bet).filter(Bet.confirmation_id == confirmation_id).first()
            if existing:
                logger.info(f"[mirror] Bet {confirmation_id} already logged (dedup)")
                return {"status": "duplicate", "confirmation_id": confirmation_id, "provider": provider_id}

            # Match event
            event_id = self._match_event(db, parsed)

            # Create bet
            bet_service = BetService(db)
            bet_result = bet_service.create_bet(
                event_id=event_id,
                provider_id=provider_id,
                market=parsed.get("market"),
                outcome=parsed.get("outcome"),
                odds=parsed["odds"],
                stake=parsed["stake"],
                point=parsed.get("point"),
                bet_type="mirror",
            )

            if "error" not in bet_result:
                bet_obj = db.get(Bet, bet_result["bet_id"])
                if bet_obj:
                    bet_obj.confirmation_id = confirmation_id

            db.commit()

            bet_id = bet_result.get("bet_id")
            parse_status = "ok" if event_id else "unmatched"
            if "error" in bet_result:
                parse_status = "failed"

            self._store_trace(
                db=db,
                provider_id=provider_id,
                url=url,
                request_body=request_body,
                response_body=response_body,
                parse_status=parse_status,
                provider_bet_id=confirmation_id,
                bet_id=bet_id,
            )
            db.commit()

            return {
                "status": "ok" if "error" not in bet_result else "error",
                "confirmation_id": confirmation_id,
                "provider": provider_id,
                "event": parsed.get("event_name", "Unknown event"),
                "market": parsed.get("market"),
                "outcome": parsed.get("outcome"),
                "odds": parsed["odds"],
                "stake": parsed["stake"],
                "matched": event_id is not None,
                "error": bet_result.get("error"),
            }
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror] Error processing bet: {e}", exc_info=True)
            return {"status": "error", "error": str(e), "provider": provider_id}
        finally:
            db.close()

    def _record_intercepted_bet(self, provider_id: str, bet_info: dict) -> bool:
        """Record an intercepted bet placement to the database.

        Matches the bet to our event DB using matchup_id (Pinnacle) or event name.
        Returns True if recorded successfully.
        """
        from ..repositories.profile_repo import ProfileRepo
        from ..services.bet_service import BetService

        odds = bet_info.get("odds")
        stake = bet_info.get("stake")
        if not odds or not stake:
            return False

        odds = float(odds)
        stake = float(stake)
        confirmation_id = bet_info.get("confirmation_id")
        market = bet_info.get("market", "moneyline")
        designation = bet_info.get("designation", "")  # home/away
        matchup_id = bet_info.get("matchup_id")

        # Map designation to canonical outcome
        outcome = designation if designation in ("home", "away", "draw", "over", "under") else "home"

        # Normalize market type
        if market.lower() in ("moneyline", "ml"):
            market = "moneyline"
        elif market.lower() in ("spread", "handicap"):
            market = "spread"
        elif market.lower() in ("total", "totals", "over_under"):
            market = "total"
        elif market.lower() in ("1x2",):
            market = "1x2"

        # Find event_id by matchup_id (Pinnacle), page URL slug (Polymarket), or event name
        event_id = None
        page_url = bet_info.get("page_url", "")
        db = get_session()
        try:
            if matchup_id:
                from sqlalchemy import text

                row = db.execute(
                    text(
                        "SELECT event_id FROM odds WHERE provider_id = :pid "
                        "AND provider_meta->>'matchup_id' = :mid LIMIT 1"
                    ),
                    {"pid": provider_id, "mid": str(matchup_id)},
                ).first()
                if row:
                    event_id = row[0]

            # Altenar: match by event_id in provider_meta
            altenar_eid = bet_info.get("altenar_event_id")
            if not event_id and altenar_eid:
                from sqlalchemy import text

                row = db.execute(
                    text(
                        "SELECT event_id FROM odds WHERE provider_id = :pid "
                        "AND provider_meta->>'event_id' = :eid LIMIT 1"
                    ),
                    {"pid": provider_id, "eid": str(altenar_eid)},
                ).first()
                if row:
                    event_id = row[0]
                    logger.info(f"[mirror] Altenar event matched by event_id: {altenar_eid} → {event_id}")

            # Polymarket: match by event_slug from page URL
            if not event_id and provider_id == "polymarket" and page_url:
                # Extract slug from URL: polymarket.com/event/{slug} or /sports/.../slug
                import re

                from sqlalchemy import text

                # Extract slug = last path segment with date pattern (e.g. lol-ff1-big1-2026-04-07)
                slug_match = re.search(r"polymarket\.com/.+/([a-z0-9][\w-]+-\d{4}-\d{2}-\d{2})", page_url.lower())
                if slug_match:
                    slug = slug_match.group(1)
                    row = db.execute(
                        text(
                            "SELECT event_id FROM odds WHERE provider_id = 'polymarket' "
                            "AND provider_meta->>'event_slug' = :slug LIMIT 1"
                        ),
                        {"slug": slug},
                    ).first()
                    if row:
                        event_id = row[0]
                        logger.info(f"[mirror] Polymarket event matched by slug: {slug} → {event_id}")

            # Fallback: match by event name (home vs away) against events table
            event_name = bet_info.get("event_name", "")
            if not event_id and event_name:
                import re

                from ..matching.normalizer import normalize_team_name

                # Split "Home vs. Away" or "Home vs Away"
                parts = re.split(r"\s+vs\.?\s+", event_name, maxsplit=1)
                if len(parts) == 2:
                    home_norm = normalize_team_name(parts[0].strip())
                    away_norm = normalize_team_name(parts[1].strip())
                    from datetime import datetime, timedelta

                    from ..db.models import Event

                    cutoff = datetime.utcnow() - timedelta(days=3)
                    candidates = (
                        db.query(Event)
                        .filter(
                            Event.start_time >= cutoff,
                        )
                        .all()
                    )
                    for ev in candidates:
                        ev_home = normalize_team_name(ev.home_team)
                        ev_away = normalize_team_name(ev.away_team)
                        if (ev_home == home_norm and ev_away == away_norm) or (
                            ev_home == away_norm and ev_away == home_norm
                        ):
                            event_id = ev.id
                            logger.info(f"[mirror] Event matched by name: '{event_name}' → {event_id}")
                            break

            if not event_id:
                logger.warning(f"[mirror] Could not match bet to event: {bet_info}")
                db.close()
                return False

            # Check for duplicate
            from ..db.models import Bet

            existing = (
                db.query(Bet)
                .filter(
                    Bet.confirmation_id == str(confirmation_id),
                    Bet.provider_id == provider_id,
                )
                .first()
            )
            if existing:
                logger.info(f"[mirror] Bet already recorded: {confirmation_id}")
                db.close()
                return True

            # Record via BetService
            svc = BetService(db)
            resp = svc.create_bet(
                event_id=event_id,
                provider_id=provider_id,
                market=market,
                outcome=outcome,
                odds=odds,
                stake=stake,
                bet_type="value",
            )
            if "error" in resp:
                logger.warning(f"[mirror] Bet recording failed: {resp['error']}")
                db.close()
                return False

            # Update confirmation_id on the bet
            bet_id = resp.get("id")
            if bet_id and confirmation_id:
                bet = db.query(Bet).filter(Bet.id == bet_id).first()
                if bet:
                    bet.confirmation_id = str(confirmation_id)

            db.commit()

            # Deduct balance
            repo = ProfileRepo(db)
            profile = repo.get_active()
            if profile:
                current = repo.get_balance(profile.id, provider_id)
                repo.set_balance(profile.id, provider_id, max(0, current - stake))
                db.commit()

            logger.info(f"[mirror] Bet recorded to DB: {provider_id} {event_id} {market}/{outcome} @ {odds} × {stake}")
            return True
        except Exception as exc:
            logger.exception(f"[mirror] Failed to record bet: {exc}")
            db.rollback()
            return False
        finally:
            db.close()

    def _store_trace_sync(
        self, provider_id: str, url: str, request_body: str | None, response_body: str, parse_status: str
    ):
        """Store trace in a new DB session (for rejected/failed bets)."""
        db = get_session()
        try:
            self._store_trace(db, provider_id, url, request_body, response_body, parse_status)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror] Error storing trace: {e}")
        finally:
            db.close()

    def _store_trace(
        self,
        db,
        provider_id: str,
        url: str,
        request_body: str | None,
        response_body: str,
        parse_status: str,
        provider_bet_id: str | None = None,
        bet_id: int | None = None,
    ) -> BetTrace:
        """Insert a BetTrace record."""
        trace = BetTrace(
            timestamp=datetime.now(timezone.utc),
            provider_id=provider_id,
            request_url=url,
            request_body=request_body,
            response_body=response_body,
            bet_id=bet_id,
            provider_bet_id=provider_bet_id,
            parse_status=parse_status,
        )
        db.add(trace)
        return trace

    def _match_event(self, db, parsed: dict) -> str | None:
        """Try to match intercepted bet to an internal Event."""
        from datetime import timedelta

        from rapidfuzz import fuzz

        from ..db.models import Event

        home = parsed.get("home_team")
        away = parsed.get("away_team")
        if not home or not away:
            logger.warning("[mirror] Cannot match — no team names resolved")
            return None

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=7)
        events = (
            db.query(Event)
            .filter(
                Event.home_team.isnot(None),
                Event.away_team.isnot(None),
                Event.start_time >= now - timedelta(hours=3),
                Event.start_time <= cutoff,
            )
            .all()
        )

        best_match = None
        best_score = 0.0

        for event in events:
            home_score = fuzz.ratio(home, event.home_team or "")
            away_score = fuzz.ratio(away, event.away_team or "")
            combined = (home_score + away_score) / 2
            if combined > best_score:
                best_score = combined
                best_match = event

        if best_match and best_score >= 75:
            logger.info(f"[mirror] Matched to event {best_match.id} (score={best_score:.0f})")
            return best_match.id

        logger.warning(f"[mirror] No match for {home} vs {away} (best={best_score:.0f})")
        return None

    def _load_notification_recipes(self):
        """Load recipes from disk on init."""
        self._recipes = load_recipes(self._recipes_dir)
        if self._recipes:
            active = [r for r in self._recipes if r.status == "active"]
            logger.info(f"[mirror] Loaded {len(active)} active notification recipes")

    def _save_notification_recipes(self):
        """Persist recipes to disk."""
        save_recipes(self._recipes, self._recipes_dir)

    async def _handle_notification_settings(
        self,
        url: str,
        method: str,
        request_body: str | None,
        response_body: str,
        content_type: str,
    ):
        """Capture a notification settings API call as a recipe."""
        provider_id = self._detect_provider(url)
        if provider_id == "unknown":
            logger.debug(f"[mirror] Notification settings call from unknown provider: {url}")
            return

        recipe = NotificationRecipe(
            provider_id=provider_id,
            captured_at=datetime.now(timezone.utc).isoformat(),
            method=method,
            url=url,
            content_type=content_type or "application/json",
            body=request_body or "",
            status="active",
        )

        # Replace existing recipe for this provider
        self._recipes = [r for r in self._recipes if r.provider_id != provider_id]
        self._recipes.append(recipe)
        self._save_notification_recipes()

        logger.info(f"[mirror] Captured notification mute recipe for {provider_id}: {method} {url}")
        self._notify(
            "notification_recipe_captured",
            {
                "provider": provider_id,
                "method": method,
                "url": url,
            },
        )

    async def _replay_notification_mute(self, provider_id: str):
        """Replay a stored notification mute recipe for a provider."""
        if provider_id in self._muted_providers:
            return

        recipe = next((r for r in self._recipes if r.provider_id == provider_id and r.status == "active"), None)
        if not recipe:
            return

        context = self.interceptor.context
        if not context:
            return

        try:
            # Small delay for auth cookies to settle after navigation
            await asyncio.sleep(2)

            resp = await context.request.fetch(
                recipe.url,
                method=recipe.method,
                headers={"content-type": recipe.content_type},
                data=recipe.body if recipe.body else None,
                timeout=10000,
            )

            if resp.status < 400:
                self._muted_providers.add(provider_id)
                logger.info(f"[mirror] Notifications muted for {provider_id} (HTTP {resp.status})")
                self._notify("notifications_muted", {"provider": provider_id})
            else:
                recipe.status = "stale"
                self._save_notification_recipes()
                logger.warning(
                    f"[mirror] Mute replay failed for {provider_id} (HTTP {resp.status}) — recipe marked stale"
                )
                self._notify("notifications_mute_failed", {"provider": provider_id, "status": resp.status})

        except Exception as e:
            logger.error(f"[mirror] Mute replay error for {provider_id}: {e}")

    def get_notification_recipes(self) -> list[dict]:
        """Return all recipes as dicts for API response."""
        return [r.to_dict() for r in self._recipes]

    def delete_notification_recipe(self, provider_id: str) -> bool:
        """Delete a recipe by provider ID. Returns True if found and deleted."""
        before = len(self._recipes)
        self._recipes = [r for r in self._recipes if r.provider_id != provider_id]
        if len(self._recipes) < before:
            self._save_notification_recipes()
            self._muted_providers.discard(provider_id)
            return True
        return False

    def _fetch_fair_odds(self, event_ids: list[str]) -> dict[str, dict[str, float]]:
        """Fetch Pinnacle devigged fair odds for events from DB.

        Returns: {event_id: {outcome: fair_odds}} where fair_odds are devigged.
        """
        from collections import defaultdict

        from ..analysis.devig import get_fair_odds_for_outcome
        from ..db.models import Odds, get_session

        db = get_session()
        try:
            pinnacle_rows = (
                db.query(Odds)
                .filter(
                    Odds.event_id.in_(event_ids),
                    Odds.provider_id == "pinnacle",
                )
                .all()
            )

            # Group by (event_id, market, point) to build full markets for devigging
            markets: dict[tuple, dict[str, float]] = defaultdict(dict)
            for row in pinnacle_rows:
                key = (row.event_id, row.market, row.point)
                markets[key][row.outcome] = row.odds

            # Devig each market, build result
            result: dict[str, dict[str, float]] = defaultdict(dict)
            for (event_id, _market, _point), market_odds in markets.items():
                if len(market_odds) >= 2:
                    for outcome in market_odds:
                        fair = get_fair_odds_for_outcome(outcome, market_odds, method="multiplicative")
                        if fair is not None:
                            result[event_id][outcome] = fair
                else:
                    # Single outcome (e.g. spread) — use raw as conservative estimate
                    for outcome, odds_val in market_odds.items():
                        result[event_id][outcome] = odds_val
            return dict(result)
        finally:
            db.close()

    def _btn_index_for_outcome(self, original_outcome: str, market_type: str) -> int:
        """Map outcome to button index within a market section (0=home/over, 1=away/under/draw-for-2btn).

        For 1x2 markets: home=0, draw=1, away=2.
        For 2-button markets (ML, spread, total): home/over=0, away/under=1.
        """
        if original_outcome in ("home", "over"):
            return 0
        elif original_outcome == "draw":
            return 1
        elif original_outcome in ("away", "under"):
            return 2 if market_type == "1x2" else 1
        return 0

    # Map internal market types to Polymarket section labels
    _MARKET_SECTION_LABELS = {
        "moneyline": ["moneyline", "match winner", "series winner", "winner"],
        "1x2": ["moneyline", "match winner", "1x2", "winner"],
        "spread": ["game handicap", "handicap", "spread", "map handicap"],
        "total": ["total games", "total maps", "total", "over/under"],
    }

    @staticmethod
    def _market_url(slug: str) -> str:
        """Build Polymarket event URL from slug."""
        return f"https://polymarket.com/event/{slug}"

    @staticmethod
    async def _read_btn_prices(page) -> list[dict]:
        """Read trading button prices from a Polymarket page, grouped by market section.

        Returns list of {text, price, section} where section is the market label
        (e.g. 'Moneyline', 'Game Handicap', 'Total Games').
        """
        return await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('button.trading-button')];
            return btns.map(b => {
                const text = b.textContent || '';
                // Match cents price before ¢. Use last match to avoid spread numbers.
                // "GEN -1.5  63¢" → captures "63", not "1.5"
                const allMatches = [...text.matchAll(/(\\d{1,2})\\u00a2/g)];
                const priceMatch = allMatches.length > 0 ? allMatches[allMatches.length - 1] : null;
                const price = priceMatch ? parseInt(priceMatch[1]) / 100 : null;

                // Walk up to find the market section label
                let section = '';
                let el = b.parentElement;
                for (let i = 0; i < 15 && el; i++) {
                    // Look for a sibling or child heading that names the market
                    const headings = el.querySelectorAll('p, span, h3, h4');
                    for (const h of headings) {
                        const t = (h.textContent || '').trim().toLowerCase();
                        if (['moneyline', 'match winner', 'series winner', 'winner',
                             'game handicap', 'handicap', 'spread', 'map handicap',
                             'total games', 'total maps', 'total', 'over/under',
                             'game 1 winner', 'game 2 winner', 'game 3 winner',
                             'map 1 winner', 'map 2 winner', 'map 3 winner',
                             '1st half', '2nd half'].some(kw => t.includes(kw))) {
                            section = t;
                            break;
                        }
                    }
                    if (section) break;
                    el = el.parentElement;
                }

                return {text: text.trim().slice(0, 40), price, section};
            });
        }""")

    def _find_btn_for_market(
        self,
        buttons: list[dict],
        original_outcome: str,
        market_type: str,
        home_name: str = "",
        away_name: str = "",
    ) -> dict | None:
        """Find the correct button for a bet's market type and outcome.

        First matches by section label (market type), then matches by team
        name in button text. Falls back to index-based matching if no text match.
        """
        target_labels = self._MARKET_SECTION_LABELS.get(market_type, ["moneyline", "winner"])

        # Group buttons by section, preserving order
        sections: dict[str, list[dict]] = {}
        for btn in buttons:
            sec = btn.get("section", "")
            sections.setdefault(sec, []).append(btn)

        # Find the matching section
        matched_section = None
        for sec_label, sec_btns in sections.items():
            if any(kw in sec_label for kw in target_labels):
                if any(skip in sec_label for skip in ["game 1", "game 2", "game 3", "map 1", "map 2", "map 3"]):
                    continue
                matched_section = sec_btns
                break

        if matched_section is None:
            matched_section = buttons if len(sections) <= 1 else list(sections.values())[0] if sections else buttons

        if not matched_section:
            return None

        # Build candidate names to search for in button text
        # Polymarket abbreviates: "Alba Berlin" → "ALB", "Cloud9" → "C9"
        target_names: list[str] = []
        if original_outcome == "home" and home_name:
            name = home_name.lower()
            parts = name.split()
            target_names.append(name)  # "indiana pacers"
            if len(parts) > 1:
                target_names.append(parts[-1])  # "pacers"
                target_names.append(parts[0])  # "indiana"
            target_names.append(name[:3])  # "ind"
        elif original_outcome == "away" and away_name:
            name = away_name.lower()
            parts = name.split()
            target_names.append(name)
            if len(parts) > 1:
                target_names.append(parts[-1])
                target_names.append(parts[0])
            target_names.append(name[:3])
        elif original_outcome == "over":
            target_names.append("o ")
        elif original_outcome == "under":
            target_names.append("u ")
        elif original_outcome == "draw":
            target_names.append("draw")

        # Deduplicate buttons by text (Polymarket renders same buttons in sidebar + main)
        seen_texts = set()
        deduped = []
        for btn in matched_section:
            t = (btn.get("text") or "").lower()
            if t not in seen_texts:
                seen_texts.add(t)
                deduped.append(btn)

        # Try text-based matching — try each candidate name
        if target_names and len(deduped) >= 2:
            for target in target_names:
                matches = [btn for btn in deduped if target in (btn.get("text") or "").lower()]
                if len(matches) == 1:
                    return matches[0]  # Unique match — confident

        # Fallback: price-based matching for 2-button moneyline markets
        # If we know our expected odds, pick the button closest to our price
        if len(matched_section) == 2 and market_type in ("moneyline", "1x2"):
            # For home/away: our outcome has a known price direction
            # home_name is set → find which button is NOT the other team
            btn_a, btn_b = matched_section[0], matched_section[1]
            pa, pb = btn_a.get("price"), btn_b.get("price")
            if pa is not None and pb is not None:
                # The button with the minority name match wins
                a_text = (btn_a.get("text") or "").lower()
                b_text = (btn_b.get("text") or "").lower()
                # Check if away name appears in either button
                if away_name:
                    away_parts = [
                        away_name.lower()[:3],
                        away_name.lower().split()[-1] if " " in away_name else away_name.lower(),
                    ]
                    a_is_away = any(p in a_text for p in away_parts)
                    b_is_away = any(p in b_text for p in away_parts)
                    if original_outcome == "home":
                        if a_is_away and not b_is_away:
                            return btn_b
                        if b_is_away and not a_is_away:
                            return btn_a

        # Last fallback: index-based matching
        btn_idx = self._btn_index_for_outcome(original_outcome, market_type)
        if 0 <= btn_idx < len(matched_section):
            return matched_section[btn_idx]
        return None

    async def _ensure_poly_tabs(self, bets: list[dict]) -> None:
        """Ensure exactly one tab per unique market slug in the current batch.

        - Closes tabs for slugs no longer needed
        - Cleans up stale/closed tabs
        - Opens new tabs for unseen slugs
        """
        import asyncio

        context = self.interceptor.context
        if not context:
            return

        wanted_slugs = {b["market_slug"] for b in bets}

        # Close tabs for slugs not in current batch + clean stale
        for slug in list(self._poly_tabs):
            try:
                page = self._poly_tabs[slug]
                if page.is_closed() or slug not in wanted_slugs:
                    if not page.is_closed():
                        await page.close()
                    del self._poly_tabs[slug]
            except Exception:
                self._poly_tabs.pop(slug, None)

        # Find slugs that need new tabs
        needed_slugs = wanted_slugs - set(self._poly_tabs)
        if not needed_slugs:
            return

        # Open new tabs for missing slugs
        async def open_tab(slug):
            url = self._market_url(slug)
            try:
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_selector("button.trading-button", timeout=15000)
                self._poly_tabs[slug] = page
            except Exception as e:
                logger.warning(f"[mirror] Failed to open tab for {slug}: {e}")

        await asyncio.gather(*[open_tab(slug) for slug in needed_slugs])

    async def close_poly_tabs(self) -> None:
        """Close all persistent Polymarket tabs."""
        for slug in list(self._poly_tabs):
            try:
                await self._poly_tabs.pop(slug).close()
            except Exception:
                self._poly_tabs.pop(slug, None)

    async def get_live_edge(self, bets: list[dict]) -> dict:
        """Read live Polymarket prices from persistent tabs, compare against Pinnacle fair odds.

        Reuses tabs across poll cycles — only opens new tabs for unseen markets.

        Each bet dict: {bet_id, market_slug, outcome, expected_price, amount_usdc,
                        event_id, original_odds, _original_outcome, _market_type}
        Returns: {bets: [{bet_id, outcome, event_id, live_odds, fair_odds, edge_pct, stake, status}]}
        """
        import asyncio

        from ..analysis.value import compute_edge

        context = self.interceptor.context
        if not context or not context.pages:
            return {"error": "No mirror browser open", "bets": []}

        # Fetch Pinnacle fair odds for all events in batch
        event_ids = list({b["event_id"] for b in bets if b.get("event_id")})
        fair_odds_map = await asyncio.to_thread(self._fetch_fair_odds, event_ids)

        # Ensure tabs are open (reuses existing ones)
        await self._ensure_poly_tabs(bets)

        # Read prices from persistent tabs
        results = []
        for bet in bets:
            bet_id = bet["bet_id"]
            slug = bet["market_slug"]
            outcome = bet["outcome"]
            amount = bet["amount_usdc"]
            original_outcome = bet.get("_original_outcome", outcome).lower()
            market_type = bet.get("_market_type", "")
            event_id = bet.get("event_id", "")

            event_fair = fair_odds_map.get(event_id, {})
            fair = event_fair.get(original_outcome)

            page = self._poly_tabs.get(slug)
            if page is None:
                results.append(
                    {
                        "bet_id": bet_id,
                        "outcome": outcome,
                        "event_id": event_id,
                        "live_odds": None,
                        "fair_odds": round(fair, 2) if fair else None,
                        "edge_pct": None,
                        "stake": amount,
                        "status": "error",
                        "reason": "Page load failed",
                    }
                )
                continue

            try:
                btn_data = await self._read_btn_prices(page)
                matched = self._find_btn_for_market(btn_data, original_outcome, market_type)

                if matched and matched.get("price") is not None:
                    live_price = matched["price"]
                    live_odds = round(1 / live_price, 2) if 0 < live_price < 1 else 999
                    edge_pct = compute_edge("polymarket", live_odds, fair) if fair else None

                    status = (
                        "value"
                        if edge_pct is not None and edge_pct > 0
                        else ("negative" if edge_pct is not None else "no-sharp")
                    )
                    results.append(
                        {
                            "bet_id": bet_id,
                            "outcome": outcome,
                            "event_id": event_id,
                            "live_odds": live_odds,
                            "fair_odds": round(fair, 2) if fair else None,
                            "edge_pct": round(edge_pct, 1) if edge_pct is not None else None,
                            "stake": amount,
                            "status": status,
                        }
                    )
                else:
                    results.append(
                        {
                            "bet_id": bet_id,
                            "outcome": outcome,
                            "event_id": event_id,
                            "live_odds": None,
                            "fair_odds": round(fair, 2) if fair else None,
                            "edge_pct": None,
                            "stake": amount,
                            "status": "error",
                            "reason": f"No price for outcome {original_outcome!r}",
                        }
                    )
            except Exception as e:
                results.append(
                    {
                        "bet_id": bet_id,
                        "outcome": outcome,
                        "event_id": event_id,
                        "live_odds": None,
                        "fair_odds": round(fair, 2) if fair else None,
                        "edge_pct": None,
                        "stake": amount,
                        "status": "error",
                        "reason": str(e),
                    }
                )

        self._notify("live_edge_complete", {"bets": results})
        return {"bets": results}

    async def fire_with_live_edge(self, bets: list[dict]) -> dict:
        """Use persistent tabs to read live prices, auto-fire bets with positive edge.

        Reuses tabs from _poly_tabs. Places bets on their respective tabs.
        Closes all tabs after firing is complete.

        Returns: {placed: [...], skipped: [...], negative: [...], errors: [], total: N}
        """
        import asyncio

        from ..analysis.value import compute_edge

        context = self.interceptor.context
        if not context or not context.pages:
            return {"error": "No mirror browser open", "placed": [], "skipped": [], "negative": [], "errors": []}

        # Fetch Pinnacle fair odds for all events
        event_ids = list({b["event_id"] for b in bets if b.get("event_id")})
        fair_odds_map = await asyncio.to_thread(self._fetch_fair_odds, event_ids)

        # Ensure tabs are open (reuses existing ones)
        await self._ensure_poly_tabs(bets)

        placed = []
        skipped = []
        negative = []
        errors = []

        for bet in bets:
            bet_id = bet["bet_id"]
            event_id = bet.get("event_id", "")
            outcome = bet["outcome"]
            original_outcome = bet.get("_original_outcome", outcome).lower()
            market_type = bet.get("_market_type", "")
            slug = bet["market_slug"]
            amount = bet["amount_usdc"]
            expected_price = bet["expected_price"]
            max_slippage = bet.get("max_slippage_pct", 2.0)

            event_fair = fair_odds_map.get(event_id, {})
            fair = event_fair.get(original_outcome)

            if fair is None:
                errors.append({"bet_id": bet_id, "reason": "No Pinnacle fair odds", "status": "no-sharp"})
                continue

            page = self._poly_tabs.get(slug)
            if page is None:
                errors.append({"bet_id": bet_id, "reason": "Page load failed", "status": "error"})
                continue

            try:
                btn_data = await self._read_btn_prices(page)
            except Exception as e:
                errors.append({"bet_id": bet_id, "reason": f"Could not read prices: {e}", "status": "error"})
                continue

            matched = self._find_btn_for_market(btn_data, original_outcome, market_type)
            if not matched or matched.get("price") is None:
                errors.append(
                    {"bet_id": bet_id, "reason": f"No price for {market_type}/{original_outcome}", "status": "error"}
                )
                continue

            live_price = matched["price"]
            live_odds = round(1 / live_price, 2) if 0 < live_price < 1 else 999
            edge_pct = compute_edge("polymarket", live_odds, fair)

            if edge_pct is None or edge_pct <= 0:
                negative.append(
                    {
                        "bet_id": bet_id,
                        "live_odds": live_odds,
                        "fair_odds": round(fair, 2),
                        "edge_pct": round(edge_pct, 1) if edge_pct is not None else None,
                        "status": "negative",
                    }
                )
                self._notify(
                    "live_edge_skip",
                    {
                        "bet_id": bet_id,
                        "live_odds": live_odds,
                        "fair_odds": round(fair, 2),
                        "edge_pct": round(edge_pct, 1) if edge_pct is not None else None,
                    },
                )
                continue

            # Edge is positive — place the bet on its tab
            logger.info(
                f"[mirror] Firing bet {bet_id}: live_odds={live_odds}, fair_odds={fair:.2f}, edge={edge_pct:.1f}%"
            )
            self._notify(
                "live_edge_firing",
                {
                    "bet_id": bet_id,
                    "live_odds": live_odds,
                    "fair_odds": round(fair, 2),
                    "edge_pct": round(edge_pct, 1),
                },
            )

            result = await self._place_single_polymarket_bet(
                page,
                bet_id,
                slug,
                outcome,
                amount,
                expected_price,
                max_slippage,
                original_outcome=bet.get("_original_outcome", outcome),
                market_type=market_type,
            )

            if result.get("status") == "placed":
                result["live_odds"] = live_odds
                result["fair_odds"] = round(fair, 2)
                result["edge_pct"] = round(edge_pct, 1)
                placed.append(result)
            elif result.get("status") == "skipped":
                result["live_odds"] = live_odds
                result["fair_odds"] = round(fair, 2)
                result["edge_pct"] = round(edge_pct, 1)
                skipped.append(result)
            else:
                errors.append(result)

        # Close all tabs after firing
        await self.close_poly_tabs()

        summary = {
            "placed": placed,
            "skipped": skipped,
            "negative": negative,
            "errors": errors,
            "total": len(bets),
        }
        self._notify("fire_live_complete", summary)
        return summary

    async def place_polymarket_bets(self, bets: list[dict]) -> dict:
        """Place bets on Polymarket via Playwright UI automation.

        Each bet dict: {bet_id, market_slug, token_id, outcome, amount_usdc, expected_price, max_slippage_pct}
        Returns: {placed: [...], skipped: [...], failed: [...], total: N}
        """
        context = self.interceptor.context
        if not context or not context.pages:
            return {"error": "No mirror browser open", "placed": [], "skipped": [], "failed": [], "total": 0}

        page = context.pages[0]
        placed = []
        skipped = []
        failed = []

        for bet in bets:
            bet_id = bet["bet_id"]
            slug = bet["market_slug"]
            outcome = bet["outcome"]
            amount = bet["amount_usdc"]
            expected_price = bet["expected_price"]
            max_slippage = bet.get("max_slippage_pct", 2.0)

            self._notify(
                "polymarket_bet_placing",
                {
                    "bet_id": bet_id,
                    "market_slug": slug,
                    "outcome": outcome,
                    "amount": amount,
                },
            )

            try:
                original_outcome = bet.get("_original_outcome", outcome)
                market_type = bet.get("_market_type", "")
                result = await self._place_single_polymarket_bet(
                    page,
                    bet_id,
                    slug,
                    outcome,
                    amount,
                    expected_price,
                    max_slippage,
                    original_outcome=original_outcome,
                    market_type=market_type,
                )
                if result["status"] == "placed":
                    placed.append(result)
                elif result["status"] == "skipped":
                    skipped.append(result)
                else:
                    failed.append(result)
            except Exception as e:
                logger.error(f"[mirror] Polymarket bet {bet_id} failed: {e}", exc_info=True)
                result = {"bet_id": bet_id, "status": "failed", "reason": str(e)}
                failed.append(result)
                self._notify("polymarket_bet_failed", result)

        summary = {"placed": placed, "skipped": skipped, "failed": failed, "total": len(bets)}
        self._notify(
            "polymarket_batch_complete",
            {
                "placed": len(placed),
                "skipped": len(skipped),
                "failed": len(failed),
                "total": len(bets),
            },
        )
        return summary

    async def _prepare_polymarket_bet(
        self,
        page,
        bet_id: int,
        slug: str,
        outcome: str,
        amount: float,
        expected_price: float,
        max_slippage: float,
        original_outcome: str = "",
        market_type: str = "",
        home_name: str = "",
        away_name: str = "",
    ) -> dict:
        """Phase 1: Navigate, click outcome, check slippage, fill amount. Does NOT click Buy.

        Returns {status: "ready", ...} if betslip is prepared and waiting for confirmation.
        The user can visually verify the betslip, then call _confirm_polymarket_bet to execute.
        """
        import asyncio

        # 1. Navigate to market page
        slug.split("-")
        market_url = f"https://polymarket.com/event/{slug}"
        logger.info(f"[mirror] Preparing Polymarket bet {bet_id}: {market_url} {outcome} ${amount}")
        await page.goto(market_url, wait_until="domcontentloaded", timeout=30000)

        # Wait for trading buttons
        try:
            await page.wait_for_selector("button.trading-button", timeout=15000)
        except Exception:
            await asyncio.sleep(5)

        # 2. Click the correct outcome button
        outcome_lower = (original_outcome or outcome).lower()
        home_name = home_name or ""
        away_name = away_name or ""

        if outcome_lower in ("home", "over"):
            target = home_name.lower()[:3] if home_name else ""
        elif outcome_lower in ("away", "under"):
            target = away_name.lower()[:3] if away_name else ""
        elif outcome_lower == "draw":
            target = "draw"
        else:
            target = outcome.lower()[:3]

        try:
            clicked = await page.evaluate(
                "(target) => {"
                "  const btns = [...document.querySelectorAll('button.trading-button')];"
                "  for (const btn of btns) {"
                "    const text = (btn.textContent || '').toLowerCase();"
                "    if (target && text.includes(target)) {"
                "      btn.style.outline = '3px solid #00ff00';"
                "      btn.style.outlineOffset = '2px';"
                "      btn.scrollIntoView({block: 'center'});"
                "      btn.click();"
                "      return btn.textContent.trim().slice(0, 40);"
                "    }"
                "  }"
                "  return null;"
                "}",
                target,
            )
            if not clicked:
                return {
                    "bet_id": bet_id,
                    "status": "failed",
                    "reason": f"No button matching '{target}' for '{outcome}'",
                }
            logger.info(f"[mirror] Clicked outcome button: '{clicked}' (target='{target}') for bet {bet_id}")
            await asyncio.sleep(1)
        except Exception as e:
            return {"bet_id": bet_id, "status": "failed", "reason": f"Could not click outcome: {e}"}

        # 3. Read live price and check slippage
        live_price = None
        try:
            price_text = await page.evaluate(
                "(outcome) => {"
                "  const btns = document.querySelectorAll('button.trading-button[role=\"radio\"]');"
                "  for (const btn of btns) {"
                "    const text = btn.textContent || '';"
                "    if (text.startsWith(outcome)) {"
                "      const priceMatch = text.match(/([\\d.]+)\\u00a2/);"
                "      if (priceMatch) return parseFloat(priceMatch[1]) / 100;"
                "    }"
                "  }"
                "  return null;"
                "}",
                outcome,
            )
            if price_text is not None:
                live_price = float(price_text)
                slippage_pct = abs(live_price - expected_price) / expected_price * 100
                if not self.polymarket_parser.check_slippage(expected_price, live_price, max_slippage):
                    return {
                        "bet_id": bet_id,
                        "status": "skipped",
                        "reason": "slippage",
                        "expected_price": expected_price,
                        "actual_price": live_price,
                        "slippage_pct": round(slippage_pct, 2),
                    }
        except Exception as e:
            logger.warning(f"[mirror] Could not read price for bet {bet_id}: {e}")

        # 4. Enter amount — use quick-add buttons (+$1, +$5, +$10, +$100)
        # React controls the input so evaluate-based value setting doesn't stick.
        # Quick-add buttons are real clicks that React handles natively.
        try:
            target_amount = int(amount)
            remaining = target_amount
            for btn_val in [100, 10, 5, 1]:
                while remaining >= btn_val:
                    clicked = await page.evaluate(
                        f"() => {{"
                        f"  const btns = document.querySelectorAll('button');"
                        f"  for (const btn of btns) {{"
                        f"    if (btn.textContent.trim() === '+${btn_val}') {{"
                        f"      btn.click(); return true;"
                        f"    }}"
                        f"  }}"
                        f"  return false;"
                        f"}}"
                    )
                    if clicked:
                        remaining -= btn_val
                        await asyncio.sleep(0.15)
                    else:
                        break
            if remaining > 0:
                logger.warning(f"[mirror] Could not fill full amount: ${target_amount - remaining}/${target_amount}")
            else:
                logger.info(f"[mirror] Filled amount ${target_amount} via quick-add buttons")
            await asyncio.sleep(0.5)
        except Exception as e:
            return {"bet_id": bet_id, "status": "failed", "reason": f"Could not enter amount: {e}"}

        # Ready — betslip is filled, waiting for user to verify and confirm
        logger.info(f"[mirror] Polymarket bet {bet_id} READY: {outcome} ${amount} @ {live_price or '?'}")
        return {
            "bet_id": bet_id,
            "status": "ready",
            "outcome": outcome,
            "amount": amount,
            "live_price": live_price,
            "live_odds": round(1.0 / live_price, 3) if live_price and live_price > 0 else None,
        }

    async def _confirm_polymarket_bet(self, page, bet_id: int) -> dict:
        """Phase 2: Click Buy button and wait for confirmation. Call after _prepare."""
        import asyncio

        try:
            submit_btn = page.locator('button.trading-button:not([role="radio"])').filter(has_text="Buy").first
            await submit_btn.click(timeout=5000)
            logger.info(f"[mirror] Clicked Buy button for bet {bet_id}")
            await asyncio.sleep(5)
        except Exception as e:
            return {"bet_id": bet_id, "status": "failed", "reason": f"Could not click Buy: {e}"}

        # Check for success
        try:
            success = await page.evaluate(
                "() => {"
                "  const text = document.body.innerText;"
                "  return text.includes('Order placed') || text.includes('Success') ||"
                "         text.includes('Confirmed') || text.includes('Position') ||"
                "         text.includes('Open order');"
                "}"
            )
            if success:
                logger.info(f"[mirror] Polymarket bet {bet_id} confirmed")
                result = {"bet_id": bet_id, "status": "placed"}
                self._notify("polymarket_bet_placed", result)
                return result
        except Exception:
            pass

        # Uncertain — report as placed
        result = {"bet_id": bet_id, "status": "placed", "note": "confirmation_uncertain"}
        self._notify("polymarket_bet_placed", result)
        return result

    async def _place_single_polymarket_bet(
        self,
        page,
        bet_id: int,
        slug: str,
        outcome: str,
        amount: float,
        expected_price: float,
        max_slippage: float,
        original_outcome: str = "",
        market_type: str = "",
        home_name: str = "",
        away_name: str = "",
    ) -> dict:
        """Place a single bet on Polymarket via browser automation.

        Discovered DOM structure (2026-04-01):
        - Order panel: div with class containing 'shadow-md' and 'bg-surface-1'
        - Buy/Sell toggle: button[role="radio"] with text "Buy" / "Sell"
        - Outcome buttons: button.trading-button[role="radio"] containing "Yes"/"No" + price
        - Amount input: input[placeholder="$0"]
        - Quick amounts: buttons with text "+$1", "+$5", "+$10", "+$100", "Max"
        - Submit: button.trading-button with text "Buy Yes" / "Buy No" etc.
        """
        import asyncio

        # 1. Navigate to market page
        # Polymarket sports URLs: /sports/{league}/{slug} or just /{slug}
        # Extract league prefix from slug (e.g. "bra2" from "bra2-juv-nov-2026-03-31")
        slug.split("-")
        market_url = f"https://polymarket.com/event/{slug}"
        logger.info(f"[mirror] Placing Polymarket bet {bet_id}: {market_url} {outcome} ${amount}")
        await page.goto(market_url, wait_until="domcontentloaded", timeout=30000)

        # Wait for trading buttons to appear (React hydration)
        try:
            await page.wait_for_selector("button.trading-button", timeout=15000)
        except Exception:
            await asyncio.sleep(5)  # Fallback wait

        # 2. Click the correct outcome button by matching team name in text
        # Cannot use index — page has duplicate trading-button sets (order panel + chart)
        outcome_lower = (original_outcome or outcome).lower()

        # Determine which team name to match
        # home_name/away_name passed from workflow caller

        if outcome_lower in ("home", "over"):
            target = home_name.lower()[:3] if home_name else ""
        elif outcome_lower in ("away", "under"):
            target = away_name.lower()[:3] if away_name else ""
        elif outcome_lower == "draw":
            target = "draw"
        else:
            target = outcome.lower()[:3]

        try:
            # Highlight the target button with a border, then click it
            clicked = await page.evaluate(
                "(target) => {"
                "  const btns = [...document.querySelectorAll('button.trading-button')];"
                "  for (const btn of btns) {"
                "    const text = (btn.textContent || '').toLowerCase();"
                "    if (target && text.includes(target)) {"
                "      btn.style.outline = '3px solid #00ff00';"
                "      btn.style.outlineOffset = '2px';"
                "      btn.scrollIntoView({block: 'center'});"
                "      btn.click();"
                "      return btn.textContent.trim().slice(0, 40);"
                "    }"
                "  }"
                "  return null;"
                "}",
                target,
            )
            if not clicked:
                return {
                    "bet_id": bet_id,
                    "status": "failed",
                    "reason": f"No button matching '{target}' for '{outcome}'",
                }
            logger.info(f"[mirror] Clicked outcome button: '{clicked}' (target='{target}') for bet {bet_id}")
            await asyncio.sleep(1)
        except Exception as e:
            return {"bet_id": bet_id, "status": "failed", "reason": f"Could not click outcome: {e}"}

        # 3. Read current price from the selected outcome button and check slippage
        try:
            # The trading-button text format is "Yes0.1¢" or "No52¢" — extract the price
            price_text = await page.evaluate(
                "(outcome) => {"
                "  const btns = document.querySelectorAll('button.trading-button[role=\"radio\"]');"
                "  for (const btn of btns) {"
                "    const text = btn.textContent || '';"
                "    if (text.startsWith(outcome)) {"
                "      const priceMatch = text.match(/([\\d.]+)\\u00a2/);"
                "      if (priceMatch) return parseFloat(priceMatch[1]) / 100;"
                "    }"
                "  }"
                "  return null;"
                "}",
                outcome,
            )

            if price_text is not None:
                current_price = float(price_text)
                slippage_ok = self.polymarket_parser.check_slippage(expected_price, current_price, max_slippage)
                slippage_pct = abs(current_price - expected_price) / expected_price * 100

                self._notify(
                    "polymarket_bet_price_check",
                    {
                        "bet_id": bet_id,
                        "expected": expected_price,
                        "actual": current_price,
                        "slippage_pct": round(slippage_pct, 2),
                    },
                )

                if not slippage_ok:
                    logger.warning(
                        f"[mirror] Polymarket bet {bet_id}: slippage {slippage_pct:.1f}% "
                        f"exceeds {max_slippage}% (expected={expected_price}, actual={current_price})"
                    )
                    return {
                        "bet_id": bet_id,
                        "status": "skipped",
                        "reason": "slippage",
                        "expected_price": expected_price,
                        "actual_price": current_price,
                        "slippage_pct": round(slippage_pct, 2),
                    }
        except Exception as e:
            logger.warning(f"[mirror] Could not read price for bet {bet_id}: {e}")

        # 4. Enter amount in the $0 input
        try:
            amount_input = page.locator('input[placeholder="$0"]').first
            await amount_input.click()
            await amount_input.fill("")
            await amount_input.type(str(amount), delay=50)
            await asyncio.sleep(0.5)
        except Exception as e:
            return {"bet_id": bet_id, "status": "failed", "reason": f"Could not enter amount: {e}"}

        # 5. Click the submit button ("Buy [TeamName]")
        # The submit button is a trading-button whose text starts with "Buy"
        # and is NOT a role="radio" button (those are the outcome selectors)
        try:
            submit_btn = page.locator('button.trading-button:not([role="radio"])').filter(has_text="Buy").first
            await submit_btn.click(timeout=5000)
            logger.info(f"[mirror] Clicked Buy button for bet {bet_id}")
            await asyncio.sleep(2)
        except Exception as e:
            return {"bet_id": bet_id, "status": "failed", "reason": f"Could not click Buy: {e}"}

        # 6. Wait for order to process (Magic wallet signing + CLOB submission)
        # The signing happens automatically for Magic wallets — no popup needed.
        # We wait and then check for the order confirmation via intercepted CLOB traffic.
        await asyncio.sleep(5)

        # 7. Check for success — look for order confirmation or position update
        try:
            success = await page.evaluate(
                "() => {"
                "  const text = document.body.innerText;"
                "  return text.includes('Order placed') || text.includes('Success') ||"
                "         text.includes('Confirmed') || text.includes('Position') ||"
                "         text.includes('Open order');"
                "}"
            )
            if success:
                logger.info(f"[mirror] Polymarket bet {bet_id} confirmed")
                result = {
                    "bet_id": bet_id,
                    "status": "placed",
                    "amount_usdc": amount,
                    "outcome": outcome,
                }
                self._notify("polymarket_bet_placed", result)
                return result
        except Exception:
            pass

        # Uncertain — report as placed but flag for manual verification
        logger.warning(f"[mirror] Polymarket bet {bet_id}: placement uncertain")
        result = {
            "bet_id": bet_id,
            "status": "placed",
            "amount_usdc": amount,
            "outcome": outcome,
            "note": "confirmation_uncertain",
        }
        self._notify("polymarket_bet_placed", result)
        return result

    def _notify(self, event_type: str, data: dict):
        """Publish SSE event if broadcaster is available."""
        if self.broadcaster:
            self.broadcaster.publish(event_type, data)
