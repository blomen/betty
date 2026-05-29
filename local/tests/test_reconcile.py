"""reconcile_from_history — tests."""

from __future__ import annotations

from local.mirror.reconcile import _has_meaningful_diff, reconcile_from_history


def test_no_history_no_deltas():
    deltas = reconcile_from_history(
        db_pending=[{"id": 1, "stake": 100, "odds": 2.0}], history=[]
    )
    assert deltas == []


def test_no_pending_no_deltas():
    deltas = reconcile_from_history(
        db_pending=[], history=[{"status": "lost", "stake": 100}]
    )
    assert deltas == []


def test_exact_provider_bet_id_match_settlement():
    db = [
        {
            "id": 1,
            "provider_bet_id": "12345",
            "stake": 130,
            "odds": 3.20,
            "event_name": "El Paso v San Antonio",
        }
    ]
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
    db = [
        {
            "id": 2,
            "stake": 130,
            "odds": 5.60,
            "home_team": "Sambenedettese",
            "away_team": "Citta Di Pontedera",
        }
    ]
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
    db = [
        {
            "id": 3,
            "stake": 100,
            "odds": 2.00,
            "home_team": "Team A",
            "away_team": "Team B",
        }
    ]
    history = [
        {
            "status": "won",
            "stake": 100,
            "odds": 2.20,
            "event_name": "Team A - Team B",
            "payout": 220,
        }
    ]
    deltas = reconcile_from_history(db, history)
    # 2.20 vs 2.00 = 10% drift, above 5% threshold → no match
    assert deltas == []


def test_no_delta_when_db_already_matches():
    db = [
        {
            "id": 4,
            "provider_bet_id": "x",
            "stake": 100,
            "odds": 2.0,
            "result": "won",
            "payout": 200,
        }
    ]
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
    db = [
        {
            "id": 5,
            "provider_bet_id": "y",
            "stake": 100,
            "odds": 2.0,
            "result": "pending",
        }
    ]
    history = [
        {
            "provider_bet_id": "y",
            "status": "pending",
            "stake": 100,
            "odds": 2.0,
            "event_name": "irrelevant",
        }
    ]
    deltas = reconcile_from_history(db, history)
    assert deltas == []


def test_two_db_bets_match_two_history_entries_no_double_use():
    db = [
        {"id": 6, "provider_bet_id": "a", "stake": 100, "odds": 2.0},
        {"id": 7, "provider_bet_id": "b", "stake": 100, "odds": 2.0},
    ]
    history = [
        {
            "provider_bet_id": "a",
            "status": "lost",
            "stake": 100,
            "odds": 2.0,
            "event_name": "x",
        },
        {
            "provider_bet_id": "b",
            "status": "won",
            "stake": 100,
            "odds": 2.0,
            "payout": 200,
            "event_name": "y",
        },
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


def test_fallback_finds_bet_in_targeted_window():
    """The fallback path receives a small targeted history list and reconciles
    against it — same logic as main pass, just a smaller input."""
    bet = {
        "id": 999,
        "stake": 6.09,
        "odds": 4.25,
        "home_team": "Al Bataeh",
        "away_team": "Al Sharjah UAE",
        "start_time": "2026-04-13T15:30:00Z",
    }
    targeted_history = [
        {
            "status": "lost",
            "stake": 6.09,
            "odds": 4.25,
            "payout": 0,
            "event_name": "Al Bataeh vs Al Sharjah UAE",
            "provider_bet_id": "ALT-123",
        },
    ]
    deltas = reconcile_from_history([bet], targeted_history)
    assert len(deltas) == 1
    d = deltas[0]
    assert d.changes.get("result") == "lost"
    assert d.changes.get("provider_bet_id") == "ALT-123"


def test_fuzzy_match_terminal_status_with_zero_odds():
    """Polymarket Loss rows carry odds=0 (no payout, position liquidated). The
    name match alone must be sufficient when h_status is terminal — otherwise
    lost polymarket bets stay pending forever."""
    bet = {
        "id": 416,
        "provider_bet_id": None,
        "stake": 9.52,
        "odds": 11.549,
        "home_team": "Vitality",
        "away_team": "FUT",
    }
    history = [
        {
            "status": "lost",
            "stake": 0.0,
            "odds": 0.0,
            "payout": 0.0,
            "event_name": "Counter-Strike: Vitality vs FUT Esports (BO3) - BLAST Rivals Group A",
            "provider_bet_id": "",
        }
    ]
    deltas = reconcile_from_history([bet], history)
    assert len(deltas) == 1
    assert deltas[0].changes["result"] == "lost"


def test_fuzzy_match_skips_open_entry_with_zero_odds():
    """Non-terminal entries with odds=0 still get skipped — matching an open
    bet by name without odds confirmation is meaningless."""
    bet = {"id": 500, "stake": 10, "odds": 2.0, "home_team": "X", "away_team": "Y"}
    history = [{"status": "pending", "stake": 0, "odds": 0, "event_name": "X v Y"}]
    deltas = reconcile_from_history([bet], history)
    assert deltas == []


def test_fallback_no_match_when_window_empty():
    bet = {
        "id": 1000,
        "stake": 100,
        "odds": 2.0,
        "home_team": "X",
        "away_team": "Y",
        "start_time": "2026-04-13T15:30:00Z",
    }
    targeted_history = []
    deltas = reconcile_from_history([bet], targeted_history)
    assert deltas == []


# ---------------------------------------------------------------------------
# market+outcome disambiguation — an arb_counter (moneyline) and a mirror
# (spread) leg on the SAME event at the SAME odds must NOT cross-match. The
# arb_counter is recorded with provider_bet_id=None and depends on reconcile
# backfill; market-blind fuzzy/signature matching stamped it with the sibling
# leg's wagerNumber (live bug: pinnacle 2239139046 → bets 822 moneyline +
# 823 spread; same pattern on polymarket 627/810). reconcile must require
# market+outcome agreement when BOTH sides carry the data, and stay lenient
# (match as before) when either side omits it.
# ---------------------------------------------------------------------------


def test_fuzzy_rejects_market_mismatch():
    """A moneyline bet must not fuzzy-match a spread history entry on the same
    event at the same odds."""
    db = [
        {
            "id": 10,
            "provider_bet_id": None,
            "market": "moneyline",
            "outcome": "home",
            "stake": 28.89,
            "odds": 2.97,
            "event_name": "Bendigo Braves vs Waverley Falcons",
        }
    ]
    history = [
        {
            "provider_bet_id": "2239139046",
            "market": "spread",
            "outcome": "home",
            "status": "lost",
            "stake": 29,
            "odds": 2.97,
            "payout": 0,
            "event_name": "Bendigo Braves vs Waverley Falcons",
        }
    ]
    deltas = reconcile_from_history(db, history)
    assert deltas == []


def test_fuzzy_rejects_outcome_mismatch():
    """Same event, same market, same odds, opposite outcome → no match."""
    db = [
        {
            "id": 11,
            "provider_bet_id": None,
            "market": "spread",
            "outcome": "home",
            "stake": 15,
            "odds": 8.37,
            "event_name": "Manly Warringah Sea Eagles vs Hills Hornets",
        }
    ]
    history = [
        {
            "provider_bet_id": "2238830388",
            "market": "spread",
            "outcome": "away",
            "status": "lost",
            "stake": 15,
            "odds": 8.37,
            "payout": 0,
            "event_name": "Manly Warringah Sea Eagles vs Hills Hornets",
        }
    ]
    deltas = reconcile_from_history(db, history)
    assert deltas == []


def test_fuzzy_allows_when_history_market_absent():
    """Leniency: history rows that don't carry market/outcome (many providers)
    still match by name+odds — we must not regress those."""
    db = [
        {
            "id": 12,
            "provider_bet_id": None,
            "market": "moneyline",
            "outcome": "home",
            "stake": 130,
            "odds": 5.60,
            "home_team": "Sambenedettese",
            "away_team": "Pontedera",
        }
    ]
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
    assert deltas[0].changes["result"] == "lost"


def test_fuzzy_treats_1x2_and_moneyline_as_equivalent():
    """1x2 and moneyline are the same market in different provider vocab —
    must not be treated as a conflict."""
    db = [
        {
            "id": 13,
            "provider_bet_id": None,
            "market": "1x2",
            "outcome": "away",
            "stake": 20,
            "odds": 8.59,
            "event_name": "Francisco Cerundolo vs Zachary Svajda",
        }
    ]
    history = [
        {
            "provider_bet_id": "X",
            "market": "moneyline",
            "outcome": "away",
            "status": "won",
            "stake": 20,
            "odds": 8.59,
            "payout": 171.8,
            "event_name": "Francisco Cerundolo vs Zachary Svajda",
        }
    ]
    deltas = reconcile_from_history(db, history)
    assert len(deltas) == 1
    assert deltas[0].changes["result"] == "won"


def test_signature_rejects_market_mismatch():
    """Pass-3 signature fallback (odds+stake, no id, no event_name) must also
    refuse to settle across a market mismatch."""
    db = [
        {
            "id": 14,
            "provider_bet_id": "",
            "boost_event": "",
            "market": "moneyline",
            "outcome": "home",
            "stake": 32,
            "odds": 1.909,
        }
    ]
    history = [
        {
            "provider_bet_id": "2239139682",
            "market": "spread",
            "outcome": "away",
            "status": "lost",
            "stake": 32,
            "odds": 1.909,
            "payout": 0,
            "event_name": "Dorados de Chihuahua vs Rieleros de Aguascalientes",
        }
    ]
    deltas = reconcile_from_history(db, history)
    assert deltas == []


def test_shared_pid_settles_only_matching_market():
    """Two DB bets that (due to a prior bad backfill) share one provider_bet_id
    but differ in market: the id-keyed Pass 1 must settle ONLY the leg whose
    market+outcome matches the history entry, not both."""
    db = [
        {
            "id": 822,
            "provider_bet_id": "2239139046",
            "market": "moneyline",
            "outcome": "home",
            "stake": 28.89,
            "odds": 2.97,
            "event_name": "Bendigo Braves vs Waverley Falcons",
        },
        {
            "id": 823,
            "provider_bet_id": "2239139046",
            "market": "spread",
            "outcome": "home",
            "stake": 29,
            "odds": 2.97,
            "event_name": "Bendigo Braves vs Waverley Falcons",
        },
    ]
    history = [
        {
            "provider_bet_id": "2239139046",
            "market": "spread",
            "outcome": "home",
            "status": "lost",
            "stake": 29,
            "odds": 2.97,
            "payout": 0,
            "event_name": "Bendigo Braves vs Waverley Falcons",
        }
    ]
    deltas = reconcile_from_history(db, history)
    assert len(deltas) == 1
    assert deltas[0].bet_id == 823
    assert deltas[0].changes["result"] == "lost"


def test_id_match_still_settles_when_history_omits_market():
    """Leniency for Pass 1 too: an exact provider_bet_id match where the history
    row carries no market/outcome must still settle (no regression)."""
    db = [
        {
            "id": 15,
            "provider_bet_id": "ID1",
            "market": "moneyline",
            "outcome": "home",
            "stake": 100,
            "odds": 2.0,
            "event_name": "A v B",
        }
    ]
    history = [
        {
            "provider_bet_id": "ID1",
            "status": "won",
            "stake": 100,
            "odds": 2.0,
            "payout": 200,
            "event_name": "A v B",
        }
    ]
    deltas = reconcile_from_history(db, history)
    assert len(deltas) == 1
    assert deltas[0].changes["result"] == "won"
