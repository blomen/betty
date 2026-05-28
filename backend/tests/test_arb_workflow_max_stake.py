"""scan_arb_workflow attaches pinnacle_max_stake_sek to each opp."""

from src.services.opportunity_service import (
    _SEK_PER_USD,  # added below
)


def test_sek_per_usd_constant_exposed():
    # Sanity check that the conversion constant exists and matches the
    # frontend's hard-coded SEK_PER_USD = 10.5 in PlayPage.tsx.
    assert _SEK_PER_USD == 10.5


def test_compute_pinnacle_max_stake_sek_picks_min_pinnacle_leg():
    """Helper picks the smallest max_stake across Pinnacle legs and converts to SEK.

    Non-Pinnacle legs are ignored. Returns None when no Pinnacle leg has a
    populated max_stake.
    """
    from src.services.opportunity_service import _compute_pinnacle_max_stake_sek

    legs = [
        {"provider": "pinnacle", "max_stake": 1500.0},
        {"provider": "pinnacle", "max_stake": 800.0},
        {"provider": "lodur", "max_stake": None},
    ]
    assert _compute_pinnacle_max_stake_sek(legs) == 800.0 * 10.5


def test_compute_pinnacle_max_stake_sek_returns_none_when_no_pinnacle():
    from src.services.opportunity_service import _compute_pinnacle_max_stake_sek

    legs = [{"provider": "polymarket", "max_stake": None}, {"provider": "lodur"}]
    assert _compute_pinnacle_max_stake_sek(legs) is None


def test_compute_pinnacle_max_stake_sek_returns_none_when_pinnacle_max_null(monkeypatch):
    from src.services.opportunity_service import _compute_pinnacle_max_stake_sek

    legs = [{"provider": "pinnacle", "max_stake": None}, {"provider": "lodur", "max_stake": None}]
    assert _compute_pinnacle_max_stake_sek(legs) is None
