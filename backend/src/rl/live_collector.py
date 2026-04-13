"""Live episode collector — captures zone touches and measures outcomes.

Hooks into LevelMonitor zone touches. For each touch:
1. Builds 276-dim observation at touch time
2. Schedules outcome measurement after OUTCOME_DELAY seconds
3. Queries market_trades for price movement to compute reward
4. Appends completed episode to the live episode buffer on disk

Episodes accumulate in data/rl/live_episodes/. The training scheduler
merges these with historical episodes for periodic retraining.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import numpy as np

from .config import COST_PER_TRADE_TICKS, STOP_TICKS, TICK_SIZE
from .features.observation import build_observation

log = logging.getLogger(__name__)

# Cost per trade in R-multiples — matches episode_builder.py
COST_R = COST_PER_TRADE_TICKS / max(STOP_TICKS, 1)

# Buffer config
FLUSH_INTERVAL = 10  # Write to disk every N episodes
FLUSH_TIMER_S = 300  # Force flush every 5 minutes regardless of count
LIVE_DIR_NAME = "live_episodes"


@dataclass
class PendingEpisode:
    """Episode waiting for outcome measurement."""

    observation: np.ndarray
    touch_price: float
    touch_ts: float  # epoch seconds
    approach_direction: str
    level_type: str
    zone_members: int = 1


@dataclass
class CompletedEpisode:
    """Episode with measured outcome."""

    observation: np.ndarray
    reward_continuation: float
    reward_reversal: float
    optimal_stop_ticks: float
    level_type: str
    touch_price: float
    touch_ts: float
    breakeven_reached: bool = False
    levels_captured: int = 0


class LiveEpisodeCollector:
    """Collects live episodes from zone touches and measures outcomes.

    Usage:
        collector = LiveEpisodeCollector(data_dir)
        # On zone touch (called from LevelMonitor):
        collector.on_zone_touch(rl_state, price, approach, level_type)
        # Background task measures outcomes and flushes to disk
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or Path("data/rl")
        self._live_dir = self._data_dir / LIVE_DIR_NAME
        self._live_dir.mkdir(parents=True, exist_ok=True)

        self._pending: list[PendingEpisode] = []
        self._completed: list[CompletedEpisode] = []
        self._lock = Lock()

        # Stats
        self.total_collected = 0
        self.total_flushed = 0

        # Load existing count
        self._chunk_idx = len(list(self._live_dir.glob("obs_*.npy")))

        log.info("LiveEpisodeCollector initialized: dir=%s, existing_chunks=%d", self._live_dir, self._chunk_idx)

    def on_zone_touch(
        self, rl_state: dict, price: float, approach: str, level_type: str, zone_members: int = 1
    ) -> None:
        """Called by LevelMonitor when a zone is touched.

        Builds observation and queues for outcome measurement.
        """
        try:
            obs = build_observation(rl_state)
            pending = PendingEpisode(
                observation=obs,
                touch_price=price,
                touch_ts=time.time(),
                approach_direction=approach,
                level_type=level_type,
                zone_members=zone_members,
            )
            with self._lock:
                self._pending.append(pending)
            log.info(
                "Queued live episode: %s @ %.2f (%s) [pending=%d]",
                level_type,
                price,
                approach,
                len(self._pending),
            )
        except Exception:
            log.warning("Failed to build observation for live episode", exc_info=True)

    async def measure_outcomes_loop(self, get_recent_trades_fn) -> None:
        """Background loop that measures outcomes for pending episodes.

        Args:
            get_recent_trades_fn: async callable(since_ts, until_ts) -> list[dict]
                Returns trades with keys: ts (datetime), price (float), size (int)
                Typically queries market_trades table.
        """
        last_flush = time.time()
        while True:
            try:
                await self._process_pending(get_recent_trades_fn)
                # Periodic flush — don't lose episodes to restarts
                now = time.time()
                if now - last_flush >= FLUSH_TIMER_S and self._completed:
                    log.info("Periodic flush: %d buffered episodes", len(self._completed))
                    self._flush_to_disk()
                    last_flush = now
            except Exception:
                log.exception("Error in outcome measurement loop")
            await asyncio.sleep(30)  # Check every 30s

    async def _process_pending(self, get_trades_fn) -> None:
        """Check pending episodes that are old enough to measure."""
        now = time.time()
        max_window = max(OUTCOME_WINDOWS)

        ready = []
        still_pending = []

        with self._lock:
            for ep in self._pending:
                age = now - ep.touch_ts
                if age >= max_window + 10:  # 10s buffer
                    ready.append(ep)
                else:
                    still_pending.append(ep)
            self._pending = still_pending

        for ep in ready:
            try:
                since = datetime.fromtimestamp(ep.touch_ts, tz=timezone.utc)
                until = since + timedelta(seconds=max_window + 5)
                trades = await get_trades_fn(since, until)

                if not trades:
                    log.warning("No trades found for outcome measurement at %.2f", ep.touch_price)
                    continue

                completed = self._compute_reward(ep, trades)
                if completed:
                    with self._lock:
                        self._completed.append(completed)
                        self.total_collected += 1
                    log.info(
                        "Episode measured: %.2f rc=%.3f rr=%.3f [collected=%d, buffered=%d]",
                        ep.touch_price,
                        completed.reward_continuation,
                        completed.reward_reversal,
                        self.total_collected,
                        len(self._completed),
                    )

                    if len(self._completed) >= FLUSH_INTERVAL:
                        self._flush_to_disk()

            except Exception:
                log.warning("Failed to measure outcome for episode at %.2f", ep.touch_price, exc_info=True)

    def _compute_reward(self, ep: PendingEpisode, trades: list[dict]) -> CompletedEpisode | None:
        """Compute reward using the same velocity+stop lifecycle as episode_builder.

        Converts raw trades to tick-dict format and calls _measure_movement /
        _score_velocity directly so that live and historical episodes have the
        same label distribution.  Trail bonus is omitted (no structural levels
        available) but the base velocity score + cost matches exactly.
        """
        touch_price = ep.touch_price
        touch_ts_epoch = ep.touch_ts
        touch_ts_dt = datetime.fromtimestamp(touch_ts_epoch, tz=timezone.utc)

        # Convert trades to the format expected by episode_builder helpers
        tick_dicts = [{"ts": t["ts"], "price": float(t["price"])} for t in trades]
        if not tick_dicts:
            return None

        # Velocity score for each direction (same logic as episode_builder)
        long_profiles = _measure_movement(touch_price, tick_dicts, 0, len(tick_dicts), touch_ts_dt, direction=+1)
        short_profiles = _measure_movement(touch_price, tick_dicts, 0, len(tick_dicts), touch_ts_dt, direction=-1)
        base_long = _score_velocity(long_profiles)
        base_short = _score_velocity(short_profiles)

        # BE lifecycle (empty levels_ahead — just stop/breakeven, no trail)
        cont_dir = 1 if ep.approach_direction == "up" else -1
        _, be_cont = _count_levels_captured(
            touch_price,
            tick_dicts,
            0,
            len(tick_dicts),
            touch_ts_dt,
            direction=cont_dir,
            levels_ahead=[],
            be_trigger_r=_BE_TRIGGER_R,
        )

        if ep.approach_direction == "up":
            reward_cont = base_long - COST_R
            reward_rev = base_short - COST_R
        else:
            reward_cont = base_short - COST_R
            reward_rev = base_long - COST_R

        # Stop estimate from MAE (same conservative method as episode_builder)
        prices = [float(t["price"]) for t in trades[:200]]
        if prices:
            if ep.approach_direction == "up":
                mae = max(0.0, touch_price - min(prices)) / TICK_SIZE
            else:
                mae = max(0.0, max(prices) - touch_price) / TICK_SIZE
            stop_ticks = float(np.clip(mae + 2.0, 6.0, 40.0))
        else:
            stop_ticks = float(STOP_TICKS)

        return CompletedEpisode(
            observation=ep.observation,
            reward_continuation=float(np.clip(reward_cont, -2.0, 4.0)),
            reward_reversal=float(np.clip(reward_rev, -2.0, 4.0)),
            optimal_stop_ticks=stop_ticks,
            level_type=ep.level_type,
            touch_price=touch_price,
            touch_ts=ep.touch_ts,
            breakeven_reached=be_cont,  # actual BE lifecycle, not threshold heuristic
            levels_captured=0,  # no structural levels available in live path
        )

    def _flush_to_disk(self) -> None:
        """Write completed episodes to disk as numpy chunks."""
        with self._lock:
            if not self._completed:
                return
            episodes = list(self._completed)
            self._completed.clear()

        obs = np.array([e.observation for e in episodes], dtype=np.float32)
        rc = np.array([e.reward_continuation for e in episodes], dtype=np.float32)
        rr = np.array([e.reward_reversal for e in episodes], dtype=np.float32)
        lt = np.array([e.level_type for e in episodes])
        st = np.array([e.optimal_stop_ticks for e in episodes], dtype=np.float32)
        be = np.array([float(e.breakeven_reached) for e in episodes], dtype=np.float32)
        lc = np.array([float(e.levels_captured) for e in episodes], dtype=np.float32)

        idx = self._chunk_idx
        np.save(self._live_dir / f"obs_{idx:04d}.npy", obs)
        np.save(self._live_dir / f"rc_{idx:04d}.npy", rc)
        np.save(self._live_dir / f"rr_{idx:04d}.npy", rr)
        np.save(self._live_dir / f"lt_{idx:04d}.npy", lt)
        np.save(self._live_dir / f"st_{idx:04d}.npy", st)
        np.save(self._live_dir / f"be_{idx:04d}.npy", be)
        np.save(self._live_dir / f"lc_{idx:04d}.npy", lc)

        self._chunk_idx += 1
        self.total_flushed += len(episodes)
        log.info("Flushed %d live episodes to chunk %04d (total: %d)", len(episodes), idx, self.total_flushed)

    def flush(self) -> None:
        """Force flush any buffered episodes to disk."""
        self._flush_to_disk()

    def get_stats(self) -> dict:
        """Return collector statistics."""
        with self._lock:
            return {
                "pending": len(self._pending),
                "buffered": len(self._completed),
                "total_collected": self.total_collected,
                "total_flushed": self.total_flushed,
                "chunks": self._chunk_idx,
            }


# Singleton
_collector: LiveEpisodeCollector | None = None


def get_live_collector(data_dir: Path | None = None) -> LiveEpisodeCollector:
    """Get or create the global live episode collector."""
    global _collector
    if _collector is None:
        _collector = LiveEpisodeCollector(data_dir)
    return _collector
