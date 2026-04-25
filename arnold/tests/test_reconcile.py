"""reconcile_from_history — tests."""

from __future__ import annotations

from arnold.mirror.reconcile import _has_meaningful_diff, reconcile_from_history


def test_no_history_no_deltas():
    deltas = reconcile_from_history(db_pending=[{"id": 1, "stake": 100, "odds": 2.0}], history=[])
    assert deltas == []


def test_no_pending_no_deltas():
    deltas = reconcile_from_history(db_pending=[], history=[{"status": "lost", "stake": 100}])
    assert deltas == []


def test_exact_provider_bet_id_match_settlement():
    db = [{"id": 1, "provider_bet_id": "12345", "stake": 130, "odds": 3.20, "event_name": "El Paso v San Antonio"}]
    history = [
        {
            "provider_bet_id": "12345",
            "status": "lost",
            "stake": 72.02,
            "odds": 3.25,
            "payout": 0,
            "event_name": "El Paso Locomotive FC - San Antonio FC",
        }
    ]
    deltas = reconcile_from_history(db, history)
    assert len(deltas) == 1
    d = deltas[0]
    assert d.bet_id == 1
    assert d.match_method == "id"
    assert d.changes["result"] == "lost"
    assert d.changes["stake"] == 72.02
    assert d.changes["odds"] == 3.25


def test_fuzzy_name_match_above_threshold():
    """Sambenedettese case from live audit — token overlap 0.4 fails old logic but rapidfuzz token_set_ratio passes."""
    db = [{"id": 2, "stake": 130, "odds": 5.60, "home_team": "Sambenedettese", "away_team": "Citta Di Pontedera"}]
    history = [
        {
            "status": "lost",
            "stake": 35.22,
            "odds": 5.60,
            "event_name": "Sambenedettese - Pontedera",
            "provider_bet_id": "98765",
        }
    ]
    deltas = reconcile_from_history(db, history)
    assert len(deltas) == 1
    d = deltas[0]
    assert d.match_method == "fuzzy"
    assert d.confidence >= 80
    assert d.changes["result"] == "lost"
    assert d.changes["stake"] == 35.22
    assert d.changes["provider_bet_id"] == "98765"  # backfilled


def test_fuzzy_match_rejects_when_odds_drift_exceeds_5pct():
    db = [{"id": 3, "stake": 100, "odds": 2.00, "home_team": "Team A", "away_team": "Team B"}]
    history = [{"status": "won", "stake": 100, "odds": 2.20, "event_name": "Team A - Team B", "payout": 220}]
    deltas = reconcile_from_history(db, history)
    # 2.20 vs 2.00 = 10% drift, above 5% threshold → no match
    assert deltas == []


def test_no_delta_when_db_already_matches():
    db = [{"id": 4, "provider_bet_id": "x", "stake": 100, "odds": 2.0, "result": "won", "payout": 200}]
    history = [
        {
            "provider_bet_id": "x",
            "status": "won",
            "stake": 100,
            "odds": 2.0,
            "payout": 200,
            "event_name": "irrelevant",
        }
    ]
    deltas = reconcile_from_history(db, history)
    assert deltas == []  # nothing to update


def test_pending_status_not_pushed():
    """Provider says still pending — don't push status change."""
    db = [{"id": 5, "provider_bet_id": "y", "stake": 100, "odds": 2.0, "result": "pending"}]
    history = [{"provider_bet_id": "y", "status": "pending", "stake": 100, "odds": 2.0, "event_name": "irrelevant"}]
    deltas = reconcile_from_history(db, history)
    assert deltas == []


def test_two_db_bets_match_two_history_entries_no_double_use():
    db = [
        {"id": 6, "provider_bet_id": "a", "stake": 100, "odds": 2.0},
        {"id": 7, "provider_bet_id": "b", "stake": 100, "odds": 2.0},
    ]
    history = [
        {"provider_bet_id": "a", "status": "lost", "stake": 100, "odds": 2.0, "event_name": "x"},
        {"provider_bet_id": "b", "status": "won", "stake": 100, "odds": 2.0, "payout": 200, "event_name": "y"},
    ]
    deltas = reconcile_from_history(db, history)
    assert len(deltas) == 2
    by_id = {d.bet_id: d for d in deltas}
    assert by_id[6].changes["result"] == "lost"
    assert by_id[7].changes["result"] == "won"
    assert by_id[7].changes["payout"] == 200


def test_meaningful_diff_helper():
    assert not _has_meaningful_diff(100.0, 100.0)
    assert not _has_meaningful_diff(100.0, 100.005)  # below abs floor
    assert _has_meaningful_diff(100.0, 100.5)
    assert _has_meaningful_diff(50.0, 50.7)  # 1.4% drift
    assert _has_meaningful_diff(None, 100.0)  # None → present
    assert not _has_meaningful_diff(None, None)
