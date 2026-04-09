"""PendingLoop — periodic settlement sync across all providers with pending bets."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .browser import MirrorBrowser
    from .sse import MirrorBroadcaster

logger = logging.getLogger(__name__)

_AUTH_HEADER = "X-Nginx-Authenticated"
_AUTH_VALUE = "firevsports"
_POLL_INTERVAL = 60  # seconds
_CONFIRM_TIMEOUT = 300  # seconds
_ODDS_TOL = 0.10  # 10% tolerance
_STAKE_TOL = 0.30  # 30% tolerance


# ---------------------------------------------------------------------------
# Module-level detection helper
# ---------------------------------------------------------------------------

def _detect_settlements(db_pending: list[dict], history: list[dict]) -> list[dict]:
    """Match DB pending bets against provider history entries by odds + stake.

    Matching criteria (fuzzy):
    - odds within 10% of each other
    - stake within 30% of each other
    - history entry status is NOT "pending"

    Returns a list of settlement dicts: {bet_id, result, payout}.
    """
    settlements: list[dict] = []
    for bet in db_pending:
        bet_odds = float(bet.get("odds", 0) or 0)
        bet_stake = float(bet.get("stake", 0) or 0)

        for entry in history:
            h_odds = float(entry.get("odds", 0) or 0)
            h_stake = float(entry.get("stake", 0) or 0)
            h_status = (entry.get("status") or "").lower()

            if h_status == "pending":
                continue

            if bet_odds > 0 and h_odds > 0:
                if abs(h_odds - bet_odds) / bet_odds > _ODDS_TOL:
                    continue
            if bet_stake > 0 and h_stake > 0:
                if abs(h_stake - bet_stake) / bet_stake > _STAKE_TOL:
                    continue

            settlements.append({
                "bet_id": bet["bet_id"],
                "result": h_status,
                "payout": entry.get("payout"),
            })
            break  # matched — move to next pending bet

    return settlements


# ---------------------------------------------------------------------------
# PendingLoop
# ---------------------------------------------------------------------------

class PendingLoop:
    """Periodically fetches pending bets and syncs settlement status per provider."""

    def __init__(
        self,
        browser: "MirrorBrowser",
        broadcaster: "MirrorBroadcaster",
        proxy_url: str,
    ):
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")
        self._task: asyncio.Task | None = None
        self._running = False
        self._confirm_events: dict[str, asyncio.Event] = {}
        self._status: dict[str, dict] = {}  # pid -> {last_sync, pending, settlements}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="pending_loop")
        logger.info("[PendingLoop] started")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[PendingLoop] stopped")

    def confirm(self, provider_id: str) -> None:
        """Signal that the user has confirmed settlements for a provider."""
        ev = self._confirm_events.get(provider_id)
        if ev:
            ev.set()
            logger.info(f"[PendingLoop] confirm received for {provider_id}")

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "providers": dict(self._status),
        }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while self._running:
            try:
                await self._sync_all()
            except Exception:
                logger.exception("[PendingLoop] error in _sync_all")
            await asyncio.sleep(_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Sync all providers
    # ------------------------------------------------------------------

    async def _sync_all(self) -> None:
        pending_by_provider = await self._fetch_pending()
        if not pending_by_provider:
            return

        tasks = [
            self._sync_provider(pid, bets)
            for pid, bets in pending_by_provider.items()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_pending(self) -> dict[str, list[dict]]:
        """GET /api/opportunities/play/pending-bets → {provider_id: [bet, ...]}"""
        url = f"{self._proxy_url}/api/opportunities/play/pending-bets"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
                data: list[dict] = resp.json()
        except Exception:
            logger.exception("[PendingLoop] failed to fetch pending bets")
            return {}

        grouped: dict[str, list[dict]] = {}
        for bet in data:
            pid = bet.get("provider_id")
            if pid:
                grouped.setdefault(pid, []).append(bet)
        return grouped

    # ------------------------------------------------------------------
    # Per-provider sync
    # ------------------------------------------------------------------

    async def _sync_provider(self, pid: str, db_bets: list[dict]) -> None:
        logger.info(f"[PendingLoop] syncing {pid} ({len(db_bets)} pending bets)")
        self._status.setdefault(pid, {})["pending"] = len(db_bets)

        # 1. Find / open provider tab
        page = None
        if self._browser.running and self._browser.context:
            from .workflows import get_workflow
            workflow = get_workflow(pid)
            page = await workflow.find_tab(self._browser.context)

            if page is None:
                try:
                    page = await self._browser.open_tab(f"https://{workflow.domain}")
                except Exception:
                    logger.warning(f"[PendingLoop] could not open tab for {pid}")
                    return

            # 2. Check login
            try:
                logged_in = await workflow.check_login(page)
                if not logged_in:
                    logger.warning(f"[PendingLoop] not logged in on {pid}")
                    self._broadcaster.publish("login_required", {"provider_id": pid})
                    return
            except Exception:
                logger.warning(f"[PendingLoop] check_login failed for {pid}")
                return

            # 3. Sync history
            try:
                raw_history = await workflow.sync_history(page)
            except Exception:
                logger.exception(f"[PendingLoop] sync_history failed for {pid}")
                return

            history = [
                {
                    "odds": e.odds,
                    "stake": e.stake,
                    "status": e.status,
                    "payout": e.payout,
                }
                for e in raw_history
            ]
        else:
            logger.warning(f"[PendingLoop] browser not running, skipping history for {pid}")
            return

        # 4. Detect settlements
        settlements = _detect_settlements(db_bets, history)
        self._status[pid]["last_sync"] = datetime.utcnow().isoformat()

        if not settlements:
            logger.info(f"[PendingLoop] no new settlements for {pid}")
            self._status[pid]["settlements"] = []
            return

        self._status[pid]["settlements"] = settlements
        logger.info(f"[PendingLoop] {len(settlements)} settlements detected for {pid}")

        # 5. Broadcast and wait for confirm
        self._broadcaster.publish("settlements_detected", {
            "provider_id": pid,
            "settlements": settlements,
        })

        ev = asyncio.Event()
        self._confirm_events[pid] = ev
        try:
            await asyncio.wait_for(ev.wait(), timeout=_CONFIRM_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"[PendingLoop] confirm timeout for {pid} — skipping")
            return
        finally:
            self._confirm_events.pop(pid, None)

        # 6. Record settlements
        await self._record_settlements(pid, settlements)
        self._broadcaster.publish("settlements_confirmed", {
            "provider_id": pid,
            "settlements": settlements,
        })

        # 7. Sync balance
        try:
            balance = await workflow.sync_balance(page)
            await self._post_balance(pid, balance)
        except Exception:
            logger.warning(f"[PendingLoop] balance sync failed for {pid}")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _record_settlements(self, pid: str, settlements: list[dict]) -> None:
        url = f"{self._proxy_url}/api/opportunities/play/settle-confirm"
        payload = {"provider_id": pid, "settlements": settlements}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={_AUTH_HEADER: _AUTH_VALUE},
                )
                resp.raise_for_status()
            logger.info(f"[PendingLoop] settlements recorded for {pid}")
        except Exception:
            logger.exception(f"[PendingLoop] failed to record settlements for {pid}")

    async def _post_balance(self, pid: str, balance: float) -> None:
        url = f"{self._proxy_url}/api/bankroll/set/{pid}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    json={"balance": balance},
                    headers={_AUTH_HEADER: _AUTH_VALUE},
                )
                resp.raise_for_status()
            logger.info(f"[PendingLoop] balance posted for {pid}: {balance}")
        except Exception:
            logger.warning(f"[PendingLoop] failed to post balance for {pid}")
