"""Exit-signals: framework-style reversal detection while in an open position.

"Let winners ride" only works if we have a clear definition of when to get
out. The framework says exit when the tape ACTIVELY turns against the trade,
not when the stop happens to get touched by noise. This module reads the
base observation + current trade direction and counts framework reversal
confirmations. The live caller decides how many simultaneous signals trigger
an exit (typically 2 of 4 is the safety threshold).

The four signals (direct framework quotes):
  1. CVD flip          — cumulative-delta slope reverses against trade dir
                         with meaningful magnitude
  2. Absorption at target — high-vol + narrow-body bars stacking near where
                         price is trying to extend
  3. Stacked imbalance flip — imbalance direction flips signed against
                         our trade direction
  4. Big trades against — aggression (large-size prints) accelerating in
                         the opposite direction

Each is a boolean (fired / didn't). Threshold for exit is the caller's
call — safe default is 2 of 4 before closing position.

Inputs are read from the 302-dim base observation; index constants map
directly to the feature_names.py layout (orderflow[...] at obs[31:52],
micro[...] at obs[248:268]).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# --- Base-observation indices --------------------------------------------
# orderflow[] segment starts at 31, is 21 dims (see registry.BASE_OBSERVATION_SCHEMA)
_OF_OFFSET = 31

# micro[] segment starts at 248, is 20 dims
_MICRO_OFFSET = 248

# orderflow subfeature offsets (see features/feature_names.py _SUB_FEATURES)
_OF_CVD_SLOPE = _OF_OFFSET + 4  # orderflow[4]
_OF_STACKED_IMB_SIGNED = _OF_OFFSET + 13  # orderflow[13]
_OF_STACKED_IMB_FLIP = _OF_OFFSET + 15  # orderflow[15]
_OF_ABSORPTION_COUNT = _OF_OFFSET + 16  # orderflow[16]
_OF_ABSORPTION_STRENGTH = _OF_OFFSET + 17  # orderflow[17]
_OF_BIG_TRADE_COUNT = _OF_OFFSET + 9  # orderflow[9] — signed via CVD context

# micro subfeature offsets
_MICRO_BIG_TRADE_COUNT = _MICRO_OFFSET + 10  # micro[10]
_MICRO_LAST5_DELTA = _MICRO_OFFSET + 12  # micro[12]
_MICRO_ACCEL_SIGN_FLIP = _MICRO_OFFSET + 18  # micro[18]

# Thresholds — tuned conservative so signals only fire on clearly meaningful tape
_CVD_FLIP_MAGNITUDE: float = 0.3
_ABSORPTION_COUNT_STRONG: float = 0.4  # normalized — means ≥2 absorption bars lately
_IMBALANCE_FLIP_MAGNITUDE: float = 0.2
_BIG_TRADE_ACCEL_THRESHOLD: float = 0.4


@dataclass(frozen=True)
class ExitSignals:
    """Container for reversal signal detection.

    `fired_count` is the total of the 4 boolean signals. `details` exposes
    each one individually for UI / logging so live operators can see which
    signals drove an exit.
    """

    cvd_flip: bool
    absorption_at_target: bool
    imbalance_flip: bool
    big_trades_against: bool
    fired_count: int
    details: dict

    @property
    def should_exit_at_threshold(self) -> dict:
        """Convenience: which caller thresholds (1..4) would trigger exit."""
        return {
            "1_signal": self.fired_count >= 1,
            "2_signals": self.fired_count >= 2,  # recommended default
            "3_signals": self.fired_count >= 3,
            "4_signals": self.fired_count >= 4,
        }


def count_reversal_signals(obs: np.ndarray, trade_direction: int) -> ExitSignals:
    """Detect framework reversal signals in the current observation.

    Args:
        obs: 302-dim base observation (NOT the augmented 324-dim).
        trade_direction: +1 long, -1 short. 0 → no signals (no open trade).

    Returns an ExitSignals dataclass with per-signal booleans + fired_count.
    When obs is too short or trade_direction is 0, returns all-False.
    """
    if obs is None or len(obs) < 302 or trade_direction == 0:
        return ExitSignals(
            cvd_flip=False,
            absorption_at_target=False,
            imbalance_flip=False,
            big_trades_against=False,
            fired_count=0,
            details={},
        )

    # Feature reads
    cvd_slope = float(obs[_OF_CVD_SLOPE])
    stacked_imb_signed = float(obs[_OF_STACKED_IMB_SIGNED])
    stacked_imb_flip = float(obs[_OF_STACKED_IMB_FLIP])
    absorption_count = float(obs[_OF_ABSORPTION_COUNT])
    absorption_strength = float(obs[_OF_ABSORPTION_STRENGTH])
    big_trade_count_micro = float(obs[_MICRO_BIG_TRADE_COUNT])
    last5_delta_signed = float(obs[_MICRO_LAST5_DELTA])

    # --- 1. CVD flip: slope against trade direction, meaningful magnitude
    cvd_flip = bool((cvd_slope * trade_direction < 0) and abs(cvd_slope) >= _CVD_FLIP_MAGNITUDE)

    # --- 2. Absorption at target: multiple absorption bars with strength
    # Absorption near where price is trying to extend = stalling into resistance
    absorption_at_target = bool(
        absorption_count >= _ABSORPTION_COUNT_STRONG and absorption_strength >= _ABSORPTION_COUNT_STRONG
    )

    # --- 3. Imbalance flips signed against us
    # stacked_imb_signed is signed (+ = buying pressure, - = selling). Against a
    # LONG trade (dir=+1), we want stacked_imb_signed > 0 (supportive). Flip
    # fires when it swings negative with the flip indicator active.
    imbalance_flip = bool(
        (stacked_imb_signed * trade_direction < -_IMBALANCE_FLIP_MAGNITUDE) and abs(stacked_imb_flip) >= 0.1
    )

    # --- 4. Big trades building against us
    # Combine big-trade volume with signed delta in the last 5 ticks. If lots of
    # large prints AND last5 delta sign is opposed to trade dir → aggression flip.
    big_trades_against = bool(
        big_trade_count_micro >= _BIG_TRADE_ACCEL_THRESHOLD
        and (last5_delta_signed * trade_direction < -_IMBALANCE_FLIP_MAGNITUDE)
    )

    fired_count = int(cvd_flip) + int(absorption_at_target) + int(imbalance_flip) + int(big_trades_against)

    return ExitSignals(
        cvd_flip=cvd_flip,
        absorption_at_target=absorption_at_target,
        imbalance_flip=imbalance_flip,
        big_trades_against=big_trades_against,
        fired_count=fired_count,
        details={
            "cvd_slope": cvd_slope,
            "stacked_imbalance_signed": stacked_imb_signed,
            "stacked_imbalance_flip": stacked_imb_flip,
            "absorption_count": absorption_count,
            "absorption_strength": absorption_strength,
            "big_trade_count_micro": big_trade_count_micro,
            "last5_delta_signed": last5_delta_signed,
            "trade_direction": trade_direction,
        },
    )


def should_exit_on_reversal(
    obs: np.ndarray,
    trade_direction: int,
    min_signals: int = 2,
) -> bool:
    """Convenience: returns True when reversal signals fired >= min_signals.

    Default `min_signals=2` matches the framework's "two confirmations before
    cutting" rule. Set to 1 for aggressive exits, 3 for very conservative.
    """
    signals = count_reversal_signals(obs, trade_direction)
    return signals.fired_count >= min_signals
