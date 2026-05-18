"""L2 orderflow confirmation signals computed from tick data."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

TICK_SIZE = 0.25  # NQ futures minimum tick


IMBALANCE_THRESHOLD = 3.0  # ratio threshold for diagonal imbalance
IMBALANCE_MIN_VOL = 5  # minimum volume on dominant side to qualify
STACKED_MIN_COUNT = 3  # minimum consecutive imbalances to form a stack


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
class DiagonalImbalance:
    """A single price level with a diagonal imbalance (buy@N vs sell@N+1)."""

    price: float
    direction: str  # "buy" or "sell"
    ratio: float  # dominant / weak (capped at 99)


@dataclass
class StackedImbalance:
    """Consecutive price levels with same-direction diagonal imbalance."""

    direction: str
    price_low: float
    price_high: float
    count: int


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
    def delta_pct(self) -> float:
        return (self.delta / self.volume * 100) if self.volume else 0.0

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

    @property
    def diagonal_imbalances(self) -> list[DiagonalImbalance]:
        """Diagonal imbalance: compare buy@price vs sell@price+tick.

        A buy imbalance at price N means buyers lifted the offer at N while
        few sellers hit the bid at N+1 (price above).  This shows aggressive
        buying pressure that wasn't met by supply one tick higher.
        """
        if len(self.price_levels) < 2:
            return []
        by_price = {pl.price: pl for pl in self.price_levels}
        prices = sorted(by_price.keys())
        result: list[DiagonalImbalance] = []
        for i in range(len(prices) - 1):
            lo = by_price[prices[i]]
            hi = by_price[prices[i + 1]]
            # Buy imbalance: buy@lo vs sell@hi
            if lo.buy_volume >= IMBALANCE_MIN_VOL and lo.buy_volume > hi.sell_volume * IMBALANCE_THRESHOLD:
                ratio = lo.buy_volume / max(1, hi.sell_volume)
                result.append(DiagonalImbalance(price=lo.price, direction="buy", ratio=min(round(ratio, 1), 99)))
            # Sell imbalance: sell@hi vs buy@lo
            if hi.sell_volume >= IMBALANCE_MIN_VOL and hi.sell_volume > lo.buy_volume * IMBALANCE_THRESHOLD:
                ratio = hi.sell_volume / max(1, lo.buy_volume)
                result.append(DiagonalImbalance(price=hi.price, direction="sell", ratio=min(round(ratio, 1), 99)))
        return result

    @property
    def stacked_imbalances(self) -> list[StackedImbalance]:
        """Find runs of consecutive same-direction diagonal imbalances."""
        diags = self.diagonal_imbalances
        if len(diags) < STACKED_MIN_COUNT:
            return []
        # Sort by price
        diags_sorted = sorted(diags, key=lambda d: d.price)
        stacks: list[StackedImbalance] = []
        run_start = 0
        for i in range(1, len(diags_sorted)):
            same_dir = diags_sorted[i].direction == diags_sorted[run_start].direction
            consecutive = (diags_sorted[i].price - diags_sorted[i - 1].price) <= TICK_SIZE * 1.5
            if not (same_dir and consecutive):
                # Flush current run if long enough
                run_len = i - run_start
                if run_len >= STACKED_MIN_COUNT:
                    stacks.append(
                        StackedImbalance(
                            direction=diags_sorted[run_start].direction,
                            price_low=diags_sorted[run_start].price,
                            price_high=diags_sorted[i - 1].price,
                            count=run_len,
                        )
                    )
                run_start = i
        # Final run
        run_len = len(diags_sorted) - run_start
        if run_len >= STACKED_MIN_COUNT:
            stacks.append(
                StackedImbalance(
                    direction=diags_sorted[run_start].direction,
                    price_low=diags_sorted[run_start].price,
                    price_high=diags_sorted[-1].price,
                    count=run_len,
                )
            )
        return stacks


@dataclass
class OrderflowSignals:
    """Aggregated orderflow confirmation signals."""

    delta: int
    delta_aligned: bool  # Delta matches expected trade direction
    delta_divergence: bool  # Price vs delta disagree
    delta_unwind: bool  # Rapid delta flip at extreme
    cvd: int
    cvd_trend: str  # "rising", "falling", "flat"
    vsa_absorption: bool  # High volume + narrow spread
    tick_vol_accelerating: bool
    trapped_traders: bool
    passive_active_ratio: float  # > 1.0 = more passive (limit) orders than aggressive
    # Footprint signals
    big_trades_count: int = 0  # Ticks with size >= 3× median
    big_trades_net_delta: int = 0  # Net direction of big trades (+ = buy, - = sell)
    stop_run_detected: bool = False  # Price spike on high vol that reversed quickly
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
            delta=0,
            delta_aligned=False,
            delta_divergence=False,
            delta_unwind=False,
            cvd=0,
            cvd_trend="flat",
            vsa_absorption=False,
            tick_vol_accelerating=False,
            trapped_traders=False,
            passive_active_ratio=0.0,
        )

    recent = candles[-lookback:] if len(candles) >= lookback else candles
    last = recent[-1]
    prev = recent[-2]

    # Delta
    total_delta = sum(c.delta for c in recent)
    delta_aligned = (total_delta > 0 and direction == "long") or (total_delta < 0 and direction == "short")

    # Delta divergence: multi-bar cumulative-delta divergence at a price
    # extreme. The old single-candle definition (price_up & !delta_positive)
    # fired on noise and was neutral in the 2026-05-18 audit despite firing
    # 47%. Per Fabio + Flowhorse methodology, true divergence requires:
    #   - bull div: price makes a NEW HIGHER HIGH over the last 5 bars
    #     AND cumulative delta does NOT confirm the higher-high
    #   - bear div: price makes a NEW LOWER LOW over the last 5 bars
    #     AND cumulative delta does NOT confirm the lower-low
    delta_divergence = False
    if len(recent) >= 5:
        last5 = recent[-5:]
        prices_recent = [c.close for c in last5]
        cum_delta = 0.0
        deltas_cum = []
        for c in last5:
            cum_delta += c.delta
            deltas_cum.append(cum_delta)
        price_higher_high = prices_recent[-1] > max(prices_recent[:-1])
        delta_lower_high = deltas_cum[-1] < max(deltas_cum[:-1])
        price_lower_low = prices_recent[-1] < min(prices_recent[:-1])
        delta_higher_low = deltas_cum[-1] > min(deltas_cum[:-1])
        bull_div = price_higher_high and delta_lower_high
        bear_div = price_lower_low and delta_higher_low
        delta_divergence = bull_div or bear_div

    # Delta unwind: sign flipped from previous candle + magnitude > 50% of prev
    delta_unwind = (last.delta > 0 and prev.delta < 0 and abs(last.delta) > abs(prev.delta) * 0.5) or (
        last.delta < 0 and prev.delta > 0 and abs(last.delta) > abs(prev.delta) * 0.5
    )

    # CVD
    cvd = sum(c.delta for c in recent)
    if len(recent) >= 5:
        cvd_first_half = sum(c.delta for c in recent[: len(recent) // 2])
        cvd_second_half = sum(c.delta for c in recent[len(recent) // 2 :])
        if cvd_second_half > cvd_first_half * 1.2:
            cvd_trend = "rising"
        elif cvd_second_half < cvd_first_half * 0.8:
            cvd_trend = "falling"
        else:
            cvd_trend = "flat"
    else:
        cvd_trend = "flat"

    # VSA absorption: high volume + narrow body + close at range extreme.
    # Iteration 2026-05-18 (post-Phase-1 audit): the first relaxation
    # (1.3×, body<0.4, range_pos>0.65|<0.35) raised firing rate from 1.9%
    # → 4.6% BUT lost the role (was STOP+3.2/RUNNER+2.7, now neutral).
    # Root cause: 0.65/0.35 range_pos lets in candles that aren't really
    # at the extreme — the methodology requires close AT the extreme.
    # Keep the volume + body relaxations (those didn't dilute), tighten
    # range_pos back to 0.7/0.3.
    prior_candles_for_vol = recent[:-1] if len(recent) > 1 else recent
    prior_avg_volume = sum(c.volume for c in prior_candles_for_vol) / max(len(prior_candles_for_vol), 1)
    avg_volume = sum(c.volume for c in recent) / len(recent)  # kept for downstream usage below
    if last.volume > prior_avg_volume * 1.3 and last.body_ratio < 0.4:
        last_range = max(last.high - last.low, 1e-6)
        range_pos = (last.close - last.low) / last_range
        vsa_absorption = range_pos > 0.7 or range_pos < 0.3
    else:
        vsa_absorption = False

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

    # Stop run detection: spike beyond prior range + RECLAIM back inside +
    # strong reversal candle + spike on high volume. The classic Fabio /
    # Ryan stop-run pattern: sweep liquidity below/above a level then
    # close back inside it, trapping breakout traders.
    # Bugs fixed 2026-05-18 (audit_gbt_orderflow): reclaim + body_ratio +
    # self-inflation baseline.
    # Thresholds relaxed + 3-bar variant added 2026-05-18 (PROFILE follow-
    # up): prior version (body > 0.4, vol > prior_avg × 1.5, 2-bar only)
    # fired only 2.6%. It is the BEST runner predictor when it does fire
    # (+3.4pt runner, +0.049R vel), so giving the model more samples is
    # high-value. 3-bar variant: spike + doji + reversal (common pattern).
    stop_run_detected = False

    def _check_stop_run(prior_candles, spike, reversal) -> bool:
        if not prior_candles:
            return False
        prior_high = max(c.high for c in prior_candles)
        prior_low = min(c.low for c in prior_candles)
        prior_avg_vol = sum(c.volume for c in prior_candles) / max(len(prior_candles), 1)
        vol_threshold = prior_avg_vol * 1.3
        body_threshold = 0.3
        bull = (
            spike.low < prior_low
            and reversal.close > prior_low
            and reversal.close > spike.close
            and spike.volume > vol_threshold
            and reversal.body_ratio > body_threshold
        )
        bear = (
            spike.high > prior_high
            and reversal.close < prior_high
            and reversal.close < spike.close
            and spike.volume > vol_threshold
            and reversal.body_ratio > body_threshold
        )
        return bull or bear

    if len(recent) >= 4:
        # 2-bar variant: spike + reversal back-to-back.
        if _check_stop_run(recent[:-2], recent[-2], recent[-1]):
            stop_run_detected = True
    if not stop_run_detected and len(recent) >= 5:
        # 3-bar variant: spike + doji/inside-bar + reversal.
        if _check_stop_run(recent[:-3], recent[-3], recent[-1]):
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
