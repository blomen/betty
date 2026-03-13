"""L2 orderflow confirmation signals computed from tick data."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime


TICK_SIZE = 0.25  # NQ futures minimum tick


@dataclass
class PriceLevelFlow:
    """Aggressor volume at a single price level within a candle."""
    price: float
    buy_volume: int = 0
    sell_volume: int = 0

    @property
    def total(self) -> int:
        return self.buy_volume + self.sell_volume

    @property
    def imbalance_ratio(self) -> float:
        """Buy fraction: >0.65 = buy imbalance, <0.35 = sell imbalance."""
        t = self.total
        return self.buy_volume / t if t > 0 else 0.5


@dataclass
class CandleFlow:
    """Orderflow data for a single candle."""
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    buy_volume: int
    sell_volume: int
    delta: int  # buy_volume - sell_volume
    tick_count: int
    spread: float  # high - low
    # Footprint: per-price-level flow
    price_levels: list[PriceLevelFlow] = field(default_factory=list)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def body_ratio(self) -> float:
        """Body as fraction of spread. Low ratio + high volume = absorption."""
        return self.body / self.spread if self.spread > 0 else 0

    @property
    def imbalance_ratio_max(self) -> float:
        """Max imbalance across price levels (most extreme buy or sell)."""
        if not self.price_levels:
            return 0.5
        ratios = [pl.imbalance_ratio for pl in self.price_levels if pl.total >= 3]
        if not ratios:
            return 0.5
        # Return the ratio furthest from 0.5 (most imbalanced)
        return max(ratios, key=lambda r: abs(r - 0.5))

    @property
    def imbalance_direction(self) -> str:
        """Dominant imbalance direction for this candle."""
        r = self.imbalance_ratio_max
        if r >= 0.65:
            return "buy"
        elif r <= 0.35:
            return "sell"
        return "neutral"


@dataclass
class OrderflowSignals:
    """Aggregated orderflow confirmation signals."""
    delta: int
    delta_aligned: bool        # Delta matches expected trade direction
    delta_divergence: bool     # Price vs delta disagree
    delta_unwind: bool         # Rapid delta flip at extreme
    cvd: int
    cvd_trend: str             # "rising", "falling", "flat"
    vsa_absorption: bool       # High volume + narrow spread
    tick_vol_accelerating: bool
    trapped_traders: bool
    passive_active_ratio: float  # > 1.0 = more passive (limit) orders than aggressive
    # Footprint signals
    big_trades_count: int = 0       # Ticks with size >= 3× median
    big_trades_net_delta: int = 0   # Net direction of big trades (+ = buy, - = sell)
    stop_run_detected: bool = False # Price spike on high vol that reversed quickly
    # Imbalance stacking
    imbalance_ratio_max: float = 0.5  # Most extreme imbalance across recent candles
    stacked_imbalance_count: int = 0  # Consecutive candles with same-direction imbalance
    stacked_imbalance_direction: str = "neutral"  # "buy", "sell", "neutral"


def build_candle_flow(ticks: list[dict], period_seconds: int = 60) -> list[CandleFlow]:
    """Build CandleFlow bars from raw ticks, grouped by period."""
    if not ticks:
        return []

    candles = []
    current_ticks = []
    period_start = None

    for tick in ticks:
        ts = tick["ts"] if isinstance(tick["ts"], datetime) else datetime.fromisoformat(tick["ts"])
        if period_start is None:
            period_start = ts.replace(second=0, microsecond=0)

        # Check if tick belongs to new period
        elapsed = (ts - period_start).total_seconds()
        if elapsed >= period_seconds and current_ticks:
            candles.append(_aggregate_candle(current_ticks, period_start))
            period_start = ts.replace(second=0, microsecond=0)
            current_ticks = []

        current_ticks.append(tick)

    if current_ticks and period_start:
        candles.append(_aggregate_candle(current_ticks, period_start))

    return candles


def _aggregate_candle(ticks: list[dict], ts: datetime) -> CandleFlow:
    prices = [t["price"] for t in ticks]
    buy_vol = sum(t["size"] for t in ticks if t["side"] == "A")
    sell_vol = sum(t["size"] for t in ticks if t["side"] == "B")

    # Build per-price-level footprint
    level_map: dict[float, PriceLevelFlow] = defaultdict(lambda: PriceLevelFlow(price=0.0))
    for t in ticks:
        # Snap price to tick size grid
        px = round(t["price"] / TICK_SIZE) * TICK_SIZE
        if level_map[px].price == 0.0:
            level_map[px] = PriceLevelFlow(price=px)
        if t["side"] == "A":
            level_map[px].buy_volume += t["size"]
        else:
            level_map[px].sell_volume += t["size"]
    price_levels = sorted(level_map.values(), key=lambda pl: pl.price)

    return CandleFlow(
        ts=ts,
        open=prices[0],
        high=max(prices),
        low=min(prices),
        close=prices[-1],
        volume=buy_vol + sell_vol,
        buy_volume=buy_vol,
        sell_volume=sell_vol,
        delta=buy_vol - sell_vol,
        tick_count=len(ticks),
        spread=max(prices) - min(prices),
        price_levels=price_levels,
    )


def compute_signals(
    candles: list[CandleFlow],
    direction: str,  # "long" or "short"
    lookback: int = 10,
) -> OrderflowSignals:
    """Compute orderflow confirmation signals from recent candle flow data."""
    if len(candles) < 3:
        return OrderflowSignals(
            delta=0, delta_aligned=False, delta_divergence=False,
            delta_unwind=False, cvd=0, cvd_trend="flat",
            vsa_absorption=False, tick_vol_accelerating=False,
            trapped_traders=False, passive_active_ratio=0.0,
        )

    recent = candles[-lookback:] if len(candles) >= lookback else candles
    last = recent[-1]
    prev = recent[-2]

    # Delta
    total_delta = sum(c.delta for c in recent)
    delta_aligned = (total_delta > 0 and direction == "long") or (total_delta < 0 and direction == "short")

    # Delta divergence: price making new extreme but delta not confirming
    price_up = last.close > prev.close
    delta_positive = last.delta > 0
    delta_divergence = (price_up and not delta_positive) or (not price_up and delta_positive)

    # Delta unwind: sign flipped from previous candle + magnitude > 50% of prev
    delta_unwind = (
        (last.delta > 0 and prev.delta < 0 and abs(last.delta) > abs(prev.delta) * 0.5) or
        (last.delta < 0 and prev.delta > 0 and abs(last.delta) > abs(prev.delta) * 0.5)
    )

    # CVD
    cvd = sum(c.delta for c in recent)
    if len(recent) >= 5:
        cvd_first_half = sum(c.delta for c in recent[:len(recent)//2])
        cvd_second_half = sum(c.delta for c in recent[len(recent)//2:])
        if cvd_second_half > cvd_first_half * 1.2:
            cvd_trend = "rising"
        elif cvd_second_half < cvd_first_half * 0.8:
            cvd_trend = "falling"
        else:
            cvd_trend = "flat"
    else:
        cvd_trend = "flat"

    # VSA absorption: high volume + narrow body (body_ratio < 0.3) on last candle
    avg_volume = sum(c.volume for c in recent) / len(recent)
    vsa_absorption = last.volume > avg_volume * 1.5 and last.body_ratio < 0.3

    # Tick volume acceleration
    if len(recent) >= 4:
        recent_tick_avg = sum(c.tick_count for c in recent[-3:]) / 3
        prior_tick_avg = sum(c.tick_count for c in recent[:-3]) / max(1, len(recent) - 3)
        tick_vol_accelerating = recent_tick_avg > prior_tick_avg * 1.3
    else:
        tick_vol_accelerating = False

    # Trapped traders: delta flipped after aggressive move in one direction
    trapped_traders = delta_unwind and abs(last.delta) > avg_volume * 0.3

    # Passive/active ratio: total volume vs delta magnitude
    total_vol = sum(c.volume for c in recent)
    total_abs_delta = sum(abs(c.delta) for c in recent)
    passive_active_ratio = (total_vol - total_abs_delta) / max(1, total_abs_delta)

    # Big trade detection: candles with volume >= 3× median volume
    volumes = sorted(c.volume for c in recent)
    median_vol = volumes[len(volumes) // 2] if volumes else 0
    big_threshold = median_vol * 3 if median_vol > 0 else float("inf")
    big_trades_count = sum(1 for c in recent if c.volume >= big_threshold)
    big_trades_net_delta = sum(c.delta for c in recent if c.volume >= big_threshold)

    # Stop run detection: price spike beyond prior range on high volume, then reversal
    stop_run_detected = False
    if len(recent) >= 4:
        prior_high = max(c.high for c in recent[:-2])
        prior_low = min(c.low for c in recent[:-2])
        spike = recent[-2]  # The candle before last
        reversal = recent[-1]  # Current candle
        # Bullish stop run: spike below range, snaps back
        if (spike.low < prior_low and reversal.close > spike.close
                and spike.volume > avg_volume * 1.5):
            stop_run_detected = True
        # Bearish stop run: spike above range, snaps back
        if (spike.high > prior_high and reversal.close < spike.close
                and spike.volume > avg_volume * 1.5):
            stop_run_detected = True

    # Imbalance stacking: consecutive candles with same-direction imbalance (>0.65 buy or <0.35 sell)
    imbalance_ratio_max = 0.5
    stacked_imbalance_count = 0
    stacked_imbalance_direction = "neutral"
    if recent and recent[-1].price_levels:
        imbalance_ratio_max = recent[-1].imbalance_ratio_max
        # Count consecutive same-direction imbalanced candles from the end
        streak_dir = None
        for c in reversed(recent):
            c_dir = c.imbalance_direction
            if c_dir == "neutral":
                break
            if streak_dir is None:
                streak_dir = c_dir
            if c_dir == streak_dir:
                stacked_imbalance_count += 1
            else:
                break
        stacked_imbalance_direction = streak_dir or "neutral"

    return OrderflowSignals(
        delta=total_delta,
        delta_aligned=delta_aligned,
        delta_divergence=delta_divergence,
        delta_unwind=delta_unwind,
        cvd=cvd,
        cvd_trend=cvd_trend,
        vsa_absorption=vsa_absorption,
        tick_vol_accelerating=tick_vol_accelerating,
        trapped_traders=trapped_traders,
        passive_active_ratio=round(passive_active_ratio, 2),
        big_trades_count=big_trades_count,
        big_trades_net_delta=big_trades_net_delta,
        stop_run_detected=stop_run_detected,
        imbalance_ratio_max=round(imbalance_ratio_max, 3),
        stacked_imbalance_count=stacked_imbalance_count,
        stacked_imbalance_direction=stacked_imbalance_direction,
    )
