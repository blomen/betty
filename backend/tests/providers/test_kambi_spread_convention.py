"""Kambi Asian Handicap (betOfferType=7) must NOT collapse per-outcome
point signs via abs().

The 2026-05-26 Bahia vs Botafogo case: unibet stored `spread, point=+0.5,
home, odds=1.92` — but the real Bahia +0.5 line is priced ~1.25 (Pinnacle
home@+0.5). The 1.92 was actually "Bahia outright" (matches 1.86) from a
raw line=-500 entry that got abs()'d to point=+0.5 and overwrote the true
"Bahia +0.5" row via the unique (event, provider, market, outcome, point)
upsert.

The previous fix at kambi.py:574-577 (`for o in outcomes: o["point"] =
abs(o["point"])`) destroys sign information whenever Kambi emits the same
absolute line on both sign sides. Kambi's per-outcome lines are already
Pinnacle convention for betOfferType=1 (verified against Athletics data),
and there is no evidence type=7 uses an opposite convention; the original
"opposite signs" claim in the code comment is unsubstantiated.

This test pins the no-abs behavior so a future "let's just abs again"
regression fails loudly.
"""

from __future__ import annotations

from src.providers.kambi import KambiRetriever


def _betoffer(home_line: float, away_line: float, home_odds: float, away_odds: float):
    """Build a minimal betOffer dict that exercises _parse_market type=7."""
    return {
        "id": "bo-1",
        "eventId": "evt-1",
        "betOfferType": {"id": 7},
        "tags": ["MAIN_LINE"],
        "criterion": {"englishLabel": "Asian Handicap", "label": "Asian Handicap"},
        "outcomes": [
            {"id": "out-h", "label": "Home", "odds": int(home_odds * 1000), "line": int(home_line * 1000)},
            {"id": "out-a", "label": "Away", "odds": int(away_odds * 1000), "line": int(away_line * 1000)},
        ],
    }


def _build_retriever():
    return KambiRetriever({"id": "unibet", "api_base": "https://example.invalid"})


def test_kambi_ah_type7_preserves_negative_sign_on_home():
    """home line=-500 must stay as point=-0.5 (the 'home -0.5' = outright line)."""
    retriever = _build_retriever()
    bo = _betoffer(home_line=-0.5, away_line=0.5, home_odds=1.86, away_odds=1.94)
    parsed = retriever._parse_market(bo, outcome_map={}, home_team="bahia", away_team="botafogo")
    assert parsed is not None
    assert parsed["type"] == "spread"
    points_by_outcome = {o["name"]: o["point"] for o in parsed["outcomes"]}
    assert points_by_outcome["home"] == -0.5
    assert points_by_outcome["away"] == 0.5


def test_kambi_ah_type7_preserves_positive_sign_on_home():
    """home line=+500 must stay as point=+0.5 (the 'home +0.5' = win-or-draw line).
    The previous abs() collapsed -500 and +500 to the same key, overwriting one
    via upsert and producing phantom 40%+ edges."""
    retriever = _build_retriever()
    bo = _betoffer(home_line=0.5, away_line=-0.5, home_odds=1.25, away_odds=4.03)
    parsed = retriever._parse_market(bo, outcome_map={}, home_team="bahia", away_team="botafogo")
    assert parsed is not None
    points_by_outcome = {o["name"]: o["point"] for o in parsed["outcomes"]}
    assert points_by_outcome["home"] == 0.5
    assert points_by_outcome["away"] == -0.5


def test_kambi_two_distinct_ah_lines_do_not_collide_after_parsing():
    """Two separate betOffers (-0.5 and +0.5 lines) must yield FOUR distinct
    (outcome, point) pairs — not two, as abs() would collapse them."""
    retriever = _build_retriever()
    bo_minus = _betoffer(home_line=-0.5, away_line=0.5, home_odds=1.86, away_odds=1.94)
    bo_plus = _betoffer(home_line=0.5, away_line=-0.5, home_odds=1.25, away_odds=4.03)
    parsed_minus = retriever._parse_market(bo_minus, outcome_map={}, home_team="bahia", away_team="botafogo")
    parsed_plus = retriever._parse_market(bo_plus, outcome_map={}, home_team="bahia", away_team="botafogo")
    all_keys = {(o["name"], o["point"]) for m in (parsed_minus, parsed_plus) for o in m["outcomes"]}
    assert all_keys == {
        ("home", -0.5),
        ("away", 0.5),
        ("home", 0.5),
        ("away", -0.5),
    }, f"unique (outcome, point) collapsed via abs(): {all_keys}"
