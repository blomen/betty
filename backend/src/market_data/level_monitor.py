"""Level proximity monitor. Plugs into DatabentoLiveStream as a tick callback."""

import asyncio
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
    approach_price: float | None = None  # price when WATCHING → APPROACHING

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
        self._open_positions: list[dict] = []  # [{trade_id, direction, entry, stop, targets: [{name, price, hit}]}]
        self._loop: asyncio.AbstractEventLoop | None = None
        self._db_session_factory = None
        self._level_context_lock: asyncio.Lock | None = None
        self._last_ml_features: dict | None = None
        self._active_level_name: str | None = None
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

            if dist <= self.AT_LEVEL_TICKS:
                if old_status != LevelStatus.AT_LEVEL:
                    level.status = LevelStatus.AT_LEVEL
                    level.touched_at = now
                    newly_touched.append(level)
                at_level_levels.append(level)

            elif dist <= self.APPROACHING_TICKS:
                if old_status == LevelStatus.WATCHING:
                    level.status = LevelStatus.APPROACHING
                    level.approach_price = price
                    self._on_level_approaching(level, price, dist)

            elif old_status in (LevelStatus.AT_LEVEL, LevelStatus.APPROACHING):
                if dist > self.REJECT_TICKS:
                    level.status = LevelStatus.REJECTED
                    self._on_level_rejected(level, price)
                    level.status = LevelStatus.WATCHING

        # Mark confluence clusters before emitting touch events
        if len(at_level_levels) > 1:
            cluster_names = [l.name for l in at_level_levels]
            for l in at_level_levels:
                l.cluster = [n for n in cluster_names if n != l.name]

        # Emit touch events after confluence is computed
        for level in newly_touched:
            self._on_level_touched(level, price)

        self._any_at_level = bool(at_level_levels)
        if self._any_at_level and (now - self._last_orderflow_emit) >= self._orderflow_interval:
            self._emit_orderflow_update(price)
            self._last_orderflow_emit = now

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

    # --- Position target tracking ---

    def register_position(self, trade_id: int, direction: str, entry: float, stop: float, targets: list[dict]) -> None:
        """Register an open position for target monitoring."""
        self._open_positions.append({
            "trade_id": trade_id,
            "direction": direction,
            "entry_price": entry,
            "stop_price": stop,
            "targets": [{"name": t["name"], "price": t["price"], "hit": False} for t in targets],
        })

    def close_position(self, trade_id: int) -> None:
        """Remove a closed position from monitoring."""
        self._open_positions = [p for p in self._open_positions if p["trade_id"] != trade_id]

    def _check_positions(self, price: float) -> None:
        """Check if any open position has reached a target level."""
        for pos in self._open_positions:
            for target in pos["targets"]:
                if target["hit"]:
                    continue
                dist = abs(price - target["price"]) / TICK_SIZE
                if dist <= self.AT_LEVEL_TICKS:
                    target["hit"] = True
                    snapshot = self._compute_orderflow_snapshot()
                    self._publish({
                        "type": "position_at_target",
                        "trade_id": pos["trade_id"],
                        "target_name": target["name"],
                        "target_price": target["price"],
                        "price": price,
                        "direction": pos["direction"],
                        "orderflow": snapshot,
                    })

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
        # Schedule async ML/macro fetch
        if self._loop and self._db_session_factory:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._emit_level_context(level.name, level.price))
            )

        # ML feature extraction + outcome tracking
        self._handle_ml_touch(level, price, snapshot)

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

                self._publish({
                    "type": "level_context",
                    "level": level_name,
                    "level_price": level_price,
                    "ml": {
                        "day_type": indicators.get("ml_day_type"),
                        "day_type_confidence": indicators.get("ml_day_type_confidence"),
                    },
                    "macro": macro_data,
                })
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
            from src.ml.level_touch.compute import compute_temporal_derivatives, compute_candle_pattern_features

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
                        candle_dicts.append({
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
                        })
                    temporal = compute_temporal_derivatives(candle_dicts)
                    candle_patterns = compute_candle_pattern_features(candle_dicts)

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

            # Cache features for live refresh on orderflow_update
            self._last_ml_features = features
            self._active_level_name = level.name

            # Emit full feature snapshot for gauge dashboard
            self._publish({
                "type": "ml_features",
                "level": level.name,
                "features": features,
                "timestamp": time.time(),
            })

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

                        self._publish({
                            "type": "ml_prediction",
                            "level": level.name,
                            "predicted": surfaced,
                            "raw_predicted": prediction,
                            "confidence": confidence,
                            "probabilities": pred_result.get("probabilities", {}),
                        })

                        from src.ml.level_touch import set_last_prediction
                        set_last_prediction({
                            "level": level.name,
                            "predicted": surfaced,
                            "raw_predicted": prediction,
                            "confidence": confidence,
                            "probabilities": pred_result.get("probabilities", {}),
                            "timestamp": time.time(),
                        })
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
        self._publish({
            "type": "level_rejected",
            "level": level.name,
            "level_price": level.price,
        })

    def _recompute_live_features(self, of_snapshot: dict, price: float) -> dict:
        """Refresh live-changing features (orderflow + temporal + candle) on cached base."""
        features = dict(self._last_ml_features)  # shallow copy
        long_of = of_snapshot.get("long", {})

        # Refresh orderflow fields from fresh snapshot
        of_keys = [
            "delta", "delta_aligned", "delta_divergence", "delta_unwind",
            "cvd", "cvd_trend", "vsa_absorption", "tick_vol_accelerating",
            "trapped_traders", "passive_active_ratio", "big_trades_count",
            "big_trades_net_delta", "stop_run_detected", "imbalance_ratio_max",
            "stacked_imbalance_count", "stacked_imbalance_direction",
        ]
        for k in of_keys:
            if k in long_of:
                features[k] = long_of[k]

        # Refresh temporal derivatives + candle patterns from fresh candles
        if self._candle_flow_fn:
            try:
                from src.ml.level_touch.compute import compute_temporal_derivatives, compute_candle_pattern_features
                candles = self._candle_flow_fn()
                if candles:
                    candle_dicts = [{
                        "delta": getattr(c, "delta", 0),
                        "volume": getattr(c, "volume", 0),
                        "tick_count": getattr(c, "tick_count", 0),
                        "spread": getattr(c, "spread", 0),
                        "body_ratio": getattr(c, "body_ratio", 0),
                        "stacked_imbalance_count": len(getattr(c, "stacked_imbalances", [])) if hasattr(c, "stacked_imbalances") else 0,
                        "open": getattr(c, "open", 0),
                        "close": getattr(c, "close", 0),
                    } for c in candles]
                    features.update(compute_temporal_derivatives(candle_dicts))
                    features.update(compute_candle_pattern_features(candle_dicts))
                    # Refresh last candle specifics
                    if candle_dicts:
                        features["last_candle_delta"] = candle_dicts[-1].get("delta")
                        features["last_candle_body_ratio"] = candle_dicts[-1].get("body_ratio")
            except Exception:
                pass

        return features

    def _emit_orderflow_update(self, price: float) -> None:
        snapshot = self._compute_orderflow_snapshot()
        if snapshot:
            self._publish({
                "type": "orderflow_update",
                "price": price,
                "ts": time.time(),
                "orderflow": snapshot,
            })

            # Emit refreshed ML feature snapshot for gauge dashboard
            if self._last_ml_features is not None:
                try:
                    updated = self._recompute_live_features(snapshot, price)
                    self._last_ml_features = updated
                    self._publish({
                        "type": "ml_features",
                        "level": self._active_level_name,
                        "features": updated,
                        "timestamp": time.time(),
                    })
                except Exception:
                    pass
