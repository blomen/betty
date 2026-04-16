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


def _token_overlap(a: str, b: str) -> float:
    """Word-level overlap ratio between two strings."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa), len(sb))


_NAME_ODDS_TOL = 0.05  # 5% tolerance when event name matches


def _detect_settlements(db_pending: list[dict], history: list[dict]) -> list[dict]:
    """Three-tier matching: exact provider_bet_id → event name+odds → fuzzy odds+stake.

    Returns a list of settlement dicts: {bet_id, result, payout, match_method}.
    """
    settlements: list[dict] = []
    used_history: set[int] = set()  # indices of matched history entries

    # Build a set of open bet signatures to avoid false positives:
    # if a bet with the same odds+stake is still open in history, don't settle it
    # via fuzzy matching against a different settled bet.
    _open_sigs: set[tuple[float, float]] = set()
    for entry in history:
        h_status = (entry.get("status") or "").lower()
        if h_status in ("pending", "open", ""):
            h_odds = float(entry.get("odds", 0) or 0)
            h_stake = float(entry.get("stake", 0) or 0)
            if h_odds > 0:
                _open_sigs.add((round(h_odds, 2), round(h_stake, 2)))

    for bet in db_pending:
        bet_id = bet.get("bet_id") or bet.get("id")
        bet_provider_id = str(bet.get("provider_bet_id") or "")
        # Build event name from home_team/away_team if event_name not set
        bet_event = (bet.get("event_name") or "").lower().strip()
        if not bet_event:
            home = bet.get("home_team") or ""
            away = bet.get("away_team") or ""
            if home and away:
                bet_event = f"{home} v {away}".lower().strip()
        bet_odds = float(bet.get("odds", 0) or 0)
        bet_stake = float(bet.get("stake", 0) or 0)

        matched = None
        method = None

        for idx, entry in enumerate(history):
            if idx in used_history:
                continue
            h_status = (entry.get("status") or "").lower()
            if h_status in ("pending", "open", ""):
                continue

            # Tier 1: exact provider_bet_id match
            h_pid = str(entry.get("provider_bet_id") or "")
            if bet_provider_id and h_pid and bet_provider_id == h_pid:
                matched = (idx, entry)
                method = "id"
                break

            # Tier 2: event name match (+ odds if available)
            h_event = (entry.get("event_name") or "").lower().strip()
            # Normalize "vs." → "v" for comparison
            h_norm = h_event.replace(" vs. ", " v ").replace(" vs ", " v ")
            bet_norm = bet_event.replace(" vs. ", " v ").replace(" vs ", " v ")
            if (
                bet_norm
                and h_norm
                and (bet_norm in h_norm or h_norm in bet_norm or _token_overlap(bet_norm, h_norm) >= 0.5)
            ):
                h_odds = float(entry.get("odds", 0) or 0)
                # If both have odds, check they're close; if history has no odds (lost bet), accept name match alone
                if h_odds > 0 and bet_odds > 0 and abs(h_odds - bet_odds) / bet_odds > _NAME_ODDS_TOL:
                    continue  # odds don't match, try next
                # Also check stake is in the right ballpark (within 50%) to avoid false matches
                h_stake = float(entry.get("stake", 0) or 0)
                if h_stake > 0 and bet_stake > 0 and abs(h_stake - bet_stake) / bet_stake > 0.5:
                    continue
                matched = (idx, entry)
                method = "name"
                break

            # Tier 3: fuzzy odds+stake fallback
            h_odds = float(entry.get("odds", 0) or 0)
            h_stake = float(entry.get("stake", 0) or 0)
            if (
                bet_odds > 0
                and h_odds > 0
                and abs(h_odds - bet_odds) / bet_odds <= _ODDS_TOL
                and bet_stake > 0
                and h_stake > 0
                and abs(h_stake - bet_stake) / bet_stake <= _STAKE_TOL
            ):
                matched = (idx, entry)
                method = "fuzzy"
                break

        if matched:
            idx, entry = matched
            # Guard: if matched via fuzzy/name (not exact ID) and a bet with
            # the same odds is still open, this is likely a false positive —
            # the DB bet is the open one, not the settled one.
            if method != "id" and (round(bet_odds, 2), round(bet_stake, 2)) in _open_sigs:
                logger.info(
                    f"[settle] Skipping false positive: bet {bet_id} ({bet_odds}@{bet_stake}) "
                    f"matched settled entry via {method} but an open bet with same odds exists"
                )
                continue
            used_history.add(idx)
            settlements.append(
                {
                    "bet_id": bet_id,
                    "result": (entry.get("status") or "").lower(),
                    "payout": entry.get("payout"),
                    "match_method": method,
                    "provider_bet_id": str(entry.get("provider_bet_id") or ""),
                }
            )

    return settlements


# ---------------------------------------------------------------------------
# PendingLoop
# ---------------------------------------------------------------------------


class PendingLoop:
    """Periodically fetches pending bets and syncs settlement status per provider."""

    def __init__(
        self,
        browser: MirrorBrowser,
        broadcaster: MirrorBroadcaster,
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

        tasks = [self._sync_provider(pid, bets) for pid, bets in pending_by_provider.items()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_pending(self) -> dict[str, list[dict]]:
        """GET /api/opportunities/play/pending-bets → {provider_id: [bet, ...]}"""
        url = f"{self._proxy_url}/api/opportunities/play/pending-bets"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("[PendingLoop] failed to fetch pending bets")
            return {}

        # API returns {providers: [{provider_id, bets: [...]}, ...]}
        grouped: dict[str, list[dict]] = {}
        for prov in data.get("providers", []):
            pid = prov.get("provider_id")
            bets = prov.get("bets", [])
            if pid and bets:
                grouped[pid] = bets
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
                logger.debug(f"[PendingLoop] no open tab for {pid}, skipping (user must open it)")
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
                    "provider_bet_id": e.provider_bet_id,
                    "event_name": e.event_name,
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
        logger.info(f"[PendingLoop] {len(settlements)} settlements detected for {pid} — broadcasting for review")

        # 5. Broadcast to UI for user confirmation — don't auto-record
        self._broadcaster.publish(
            "settlements_detected",
            {
                "provider_id": pid,
                "settlements": settlements,
            },
        )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _record_settlements(self, pid: str, settlements: list[dict]) -> None:
        url = f"{self._proxy_url}/api/opportunities/play/settle-batch"
        batch = [
            {"bet_id": s["bet_id"], "result": s["result"]} for s in settlements if s.get("bet_id") and s.get("result")
        ]
        if not batch:
            logger.info(f"[PendingLoop] no valid settlements to record for {pid}")
            return
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    json=batch,
                    headers={_AUTH_HEADER: _AUTH_VALUE},
                )
                resp.raise_for_status()
                data = resp.json()
            logger.info(
                f"[PendingLoop] settlements recorded for {pid}: {data.get('settled', 0)}/{data.get('total', 0)}"
            )
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
