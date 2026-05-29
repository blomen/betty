"""Verify the opp_snapshots migration creates the expected schema."""

from sqlalchemy import create_engine, inspect


def test_opp_snapshots_table_has_required_columns():
    """After Base.metadata.create_all, opp_snapshots exists with all design columns."""
    from src.db.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    assert "opp_snapshots" in inspector.get_table_names()

    cols = {c["name"] for c in inspector.get_columns("opp_snapshots")}
    required = {
        "id",
        "event_id",
        "type",
        "market",
        "outcome1",
        "point",
        "scope",
        "provider1_id",
        "odds1_at_detection",
        "fair_odds1_at_detection",
        "edge_pct_at_detection",
        "provider2_id",
        "outcome2",
        "odds2_at_detection",
        "first_detected_at",
        "last_detected_at",
        "detection_count",
        "time_to_start_minutes_at_detection",
        "provider1_closing_odds",
        "provider1_closing_age_minutes",
        "provider2_closing_odds",
        "provider2_closing_age_minutes",
        "pinnacle_closing_fair",
        "pinnacle_closing_age_minutes",
        "provider_clv_pct",
        "pinnacle_clv_pct",
        "closing_prob_sum",
        "was_arb_at_close",
        "clv_computed_at",
    }
    missing = required - cols
    assert not missing, f"Missing columns: {missing}"


def test_opp_snapshots_unique_constraint():
    """Unique on (event_id, market, outcome1, provider1_id, type, scope)."""
    from src.db.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    uniques = inspector.get_unique_constraints("opp_snapshots")
    expected_cols = {"event_id", "market", "outcome1", "provider1_id", "type", "scope"}
    assert any(set(u["column_names"]) == expected_cols for u in uniques), (
        f"Missing unique constraint on {expected_cols}; got {uniques}"
    )
