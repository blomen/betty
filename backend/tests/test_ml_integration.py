"""End-to-end smoke test: feature extraction -> store -> query."""
from datetime import datetime, timezone, timedelta


def test_betting_feature_e2e(db_session):
    """Full flow: extract features -> store -> resolve -> query training data."""
    from src.ml.features.betting_features import extract_betting_features
    from src.ml.feature_store import log_features, resolve_outcome, get_training_data

    # 1. Extract features (as scanner would)
    features = extract_betting_features(
        edge_pct=8.0,
        provider_odds=2.15,
        fair_odds=1.99,
        fair_probability=0.503,
        provider="betsson",
        sport="football",
        market="1x2",
        event_id="evt-100",
        prob_sum=1.02,
        odds_by_outcome={"home": [
            {"provider": "pinnacle", "odds": 1.99, "updated_at": datetime.now(timezone.utc).isoformat()},
            {"provider": "betsson", "odds": 2.15, "updated_at": datetime.now(timezone.utc).isoformat()},
        ]},
        pinnacle_overround=0.025,
        event_start_time=datetime.now(timezone.utc) + timedelta(hours=2),
    )

    # 2. Store
    log_features(db_session, "betting", "opp-100", "opportunity", features)

    # 3. Resolve outcome (simulate: bet won, CLV was positive)
    resolve_outcome(db_session, "opportunity", "opp-100", outcome=0.05, outcome_binary=1)

    # 4. Query training data
    data = get_training_data(db_session, "betting", "opportunity")
    assert len(data) == 1
    assert data[0].features["edge_pct"] == 8.0
    assert data[0].outcome == 0.05


def test_trading_feature_e2e(db_session):
    """Full flow: extract trading features -> store -> resolve."""
    from src.ml.features.trading_features import extract_trading_features
    from src.ml.feature_store import log_features, resolve_outcome, get_training_data

    features = extract_trading_features(
        setup_type="spring",
        direction="long",
        delta=380,
        delta_pct=0.089,
        volume_ratio_vs_20bar=1.45,
        distance_to_level_ticks=3,
        minutes_since_rth_open=45,
    )

    log_features(db_session, "trading", "sig-50", "signal", features)
    resolve_outcome(db_session, "signal", "sig-50", outcome=2.5, outcome_binary=1)

    data = get_training_data(db_session, "trading", "signal")
    assert len(data) == 1
    assert data[0].features["setup_type"] == "spring"
    assert data[0].outcome == 2.5


def test_pinnacle_coverage_compute():
    """Verify coverage delta computation."""
    from src.ml.features.pinnacle_coverage import compute_coverage_delta

    delta = compute_coverage_delta(
        pinnacle_events=200, pinnacle_ml=200, pinnacle_spread=150, pinnacle_total=180,
        provider_matched=130, provider_ml=130, provider_spread=60, provider_total=90,
    )
    assert delta["event_coverage_pct"] == 65.0
    assert delta["spread_coverage_pct"] == 40.0
    assert delta["total_coverage_pct"] == 50.0
    assert delta["missing_events"] == 70
    assert delta["missing_spread"] == 90
    assert delta["missing_total"] == 90
