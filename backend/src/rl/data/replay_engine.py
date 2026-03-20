"""Session replay engine — the core training data generator.

Replays a session of historical ticks and produces labelled Episodes for RL training.
Each episode corresponds to a level touch event detected during replay.

Usage::

    engine = ReplayEngine(macro_data={"2025-01-15": {...}})
    episodes = engine.replay_session(ticks, session_date, prior_session_levels={...})
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone, time
from typing import Any
from zoneinfo import ZoneInfo

from .candle_aggregator import CandleAggregator
from .accumulators import IncrementalVWAP, IncrementalVolumeProfile
from .episode_builder import Episode, label_outcome
from ..features.observation import build_observation
from ..config import LevelType, AT_LEVEL_TICKS, TICK_SIZE
from ...market_data.levels import (
    compute_session_levels,
    detect_fvgs,
    detect_order_blocks,
    SessionLevels,
)
from ...market_data.tpo import build_full_tpo_profile
from ...market_data.orderflow import (
    build_candle_flow,
    compute_signals,
    CandleFlow,
)

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

        # Precomputed cross-session levels (injected before replay)
        self._precomputed: dict | None = None

        # Running session high/low for naked POC invalidation
        self._session_high: float | None = None
        self._session_low: float | None = None

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

        return {
            "pdh": rth_high,
            "pdl": rth_low,
            "weekly_high": max(filter(None, [sl.weekly_high, rth_high])) if any([sl.weekly_high, rth_high]) else None,
            "weekly_low": min(filter(None, [sl.weekly_low, rth_low])) if any([sl.weekly_low, rth_low]) else None,
            "monthly_high": max(filter(None, [sl.monthly_high, rth_high])) if any([sl.monthly_high, rth_high]) else None,
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
            "fvgs": [
                {"low": f.price_low, "high": f.price_high, "direction": f.direction}
                for f in self._fvgs
            ],
            "order_blocks": [
                {"low": ob.price_low, "high": ob.price_high, "direction": ob.direction}
                for ob in self._order_blocks
            ],
            "active_levels": [
                {"name": name, "type": lt.value, "price": price}
                for name, lt, price in self._active_levels
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

        # Normalise ticks to dicts once for uniform access
        norm_ticks: list[dict] = [_normalise_tick(t) for t in ticks]

        episodes: list[Episode] = []
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

            # 3. Buffer tick for orderflow CandleFlow construction
            self._candle_ticks.append(tick)

            # 4. On bar close: recompute structure and orderflow
            for _bar in completed_bars:
                self._on_bar_close(session_date)

            # 5. Check which active levels are touched
            newly_touched = self._check_level_touch(price)

            # 6. Emit ONE episode per tick with 30s cooldown between episodes.
            # Multiple simultaneous touches show up as confluence in the
            # observation vector; we only need one training sample per moment.
            if not newly_touched:
                continue
            # Cooldown: skip if too soon after last episode
            if self._last_episode_ts is not None:
                elapsed = (tick["ts"] - self._last_episode_ts).total_seconds()
                if elapsed < self._episode_cooldown.total_seconds():
                    continue

            level_name, level_type, level_price = newly_touched[0]
            if True:  # single-episode block
                # Forward ticks: cap at ~30 min of data (TIMEOUT_MINUTES)
                # NQ averages ~6k ticks/min, 30 min ≈ 180k ticks max.
                # Use 20k tick cap as practical upper bound.
                max_forward = min(len(norm_ticks) - i - 1, 20_000)
                forward_raw = norm_ticks[i + 1 : i + 1 + max_forward]
                forward_objs = [_TickView(t) for t in forward_raw]

                state = self._build_state(tick, level_type, session_date, date_str)
                observation = build_observation(state)

                episode = label_outcome(
                    touch_price=price,
                    forward_ticks=forward_objs,
                    observation=observation,
                    level_type=level_type.value,
                    touch_ts=tick["ts"],
                )
                episodes.append(episode)
                self._last_episode_ts = tick["ts"]
                log.debug(
                    "Episode at %s: %s @ %.2f → best=%s",
                    tick["ts"],
                    level_name,
                    price,
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
            self._orderflow_signals = compute_signals(
                self._candle_flows, direction="long", lookback=10
            )

        # Invalidate naked POCs that the current session has swept through
        if (self._precomputed and self._precomputed.get("naked_pocs")
                and self._session_high is not None):
            self._precomputed["naked_pocs"] = [
                n for n in self._precomputed["naked_pocs"]
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
        _add_optional(levels, "nyib_high", LevelType.NYIB_HIGH, getattr(sl, "nyib_high", None))
        _add_optional(levels, "nyib_low", LevelType.NYIB_LOW, getattr(sl, "nyib_low", None))

        # --- TPO levels ---
        tpo = self._tpo_profile if hasattr(self, "_tpo_profile") else None
        if tpo:
            _add_optional(levels, "tpoc", LevelType.TPOC, tpo.get("poc"))
            _add_optional(levels, "tvah", LevelType.TVAH, tpo.get("vah"))
            _add_optional(levels, "tval", LevelType.TVAL, tpo.get("val"))
        _add_optional(levels, "tibh", LevelType.TIBH, getattr(sl, "ib_high", None))
        _add_optional(levels, "tibl", LevelType.TIBL, getattr(sl, "ib_low", None))

        # --- Fair Value Gaps (midpoint) — only significant gaps (≥2 ticks wide) ---
        for fvg in self._fvgs:
            gap_size = fvg.price_high - fvg.price_low
            if gap_size >= TICK_SIZE * 2:
                mid = (fvg.price_low + fvg.price_high) / 2.0
                levels.append(("fvg", LevelType.FVG, mid))

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

            for sp_low, sp_high in self._precomputed.get("single_print_zones", []):
                mid = (sp_low + sp_high) / 2.0
                levels.append(("single_print", LevelType.SINGLE_PRINT, mid))

        self._active_levels = levels

    # ------------------------------------------------------------------
    # Level touch detection with debouncing
    # ------------------------------------------------------------------

    def _check_level_touch(
        self, price: float
    ) -> list[tuple[str, LevelType, float]]:
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
    # State dict construction
    # ------------------------------------------------------------------

    def _build_state(
        self,
        tick: dict,
        level_type: LevelType,
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
        if bars_30m:
            profile = build_full_tpo_profile(bars_30m, tick_size=TICK_SIZE)
            tpo_profile_dict = {
                "poc": profile.poc,
                "vah": profile.vah,
                "val": profile.val,
                "shape": profile.profile_shape,
                "rotation_factor": profile.rotation_factor,
                "rotation_count": profile.rotation_factor,
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

        # Session context
        session_context = self._build_session_context(ts, bars_1m, session_date)

        # Orderflow signals as dict (or None)
        of_signals = self._orderflow_signals

        # All active level prices for confluence encoding
        all_level_prices = [lp for _, _, lp in self._active_levels]

        # Macro context for this date
        macro = self._macro_data.get(date_str)

        return {
            "level_type": level_type,
            "price": price,
            "candles": recent_flows,
            "vwap_bands": vwap_bands,
            "volume_profile": vp,
            "tpo_profile": tpo_profile_dict,
            "session_levels": self._session_levels,
            "all_levels": all_level_prices,
            "orderflow_signals": of_signals,
            "macro": macro,
            "session_context": session_context,
        }

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
        rth_bar_count = sum(
            1 for b in bars_1m
            if _is_rth_bar(b)
        )
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

        return {
            "minutes_since_rth": minutes_since_open,
            "minute_of_day": minute_of_day,
            "session_volume_pct": session_volume_pct,
            "daily_range_pct": daily_range_pct,
            "session_type": session_type,
            "ib_broken": ib_broken_str,
        }

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


class _TickView:
    """Lightweight wrapper so raw tick dicts expose .ts and .price attributes.

    Required by label_outcome(), which accesses tick.ts and tick.price.
    """

    __slots__ = ("ts", "price")

    def __init__(self, tick: dict) -> None:
        self.ts: datetime = tick["ts"]
        self.price: float = tick["price"]
