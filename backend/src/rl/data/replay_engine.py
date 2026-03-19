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
    detect_swing_points,
    SessionLevels,
)
from ...market_data.tpo import (
    compute_tpo_profile,
    classify_tpo_shape,
    compute_rotation_factor,
    detect_excess,
)
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
        self._swing_points: dict = {}

        # Active levels: list of (name, LevelType, price)
        self._active_levels: list[tuple[str, LevelType, float]] = []

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def replay_session(
        self,
        ticks: list[Any],
        session_date: datetime,
        prior_session_levels: dict | None = None,
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

        # Normalise ticks to dicts once for uniform access
        norm_ticks: list[dict] = [_normalise_tick(t) for t in ticks]

        episodes: list[Episode] = []
        date_str = _date_key(session_date)

        for i, tick in enumerate(norm_ticks):
            price: float = tick["price"]

            # 1. Update candle aggregator → detect 1m bar closes
            completed_bars = self._candle_agg.update(tick)

            # 2. Update running accumulators
            self._vwap.update(price, tick["size"])
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
            self._swing_points = detect_swing_points(recent_bars)

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

        # --- Volume profile: POC, VAH, VAL (skip single prints as levels — too noisy) ---
        vp = self._vp.get()
        if vp is not None:
            levels.append(("poc_session", LevelType.POC_SESSION, vp.poc))
            levels.append(("vah", LevelType.VAH, vp.vah))
            levels.append(("val", LevelType.VAL, vp.val))
            # Single prints excluded as individual levels (too noisy — 200+
            # per session). They're still used as features in the observation
            # vector via the VP profile data.

        # --- Session structural levels ---
        sl = self._session_levels
        _add_optional(levels, "ib_high", LevelType.IB_HIGH, sl.ib_high)
        _add_optional(levels, "ib_low", LevelType.IB_LOW, sl.ib_low)
        _add_optional(levels, "pdh", LevelType.PDH, sl.pdh)
        _add_optional(levels, "pdl", LevelType.PDL, sl.pdl)
        _add_optional(levels, "tokyo_high", LevelType.TOKYO_HL, sl.tokyo_high)
        _add_optional(levels, "tokyo_low", LevelType.TOKYO_HL, sl.tokyo_low)
        _add_optional(levels, "london_high", LevelType.LONDON_HL, sl.london_high)
        _add_optional(levels, "london_low", LevelType.LONDON_HL, sl.london_low)
        _add_optional(levels, "weekly_high", LevelType.WEEKLY_HL, sl.weekly_high)
        _add_optional(levels, "weekly_low", LevelType.WEEKLY_HL, sl.weekly_low)
        _add_optional(levels, "monthly_high", LevelType.MONTHLY_HL, sl.monthly_high)
        _add_optional(levels, "monthly_low", LevelType.MONTHLY_HL, sl.monthly_low)

        # --- Fair Value Gaps (midpoint) — only significant gaps (≥2 ticks wide) ---
        for fvg in self._fvgs:
            gap_size = fvg.price_high - fvg.price_low
            if gap_size >= TICK_SIZE * 2:
                mid = (fvg.price_low + fvg.price_high) / 2.0
                levels.append(("fvg", LevelType.FVG, mid))

        # --- Order blocks (midpoint) ---
        for ob in self._order_blocks:
            mid = (ob.price_low + ob.price_high) / 2.0
            levels.append(("order_block", LevelType.ORDER_BLOCK, mid))

        # --- Swing points: last HH, HL, LH, LL ---
        sp = self._swing_points
        _add_optional(levels, "swing_hh", LevelType.SWING_POINT, sp.get("last_hh"))
        _add_optional(levels, "swing_hl", LevelType.SWING_POINT, sp.get("last_hl"))
        _add_optional(levels, "swing_lh", LevelType.SWING_POINT, sp.get("last_lh"))
        _add_optional(levels, "swing_ll", LevelType.SWING_POINT, sp.get("last_ll"))

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
            profile = compute_tpo_profile(bars_30m, tick_size=TICK_SIZE)
            shape = classify_tpo_shape(profile)
            rotation_factor, rotation_count = compute_rotation_factor(bars_30m)
            excess_high, excess_low = detect_excess(profile)
            tpo_profile_dict = {
                "poc": profile.poc,
                "vah": profile.vah,
                "val": profile.val,
                "shape": shape,
                "rotation_factor": rotation_factor,
                "rotation_count": rotation_count,
                "excess_high": excess_high,
                "excess_low": excess_low,
                "poor_high": profile.poor_high,
                "poor_low": profile.poor_low,
                "single_prints": profile.single_prints,
                "ledges": profile.ledges,
                "ib_tpo_count": profile.ib_tpo_count,
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
        """Compute session_context dict for structure_features.py."""
        # RTH open in ET: 09:30
        ts_et = ts.astimezone(ET) if ts.tzinfo else ts.replace(tzinfo=timezone.utc).astimezone(ET)
        rth_open = ts_et.replace(hour=9, minute=30, second=0, microsecond=0)
        minutes_since_open = max(0.0, (ts_et - rth_open).total_seconds() / 60.0)

        # Session volume as fraction of total (proxy: completed bars vs all bars)
        total_volume = sum(b.get("volume", 0) for b in bars_1m)
        # We don't have a "total expected" reference, so use raw volume normalised
        session_volume_pct = min(1.0, total_volume / max(1, total_volume))  # always 1.0; callers normalise by avg

        # Daily range percentage
        if bars_1m:
            session_high = max(b["high"] for b in bars_1m)
            session_low = min(b["low"] for b in bars_1m)
            daily_range_pct = (session_high - session_low) / max(1e-6, session_low)
        else:
            session_high = 0.0
            session_low = 0.0
            daily_range_pct = 0.0

        # IB breakout status
        sl = self._session_levels
        ib_high = sl.ib_high
        ib_low = sl.ib_low
        ib_broken = False
        if ib_high is not None and ib_low is not None:
            price_now = bars_1m[-1]["close"] if bars_1m else 0.0
            ib_broken = price_now > ib_high or price_now < ib_low

        return {
            "minutes_since_rth_open": minutes_since_open,
            "session_volume_pct": session_volume_pct,
            "daily_range_pct": daily_range_pct,
            "ib_high": ib_high,
            "ib_low": ib_low,
            "ib_broken": ib_broken,
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
