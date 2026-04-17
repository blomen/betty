"""Session replay engine — the core training data generator.

Replays a session of historical ticks and produces labelled Episodes for RL training.
Each episode corresponds to a level touch event detected during replay.

Usage::

    engine = ReplayEngine(macro_data={"2025-01-15": {...}})
    episodes = engine.replay_session(ticks, session_date, prior_session_levels={...})
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ...market_data.amt_dynamics import AMTDynamicsTracker
from ...market_data.levels import (
    SessionLevels,
    compute_session_levels,
    detect_fvgs,
    detect_order_blocks,
)
from ...market_data.orderflow import (
    CandleFlow,
    build_candle_flow,
    compute_signals,
)
from ...market_data.tpo import build_full_tpo_profile, compute_session_tpos
from ..config import AT_LEVEL_TICKS, ATR_PERIOD, TICK_SIZE, LevelType
from ..features.observation import build_observation
from ..zone_builder import Zone, build_zones
from .accumulators import IncrementalVolumeProfile, IncrementalVWAP
from .candle_aggregator import CandleAggregator
from .episode_builder import Episode, label_outcome_from_array

ET = ZoneInfo("US/Eastern")
log = logging.getLogger(__name__)

# Minimum candle flows required before computing orderflow signals
_MIN_CANDLE_FLOWS = 3

# Proximity threshold for level touch detection (price distance in points)
_TOUCH_PROXIMITY = AT_LEVEL_TICKS * TICK_SIZE


class ReplayEngine:
    """Replays a historical tick session and emits labelled training episodes.

    For each tick the engine:
    - Updates OHLCV candles (1m and 30m) incrementally
    - Maintains running VWAP bands and volume profile
    - Recomputes structural levels on each 1m bar close
    - Detects level touches (within AT_LEVEL_TICKS of any active level)
    - Labels each touch by scanning forward ticks for target/stop outcomes

    Args:
        macro_data: Optional dict mapping "YYYY-MM-DD" → macro feature dict.
            Used to inject macro context into observations.
    """

    def __init__(self, macro_data: dict | None = None) -> None:
        self._macro_data: dict = macro_data or {}
        self._amt_tracker = AMTDynamicsTracker()
        self._reset()

    # ------------------------------------------------------------------
    # Internal state initialisation
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        """Initialise / clear all per-session state."""
        # Candle aggregation
        self._candle_agg = CandleAggregator()

        # Running accumulators (reset each session)
        self._vwap = IncrementalVWAP()
        self._vp = IncrementalVolumeProfile(tick_size=TICK_SIZE)

        # Structural levels
        self._session_levels: SessionLevels = SessionLevels()
        self._fvgs: list = []
        self._order_blocks: list = []
        # Active levels: list of (name, LevelType, price)
        self._active_levels: list[tuple[str, LevelType, float]] = []

        # VWAP is RTH-only: reset at 09:30 ET
        self._rth_vwap_started: bool = False

        # Debounce set: tracks "name_price" keys of currently-touched levels
        # A key is removed when price moves away beyond the proximity threshold
        self._touched_keys: set[str] = set()

        # Cooldown: minimum 30 seconds between episodes to avoid noise
        self._last_episode_ts: datetime | None = None
        self._episode_cooldown = timedelta(seconds=30)

        # Zone state (clustered levels)
        self._active_zones: list[Zone] = []
        self._zone_keys: set[str] = set()

        # Orderflow: per-candle tick buffer and completed CandleFlow list
        self._candle_ticks: list[dict] = []
        self._candle_flows: list[CandleFlow] = []
        self._orderflow_signals: Any | None = None  # OrderflowSignals

        # Prior session data loaded via _load_prior_levels
        self._prior_pdh: float | None = None
        self._prior_pdl: float | None = None
        self._prior_weekly_high: float | None = None
        self._prior_weekly_low: float | None = None
        self._prior_monthly_high: float | None = None
        self._prior_monthly_low: float | None = None

        # TPO profile dict (updated in _build_state, used by _rebuild_active_levels)
        self._tpo_profile: dict | None = None

        # Zone touch memory: tracks how many times each zone was touched in this session
        self._zone_touch_mem: dict[float, dict] = {}  # zone_key → {count, last_ts}

        # Precomputed cross-session levels (injected before replay)
        self._precomputed: dict | None = None

        # Running session high/low for naked POC invalidation
        self._session_high: float | None = None
        self._session_low: float | None = None

        # Prior session state for AMT static features (indices 13-19)
        self._prior_poor_high: bool = False
        self._prior_poor_low: bool = False
        self._prior_excess_quality: int = 0
        self._prior_poc: float | None = None

        # Computed AMT enrichment — updated on bar close once data is available.
        # Start at 0.5 (neutral median) so early-session observations carry the
        # least-biased prior rather than a hard zero.
        self._ib_range_percentile: float = 0.5
        self._composite_va_overlap: float = 0.5

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_prior_session_for_chaining(self) -> dict:
        """Extract computed levels to pass as prior_session_levels to next session.

        Call after replay_session() completes. Returns the dict format expected
        by _load_prior_levels(): pdh, pdl, weekly_high/low, monthly_high/low.

        PDH/PDL = this session's RTH high/low (computed from all RTH bars).
        """
        sl = self._session_levels
        bars_1m = self._candle_agg.get_completed_1m()

        # Compute RTH high/low from bars (09:30-16:00 ET)
        rth_high = None
        rth_low = None
        for bar in bars_1m:
            bar_ts = bar["ts"]
            if isinstance(bar_ts, str):
                bar_ts = datetime.fromisoformat(bar_ts)
            if bar_ts.tzinfo is None:
                bar_ts = bar_ts.replace(tzinfo=timezone.utc)
            bar_et = bar_ts.astimezone(ET)
            bar_time = bar_et.time()
            if time(9, 30) <= bar_time < time(16, 0):
                h, l = bar["high"], bar["low"]
                rth_high = max(rth_high or h, h)
                rth_low = min(rth_low or l, l)

        # Save prior session state for next session's AMT features
        tpo = self._tpo_profile or {}
        self._prior_poor_high = bool(tpo.get("poor_high", False))
        self._prior_poor_low = bool(tpo.get("poor_low", False))
        self._prior_excess_quality = (tpo.get("upper_excess_ticks", 0) or 0) - (tpo.get("lower_excess_ticks", 0) or 0)
        vp = self._vp.get()
        self._prior_poc = vp.poc if vp else None

        return {
            "pdh": rth_high,
            "pdl": rth_low,
            "weekly_high": max(filter(None, [sl.weekly_high, rth_high])) if any([sl.weekly_high, rth_high]) else None,
            "weekly_low": min(filter(None, [sl.weekly_low, rth_low])) if any([sl.weekly_low, rth_low]) else None,
            "monthly_high": max(filter(None, [sl.monthly_high, rth_high]))
            if any([sl.monthly_high, rth_high])
            else None,
            "monthly_low": min(filter(None, [sl.monthly_low, rth_low])) if any([sl.monthly_low, rth_low]) else None,
        }

    def get_level_snapshot(self) -> dict:
        """Return current computed levels for visual verification.

        Call after replay_session() to inspect what the engine computed.
        Returns all session levels, VWAP, VP, structural levels, and
        the active level list used for touch detection.
        """
        vwap_bands = self._vwap.get()
        vp = self._vp.get()
        sl = self._session_levels

        return {
            "session_levels": {
                "pdh": sl.pdh,
                "pdl": sl.pdl,
                "tokyo_high": sl.tokyo_high,
                "tokyo_low": sl.tokyo_low,
                "london_high": sl.london_high,
                "london_low": sl.london_low,
                "ib_high": sl.ib_high,
                "ib_low": sl.ib_low,
                "weekly_high": sl.weekly_high,
                "weekly_low": sl.weekly_low,
                "monthly_high": sl.monthly_high,
                "monthly_low": sl.monthly_low,
            },
            "vwap": {
                "vwap": vwap_bands.vwap if vwap_bands else None,
                "sd1_upper": vwap_bands.sd1_upper if vwap_bands else None,
                "sd1_lower": vwap_bands.sd1_lower if vwap_bands else None,
                "sd2_upper": vwap_bands.sd2_upper if vwap_bands else None,
                "sd2_lower": vwap_bands.sd2_lower if vwap_bands else None,
                "sd3_upper": vwap_bands.sd3_upper if vwap_bands else None,
                "sd3_lower": vwap_bands.sd3_lower if vwap_bands else None,
            },
            "volume_profile": {
                "poc": vp.poc if vp else None,
                "vah": vp.vah if vp else None,
                "val": vp.val if vp else None,
            },
            "fvgs": [{"low": f.price_low, "high": f.price_high, "direction": f.direction} for f in self._fvgs],
            "order_blocks": [
                {"low": ob.price_low, "high": ob.price_high, "direction": ob.direction} for ob in self._order_blocks
            ],
            "active_levels": [
                {"name": name, "type": lt.value, "price": price} for name, lt, price in self._active_levels
            ],
        }

    def replay_session(
        self,
        ticks: list[Any],
        session_date: datetime,
        prior_session_levels: dict | None = None,
        precomputed_levels: dict | None = None,
    ) -> list[Episode]:
        """Replay a full session of ticks and return all labelled episodes.

        Args:
            ticks: Ordered list of tick dicts (or objects) with attributes/keys:
                   ts (datetime), price (float), size (int), side ("A"|"B").
                   Dicts and attribute-style objects are both supported.
            session_date: The calendar date (UTC-aware datetime or date) of the session.
                         Used to compute session boundaries (RTH, IB, Tokyo, London).
            prior_session_levels: Optional dict with keys pdh, pdl, weekly_high,
                                  weekly_low, monthly_high, monthly_low from the
                                  prior session. Injected into SessionLevels before
                                  any bars are processed.

        Returns:
            List of Episode objects, one per unique level touch.
        """
        self._reset()

        if prior_session_levels:
            self._load_prior_levels(prior_session_levels)

        if precomputed_levels:
            self._precomputed = precomputed_levels

        # Normalise ticks for uniform dict-like access.
        # TickArray already provides dict-like access via TickView — skip copy.
        from .tick_array import TickArray

        if isinstance(ticks, TickArray):
            norm_ticks = ticks
        else:
            norm_ticks: list[dict] = [_normalise_tick(t) for t in ticks]

        episodes: list[Episode] = []
        self._amt_tracker = AMTDynamicsTracker()
        date_str = _date_key(session_date)

        for i, tick in enumerate(norm_ticks):
            price: float = tick["price"]
            self._session_high = max(self._session_high or price, price)
            self._session_low = min(self._session_low or price, price)

            # 1. Update candle aggregator → detect 1m bar closes
            completed_bars = self._candle_agg.update(tick)

            # 2. Update running accumulators (RTH VWAP: reset at 09:30 ET)
            tick_et = tick["ts"].astimezone(ET) if tick["ts"].tzinfo else tick["ts"]
            is_rth = time(9, 30) <= tick_et.time() < time(16, 0)

            if is_rth and not self._rth_vwap_started:
                # First RTH tick: reset VWAP to start fresh from session open
                self._vwap.reset()
                self._rth_vwap_started = True

            if is_rth:
                self._vwap.update(price, tick["size"])

            # VP uses all ticks (full session profile)
            self._vp.update(price, tick["size"])

            # Update AMT dynamics tracker (map A/B → sell/buy)
            _raw_side = tick.get("side", "B")
            _tick_side = "buy" if _raw_side == "B" else "sell"
            self._amt_tracker.update(price, tick["size"], _tick_side)

            # 3. On bar close: build CandleFlow from buffered ticks, then recompute
            for _bar in completed_bars:
                self._on_bar_close(session_date)

            # 4. Buffer tick for NEXT candle's orderflow CandleFlow construction
            self._candle_ticks.append(tick)

            # 5. Check which zones price has entered
            newly_entered = self._check_zone_entry(price)

            # 6. Emit ONE episode per tick with 30s cooldown between episodes.
            # Multiple simultaneous zone entries show up as confluence in the
            # observation vector; we only need one training sample per moment.
            if not newly_entered:
                continue
            # Cooldown: skip if too soon after last episode
            if self._last_episode_ts is not None:
                elapsed = (tick["ts"] - self._last_episode_ts).total_seconds()
                if elapsed < self._episode_cooldown.total_seconds():
                    continue

            zone = newly_entered[0]
            if True:  # single-episode block
                # Determine approach direction: compare to price 200 ticks ago
                # (~2 seconds of NQ data — captures the micro move into the level)
                lookback_idx = max(0, i - 200)
                prior_price = norm_ticks[lookback_idx]["price"]
                approach_direction = "up" if price > prior_price else "down"

                # Forward ticks: scan up to 30 min of data (TIMEOUT_MINUTES)
                # NQ averages ~6k ticks/min, 30 min ≈ 180k ticks max.
                fwd_start = i + 1
                fwd_end = min(len(norm_ticks), fwd_start + 180_000)

                # Last 50 ticks for raw tick sequence (temporal stream)
                micro_start = max(0, i - 50)
                recent_ticks = norm_ticks[micro_start : i + 1]

                state = self._build_state(tick, zone, session_date, date_str)
                state["recent_ticks"] = recent_ticks
                state["approach_direction"] = approach_direction
                observation = build_observation(state)

                # Gather zone centers above and below for multi-level trailing reward
                zone_centers_above = sorted(
                    [z.center_price for z in self._active_zones if z.center_price > price + TICK_SIZE]
                )
                zone_centers_below = sorted(
                    [z.center_price for z in self._active_zones if z.center_price < price - TICK_SIZE], reverse=True
                )

                episode = label_outcome_from_array(
                    touch_price=zone.center_price,
                    ticks=norm_ticks,
                    start=fwd_start,
                    end=fwd_end,
                    observation=observation,
                    level_type=f"zone_{zone.member_count}m",
                    touch_ts=tick["ts"],
                    approach_direction=approach_direction,
                    levels_above=zone_centers_above,
                    levels_below=zone_centers_below,
                )
                episode.state = state
                episodes.append(episode)
                self._last_episode_ts = tick["ts"]
                log.debug(
                    "Episode at %s: zone(%d members) @ %.2f → best=%s",
                    tick["ts"],
                    zone.member_count,
                    zone.center_price,
                    episode.best_action,
                )

        # Flush any remaining partial candle at session end
        self._candle_agg.flush()

        return episodes

    # ------------------------------------------------------------------
    # Bar-close handler
    # ------------------------------------------------------------------

    def _on_bar_close(self, session_date: datetime) -> None:
        """Called once per completed 1m bar. Recomputes structural levels."""
        bars_1m = self._candle_agg.get_completed_1m()
        bar_count = len(bars_1m)

        # Recompute session levels every 15 bars (levels don't change every minute)
        # Always compute on first 60 bars (IB period is critical)
        if bar_count <= 60 or bar_count % 15 == 0:
            computed = compute_session_levels(bars_1m, session_date)

            # Merge prior session data (PDH/PDL, weekly, monthly)
            if self._prior_pdh is not None and computed.pdh is None:
                computed.pdh = self._prior_pdh
            if self._prior_pdl is not None and computed.pdl is None:
                computed.pdl = self._prior_pdl
            if self._prior_weekly_high is not None and computed.weekly_high is None:
                computed.weekly_high = self._prior_weekly_high
            if self._prior_weekly_low is not None and computed.weekly_low is None:
                computed.weekly_low = self._prior_weekly_low
            if self._prior_monthly_high is not None and computed.monthly_high is None:
                computed.monthly_high = self._prior_monthly_high
            if self._prior_monthly_low is not None and computed.monthly_low is None:
                computed.monthly_low = self._prior_monthly_low

            self._session_levels = computed

            # Initialize AMT tracker when IB levels first appear
            if not self._amt_tracker._initialized and computed.ib_high and computed.ib_low:
                vp = self._vp.get()
                tpo_data = self._tpo_profile or {}
                self._amt_tracker.initialize(
                    {
                        "ib_high": computed.ib_high,
                        "ib_low": computed.ib_low,
                        "vah": vp.vah if vp else 0,
                        "val": vp.val if vp else 0,
                        "poc": vp.poc if vp else 0,
                        "single_prints": tpo_data.get("single_prints", []),
                    }
                )

            # Update AMT tracker on 30-min period close (after initialization)
            if self._amt_tracker._initialized and bar_count % 30 == 0:
                vp = self._vp.get()
                if vp and len(bars_1m) >= 30:
                    last_30 = bars_1m[-30:]
                    self._amt_tracker.on_period_close(
                        period_high=max(b["high"] for b in last_30),
                        period_low=min(b["low"] for b in last_30),
                        developing_poc=vp.poc,
                        developing_vah=vp.vah,
                        developing_val=vp.val,
                    )

        # Detect SMC structures every 15 bars (last 50 bars lookback)
        if bar_count % 15 == 0:
            recent_bars = bars_1m[-50:]
            self._fvgs = detect_fvgs(recent_bars)
            self._order_blocks = detect_order_blocks(recent_bars)

        # Build CandleFlow from ticks accumulated since last bar close
        if self._candle_ticks:
            new_flows = build_candle_flow(self._candle_ticks, period_seconds=60)
            self._candle_flows.extend(new_flows)
            self._candle_ticks = []  # Reset buffer for next candle period

        # Recompute orderflow signals if we have enough candle flows
        if len(self._candle_flows) >= _MIN_CANDLE_FLOWS:
            self._orderflow_signals = compute_signals(self._candle_flows, direction="long", lookback=10)

        # Update ib_range_percentile once the IB has formed (bar 60 = 10:30 ET)
        # and composite_va_overlap on every VP update (both need precomputed data).
        if self._precomputed and bar_count >= 60:
            sl = self._session_levels
            if sl.ib_high is not None and sl.ib_low is not None:
                today_ib = sl.ib_high - sl.ib_low
                ref = self._precomputed.get("prior_ib_ranges_sorted", [])
                if ref and today_ib > 0:
                    # Fraction of prior sessions with a smaller IB than today
                    n_below = sum(1 for r in ref if r < today_ib)
                    self._ib_range_percentile = n_below / len(ref)

            vp = self._vp.get()
            prior_vas: list[tuple[float, float]] = self._precomputed.get("prior_vas", [])
            if vp and prior_vas:
                curr_vah, curr_val = vp.vah, vp.val
                curr_width = max(curr_vah - curr_val, 1e-9)
                overlaps = []
                for p_vah, p_val in prior_vas:
                    p_width = max(p_vah - p_val, 1e-9)
                    overlap = max(0.0, min(curr_vah, p_vah) - max(curr_val, p_val))
                    overlaps.append(overlap / max(curr_width, p_width))
                self._composite_va_overlap = sum(overlaps) / len(overlaps)

        # Invalidate naked POCs that the current session has swept through
        if self._precomputed and self._precomputed.get("naked_pocs") and self._session_high is not None:
            self._precomputed["naked_pocs"] = [
                n
                for n in self._precomputed["naked_pocs"]
                if not (self._session_low <= n["price"] <= self._session_high)
            ]

        # Rebuild the active levels list
        self._rebuild_active_levels()

    # ------------------------------------------------------------------
    # Level building
    # ------------------------------------------------------------------

    def _rebuild_active_levels(self) -> None:
        """Collect all structural levels into self._active_levels."""
        levels: list[tuple[str, LevelType, float]] = []

        # --- VWAP bands ---
        vwap_bands = self._vwap.get()
        if vwap_bands is not None:
            levels.append(("vwap", LevelType.VWAP, vwap_bands.vwap))
            levels.append(("vwap_sd1_upper", LevelType.VWAP_SD1, vwap_bands.sd1_upper))
            levels.append(("vwap_sd1_lower", LevelType.VWAP_SD1, vwap_bands.sd1_lower))
            levels.append(("vwap_sd2_upper", LevelType.VWAP_SD2, vwap_bands.sd2_upper))
            levels.append(("vwap_sd2_lower", LevelType.VWAP_SD2, vwap_bands.sd2_lower))
            levels.append(("vwap_sd3_upper", LevelType.VWAP_SD3, vwap_bands.sd3_upper))
            levels.append(("vwap_sd3_lower", LevelType.VWAP_SD3, vwap_bands.sd3_lower))

        # --- Volume profile: daily POC, VAH, VAL ---
        vp = self._vp.get()
        if vp is not None:
            levels.append(("daily_poc", LevelType.DAILY_POC, vp.poc))
            levels.append(("daily_vah", LevelType.DAILY_VAH, vp.vah))
            levels.append(("daily_val", LevelType.DAILY_VAL, vp.val))

        # --- Session structural levels ---
        sl = self._session_levels
        _add_optional(levels, "pdh", LevelType.PDH, sl.pdh)
        _add_optional(levels, "pdl", LevelType.PDL, sl.pdl)
        _add_optional(levels, "tokyo_high", LevelType.TOKYO_HIGH, sl.tokyo_high)
        _add_optional(levels, "tokyo_low", LevelType.TOKYO_LOW, sl.tokyo_low)
        # NYIB = NY session IB. Use ib_high/ib_low from SessionLevels (set after IB forms).
        _add_optional(levels, "nyib_high", LevelType.NYIB_HIGH, sl.ib_high)
        _add_optional(levels, "nyib_low", LevelType.NYIB_LOW, sl.ib_low)

        # --- TPO levels ---
        tpo = self._tpo_profile if hasattr(self, "_tpo_profile") else None
        if tpo:
            _add_optional(levels, "tpoc", LevelType.TPOC, tpo.get("poc"))
            _add_optional(levels, "tvah", LevelType.TVAH, tpo.get("vah"))
            _add_optional(levels, "tval", LevelType.TVAL, tpo.get("val"))
        _add_optional(levels, "tibh", LevelType.TIBH, getattr(sl, "ib_high", None))
        _add_optional(levels, "tibl", LevelType.TIBL, getattr(sl, "ib_low", None))

        # FVGs and single prints are NOT active levels — they are confluence
        # signals injected into the observation vector via encode_confluence().

        # --- Precomputed cross-session levels ---
        if self._precomputed:
            for naked in self._precomputed.get("naked_pocs", []):
                levels.append(("naked_poc", LevelType.NAKED_POC, naked["price"]))

            # Weekly/monthly volume profiles
            _add_optional(levels, "weekly_poc", LevelType.WEEKLY_POC, self._precomputed.get("weekly_poc"))
            _add_optional(levels, "weekly_vah", LevelType.WEEKLY_VAH, self._precomputed.get("weekly_vah"))
            _add_optional(levels, "weekly_val", LevelType.WEEKLY_VAL, self._precomputed.get("weekly_val"))
            _add_optional(levels, "monthly_poc", LevelType.MONTHLY_POC, self._precomputed.get("monthly_poc"))
            _add_optional(levels, "monthly_vah", LevelType.MONTHLY_VAH, self._precomputed.get("monthly_vah"))
            _add_optional(levels, "monthly_val", LevelType.MONTHLY_VAL, self._precomputed.get("monthly_val"))

            # Swing levels (most recent pivot per timeframe)
            swing = self._precomputed.get("swing_structure")
            if swing is not None:
                for tf_swings in [swing.daily, swing.weekly, swing.monthly]:
                    if tf_swings.swing_highs:
                        sh = tf_swings.swing_highs[0]
                        lt = getattr(LevelType, f"{tf_swings.timeframe.upper()}_SWING_HIGH", None)
                        if lt:
                            levels.append((f"{tf_swings.timeframe}_swing_high", lt, sh.price))
                    if tf_swings.swing_lows:
                        sl = tf_swings.swing_lows[0]
                        lt = getattr(LevelType, f"{tf_swings.timeframe.upper()}_SWING_LOW", None)
                        if lt:
                            levels.append((f"{tf_swings.timeframe}_swing_low", lt, sl.price))

        self._active_levels = levels

        # Build zones from active levels
        session_atr = self._compute_session_atr()
        self._active_zones = build_zones(self._active_levels, session_atr)

    def _compute_session_atr(self) -> float:
        """Compute ATR from 30m bars for zone radius. Fallback to session range."""
        bars_30m = self._candle_agg.get_completed_30m()
        if len(bars_30m) < 2:
            if self._session_high is not None and self._session_low is not None:
                return max(1.0, self._session_high - self._session_low)
            return 40.0  # reasonable NQ default
        trs = []
        for i in range(1, min(len(bars_30m), ATR_PERIOD + 1)):
            bar = bars_30m[-i]
            prev = bars_30m[-i - 1] if i < len(bars_30m) else bar
            tr = max(
                bar["high"] - bar["low"],
                abs(bar["high"] - prev["close"]),
                abs(bar["low"] - prev["close"]),
            )
            trs.append(tr)
        return sum(trs) / len(trs) if trs else 40.0

    # ------------------------------------------------------------------
    # Level touch detection with debouncing
    # ------------------------------------------------------------------

    def _check_level_touch(self, price: float) -> list[tuple[str, LevelType, float]]:
        """Detect newly-touched levels, with debouncing.

        A level is "touched" when price comes within _TOUCH_PROXIMITY.
        A touched level is debounced (won't re-fire) until price moves
        beyond the proximity threshold again.

        Returns:
            List of (name, LevelType, level_price) for newly triggered touches.
        """
        newly_touched: list[tuple[str, LevelType, float]] = []
        still_close: set[str] = set()

        for name, level_type, level_price in self._active_levels:
            dist = abs(price - level_price)
            # Snap level_price to tick grid for debounce key — prevents
            # VWAP/VP micro-shifts from creating new keys each tick
            snapped = round(level_price / TICK_SIZE) * TICK_SIZE
            key = f"{name}_{snapped}"

            if dist <= _TOUCH_PROXIMITY:
                still_close.add(key)
                if key not in self._touched_keys:
                    # First time entering proximity — fire
                    self._touched_keys.add(key)
                    newly_touched.append((name, level_type, level_price))
            # If key was in touched_keys but is no longer close, it will
            # be absent from still_close. We prune stale keys below.

        # Remove debounce keys where price has moved away
        departed = self._touched_keys - still_close
        self._touched_keys -= departed

        return newly_touched

    # ------------------------------------------------------------------
    # Zone entry detection with debouncing
    # ------------------------------------------------------------------

    def _check_zone_entry(self, price: float) -> list[Zone]:
        """Detect newly-entered zones with debouncing."""
        newly_entered: list[Zone] = []
        still_inside: set[str] = set()

        for zone in self._active_zones:
            inside = zone.lower_bound <= price <= zone.upper_bound
            snapped = round(zone.center_price / TICK_SIZE) * TICK_SIZE
            key = f"zone_{snapped}"

            if inside:
                still_inside.add(key)
                if key not in self._zone_keys:
                    self._zone_keys.add(key)
                    newly_entered.append(zone)

        # Remove keys for zones price has exited
        self._zone_keys -= self._zone_keys - still_inside
        return newly_entered

    # ------------------------------------------------------------------
    # State dict construction
    # ------------------------------------------------------------------

    def _build_state(
        self,
        tick: dict,
        zone: Zone,
        session_date: datetime,
        date_str: str,
    ) -> dict:
        """Build the full state dict consumed by build_observation()."""
        price: float = tick["price"]
        ts: datetime = tick["ts"]

        bars_1m = self._candle_agg.get_completed_1m()
        bars_30m = self._candle_agg.get_completed_30m()

        # Candle flows for observation (last 20 for feature window)
        recent_flows = self._candle_flows[-20:] if self._candle_flows else []

        # VWAP and volume profile
        vwap_bands = self._vwap.get()
        vp = self._vp.get()

        # TPO profile from 30m bars
        tpo_profile_dict: dict | None = None
        tpo_profile_obj = None  # TPOProfile object for setup detection
        session_tpos = None  # Per-session TPO for RL features
        if bars_30m:
            profile = build_full_tpo_profile(bars_30m, tick_size=TICK_SIZE)
            tpo_profile_obj = profile  # Keep object for setup detection
            # Compute rotation count: total direction changes in 30m bars
            rotation_count = 0
            if len(bars_30m) >= 2:
                for j in range(1, len(bars_30m)):
                    prev_dir = bars_30m[j - 1]["close"] - bars_30m[j - 1]["open"]
                    curr_dir = bars_30m[j]["close"] - bars_30m[j]["open"]
                    if (prev_dir > 0 and curr_dir < 0) or (prev_dir < 0 and curr_dir > 0):
                        rotation_count += 1

            tpo_profile_dict = {
                "poc": profile.poc,
                "vah": profile.vah,
                "val": profile.val,
                "shape": profile.profile_shape,
                "rotation_factor": profile.rotation_factor,
                "rotation_count": rotation_count,
                "excess_high": profile.upper_excess > 0,
                "excess_low": profile.lower_excess > 0,
                "upper_excess_ticks": profile.upper_excess,
                "lower_excess_ticks": profile.lower_excess,
                "poor_high": profile.poor_high,
                "poor_low": profile.poor_low,
                "single_prints": profile.single_prints,
                "ledges": profile.ledges,
                "ib_tpo_count": profile.ib_tpo_count,
                "opening_type": profile.opening_type,
                "opening_direction": profile.opening_direction,
                "ib_high": profile.ib_high,
                "ib_low": profile.ib_low,
            }
            # Store on self so _rebuild_active_levels can inject TPOC/TVAH/TVAL
            self._tpo_profile = tpo_profile_dict

            # --- Per-session TPO (for RL observation) ---
            # bars_30m from CandleAggregator have "ts" field (datetime)
            session_tpos = compute_session_tpos(bars_30m, tick_size=TICK_SIZE)

        # Session context
        session_context = self._build_session_context(ts, bars_1m, session_date)

        # Orderflow signals as dict (or None)
        of_signals = self._orderflow_signals

        # All active level prices for confluence encoding
        all_level_prices = [lp for _, _, lp in self._active_levels]

        # Macro context for this date
        macro = self._macro_data.get(date_str)

        # Market type for setup detectors
        ctx = session_context or {}
        drp = float(ctx.get("daily_range_pct", 0.01))
        if drp > 0.02:
            day_type = "trend"
        elif drp < 0.008:
            day_type = "range"
        else:
            day_type = "normal"

        # Build 5m candle flows by grouping 1m flows in chunks of 5
        candle_flows_5m: list = []
        if len(self._candle_flows) >= 5:
            for chunk_start in range(0, len(self._candle_flows) - 4, 5):
                chunk = self._candle_flows[chunk_start : chunk_start + 5]
                agg_high = max(c.high for c in chunk)
                agg_low = min(c.low for c in chunk)
                agg = CandleFlow(
                    ts=chunk[-1].ts,
                    open=chunk[0].open,
                    high=agg_high,
                    low=agg_low,
                    close=chunk[-1].close,
                    volume=sum(c.volume for c in chunk),
                    buy_volume=sum(c.buy_volume for c in chunk),
                    sell_volume=sum(c.sell_volume for c in chunk),
                    delta=sum(c.delta for c in chunk),
                    tick_count=sum(c.tick_count for c in chunk),
                    spread=agg_high - agg_low,
                )
                candle_flows_5m.append(agg)

        return {
            "zone": zone,
            "all_zones": self._active_zones,
            "price": price,
            "touch_epoch": ts.timestamp(),
            "candles": recent_flows,
            "candles_5m": candle_flows_5m[-10:] if candle_flows_5m else [],
            "vwap_bands": vwap_bands,
            "volume_profile": vp,
            "tpo_profile": tpo_profile_dict,
            "tpo_profile_obj": tpo_profile_obj,
            "session_tpos": session_tpos,
            "session_levels": self._session_levels,
            "all_levels": all_level_prices,
            "orderflow_signals": of_signals,
            "macro": macro,
            "session_context": session_context,
            "day_type": day_type,
            "fvgs": self._fvgs,
            "single_print_zones": (self._precomputed.get("single_print_zones", []) if self._precomputed else []),
            "swing_structure": (self._precomputed.get("swing_structure") if self._precomputed else None),
            "amt_dynamics": self._amt_tracker.snapshot(),
            "zone_memory": self._get_zone_memory_for_state(zone, ts),
        }

    def _get_zone_memory_for_state(self, zone: Zone, ts: datetime) -> dict:
        """Build zone_memory dict for the observation.

        Records this touch and returns memory for all zones.
        """
        zone_key = round(zone.center_price * 4) / 4
        ts_epoch = ts.timestamp()

        # Record this touch
        entry = self._zone_touch_mem.get(zone_key, {"count": 0, "last_ts": 0.0})
        entry["count"] += 1
        entry["last_ts"] = ts_epoch
        self._zone_touch_mem[zone_key] = entry

        # Build memory dict for all zones
        result = {}
        for key, mem in self._zone_touch_mem.items():
            result[key] = {
                "touch_count": mem["count"],
                "last_result": 0.0,  # not tracked in replay (would need forward scan)
                "time_since_last": ts_epoch - mem["last_ts"] if mem["last_ts"] > 0 else 3600,
            }
        return result

    def _build_session_context(
        self,
        ts: datetime,
        bars_1m: list[dict],
        session_date: datetime,
    ) -> dict:
        """Compute session_context dict for structure_features.py.

        Keys must match what structure_features.py reads:
          minutes_since_rth, minute_of_day, session_volume_pct,
          daily_range_pct, session_type, ib_broken (str: "up"/"down"/"none")
        """
        ts_et = ts.astimezone(ET) if ts.tzinfo else ts.replace(tzinfo=timezone.utc).astimezone(ET)
        rth_open = ts_et.replace(hour=9, minute=30, second=0, microsecond=0)
        minutes_since_open = max(0.0, (ts_et - rth_open).total_seconds() / 60.0)

        # Minute of day for time-of-day sin/cos encoding
        minute_of_day = ts_et.hour * 60 + ts_et.minute

        # Session type based on time of day (ET)
        t = ts_et.time()
        if time(9, 30) <= t < time(16, 0):
            session_type = "rth"
        elif time(3, 0) <= t < time(9, 30):
            session_type = "london"
        else:
            session_type = "globex"

        # Session progress: fraction of RTH elapsed (390 minutes total)
        # We can't know future volume, so bar count is the best proxy
        rth_bar_count = sum(1 for b in bars_1m if _is_rth_bar(b))
        session_volume_pct = min(1.0, rth_bar_count / 390.0)

        # Daily range percentage
        if bars_1m:
            session_high = max(b["high"] for b in bars_1m)
            session_low = min(b["low"] for b in bars_1m)
            daily_range_pct = (session_high - session_low) / max(1e-6, session_low)
        else:
            daily_range_pct = 0.0

        # IB breakout status as string ("up", "down", "none")
        sl = self._session_levels
        ib_high = sl.ib_high
        ib_low = sl.ib_low
        ib_broken_str = "none"
        if ib_high is not None and ib_low is not None and bars_1m:
            price_now = bars_1m[-1]["close"]
            if price_now > ib_high:
                ib_broken_str = "up"
            elif price_now < ib_low:
                ib_broken_str = "down"

        # Open price: first RTH bar's open (or first bar if pre-RTH)
        open_price = None
        for b in bars_1m:
            if _is_rth_bar(b):
                open_price = b["open"]
                break
        if open_price is None and bars_1m:
            open_price = bars_1m[0]["open"]

        return {
            "minutes_since_rth": minutes_since_open,
            "minute_of_day": minute_of_day,
            "session_volume_pct": session_volume_pct,
            "daily_range_pct": daily_range_pct,
            "session_type": session_type,
            "ib_broken": ib_broken_str,
            # AMT features need these:
            "open_price": open_price,
            "daily_high": session_high if bars_1m else None,
            "daily_low": session_low if bars_1m else None,
            "ib_high": ib_high,
            "ib_low": ib_low,
            # AMT static enrichment (for amt_features indices 13-19)
            "ib_range_percentile": self._ib_range_percentile,  # updated in _on_bar_close once IB forms
            "overnight_gap": self._compute_overnight_gap(bars_1m),
            "open_vs_prior_poc": self._compute_open_vs_prior_poc(open_price),
            "composite_va_overlap": self._composite_va_overlap,  # updated in _on_bar_close each VP update
            "prior_poor_high": self._prior_poor_high,
            "prior_poor_low": self._prior_poor_low,
            "prior_excess_quality": self._prior_excess_quality,
        }

    # ------------------------------------------------------------------
    # AMT helpers
    # ------------------------------------------------------------------

    def _compute_overnight_gap(self, bars_1m: list[dict]) -> float:
        """Overnight gap = (RTH open - prior close) / IB range."""
        sl = self._session_levels
        if not sl or not sl.ib_high or not sl.ib_low:
            return 0.0
        ib_range = sl.ib_high - sl.ib_low
        if ib_range <= 0:
            return 0.0
        open_price = None
        for b in bars_1m:
            if _is_rth_bar(b):
                open_price = b["open"]
                break
        if open_price is None:
            return 0.0
        prior_close = bars_1m[0]["close"] if bars_1m else open_price
        return max(-1.0, min(1.0, (open_price - prior_close) / ib_range))

    def _compute_open_vs_prior_poc(self, open_price: float | None) -> float:
        """Open price distance from prior session POC, normalized."""
        if open_price is None or self._prior_poc is None:
            return 0.0
        return max(-1.0, min(1.0, (open_price - self._prior_poc) / 0.25 / 200.0))

    # ------------------------------------------------------------------
    # Prior session data
    # ------------------------------------------------------------------

    def _load_prior_levels(self, prior: dict) -> None:
        """Inject prior-session levels that bars alone cannot reconstruct.

        Args:
            prior: Dict with optional keys: pdh, pdl, weekly_high, weekly_low,
                   monthly_high, monthly_low.
        """
        self._prior_pdh = prior.get("pdh")
        self._prior_pdl = prior.get("pdl")
        self._prior_weekly_high = prior.get("weekly_high")
        self._prior_weekly_low = prior.get("weekly_low")
        self._prior_monthly_high = prior.get("monthly_high")
        self._prior_monthly_low = prior.get("monthly_low")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _normalise_tick(tick: Any) -> dict:
    """Convert a tick to a plain dict with consistent keys.

    Supports both dict ticks and attribute-style objects (e.g. databento rows).
    """
    if isinstance(tick, dict):
        return tick
    return {
        "ts": tick.ts,
        "price": float(tick.price),
        "size": int(tick.size),
        "side": tick.side,
    }


def _is_rth_bar(bar: dict) -> bool:
    """Check if a bar falls within RTH (09:30-16:00 ET)."""
    bar_ts = bar.get("ts")
    if bar_ts is None:
        return False
    if isinstance(bar_ts, str):
        bar_ts = datetime.fromisoformat(bar_ts)
    if bar_ts.tzinfo is None:
        bar_ts = bar_ts.replace(tzinfo=timezone.utc)
    bar_et = bar_ts.astimezone(ET)
    return time(9, 30) <= bar_et.time() < time(16, 0)


def _add_optional(
    levels: list,
    name: str,
    level_type: LevelType,
    price: float | None,
) -> None:
    """Append a level only if its price is not None and non-zero."""
    if price is not None and price != 0.0:
        levels.append((name, level_type, price))


def _date_key(session_date: datetime | Any) -> str:
    """Return "YYYY-MM-DD" string for macro_data lookup."""
    if isinstance(session_date, datetime):
        if session_date.tzinfo:
            d = session_date.astimezone(ET).date()
        else:
            d = session_date.date()
    else:
        d = session_date  # assume date object
    return d.isoformat()
