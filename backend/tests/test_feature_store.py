"""Test ML feature store read/write operations."""
from datetime import datetime, timezone


def test_log_betting_features(db_session):
    from src.ml.feature_store import log_features
    log_features(
        session=db_session,
        domain="betting",
        source_id="opp-42",
        source_type="opportunity",
        features={"edge_pct": 7.5, "prob_sum": 1.02, "odds_ratio": 1.05},
        feature_version=1,
    )
    from src.db.models import MlFeature
    row = db_session.query(MlFeature).first()
    assert row is not None
    assert row.domain == "betting"
    assert row.features["edge_pct"] == 7.5
    assert row.outcome is None


def test_resolve_outcome(db_session):
    from src.ml.feature_store import log_features, resolve_outcome
    log_features(db_session, "betting", "opp-42", "opportunity", {"edge_pct": 7.5}, 1)
    resolve_outcome(db_session, "opportunity", "opp-42", outcome=0.03, outcome_binary=1)
    from src.db.models import MlFeature
    row = db_session.query(MlFeature).first()
    assert row.outcome == 0.03
    assert row.outcome_binary == 1
    assert row.resolved_at is not None


def test_get_training_data(db_session):
    from src.ml.feature_store import log_features, resolve_outcome, get_training_data
    for i in range(3):
        log_features(db_session, "betting", f"opp-{i}", "opportunity", {"edge_pct": 5.0 + i}, 1)
    resolve_outcome(db_session, "opportunity", "opp-0", outcome=0.02, outcome_binary=1)
    resolve_outcome(db_session, "opportunity", "opp-1", outcome=-0.01, outcome_binary=0)
    data = get_training_data(db_session, domain="betting", source_type="opportunity")
    assert len(data) == 2
    assert all(row.outcome is not None for row in data)


def test_log_candle_snapshot(db_session):
    from src.db.models import TradingSignal, MarketSession
    ms = MarketSession(symbol="NQ", date="2026-03-12")
    db_session.add(ms)
    db_session.flush()
    sig = TradingSignal(session_id=ms.id, setup_type="spring", score=75.0)
    db_session.add(sig)
    db_session.flush()
    from src.ml.feature_store import log_candle_snapshot
    candles = [{"ts": f"2026-03-12T15:{i:02d}:00Z", "delta": 100 + i, "volume": 4000} for i in range(20)]
    log_candle_snapshot(db_session, signal_id=sig.id, candles=candles, timeframe="1m")
    from src.db.models import CandleSnapshot
    row = db_session.query(CandleSnapshot).first()
    assert row is not None
    assert len(row.candles) == 20


def test_resolve_boost_outcomes_won(db_session):
    """Settling a boost bet as 'won' should set outcome_binary=1 on matching ml_features."""
    from src.ml.feature_store import log_features, resolve_boost_outcomes
    from src.db.models import Bet, Profile, Provider
    db_session.add(Provider(id="unibet", name="Unibet"))
    profile = Profile(name="test", is_active=True, bankroll=10000)
    db_session.add(profile)
    db_session.flush()
    log_features(db_session, "betting", "Arsenal att vinna", "boost", {"llm_raw_probability": 0.45})
    bet = Bet(
        profile_id=profile.id, provider_id="unibet", market="boost",
        outcome="Arsenal att vinna", odds=3.0, stake=100, bet_type="boost",
        result="won", payout=300,
    )
    db_session.add(bet)
    db_session.flush()
    count = resolve_boost_outcomes(db_session, "Arsenal att vinna")
    assert count == 1
    from src.db.models import MlFeature
    row = db_session.query(MlFeature).filter_by(source_type="boost").first()
    assert row.outcome == 1.0
    assert row.outcome_binary == 1
    assert row.resolved_at is not None


def test_resolve_boost_outcomes_lost(db_session):
    from src.ml.feature_store import log_features, resolve_boost_outcomes
    from src.db.models import Bet, Profile, Provider
    db_session.add(Provider(id="unibet", name="Unibet"))
    profile = Profile(name="test", is_active=True, bankroll=10000)
    db_session.add(profile)
    db_session.flush()
    log_features(db_session, "betting", "Arsenal att vinna", "boost", {"llm_raw_probability": 0.45})
    bet = Bet(
        profile_id=profile.id, provider_id="unibet", market="boost",
        outcome="Arsenal att vinna", odds=3.0, stake=100, bet_type="boost",
        result="lost", payout=0,
    )
    db_session.add(bet)
    db_session.flush()
    count = resolve_boost_outcomes(db_session, "Arsenal att vinna")
    assert count == 1
    from src.db.models import MlFeature
    row = db_session.query(MlFeature).filter_by(source_type="boost").first()
    assert row.outcome == 0.0
    assert row.outcome_binary == 0


def test_resolve_boost_outcomes_void_deletes(db_session):
    from src.ml.feature_store import log_features, resolve_boost_outcomes
    from src.db.models import Bet, Profile, Provider, MlFeature
    db_session.add(Provider(id="unibet", name="Unibet"))
    profile = Profile(name="test", is_active=True, bankroll=10000)
    db_session.add(profile)
    db_session.flush()
    log_features(db_session, "betting", "Arsenal att vinna", "boost", {"llm_raw_probability": 0.45})
    bet = Bet(
        profile_id=profile.id, provider_id="unibet", market="boost",
        outcome="Arsenal att vinna", odds=3.0, stake=100, bet_type="boost",
        result="void", payout=100,
    )
    db_session.add(bet)
    db_session.flush()
    count = resolve_boost_outcomes(db_session, "Arsenal att vinna")
    assert count == 0
    assert db_session.query(MlFeature).filter_by(source_type="boost").count() == 0


def test_resolve_boost_no_settled_bet(db_session):
    """If no settled bet exists, features should remain unresolved."""
    from src.ml.feature_store import log_features, resolve_boost_outcomes
    log_features(db_session, "betting", "Arsenal att vinna", "boost", {"llm_raw_probability": 0.45})
    count = resolve_boost_outcomes(db_session, "Arsenal att vinna")
    assert count == 0
    from src.db.models import MlFeature
    row = db_session.query(MlFeature).filter_by(source_type="boost").first()
    assert row.outcome is None
