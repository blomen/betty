"""Smoke test for the 3 PROFILE-audit fixes (2026-05-18).

Validates without needing server data / real ticks that:

  1. FVG/OB midpoints enter `zone.composition` via the patched
     ReplayEngine._rebuild_active_levels (zone_composition wiring bug).

  2. Prior period H/L fallback emits weekly/monthly swing levels when the
     swing engine produced none (dead-swing-dims bug).

  3. The 4 new passthrough TPO indices (126, 132, 144, 145) select the
     correct positions in a 276-dim base obs (TPO gap bug).

Exit 0 on success, exit 1 on any failed check. Run before kicking off the
full April re-replay + retrain cycle.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python backend/scripts/smoke_audit_fixes.py` from worktree root.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import numpy as np

from src.market_data.levels import FairValueGap, OrderBlock, compute_multi_tf_swings
from src.rl.config import LevelType
from src.rl.data.replay_engine import ReplayEngine
from src.rl.features.passthrough_features import (
    _PASSTHROUGH_INDICES,
    PASSTHROUGH_DIM,
    PASSTHROUGH_NAMES,
    extract_passthrough,
)
from src.rl.features.trigger_features import TRIGGER_DIM
from src.rl.zone_builder import build_zones

_FAILURES: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{('  — ' + detail) if detail else ''}")
    if not ok:
        _FAILURES.append(name)


# ---------------------------------------------------------------------------
# Check 1: FVG/OB → zone.composition
# ---------------------------------------------------------------------------


def smoke_fvg_ob_composition() -> None:
    print("\n[1/3] FVG/OB enter zone.composition via replay_engine fix")

    engine = ReplayEngine()
    engine._reset()
    engine._fvgs = [FairValueGap(price_low=18995.0, price_high=19005.0, direction="bullish")]
    engine._order_blocks = [
        OrderBlock(price_low=19002.0, price_high=19008.0, direction="bearish", volume=100),
    ]
    engine._session_levels.pdh = 19000.0
    engine._session_levels.pdl = 18980.0

    engine._rebuild_active_levels()
    levels = engine._active_levels

    fvg_emitted = any(lt == LevelType.FVG_BULL for _, lt, _ in levels)
    ob_emitted = any(lt == LevelType.ORDER_BLOCK_BEAR for _, lt, _ in levels)
    _check("replay_engine emits FVG_BULL into active_levels", fvg_emitted)
    _check("replay_engine emits ORDER_BLOCK_BEAR into active_levels", ob_emitted)

    zones = build_zones(engine._active_levels, session_atr=40.0)
    lt_list = list(LevelType)
    fvg_bull_idx = lt_list.index(LevelType.FVG_BULL)
    ob_bear_idx = lt_list.index(LevelType.ORDER_BLOCK_BEAR)

    any_fvg_set = any(z.composition[fvg_bull_idx] == 1.0 for z in zones)
    any_ob_set = any(z.composition[ob_bear_idx] == 1.0 for z in zones)
    _check(
        f"zone.composition[FVG_BULL idx={fvg_bull_idx}] fires in at least one zone",
        any_fvg_set,
        detail=f"{len(zones)} zones built",
    )
    _check(
        f"zone.composition[ORDER_BLOCK_BEAR idx={ob_bear_idx}] fires in at least one zone",
        any_ob_set,
    )


# ---------------------------------------------------------------------------
# Check 2: prior_high/prior_low swing fallback
# ---------------------------------------------------------------------------


def smoke_swing_fallback() -> None:
    print("\n[2/3] Weekly/monthly swing prior_high/low fallback")

    from datetime import datetime, timezone

    synth_bars = []
    for month, (hi, lo) in enumerate([(19000, 18900), (19200, 19000), (19400, 19150)], start=1):
        ts = datetime(2026, month, 15, 12, 0, tzinfo=timezone.utc)
        synth_bars.append({"ts": ts, "open": (hi + lo) / 2, "high": hi, "low": lo, "close": (hi + lo) / 2})

    swing = compute_multi_tf_swings(synth_bars)
    _check(
        "monthly.swing_highs empty when only 3 monthly candles (engine cannot run)",
        len(swing.monthly.swing_highs) == 0,
    )
    _check(
        "monthly.prior_high populated from second-to-last candle (was None pre-fix)",
        swing.monthly.prior_high is not None,
        detail=f"prior_high={swing.monthly.prior_high}",
    )
    _check(
        "monthly.prior_low populated (was None pre-fix)",
        swing.monthly.prior_low is not None,
        detail=f"prior_low={swing.monthly.prior_low}",
    )

    engine = ReplayEngine()
    engine._reset()
    engine._precomputed = {"swing_structure": swing}
    engine._session_levels.pdh = 19200.0
    engine._rebuild_active_levels()
    levels = engine._active_levels

    monthly_high_emitted = any(lt == LevelType.MONTHLY_SWING_HIGH for _, lt, _ in levels)
    monthly_low_emitted = any(lt == LevelType.MONTHLY_SWING_LOW for _, lt, _ in levels)
    _check("replay_engine emits MONTHLY_SWING_HIGH via prior_high fallback", monthly_high_emitted)
    _check("replay_engine emits MONTHLY_SWING_LOW via prior_low fallback", monthly_low_emitted)


# ---------------------------------------------------------------------------
# Check 3: PASSTHROUGH indices select the correct TPO positions
# ---------------------------------------------------------------------------


def smoke_passthrough_indices() -> None:
    print("\n[3/3] Passthrough selects correct 4 new TPO indices")

    _check("PASSTHROUGH_DIM == 14", PASSTHROUGH_DIM == 14, detail=f"actual={PASSTHROUGH_DIM}")
    _check("TRIGGER_DIM == 122", TRIGGER_DIM == 122, detail=f"actual={TRIGGER_DIM}")
    _check(
        "_PASSTHROUGH_INDICES length matches PASSTHROUGH_DIM",
        len(_PASSTHROUGH_INDICES) == PASSTHROUGH_DIM,
    )

    base = np.arange(302, dtype=np.float32)
    out = extract_passthrough(base)
    _check("extract_passthrough output shape == (14,)", out.shape == (14,))

    expected = np.array(_PASSTHROUGH_INDICES, dtype=np.float32)
    matches = np.array_equal(out, expected)
    _check(
        "extract_passthrough returns values at the declared indices",
        matches,
        detail=f"got {out.tolist()} expected {expected.tolist()}" if not matches else "",
    )

    audit_dims = {
        "tpo_tokyo_opening_direction": 126,
        "tpo_london_ib_range": 132,
        "tpo_ny_ib_range": 144,
        "tpo_ny_price_vs_ib_mid": 145,
    }
    for name, expected_raw_idx in audit_dims.items():
        pos_in_passthrough = PASSTHROUGH_NAMES.index(name)
        actual_raw_idx = _PASSTHROUGH_INDICES[pos_in_passthrough]
        _check(
            f"{name} points to raw obs index {expected_raw_idx}",
            actual_raw_idx == expected_raw_idx,
            detail=f"got {actual_raw_idx}",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 80)
    print("PROFILE-audit fix smoke test")
    print("=" * 80)

    smoke_fvg_ob_composition()
    smoke_swing_fallback()
    smoke_passthrough_indices()

    print("\n" + "=" * 80)
    if _FAILURES:
        print(f"FAILED — {len(_FAILURES)} check(s) failed:")
        for f in _FAILURES:
            print(f"  - {f}")
        print("\nDo NOT kick off the April re-replay + retrain until these pass.")
        return 1
    print("OK — all checks passed. Safe to proceed with April re-replay + retrain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
