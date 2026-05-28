"""Odds.max_stake column is present after init_db."""

from sqlalchemy import create_engine, inspect

from src.db.models import Base


def test_odds_max_stake_column_present():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("odds")}
    assert "max_stake" in cols
