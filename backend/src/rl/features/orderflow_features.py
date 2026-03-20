"""Orderflow feature extraction from CandleFlow bars and OrderflowSignals."""
from __future__ import annotations

import numpy as np

from ...market_data.orderflow import CandleFlow, OrderflowSignals
from ..config import TICK_SIZE

_N_FEATURES = 15


def extract_orderflow_features(
    candles: list[CandleFlow],
    signals: OrderflowSignals | None = None,
    lookback: int = 20,
) -> np.ndarray:
    """Extract 15 orderflow features from recent 1-minute candles.

    Feature layout (indices 0-14):
      0  delta_pct          — delta / volume of most recent candle (%)
      1  delta              — raw delta of last candle, normalised by avg volume
      2  cvd                — cumulative delta over lookback, normalised by avg volume
      3  cvd_trend          — -1 falling / 0 flat / 1 rising
      4  volume_ratio       — last candle volume / avg volume
      5  body_ratio         — body / spread of last candle
      6  spread_ticks       — last candle spread in ticks (capped at 50)
      7  passive_active_ratio — from signals (capped at 5)
      8  imbalance_ratio_max  — most extreme diagonal imbalance ratio (capped at 10)
      9  stacked_imbalance_count — consecutive stacked levels (capped at 10)
     10  stacked_direction   — -1 sell / 0 neutral / 1 buy
     11  big_trades_count    — number of big-volume candles (capped at 10)
     12  big_trades_net_delta — net delta of big trades, normalised by avg volume
     13  vsa_absorption      — 0/1 bool from signals
     14  stop_run_detected   — 0/1 bool from signals

    Returns zeros(15) if candles is empty.
    """
    if not candles:
        return np.zeros(_N_FEATURES, dtype=np.float32)

    recent = candles[-lookback:] if len(candles) >= lookback else candles
    last = recent[-1]

    volumes = [c.volume for c in recent]
    avg_vol = sum(volumes) / len(volumes) if volumes else 1.0

    # 0: delta_pct of last candle (delta / volume as fraction)
    delta_pct = (last.delta / max(last.volume, 1)) / 1.0  # already ±1 range

    # 1: normalised delta of last candle
    delta_norm = last.delta / max(avg_vol, 1.0)

    # 2: cumulative delta normalised
    cvd_raw = sum(c.delta for c in recent)
    cvd_norm = cvd_raw / max(avg_vol * len(recent), 1.0)

    # 3: cvd trend from signals
    if signals is not None:
        cvd_trend_val = {"rising": 1.0, "flat": 0.0, "falling": -1.0}.get(signals.cvd_trend, 0.0)
    else:
        # Compute directly from candles
        half = max(1, len(recent) // 2)
        first_half_cvd = sum(c.delta for c in recent[:half])
        second_half_cvd = sum(c.delta for c in recent[half:])
        if second_half_cvd > first_half_cvd * 1.2:
            cvd_trend_val = 1.0
        elif second_half_cvd < first_half_cvd * 0.8:
            cvd_trend_val = -1.0
        else:
            cvd_trend_val = 0.0

    # 4: volume ratio
    volume_ratio = min(last.volume / max(avg_vol, 1.0), 5.0) / 5.0

    # 5: body ratio
    body_ratio = last.body_ratio

    # 6: spread in ticks (capped at 50)
    spread_ticks = min(last.spread / TICK_SIZE, 50.0) / 50.0

    # 7-14: from signals when available, else derive from candles
    if signals is not None:
        passive_active = min(signals.passive_active_ratio, 5.0) / 5.0
        imbalance_max = min(signals.imbalance_ratio_max, 10.0) / 10.0
        stacked_count = min(signals.stacked_imbalance_count, 10.0) / 10.0
        stacked_dir = {"buy": 1.0, "neutral": 0.0, "sell": -1.0}.get(
            signals.stacked_imbalance_direction, 0.0
        )
        big_count = min(signals.big_trades_count, 10.0) / 10.0
        big_net = signals.big_trades_net_delta / max(avg_vol, 1.0)
        vsa_abs = 1.0 if signals.vsa_absorption else 0.0
        stop_run = 1.0 if signals.stop_run_detected else 0.0
    else:
        # Derive from raw candle data
        total_vol_sum = sum(c.volume for c in recent)
        total_abs_delta = sum(abs(c.delta) for c in recent)
        passive_active_raw = (total_vol_sum - total_abs_delta) / max(1, total_abs_delta)
        passive_active = min(passive_active_raw, 5.0) / 5.0

        diag = getattr(last, "diagonal_imbalances", None)
        imbalance_max_raw = getattr(last, "imbalance_ratio_max", 0.0) if diag else 0.0
        imbalance_max = min(imbalance_max_raw, 10.0) / 10.0

        stacked = getattr(last, "stacked_imbalances", None)
        if stacked:
            largest_stacked = max(stacked, key=lambda s: s.count)
            stacked_count = min(largest_stacked.count, 10.0) / 10.0
            stacked_dir = {"buy": 1.0, "sell": -1.0}.get(largest_stacked.direction, 0.0)
        else:
            stacked_count = 0.0
            stacked_dir = 0.0

        sorted_vols = sorted(c.volume for c in recent)
        median_vol = sorted_vols[len(sorted_vols) // 2] if sorted_vols else 0
        threshold = median_vol * 3 if median_vol > 0 else float("inf")
        big_candles = [c for c in recent if c.volume >= threshold]
        big_count = min(len(big_candles), 10.0) / 10.0
        big_net_raw = sum(c.delta for c in big_candles)
        big_net = big_net_raw / max(avg_vol, 1.0)

        vsa_abs = 1.0 if (last.volume > avg_vol * 1.5 and last.body_ratio < 0.3) else 0.0
        stop_run = 0.0  # Cannot reliably detect without signals

    feats = np.array([
        delta_pct,
        delta_norm,
        cvd_norm,
        cvd_trend_val,
        volume_ratio,
        body_ratio,
        spread_ticks,
        passive_active,
        imbalance_max,
        stacked_count,
        stacked_dir,
        big_count,
        big_net,
        vsa_abs,
        stop_run,
    ], dtype=np.float32)

    # Clip to avoid extreme outliers
    feats = np.clip(feats, -5.0, 5.0)
    return feats
