"""Extract candle snapshot feature vectors for M6 temporal pattern model."""

from src.market_data.orderflow import CandleFlow

TICK_SIZE = 0.25  # NQ/ES futures minimum tick


def snapshot_candles(
    candles: list[CandleFlow],
    vwap: float | None = None,
    poc: float | None = None,
    max_candles: int = 20,
) -> list[dict]:
    """Return a list of feature dicts for the last max_candles candles.

    Each dict captures orderflow, structure, and context for one candle.
    Footprint fields (imbalance_ratio_max etc.) are None placeholders —
    populated by a separate footprint enrichment step.
    """
    window = candles[-max_candles:] if len(candles) > max_candles else candles
    if not window:
        return []

    avg_volume = sum(c.volume for c in window) / len(window)

    # Build cumulative delta across the window (oldest → newest)
    cvd_running = 0
    result = []
    for candle in window:
        cvd_running += candle.delta
        volume = candle.volume
        delta_pct = candle.delta / volume if volume > 0 else None
        volume_ratio = volume / avg_volume if avg_volume > 0 else None
        abs_delta = abs(candle.delta)
        passive_active = (volume - abs_delta) / abs_delta if abs_delta > 0 else None
        close_position = (candle.close - candle.low) / (candle.high - candle.low) if candle.high != candle.low else None
        vwap_distance = (candle.close - vwap) / TICK_SIZE if vwap is not None else None
        poc_distance = (candle.close - poc) / TICK_SIZE if poc is not None else None
        # spread field on CandleFlow = high - low (candle range), not bid/ask spread
        spread_ticks = candle.spread / TICK_SIZE

        # Footprint: compute from price_levels if available
        imb_max = candle.imbalance_ratio_max if hasattr(candle, "imbalance_ratio_max") else None
        # Big trades: candles with volume >= 3× median
        big_count = None
        big_net = None
        if avg_volume > 0:
            big_count = 1 if volume >= avg_volume * 3 else 0
            big_net = candle.delta if big_count else 0

        result.append(
            {
                "ts": candle.ts.isoformat(),
                "delta": candle.delta,
                "delta_pct": delta_pct,
                "cvd": cvd_running,
                "volume": volume,
                "volume_ratio": volume_ratio,
                "spread_ticks": spread_ticks,
                "body_ratio": candle.body_ratio,
                "close_position": close_position,
                "tick_count": candle.tick_count,
                "passive_active_ratio": passive_active,
                "vwap_distance_ticks": vwap_distance,
                "poc_distance_ticks": poc_distance,
                # Footprint — computed from tick-level price flow
                "imbalance_ratio_max": imb_max,
                "stacked_imbalance_count": None,  # Set below after full window processed
                "big_trades_count": big_count,
                "big_trades_net_delta": big_net,
            }
        )

    # Backfill stacked_imbalance_count per candle (consecutive same-direction imbalances ending at each bar)
    for i in range(len(result)):
        imb = result[i].get("imbalance_ratio_max")
        if imb is None:
            continue
        if imb >= 0.65:
            cur_dir = "buy"
        elif imb <= 0.35:
            cur_dir = "sell"
        else:
            result[i]["stacked_imbalance_count"] = 0
            continue
        count = 1
        for j in range(i - 1, -1, -1):
            prev_imb = result[j].get("imbalance_ratio_max")
            if prev_imb is None:
                break
            prev_dir = "buy" if prev_imb >= 0.65 else ("sell" if prev_imb <= 0.35 else "neutral")
            if prev_dir == cur_dir:
                count += 1
            else:
                break
        result[i]["stacked_imbalance_count"] = count

    return result
