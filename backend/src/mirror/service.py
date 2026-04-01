"""MirrorService — orchestrates bet interception, parsing, storage, and notification."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, unquote

from pathlib import Path

from ..db.models import get_session, BetTrace, Bet
from ..services.bet_service import BetService
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
        self.interceptor = BetInterceptor(
            on_bet_response=self._handle_bet_response,
            on_event_data=self._handle_event_data,
            on_bet_history=self._handle_bet_history,
            on_financial_data=self._handle_financial_data,
            on_provider_detected=self._handle_provider_detected,
            on_notification_settings=self._handle_notification_settings,
        )

    async def start(self, site_url: str | None = None):
        """Start the mirror browser."""
        await self.interceptor.start()

    async def stop(self):
        """Stop the mirror browser."""
        await self.interceptor.stop()

    # Verified SSR bet history — need DOM scraping, not API interception
    # Only unibet confirmed; other Kambi operators may have XHR — verify before adding
    _SSR_PROVIDERS = frozenset({"unibet"})

    async def _handle_provider_detected(self, provider_id: str):
        """Fires when user navigates to a known provider site."""
        info = await asyncio.to_thread(self._get_provider_sync_info, provider_id)
        logger.info(
            f"[mirror] Sync available for {provider_id}: "
            f"balance={info['balance']}, pending={info['pending_bets']}"
        )
        self._notify("sync_available", {
            "provider": provider_id,
            "balance": info["balance"],
            "pending_bets": info["pending_bets"],
            "pending_stake": info["pending_stake"],
        })
        # Auto-mute notifications if we have a recipe
        await self._replay_notification_mute(provider_id)
        # Polymarket: scrape cash balance from DOM (not available via API)
        if provider_id == "polymarket":
            asyncio.ensure_future(self._scrape_polymarket_balance())
        # Auto-scrape bet history for SSR providers when pending bets exist
        if provider_id in self._SSR_PROVIDERS and info["pending_bets"] > 0:
            asyncio.ensure_future(self._auto_scrape_bet_history(provider_id))

    async def _scrape_polymarket_balance(self):
        """Scrape USDC cash balance from Polymarket DOM.

        The cash balance is rendered client-side (not from a single API endpoint).
        The nav shows "Cash$101.51" — we extract the dollar amount from that text.
        """
        await asyncio.sleep(3)  # Wait for page to render

        context = self.interceptor.context
        if not context or not context.pages:
            return

        page = context.pages[0]
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
                logger.info(f"[mirror] Polymarket cash balance from DOM: ${balance}")
                await asyncio.to_thread(self._sync_balance, "polymarket", balance)
            else:
                logger.debug("[mirror] Could not find Polymarket cash balance in DOM")
        except Exception as e:
            logger.warning(f"[mirror] Could not scrape Polymarket balance: {e}")

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
            r'Singel\s*@\s*([\d.]+)\s+'
            r'(Vinst|F.rlust|Oavgjord|Cashout)\s+'
            r'(\d+ \w+ \d{4})\s*.\s*([\d:]+)\s+'
            r'Kupong-Id:\s*(\d+)\s+'
            r'(.*?)'
            r'Insats:\s*([\d.,]+)\s*kr'
            r'(?:\s*Utbetalning:\s*([\d.,]+)\s*kr)?',
            re.DOTALL
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
            scraped.append({
                "odds": float(m.group(1)),
                "result": result,
                "stake": float(m.group(7).replace(",", ".")),
                "payout": float(m.group(8).replace(",", ".")) if m.group(8) else 0,
                "event": m.group(6).strip().replace("\n", " ")[:80],
            })

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
                    staged.append({
                        "bet_id": pb["id"],
                        "provider": provider_id,
                        "event": sb["event"],
                        "odds": sb["odds"],
                        "stake": sb["stake"],
                        "result": sb["result"],
                        "payout": sb["payout"],
                    })
                    break

        if staged:
            self._pending_settlements = staged
            wins = [s for s in staged if s["result"] == "won"]
            losses = [s for s in staged if s["result"] == "lost"]
            logger.info(
                f"[mirror] Staged {len(staged)} SSR settlement(s) from {provider_id}: "
                f"{len(wins)}W {len(losses)}L"
            )
            self._notify("settlements_pending", {
                "provider": provider_id,
                "count": len(staged),
                "wins": len(wins),
                "losses": len(losses),
                "total_staked": sum(s["stake"] for s in staged),
                "total_payout": sum(s["payout"] for s in staged),
                "net": sum(s["payout"] for s in staged) - sum(s["stake"] for s in staged),
                "settlements": staged,
            })

    def _get_pending_bets_sync(self, provider_id: str) -> list[dict]:
        """Get pending bets for a provider."""
        from ..repositories.profile_repo import ProfileRepo
        db = get_session()
        try:
            profile = ProfileRepo(db).get_active()
            pending = db.query(Bet).filter(
                Bet.profile_id == profile.id,
                Bet.provider_id == provider_id,
                Bet.result == "pending",
            ).all()
            return [{"id": b.id, "odds": b.odds, "stake": b.stake} for b in pending]
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
            pending = db.query(Bet).filter(
                Bet.provider_id == provider_id,
                Bet.result == "pending",
            ).all()
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
        return self.interceptor.get_status()

    async def _handle_event_data(self, url: str, response_body: str):
        """Cache event participant data from events-table API responses."""
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

        # Gecko V2 coupon-history format: normalize to Altenar-compatible format
        if "coupon-history" in url:
            coupons = data.get("data", {}).get("coupons", [])
            # Always store trace for debugging
            provider_id = self._detect_provider(url)
            await asyncio.to_thread(
                self._store_trace_sync, provider_id, url, request_body, response_body, "history"
            )
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
                bets.append({
                    "status": status_code,
                    "totalStake": c.get("stake", 0),
                    "totalOdds": c.get("totalOdds", 0),
                    "totalWin": c.get("totalPayout", 0),
                    "eventName": event_name,
                })
        else:
            bets = data.get("bets", [])

        if not bets:
            return

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
            self._notify("settlements_pending", {
                "provider": provider_id,
                "count": len(staged),
                "wins": len(wins),
                "losses": len(losses),
                "total_staked": total_staked,
                "total_payout": total_payout,
                "net": net,
                "settlements": staged,
            })

        # Also store trace for audit
        await asyncio.to_thread(
            self._store_trace_sync, provider_id, url, request_body, response_body, "history"
        )

    def _stage_settlements_sync(self, history_bets: list[dict], provider_id: str) -> list[dict]:
        """Match bet history against pending bets — stage for confirmation, don't commit."""
        STATUS_MAP = {1: "won", 2: "lost", 3: "void", 4: "cashout"}

        db = get_session()
        staged: list[dict] = []
        try:
            pending = db.query(Bet).filter(
                Bet.result == "pending",
                Bet.provider_id == provider_id,
            ).all()
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

                matched_bet = None
                for bet in pending:
                    if bet.result != "pending":
                        continue
                    if abs(bet.stake - stake) > 0.01:
                        continue
                    if abs(bet.odds - odds) > 0.01:
                        continue
                    matched_bet = bet
                    break

                if not matched_bet:
                    continue

                pending.remove(matched_bet)
                staged.append({
                    "bet_id": matched_bet.id,
                    "provider": provider_id,
                    "event": event_name,
                    "odds": odds,
                    "stake": stake,
                    "result": result,
                    "payout": payout,
                })

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
                    f"[mirror] Confirmed: bet #{s['bet_id']} {s['event']} "
                    f"→ {s['result']} (payout={s['payout']})"
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
        self._notify("settlements_confirmed", {
            "provider": provider_id,
            "settled": settled,
        })
        # Reset provider detection so balance re-syncs on next page visit
        self.interceptor.reset_detected_providers()

        return {"settled": settled, "provider": provider_id, "settlements": summary}

    def reject_settlements(self) -> dict:
        """Discard all pending settlements."""
        count = len(self._pending_settlements)
        self._pending_settlements.clear()
        return {"rejected": count}

    def settle_polymarket_bets(self) -> list[dict]:
        """Check for resolved Polymarket markets and stage settlements for pending bets.

        Uses the Gamma API (via PolymarketRetriever.fetch_resolved) to find finished events,
        then matches against pending Polymarket bets.
        """
        from ..db.models import get_session, Bet, Odds, Event
        from ..repositories.profile_repo import ProfileRepo

        db = get_session()
        staged = []
        try:
            profile = ProfileRepo(db).get_active()
            pending = db.query(Bet).filter(
                Bet.profile_id == profile.id,
                Bet.provider_id == "polymarket",
                Bet.result == "pending",
            ).all()

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
                odds = db.query(Odds).filter(
                    Odds.event_id == bet.event_id,
                    Odds.provider == "polymarket",
                    Odds.market == bet.market,
                    Odds.outcome == bet.outcome,
                ).first()

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
                    staged.append({
                        "bet_id": bet.id,
                        "provider": "polymarket",
                        "event": (event.home_team or "") + " vs " + (event.away_team or "") if event.home_team else "Unknown",
                        "odds": bet.odds,
                        "stake": bet.stake,
                        "result": result,
                        "payout": payout,
                    })

        except Exception as e:
            logger.error(f"[mirror] Polymarket settlement check failed: {e}", exc_info=True)
        finally:
            db.close()

        if staged:
            self._pending_settlements.extend(staged)
            self._notify("settlements_pending", {
                "provider": "polymarket",
                "count": len(staged),
                "wins": len([s for s in staged if s["result"] == "won"]),
                "losses": len([s for s in staged if s["result"] == "lost"]),
                "total_staked": sum(s["stake"] for s in staged),
                "total_payout": sum(s["payout"] for s in staged),
                "net": sum(s["payout"] for s in staged) - sum(s["stake"] for s in staged),
                "settlements": staged,
            })

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
            # Polymarket /value returns portfolio value (positions), not cash.
            # If it's 0 or we're on Polymarket, also scrape cash from DOM.
            if provider_id == "polymarket" and balance == 0:
                await self._scrape_polymarket_balance()
            else:
                await asyncio.to_thread(self._sync_balance, provider_id, balance)

        # Polymarket: store deposit trace from Swapped widget
        if "swapped.com" in url and "create_order" in url:
            deposit = self.polymarket_parser.parse_deposit(url, response_body)
            if deposit:
                logger.info(f"[mirror] Polymarket deposit initiated: ${deposit['amount']} {deposit['currency']}")
                self._notify("deposit_initiated", {
                    "provider": "polymarket",
                    "amount": deposit["amount"],
                    "currency": deposit["currency"],
                    "order_id": deposit["order_id"],
                })

        # Polymarket: parse and broadcast open orders
        if "clob.polymarket.com/data/orders" in url:
            orders = self.polymarket_parser.parse_orders(response_body)
            if orders:
                self._notify("polymarket_orders", {
                    "orders": orders,
                    "count": len(orders),
                    "open": len([o for o in orders if o["status"] == "live"]),
                })

        # Store trace for audit
        await asyncio.to_thread(
            self._store_trace_sync, provider_id, url, None, response_body, "balance"
        )

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
            for currency, parts in balances.items():
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
                logger.info(
                    f"[mirror] Balance synced: {provider_id} "
                    f"{old_balance:.2f} → {balance:.2f} SEK"
                )
                delta = balance - (old_balance or 0)
                event_data = {
                    "provider": provider_id,
                    "balance": balance,
                    "previous": old_balance,
                    "delta": round(delta, 2),
                }
                self._notify("balance_synced", event_data)
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
        provider_id = (
            self._detect_provider_from_request(request_body)
            or self._detect_provider(url)
        )

        try:
            body = json.loads(response_body)
        except json.JSONDecodeError:
            logger.warning(f"[mirror] Invalid JSON response from {url}")
            await asyncio.to_thread(self._store_trace_sync, provider_id, url, request_body, response_body, "failed")
            return

        # Try to extract basic bet info from any platform
        bet_info = self._extract_bet_info(url, body, request_body)

        # Store the raw trace
        await asyncio.to_thread(
            self._store_trace_sync, provider_id, url, request_body, response_body, "bet_placed"
        )

        # Toast notification with whatever info we could extract
        toast = {
            "status": "ok",
            "provider": provider_id,
            "event": bet_info.get("event_name", "Unknown event"),
            "market": bet_info.get("market"),
            "outcome": bet_info.get("outcome"),
            "odds": bet_info.get("odds"),
            "stake": bet_info.get("stake"),
            "matched": False,
        }
        logger.info(
            f"[mirror] Bet recorded: {provider_id} — {toast['event']} "
            f"@ {toast['odds']} × {toast['stake']}"
        )
        self._notify("bet_mirrored", toast)

    def _extract_bet_info(self, url: str, body: dict, request_body: str | None) -> dict:
        """Best-effort extraction of bet info from any platform response.

        Returns dict with whatever fields could be extracted:
        event_name, odds, stake, market, outcome, confirmation_id
        """
        info: dict[str, Any] = {}
        req: dict = {}
        if request_body:
            try:
                req = json.loads(request_body) if isinstance(request_body, str) else request_body
            except (json.JSONDecodeError, TypeError):
                pass

        url_lower = url.lower()

        # --- Altenar (placeWidget) ---
        if "placewidget" in url_lower:
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
            # Request has richer data
            markets = req.get("betMarkets", [])
            if markets and not info.get("event_name"):
                m = markets[0]
                info["event_name"] = m.get("eventName", "")
                odds_list = m.get("odds", [])
                if odds_list:
                    info["outcome"] = odds_list[0].get("selectionName", "")
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
            match = re.search(r'/([^/]+?)(?:-vs?-|%20vs?%20)([^/]+?)(?:-[a-zA-Z0-9_]{10,})?/?$', path, re.IGNORECASE)
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
                    away = re.split(r'\s*[|–—]\s*', parts[1])[0].strip()
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
                db=db, provider_id=provider_id, url=url,
                request_body=request_body, response_body=response_body,
                parse_status=parse_status, provider_bet_id=confirmation_id, bet_id=bet_id,
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
        self, db, provider_id: str, url: str, request_body: str | None, response_body: str,
        parse_status: str, provider_bet_id: str | None = None, bet_id: int | None = None,
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
        from ..db.models import Event
        from rapidfuzz import fuzz
        from datetime import timedelta

        home = parsed.get("home_team")
        away = parsed.get("away_team")
        if not home or not away:
            logger.warning(f"[mirror] Cannot match — no team names resolved")
            return None

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=7)
        events = db.query(Event).filter(
            Event.home_team.isnot(None),
            Event.away_team.isnot(None),
            Event.start_time >= now - timedelta(hours=3),
            Event.start_time <= cutoff,
        ).all()

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
        self, url: str, method: str, request_body: str | None,
        response_body: str, content_type: str,
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
        self._notify("notification_recipe_captured", {
            "provider": provider_id,
            "method": method,
            "url": url,
        })

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
                logger.warning(f"[mirror] Mute replay failed for {provider_id} (HTTP {resp.status}) — recipe marked stale")
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

    async def scan_polymarket_bets(self, bets: list[dict]) -> dict:
        """Scan Polymarket markets — navigate to each, read live prices, report deltas.

        Returns: {scanned: [{bet_id, slug, outcome, expected_price, live_price, delta_pct, live_odds, expected_odds, stake, status}]}
        """
        import asyncio

        context = self.interceptor.context
        if not context or not context.pages:
            return {"error": "No mirror browser open", "scanned": []}

        page = context.pages[0]
        scanned = []

        for bet in bets:
            bet_id = bet["bet_id"]
            slug = bet["market_slug"]
            outcome = bet["outcome"]
            expected_price = bet["expected_price"]
            amount = bet["amount_usdc"]
            original_outcome = bet.get("_original_outcome", outcome).lower()
            market_type = bet.get("_market_type", "")

            # Determine button index
            if original_outcome in ("home", "over"):
                btn_index = 0
            elif original_outcome == "draw":
                btn_index = 1
            elif original_outcome in ("away", "under"):
                btn_index = 2 if market_type == "1x2" else 1
            else:
                btn_index = 0

            # Navigate to market page
            slug_parts = slug.split("-")
            league = slug_parts[0] if slug_parts else ""
            market_url = f"https://polymarket.com/sports/{league}/{slug}"
            logger.info(f"[mirror] Scanning bet {bet_id}: {market_url}")

            try:
                await page.goto(market_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_selector('button.trading-button', timeout=15000)
            except Exception as e:
                scanned.append({
                    "bet_id": bet_id, "slug": slug, "outcome": outcome,
                    "expected_price": expected_price, "expected_odds": bet.get("original_odds", 0),
                    "live_price": None, "live_odds": None, "delta_pct": None,
                    "stake": amount, "status": "error", "reason": f"Page load failed: {e}",
                })
                continue

            # Read all trading button prices
            try:
                btn_data = await page.evaluate(
                    "() => {"
                    "  const btns = [...document.querySelectorAll('button.trading-button')];"
                    "  return btns.map(b => {"
                    "    const text = b.textContent || '';"
                    "    const priceMatch = text.match(/([\\d.]+)\\u00a2/);"
                    "    const price = priceMatch ? parseFloat(priceMatch[1]) / 100 : null;"
                    "    return {text: text.trim().slice(0, 40), price};"
                    "  });"
                    "}"
                )

                if btn_index < len(btn_data) and btn_data[btn_index]["price"] is not None:
                    live_price = btn_data[btn_index]["price"]
                    live_odds = round(1 / live_price, 2) if live_price > 0.01 else 999
                    expected_odds = bet.get("original_odds", round(1 / expected_price, 2) if expected_price > 0 else 0)
                    delta_pct = round((live_price - expected_price) / expected_price * 100, 1) if expected_price > 0 else 0

                    scanned.append({
                        "bet_id": bet_id,
                        "slug": slug,
                        "outcome": outcome,
                        "button_text": btn_data[btn_index]["text"],
                        "expected_price": expected_price,
                        "expected_odds": expected_odds,
                        "live_price": live_price,
                        "live_odds": live_odds,
                        "delta_pct": delta_pct,
                        "stake": amount,
                        "all_buttons": [b["text"] for b in btn_data],
                        "status": "ok",
                    })
                else:
                    scanned.append({
                        "bet_id": bet_id, "slug": slug, "outcome": outcome,
                        "expected_price": expected_price, "expected_odds": bet.get("original_odds", 0),
                        "live_price": None, "live_odds": None, "delta_pct": None,
                        "stake": amount, "all_buttons": [b["text"] for b in btn_data],
                        "status": "error", "reason": f"No price at button index {btn_index}",
                    })
            except Exception as e:
                scanned.append({
                    "bet_id": bet_id, "slug": slug, "outcome": outcome,
                    "expected_price": expected_price, "expected_odds": bet.get("original_odds", 0),
                    "live_price": None, "live_odds": None, "delta_pct": None,
                    "stake": amount, "status": "error", "reason": str(e),
                })

        self._notify("polymarket_scan_complete", {"scanned": scanned})
        return {"scanned": scanned}

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

            self._notify("polymarket_bet_placing", {
                "bet_id": bet_id, "market_slug": slug,
                "outcome": outcome, "amount": amount,
            })

            try:
                original_outcome = bet.get("_original_outcome", outcome)
                market_type = bet.get("_market_type", "")
                result = await self._place_single_polymarket_bet(
                    page, bet_id, slug, outcome, amount, expected_price, max_slippage,
                    original_outcome=original_outcome, market_type=market_type,
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
        self._notify("polymarket_batch_complete", {
            "placed": len(placed), "skipped": len(skipped),
            "failed": len(failed), "total": len(bets),
        })
        return summary

    async def _place_single_polymarket_bet(
        self, page, bet_id: int, slug: str, outcome: str,
        amount: float, expected_price: float, max_slippage: float,
        original_outcome: str = "", market_type: str = "",
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
        slug_parts = slug.split("-")
        league = slug_parts[0] if slug_parts else ""
        market_url = f"https://polymarket.com/sports/{league}/{slug}"
        logger.info(f"[mirror] Placing Polymarket bet {bet_id}: {market_url} {outcome} ${amount}")
        await page.goto(market_url, wait_until="domcontentloaded", timeout=30000)

        # Wait for trading buttons to appear (React hydration)
        try:
            await page.wait_for_selector('button.trading-button', timeout=15000)
        except Exception:
            await asyncio.sleep(5)  # Fallback wait

        # 2. Click the outcome's trading-button on the market card
        # Buttons use abbreviations (e.g. "juv27¢") not full team names.
        # Strategy: use positional index based on outcome type:
        #   1x2:        home=0, draw=1, away=2
        #   moneyline:  home=0, away=1
        #   spread/total: home=0, away=1 (or over=0, under=1)
        outcome_lower = (original_outcome or outcome).lower()
        try:
            if outcome_lower in ("home", "over"):
                btn_index = 0
            elif outcome_lower == "draw":
                btn_index = 1
            elif outcome_lower in ("away", "under"):
                # For 1x2 away is index 2, for moneyline it's index 1
                btn_index = 2 if market_type == "1x2" else 1
            else:
                btn_index = 0  # fallback

            clicked = await page.evaluate(
                "(idx) => {"
                "  const btns = document.querySelectorAll('button.trading-button');"
                "  if (idx < btns.length) {"
                "    btns[idx].click();"
                "    return btns[idx].textContent.trim().slice(0, 40);"
                "  }"
                "  return null;"
                "}",
                btn_index,
            )
            if not clicked:
                return {"bet_id": bet_id, "status": "failed", "reason": f"No trading button at index {btn_index} for '{outcome}'"}
            logger.info(f"[mirror] Clicked outcome button #{btn_index}: '{clicked}' for bet {bet_id}")
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

                self._notify("polymarket_bet_price_check", {
                    "bet_id": bet_id, "expected": expected_price,
                    "actual": current_price, "slippage_pct": round(slippage_pct, 2),
                })

                if not slippage_ok:
                    logger.warning(
                        f"[mirror] Polymarket bet {bet_id}: slippage {slippage_pct:.1f}% "
                        f"exceeds {max_slippage}% (expected={expected_price}, actual={current_price})"
                    )
                    return {
                        "bet_id": bet_id, "status": "skipped", "reason": "slippage",
                        "expected_price": expected_price, "actual_price": current_price,
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
                    "bet_id": bet_id, "status": "placed",
                    "amount_usdc": amount, "outcome": outcome,
                }
                self._notify("polymarket_bet_placed", result)
                return result
        except Exception:
            pass

        # Uncertain — report as placed but flag for manual verification
        logger.warning(f"[mirror] Polymarket bet {bet_id}: placement uncertain")
        result = {
            "bet_id": bet_id, "status": "placed",
            "amount_usdc": amount, "outcome": outcome,
            "note": "confirmation_uncertain",
        }
        self._notify("polymarket_bet_placed", result)
        return result

    def _notify(self, event_type: str, data: dict):
        """Publish SSE event if broadcaster is available."""
        if self.broadcaster:
            self.broadcaster.publish(event_type, data)
