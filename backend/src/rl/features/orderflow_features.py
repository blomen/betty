"""Orderflow feature extraction from CandleFlow bars and OrderflowSignals."""

from __future__ import annotations

import numpy as np

from ...market_data.l1_quote_state import L1Snapshot
from ...market_data.orderflow import CandleFlow, OrderflowSignals
from ..config import TICK_SIZE
from .l1_features import compute_l1_features

# OF stack growth log:
#   21 → 25 on 2026-05-18: 4 Tier C pattern dims (Fabio + Flowhorse audit)
#   25 → 27 on 2026-05-18: 2 approach-aligned dims that bake the
#                          OF×approach interaction into a directional
#                          cont/rev signal the audit metric can read:
#     25 vsa_aligned       = vsa_absorption × sign(approach_direction)
#     26 stop_run_aligned  = stop_run_detected × sign(approach_direction)
#   Sign convention for both: +x = pattern favors CONT trade,
#                              -x = pattern favors REV trade.
_N_FEATURES = 27

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
    approach_direction: str | None = None,
) -> np.ndarray:
    """Extract 27 orderflow features from recent 1-minute candles.

    Feature layout (indices 0-26):
      0  delta_pct          — delta / volume of most recent candle (%)
      1  delta              — raw delta of last candle, normalised by avg volume
      2  cvd                — cumulative delta over lookback, normalised by avg volume
      3  cvd_trend          — -1 falling / 0 flat / 1 rising
      4  volume_ratio       — last candle volume / avg volume
      5  body_ratio         — body / spread of last candle
      6  spread_ticks       — last candle spread in ticks (capped at 50)
      7  passive_active_ratio — from signals (capped at 5)
      8  imbalance_density    — SIGNED density: +x = buy-side cluster, -x = sell-side
      9  stacked_imbalance_count — consecutive stacked levels (capped at 10)
     10  stacked_direction   — -1 sell / 0 neutral / 1 buy
     11  big_trades_count    — number of big-volume candles (capped at 10)
     12  big_trades_net_delta — net delta of big trades, normalised by avg volume
     13  vsa_absorption      — 0/1 bool (relaxed thresholds 2026-05-18)
     14  stop_run_detected   — 0/1 bool (relaxed thresholds + 3-bar variant 2026-05-18)
     -- temporal dynamics --
     15  delta_acceleration  — delta change rate (last 3 vs prev 3 candles)
     16  absorption_strength — high volume + narrow body over last 3 candles (0-1)
     17  initiative_momentum — delta * body_ratio of last candle (strong = high both)
     18  volume_climax       — SIGNED capitulation spike: +1 buy / -1 sell / 0 none
     19  delta_divergence    — multi-bar cum-delta divergence (signal path fixed 2026-05-18)
     20  flow_shift          — SIGNED passive→initiative transition magnitude
     -- Tier C (2026-05-18 methodology gap fill) --
     21  two_way_battle      — high vol + ~zero delta + price reversal in candle (0-1)
     22  failed_auction_reabsorption — broke prior 5-bar range, came back, vol rose on return
     23  close_position_in_range — signed: where last candle closed in its H-L range (hammer/star)
     24  initiative_follow_through — last bar direction * volume_ratio (cont confirmation)
     -- Approach-aligned interactions (2026-05-18, OF×approach baked in) --
     25  vsa_aligned        — vsa_absorption × sign(approach); +x=CONT, -x=REV
     26  stop_run_aligned   — stop_run_detected × sign(approach); +x=CONT, -x=REV

    Per Fabio methodology, absorption / stop-run at a level predicts
    cont vs rev OUTCOME depending on approach direction:
      bull pattern + UP approach   → CONT (price through level)
      bull pattern + DOWN approach → REV (bounce off support)
      bear pattern + UP approach   → REV (rejection at resistance)
      bear pattern + DOWN approach → CONT (price through level)
    Dims 25-26 compute this interaction so the audit's per-dim metric
    can see the directional bias. The model can also re-derive it
    from dims 13 + 14 + the approach_direction dim in trigger obs.

    When l1_snapshot is provided, dims 6 (spread_ticks) and 7
    (passive_active_ratio) are recomputed from true bid/ask + Lee-Ready
    aggressor classification instead of the candle-derived approximations.
    Other dims are unchanged. Backward-compatible: l1_snapshot=None
    preserves legacy candle-only behaviour.

    Returns zeros(27) if candles is empty — but the L1 override still
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
        # Diagonal imbalance density (SIGNED — 2026-05-18 PROFILE follow-up).
        # Was: unsigned density (Q4 = chop, STOP predictor only).
        # Now: density × sign(last.imbalance_direction) → +x = buy-side
        # cluster, -x = sell-side cluster, 0 = balanced/two-way.
        # Per Fabio: imbalance cluster IN the breakout direction confirms
        # continuation; mixed cluster = chop. Direction-agnostic count
        # conflates the two; signed density separates them.
        imbalance_max = min(signals.stacked_imbalance_count / 10.0, 1.0)
        if candles:
            _last_cf = candles[-1]
            _n_levels = len(getattr(_last_cf, "price_levels", [])) or 1
            _n_diags = len(getattr(_last_cf, "diagonal_imbalances", []))
            _density = min(_n_diags / max(_n_levels - 1, 1), 1.0)
            _dir = {"buy": 1.0, "sell": -1.0}.get(getattr(_last_cf, "imbalance_direction", "neutral"), 0.0)
            imbalance_max = _density * _dir
        stacked_count = min(signals.stacked_imbalance_count, 10.0) / 10.0
        stacked_dir = {"buy": 1.0, "neutral": 0.0, "sell": -1.0}.get(signals.stacked_imbalance_direction, 0.0)
        big_count = min(signals.big_trades_count, 10.0) / 10.0
        big_net = signals.big_trades_net_delta / max(avg_vol, 1.0)
        # vsa_absorption SIGNED 2026-05-18: per Fabio + Flowhorse, the
        # direction is inherent in the pattern — close near high =
        # buyers absorbed sellers (bull rev coming); close near low =
        # sellers absorbed buyers (bear rev coming). Previously stored
        # as 0/1, throwing away the direction. Now ±1/0.
        vsa_abs = 0.0
        if signals.vsa_absorption:
            _last_range = max(last.high - last.low, 1e-6)
            _range_pos = (last.close - last.low) / _last_range
            if _range_pos > 0.7:
                vsa_abs = 1.0  # buyers absorbed (bullish)
            elif _range_pos < 0.3:
                vsa_abs = -1.0  # sellers absorbed (bearish)

        # stop_run_detected SIGNED 2026-05-18: bull stop run = swept
        # below prior_low + reclaim up = predict price UP. Bear stop
        # run = swept above prior_high + reclaim down = predict price
        # DOWN. Direction inherent in the pattern, was stored as 0/1.
        stop_run = 0.0
        if signals.stop_run_detected and len(recent) >= 2:
            _spike = recent[-2]
            _reversal = recent[-1]
            if _reversal.close > _spike.close:
                stop_run = 1.0  # bull stop run (low swept, reclaimed up)
            elif _reversal.close < _spike.close:
                stop_run = -1.0  # bear stop run (high swept, reclaimed down)
    else:
        # Derive from raw candle data
        total_vol_sum = sum(c.volume for c in recent)
        total_abs_delta = sum(abs(c.delta) for c in recent)
        passive_active_raw = (total_vol_sum - total_abs_delta) / max(1, total_abs_delta)
        passive_active = min(passive_active_raw, 5.0) / 5.0

        _n_levels = len(getattr(last, "price_levels", [])) or 1
        _n_diags = len(getattr(last, "diagonal_imbalances", []))
        _density = min(_n_diags / max(_n_levels - 1, 1), 1.0)
        # Signed: +x = buy-side cluster, -x = sell-side, 0 = balanced.
        # See signal-path comment above (2026-05-18 PROFILE follow-up).
        _dir = {"buy": 1.0, "sell": -1.0}.get(getattr(last, "imbalance_direction", "neutral"), 0.0)
        imbalance_max = _density * _dir

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

        # vsa_absorption (no-signals fallback) — mirrors signal path,
        # SIGNED 2026-05-18.
        vsa_abs = 0.0
        if last.volume > avg_vol * 1.3 and last.body_ratio < 0.4:
            _last_range = max(last.high - last.low, 1e-6)
            _range_pos = (last.close - last.low) / _last_range
            if _range_pos > 0.7:
                vsa_abs = 1.0  # buyers absorbed
            elif _range_pos < 0.3:
                vsa_abs = -1.0  # sellers absorbed

        # stop_run_detected (no-signals fallback) — re-derive direction
        # from the spike + reversal pattern. Same logic as orderflow.py
        # signal computation, just here for the no-signals path.
        stop_run = 0.0
        if len(recent) >= 4:
            prior = recent[:-2]
            spike = recent[-2]
            reversal = recent[-1]
            prior_high = max(c.high for c in prior)
            prior_low = min(c.low for c in prior)
            prior_avg_v = sum(c.volume for c in prior) / max(len(prior), 1)
            if spike.volume > prior_avg_v * 1.3 and reversal.body_ratio > 0.3:
                if spike.low < prior_low and reversal.close > prior_low and reversal.close > spike.close:
                    stop_run = 1.0  # bull stop run
                elif spike.high > prior_high and reversal.close < prior_high and reversal.close < spike.close:
                    stop_run = -1.0  # bear stop run

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

    # 18: volume_climax — repurposed 2026-05-18 from "last vs max vol
    # ratio" (100% nz, neutral) to a SIGNED capitulation spike detector.
    # ITERATION (post-Phase-1 audit): first version (vol > 3×, |delta_pct|
    # > 0.7, body < 0.5) fired 0% on real data — body<0.5 was the killer.
    # Real capitulation candles often have substantial body (the spike IS
    # the move). Dropped the body condition entirely. The signature is
    # the volume-delta-asymmetry, not the body shape.
    #   1. Last bar volume > 2.5× prior_avg (massive thrust — was 3×)
    #   2. Last bar |delta_pct| > 0.65 (one-sided — was 0.7)
    # Sign: +1.0 = buy-side spike (positive delta), -1.0 = sell-side.
    vol_climax = 0.0
    if len(recent) >= 5:
        prior_for_spike = recent[:-1]
        prior_avg_vol_spike = sum(c.volume for c in prior_for_spike) / max(len(prior_for_spike), 1)
        if last.volume > prior_avg_vol_spike * 2.5 and abs(delta_pct) > 0.65:
            vol_climax = 1.0 if last.delta > 0 else -1.0

    # 19: delta_divergence — classic multi-bar divergence where price
    # makes a new extreme but CUMULATIVE delta fails to confirm.
    # Bug fixed 2026-05-18 (audit_gbt_orderflow):
    #   Old definition used abs(delta) < abs(sum/4) which checks
    #   MAGNITUDE weakening, not DIRECTIONAL divergence. Fired on noise.
    #   New definition uses cumulative delta vs price direction —
    #   bull div = price higher-high + cum-delta NOT making higher-high,
    #   bear div = price lower-low + cum-delta NOT making lower-low.
    # ITERATION (post-Phase-1 audit): the binary 0/1 version fires 47%
    # but is neutral on R outcomes. The methodology says div is a SETUP
    # signal, not directional alone. Making it SIGNED so the model can
    # see bull vs bear separately (bull div at a low predicts rev UP,
    # bear div at a high predicts rev DOWN).
    delta_div = 0.0
    if len(recent) >= 5:
        prices_recent = [c.close for c in recent[-5:]]
        deltas_cum = np.cumsum([c.delta for c in recent[-5:]])
        price_higher_high = prices_recent[-1] > max(prices_recent[:-1])
        delta_lower_high = deltas_cum[-1] < max(deltas_cum[:-1])
        price_lower_low = prices_recent[-1] < min(prices_recent[:-1])
        delta_higher_low = deltas_cum[-1] > min(deltas_cum[:-1])
        if price_higher_high and delta_lower_high:
            delta_div = +1.0  # bull div (price made new HH, cum-delta failed)
        elif price_lower_low and delta_higher_low:
            delta_div = -1.0  # bear div (price made new LL, cum-delta failed)

    # 20: flow_shift — repurposed 2026-05-18 (PROFILE follow-up) from
    # binary sign-change (fired 47%, neutral) to a SIGNED passive→initiative
    # transition magnitude. Per Flowhorse, the highest-conviction reversal
    # trigger is: 3 bars of absorption (high vol + low body, one side
    # passively defending) followed by 1 bar of initiative aggression in
    # the OPPOSITE direction of the absorbed side.
    #
    # Detection (relaxed post-Phase-1 audit; prior thresholds fired only
    # 0.2% which is too rare for the model to learn from):
    #   - prior 3 bars: avg body_ratio < 0.5 (was 0.4) AND avg vol > avg_vol
    #   - last bar:     body_ratio > 0.4 (was 0.5) AND |delta_pct| > 0.4 (was 0.5)
    #   - sign:         +1.0 = passive sellers absorbed → buyers initiate
    #                   -1.0 = passive buyers absorbed → sellers initiate
    #                   0    = no pattern
    # Magnitude: scaled by last bar's body_ratio × |delta_pct| (0..1).
    flow_shift = 0.0
    if len(recent) >= 4:
        absorption_bars = recent[-4:-1]
        avg_body_absorb = sum(c.body_ratio for c in absorption_bars) / 3
        avg_vol_absorb = sum(c.volume for c in absorption_bars) / 3
        if (
            avg_body_absorb < 0.5
            and avg_vol_absorb > avg_vol  # high-vol absorption
            and last.body_ratio > 0.4
            and abs(delta_pct) > 0.4
        ):
            magnitude = float(np.clip(last.body_ratio * abs(delta_pct), 0.0, 1.0))
            flow_shift = magnitude if last.delta > 0 else -magnitude

    # ─── Tier C (NEW 2026-05-18 PROFILE follow-up) ──────────────────────
    # Four new pattern dims that fill the gap between our existing OF
    # primitives and the Fabio + Flowhorse methodology. Each one captures
    # a specific rev/cont signature that no single existing dim reads.

    # 21: two_way_battle — both sides equally aggressive at the same
    # price level. Per Flowhorse: "two-way trade = no edge, stay out."
    # Signature: high volume (>1.5× avg) + near-zero |delta_pct| (<0.15)
    # + non-trivial range (body_ratio < 0.6 = price churned but didn't
    # commit). Returns 0..1 magnitude.
    two_way_battle = 0.0
    if last.volume > avg_vol * 1.5 and abs(delta_pct) < 0.15 and last.body_ratio < 0.6:
        vol_intensity = min((last.volume / max(avg_vol, 1.0)) / 3.0, 1.0)
        balance = 1.0 - min(abs(delta_pct) / 0.15, 1.0)
        two_way_battle = vol_intensity * balance

    # 22: failed_auction_reabsorption — Fabio's #1 reversal setup.
    # Price broke OUTSIDE the prior 5-bar range, came back INSIDE within
    # 2 bars, with INCREASING volume on the return leg.
    # Signed: +1.0 = bull reversal (broke down + came back up) →
    # likely move higher; -1.0 = bear reversal (broke up + came back
    # down) → likely move lower.
    failed_auction = 0.0
    if len(recent) >= 7:
        prior_window = recent[-7:-2]  # 5 bars before the potential break
        prior_high = max(c.high for c in prior_window)
        prior_low = min(c.low for c in prior_window)
        break_bar = recent[-2]
        return_bar = recent[-1]
        # Bull reversal: break_bar's low went below prior_low, return_bar closed back above
        if break_bar.low < prior_low and return_bar.close > prior_low and return_bar.volume > break_bar.volume:
            failed_auction = 1.0
        # Bear reversal: mirror
        elif break_bar.high > prior_high and return_bar.close < prior_high and return_bar.volume > break_bar.volume:
            failed_auction = -1.0

    # 23: close_position_in_range — where the LAST candle's close landed
    # within its own H-L range, signed. Captures hammer (long lower wick,
    # close near high) and shooting-star (long upper wick, close near
    # low) patterns. Per Flowhorse: these candles are the absorption
    # signature on a single bar.
    # Range:
    #   +1.0 = close at the HIGH (perfect hammer — rejection from below)
    #   -1.0 = close at the LOW (perfect shooting star — rejection from above)
    #    0.0 = close at midpoint (no rejection bias)
    last_range = max(last.high - last.low, 1e-6)
    close_pos_raw = (last.close - last.low) / last_range  # 0..1
    close_position = (close_pos_raw - 0.5) * 2.0  # -1..+1
    # Only meaningful if the candle has a meaningful range (not flat).
    if last_range < TICK_SIZE * 0.5:
        close_position = 0.0

    # 24: initiative_follow_through — continuation confirmation. If the
    # PREVIOUS bar was a strong directional thrust (high body + high
    # |delta|), the LAST bar's same-direction delta × volume_ratio
    # measures whether the move is being followed (continuation valid)
    # or has dried up (continuation likely failed). Per Fabio: "what
    # happens to volume when we break out? Dry up = bad. Sustained = go."
    # Signed: +x = bullish follow-through, -x = bearish, 0 = no trigger
    # or no follow-through.
    # Relaxed post-Phase-1 audit: prior thresholds (trigger.body_ratio
    # > 0.6, |trigger_delta_pct| > 0.5) fired only 0.9% — the trigger
    # qualification was too strict. When it fires it gives +9.6pt CONT
    # directional bias — strongest signal in stack — so getting more
    # samples is the priority.
    initiative_followup = 0.0
    if len(recent) >= 2:
        trigger = recent[-2]
        if trigger.body_ratio > 0.5:
            trigger_delta_pct = trigger.delta / max(trigger.volume, 1)
            if abs(trigger_delta_pct) > 0.4:
                # Trigger was a strong thrust. Now measure follow-through.
                same_dir = (trigger.delta > 0 and last.delta > 0) or (trigger.delta < 0 and last.delta < 0)
                if same_dir:
                    follow_vol_ratio = min(last.volume / max(avg_vol, 1.0), 3.0) / 3.0
                    follow_body = last.body_ratio
                    magnitude = float(np.clip(follow_vol_ratio * follow_body, 0.0, 1.0))
                    initiative_followup = magnitude if trigger.delta > 0 else -magnitude

    # ─── Approach-aligned interactions (2026-05-18) ─────────────────────
    # vsa_absorption / stop_run_detected describe ABSOLUTE direction
    # (bull/bear). cont/rev OUTCOMES depend on approach_direction.
    # Bake the OF×approach interaction so the audit's per-dim metric
    # can see directional bias (the model can also re-derive it from
    # the raw dims + approach_direction at trig idx 110, but pre-
    # computing it lets the audit verify the methodology directly).
    #
    # Sign convention:
    #   approach=UP   + bull pattern → CONT win (price through resistance) → +1
    #   approach=UP   + bear pattern → REV win  (rejection at resistance)  → -1
    #   approach=DOWN + bull pattern → REV win  (bounce off support)       → -1
    #   approach=DOWN + bear pattern → CONT win (price through support)    → +1
    # Which simplifies to: aligned = pattern × approach_sign, where
    # approach_sign = +1 for "up" and -1 for "down".
    if approach_direction == "down":
        _approach_sign = -1.0
    elif approach_direction == "up":
        _approach_sign = 1.0
    else:
        _approach_sign = 0.0  # unknown / fallback — zeros out the aligned dims
    vsa_aligned = float(vsa_abs) * _approach_sign
    stop_run_aligned = float(stop_run) * _approach_sign

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
            vsa_abs,
            stop_run,
            float(delta_accel),
            float(absorption_str),
            float(init_momentum),
            float(vol_climax),
            float(delta_div),
            float(flow_shift),
            float(two_way_battle),
            float(failed_auction),
            float(close_position),
            float(initiative_followup),
            float(vsa_aligned),
            float(stop_run_aligned),
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
