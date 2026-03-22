"""Test EV enrichment for odds boosts."""
from datetime import datetime, timezone, timedelta
from src.db.models import Event


def _make_event(db_session, event_id="evt-1", home="Arsenal", away="Chelsea", hours_from_now=24):
    start = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    ev = Event(id=event_id, home_team=home, away_team=away, sport="football",
               start_time=start)
    db_session.add(ev)
    db_session.flush()
    return ev


def test_enrich_matches_before_edge(db_session):
    """Event matching must run BEFORE edge calculation so Pinnacle proxy can fill original_odds."""
    _make_event(db_session)
    specials = [{
        "title": "Arsenal to win",
        "event": "Arsenal vs Chelsea",
        "boosted_odds": 2.50,
        "original_odds": 2.00,
        "sport": "football",
    }]
    from src.analysis.ev_enrichment import enrich_specials_with_ev
    result = enrich_specials_with_ev(specials, db_session)
    assert result[0].get("matched_event_id") is not None
    assert result[0].get("edge_pct") == 25.0


from src.db.models import Odds


def _add_pinnacle_odds(db_session, event_id="evt-1"):
    """Add Pinnacle 1x2 odds for an event."""
    from src.db.models import Provider
    db_session.add(Provider(id="pinnacle", name="Pinnacle"))
    for outcome, odds_val in [("home", 2.10), ("draw", 3.40), ("away", 3.50)]:
        db_session.add(Odds(
            event_id=event_id, provider_id="pinnacle",
            market="1x2", outcome=outcome, odds=odds_val,
        ))
    db_session.flush()


def test_pinnacle_proxy_fills_original_odds(db_session):
    """Kambi boost with no original_odds gets Pinnacle fair odds as proxy."""
    ev = _make_event(db_session)
    _add_pinnacle_odds(db_session, ev.id)
    specials = [{
        "title": "Arsenal to win",
        "event": "Arsenal vs Chelsea",
        "boosted_odds": 3.00,
        "original_odds": None,
        "sport": "football",
    }]
    from src.analysis.ev_enrichment import enrich_specials_with_ev
    result = enrich_specials_with_ev(specials, db_session)
    s = result[0]
    assert s.get("matched_event_id") == ev.id
    assert s.get("original_odds") is not None
    assert s["original_odds"] > 2.0
    assert s.get("edge_pct") is not None


def test_pinnacle_proxy_skips_combos(db_session):
    """Combo boosts (multi-leg) should NOT get Pinnacle proxy."""
    ev = _make_event(db_session)
    _add_pinnacle_odds(db_session, ev.id)
    specials = [{
        "title": "Arsenal to win & over 2.5 goals",
        "event": "Arsenal vs Chelsea",
        "boosted_odds": 5.00,
        "original_odds": None,
        "sport": "football",
    }]
    from src.analysis.ev_enrichment import enrich_specials_with_ev
    result = enrich_specials_with_ev(specials, db_session)
    assert result[0].get("original_odds") is None


def test_pinnacle_proxy_skips_when_already_has_odds(db_session):
    """Boosts that already have original_odds should not be overwritten."""
    ev = _make_event(db_session)
    _add_pinnacle_odds(db_session, ev.id)
    specials = [{
        "title": "Arsenal to win",
        "event": "Arsenal vs Chelsea",
        "boosted_odds": 3.00,
        "original_odds": 2.50,
        "sport": "football",
    }]
    from src.analysis.ev_enrichment import enrich_specials_with_ev
    result = enrich_specials_with_ev(specials, db_session)
    assert result[0]["original_odds"] == 2.50
