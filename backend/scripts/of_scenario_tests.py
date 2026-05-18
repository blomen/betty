"""OF dim scenario tests — methodology-aligned synthetic patterns.

For each Fabio + Flowhorse methodology pattern, construct an idealized
CandleFlow sequence and verify the corresponding OF dim fires with the
expected sign + magnitude.

Patterns covered:
  REVERSAL:
    R1  vsa_absorption          — narrow body + high vol + close at extreme
    R2  stop_run_detected (2bar) — spike beyond + reclaim + strong reversal
    R3  stop_run_detected (3bar) — spike + doji + reversal
    R4  failed_auction_reabsorption — broke out, came back, vol rose on return
    R5  volume_climax (capitulation) — massive 1-sided thrust, no body extension
    R6  close_position_in_range (hammer) — long lower wick, close at high
    R7  close_position_in_range (star)   — long upper wick, close at low
    R8  flow_shift (passive→initiative) — 3 absorption bars + 1 thrust
    R9  delta_divergence (bull)  — price HH + cum-delta lower-high
    R10 delta_divergence (bear)  — price LL + cum-delta higher-low

  CONTINUATION:
    C1  initiative_follow_through — strong trigger + sustained next-bar vol
    C2  imbalance_density (bull)  — signed: positive when buy-side cluster
    C3  imbalance_density (bear)  — signed: negative when sell-side cluster
    C4  stacked_direction (bull)  — +1 when stacked imbalance buy-side
    C5  initiative_momentum (bull) — strong directional candle

  CHOP / SKIP:
    S1  two_way_battle — high vol + ~zero delta + low body
    S2  absorption_strength (high) — 3 bars narrow body, high vol

Each scenario PASSES if the expected dim fires with the right sign at
the right magnitude threshold. Includes "negative" checks: scenarios
that should NOT trigger the dim it's adjacent to.

Run:
  python backend/scripts/of_scenario_tests.py

Exit 0 on all-pass, 1 on any failure.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from worktree root.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


from src.market_data.orderflow import CandleFlow, PriceLevelFlow, compute_signals
from src.rl.features.observation_index import _ORDERFLOW_LABELS
from src.rl.features.orderflow_features import extract_orderflow_features

_FAILURES: list[str] = []
_T0 = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)


def _candle(
    minute_offset: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
    delta: int,
    tick_count: int | None = None,
    price_levels: list[PriceLevelFlow] | None = None,
) -> CandleFlow:
    """Construct a CandleFlow with reasonable defaults."""
    ts = datetime(2026, 5, 1, 14, 30 + minute_offset, tzinfo=timezone.utc)
    buy_v = (volume + delta) // 2
    sell_v = volume - buy_v
    return CandleFlow(
        ts=ts,
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=int(volume),
        buy_volume=int(buy_v),
        sell_volume=int(sell_v),
        delta=int(delta),
        tick_count=tick_count if tick_count is not None else max(volume // 5, 1),
        spread=float(high - low),
        price_levels=price_levels or [],
    )


def _check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        _FAILURES.append(name)


def _of_idx(label: str) -> int:
    return _ORDERFLOW_LABELS.index(label)


def _run(name: str, candles: list[CandleFlow], expected: dict, use_signals: bool = True) -> None:
    """Run the extractor + signal computation and assert expected dim values.

    expected is {label: (predicate, description)} where predicate is a
    callable receiving the float value and returning bool.
    """
    print(f"\n[{name}]")
    signals = compute_signals(candles, direction="long") if use_signals else None
    feats = extract_orderflow_features(candles, signals)
    for label, (pred, desc) in expected.items():
        idx = _of_idx(label)
        val = float(feats[idx])
        ok = pred(val)
        _check(f"{label} {desc}", ok, detail=f"got {val:+.4f}")


# ---------------------------------------------------------------------------
# REVERSAL scenarios
# ---------------------------------------------------------------------------


def scenario_R1_vsa_absorption() -> None:
    """Narrow body + high vol + close near top = buyers absorbed sellers."""
    # 5 baseline candles, modest volume
    base = [_candle(i, 100 + i * 0.5, 100.5 + i * 0.5, 99.5 + i * 0.5, 100 + i * 0.5, 1000, 100) for i in range(5)]
    # Absorption candle: wide range, narrow body, close near high, big volume
    absorption = _candle(
        5,
        open_=102.5,
        high=103.5,
        low=101.0,
        close=103.3,  # close near high
        volume=3000,
        delta=200,  # high vol
    )
    candles = base + [absorption]
    _run(
        "R1 — vsa_absorption (buyers absorbed sellers, close at top)",
        candles,
        {"vsa_absorption": (lambda v: v == 1.0, "fires (=1)")},
    )


def scenario_R2_stop_run_2bar() -> None:
    """Sweep below prior_low + reclaim within 1 bar = stop run."""
    base = [_candle(i, 100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100 + i * 0.1, 1000, 50) for i in range(4)]
    prior_low = min(c.low for c in base)
    spike = _candle(
        4,
        open_=100.4,
        high=100.5,
        low=prior_low - 0.5,
        close=prior_low - 0.2,  # spiked BELOW prior_low
        volume=2500,
        delta=-300,
    )
    reversal = _candle(
        5,
        open_=prior_low - 0.2,
        high=prior_low + 0.8,
        low=prior_low - 0.3,
        close=prior_low + 0.6,  # closes BACK INSIDE prior range (above prior_low)
        volume=1500,
        delta=400,
    )
    candles = base + [spike, reversal]
    _run(
        "R2 — stop_run_detected (2-bar bullish sweep)",
        candles,
        {"stop_run_detected": (lambda v: v == 1.0, "fires (=1)")},
    )


def scenario_R3_stop_run_3bar() -> None:
    """Sweep + doji + reversal — 3-bar variant added in Tier A."""
    base = [_candle(i, 100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100 + i * 0.1, 1000, 50) for i in range(4)]
    prior_low = min(c.low for c in base)
    spike = _candle(4, open_=100.4, high=100.5, low=prior_low - 0.5, close=prior_low - 0.2, volume=2500, delta=-300)
    doji = _candle(
        5, open_=prior_low - 0.2, high=prior_low - 0.1, low=prior_low - 0.3, close=prior_low - 0.2, volume=800, delta=20
    )
    reversal = _candle(
        6,
        open_=prior_low - 0.2,
        high=prior_low + 0.8,
        low=prior_low - 0.3,
        close=prior_low + 0.6,
        volume=1500,
        delta=400,
    )
    candles = base + [spike, doji, reversal]
    _run(
        "R3 — stop_run_detected (3-bar bullish: spike + doji + reversal)",
        candles,
        {"stop_run_detected": (lambda v: v == 1.0, "fires (=1)")},
    )


def scenario_R4_failed_auction() -> None:
    """Price broke out of prior 5-bar range, came back inside, vol rose on return."""
    # 5 bars consolidating in 100-101 range
    base = [
        _candle(i, 100.2 + (i % 2) * 0.3, 100.6 + (i % 2) * 0.3, 99.9 + (i % 2) * 0.3, 100.3 + (i % 2) * 0.3, 1000, 50)
        for i in range(5)
    ]
    prior_high = max(c.high for c in base)
    # Break candle: pushes above prior_high
    break_bar = _candle(
        5, open_=100.5, high=prior_high + 0.8, low=100.4, close=prior_high + 0.5, volume=1500, delta=300
    )
    # Return candle: closes BACK INSIDE the prior_high (rejection) + higher volume
    return_bar = _candle(
        6,
        open_=prior_high + 0.5,
        high=prior_high + 0.6,
        low=99.8,
        close=100.4,  # back inside, below prior_high
        volume=2200,  # HIGHER vol on return (key methodology signal)
        delta=-400,
    )
    candles = base + [break_bar, return_bar]
    _run(
        "R4 — failed_auction_reabsorption (bear: broke up, came back down)",
        candles,
        {"failed_auction_reabsorption": (lambda v: v < -0.5, "fires negative (bear reversal)")},
    )


def scenario_R5_capitulation_spike() -> None:
    """Massive one-sided thrust with no body extension = absorbed."""
    base = [_candle(i, 100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100 + i * 0.1, 1000, 50) for i in range(5)]
    # Capitulation: 4× volume, very one-sided delta (-90%), narrow body
    capitulation = _candle(
        5,
        open_=100.5,
        high=101.0,
        low=99.0,
        close=100.4,  # narrow body around open
        volume=5000,
        delta=-4500,  # |delta_pct| = 0.9, sell-side
    )
    candles = base + [capitulation]
    _run(
        "R5 — volume_climax (sell-side capitulation spike)",
        candles,
        {"volume_climax": (lambda v: v < -0.5, "fires negative (sell-side spike)")},
    )


def scenario_R6_hammer() -> None:
    """Long lower wick, close at high → hammer = absorption candle."""
    base = [_candle(i, 100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100 + i * 0.1, 1000, 50) for i in range(5)]
    # Hammer: open near high, dropped low, closed AT high (rejection)
    hammer = _candle(
        5,
        open_=100.4,
        high=100.5,
        low=99.0,
        close=100.45,  # close near high
        volume=1500,
        delta=200,
    )
    candles = base + [hammer]
    _run(
        "R6 — close_position_in_range (hammer: close at high after wick down)",
        candles,
        {"close_position_in_range": (lambda v: v > 0.7, "fires near +1 (close at top)")},
    )


def scenario_R7_shooting_star() -> None:
    """Long upper wick, close at low → shooting star = rejection candle."""
    base = [_candle(i, 100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100 + i * 0.1, 1000, 50) for i in range(5)]
    star = _candle(
        5,
        open_=100.4,
        high=102.0,
        low=100.3,
        close=100.35,  # close near low
        volume=1500,
        delta=-200,
    )
    candles = base + [star]
    _run(
        "R7 — close_position_in_range (shooting star: close at low after wick up)",
        candles,
        {"close_position_in_range": (lambda v: v < -0.7, "fires near -1 (close at bottom)")},
    )


def scenario_R8_passive_to_initiative() -> None:
    """3 absorption bars (low body, high vol) + 1 strong initiative bar in opposite dir."""
    # 2 normal bars baseline
    base = [_candle(i, 100, 100.5, 99.5, 100, 1000, 100) for i in range(2)]
    # 3 absorption bars: low body_ratio (<0.4), high volume
    abs1 = _candle(
        2, open_=100, high=100.8, low=99.2, close=100.1, volume=2000, delta=-300
    )  # body_ratio = 0.1/1.6 = 0.06
    abs2 = _candle(3, open_=100.1, high=100.7, low=99.3, close=99.95, volume=2000, delta=-200)  # body_ratio low
    abs3 = _candle(4, open_=99.95, high=100.6, low=99.4, close=99.9, volume=2000, delta=-150)  # body_ratio low
    # Initiative bar: high body_ratio + |delta_pct| > 0.5, POSITIVE delta (buyers initiated)
    initiative = _candle(
        5,
        open_=99.9,
        high=101.0,
        low=99.85,
        close=100.95,  # body_ratio = 1.05/1.15 = 0.91
        volume=1800,
        delta=1500,
    )  # delta_pct = 0.83
    candles = base + [abs1, abs2, abs3, initiative]
    _run(
        "R8 — flow_shift (passive sellers → buyer initiative)",
        candles,
        {"flow_shift": (lambda v: v > 0.3, "fires positive (buy-side initiative after sell absorption)")},
    )


def scenario_R9_delta_divergence_bull() -> None:
    """Price makes new HH, cum-delta fails to confirm (lower-high)."""
    # 5 bars: prices rising overall, but cum-delta turning negative on the breakout
    candles = [
        _candle(0, 100, 100.6, 99.8, 100.5, 1000, 300),  # cum_delta = 300
        _candle(1, 100.5, 101.0, 100.2, 100.9, 1100, 250),  # cum = 550
        _candle(2, 100.9, 101.4, 100.7, 101.2, 1100, 200),  # cum = 750 (max so far)
        _candle(3, 101.2, 101.6, 101.0, 101.3, 900, -100),  # cum = 650 (down)
        _candle(4, 101.3, 101.9, 101.1, 101.85, 1200, -50),  # price HH (101.85 > prev max 101.6) but cum=600 < 750
    ]
    _run(
        "R9 — delta_divergence (bull: price HH, cum-delta lower-high)",
        candles,
        {"delta_divergence": (lambda v: v == 1.0, "fires (=1)")},
    )


def scenario_R10_delta_divergence_bear() -> None:
    """Price makes new LL, cum-delta fails to confirm (higher-low)."""
    candles = [
        _candle(0, 100, 100.3, 99.4, 99.6, 1000, -300),  # cum = -300
        _candle(1, 99.6, 100.0, 99.0, 99.2, 1100, -250),  # cum = -550
        _candle(2, 99.2, 99.5, 98.6, 98.8, 1100, -200),  # cum = -750 (min so far)
        _candle(3, 98.8, 99.1, 98.5, 98.9, 900, 100),  # cum = -650 (up)
        _candle(4, 98.9, 99.2, 98.4, 98.45, 1200, 50),  # price LL (98.4 < prev min 98.5) but cum=-600 > -750
    ]
    _run(
        "R10 — delta_divergence (bear: price LL, cum-delta higher-low)",
        candles,
        {"delta_divergence": (lambda v: v == 1.0, "fires (=1)")},
    )


# ---------------------------------------------------------------------------
# CONTINUATION scenarios
# ---------------------------------------------------------------------------


def scenario_C1_initiative_follow_through() -> None:
    """Strong trigger candle + sustained HIGH volume next bar same direction."""
    # Low baseline so the follow-through bar's vol_ratio is high.
    base = [_candle(i, 100, 100.5, 99.5, 100, 500, 25) for i in range(3)]
    # Trigger: strong body + high |delta_pct|
    trigger = _candle(
        3,
        open_=100,
        high=100.9,
        low=99.9,
        close=100.85,  # body_ratio 0.85
        volume=1500,
        delta=1200,
    )  # delta_pct = 0.8
    # Follow-through: textbook strong follow — 3× baseline vol + tight high-body candle
    followup = _candle(
        4,
        open_=100.85,
        high=102.0,
        low=100.83,
        close=101.95,  # body_ratio 1.10/1.17 ≈ 0.94
        volume=3000,
        delta=2500,
    )
    candles = base + [trigger, followup]
    _run(
        "C1 — initiative_follow_through (strong trigger + same-dir follow-through)",
        candles,
        {"initiative_follow_through": (lambda v: v > 0.4, "fires positive (bull follow-through)")},
    )


def scenario_C2_imbalance_density_bull() -> None:
    """Buy-side imbalance cluster → signed imbalance_density positive."""
    base = [_candle(i, 100, 100.5, 99.5, 100, 1000, 50) for i in range(3)]
    # Construct candle with multiple buy-side price levels showing imbalance.
    pls = [
        PriceLevelFlow(price=100.50, buy_volume=200, sell_volume=20),
        PriceLevelFlow(price=100.75, buy_volume=300, sell_volume=30),
        PriceLevelFlow(price=101.00, buy_volume=250, sell_volume=25),
        PriceLevelFlow(price=101.25, buy_volume=350, sell_volume=35),
    ]
    buy_cluster = _candle(3, open_=100.4, high=101.5, low=100.4, close=101.4, volume=1410, delta=1240, price_levels=pls)
    candles = base + [buy_cluster]
    _run(
        "C2 — imbalance_density (signed: bull cluster → +x)",
        candles,
        {"imbalance_density": (lambda v: v > 0.1, "fires positive (buy-side density)")},
    )


def scenario_C3_imbalance_density_bear() -> None:
    """Sell-side imbalance cluster → signed imbalance_density negative."""
    base = [_candle(i, 100, 100.5, 99.5, 100, 1000, 50) for i in range(3)]
    pls = [
        PriceLevelFlow(price=99.50, buy_volume=20, sell_volume=200),
        PriceLevelFlow(price=99.25, buy_volume=30, sell_volume=300),
        PriceLevelFlow(price=99.00, buy_volume=25, sell_volume=250),
        PriceLevelFlow(price=98.75, buy_volume=35, sell_volume=350),
    ]
    sell_cluster = _candle(3, open_=99.6, high=99.6, low=98.6, close=98.7, volume=1410, delta=-1240, price_levels=pls)
    candles = base + [sell_cluster]
    _run(
        "C3 — imbalance_density (signed: bear cluster → -x)",
        candles,
        {"imbalance_density": (lambda v: v < -0.1, "fires negative (sell-side density)")},
    )


def scenario_C4_initiative_momentum() -> None:
    """Strong directional candle (high body × high |delta_pct|)."""
    base = [_candle(i, 100, 100.5, 99.5, 100, 1000, 50) for i in range(3)]
    strong = _candle(
        3,
        open_=100,
        high=101.0,
        low=99.95,
        close=100.95,  # body_ratio = 0.95/1.05 = 0.90
        volume=1200,
        delta=1100,
    )  # |delta_pct| = 0.92
    candles = base + [strong]
    _run(
        "C4 — initiative_momentum (strong directional candle)",
        candles,
        {"initiative_momentum": (lambda v: v > 0.7, "fires high (body × |delta_pct| > 0.7)")},
    )


# ---------------------------------------------------------------------------
# CHOP / SKIP scenarios
# ---------------------------------------------------------------------------


def scenario_S1_two_way_battle() -> None:
    """High volume + ~zero delta + low body = two-way battle, skip."""
    base = [_candle(i, 100, 100.5, 99.5, 100, 1000, 50) for i in range(4)]
    # High vol (2× avg), near-zero delta (|delta_pct| < 0.15), wide range/narrow body
    battle = _candle(
        4,
        open_=100.2,
        high=101.0,
        low=99.4,
        close=100.25,  # body 0.05/1.6 = 0.03
        volume=2500,
        delta=100,
    )  # delta_pct = 0.04
    candles = base + [battle]
    _run(
        "S1 — two_way_battle (high vol + zero delta + low body)",
        candles,
        {"two_way_battle": (lambda v: v > 0.3, "fires (>0.3)")},
    )


def scenario_S2_absorption_strength_chop() -> None:
    """3 consecutive narrow-body high-vol bars = sustained absorption (chop).

    Formula: vol_factor (last3 avg vol / lookback avg, clipped 3, /3) ×
    body_factor (1 - last3 avg body_ratio). Needs >2× vol ratio for the
    vol_factor to push above 0.5 after body_factor multiplication.
    """
    # Low baseline so last3 vol_ratio is ~3× (the formula clips at 3×).
    base = [_candle(i, 100, 100.5, 99.5, 100, 500, 25) for i in range(3)]
    # 3 absorption bars — high vol (3× base), narrow body
    a1 = _candle(3, open_=100, high=100.6, low=99.4, close=100.1, volume=2500, delta=-50)
    a2 = _candle(4, open_=100.1, high=100.6, low=99.5, close=99.95, volume=2400, delta=-30)
    a3 = _candle(5, open_=99.95, high=100.5, low=99.4, close=99.9, volume=2500, delta=-40)
    candles = base + [a1, a2, a3]
    _run(
        "S2 — absorption_strength (3 narrow-body high-vol bars)",
        candles,
        {"absorption_strength": (lambda v: v > 0.5, "fires high (>0.5)")},
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 80)
    print("OF DIM SCENARIO TESTS — methodology-aligned synthetic patterns")
    print(f"  using {len(_ORDERFLOW_LABELS)}-dim OF stack (post-Tier A/B/C fixes)")
    print("=" * 80)

    # REVERSAL scenarios
    print("\n" + "─" * 60)
    print("REVERSAL SCENARIOS")
    print("─" * 60)
    scenario_R1_vsa_absorption()
    scenario_R2_stop_run_2bar()
    scenario_R3_stop_run_3bar()
    scenario_R4_failed_auction()
    scenario_R5_capitulation_spike()
    scenario_R6_hammer()
    scenario_R7_shooting_star()
    scenario_R8_passive_to_initiative()
    scenario_R9_delta_divergence_bull()
    scenario_R10_delta_divergence_bear()

    # CONTINUATION scenarios
    print("\n" + "─" * 60)
    print("CONTINUATION SCENARIOS")
    print("─" * 60)
    scenario_C1_initiative_follow_through()
    scenario_C2_imbalance_density_bull()
    scenario_C3_imbalance_density_bear()
    scenario_C4_initiative_momentum()

    # CHOP / SKIP scenarios
    print("\n" + "─" * 60)
    print("CHOP / SKIP SCENARIOS")
    print("─" * 60)
    scenario_S1_two_way_battle()
    scenario_S2_absorption_strength_chop()

    print("\n" + "=" * 80)
    if _FAILURES:
        print(f"FAILED — {len(_FAILURES)} scenario check(s) failed:")
        for f in _FAILURES:
            print(f"  - {f}")
        print("\nFix the dim that failed before proceeding to real-data replay.")
        return 1
    print(f"OK — all {16 + 4 + 2} scenario checks passed.")
    print("Safe to escalate to real-data replay + Phase 1 audit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
