"""Audit #50: BrokerTrade.profile_id + StockSignal.trade_id FK constraints.

Both columns referenced other tables by convention but had no enforced FK,
so orphan rows could accumulate undetected. The fix adds ForeignKey to the
ORM (so create_all picks them up on fresh DBs) and ALTER TABLE migrations
in _run_pg_migrations for the existing prod tables.
"""

from sqlalchemy import inspect


def _fk_for(inspector, table: str, col: str) -> dict | None:
    for fk in inspector.get_foreign_keys(table):
        if col in fk["constrained_columns"]:
            return fk
    return None


def test_broker_trades_profile_id_has_fk(db_session):
    inspector = inspect(db_session.bind)
    fk = _fk_for(inspector, "broker_trades", "profile_id")
    assert fk is not None, "broker_trades.profile_id should have a FK"
    assert fk["referred_table"] == "profiles"
    assert fk["referred_columns"] == ["id"]


def test_stock_signals_trade_id_has_fk(db_session):
    inspector = inspect(db_session.bind)
    fk = _fk_for(inspector, "stock_signals", "trade_id")
    assert fk is not None, "stock_signals.trade_id should have a FK"
    assert fk["referred_table"] == "broker_trades"
    assert fk["referred_columns"] == ["id"]
