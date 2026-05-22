"""Arb leg provider_meta must resolve PER OUTCOME.

Polymarket `token_id` and Altenar/Kambi `outcome_id` are per-side. A 2-way
arb's two legs must each get their OWN side's metadata — keying the lookup
without `outcome` handed both legs the first-seen side's token, which priced
+ placed the Polymarket hedge on the wrong outcome (the home/away swap)."""

from __future__ import annotations

from src.services.opportunity_service import _resolve_leg_provider_meta


def _meta_map() -> dict[tuple, dict]:
    """provider_meta_map keyed (event_id, provider, market, outcome, point)."""
    return {
        ("ev1", "polymarket", "moneyline", "home", None): {"token_id": "HOME_TOK"},
        ("ev1", "polymarket", "moneyline", "away", None): {"token_id": "AWAY_TOK"},
    }


def test_home_leg_gets_home_token():
    leg = {"provider": "polymarket", "outcome": "home", "point": None}
    meta = _resolve_leg_provider_meta(_meta_map(), "ev1", "moneyline", leg)
    assert meta["token_id"] == "HOME_TOK"


def test_away_leg_gets_away_token():
    leg = {"provider": "polymarket", "outcome": "away", "point": None}
    meta = _resolve_leg_provider_meta(_meta_map(), "ev1", "moneyline", leg)
    assert meta["token_id"] == "AWAY_TOK"


def test_legs_never_share_token():
    """The swap bug: both legs of a 2-way arb getting the same token."""
    m = _meta_map()
    home = _resolve_leg_provider_meta(
        m, "ev1", "moneyline", {"provider": "polymarket", "outcome": "home", "point": None}
    )
    away = _resolve_leg_provider_meta(
        m, "ev1", "moneyline", {"provider": "polymarket", "outcome": "away", "point": None}
    )
    assert home["token_id"] != away["token_id"]


def test_spread_sign_flip_fallback():
    """A spread leg's point may carry the opposite sign vs its odds row
    (home -1.5 / away +1.5) — the fallback finds the sign-flipped key."""
    m = {("ev1", "polymarket", "spread", "home", -1.5): {"token_id": "SPR_HOME"}}
    leg = {"provider": "polymarket", "outcome": "home", "point": 1.5}
    meta = _resolve_leg_provider_meta(m, "ev1", "spread", leg)
    assert meta["token_id"] == "SPR_HOME"


def test_missing_meta_returns_empty_dict():
    leg = {"provider": "polymarket", "outcome": "home", "point": None}
    assert _resolve_leg_provider_meta({}, "ev1", "moneyline", leg) == {}
