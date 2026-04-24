"""Level proximity monitor. Plugs into DatabentoLiveStream as a tick callback."""

import asyncio
import contextlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from src.rl.config import LevelType as RLLevelType
from src.rl.zone_builder import Zone, build_zones

from .amt_dynamics import AMTDynamicsTracker

logger = logging.getLogger(__name__)

TICK_SIZE = 0.25  # NQ tick size


def _persist_stock_signal_async(payload: dict) -> None:
    """Fire-and-forget insert of a dispatched signal into stock_signals.

    Threaded so it never blocks LevelMonitor.on_tick. Errors are logged but
    never propagated — missing a persist is always preferable to blocking
    signal dispatch.
    """
    def _worker(p: dict) -> None:
        try:
            from ..db.models import StockSignal, get_session

            ts_raw = p.get("ts")
            if isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc).replace(tzinfo=None)
            else:
                ts = datetime.utcnow()

            db = get_session()
            try:
                row = StockSignal(
                    ts=ts,
                    symbol="NQ",
                    action=str(p.get("action", "")),
                    price=float(p.get("price", 0)),
                    confidence=_maybe_float(p.get("confidence")),
                    cont_p=_maybe_float(p.get("cont_p")),
                    rev_p=_maybe_float(p.get("rev_p")),
                    stop_price=_maybe_float(p.get("stop_price")),
                    stop_ticks=_maybe_int(p.get("stop_ticks")),
                    zone_center=_maybe_float(p.get("zone")),
                    zone_members=_maybe_int(p.get("zone_members")),
                    model_type=str(p.get("model_type") or "")[:32] or None,
                )
                db.add(row)
                db.commit()
            finally:
                db.close()
        except Exception:
            logger.warning("stock_signals persist failed", exc_info=True)

    threading.Thread(target=_worker, args=(payload,), daemon=True, name="signal-persist").start()


def _maybe_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _maybe_int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None

# File-based kill switch: `touch /app/data/rl/trading_paused` to raise the
# signal dispatch threshold to 0.99 without a rebuild. Used while we're
# iterating on the model and don't want live orders firing. Remove the
# file to resume normal trading.
_TRADING_PAUSED_FLAG = "/app/data/rl/trading_paused"


def _trading_paused() -> bool:
    return os.path.exists(_TRADING_PAUSED_FLAG)


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
    approach_price: float | None = None  # price when WATCHING → APPROACHING
    approach_ticks: int = 15  # default, overridden for swing levels
    at_level_ticks: int = 5  # default
    reject_ticks: int = 20  # default

    def distance_ticks(self, price: float) -> float:
        return (price - self.price) / TICK_SIZE

    def abs_distance_ticks(self, price: float) -> float:
        return abs(self.distance_ticks(price))


def _compute_orderflow_score_live(state: dict, zone, price: float, action: str) -> float:
    """Orderflow confluence score [0, 1] for live gating.

    Returns how strongly orderflow confirms the trade direction implied by
    action (REVERSAL = fade approach). Score < 0.30 → veto entry.

    Mirrors the logic in session_manager._compute_orderflow_score so training
    and live gate use the same metric.
    """
    # Determine trade direction: REV fades the approach.
    approach_up = price < zone.center_price if zone is not None else True
    if action in ("REVERSAL", "reversal"):
        # Short if approach up, long if approach down
        trade_dir = -1 if approach_up else 1
    else:
        trade_dir = 1 if approach_up else -1

    candles = state.get("candles") or []
    signals = state.get("orderflow_signals")
    score = 0.0

    # 1. Delta direction match (0.20)
    if candles:
        last = candles[-1]
        vol = max(getattr(last, "volume", 1), 1)
        delta_pct = getattr(last, "delta", 0) / vol
        delta_score = max(-1.0, min(1.0, delta_pct * trade_dir / 0.15))
        if delta_score > 0:
            score += 0.20 * delta_score

    if signals is not None:
        # 2. CVD trend alignment (0.20)
        cvd_trend = getattr(signals, "cvd_trend", "flat")
        if (trade_dir == 1 and cvd_trend == "rising") or (trade_dir == -1 and cvd_trend == "falling"):
            score += 0.20
        elif cvd_trend == "flat":
            score += 0.05

        # 3. Stacked imbalance cluster (0.25)
        sic = getattr(signals, "stacked_imbalance_count", 0) or 0
        sdir = getattr(signals, "stacked_direction", None)
        wants_buy = trade_dir == 1
        matches = (wants_buy and sdir == "buy") or (not wants_buy and sdir == "sell")
        if matches:
            score += 0.25 * min(sic / 3.0, 1.0)

        # 4. Absorption at level (0.20)
        vsa = float(getattr(signals, "vsa_absorption", 0) or 0)
        absorb_strength = float(getattr(signals, "absorption_strength", 0) or 0)
        score += 0.20 * min(max(vsa, absorb_strength), 1.0)

        # 5. Big-trade net delta alignment (0.15)
        big_net = float(getattr(signals, "big_trades_net_delta", 0) or 0)
        if trade_dir == 1 and big_net > 0:
            score += 0.15 * min(big_net / 100.0, 1.0)
        elif trade_dir == -1 and big_net < 0:
            score += 0.15 * min(-big_net / 100.0, 1.0)

    return max(0.0, min(1.0, score))


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
        self._open_positions: list[dict] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._db_session_factory = None
        self._level_context_lock: asyncio.Lock | None = None
        self._last_ml_features: dict | None = None
        self._active_level_name: str | None = None
        # Live session context for DQN inference (populated by set_session_context)
        self._session_context: dict | None = None
        # Zone-aware DQN inference state
        self._zones: list[Zone] = []
        self._zone_debounce: set[int] = set()  # zone object ids for O(1) lookup
        self._session_atr: float = 40.0
        self._amt_tracker = AMTDynamicsTracker()
        # Throttle: skip level checks when price hasn't moved
        self._last_price: float = 0.0
        try:
            from src.ml.level_touch.outcomes import OutcomeTracker

            self._outcome_tracker = OutcomeTracker()
        except Exception:
            self._outcome_tracker = None

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
            self._levels.append(
                MonitoredLevel(
                    name=name,
                    price=float(price),
                    category=category,
                )
            )

        # Extract structural levels from session dict (POC, VAH, VAL, PDH, PDL, etc.)
        # Tuples: (session_dict_key, level_name, category)
        _SESSION_LEVELS = [
            ("poc", "poc", "session"),
            ("vah", "vah", "session"),
            ("val", "val", "session"),
            ("pdh", "pdh", "prior"),
            ("pdl", "pdl", "prior"),
            ("tokyo_high", "tokyo_high", "session"),
            ("tokyo_low", "tokyo_low", "session"),
            ("london_high", "london_high", "session"),
            ("london_low", "london_low", "session"),
            ("tpo_poc", "tpoc", "session"),
            ("tpo_vah", "tvah", "session"),
            ("tpo_val", "tval", "session"),
        ]
        for session_key, name, category in _SESSION_LEVELS:
            val = session.get(session_key)
            if val is not None:
                if not any(l.name == name and abs(l.price - val) < TICK_SIZE for l in self._levels):
                    self._levels.append(MonitoredLevel(name=name, price=float(val), category=category))

        # Extract IB levels
        ib_high = session.get("ib_high")
        ib_low = session.get("ib_low")
        if ib_high is not None:
            self._levels.append(MonitoredLevel(name="nyib_high", price=float(ib_high), category="session"))
        if ib_low is not None:
            self._levels.append(MonitoredLevel(name="nyib_low", price=float(ib_low), category="session"))

        for band_name, key in [
            ("VWAP", "vwap"),
            ("VWAP +1SD", "vwap_1sd_upper"),
            ("VWAP -1SD", "vwap_1sd_lower"),
            ("VWAP +2SD", "vwap_2sd_upper"),
            ("VWAP -2SD", "vwap_2sd_lower"),
            ("VWAP +3SD", "vwap_3sd_upper"),
            ("VWAP -3SD", "vwap_3sd_lower"),
        ]:
            val = session.get(key)
            if val is not None:
                if not any(l.name == band_name and abs(l.price - val) < TICK_SIZE for l in self._levels):
                    self._levels.append(
                        MonitoredLevel(
                            name=band_name,
                            price=float(val),
                            category="band",
                        )
                    )

        logger.info(
            "LevelMonitor loaded %d levels (from session: %d structural + %d bands, from DB: %d)",
            len(self._levels),
            sum(1 for l in self._levels if l.category in ("session", "prior")),
            sum(1 for l in self._levels if l.category == "band"),
            len(levels_list),
        )

        # Set wider approach zones for swing levels
        _SWING_ZONES = {
            "daily_swing_high": (15, 5, 20),
            "daily_swing_low": (15, 5, 20),
            "weekly_swing_high": (25, 10, 35),
            "weekly_swing_low": (25, 10, 35),
            "monthly_swing_high": (40, 15, 50),
            "monthly_swing_low": (40, 15, 50),
        }
        for level in self._levels:
            zones = _SWING_ZONES.get(level.name)
            if zones:
                level.approach_ticks, level.at_level_ticks, level.reject_ticks = zones

        self._rebuild_zones()

    def update_vwap(
        self,
        vwap: float,
        sd1_upper: float | None = None,
        sd1_lower: float | None = None,
        sd2_upper: float | None = None,
        sd2_lower: float | None = None,
        sd3_upper: float | None = None,
        sd3_lower: float | None = None,
    ) -> None:
        """Update VWAP levels in real-time without a full reload.

        Called on every 1m candle close from the signal relay WS handler so that
        the developing VWAP (anchored midnight CET, daily reset) is always current.
        """
        # Remove stale VWAP/SD levels
        self._levels = [lv for lv in self._levels if "vwap" not in lv.name.lower() and " sd" not in lv.name.lower()]

        band_values = [
            ("VWAP", vwap),
            ("VWAP +1SD", sd1_upper),
            ("VWAP -1SD", sd1_lower),
            ("VWAP +2SD", sd2_upper),
            ("VWAP -2SD", sd2_lower),
            ("VWAP +3SD", sd3_upper),
            ("VWAP -3SD", sd3_lower),
        ]
        for name, val in band_values:
            if val is not None:
                self._levels.append(
                    MonitoredLevel(
                        name=name,
                        price=float(val),
                        category="band",
                    )
                )

        self._rebuild_zones(reset_debounce=False)
        logger.debug("VWAP updated: %.2f ±1SD=[%.2f, %.2f]", vwap, sd1_lower or 0, sd1_upper or 0)

    def _rebuild_zones(self, reset_debounce: bool = True) -> None:
        level_type_map = {
            "poc": RLLevelType.DAILY_POC,
            "daily_poc": RLLevelType.DAILY_POC,
            "vah": RLLevelType.DAILY_VAH,
            "daily_vah": RLLevelType.DAILY_VAH,
            "val": RLLevelType.DAILY_VAL,
            "daily_val": RLLevelType.DAILY_VAL,
            "vwap": RLLevelType.VWAP,
            "vwap +1sd": RLLevelType.VWAP_SD1,
            "vwap -1sd": RLLevelType.VWAP_SD1,
            "vwap +2sd": RLLevelType.VWAP_SD2,
            "vwap -2sd": RLLevelType.VWAP_SD2,
            "vwap +3sd": RLLevelType.VWAP_SD3,
            "vwap -3sd": RLLevelType.VWAP_SD3,
            "pdh": RLLevelType.PDH,
            "pdl": RLLevelType.PDL,
            "tokyo_high": RLLevelType.TOKYO_HIGH,
            "tokyo_low": RLLevelType.TOKYO_LOW,
            "nyib_high": RLLevelType.NYIB_HIGH,
            "nyib_low": RLLevelType.NYIB_LOW,
            "tpoc": RLLevelType.TPOC,
            "tvah": RLLevelType.TVAH,
            "tval": RLLevelType.TVAL,
            "tibh": RLLevelType.TIBH,
            "tibl": RLLevelType.TIBL,
            "naked_poc": RLLevelType.NAKED_POC,
            "daily_swing_high": RLLevelType.DAILY_SWING_HIGH,
            "daily_swing_low": RLLevelType.DAILY_SWING_LOW,
            "weekly_swing_high": RLLevelType.WEEKLY_SWING_HIGH,
            "weekly_swing_low": RLLevelType.WEEKLY_SWING_LOW,
            "monthly_swing_high": RLLevelType.MONTHLY_SWING_HIGH,
            "monthly_swing_low": RLLevelType.MONTHLY_SWING_LOW,
        }
        level_tuples = []
        for lv in self._levels:
            name_key = lv.name.lower().replace(" ", "_").replace("+", "").replace("-", "")
            lt = level_type_map.get(name_key, RLLevelType.VWAP)
            level_tuples.append((lv.name, lt, lv.price))
        self._zones = build_zones(level_tuples, self._session_atr)
        if reset_debounce:
            self._zone_debounce.clear()
            if hasattr(self, "_zone_last_fire"):
                self._zone_last_fire = {}
        logger.info(
            "LevelMonitor rebuilt %d zones from %d levels (debounce_reset=%s)",
            len(self._zones),
            len(self._levels),
            reset_debounce,
        )
        self._broadcast_zones()

    def _broadcast_zones(self) -> None:
        """Send current zones to all relay clients so the chart can render them."""
        callbacks = getattr(self, "_signal_callbacks", set())
        if not callbacks or not self._zones:
            return
        payload = {
            "type": "zone_update",
            "zones": [
                {
                    "price": round(z.center_price, 2),
                    "members": z.member_count,
                    "upper": round(z.upper_bound, 2),
                    "lower": round(z.lower_bound, 2),
                    "hierarchy": round(z.hierarchy_score, 3),
                }
                for z in self._zones
            ],
        }
        for cb in list(callbacks):
            try:
                if asyncio.iscoroutinefunction(cb):
                    asyncio.create_task(cb(payload))
                else:
                    cb(payload)
            except Exception:
                logger.debug("Failed to broadcast zones to callback", exc_info=True)

    def set_async_context(self, loop, db_session_factory) -> None:
        self._level_context_lock = asyncio.Lock()
        self._loop = loop
        self._db_session_factory = db_session_factory
        if self._outcome_tracker is not None:
            self._outcome_tracker.set_context(loop, db_session_factory)

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
        """Called on each trade tick. Checks all levels for proximity transitions.

        Args:
            ts: Exchange timestamp as epoch seconds (from Databento ts_event / 1e9).
        """
        now = ts  # Use exchange timestamp for consistency and replay-ability

        # Skip level/zone checks when price hasn't changed — many ticks hit the
        # same price and repeating O(n) level scans is pure waste.
        price_changed = price != self._last_price
        # Update AMT dynamics tracker (infer side from price movement)
        _side = "buy" if price >= self._last_price else "sell"
        self._amt_tracker.update(price, size, _side)
        if price_changed:
            self._last_price = price
        else:
            # Still check orderflow timer even without price change
            if self._any_at_level and (now - self._last_orderflow_emit) >= self._orderflow_interval:
                self._emit_orderflow_update(price)
                self._last_orderflow_emit = now
            self._check_positions(price)
            return

        at_level_levels = []
        newly_touched = []

        # Clear stale confluence clusters each tick
        for level in self._levels:
            level.cluster = []

        for level in self._levels:
            if level.status == LevelStatus.TRIGGERED:
                continue

            dist = level.abs_distance_ticks(price)
            old_status = level.status

            if dist <= level.at_level_ticks:
                if old_status != LevelStatus.AT_LEVEL:
                    level.status = LevelStatus.AT_LEVEL
                    level.touched_at = now
                    newly_touched.append(level)
                at_level_levels.append(level)

            elif dist <= level.approach_ticks:
                if old_status == LevelStatus.WATCHING:
                    level.status = LevelStatus.APPROACHING
                    level.approach_price = price
                    self._on_level_approaching(level, price, dist)

            elif old_status in (LevelStatus.AT_LEVEL, LevelStatus.APPROACHING):
                if dist > level.reject_ticks:
                    level.status = LevelStatus.REJECTED
                    self._on_level_rejected(level, price)
                    level.status = LevelStatus.WATCHING

        # Mark confluence clusters before emitting touch events
        if len(at_level_levels) > 1:
            cluster_names = tuple(l.name for l in at_level_levels)
            for l in at_level_levels:
                l.cluster = [n for n in cluster_names if n != l.name]

        # Emit touch events after confluence is computed
        for level in newly_touched:
            self._on_level_touched(level, price)

        self._any_at_level = bool(at_level_levels)
        if self._any_at_level and (now - self._last_orderflow_emit) >= self._orderflow_interval:
            self._emit_orderflow_update(price)
            self._last_orderflow_emit = now

        # Zone entry detection for DQN inference (with 60s cooldown per zone)
        # Use quantized center price as stable key (survives zone rebuilds)
        newly_entered_zones = []
        still_in_zones: set[float] = set()
        _ZONE_COOLDOWN_S = 60.0
        for zone in self._zones:
            if zone.lower_bound <= price <= zone.upper_bound:
                zkey = round(zone.center_price / TICK_SIZE) * TICK_SIZE
                still_in_zones.add(zkey)
                if zkey not in self._zone_debounce:
                    last_fire = getattr(self, "_zone_last_fire", {}).get(zkey, 0)
                    if (now - last_fire) >= _ZONE_COOLDOWN_S:
                        self._zone_debounce.add(zkey)
                        if not hasattr(self, "_zone_last_fire"):
                            self._zone_last_fire = {}
                        self._zone_last_fire[zkey] = now
                        newly_entered_zones.append(zone)
        self._zone_debounce &= still_in_zones

        # Only fire inference for the BEST zone (highest confluence) per tick
        if newly_entered_zones:
            best_zone = max(newly_entered_zones, key=lambda z: (z.member_count, z.hierarchy_score))
            logger.info(
                "Zone touch: price=%.2f zone=%.2f (%.2f-%.2f) members=%d",
                price,
                best_zone.center_price,
                best_zone.lower_bound,
                best_zone.upper_bound,
                best_zone.member_count,
            )
            self._emit_zone_dqn_inference(best_zone, price)

        self._check_positions(price)

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
            result.append(
                {
                    "name": level.name,
                    "price": level.price,
                    "category": level.category,
                    "status": level.status.value,
                    "distance_ticks": round(level.distance_ticks(price), 1),
                    "cluster": level.cluster,
                }
            )
        result.sort(key=lambda x: abs(x["distance_ticks"]))
        return result

    def set_session_context(self, ctx: dict) -> None:
        """Store live session context for DQN inference.

        Called by compute_session route with VWAP bands, VP, TPO, session levels, macro.
        This allows _build_rl_state to pass complete context to the model.
        """
        self._session_context = ctx
        # Initialize AMT dynamics tracker with session IB/VA/POC
        amt_init = {}
        vp = ctx.get("volume_profile")
        sl = ctx.get("session_levels")
        if vp:
            amt_init["vah"] = vp.vah if hasattr(vp, "vah") else 0
            amt_init["val"] = vp.val if hasattr(vp, "val") else 0
            amt_init["poc"] = vp.poc if hasattr(vp, "poc") else 0
        if sl:
            amt_init["ib_high"] = sl.ib_high if hasattr(sl, "ib_high") else 0
            amt_init["ib_low"] = sl.ib_low if hasattr(sl, "ib_low") else 0
        tpo = ctx.get("tpo_profile")
        if tpo and isinstance(tpo, dict):
            amt_init["single_prints"] = tpo.get("single_prints", [])
        self._amt_tracker.initialize(amt_init)
        if "atr" in ctx:
            self._session_atr = ctx["atr"]
        logger.info("LevelMonitor session context updated (%d keys)", len(ctx))
        # Rebuild zones with correct ATR (startup used default ATR=40.0)
        self._rebuild_zones()

    def set_tick_buffer(self, tick_buffer) -> None:
        """Provide access to the stream's TickBuffer for orderflow computation."""
        self._tick_buffer = tick_buffer

    def set_candle_flow_source(self, fn) -> None:
        """Provide callable that returns recent CandleFlow candles for orderflow."""
        self._candle_flow_fn = fn

    def _compute_orderflow_snapshot(self) -> dict:
        """Compute orderflow signals for both directions and package as snapshot."""
        from .orderflow import compute_signals

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

    # --- Position target tracking ---

    def register_position(self, trade_id: int, direction: str, entry: float, stop: float, targets: list[dict]) -> None:
        """Register an open position for target monitoring."""
        self._open_positions.append(
            {
                "trade_id": trade_id,
                "direction": direction,
                "entry_price": entry,
                "stop_price": stop,
                "targets": [{"name": t["name"], "price": t["price"], "hit": False} for t in targets],
            }
        )

    def close_position(self, trade_id: int) -> None:
        """Remove a closed position from monitoring."""
        self._open_positions = [p for p in self._open_positions if p["trade_id"] != trade_id]

    def _check_positions(self, price: float) -> None:
        """Check if any open position has reached a target level."""
        # Tick-rate mark update so peak_R is accurate when a zone touch
        # later consults it for the EarlyExit lock.
        broker = getattr(self, "_broker_adapter", None)
        if broker is not None and not broker.tracker.is_flat:
            broker.tracker.update_mark(price)

        for pos in self._open_positions:
            for target in pos["targets"]:
                if target["hit"]:
                    continue
                dist = abs(price - target["price"]) / TICK_SIZE
                if dist <= self.AT_LEVEL_TICKS:
                    target["hit"] = True
                    snapshot = self._compute_orderflow_snapshot()
                    self._publish(
                        {
                            "type": "position_at_target",
                            "trade_id": pos["trade_id"],
                            "target_name": target["name"],
                            "target_price": target["price"],
                            "price": price,
                            "direction": pos["direction"],
                            "orderflow": snapshot,
                        }
                    )

    # --- SSE event emitters ---

    def _on_level_approaching(self, level: MonitoredLevel, price: float, dist: float) -> None:
        self._publish(
            {
                "type": "level_approaching",
                "level": level.name,
                "level_price": level.price,
                "category": level.category,
                "price": price,
                "distance_ticks": round(dist, 1),
            }
        )
        self._emit_dqn_inference(level, price, "approaching")

    def _on_level_touched(self, level: MonitoredLevel, price: float) -> None:
        snapshot = self._compute_orderflow_snapshot()
        self._publish(
            {
                "type": "level_touched",
                "level": level.name,
                "level_price": level.price,
                "category": level.category,
                "price": price,
                "confluence": level.cluster,
                "orderflow": snapshot,
                "amt_dynamics": self._amt_tracker.snapshot(),
            }
        )
        # Schedule async ML/macro fetch
        if self._loop and self._db_session_factory:
            asyncio.run_coroutine_threadsafe(
                self._emit_level_context(level.name, level.price),
                self._loop,
            )

        # ML feature extraction + outcome tracking
        self._handle_ml_touch(level, price, snapshot)
        self._emit_dqn_inference(level, price, "touched")

    async def _emit_level_context(self, level_name: str, level_price: float) -> None:
        """Fetch ML predictions and macro data, emit as follow-up event.

        Uses a lock to prevent concurrent calls from exhausting the DB pool —
        get_indicators() and fetch_macro_snapshot() hold sessions for seconds.
        """
        if self._level_context_lock and self._level_context_lock.locked():
            return  # Already running, skip duplicate
        async with self._level_context_lock:
            try:
                from ..services.market_service import MarketService

                # Get indicators (holds DB session briefly, then releases)
                db = self._db_session_factory()
                try:
                    svc = MarketService(db)
                    indicators = await svc.get_indicators()
                finally:
                    db.close()

                # Macro fetch is pure HTTP — no DB session needed
                macro_data = {}
                try:
                    from ..market_data.macro_provider import fetch_macro_snapshot

                    macro = await fetch_macro_snapshot()
                    macro_data = {
                        "vix": macro.vix,
                        "vix_change_pct": macro.vix_change_pct,
                        "regime": macro.regime,
                        "regime_score": macro.regime_score,
                    }
                except Exception:
                    pass

                self._publish(
                    {
                        "type": "level_context",
                        "level": level_name,
                        "level_price": level_price,
                        "ml": {
                            "day_type": indicators.get("ml_day_type"),
                            "day_type_confidence": indicators.get("ml_day_type_confidence"),
                        },
                        "macro": macro_data,
                        "amt_dynamics": self._amt_tracker.snapshot(),
                    }
                )
            except Exception as e:
                logger.warning("Failed to emit level_context for %s: %s", level_name, e)

    def _get_approach_direction(self, level: MonitoredLevel) -> str:
        if level.approach_price is not None and level.approach_price < level.price:
            return "from_below"
        return "from_above"

    def _handle_ml_touch(self, level: MonitoredLevel, price: float, orderflow_snapshot: dict) -> None:
        """Extract ML features, optionally predict, register with outcome tracker."""
        try:
            from datetime import datetime

            from src.ml.features.level_touch_features import extract_level_touch_features
            from src.ml.level_touch.compute import (
                compute_approach_volume_features,
                compute_candle_pattern_features,
                compute_temporal_derivatives,
            )

            approach_dir = self._get_approach_direction(level)
            direction = "long" if approach_dir == "from_below" else "short"

            # Get orderflow signals for the approach direction
            of_signals = None
            if self._candle_flow_fn:
                from .orderflow import compute_signals

                candles = self._candle_flow_fn()
                if candles and len(candles) >= 3:
                    of_signals = compute_signals(candles, direction, lookback=10)

            # Compute temporal derivatives from candle data
            temporal = {}
            candle_patterns = {}
            if self._candle_flow_fn:
                candles = self._candle_flow_fn()
                if candles:
                    candle_dicts = []
                    for c in candles:
                        candle_dicts.append(
                            {
                                "delta": getattr(c, "delta", 0),
                                "volume": getattr(c, "volume", 0),
                                "tick_count": getattr(c, "tick_count", 0),
                                "spread": getattr(c, "spread", 0),
                                "body_ratio": getattr(c, "body_ratio", 0),
                                "stacked_imbalance_count": (
                                    len(getattr(c, "stacked_imbalances", [])) if hasattr(c, "stacked_imbalances") else 0
                                ),
                                "open": getattr(c, "open", 0),
                                "close": getattr(c, "close", 0),
                            }
                        )
                    temporal = compute_temporal_derivatives(candle_dicts)
                    candle_patterns = compute_candle_pattern_features(candle_dicts)
                    # Approach volume: how volume behaves coming into the level
                    approach_vol_dicts = [
                        {
                            "volume": getattr(c, "volume", 0),
                            "delta": getattr(c, "delta", 0),
                            "buy_volume": getattr(c, "buy_volume", 0),
                            "sell_volume": getattr(c, "sell_volume", 0),
                        }
                        for c in candles
                    ]
                    approach_vol = compute_approach_volume_features(approach_vol_dicts)
                    temporal.update(approach_vol)

            # Build feature dict
            features = extract_level_touch_features(
                level_type=level.name.lower().replace(" ", "_").replace("+", "").replace("-", ""),
                level_category=level.category,
                approach_direction=approach_dir,
                level_confluence=len(level.cluster),
                # Orderflow signals
                delta=of_signals.delta if of_signals else None,
                delta_aligned=of_signals.delta_aligned if of_signals else None,
                delta_divergence=of_signals.delta_divergence if of_signals else None,
                delta_unwind=of_signals.delta_unwind if of_signals else None,
                cvd=of_signals.cvd if of_signals else None,
                cvd_trend=of_signals.cvd_trend if of_signals else None,
                vsa_absorption=of_signals.vsa_absorption if of_signals else None,
                tick_vol_accelerating=of_signals.tick_vol_accelerating if of_signals else None,
                trapped_traders=of_signals.trapped_traders if of_signals else None,
                passive_active_ratio=of_signals.passive_active_ratio if of_signals else None,
                big_trades_count=of_signals.big_trades_count if of_signals else None,
                big_trades_net_delta=of_signals.big_trades_net_delta if of_signals else None,
                stop_run_detected=of_signals.stop_run_detected if of_signals else None,
                imbalance_ratio_max=of_signals.imbalance_ratio_max if of_signals else None,
                stacked_imbalance_count=of_signals.stacked_imbalance_count if of_signals else None,
                stacked_imbalance_direction=of_signals.stacked_imbalance_direction if of_signals else None,
                # Temporal derivatives
                **temporal,
                # Candle patterns
                **candle_patterns,
            )

            # Add buy/sell volume from last candle (for book gauges on frontend)
            if self._candle_flow_fn:
                candles = self._candle_flow_fn()
                if candles:
                    last_c = candles[-1]
                    features["buy_volume"] = getattr(last_c, "buy_volume", None)
                    features["sell_volume"] = getattr(last_c, "sell_volume", None)

            # Cache features for live refresh on orderflow_update
            self._last_ml_features = features
            self._active_level_name = level.name

            # Emit full feature snapshot for gauge dashboard
            self._publish(
                {
                    "type": "ml_features",
                    "level": level.name,
                    "features": features,
                    "timestamp": time.time(),
                }
            )

            # Optional: Run ML prediction if model loaded
            prediction = None
            confidence = None
            try:
                from src.ml.serving.predictor import get_predictor

                predictor = get_predictor()
                if predictor.is_loaded("level_classifier"):
                    pred_result = predictor.predict("level_classifier", features)
                    if pred_result and isinstance(pred_result, dict):
                        prediction = pred_result.get("class_name")
                        confidence = pred_result.get("confidence")

                        # Confidence gating
                        ACTIONABLE = {"strong_reversal", "strong_continuation"}
                        threshold = 0.50 if prediction in ACTIONABLE else 0.35
                        surfaced = prediction if confidence and confidence > threshold else "uncertain"

                        self._publish(
                            {
                                "type": "ml_prediction",
                                "level": level.name,
                                "predicted": surfaced,
                                "raw_predicted": prediction,
                                "confidence": confidence,
                                "probabilities": pred_result.get("probabilities", {}),
                            }
                        )

                        from src.ml.level_touch import set_last_prediction

                        set_last_prediction(
                            {
                                "level": level.name,
                                "predicted": surfaced,
                                "raw_predicted": prediction,
                                "confidence": confidence,
                                "probabilities": pred_result.get("probabilities", {}),
                                "timestamp": time.time(),
                            }
                        )
            except Exception:
                logger.debug("ML prediction not available", exc_info=True)

            # Register with outcome tracker
            if self._outcome_tracker is not None:
                self._outcome_tracker.register_touch(
                    symbol="NQ",
                    level_name=level.name,
                    level_type=level.name.lower().replace(" ", "_"),
                    level_price=level.price,
                    approach_direction=approach_dir,
                    touch_ts=time.time(),
                    session_date=datetime.now().strftime("%Y-%m-%d"),
                    features=features,
                    prediction=prediction,
                    prediction_confidence=confidence,
                )
        except Exception:
            logger.exception("ML feature extraction failed for %s", level.name)

    def _on_level_rejected(self, level: MonitoredLevel, price: float) -> None:
        if self._active_level_name == level.name:
            self._last_ml_features = None
            self._active_level_name = None
        self._publish(
            {
                "type": "level_rejected",
                "level": level.name,
                "level_price": level.price,
            }
        )

    def _recompute_live_features(self, of_snapshot: dict, price: float) -> dict:
        """Refresh live-changing features (orderflow + temporal + candle) on cached base."""
        features = dict(self._last_ml_features)  # shallow copy
        long_of = of_snapshot.get("long", {})

        # Refresh orderflow fields from fresh snapshot
        of_keys = [
            "delta",
            "delta_aligned",
            "delta_divergence",
            "delta_unwind",
            "cvd",
            "cvd_trend",
            "vsa_absorption",
            "tick_vol_accelerating",
            "trapped_traders",
            "passive_active_ratio",
            "big_trades_count",
            "big_trades_net_delta",
            "stop_run_detected",
            "imbalance_ratio_max",
            "stacked_imbalance_count",
            "stacked_imbalance_direction",
        ]
        for k in of_keys:
            if k in long_of:
                features[k] = long_of[k]

        # Refresh temporal derivatives + candle patterns from fresh candles
        if self._candle_flow_fn:
            try:
                from src.ml.level_touch.compute import (
                    compute_approach_volume_features,
                    compute_candle_pattern_features,
                    compute_temporal_derivatives,
                )

                candles = self._candle_flow_fn()
                if candles:
                    candle_dicts = [
                        {
                            "delta": getattr(c, "delta", 0),
                            "volume": getattr(c, "volume", 0),
                            "tick_count": getattr(c, "tick_count", 0),
                            "spread": getattr(c, "spread", 0),
                            "body_ratio": getattr(c, "body_ratio", 0),
                            "stacked_imbalance_count": len(getattr(c, "stacked_imbalances", []))
                            if hasattr(c, "stacked_imbalances")
                            else 0,
                            "open": getattr(c, "open", 0),
                            "close": getattr(c, "close", 0),
                        }
                        for c in candles
                    ]
                    features.update(compute_temporal_derivatives(candle_dicts))
                    # Approach volume features
                    approach_vol_dicts = [
                        {
                            "volume": getattr(c, "volume", 0),
                            "delta": getattr(c, "delta", 0),
                            "buy_volume": getattr(c, "buy_volume", 0),
                            "sell_volume": getattr(c, "sell_volume", 0),
                        }
                        for c in candles
                    ]
                    features.update(compute_approach_volume_features(approach_vol_dicts))
                    features.update(compute_candle_pattern_features(candle_dicts))
                    # Refresh last candle specifics
                    if candle_dicts:
                        features["last_candle_delta"] = candle_dicts[-1].get("delta")
                        features["last_candle_body_ratio"] = candle_dicts[-1].get("body_ratio")
                    # Refresh buy/sell volume from latest CandleFlow
                    if candles:
                        last_c = candles[-1]
                        features["buy_volume"] = getattr(last_c, "buy_volume", None)
                        features["sell_volume"] = getattr(last_c, "sell_volume", None)
            except Exception:
                pass

        return features

    def _emit_orderflow_update(self, price: float) -> None:
        snapshot = self._compute_orderflow_snapshot()
        if snapshot:
            self._publish(
                {
                    "type": "orderflow_update",
                    "price": price,
                    "ts": time.time(),
                    "orderflow": snapshot,
                }
            )

            # Emit refreshed ML feature snapshot for gauge dashboard
            if self._last_ml_features is not None:
                try:
                    updated = self._recompute_live_features(snapshot, price)
                    self._last_ml_features = updated
                    self._publish(
                        {
                            "type": "ml_features",
                            "level": self._active_level_name,
                            "features": updated,
                            "timestamp": time.time(),
                        }
                    )
                except Exception:
                    pass

        # DQN inference refresh while at level
        if self._active_level_name:
            active = next((l for l in self._levels if l.name == self._active_level_name), None)
            if active:
                self._emit_dqn_inference(active, price, "approaching")

    def _emit_dqn_inference(self, level: MonitoredLevel, price: float, trigger: str) -> None:
        """Run DQN inference and emit dqn_inference SSE event."""
        try:
            from src.rl.live_inference import get_dqn_inference

            dqn = get_dqn_inference()
            if not dqn.is_loaded:
                return
            rl_state = self._build_rl_state(level, price)
            result = dqn.infer(rl_state)
            if result is not None:
                self._publish(
                    {
                        "type": "dqn_inference",
                        "trigger": trigger,
                        "level": level.name,
                        "level_price": level.price,
                        **result,
                        "timestamp": time.time(),
                    }
                )
        except Exception:
            logger.debug("DQN inference failed for %s", level.name, exc_info=True)

    def _emit_zone_dqn_inference(self, zone: Zone, price: float) -> None:
        try:
            from src.rl.live_inference import get_dqn_inference

            dqn = get_dqn_inference()
            if not dqn.is_loaded:
                return
            rl_state = self._build_rl_state_zone(zone, price)

            # Inject live position state so infer() can evaluate pyramid /
            # reversal-exit / early-exit relative to the open trade. Without
            # this the caller sees pos_side=flat and all three outputs are
            # inert. Uses the server-side broker tracker; if running with no
            # broker attached (relay-only) the fields stay empty and infer()
            # falls back to flat behavior.
            broker = getattr(self, "_broker_adapter", None)
            if broker is not None and not broker.tracker.is_flat:
                tr = broker.tracker
                uR = tr.update_mark(price)
                rl_state["position_state"] = [
                    0.0,
                    1.0 if tr.side == "long" else 0.0,
                    1.0 if tr.side == "short" else 0.0,
                    float(uR),
                ]
                rl_state["position_size"] = float(tr.size)

            result = dqn.infer(rl_state)
            if result is not None:
                self._publish(
                    {
                        "type": "dqn_inference",
                        "trigger": "zone_entry",
                        "zone_members": zone.member_count,
                        "zone_center": zone.center_price,
                        "zone_hierarchy": round(zone.hierarchy_score, 3),
                        **result,
                        "timestamp": time.time(),
                    }
                )

                # Persist signal for post-session review
                try:
                    from src.rl.signal_log import log_signal

                    approach = "up" if price < zone.center_price else "down"
                    log_signal(
                        price=price,
                        zone_center=zone.center_price,
                        zone_members=zone.member_count,
                        zone_hierarchy=zone.hierarchy_score,
                        inference_result=result,
                        approach_direction=approach,
                    )
                except Exception:
                    pass

            # Collect live episode for continuous training
            try:
                from src.rl.live_collector import get_live_collector

                approach = "up" if price < zone.center_price else "down"
                get_live_collector().on_zone_touch(
                    rl_state,
                    price,
                    approach,
                    level_type=f"zone_{zone.member_count}m",
                    zone_members=zone.member_count,
                )
            except Exception:
                # Previously `except: pass` — which masked why live_episodes/
                # stayed empty even though signals were firing. Log at warning
                # so root-logger=WARNING still surfaces it. Never block inference.
                logger.warning("live_collector.on_zone_touch failed", exc_info=True)

            # Execute via broker if enabled (server-side path).
            broker = getattr(self, "_broker_adapter", None)
            if broker is not None and result is not None:
                import asyncio

                # In-position handling (pyramid / reversal exit / early-exit lock).
                # Runs BEFORE on_signal so an aligned touch on a winner isn't
                # misrouted to trail-stop when the framework says "add" or
                # "reverse exit". Skips the entry-signal dispatch entirely when
                # the position is already open.
                if not broker.tracker.is_flat:
                    try:
                        tr = broker.tracker
                        rev = result.get("reversal_signals") or {}
                        pyr = result.get("pyramid_decision") or {}
                        ee_prob = float(result.get("early_exit_prob") or 0.0)
                        ee_thresh = float(result.get("early_exit_threshold") or 0.70)

                        if rev.get("should_exit"):
                            logger.info(
                                "Reversal-signals exit: %d fired — flattening %s @ %.2f",
                                rev.get("fired_count", 0),
                                tr.side,
                                price,
                            )
                            asyncio.create_task(broker.flatten("reversal_signals"))
                        elif not tr.locked_half_R and tr.peak_R >= 0.5 and ee_prob >= ee_thresh:
                            logger.info(
                                "EarlyExit lock: peak_R=%.2f ee_prob=%.3f>=%.2f — flattening %s @ %.2f",
                                tr.peak_R,
                                ee_prob,
                                ee_thresh,
                                tr.side,
                                price,
                            )
                            tr.locked_half_R = True
                            asyncio.create_task(broker.flatten("early_exit_lock"))
                        elif pyr.get("should_add"):
                            add_size = int(max(1, round(float(pyr.get("add_size") or 0))))
                            logger.info(
                                "Pyramid add (%s): +%d @ %.2f (%s)",
                                tr.side,
                                add_size,
                                price,
                                pyr.get("detail", ""),
                            )
                            asyncio.create_task(broker.add_to_position(add_size, price))

                        # Suppress the entry-signal dispatch while in-position —
                        # trail/flip is replaced by the three decisions above.
                        result = None
                    except Exception:
                        logger.warning("In-position handling failed", exc_info=True)

            if broker is not None and result is not None:
                action = result.get("action", "SKIP")
                confidence = result.get("confidence", 0.0)
                # FORCE_REV_ONLY: training data shows CONT is effectively a weak REV
                # (~0% argmax). Convert CONT → equivalent REV direction.
                if action == "CONTINUATION":
                    action = "REVERSAL"
                of_score = _compute_orderflow_score_live(rl_state, zone, price, action)
                # When trading_paused flag is set, the broker path also stops
                # firing — raise the confidence bar above any realistic model output.
                _broker_conf_floor = 0.99 if _trading_paused() else 0.15
                # Log the gate decision so silent veto causes are visible in
                # deploy logs without having to attach a debugger.
                if action in ("SKIP", "skip"):
                    logger.debug("broker gate: SKIP action, zone=%.2f", zone.center_price)
                elif confidence < _broker_conf_floor:
                    logger.info(
                        "broker gate: conf %.3f < %.2f at zone %.2f — vetoed",
                        confidence, _broker_conf_floor, zone.center_price,
                    )
                elif of_score < 0.30:
                    logger.info(
                        "broker gate: of_score %.3f < 0.30 at zone %.2f (conf=%.3f, %s) — vetoed",
                        of_score, zone.center_price, confidence, action,
                    )
                else:
                    logger.info(
                        "broker gate PASSED: of=%.3f conf=%.3f action=%s zone=%.2f — dispatching",
                        of_score, confidence, action, zone.center_price,
                    )
                if action not in ("SKIP", "skip") and confidence >= _broker_conf_floor and of_score >= 0.30:
                    import asyncio

                    try:
                        approach = "up" if price < zone.center_price else "down"
                        sig_action = "enter_short" if approach == "up" else "enter_long"
                        is_long = "long" in sig_action
                        stop_ticks = int(max(6, min(50, result.get("stop_ticks") or 25)))
                        # Weak-but-passing orderflow → widen stop (more room)
                        if of_score < 0.70:
                            stop_ticks = max(stop_ticks, 23)
                        stop_offset = stop_ticks * 0.25
                        raw_stop = price - stop_offset if is_long else price + stop_offset
                        broker_signal = {
                            "action": sig_action,
                            "price": price,
                            "stop_price": round(raw_stop * 4) / 4,
                            "size": result.get("size_multiplier", result.get("sizing_signal", 1.0)),
                            "confidence": confidence,
                            "orderflow_score": of_score,
                        }
                        asyncio.create_task(broker.on_signal(broker_signal))
                    except Exception:
                        logger.warning("Broker execution failed", exc_info=True)

            # Send via relay callbacks (trading service + arnoldstocks clients)
            callbacks = getattr(self, "_signal_callbacks", set())
            if not callbacks and result is not None:
                action = result.get("action", "SKIP")
                if action not in ("SKIP", "skip"):
                    logger.warning(
                        "Signal %s (conf=%.3f) at zone %.2f but NO relay clients connected — signal lost",
                        action,
                        result.get("confidence", 0),
                        zone.center_price,
                    )
            if callbacks and result is not None:
                import asyncio

                def _send(msg):
                    for cb in list(callbacks):
                        try:
                            if asyncio.iscoroutinefunction(cb):
                                asyncio.create_task(cb(msg))
                            else:
                                cb(msg)
                        except Exception:
                            logger.warning("Signal callback failed", exc_info=True)

                # Always send full inference for DQN page visualization
                _send(
                    {
                        "type": "dqn_inference",
                        "trigger": "zone_entry",
                        "zone_members": zone.member_count,
                        "zone_center": zone.center_price,
                        "zone_hierarchy": round(zone.hierarchy_score, 3),
                        **result,
                        "price": price,
                        "timestamp": time.time(),
                    }
                )

                # Send trading signal — filter low confidence + fix stop calculation
                action = result.get("action", "SKIP")
                confidence = result.get("confidence", 0.0)
                # 0.30 normally; 0.99 when the trading_paused flag is set so we
                # collect signals + episodes without placing any orders.
                MIN_SIGNAL_CONFIDENCE = 0.99 if _trading_paused() else 0.30

                if action in ("SKIP", "skip"):
                    logger.debug("SKIP for zone %.2f (conf=%.3f)", zone.center_price, confidence)
                elif confidence < MIN_SIGNAL_CONFIDENCE:
                    logger.info(
                        "Signal filtered: %s conf=%.3f < %.2f at zone %.2f",
                        action,
                        confidence,
                        MIN_SIGNAL_CONFIDENCE,
                        zone.center_price,
                    )
                else:
                    approach = "up" if price < zone.center_price else "down"
                    if action == "CONTINUATION":
                        sig_action = "enter_long" if approach == "up" else "enter_short"
                    else:  # REVERSAL
                        sig_action = "enter_short" if approach == "up" else "enter_long"
                    is_long = "long" in sig_action

                    stop_ticks = result.get("stop_ticks") or 25  # default 25 ticks if None
                    stop_ticks = int(max(6, min(50, stop_ticks)))  # match stop_policy bounds
                    stop_offset = stop_ticks * 0.25  # NQ tick = 0.25 points
                    stop_price = price - stop_offset if is_long else price + stop_offset
                    # Round to NQ tick increment (0.25)
                    stop_price = round(stop_price * 4) / 4

                    logger.info(
                        "Dispatching signal: %s conf=%.3f price=%.2f stop=%.2f (%d ticks) zone=%.2f",
                        sig_action,
                        confidence,
                        price,
                        stop_price,
                        stop_ticks,
                        zone.center_price,
                    )
                    signal_payload = {
                        "type": "signal",
                        "action": sig_action,
                        "price": price,
                        "stop_price": stop_price,
                        "size": result.get("size_multiplier", result.get("sizing_signal", 1.0)),
                        "confidence": confidence,
                        "cont_p": result.get("cont_p"),
                        "rev_p": result.get("rev_p"),
                        "stop_ticks": stop_ticks,
                        "model_type": result.get("model_type"),
                        "zone": zone.center_price,
                        "zone_members": zone.member_count,
                        "ts": time.time(),
                    }
                    _send(signal_payload)
                    # Persist to stock_signals for later correlation with
                    # realized broker_trades. Threaded + swallowed errors so
                    # signal dispatch is never blocked or aborted by the DB.
                    _persist_stock_signal_async(signal_payload)
        except Exception:
            logger.warning("DQN zone inference failed", exc_info=True)

    def set_broker_adapter(self, adapter) -> None:
        """Set broker adapter for automated execution."""
        self._broker_adapter = adapter

    def add_signal_callback(self, fn) -> None:
        """Add a callback for zone signals. Multiple clients supported.

        Resets the zone debounce on connect so that if price is already
        inside a zone when the relay connects, the next tick fires fresh
        (the zone touch may have fired before any callbacks were registered).
        """
        if not hasattr(self, "_signal_callbacks"):
            self._signal_callbacks = set()
        self._signal_callbacks.add(fn)
        # Clear debounce + last-fire history so zones re-evaluate immediately.
        # Prevents the case where a touch fired while no callbacks were registered
        # (e.g. server started before arnoldstocks connected) from silently locking
        # the zone for 60 seconds.
        self._zone_debounce = set()
        if hasattr(self, "_zone_last_fire"):
            self._zone_last_fire = {}
        logger.info("Signal callback added (%d total) — zone debounce reset", len(self._signal_callbacks))

    def remove_signal_callback(self, fn) -> None:
        """Remove a signal callback."""
        if hasattr(self, "_signal_callbacks"):
            self._signal_callbacks.discard(fn)
            logger.info("Signal callback removed (%d remaining)", len(self._signal_callbacks))

    def set_signal_callback(self, fn) -> None:
        """Legacy single-callback API. Use add/remove for multi-client."""
        if fn is not None:
            self.add_signal_callback(fn)
        # None means remove — but we don't know which one, handled by remove_signal_callback

    def _get_zone_memory(self) -> dict:
        """Get the zone touch memory dict. Created lazily on first access."""
        if not hasattr(self, "_zone_touch_history"):
            self._zone_touch_history = {}  # zone_key → {touch_count, last_result, last_ts}
        return self._zone_touch_history

    def record_zone_touch(self, zone_key: float, result: float = 0.0) -> None:
        """Record a zone touch result for memory features.

        Args:
            zone_key: Zone center price snapped to tick grid.
            result: +1.0 if price bounced (reversal worked), -1.0 if broke through
                    (continuation worked), 0.0 if unknown/first touch.
        """
        import time as _time

        mem = self._get_zone_memory()
        entry = mem.get(zone_key, {"touch_count": 0, "last_result": 0.0, "last_ts": 0.0})
        entry["touch_count"] = entry["touch_count"] + 1
        entry["last_result"] = result
        entry["last_ts"] = _time.time()
        mem[zone_key] = entry

    def _build_zone_memory_for_state(self) -> dict:
        """Build zone_memory dict for the RL state.

        Returns dict mapping zone_key → {touch_count, last_result, time_since_last}.
        """
        import time as _time

        now = _time.time()
        mem = self._get_zone_memory()
        result = {}
        for key, entry in mem.items():
            result[key] = {
                "touch_count": entry["touch_count"],
                "last_result": entry["last_result"],
                "time_since_last": now - entry["last_ts"] if entry["last_ts"] > 0 else 3600,
            }
        return result

    def _build_rl_state_zone(self, zone: Zone, price: float) -> dict:
        import time as _time

        candles = self._candle_flow_fn() if self._candle_flow_fn else []
        ctx = self._session_context or {}
        approach = "up" if price < zone.center_price else "down"
        recent_ticks = []
        if self._tick_buffer:
            with contextlib.suppress(Exception):
                recent_ticks = self._tick_buffer.get_recent(50)

        # Record this zone touch in memory
        zone_key = round(zone.center_price * 4) / 4
        self.record_zone_touch(zone_key)

        # Compute orderflow signals on-demand from the live candle flow. The
        # stocks init path doesn't populate orderflow_signals into session
        # context, so without this the live gate `of_score >= 0.30` is
        # unreachable (only the 0.20 delta-direction component can fire,
        # vetoing every entry).
        of_signals = ctx.get("orderflow_signals")
        if of_signals is None and candles and len(candles) >= 3:
            with contextlib.suppress(Exception):
                from .orderflow import compute_signals

                direction = "long" if approach == "up" else "short"
                of_signals = compute_signals(candles, direction, lookback=10)

        return {
            "zone": zone,
            "all_zones": self._zones,
            "price": price,
            "touch_epoch": _time.time(),
            "approach_direction": approach,
            "candles": candles or [],
            "candles_5m": ctx.get("candles_5m", []),
            "vwap_bands": ctx.get("vwap_bands"),
            "volume_profile": ctx.get("volume_profile"),
            "tpo_profile": ctx.get("tpo_profile"),
            "tpo_profile_obj": ctx.get("tpo_profile_obj"),
            "session_tpos": ctx.get("session_tpos"),
            "session_levels": ctx.get("session_levels"),
            "all_levels": [l.price for l in self._levels],
            "orderflow_signals": of_signals,
            "macro": ctx.get("macro"),
            "session_context": {**(ctx.get("session_context") or {}), **(ctx.get("amt_context") or {})},
            "day_type": ctx.get("day_type"),
            "fvgs": ctx.get("fvgs", []),
            "single_print_zones": ctx.get("single_print_zones", []),
            "recent_ticks": recent_ticks,
            "swing_structure": ctx.get("swing_structure"),
            "amt_dynamics": self._amt_tracker.snapshot(),
            "zone_memory": self._build_zone_memory_for_state(),
        }

    def _build_rl_state(self, level: MonitoredLevel, price: float) -> dict:
        """Assemble a state dict compatible with RL build_observation().

        Uses _session_context (populated by compute_session) for complete
        VWAP, VP, TPO, session level, and macro context. Without it, the
        model gets ~60% zeros and can't make meaningful predictions.
        """
        import time as _time

        from src.rl.config import LevelType

        # Map level name to LevelType enum
        name_lower = level.name.lower().replace(" ", "_").replace("+", "").replace("-", "")
        level_type_map = {
            "poc": LevelType.DAILY_POC,
            "daily_poc": LevelType.DAILY_POC,
            "vah": LevelType.DAILY_VAH,
            "daily_vah": LevelType.DAILY_VAH,
            "val": LevelType.DAILY_VAL,
            "daily_val": LevelType.DAILY_VAL,
            "vwap": LevelType.VWAP,
            "vwap_1sd_upper": LevelType.VWAP_SD1,
            "vwap_1sd_lower": LevelType.VWAP_SD1,
            "vwap_2sd_upper": LevelType.VWAP_SD2,
            "vwap_2sd_lower": LevelType.VWAP_SD2,
            "vwap_3sd_upper": LevelType.VWAP_SD3,
            "vwap_3sd_lower": LevelType.VWAP_SD3,
            "pdh": LevelType.PDH,
            "pdl": LevelType.PDL,
            "tokyo_high": LevelType.TOKYO_HIGH,
            "tokyo_low": LevelType.TOKYO_LOW,
            "nyib_high": LevelType.NYIB_HIGH,
            "nyib_low": LevelType.NYIB_LOW,
            "tpoc": LevelType.TPOC,
            "tvah": LevelType.TVAH,
            "tval": LevelType.TVAL,
            "tibh": LevelType.TIBH,
            "tibl": LevelType.TIBL,
            "naked_poc": LevelType.NAKED_POC,
            "daily_swing_high": LevelType.DAILY_SWING_HIGH,
            "daily_swing_low": LevelType.DAILY_SWING_LOW,
            "weekly_swing_high": LevelType.WEEKLY_SWING_HIGH,
            "weekly_swing_low": LevelType.WEEKLY_SWING_LOW,
            "monthly_swing_high": LevelType.MONTHLY_SWING_HIGH,
            "monthly_swing_low": LevelType.MONTHLY_SWING_LOW,
        }
        lt = level_type_map.get(name_lower, LevelType.VWAP)

        # Get candles if available
        candles = []
        if self._candle_flow_fn:
            candles = self._candle_flow_fn() or []

        # Pull context from session data (set by compute_session)
        ctx = self._session_context or {}

        # Approach direction from level tracking
        approach = "up" if level.approach_price is not None and level.approach_price < level.price else "down"

        # Recent ticks from tick buffer (for micro features)
        recent_ticks = []
        if self._tick_buffer:
            with contextlib.suppress(Exception):
                recent_ticks = self._tick_buffer.get_recent(50)

        return {
            "level_type": lt,
            "price": price,
            "touch_epoch": _time.time(),
            "approach_direction": approach,
            "candles": candles,
            "candles_5m": ctx.get("candles_5m", []),
            "vwap_bands": ctx.get("vwap_bands"),
            "volume_profile": ctx.get("volume_profile"),
            "tpo_profile": ctx.get("tpo_profile"),
            "tpo_profile_obj": ctx.get("tpo_profile_obj"),
            "session_tpos": ctx.get("session_tpos"),
            "session_levels": ctx.get("session_levels"),
            "all_levels": [l.price for l in self._levels],
            "orderflow_signals": ctx.get("orderflow_signals"),
            "macro": ctx.get("macro"),
            "session_context": {**(ctx.get("session_context") or {}), **(ctx.get("amt_context") or {})},
            "day_type": ctx.get("day_type"),
            "fvgs": ctx.get("fvgs", []),
            "single_print_zones": ctx.get("single_print_zones", []),
            "recent_ticks": recent_ticks,
            "swing_structure": ctx.get("swing_structure"),
            "amt_dynamics": self._amt_tracker.snapshot(),
        }
