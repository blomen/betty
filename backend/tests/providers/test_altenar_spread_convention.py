"""Altenar spread point sign convention.

Altenar's raw API emits both home and away spread outcomes at the SAME
`market.sv` point value (book-line convention — point identifies the line,
both sides carry that same line). Pinnacle (the scanner's reference)
emits per-outcome: home@P and away@-P share a line.

Without conversion, the scanner's keying (`line = point if home else -point`)
files Altenar's away rows into the OPPOSITE spread bucket, producing
phantom 40%+ edges where one provider's "Mariners +1.5" gets value-compared
against Pinnacle's fair odds for "Mariners -1.5".

Regression test for the 49.67% Athletics @ Mariners bug seen 2026-05-26
across 6 Altenar cluster siblings (betinia, dbet, lodur, quickcasino,
swiper, campobet).
"""

from __future__ import annotations

from src.providers.altenar import AltenarRetriever


def _build_reference_data(market_point: float):
    """Synthesize the minimal reference_data dict _parse_event needs."""
    comp_home = {"id": 1, "name": "Athletics"}
    comp_away = {"id": 2, "name": "Seattle Mariners"}
    champ = {"id": 100, "name": "MLB", "catId": 999}
    market = {
        "id": 555,
        "typeId": 256,  # baseball spread (Handicap incl. extra innings)
        "name": "Handicap",
        "sv": str(market_point),
        "oddIds": [1001, 1002],
    }
    # Two outcomes at the SAME point value (Altenar's book-line convention).
    odd_home = {"id": 1001, "name": "Athletics", "price": 2.65}
    odd_away = {"id": 1002, "name": "Seattle Mariners", "price": 1.50}
    return {
        "_comp_idx": {1: comp_home, 2: comp_away},
        "_champ_idx": {100: champ},
        "_market_idx": {555: market},
        "_odd_idx": {1001: odd_home, 1002: odd_away},
    }


def test_altenar_spread_away_point_is_negated_to_per_outcome_convention():
    """Altenar home@-1.5 stays at -1.5; away@-1.5 (book convention) must
    emit as away@+1.5 (Pinnacle per-outcome convention) so both legs share
    the same scanner line key (`spread_-1.5` = the line where home is -1.5).
    """
    retriever = AltenarRetriever({"id": "betinia"})
    event_data = {
        "id": "evt-1",
        "name": "Athletics @ Seattle Mariners",
        "startDate": "2026-05-27T01:40:00Z",
        "competitorIds": [1, 2],
        "champId": 100,
        "marketIds": [555],
    }
    reference = _build_reference_data(market_point=-1.5)

    se = retriever._parse_event(event_data, sport="baseball", reference_data=reference, sport_id=76)
    assert se is not None
    spreads = [m for m in se.markets if m["type"] == "spread"]
    assert len(spreads) == 1
    outcomes_by_name = {o["name"]: o for o in spreads[0]["outcomes"]}

    # Home outcome keeps the original sign (book-line == per-outcome on home side).
    assert outcomes_by_name["home"]["point"] == -1.5
    # Away outcome's point is FLIPPED — same physical line but per-outcome.
    assert outcomes_by_name["away"]["point"] == 1.5


def test_altenar_spread_positive_point_also_negates_away():
    """The fix must be symmetric: a +1.5 line negates away's point to -1.5."""
    retriever = AltenarRetriever({"id": "betinia"})
    event_data = {
        "id": "evt-2",
        "name": "Athletics @ Seattle Mariners",
        "startDate": "2026-05-27T01:40:00Z",
        "competitorIds": [1, 2],
        "champId": 100,
        "marketIds": [555],
    }
    reference = _build_reference_data(market_point=1.5)
    se = retriever._parse_event(event_data, sport="baseball", reference_data=reference, sport_id=76)
    assert se is not None
    outcomes_by_name = {o["name"]: o for o in se.markets[0]["outcomes"]}
    assert outcomes_by_name["home"]["point"] == 1.5
    assert outcomes_by_name["away"]["point"] == -1.5


def test_altenar_total_market_point_unchanged_for_both_outcomes():
    """The fix is spread-only — totals (over/under) share the same point
    on both sides legitimately (sum of devigged probs = ~1.0). Negating
    over/under points would break total markets.
    """
    retriever = AltenarRetriever({"id": "betinia"})
    market = {
        "id": 777,
        "typeId": 258,  # baseball total
        "name": "Total",
        "sv": "8.5",
        "oddIds": [2001, 2002],
    }
    odd_over = {"id": 2001, "name": "Over 8.5", "price": 1.91}
    odd_under = {"id": 2002, "name": "Under 8.5", "price": 1.91}
    reference = {
        "_comp_idx": {1: {"id": 1, "name": "Athletics"}, 2: {"id": 2, "name": "Seattle Mariners"}},
        "_champ_idx": {100: {"id": 100, "name": "MLB"}},
        "_market_idx": {777: market},
        "_odd_idx": {2001: odd_over, 2002: odd_under},
    }
    event_data = {
        "id": "evt-3",
        "name": "Athletics @ Seattle Mariners",
        "startDate": "2026-05-27T01:40:00Z",
        "competitorIds": [1, 2],
        "champId": 100,
        "marketIds": [777],
    }
    se = retriever._parse_event(event_data, sport="baseball", reference_data=reference, sport_id=76)
    assert se is not None
    outcomes_by_name = {o["name"]: o for o in se.markets[0]["outcomes"]}
    assert outcomes_by_name["over"]["point"] == 8.5
    assert outcomes_by_name["under"]["point"] == 8.5
