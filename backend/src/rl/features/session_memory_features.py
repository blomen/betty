"""Session-memory features — stateful regime-awareness for the RL observation.

The trained heads see each touch independently unless we expose HOW THE SESSION
IS GOING as explicit features. Without these the model can't learn "5 losses in
a row on daily-only zones → hostile regime, size down / skip" the way a real
trader does.

This segment feeds the AUGMENTED observation (alongside position_state and
gbt_forecast). Values are computed chronologically per session at training
time and at inference time from the live SessionState snapshot. The two
must produce the same distribution, which is why this module is the single
source of truth — both `_simulate_session_memory` (training) and
`extract_session_memory_live` (inference) normalise the same way.

Layout (6 dims, all in [-1, 1] or [0, 1]):
  0  rolling_5_win_rate       ∈ [0, 1]   — last 5 trades' win rate (0.5 if <5)
  1  rolling_5_avg_R          ∈ [-1, 1]  — last 5 trades' avg R, clipped
  2  session_dd_from_peak_R   ∈ [-1, 0]  — distance from peak in R/100, clipped
  3  consec_loss_streak_norm  ∈ [0, 1]   — min(consec_losses / 10, 1)
  4  session_trade_count_norm ∈ [0, 1]   — min(trades_taken / 100, 1)
  5  recent_R_volatility_norm ∈ [0, 1]   — std of last 5 R / 2, clipped

Phase 3c addition — replaces the reactive circuit-breaker approach with
learned, context-aware behaviour. The model decides how to act on session
state; Phase 2 SessionState gate stays as a final safety net.
"""

from __future__ import annotations

from collections import deque

import numpy as np

SESSION_MEMORY_DIM: int = 6


def _normalise(
    rolling_win_rate: float,
    rolling_avg_R: float,
    session_R: float,
    peak_session_R: float,
    consec_losses: int,
    trades_taken: int,
    recent_R_std: float,
) -> np.ndarray:
    """Map raw session statistics to the 6-dim feature vector.

    Separated so training and inference paths stay literally identical.
    """
    dd_from_peak = session_R - peak_session_R  # <= 0
    return np.array(
        [
            float(np.clip(rolling_win_rate, 0.0, 1.0)),
            float(np.clip(rolling_avg_R, -1.0, 1.0)),
            float(np.clip(dd_from_peak / 100.0, -1.0, 0.0)),
            float(min(consec_losses / 10.0, 1.0)),
            float(min(trades_taken / 100.0, 1.0)),
            float(np.clip(recent_R_std / 2.0, 0.0, 1.0)),
        ],
        dtype=np.float32,
    )


def extract_session_memory_live(
    recent_outcomes: list[float],
    session_R: float,
    peak_session_R: float,
    consecutive_losses: int,
    trades_taken: int,
    rolling_window: int = 5,
) -> np.ndarray:
    """Build the 6-dim session_memory vector at inference time.

    Caller passes the live SessionState's recent outcomes + session tallies.
    Matches the normalisation of _simulate_session_memory exactly so the
    trained heads see the same distribution they trained on.
    """
    window = recent_outcomes[-rolling_window:] if recent_outcomes else []
    if len(window) >= rolling_window:
        rolling_win_rate = float(sum(1 for r in window if r > 0) / len(window))
        rolling_avg_R = float(np.mean(window))
        recent_R_std = float(np.std(window))
    else:
        # Not enough data → neutral defaults (matches training "warmup" state)
        rolling_win_rate = 0.5
        rolling_avg_R = 0.0
        recent_R_std = 0.0

    return _normalise(
        rolling_win_rate=rolling_win_rate,
        rolling_avg_R=rolling_avg_R,
        session_R=session_R,
        peak_session_R=peak_session_R,
        consec_losses=consecutive_losses,
        trades_taken=trades_taken,
        recent_R_std=recent_R_std,
    )


def simulate_session_memory(
    touch_epochs: np.ndarray,
    rewards_cont: np.ndarray,
    rewards_rev: np.ndarray,
    session_gap_s: float = 3600.0,
    rolling_window: int = 5,
    actions: np.ndarray | None = None,
) -> np.ndarray:
    """Chronological per-session walk producing an (N, SESSION_MEMORY_DIM) array.

    Per-session reset on ET day change or >1h gap. Used at replay time to
    produce session_memory features; the trained heads then see realistic
    session-progression features during training.

    Action semantics:
      - If `actions` is provided (0=CONT, 1=REV, 2=SKIP): use TriggerGBT's
        chosen side — `rewards_cont[i]` if action=0, `rewards_rev[i]` if
        action=1, skip if 2. Session memory reflects the REALISTIC live
        policy: runs of losses, drawdowns, etc. REQUIRED for the heads
        to learn hostile-regime behaviour from session context (H4 parity).
      - If `actions` is None (legacy): greedy best-action assumption
        `max(rc, rr, 0)`. Same as position_state sim — but it NEVER
        accumulates losses, so session_memory features look sanitised.
        Only use this when TriggerGBT isn't yet trained (first pass).

    The 5-trade rolling window is deliberately short — the model needs to
    react to regime shifts within 5-10 trades, not 20. (Apr-8 collapsed
    across 30 trades; rolling_5 catches it by trade 6-8.)
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
    n = len(touch_epochs)
    out = np.zeros((n, SESSION_MEMORY_DIM), dtype=np.float32)
    if n == 0:
        return out

    order = np.argsort(touch_epochs)

    # Per-session trackers
    recent: deque = deque(maxlen=rolling_window)
    session_R = 0.0
    peak_session_R = 0.0
    consec_losses = 0
    trades_taken = 0
    last_ts = 0.0
    last_date = None

    for idx in order:
        ts = float(touch_epochs[idx])
        if ts <= 0:
            continue
        dt_et = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_ET).date()
        # Session boundary: different ET date OR >1h gap
        if last_date is None or dt_et != last_date or (ts - last_ts) > session_gap_s:
            recent.clear()
            session_R = 0.0
            peak_session_R = 0.0
            consec_losses = 0
            trades_taken = 0
        last_ts = ts
        last_date = dt_et

        # Observation features reflect state BEFORE this touch resolves
        if recent:
            win_rate = sum(1 for r in recent if r > 0) / len(recent)
            avg_R = float(np.mean(list(recent)))
            r_std = float(np.std(list(recent))) if len(recent) > 1 else 0.0
        else:
            win_rate = 0.5
            avg_R = 0.0
            r_std = 0.0

        out[idx] = _normalise(
            rolling_win_rate=win_rate,
            rolling_avg_R=avg_R,
            session_R=session_R,
            peak_session_R=peak_session_R,
            consec_losses=consec_losses,
            trades_taken=trades_taken,
            recent_R_std=r_std,
        )

        # Resolve action + update trackers for NEXT touch.
        # Action-conditioned (H4 parity) when `actions` is provided; else
        # fall back to greedy best-of {cont, rev, skip=0}.
        rc = float(rewards_cont[idx])
        rr = float(rewards_rev[idx])
        if actions is not None:
            a = int(actions[idx])
            if a == 2:  # SKIP
                continue
            realised = rc if a == 0 else rr  # 0=CONT, 1=REV
        else:
            realised = max(rc, rr, 0.0)  # greedy — skips negative outcomes
            if realised == 0.0:
                continue
        realised = float(realised)
        trades_taken += 1
        session_R += realised
        peak_session_R = max(peak_session_R, session_R)
        recent.append(realised)
        if realised <= 0.0:
            consec_losses += 1
        else:
            consec_losses = 0

    return out
