"""L2 orderflow confirmation signals computed from tick data."""
from dataclasses import dataclass
from datetime import datetime


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

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def body_ratio(self) -> float:
        """Body as fraction of spread. Low ratio + high volume = absorption."""
        return self.body / self.spread if self.spread > 0 else 0


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
    )
