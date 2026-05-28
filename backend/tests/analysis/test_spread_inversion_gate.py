"""Regression tests for the home/away spread inversion fix (2026-05-28).

Two-part fix:
1. swap_home_away_outcomes now relabels outcomes ONLY — the point sign is
   preserved (was: relabel + negate point, which broke internal consistency
   on spread).
2. Scanner gate: drop sharp spread rows whose (outcome, point sign) disagrees
   with the sharp moneyline favorite — catches upstream-source inconsistencies
   like the Oradea/Cluj 2026-05-28 event.
"""

from __future__ import annotations

# ────────────────────────────────────────────────────────────────────────────
# swap_home_away_outcomes — label-only, point sign preserved
# ────────────────────────────────────────────────────────────────────────────


def test_swap_relabels_home_to_away():
    from src.pipeline.storage import swap_home_away_outcomes

    result = swap_home_away_outcomes([{"name": "home", "odds": 1.247}])
    assert result == [{"name": "away", "odds": 1.247}]


def test_swap_relabels_away_to_home():
    from src.pipeline.storage import swap_home_away_outcomes

    result = swap_home_away_outcomes([{"name": "away", "odds": 3.49}])
    assert result == [{"name": "home", "odds": 3.49}]


def test_swap_preserves_point_sign_on_home():
    """Negating the point sign was the 2026-05-28 Oradea/Cluj bug.

    "home -6.5 @ 1.621" means the home team is favored by 6.5 at 1.621. When we
    relabel that row to point at Betty's away team (because Pinnacle's home
    differs from Betty's canonical), the spread is unchanged for that team —
    it's still -6.5 from their perspective. Negating it would store the row as
    "away +6.5", which describes the OTHER side of the line entirely.
    """
    from src.pipeline.storage import swap_home_away_outcomes

    result = swap_home_away_outcomes([{"name": "home", "point": -6.5, "odds": 1.621}])
    assert result[0]["name"] == "away"
    assert result[0]["point"] == -6.5  # sign preserved


def test_swap_preserves_point_sign_on_away():
    from src.pipeline.storage import swap_home_away_outcomes

    result = swap_home_away_outcomes([{"name": "away", "point": 6.5, "odds": 2.17}])
    assert result[0]["name"] == "home"
    assert result[0]["point"] == 6.5  # sign preserved


def test_swap_internal_consistency_across_markets():
    """End-to-end: a full Pinnacle event (moneyline + spread pair) survives
    swap with the favorite team consistent across both markets.

    Pinnacle's "natural" source: home team is the favorite. We swap because
    matcher decided provider's home/away order differs from canonical. After
    the swap, the team that was Pinnacle's home is now Betty's away — and that
    team should still be the favorite per BOTH the moneyline (lower odds) and
    the spread (negative point).
    """
    from src.pipeline.storage import swap_home_away_outcomes

    pinnacle_source = [
        # Moneyline: home (Cluj per provider) is the favorite
        {"name": "home", "odds": 1.247},
        {"name": "away", "odds": 3.49},
        # Spread: home favorite gives 6.5 points at 1.621
        {"name": "home", "point": -6.5, "odds": 1.621},
        {"name": "away", "point": 6.5, "odds": 2.17},
    ]

    swapped = swap_home_away_outcomes(pinnacle_source)

    # In Betty's frame:
    betty_home = [o for o in swapped if o["name"] == "home"]
    betty_away = [o for o in swapped if o["name"] == "away"]

    # Moneyline favorite is now Betty's away (Cluj): 1.247
    assert next(o["odds"] for o in betty_away if "point" not in o or o.get("point") is None) == 1.247
    assert next(o["odds"] for o in betty_home if "point" not in o or o.get("point") is None) == 3.49

    # Spread: Betty's away (Cluj favorite) is at -6.5 @ 1.621. Betty's home
    # (Oradea underdog) is at +6.5 @ 2.17. Point sign matches favorite.
    away_spread = next(o for o in betty_away if "point" in o and o.get("point") is not None)
    home_spread = next(o for o in betty_home if "point" in o and o.get("point") is not None)
    assert away_spread["point"] == -6.5 and away_spread["odds"] == 1.621
    assert home_spread["point"] == 6.5 and home_spread["odds"] == 2.17


def test_swap_ignores_non_home_away_outcomes():
    """over/under/draw etc. pass through untouched."""
    from src.pipeline.storage import swap_home_away_outcomes

    outcomes = [
        {"name": "over", "point": 2.5, "odds": 1.95},
        {"name": "under", "point": 2.5, "odds": 1.85},
        {"name": "draw", "odds": 3.20},
    ]
    result = swap_home_away_outcomes(outcomes)
    assert result == outcomes


# ────────────────────────────────────────────────────────────────────────────
# Scanner gate: drop sharp spread rows when sign disagrees with moneyline
# ────────────────────────────────────────────────────────────────────────────


def _make_scanner(events=None):
    """Build an OpportunityScanner without touching the DB.

    We bypass __init__ because we only need group_odds's helpers, which are
    self-methods that don't read state beyond what's passed in.
    """
    from src.analysis.scanner import OpportunityScanner

    scanner = OpportunityScanner.__new__(OpportunityScanner)
    return scanner


def test_sharp_favorite_detected_from_moneyline():
    scanner = _make_scanner()
    grouped = {
        "moneyline": {
            "home": [{"provider": "pinnacle", "odds": 1.247}],
            "away": [{"provider": "pinnacle", "odds": 3.49}],
        }
    }
    assert scanner._sharp_moneyline_favorite(grouped) == "home"


def test_sharp_favorite_detected_from_1x2():
    scanner = _make_scanner()
    grouped = {
        "1x2": {
            "home": [{"provider": "pinnacle", "odds": 4.20}],
            "away": [{"provider": "pinnacle", "odds": 1.45}],
            "draw": [{"provider": "pinnacle", "odds": 3.50}],
        }
    }
    assert scanner._sharp_moneyline_favorite(grouped) == "away"


def test_no_favorite_when_sharp_moneyline_missing():
    scanner = _make_scanner()
    grouped = {
        "moneyline": {
            "home": [{"provider": "betinia", "odds": 1.20}],
            "away": [{"provider": "betinia", "odds": 4.00}],
        }
    }
    assert scanner._sharp_moneyline_favorite(grouped) is None


def test_no_favorite_when_pickem():
    """Equal sharp odds → no favorite, gate does nothing."""
    scanner = _make_scanner()
    grouped = {
        "moneyline": {
            "home": [{"provider": "pinnacle", "odds": 1.92}],
            "away": [{"provider": "pinnacle", "odds": 1.92}],
        }
    }
    assert scanner._sharp_moneyline_favorite(grouped) is None


def test_gate_drops_sharp_when_point_sign_disagrees_with_moneyline():
    """Oradea/Cluj 2026-05-28 reproduction.

    Sharp moneyline says home is favored (1.247), but sharp spread puts the
    favorite at +6.5 (underdog side per point sign) and the underdog at -6.5.
    Drop the sharp spread rows so the scanner doesn't surface a phantom arb.
    """
    scanner = _make_scanner()
    grouped = {
        "moneyline": {
            "home": [{"provider": "pinnacle", "odds": 1.247}],
            "away": [{"provider": "pinnacle", "odds": 3.49}],
        },
        "spread_6.5": {
            "home": [
                {"provider": "pinnacle", "odds": 1.621, "point": 6.5},  # favorite at +6.5 → violation
                {"provider": "betinia", "odds": 1.83, "point": 6.5},
            ],
            "away": [
                {"provider": "pinnacle", "odds": 2.17, "point": -6.5},  # underdog at -6.5 → violation
            ],
        },
    }
    scanner._drop_sharp_inconsistent_spread(grouped)

    # Sharp rows dropped from spread, soft row stays
    assert all(r["provider"] != "pinnacle" for r in grouped["spread_6.5"]["home"])
    assert grouped["spread_6.5"]["away"] == []
    assert any(r["provider"] == "betinia" for r in grouped["spread_6.5"]["home"])


def test_gate_keeps_sharp_when_consistent():
    """Panathinaikos-style data: home is moneyline favorite AND home has negative
    point spread. Gate is a no-op."""
    scanner = _make_scanner()
    grouped = {
        "moneyline": {
            "home": [{"provider": "pinnacle", "odds": 1.058}],
            "away": [{"provider": "pinnacle", "odds": 8.58}],
        },
        "spread_-13.5": {
            "home": [{"provider": "pinnacle", "odds": 2.36, "point": -13.5}],
            "away": [{"provider": "pinnacle", "odds": 1.62, "point": 13.5}],
        },
    }
    scanner._drop_sharp_inconsistent_spread(grouped)

    # Nothing dropped
    assert grouped["spread_-13.5"]["home"][0]["provider"] == "pinnacle"
    assert grouped["spread_-13.5"]["away"][0]["provider"] == "pinnacle"


def test_gate_keeps_sharp_when_away_is_favorite_and_signs_consistent():
    """Away-favored event: away has negative point spread, home positive."""
    scanner = _make_scanner()
    grouped = {
        "moneyline": {
            "home": [{"provider": "pinnacle", "odds": 3.20}],
            "away": [{"provider": "pinnacle", "odds": 1.35}],
        },
        "spread_4.5": {
            "home": [{"provider": "pinnacle", "odds": 2.10, "point": 4.5}],
            "away": [{"provider": "pinnacle", "odds": 1.78, "point": -4.5}],
        },
    }
    scanner._drop_sharp_inconsistent_spread(grouped)
    assert grouped["spread_4.5"]["home"][0]["provider"] == "pinnacle"
    assert grouped["spread_4.5"]["away"][0]["provider"] == "pinnacle"


def test_gate_skips_zero_point():
    """A pickem spread line (point=0) has no favorite side. Gate ignores it."""
    scanner = _make_scanner()
    grouped = {
        "moneyline": {
            "home": [{"provider": "pinnacle", "odds": 1.247}],
            "away": [{"provider": "pinnacle", "odds": 3.49}],
        },
        "spread_0.0": {
            "home": [{"provider": "pinnacle", "odds": 1.90, "point": 0}],
            "away": [{"provider": "pinnacle", "odds": 1.90, "point": 0}],
        },
    }
    scanner._drop_sharp_inconsistent_spread(grouped)
    # Sharp rows preserved
    assert grouped["spread_0.0"]["home"][0]["provider"] == "pinnacle"


def test_gate_no_op_when_no_sharp_moneyline():
    """No sharp moneyline → can't determine favorite → no filtering."""
    scanner = _make_scanner()
    grouped = {
        "spread_6.5": {
            "home": [{"provider": "pinnacle", "odds": 1.621, "point": 6.5}],
            "away": [{"provider": "pinnacle", "odds": 2.17, "point": -6.5}],
        },
    }
    scanner._drop_sharp_inconsistent_spread(grouped)
    # Without a sharp moneyline reference, we leave spread untouched
    assert grouped["spread_6.5"]["home"][0]["provider"] == "pinnacle"
