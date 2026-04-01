"""
MarketStructureEngine — Dow Theory state machine for BOS/CHoCH detection.

Swing confirmation rule: close-only.
A swing high is confirmed only when a subsequent candle CLOSES below the prior swing low.
A swing low is confirmed only when a subsequent candle CLOSES above the prior swing high.
Wick-only breaks (liquidity sweeps) do not count.

States:
  SEEKING_HIGH — tracking a potential swing high since last confirmed low
  SEEKING_LOW  — tracking a potential swing low since last confirmed high

Trend states:
  ranging, uptrend, downtrend, reversing_up, reversing_down
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SwingLevel:
    price: float
    timestamp: int        # epoch seconds
    type: str             # "swing_high" or "swing_low"
    timeframe: str = ""   # set by caller


@dataclass
class StructureEvent:
    price: float          # candle close that triggered the confirmation
    timestamp: int        # epoch seconds of the triggering candle
    event_type: str       # "bos_bullish" | "bos_bearish" | "choch_bullish" | "choch_bearish"
    swing_type: str       # "swing_high" | "swing_low" — the swing that was confirmed
    swing_price: float    # price of the confirmed swing point


@dataclass
class StructureResult:
    structure: str        # "uptrend" | "downtrend" | "reversing_up" | "reversing_down" | "ranging"
    swing_highs: list[SwingLevel]      # confirmed, newest first, max 3
    swing_lows: list[SwingLevel]       # confirmed, newest first, max 3
    last_bos: StructureEvent | None
    last_choch: StructureEvent | None
    bos_active: bool                   # True if last BOS is within recency window
    choch_active: bool                 # True if last CHoCH is within recency window
    events: list[StructureEvent]       # all events in chronological order


# ---------------------------------------------------------------------------
# Internal state sentinel
# ---------------------------------------------------------------------------

_SEEKING_HIGH = "SEEKING_HIGH"
_SEEKING_LOW = "SEEKING_LOW"

_UPTREND = "uptrend"
_DOWNTREND = "downtrend"
_REVERSING_UP = "reversing_up"
_REVERSING_DOWN = "reversing_down"
_RANGING = "ranging"

# How many bars back the last BOS/CHoCH is still considered "active"
_DEFAULT_RECENCY_BARS = 10

# Max confirmed swings to keep per side (newest first)
_MAX_SWINGS = 3


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class MarketStructureEngine:
    """
    Processes OHLC candles one at a time (or in batch) and tracks
    market structure via a two-state machine (SEEKING_HIGH / SEEKING_LOW).

    Each candle must be a dict (or object with attributes) containing:
        open, high, low, close, ts  (ts = epoch seconds int)

    The engine can be called with process(candles) for a full batch, or
    step(candle) for incremental processing.
    """

    def __init__(
        self,
        recency_bars: int = _DEFAULT_RECENCY_BARS,
        timeframe: str = "",
        use_close_only: bool = False,
    ) -> None:
        self._recency_bars = recency_bars
        self._timeframe = timeframe
        self._use_close_only = use_close_only
        self._reset()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, candles: list[dict]) -> StructureResult:
        """Process a full list of candles and return the current structure result."""
        self._reset()
        for c in candles:
            self._step(c)
        return self._build_result(len(candles))

    def step(self, candle: dict) -> StructureResult:
        """Process a single new candle incrementally and return updated result."""
        self._step(candle)
        self._bar_count += 1
        return self._build_result(self._bar_count)

    # ------------------------------------------------------------------
    # Internal reset
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self._state: str = _SEEKING_HIGH
        self._trend: str = _RANGING

        # Potential extremes being tracked (not yet confirmed)
        self._potential_high_price: float | None = None
        self._potential_high_ts: int | None = None
        self._potential_low_price: float | None = None
        self._potential_low_ts: int | None = None

        # Confirmed swings (newest first, capped at _MAX_SWINGS)
        self._confirmed_highs: list[SwingLevel] = []
        self._confirmed_lows: list[SwingLevel] = []

        # Events
        self._events: list[StructureEvent] = []
        self._last_bos: StructureEvent | None = None
        self._last_choch: StructureEvent | None = None
        self._last_bos_bar: int = -1
        self._last_choch_bar: int = -1

        # Bar counter (for recency window tracking)
        self._bar_count: int = 0

    # ------------------------------------------------------------------
    # Core step logic
    # ------------------------------------------------------------------

    def _step(self, c: dict) -> None:
        hi = c["high"]
        lo = c["low"]
        cl = c["close"]
        ts = int(c["ts"])

        if self._state == _SEEKING_HIGH:
            self._step_seeking_high(hi, lo, cl, ts)
        else:
            self._step_seeking_low(hi, lo, cl, ts)

        self._bar_count += 1

    def _step_seeking_high(self, hi: float, lo: float, cl: float, ts: int) -> None:
        """
        We are rising (or starting up). Track the running potential high.
        Confirmation trigger: price breaks BELOW the last confirmed swing low.
        """
        # Track whether potential_high advances this bar (used for bootstrap reset)
        high_advanced = (self._potential_high_price is None or hi > self._potential_high_price)

        # Update running potential high
        if high_advanced:
            self._potential_high_price = hi
            self._potential_high_ts = ts

        break_price_dn = cl if self._use_close_only else lo

        # Check for confirmation: do we have a confirmed low to break below?
        if self._confirmed_lows and self._potential_high_price is not None:
            last_sl = self._confirmed_lows[0]
            if break_price_dn < last_sl.price:
                self._confirm_high(cl, ts)
                return

            # Persistent uptrend: price breaks above last confirmed swing high
            # WITHOUT first dipping below the swing low → force-confirm pair
            if self._confirmed_highs and self._potential_low_price is not None:
                last_sh = self._confirmed_highs[0]
                break_price_up = cl if self._use_close_only else hi
                if break_price_up > last_sh.price:
                    self._force_swing_pair(
                        sh_price=self._potential_high_price, sh_ts=self._potential_high_ts or ts,
                        sl_price=self._potential_low_price, sl_ts=self._potential_low_ts or ts,
                        trigger_close=cl, trigger_ts=ts,
                    )
                    return

            # Track potential low (for persistent trend + next cycle)
            if self._potential_low_price is None or lo < self._potential_low_price:
                self._potential_low_price = lo
                self._potential_low_ts = ts

        elif not self._confirmed_lows:
            # Bootstrap phase: no confirmed lows yet.
            # Check BEFORE updating potential_low (close can't be below its own low)
            if (
                self._potential_high_price is not None
                and self._potential_low_price is not None
                and break_price_dn < self._potential_low_price
                and self._potential_high_price > self._potential_low_price
            ):
                self._confirm_high(cl, ts)
            else:
                # When potential_high advances, reset potential_low to this bar's
                # low so the reference tracks the low near the current high.
                # Without this, a sustained uptrend from the start leaves
                # potential_low stuck at the very first bar's low, and the
                # bootstrap check never fires.
                if high_advanced:
                    self._potential_low_price = lo
                    self._potential_low_ts = ts
                elif self._potential_low_price is None or lo < self._potential_low_price:
                    self._potential_low_price = lo
                    self._potential_low_ts = ts

    def _step_seeking_low(self, hi: float, lo: float, cl: float, ts: int) -> None:
        """
        We are falling (or just confirmed a high). Track the running potential low.
        Confirmation trigger: price breaks ABOVE the last confirmed swing high.

        Persistent trend: if price breaks below the last confirmed swing low
        WITHOUT first bouncing above the swing high, we force-confirm the
        potential low and re-enter SEEKING_LOW to continue tracking the move.
        """
        # Track potential high for when we flip to SEEKING_HIGH
        if self._potential_high_price is None or hi > self._potential_high_price:
            self._potential_high_price = hi
            self._potential_high_ts = ts

        # Save old potential low BEFORE updating — needed for bootstrap comparison
        prev_potential_low = self._potential_low_price

        # Update running potential low
        if self._potential_low_price is None or lo < self._potential_low_price:
            self._potential_low_price = lo
            self._potential_low_ts = ts

        break_price_up = cl if self._use_close_only else hi
        break_price_dn = cl if self._use_close_only else lo

        # Primary: price breaks above last confirmed swing high → confirm low
        if self._confirmed_highs and self._potential_low_price is not None:
            last_sh = self._confirmed_highs[0]
            if break_price_up > last_sh.price:
                self._confirm_low(cl, ts)
                return

        # Persistent downtrend: price breaks below last confirmed swing low
        # → force-confirm the bounce high + the current low, continue SEEKING_LOW
        if self._confirmed_lows and self._potential_low_price is not None and self._potential_high_price is not None:
            last_sl = self._confirmed_lows[0]
            if break_price_dn < last_sl.price:
                self._force_swing_pair(
                    sh_price=self._potential_high_price, sh_ts=self._potential_high_ts or ts,
                    sl_price=self._potential_low_price, sl_ts=self._potential_low_ts or ts,
                    trigger_close=cl, trigger_ts=ts,
                )
        elif not self._confirmed_lows and self._confirmed_highs:
            # Bootstrap: first confirmed high but no confirmed lows yet.
            # Compare against the PREVIOUS potential low (before this bar updated
            # it) so the check can actually fire when price makes a new low.
            # Without this, a sustained first downmove after the initial confirmed
            # high leaves the engine stuck in SEEKING_LOW forever.
            if (
                prev_potential_low is not None
                and self._potential_high_price is not None
                and break_price_dn < prev_potential_low
                and self._potential_high_price > prev_potential_low
            ):
                self._confirm_low(cl, ts)

    # ------------------------------------------------------------------
    # Confirmation helpers
    # ------------------------------------------------------------------

    def _confirm_high(self, trigger_close: float, trigger_ts: int) -> None:
        """Confirm the accumulated potential high as a SwingLevel, classify event."""
        assert self._potential_high_price is not None
        assert self._potential_high_ts is not None

        swing = SwingLevel(
            price=self._potential_high_price,
            timestamp=self._potential_high_ts,
            type="swing_high",
            timeframe=self._timeframe,
        )
        self._confirmed_highs.insert(0, swing)
        if len(self._confirmed_highs) > _MAX_SWINGS:
            self._confirmed_highs.pop()

        # Classify event
        event_type = self._classify_event(is_high=True)
        self._update_trend(event_type)

        event = StructureEvent(
            price=trigger_close,
            timestamp=trigger_ts,
            event_type=event_type,
            swing_type="swing_high",
            swing_price=self._potential_high_price,
        )
        self._events.append(event)
        if "bos" in event_type:
            self._last_bos = event
            self._last_bos_bar = self._bar_count
        else:
            self._last_choch = event
            self._last_choch_bar = self._bar_count

        # Switch to SEEKING_LOW; reset potential low from current bar
        self._state = _SEEKING_LOW
        self._potential_low_price = None
        self._potential_low_ts = None
        self._potential_high_price = None
        self._potential_high_ts = None

    def _confirm_low(self, trigger_close: float, trigger_ts: int) -> None:
        """Confirm the accumulated potential low as a SwingLevel, classify event."""
        assert self._potential_low_price is not None
        assert self._potential_low_ts is not None

        swing = SwingLevel(
            price=self._potential_low_price,
            timestamp=self._potential_low_ts,
            type="swing_low",
            timeframe=self._timeframe,
        )
        self._confirmed_lows.insert(0, swing)
        if len(self._confirmed_lows) > _MAX_SWINGS:
            self._confirmed_lows.pop()

        # Classify event
        event_type = self._classify_event(is_high=False)
        self._update_trend(event_type)

        event = StructureEvent(
            price=trigger_close,
            timestamp=trigger_ts,
            event_type=event_type,
            swing_type="swing_low",
            swing_price=self._potential_low_price,
        )
        self._events.append(event)
        if "bos" in event_type:
            self._last_bos = event
            self._last_bos_bar = self._bar_count
        else:
            self._last_choch = event
            self._last_choch_bar = self._bar_count

        # Switch to SEEKING_HIGH; reset potential high
        self._state = _SEEKING_HIGH
        self._potential_high_price = None
        self._potential_high_ts = None
        self._potential_low_price = None
        self._potential_low_ts = None

    def _force_swing_pair(
        self,
        sh_price: float, sh_ts: int,
        sl_price: float, sl_ts: int,
        trigger_close: float, trigger_ts: int,
    ) -> None:
        """Force-confirm a swing high + swing low pair in a persistent trend.

        Called when price continues trending without reversing to the prior swing:
        - Persistent downtrend: price breaks below prior SL without bouncing above SH
        - Persistent uptrend: price breaks above prior SH without dipping below SL

        This creates two swings and two events, keeping the engine in sync with the trend.
        """
        # Confirm swing high
        sh = SwingLevel(price=sh_price, timestamp=sh_ts, type="swing_high", timeframe=self._timeframe)
        self._confirmed_highs.insert(0, sh)
        if len(self._confirmed_highs) > _MAX_SWINGS:
            self._confirmed_highs.pop()

        sh_event_type = self._classify_event(is_high=True)
        self._update_trend(sh_event_type)
        sh_event = StructureEvent(
            price=trigger_close, timestamp=trigger_ts,
            event_type=sh_event_type, swing_type="swing_high", swing_price=sh_price,
        )
        self._events.append(sh_event)
        if "bos" in sh_event_type:
            self._last_bos = sh_event
            self._last_bos_bar = self._bar_count
        else:
            self._last_choch = sh_event
            self._last_choch_bar = self._bar_count

        # Confirm swing low
        sl = SwingLevel(price=sl_price, timestamp=sl_ts, type="swing_low", timeframe=self._timeframe)
        self._confirmed_lows.insert(0, sl)
        if len(self._confirmed_lows) > _MAX_SWINGS:
            self._confirmed_lows.pop()

        sl_event_type = self._classify_event(is_high=False)
        self._update_trend(sl_event_type)
        sl_event = StructureEvent(
            price=trigger_close, timestamp=trigger_ts,
            event_type=sl_event_type, swing_type="swing_low", swing_price=sl_price,
        )
        self._events.append(sl_event)
        if "bos" in sl_event_type:
            self._last_bos = sl_event
            self._last_bos_bar = self._bar_count
        else:
            self._last_choch = sl_event
            self._last_choch_bar = self._bar_count

        # Reset potentials and stay in same seeking state
        # (the trend is continuing, so we keep looking for the next swing in the same direction)
        self._potential_high_price = None
        self._potential_high_ts = None
        self._potential_low_price = None
        self._potential_low_ts = None

    # ------------------------------------------------------------------
    # Event classification
    # ------------------------------------------------------------------

    def _classify_event(self, is_high: bool) -> str:
        """
        Determine BOS or CHoCH based on current trend and the swing being confirmed.

        Confirming a swing HIGH (bearish break) means price closed below prior swing low.
        Confirming a swing LOW  (bullish break) means price closed above prior swing high.
        """
        if is_high:
            # Bearish event — price broke below a swing low, confirming a swing high
            if self._trend in (_UPTREND, _REVERSING_UP):
                return "choch_bearish"   # breaking against prevailing up bias
            else:
                return "bos_bearish"     # continuation of down move or from ranging
        else:
            # Bullish event — price broke above a swing high, confirming a swing low
            if self._trend in (_DOWNTREND, _REVERSING_DOWN):
                return "choch_bullish"   # breaking against prevailing down bias
            else:
                return "bos_bullish"     # continuation of up move or from ranging

    def _update_trend(self, event_type: str) -> None:
        """Advance the trend state machine."""
        t = self._trend
        if t == _RANGING:
            if event_type == "bos_bullish":
                self._trend = _UPTREND
            elif event_type == "bos_bearish":
                self._trend = _DOWNTREND
            # CHoCH from ranging is impossible by classification logic

        elif t == _UPTREND:
            if event_type == "choch_bearish":
                self._trend = _REVERSING_DOWN
            # bos_bullish → stays uptrend (no state change needed)

        elif t == _REVERSING_DOWN:
            if event_type == "bos_bearish":
                self._trend = _DOWNTREND
            elif event_type == "bos_bullish":
                self._trend = _UPTREND   # false break / failed reversal

        elif t == _DOWNTREND:
            if event_type == "choch_bullish":
                self._trend = _REVERSING_UP
            # bos_bearish → stays downtrend

        elif t == _REVERSING_UP:
            if event_type == "bos_bullish":
                self._trend = _UPTREND
            elif event_type == "bos_bearish":
                self._trend = _DOWNTREND  # false break / failed reversal

    # ------------------------------------------------------------------
    # Result builder
    # ------------------------------------------------------------------

    def _build_result(self, total_bars: int) -> StructureResult:
        bars_since_bos = (total_bars - 1 - self._last_bos_bar) if self._last_bos else total_bars
        bars_since_choch = (total_bars - 1 - self._last_choch_bar) if self._last_choch else total_bars

        return StructureResult(
            structure=self._trend,
            swing_highs=list(self._confirmed_highs),   # already newest-first, max 3
            swing_lows=list(self._confirmed_lows),
            last_bos=self._last_bos,
            last_choch=self._last_choch,
            bos_active=self._last_bos is not None and bars_since_bos <= self._recency_bars,
            choch_active=self._last_choch is not None and bars_since_choch <= self._recency_bars,
            events=list(self._events),
        )
