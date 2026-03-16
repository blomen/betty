"""Level proximity monitor. Plugs into DatabentoLiveStream as a tick callback."""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

TICK_SIZE = 0.25  # NQ tick size


class LevelStatus(str, Enum):
    WATCHING = "watching"
    APPROACHING = "approaching"
    AT_LEVEL = "at_level"
    TRIGGERED = "triggered"
    REJECTED = "rejected"


@dataclass
class MonitoredLevel:
    """A structural level being tracked for proximity."""
    name: str
    price: float
    category: str
    status: LevelStatus = LevelStatus.WATCHING
    touched_at: float = 0.0
    cluster: list[str] = field(default_factory=list)

    def distance_ticks(self, price: float) -> float:
        return (price - self.price) / TICK_SIZE

    def abs_distance_ticks(self, price: float) -> float:
        return abs(self.distance_ticks(price))


class LevelMonitor:
    """Monitors price proximity to structural levels. Called on each tick."""

    APPROACHING_TICKS = 15
    AT_LEVEL_TICKS = 5
    REJECT_TICKS = 20

    def __init__(self, publish_fn):
        self._publish = publish_fn
        self._levels: list[MonitoredLevel] = []
        self._last_orderflow_emit: float = 0.0
        self._orderflow_interval: float = 2.5
        self._any_at_level: bool = False
        self._tick_buffer = None
        self._candle_flow_fn = None

    def load_levels(self, expanded_session: dict) -> None:
        """Load levels from an ExpandedSession dict. Called on compute_session()."""
        self._levels.clear()
        session = expanded_session.get("session", {})
        levels_list = expanded_session.get("levels", [])

        for lv in levels_list:
            price = lv.get("price_low") or lv.get("price")
            if price is None:
                continue
            name = lv.get("type", "unknown")
            category = self._categorize(name)
            self._levels.append(MonitoredLevel(
                name=name,
                price=float(price),
                category=category,
            ))

        for band_name, key in [
            ("VWAP", "vwap"), ("VWAP +1SD", "vwap_1sd_upper"),
            ("VWAP -1SD", "vwap_1sd_lower"), ("VWAP +2SD", "vwap_2sd_upper"),
            ("VWAP -2SD", "vwap_2sd_lower"), ("VWAP +3SD", "vwap_3sd_upper"),
            ("VWAP -3SD", "vwap_3sd_lower"),
        ]:
            val = session.get(key)
            if val is not None:
                if not any(l.name == band_name and abs(l.price - val) < TICK_SIZE for l in self._levels):
                    self._levels.append(MonitoredLevel(
                        name=band_name, price=float(val), category="band",
                    ))

        logger.info("LevelMonitor loaded %d levels", len(self._levels))

    @staticmethod
    def _categorize(name: str) -> str:
        name_lower = name.lower()
        if "vwap" in name_lower or "sd" in name_lower:
            return "band"
        if name_lower in ("pdh", "pdl"):
            return "prior"
        if "overnight" in name_lower or name_lower in ("on_high", "on_low"):
            return "overnight"
        if any(k in name_lower for k in ("swing", "naked", "ob", "fvg")):
            return "structure"
        return "session"

    def on_tick(self, price: float, size: int, ts: float) -> None:
        """Called on each trade tick. Checks all levels for proximity transitions."""
        now = time.time()
        at_level_levels = []

        for level in self._levels:
            if level.status == LevelStatus.TRIGGERED:
                continue

            dist = level.abs_distance_ticks(price)
            old_status = level.status

            if dist <= self.AT_LEVEL_TICKS:
                if old_status != LevelStatus.AT_LEVEL:
                    level.status = LevelStatus.AT_LEVEL
                    level.touched_at = now
                    self._on_level_touched(level, price)
                at_level_levels.append(level)

            elif dist <= self.APPROACHING_TICKS:
                if old_status == LevelStatus.WATCHING:
                    level.status = LevelStatus.APPROACHING
                    self._on_level_approaching(level, price, dist)

            elif old_status in (LevelStatus.AT_LEVEL, LevelStatus.APPROACHING):
                if dist > self.REJECT_TICKS:
                    level.status = LevelStatus.REJECTED
                    self._on_level_rejected(level, price)
                    level.status = LevelStatus.WATCHING

        if len(at_level_levels) > 1:
            cluster_names = [l.name for l in at_level_levels]
            for l in at_level_levels:
                l.cluster = [n for n in cluster_names if n != l.name]

        self._any_at_level = bool(at_level_levels)
        if self._any_at_level and (now - self._last_orderflow_emit) >= self._orderflow_interval:
            self._emit_orderflow_update(price)
            self._last_orderflow_emit = now

    def mark_triggered(self, level_name: str) -> None:
        """Mark a level as triggered (trade taken)."""
        for level in self._levels:
            if level.name == level_name:
                level.status = LevelStatus.TRIGGERED
                break

    def get_levels_snapshot(self, price: float) -> list[dict]:
        """Return all levels with current distance and status for REST API."""
        result = []
        for level in self._levels:
            result.append({
                "name": level.name,
                "price": level.price,
                "category": level.category,
                "status": level.status.value,
                "distance_ticks": round(level.distance_ticks(price), 1),
                "cluster": level.cluster,
            })
        result.sort(key=lambda x: abs(x["distance_ticks"]))
        return result

    def set_tick_buffer(self, tick_buffer) -> None:
        """Provide access to the stream's TickBuffer for orderflow computation."""
        self._tick_buffer = tick_buffer

    def set_candle_flow_source(self, fn) -> None:
        """Provide callable that returns recent CandleFlow candles for orderflow."""
        self._candle_flow_fn = fn

    def _compute_orderflow_snapshot(self) -> dict:
        """Compute orderflow signals for both directions and package as snapshot."""
        from .orderflow import compute_signals, build_candle_flow

        if not self._candle_flow_fn:
            return {}

        candles = self._candle_flow_fn()
        if not candles or len(candles) < 3:
            return {}

        long_signals = compute_signals(candles, "long", lookback=10)
        short_signals = compute_signals(candles, "short", lookback=10)

        return {
            "long": long_signals.__dict__,
            "short": short_signals.__dict__,
        }

    # --- SSE event emitters ---

    def _on_level_approaching(self, level: MonitoredLevel, price: float, dist: float) -> None:
        self._publish({
            "type": "level_approaching",
            "level": level.name,
            "level_price": level.price,
            "category": level.category,
            "price": price,
            "distance_ticks": round(dist, 1),
        })

    def _on_level_touched(self, level: MonitoredLevel, price: float) -> None:
        snapshot = self._compute_orderflow_snapshot()
        self._publish({
            "type": "level_touched",
            "level": level.name,
            "level_price": level.price,
            "category": level.category,
            "price": price,
            "confluence": level.cluster,
            "orderflow": snapshot,
        })

    def _on_level_rejected(self, level: MonitoredLevel, price: float) -> None:
        self._publish({
            "type": "level_rejected",
            "level": level.name,
            "level_price": level.price,
        })

    def _emit_orderflow_update(self, price: float) -> None:
        snapshot = self._compute_orderflow_snapshot()
        if snapshot:
            self._publish({
                "type": "orderflow_update",
                "price": price,
                "ts": time.time(),
                "orderflow": snapshot,
            })
