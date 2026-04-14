"""PlayLoop — multi-provider play coordinator.

Manages multiple ProviderRunners with shared per-cluster bet queues.
Each selected provider gets its own asyncio task that independently
handles login, settlement, navigation, and placement.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

from .browser import MirrorBrowser
from .pending_loop import _detect_settlements  # noqa: F401
from .sse import MirrorBroadcaster

logger = logging.getLogger(__name__)


def _bet_ns(bet: dict) -> SimpleNamespace:
    """Wrap a bet dict as a SimpleNamespace for workflow method calls.

    Flattens provider_meta fields (matchup_id, altenar_event_id, etc.) to
    top-level attributes so workflow methods can use getattr(bet, ...) regardless
    of whether bet is a dict (play loop) or a BetProxy object (direct API call).
    """
    meta = bet.get("provider_meta") or {}
    ns = SimpleNamespace(**bet)
    for k, v in meta.items():
        if not hasattr(ns, k):
            setattr(ns, k, v)
    if not hasattr(ns, "bet_id"):
        ns.bet_id = 0
    # Explicit Kambi fields — avoid collision with top-level event_id (canonical UUID)
    ns.kambi_event_id = meta.get("event_id", "")
    ns.kambi_outcome_id = meta.get("outcome_id", "")
    # Gecko V2 fields — same event_id key in provider_meta, different prefix
    ns.gecko_event_id = meta.get("event_id", "")
    return ns


# Cluster membership — same odds across all siblings
_CLUSTER_MEMBERS: dict[str, list[str]] = {
    "kambi": ["unibet", "leovegas", "expekt", "betmgm", "speedybet", "x3000", "goldenbull", "1x2"],
    "spectate": ["888sport", "mrgreen"],
    "altenar_main": ["betinia", "campobet", "lodur", "quickcasino", "swiper", "dbet"],
    "gecko_betsson": ["betsson", "nordicbet", "betsafe", "spelklubben"],
    "comeon_group": ["comeon", "lyllo", "hajper", "snabbare"],
}
_PROVIDER_TO_CLUSTER: dict[str, str] = {}
for _cname, _members in _CLUSTER_MEMBERS.items():
    for _m in _members:
        _PROVIDER_TO_CLUSTER[_m] = _cname

_AUTH_HEADER = "X-Nginx-Authenticated"
_AUTH_VALUE = "firevsports"

# State constants
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_PROVIDER_OPENING = "provider_opening"
STATE_LOGIN_WAITING = "login_waiting"
STATE_SETTLING = "settling"
STATE_NAVIGATING = "navigating"
STATE_READY = "ready"
STATE_PLACING = "placing"

LOGIN_POLL_INTERVAL = 5.0  # seconds between login checks
LOGIN_TIMEOUT = 120.0  # seconds to wait for login before skipping provider
DAILY_BET_CAP = 10  # max bets per soft provider per day
UNCAPPED_PROVIDERS = {"pinnacle", "polymarket", "cloudbet"}


class PlayLoop:
    """Multi-provider play coordinator.

    Manages multiple ProviderRunners with shared per-cluster bet queues.
    Each selected provider gets its own asyncio task.

    Usage:
        loop = PlayLoop(browser, broadcaster, proxy_url)
        loop.load_batch(bets, balances, provider_ids=["betsson", "unibet"])
        loop.start()

        # From UI:
        loop.skip(provider_id="betsson")  # skip current bet on betsson
        loop.stop()  # stop all runners
    """

    def __init__(self, browser: MirrorBrowser, broadcaster: MirrorBroadcaster, proxy_url: str):
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")

        # Shared state
        self.state: str = STATE_IDLE
        self._placed_today: dict[str, int] = {}
        self._blocked: set[tuple[str, str]] = set()

        # Per-cluster queues: cluster_name → list of bets
        self._cluster_queues: dict[str, list[dict]] = {}
        self._queue_total: int = 0

        # Active runners: provider_id → ProviderRunner
        self._runners: dict[str, Any] = {}
        self._provider_ids: list[str] = []
        self._coordinator_task: asyncio.Task | None = None

        # Backward compat — single provider fields used by old SSE handlers
        self.current_bet: dict | None = None
        self.provider_stats: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_batch(
        self,
        batch: list[dict],
        balances: dict[str, float],
        provider_ids: list[str] | None = None,
        start_provider: str | None = None,
    ) -> None:
        """Partition bets into per-cluster queues for the selected providers."""
        # Backward compat: single provider_id → list
        if provider_ids is None and start_provider:
            provider_ids = [start_provider]
        if not provider_ids:
            provider_ids = []

        # Determine which clusters are funded
        funded_clusters: set[str] = set()
        for pid, bal in balances.items():
            if bal > 0:
                cluster = _PROVIDER_TO_CLUSTER.get(pid)
                if cluster:
                    funded_clusters.add(cluster)
                funded_clusters.add(pid)

        def _is_funded(b: dict) -> bool:
            pid = b.get("provider_id", "")
            cluster = _PROVIDER_TO_CLUSTER.get(pid)
            return pid in funded_clusters or (cluster is not None and cluster in funded_clusters)

        filtered = [b for b in batch if _is_funded(b)]
        filtered.sort(key=lambda b: -b.get("edge_pct", 0.0))

        # Partition into cluster queues
        self._cluster_queues.clear()
        for bet in filtered:
            bet_pid = bet.get("provider_id", "")
            cluster = _PROVIDER_TO_CLUSTER.get(bet_pid, bet_pid)
            if cluster not in self._cluster_queues:
                self._cluster_queues[cluster] = []
            self._cluster_queues[cluster].append(bet)

        self._queue_total = len(filtered)
        self._provider_ids = provider_ids
        self._blocked.clear()
        self.provider_stats.clear()
        logger.info(
            f"[PlayCoordinator] Loaded {self._queue_total} bets into "
            f"{len(self._cluster_queues)} cluster queues for providers {provider_ids}"
        )

    def start(self) -> None:
        """Spawn ProviderRunners for all selected providers."""
        if self._coordinator_task and not self._coordinator_task.done():
            logger.warning("[PlayCoordinator] Already running")
            return
        self._coordinator_task = asyncio.create_task(self._run_coordinator(), name="play_coordinator")

    def stop(self) -> None:
        """Stop all runners and the coordinator."""
        for runner in self._runners.values():
            runner.stop()
        if self._coordinator_task and not self._coordinator_task.done():
            self._coordinator_task.cancel()
        self._coordinator_task = None
        self._runners.clear()
        self.state = STATE_IDLE
        self.current_bet = None
        self._broadcaster.publish("play_stopped", {})

    def place(self, provider_id: str | None = None) -> None:
        """Signal place for a specific runner (or first ready runner)."""
        runner = self._find_runner(provider_id, state=STATE_READY)
        if runner:
            runner._bet_intercepted_event.set()

    def skip(self, provider_id: str | None = None) -> None:
        """Signal skip for a specific runner (or first ready runner)."""
        runner = self._find_runner(provider_id, state=STATE_READY)
        if runner:
            runner.skip()

    def on_bet_intercepted(self, provider_id: str, body: dict, request_body: dict | None = None) -> None:
        """Route intercepted bet to the correct runner."""
        runner = self._runners.get(provider_id)
        if runner:
            runner.on_bet_intercepted(body, request_body)

    def confirm_settlements(self, confirmed: list[dict] | None = None) -> None:
        """No-op for parallel play — settlements auto-confirm in runners."""
        pass

    def get_status(self) -> dict:
        remaining = sum(len(q) for q in self._cluster_queues.values())
        return {
            "state": self.state,
            "current_bet": self.current_bet,
            "queue_remaining": remaining,
            "queue_total": self._queue_total,
            "provider_stats": self.provider_stats,
            "providers": {pid: r.get_status() for pid, r in self._runners.items()},
        }

    # ------------------------------------------------------------------
    # Coordinator loop
    # ------------------------------------------------------------------

    async def _run_coordinator(self) -> None:
        """Spawn runners and wait for all to complete."""
        self.state = STATE_RUNNING
        self._runners.clear()

        from .provider_runner import ProviderRunner

        for pid in self._provider_ids:
            cluster = _PROVIDER_TO_CLUSTER.get(pid, pid)
            if cluster not in self._cluster_queues:
                self._cluster_queues[cluster] = []

            runner = ProviderRunner(
                provider_id=pid,
                browser=self._browser,
                broadcaster=self._broadcaster,
                proxy_url=self._proxy_url,
                pop_bet=self._make_pop_bet(cluster),
                block_event_market=self._block_event_market,
                is_blocked=self._is_blocked,
                placed_today=self._placed_today,
            )
            self._runners[pid] = runner
            runner.start()

        # Wait for all runners to finish
        tasks = [r._task for r in self._runners.values() if r._task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Collect stats
        for pid, runner in self._runners.items():
            self.provider_stats[pid] = runner.stats

        self._broadcaster.publish("play_complete", {"provider_stats": self.provider_stats})
        logger.info("[PlayCoordinator] All runners complete")
        self.state = STATE_IDLE

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------

    def _make_pop_bet(self, cluster: str) -> callable:
        """Return a pop function for a specific cluster queue."""
        queue = self._cluster_queues[cluster]

        def pop() -> dict | None:
            if not queue:
                return None
            return queue.pop(0)

        return pop

    def _block_event_market(self, bet: dict) -> None:
        """Block event+market across all cluster queues after placement."""
        event_id = bet.get("event_id", "")
        market = bet.get("market", "")
        market_key = "moneyline" if market in ("1x2", "moneyline") else market
        block_key = (event_id, market_key)
        self._blocked.add(block_key)
        # Remove from all queues
        for cluster_name in list(self._cluster_queues.keys()):
            queue = self._cluster_queues[cluster_name]
            before = len(queue)
            self._cluster_queues[cluster_name] = [
                b
                for b in queue
                if (b.get("event_id"), "moneyline" if b.get("market") in ("1x2", "moneyline") else b.get("market"))
                != block_key
            ]
            removed = before - len(self._cluster_queues[cluster_name])
            if removed:
                logger.info(
                    f"[PlayCoordinator] Blocked {event_id} {market_key} — removed {removed} from {cluster_name}"
                )

    def _is_blocked(self, bet: dict) -> bool:
        """Check if an event+market has been placed already."""
        event_id = bet.get("event_id", "")
        market = bet.get("market", "")
        market_key = "moneyline" if market in ("1x2", "moneyline") else market
        return (event_id, market_key) in self._blocked

    def _find_runner(self, provider_id: str | None, state: str | None = None):
        """Find a runner by provider_id, or first runner in the given state."""
        if provider_id and provider_id in self._runners:
            return self._runners[provider_id]
        if state:
            for r in self._runners.values():
                if r.state == state:
                    return r
        return None
