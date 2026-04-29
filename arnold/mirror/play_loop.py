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
    # Interwetten fields
    ns.interwetten_event_id = meta.get("event_id", "")
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
_AUTH_VALUE = "arnoldsports"

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

# Unlimited providers — play value bets via ProviderRunner (no arb required; they don't limit).
# Everything else (soft books) routes through ArbRunner for arb-only placement.
UNLIMITED_PROVIDERS = {"pinnacle", "polymarket", "cloudbet", "kalshi"}
UNCAPPED_PROVIDERS = UNLIMITED_PROVIDERS  # backward-compat alias for existing imports


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
        # Per-provider stake cap learned from limit responses (e.g. Unibet caps 70 SEK)
        self._stake_caps: dict[str, float] = {}
        # Recently-skipped bets: (event_id, market_key, outcome) → unix_ts of skip.
        # _refresh_batch checks this to avoid re-adding a bet we just dethroned —
        # without it the cascade pops the same top bet, dethrones it, refresh
        # re-adds it, and the loop pops it AGAIN, causing the runner to drain
        # the entire queue down to its lowest-edge bet within minutes.
        self._recently_skipped: dict[tuple[str, str, str], float] = {}
        # Short TTL — only used for USER-initiated skips. Auto-skips (dethrone,
        # READY-timeout) don't mark, so the runner can immediately come back to
        # the top edge if it's still top. 60s is enough that user skip ≈
        # "show me other options for a minute" without locking out the actual
        # top-edge bet for too long.
        self._recently_skipped_ttl_s: float = 60.0

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
            if pid in UNCAPPED_PROVIDERS:
                return True  # Uncapped providers (pinnacle, polymarket, cloudbet) always eligible
            cluster = _PROVIDER_TO_CLUSTER.get(pid)
            return pid in funded_clusters or (cluster is not None and cluster in funded_clusters)

        filtered = [b for b in batch if _is_funded(b)]
        filtered.sort(key=lambda b: -b.get("edge_pct", 0.0))

        # Partition into cluster queues (merge, don't clear — supports adding mid-session)
        for bet in filtered:
            bet_pid = bet.get("provider_id", "")
            cluster = _PROVIDER_TO_CLUSTER.get(bet_pid, bet_pid)
            if cluster not in self._cluster_queues:
                self._cluster_queues[cluster] = []
            # Avoid duplicates when re-loading for added providers
            existing_keys = {
                (b.get("event_id"), b.get("market"), b.get("outcome")) for b in self._cluster_queues[cluster]
            }
            if (bet.get("event_id"), bet.get("market"), bet.get("outcome")) not in existing_keys:
                self._cluster_queues[cluster].append(bet)

        self._queue_total = sum(len(q) for q in self._cluster_queues.values())
        self._provider_ids = provider_ids
        logger.info(
            f"[PlayCoordinator] Loaded {self._queue_total} bets into "
            f"{len(self._cluster_queues)} cluster queues for providers {provider_ids} "
            f"| batch_size={len(batch)} filtered={len(filtered)} "
            f"| queues={{{', '.join(f'{k}: {len(v)}' for k, v in self._cluster_queues.items())}}}"
        )

    def start(self) -> None:
        """Spawn ProviderRunners for all selected providers.

        If already running, adds runners for any new provider_ids that don't
        already have an active runner (supports adding providers mid-session).
        """
        if self._coordinator_task and not self._coordinator_task.done():
            # Already running — add new providers dynamically
            self._add_new_runners()
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
        """Route intercepted bet to the correct runner, or record directly as fallback."""
        from .arb_runner import STATE_AWAITING_HEDGES, STATE_LOADING_LEGS, STATE_STANDBY

        # Anchor case: runner for this provider, in soft-anchor state
        runner = self._runners.get(provider_id)
        if (
            runner
            and getattr(runner, "_anchor_event", None) is not None
            and runner.state in (STATE_STANDBY, STATE_LOADING_LEGS)
        ):
            runner.on_bet_intercepted(body, request_body)
            return
        # Counter case: another runner is awaiting hedges and this provider is one of its counters
        for r in self._runners.values():
            counter_events = getattr(r, "_counter_events", None) or {}
            if provider_id in counter_events and r.state == STATE_AWAITING_HEDGES:
                r.on_counter_bet_intercepted(provider_id, body, request_body)
                return
        if runner:
            runner.on_bet_intercepted(body, request_body)
            return
        logger.warning(f"[PlayCoordinator] Bet intercepted for {provider_id} — no runner matched")

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

    async def _refresh_batch(self) -> None:
        """Fetch fresh opportunities and merge new ones into cluster queues.

        Uses POST /api/opportunities/play/batch — same endpoint the React
        frontend uses. Previous code called GET /api/play/batch which returns
        404 (no such route), so refresh was silently failing for the entire
        play session, leaving the queue draining permanently as bets got
        popped without replenishment.
        """
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._proxy_url}/api/opportunities/play/batch",
                    headers={_AUTH_HEADER: _AUTH_VALUE, "Content-Type": "application/json"},
                    json={},
                )
                if resp.status_code != 200:
                    return
                data = resp.json()
                fresh_bets = data.get("batch") or data.get("bets") or []
                if not fresh_bets:
                    return

                added = 0
                touched_clusters: set[str] = set()
                for bet in fresh_bets:
                    pid = bet.get("provider_id", "")
                    cluster = _PROVIDER_TO_CLUSTER.get(pid, pid)
                    if cluster not in self._cluster_queues:
                        continue
                    queue = self._cluster_queues[cluster]
                    key = (bet.get("event_id"), bet.get("market"), bet.get("outcome"))
                    existing = {(b.get("event_id"), b.get("market"), b.get("outcome")) for b in queue}
                    if key not in existing and not self._is_blocked(bet) and not self._is_recently_skipped(bet):
                        queue.append(bet)
                        touched_clusters.add(cluster)
                        added += 1

                # Re-sort queues by descending edge so the top-edge bet is always at
                # the front. Without this, newly-added bets land at the tail and the
                # runner could pop a stale lower-edge bet next.
                for cluster in touched_clusters:
                    self._cluster_queues[cluster].sort(
                        key=lambda b: float(b.get("edge_pct") or 0),
                        reverse=True,
                    )

                if added:
                    self._queue_total = sum(len(q) for q in self._cluster_queues.values())
                    logger.info(f"[PlayCoordinator] Batch refresh: added {added} new bets (total={self._queue_total})")
        except Exception as e:
            logger.debug(f"[PlayCoordinator] Batch refresh failed: {e}")

    async def _run_coordinator(self) -> None:
        """Spawn runners and wait for all to complete.

        Polls periodically so dynamically-added runners are picked up.
        Refreshes batch every 30s to pick up new opportunities.
        """
        self.state = STATE_RUNNING
        self._runners.clear()
        self._spawn_runners(self._provider_ids)

        # Refresh frequently so newly-emerging high-edge opps reach the runner
        # without us sitting on a stale READY bet for half a minute.
        _BATCH_REFRESH_INTERVAL = 10.0
        _last_refresh = asyncio.get_event_loop().time()

        # Poll until all runners are done (supports dynamically added runners)
        while True:
            active = [r for r in self._runners.values() if r.running]
            if not active:
                break

            # Refresh batch periodically
            now = asyncio.get_event_loop().time()
            if now - _last_refresh >= _BATCH_REFRESH_INTERVAL:
                await self._refresh_batch()
                _last_refresh = now

            # Wait for any active runner to finish, then re-check
            tasks = [r._task for r in active if r._task]
            if tasks:
                try:
                    _done, _pending = await asyncio.wait(tasks, timeout=5.0, return_when=asyncio.FIRST_COMPLETED)
                except Exception:
                    await asyncio.sleep(1)
            else:
                await asyncio.sleep(1)

        # Collect stats
        for pid, runner in self._runners.items():
            self.provider_stats[pid] = runner.stats

        self._broadcaster.publish("play_complete", {"provider_stats": self.provider_stats})
        logger.info("[PlayCoordinator] All runners complete")
        self.state = STATE_IDLE

    def _spawn_runners(self, provider_ids: list[str]) -> None:
        """Create and start runners for providers that don't have one yet.

        Routing: UNLIMITED_PROVIDERS (pinnacle/poly/cloudbet) play value bets via
        ProviderRunner. All other (soft) providers play arb-only via ArbRunner.
        """
        from .arb_runner import ArbRunner
        from .provider_runner import ProviderRunner

        # ArbRunner needs the full active-provider set to know its counter pool
        active = list(provider_ids)

        for pid in provider_ids:
            if pid in self._runners and self._runners[pid].running:
                continue  # Already has an active runner

            is_unlimited = pid in UNLIMITED_PROVIDERS

            if is_unlimited:
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
                    peek_top_edge=self._make_peek_top_edge(cluster),
                    stake_caps=self._stake_caps,
                    mark_recently_skipped=self._mark_recently_skipped,
                )
            else:
                runner = ArbRunner(
                    provider_id=pid,
                    browser=self._browser,
                    broadcaster=self._broadcaster,
                    proxy_url=self._proxy_url,
                    block_event_market=self._block_event_market,
                    is_blocked=self._is_blocked,
                    placed_today=self._placed_today,
                    active_providers=active,
                    stake_caps=self._stake_caps,
                )
            self._runners[pid] = runner
            runner.start()
            logger.info(f"[PlayCoordinator] Spawned {'ProviderRunner' if is_unlimited else 'ArbRunner'} for {pid}")

    def _add_new_runners(self) -> None:
        """Add runners for newly-selected providers while coordinator is running."""
        self._spawn_runners(self._provider_ids)

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------

    def _make_pop_bet(self, cluster: str) -> callable:
        """Return a pop function that always picks the highest-edge bet."""
        queue = self._cluster_queues[cluster]

        def pop() -> dict | None:
            if not queue:
                return None
            # Always pick highest edge — re-sort in case batch was reloaded
            queue.sort(key=lambda b: -b.get("edge_pct", 0.0))
            return queue.pop(0)

        return pop

    def _make_peek_top_edge(self, cluster: str) -> callable:
        """Return a function that peeks at the highest edge in the queue without popping."""
        queue = self._cluster_queues[cluster]

        def peek(exclude_key: tuple[str, str, str] | None = None) -> float | None:
            # Optional exclude_key lets the dethrone watcher exclude the active
            # bet's own (event_id, market, outcome) from peek — without this,
            # _refresh_batch can re-add the currently-active bet to the queue,
            # making peek_top return the bet's OWN cached edge and causing
            # dethrone to fire false positives whenever the live edge dips
            # even slightly below the cached value.
            candidates = queue
            if exclude_key is not None:
                candidates = [b for b in queue if (b.get("event_id"), b.get("market"), b.get("outcome")) != exclude_key]
            if not candidates:
                return None
            return max(b.get("edge_pct", 0.0) for b in candidates)

        return peek

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

    def _mark_recently_skipped(self, bet: dict) -> None:
        """Mark a bet as recently skipped so refresh doesn't re-add it during
        the TTL window. Called by ProviderRunner when a bet is skipped (any
        reason). Keyed on (event_id, market, outcome) so different outcomes
        on the same event remain independent."""
        import time as _time

        key = (bet.get("event_id", ""), bet.get("market", ""), bet.get("outcome", ""))
        if not key[0]:
            return
        self._recently_skipped[key] = _time.monotonic()

    def _is_recently_skipped(self, bet: dict) -> bool:
        """Check if this bet was skipped within the TTL window. Side-effects:
        cleans up expired entries on each call (no separate sweeper needed)."""
        import time as _time

        now = _time.monotonic()
        # Cleanup expired entries lazily.
        expired = [k for k, ts in self._recently_skipped.items() if now - ts > self._recently_skipped_ttl_s]
        for k in expired:
            del self._recently_skipped[k]
        key = (bet.get("event_id", ""), bet.get("market", ""), bet.get("outcome", ""))
        return key in self._recently_skipped

    def _find_runner(self, provider_id: str | None, state: str | None = None):
        """Find a runner by provider_id, or first runner in the given state."""
        if provider_id and provider_id in self._runners:
            return self._runners[provider_id]
        if state:
            for r in self._runners.values():
                if r.state == state:
                    return r
        return None
