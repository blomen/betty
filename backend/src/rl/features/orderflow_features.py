"""Orderflow feature extraction from CandleFlow bars and OrderflowSignals."""

from __future__ import annotations

import numpy as np

from ...market_data.l1_quote_state import L1Snapshot
from ...market_data.orderflow import CandleFlow, OrderflowSignals
from ..config import TICK_SIZE
from .l1_features import compute_l1_features

_N_FEATURES = 21

# Indices into the feats array for L1-overridable dims.
# Must stay in sync with the order in _ORDERFLOW_LABELS (observation_index.py)
# and with the np.array([...]) construction below.
_SPREAD_TICKS_IDX = 6
_PASSIVE_ACTIVE_IDX = 7


def extract_orderflow_features(
    candles: list[CandleFlow],
    signals: OrderflowSignals | None = None,
    lookback: int = 20,
    l1_snapshot: L1Snapshot | None = None,
    recent_trades: list[dict] | None = None,
) -> np.ndarray:
    """Extract 21 orderflow features from recent 1-minute candles.

    Feature layout (indices 0-20):
      0  delta_pct          — delta / volume of most recent candle (%)
      1  delta              — raw delta of last candle, normalised by avg volume
      2  cvd                — cumulative delta over lookback, normalised by avg volume
      3  cvd_trend          — -1 falling / 0 flat / 1 rising
      4  volume_ratio       — last candle volume / avg volume
      5  body_ratio         — body / spread of last candle
      6  spread_ticks       — last candle spread in ticks (capped at 50)
      7  passive_active_ratio — from signals (capped at 5)
      8  imbalance_density    — diagonal imbalance count / price levels (0-1)
      9  stacked_imbalance_count — consecutive stacked levels (capped at 10)
     10  stacked_direction   — -1 sell / 0 neutral / 1 buy
     11  big_trades_count    — number of big-volume candles (capped at 10)
     12  big_trades_net_delta — net delta of big trades, normalised by avg volume
     13  realized_range      — lookback-avg 1m candle range, ticks [0,1]
     14  stop_run_detected   — continuous stop-run / sweep strength [0,1]
     -- NEW: temporal dynamics (what the GRU was supposed to learn) --
     15  delta_acceleration  — delta change rate (last 3 vs prev 3 candles)
     16  absorption_strength — high volume + narrow body over last 3 candles (0-1)
     17  initiative_momentum — delta * body_ratio of last candle (strong = high both)
     18  volume_climax       — last candle vol / max vol in lookback (0-1)
     19  delta_divergence    — continuous price-vs-CVD position gap [0,1]
     20  flow_shift          — sign change in 3-candle delta vs prior 3 (0/1)

    Dims 13/14/19 were 0/1 flags until 2026-05-21; the phase19/22 audit
    found them firing on 1-21% of touches — too rare to carry graded
    information. Restructured to continuous measures (computed from
    candles, independent of the signals object).

    When l1_snapshot is provided, dims 6 (spread_ticks) and 7
    (passive_active_ratio) are recomputed from true bid/ask + Lee-Ready
    aggressor classification instead of the candle-derived approximations.
    Other dims are unchanged. Backward-compatible: l1_snapshot=None
    preserves legacy candle-only behaviour.

    Returns zeros(21) if candles is empty — but the L1 override still
    applies to dims 6 and 7 when l1_snapshot is provided, so an
    L1-aware obs vector can populate top-of-book features even when
    candles haven't been built yet (e.g. cold start).
    """
    if not candles:
        feats = np.zeros(_N_FEATURES, dtype=np.float32)
        if l1_snapshot is not None:
            l1_feats = compute_l1_features(snapshot=l1_snapshot, recent_trades=recent_trades or [])
            feats[_SPREAD_TICKS_IDX] = min(l1_feats["spread_ticks"], 50.0) / 50.0
            feats[_PASSIVE_ACTIVE_IDX] = min(l1_feats["passive_active_ratio"], 5.0) / 5.0
        return feats

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
        # Diagonal imbalance density: fraction of price levels showing
        # aggressive one-sided flow (more informative than raw ratio max)
        imbalance_max = min(signals.stacked_imbalance_count / 10.0, 1.0)
        # Override with candle-level diagonal count if available
        if candles:
            _last_cf = candles[-1]
            _n_levels = len(getattr(_last_cf, "price_levels", [])) or 1
            _n_diags = len(getattr(_last_cf, "diagonal_imbalances", []))
            imbalance_max = min(_n_diags / max(_n_levels - 1, 1), 1.0)
        stacked_count = min(signals.stacked_imbalance_count, 10.0) / 10.0
        stacked_dir = {"buy": 1.0, "neutral": 0.0, "sell": -1.0}.get(signals.stacked_imbalance_direction, 0.0)
        big_count = min(signals.big_trades_count, 10.0) / 10.0
        big_net = signals.big_trades_net_delta / max(avg_vol, 1.0)
    else:
        # Derive from raw candle data
        total_vol_sum = sum(c.volume for c in recent)
        total_abs_delta = sum(abs(c.delta) for c in recent)
        passive_active_raw = (total_vol_sum - total_abs_delta) / max(1, total_abs_delta)
        passive_active = min(passive_active_raw, 5.0) / 5.0

        _n_levels = len(getattr(last, "price_levels", [])) or 1
        _n_diags = len(getattr(last, "diagonal_imbalances", []))
        imbalance_max = min(_n_diags / max(_n_levels - 1, 1), 1.0)

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

    # --- continuous realized-range / stop-run (restructured 2026-05-22
    #     from 0/1 flags — phase19/22/23 audit; computed from candles,
    #     signal-independent so signals and no-signals paths agree) ---

    # 13: realized_range — average 1-minute candle range over the lookback,
    #   in ticks (capped/normalised). The volatility REGIME: recent realized
    #   range predicts near-future range (volatility clustering). Absolute,
    #   NOT a ratio — a ratio cancels the regime signal. Distinct from dim 6
    #   spread_ticks (the single partial last bar). Replaces the old
    #   vsa_absorption 0/1 flag (1.4% fire, redundant). Continuous [0,1].
    _avg_range_ticks = sum(max(c.high - c.low, 0.0) for c in recent) / len(recent) / TICK_SIZE
    realized_range = min(_avg_range_ticks / 40.0, 1.0)

    # 14: stop_run — sweep-and-reclaim strength. Reversal probability
    #   scales with sweep volume x poke depth x reclaim-candle body.
    #   0 when there is no sweep pattern.
    stop_run = 0.0
    if len(recent) >= 4:
        _prior = recent[:-2]
        _p_hi = max(c.high for c in _prior)
        _p_lo = min(c.low for c in _prior)
        _p_av = sum(c.volume for c in _prior) / max(len(_prior), 1)
        _spike, _rev = recent[-2], recent[-1]
        _vol_f = min(_spike.volume / max(_p_av, 1.0) / 3.0, 1.0)
        if _spike.low < _p_lo and _rev.close > _p_lo:  # bullish sweep + reclaim
            _depth = min((_p_lo - _spike.low) / TICK_SIZE / 8.0, 1.0)
            stop_run = _vol_f * _depth * min(_rev.body_ratio / 0.5, 1.0)
        elif _spike.high > _p_hi and _rev.close < _p_hi:  # bearish sweep + reclaim
            _depth = min((_spike.high - _p_hi) / TICK_SIZE / 8.0, 1.0)
            stop_run = _vol_f * _depth * min(_rev.body_ratio / 0.5, 1.0)

    # --- NEW: temporal dynamics features ---

    # 15: delta_acceleration — is selling/buying getting stronger or weaker?
    if len(recent) >= 6:
        prev3_delta = sum(c.delta for c in recent[-6:-3])
        last3_delta = sum(c.delta for c in recent[-3:])
        delta_accel = np.clip((last3_delta - prev3_delta) / max(avg_vol, 1.0), -1.0, 1.0)
    else:
        delta_accel = 0.0

    # 16: absorption_strength — high volume + narrow body = passive orders absorbing
    if len(recent) >= 3:
        last3 = recent[-3:]
        avg_vol_3 = sum(c.volume for c in last3) / 3
        avg_body_3 = sum(c.body_ratio for c in last3) / 3
        # High volume relative to lookback + low body ratio = absorption
        vol_factor = min(avg_vol_3 / max(avg_vol, 1.0), 3.0) / 3.0
        body_factor = 1.0 - avg_body_3  # low body = high absorption
        absorption_str = vol_factor * body_factor  # 0 to 1
    else:
        absorption_str = 0.0

    # 17: initiative_momentum — strong directional candle (high delta % + high body ratio)
    init_momentum = np.clip(abs(delta_pct) * last.body_ratio, 0.0, 1.0)

    # 18: volume_climax — is this the highest volume bar in the lookback?
    max_vol = max(c.volume for c in recent) if recent else 1
    vol_climax = last.volume / max(max_vol, 1)

    # 19: delta_divergence — continuous (restructured 2026-05-21 from 0/1).
    # The gap between where price sits in its 5-bar range and where
    # CUMULATIVE delta sits in its own range. Aligned (both extended the
    # same way) -> ~0; price extended but CVD lagging -> large. [0,1].
    # This is the methodology's "effort vs result" — a continuous read
    # of how far CVD failed to confirm the price move, with no threshold.
    if len(recent) >= 5:
        _p5 = np.array([c.close for c in recent[-5:]], dtype=np.float64)
        _c5 = np.cumsum([c.delta for c in recent[-5:]]).astype(np.float64)
        _p_pos = (_p5[-1] - _p5.min()) / max(_p5.max() - _p5.min(), 1e-9)
        _c_pos = (_c5[-1] - _c5.min()) / max(_c5.max() - _c5.min(), 1e-9)
        delta_div = float(abs(_p_pos - _c_pos))
    else:
        delta_div = 0.0

    # 20: flow_shift — did the dominant flow direction change? (absorption → initiative)
    if len(recent) >= 6:
        prev3_net = sum(c.delta for c in recent[-6:-3])
        last3_net = sum(c.delta for c in recent[-3:])
        flow_shift = 1.0 if (prev3_net > 0 and last3_net < 0) or (prev3_net < 0 and last3_net > 0) else 0.0
    else:
        flow_shift = 0.0

    feats = np.array(
        [
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
            realized_range,
            stop_run,
            float(delta_accel),
            float(absorption_str),
            float(init_momentum),
            float(vol_climax),
            float(delta_div),
            float(flow_shift),
        ],
        dtype=np.float32,
    )

    # Clip to avoid extreme outliers
    feats = np.clip(feats, -5.0, 5.0)

    # L1 override: when L1 snapshot is available, recompute the dims that
    # actually need book context. Leave the rest at candle-derived values.
    # spread_ticks and passive_active_ratio require true bid/ask + Lee-Ready
    # aggressor classification; candle-derived values are approximations.
    if l1_snapshot is not None:
        l1_feats = compute_l1_features(snapshot=l1_snapshot, recent_trades=recent_trades or [])
        # Index _SPREAD_TICKS_IDX: spread_ticks (capped at 50, normalised /50)
        feats[_SPREAD_TICKS_IDX] = min(l1_feats["spread_ticks"], 50.0) / 50.0
        # Index _PASSIVE_ACTIVE_IDX: passive_active_ratio (capped at 5, normalised /5)
        feats[_PASSIVE_ACTIVE_IDX] = min(l1_feats["passive_active_ratio"], 5.0) / 5.0

    return feats
