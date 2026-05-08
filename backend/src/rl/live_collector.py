"""Live episode collector — captures zone touches and measures outcomes.

Hooks into LevelMonitor zone touches. For each touch:
1. Builds 302-dim base observation + 118-dim trigger observation at touch time
2. Schedules outcome measurement after OUTCOME_DELAY seconds
3. Queries market_trades for price movement to compute reward
4. Computes full trailing reward with structural levels (same as replay engine)
5. Appends completed episode to the live episode buffer on disk

Episodes accumulate in data/rl/live_episodes/. The training pipeline
merges these with historical episodes for retraining.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import numpy as np

from .config import COST_PER_TRADE_TICKS, STOP_TICKS, TICK_SIZE
from .data.episode_builder import (
    _BE_TRIGGER_R,
    _count_levels_captured,
    _measure_mae,
    _measure_movement,
    _score_velocity,
)
from .data.episode_builder import (
    _TRAIL_BONUS_PER_LEVEL as TRAIL_BONUS,
)
from .data.episode_builder import (
    _WINDOWS as OUTCOME_WINDOWS,
)
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
    trigger_observation: np.ndarray | None
    touch_price: float
    touch_ts: float  # epoch seconds
    approach_direction: str
    level_type: str
    levels_above: list[float] = field(default_factory=list)
    levels_below: list[float] = field(default_factory=list)
    zone_members: int = 1


@dataclass
class CompletedEpisode:
    """Episode with measured outcome."""

    observation: np.ndarray
    trigger_observation: np.ndarray | None
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

        # Lazy-load trigger GBT for trigger observation building (Phase 3b)
        self._trigger_gbt = None
        self._models_loaded = False

        log.info("LiveEpisodeCollector initialized: dir=%s, existing_chunks=%d", self._live_dir, self._chunk_idx)

    def _ensure_models(self) -> None:
        """Lazy-load TriggerGBT for building trigger observations."""
        if self._models_loaded:
            return
        self._models_loaded = True
        try:
            from .agent.trigger_gbt import TriggerGBT

            models_dir = self._data_dir / "models"
            tgbt_path = models_dir / "trigger_gbt_latest.joblib"
            if tgbt_path.exists():
                self._trigger_gbt = TriggerGBT.load(tgbt_path)
                log.info("Live collector loaded TriggerGBT from %s", tgbt_path)
        except Exception:
            log.warning("Failed to load TriggerGBT for live trigger obs", exc_info=True)

    def _build_trigger_obs(self, rl_state: dict, base_obs: np.ndarray) -> np.ndarray | None:
        """Build 118-dim trigger observation matching the training pipeline (Phase 3b)."""
        self._ensure_models()
        if self._trigger_gbt is None:
            return None
        try:
            from .features.trigger_features import build_trigger_observation

            trigger_no_gbt = build_trigger_observation(rl_state, base_obs)
            gbt_forecast = self._trigger_gbt.predict_full(trigger_no_gbt)
            trigger_obs = build_trigger_observation(rl_state, base_obs, gbt_forecast)
            return trigger_obs
        except Exception:
            log.debug("Failed to build trigger obs for live episode", exc_info=True)
            return None

    def on_zone_touch(
        self, rl_state: dict, price: float, approach: str, level_type: str, zone_members: int = 1
    ) -> None:
        """Called by LevelMonitor when a zone is touched.

        Builds observation and queues for outcome measurement.
        """
        try:
            obs = build_observation(rl_state)
            trigger_obs = self._build_trigger_obs(rl_state, obs)

            # Extract structural levels for trailing reward
            all_levels = rl_state.get("all_levels", [])
            levels_above = sorted([lv for lv in all_levels if lv > price + TICK_SIZE])
            levels_below = sorted([lv for lv in all_levels if lv < price - TICK_SIZE], reverse=True)

            pending = PendingEpisode(
                observation=obs,
                trigger_observation=trigger_obs,
                touch_price=price,
                touch_ts=time.time(),
                approach_direction=approach,
                level_type=level_type,
                levels_above=levels_above[:6],  # max 6 levels each direction
                levels_below=levels_below[:6],
                zone_members=zone_members,
            )
            with self._lock:
                self._pending.append(pending)
            log.info(
                "Queued live episode: %s @ %.2f (%s) levels_up=%d levels_dn=%d trig=%s [pending=%d]",
                level_type,
                price,
                approach,
                len(levels_above),
                len(levels_below),
                "yes" if trigger_obs is not None else "no",
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
                        "Episode measured: %.2f rc=%.3f rr=%.3f levels=%d be=%s [collected=%d]",
                        ep.touch_price,
                        completed.reward_continuation,
                        completed.reward_reversal,
                        completed.levels_captured,
                        completed.breakeven_reached,
                        self.total_collected,
                    )

                    if len(self._completed) >= FLUSH_INTERVAL:
                        self._flush_to_disk()

            except Exception:
                log.warning("Failed to measure outcome for episode at %.2f", ep.touch_price, exc_info=True)

    def _compute_reward(self, ep: PendingEpisode, trades: list[dict]) -> CompletedEpisode | None:
        """Compute reward with full trailing bonus — same fidelity as replay engine.

        Uses structural levels for trailing reward, stop lifecycle with breakeven,
        and MAE-based optimal stop. Matches episode_builder.build_episode() exactly.
        """
        touch_price = ep.touch_price
        touch_ts_dt = datetime.fromtimestamp(ep.touch_ts, tz=timezone.utc)

        # Convert trades to tick-dict format
        tick_dicts = []
        for t in trades:
            ts = t["ts"]
            if hasattr(ts, "tzinfo") and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            tick_dicts.append({"ts": ts, "price": float(t["price"])})
        if not tick_dicts:
            return None

        start, end = 0, len(tick_dicts)

        # Velocity score for each direction
        long_profiles = _measure_movement(touch_price, tick_dicts, start, end, touch_ts_dt, direction=+1)
        short_profiles = _measure_movement(touch_price, tick_dicts, start, end, touch_ts_dt, direction=-1)
        base_long = _score_velocity(long_profiles)
        base_short = _score_velocity(short_profiles)

        # Trailing reward with structural levels (same as episode_builder)
        long_levels, be_long = _count_levels_captured(
            touch_price,
            tick_dicts,
            start,
            end,
            touch_ts_dt,
            direction=+1,
            levels_ahead=ep.levels_above,
            be_trigger_r=_BE_TRIGGER_R,
        )
        short_levels, be_short = _count_levels_captured(
            touch_price,
            tick_dicts,
            start,
            end,
            touch_ts_dt,
            direction=-1,
            levels_ahead=ep.levels_below,
            be_trigger_r=_BE_TRIGGER_R,
        )

        reward_long = base_long + long_levels * TRAIL_BONUS - COST_R
        reward_short = base_short + short_levels * TRAIL_BONUS - COST_R

        # Map to continuation/reversal based on approach direction
        if ep.approach_direction == "up":
            reward_cont = reward_long
            reward_rev = reward_short
            be_cont = be_long
            levels_best = long_levels if reward_long >= reward_short else short_levels
        else:
            reward_cont = reward_short
            reward_rev = reward_long
            be_cont = be_short
            levels_best = short_levels if reward_short >= reward_long else long_levels

        # Optimal stop from MAE (same as episode_builder)
        best_action_dir = 1 if reward_cont >= reward_rev else -1
        if ep.approach_direction == "down":
            best_action_dir = -best_action_dir
        long_mae = _measure_mae(touch_price, tick_dicts, start, end, touch_ts_dt, direction=+1)
        short_mae = _measure_mae(touch_price, tick_dicts, start, end, touch_ts_dt, direction=-1)
        mae = long_mae if best_action_dir == 1 else short_mae

        # Structural stop: nearest level behind + 2 tick buffer
        behind = ep.levels_below if best_action_dir == 1 else ep.levels_above
        if behind:
            struct_dist = abs(behind[0] - touch_price) / TICK_SIZE + 2.0
            stop_ticks = float(max(6.0, min(40.0, struct_dist)))
        elif mae > 0:
            stop_ticks = float(np.clip(mae + 2.0, 6.0, 40.0))
        else:
            stop_ticks = float(STOP_TICKS)

        return CompletedEpisode(
            observation=ep.observation,
            trigger_observation=ep.trigger_observation,
            reward_continuation=float(np.clip(reward_cont, -2.0, 4.0)),
            reward_reversal=float(np.clip(reward_rev, -2.0, 4.0)),
            optimal_stop_ticks=stop_ticks,
            level_type=ep.level_type,
            touch_price=touch_price,
            touch_ts=ep.touch_ts,
            breakeven_reached=be_cont,
            levels_captured=levels_best,
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
        # touch_epochs feeds the chronological session-memory features; without
        # these chunks merge-live can't extend touch_epochs.npy and the live
        # block of session_memory features falls back to zeros (audit #19).
        te = np.array([e.touch_ts for e in episodes], dtype=np.float64)

        idx = self._chunk_idx
        np.save(self._live_dir / f"obs_{idx:04d}.npy", obs)
        np.save(self._live_dir / f"rc_{idx:04d}.npy", rc)
        np.save(self._live_dir / f"rr_{idx:04d}.npy", rr)
        np.save(self._live_dir / f"lt_{idx:04d}.npy", lt)
        np.save(self._live_dir / f"st_{idx:04d}.npy", st)
        np.save(self._live_dir / f"be_{idx:04d}.npy", be)
        np.save(self._live_dir / f"lc_{idx:04d}.npy", lc)
        np.save(self._live_dir / f"te_{idx:04d}.npy", te)

        # Save trigger observations if available
        has_trig = all(e.trigger_observation is not None for e in episodes)
        if has_trig:
            trig = np.array([e.trigger_observation for e in episodes], dtype=np.float32)
            np.save(self._live_dir / f"trig_{idx:04d}.npy", trig)

        self._chunk_idx += 1
        self.total_flushed += len(episodes)
        log.info(
            "Flushed %d live episodes to chunk %04d (trig=%s, total: %d)",
            len(episodes),
            idx,
            "yes" if has_trig else "no",
            self.total_flushed,
        )

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
