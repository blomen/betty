"""Regression tests: TradingRepo.get_trade eager-load is now opt-in.

Default mode (`with_relationships=False`) returns just the Trade row;
`with_relationships=True` eager-loads account/review/events for the
serialiser path.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.db.models import (
    Base,
    Trade,
    TradeEvent,
    TradeReview,
    TradingAccount,
)
from src.repositories.trading_repo import TradingRepo


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def trade_with_relations(db):
    acct = TradingAccount(name="acct1", account_type="demo", balance=10000.0, equity=10000.0)
    db.add(acct)
    db.commit()
    trade = Trade(
        account_id=acct.id,
        instrument="NQ",
        direction="long",
        setup_type="trend",
        contracts=1,
        state="open",
        entry_price=100.0,
        stop_price=99.0,
        risk_amount=100.0,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(trade)
    db.commit()
    # Add 5 events + 1 review
    for i in range(5):
        db.add(TradeEvent(trade_id=trade.id, event_type="transition", to_state=f"s{i}"))
    db.add(TradeReview(trade_id=trade.id, grade="A"))
    db.commit()
    return trade.id


def _count_queries(db) -> list:
    """Hook into the engine to count SQL statements during a block."""
    queries = []

    def _on_execute(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(db.bind, "before_cursor_execute", _on_execute)
    return queries


def test_default_returns_trade_row_without_eager_loading(db, trade_with_relations):
    """Default get_trade emits 1 query for the trade row only."""
    db.expire_all()
    repo = TradingRepo(db)
    queries = _count_queries(db)
    trade = repo.get_trade(trade_with_relations)
    assert trade is not None
    assert trade.entry_price == 100.0
    # Exactly one SELECT — no JOIN to events/review/account
    assert len(queries) == 1, f"Expected 1 query, got {len(queries)}: {queries}"
    assert "JOIN" not in queries[0].upper()


def test_with_relationships_uses_selectinload_for_events(db, trade_with_relations):
    """`with_relationships=True` returns trade + relationships pre-loaded.

    The crucial property: events use selectinload (separate query, no row
    multiplication) — the prior `joinedload(Trade.events)` would cartesian
    the trade row 5x for 5 events.
    """
    db.expire_all()
    repo = TradingRepo(db)
    queries = _count_queries(db)
    trade = repo.get_trade(trade_with_relations, with_relationships=True)
    assert trade is not None
    # Access the relationships — should NOT trigger more queries
    queries_before_access = len(queries)
    assert trade.account is not None
    assert trade.review is not None
    assert len(trade.events) == 5
    queries_after_access = len(queries)
    assert queries_before_access == queries_after_access, (
        f"Lazy loads fired on access: {queries[queries_before_access:]}"
    )

    # Should be 2 queries: main (with joinedload account/review) + selectinload events
    # SQLite execution plan exposed via `before_cursor_execute` may also include
    # ROLLBACK / BEGIN; we only count SELECTs against our tables here.
    selects = [q for q in queries if q.lstrip().upper().startswith("SELECT")]
    assert 1 <= len(selects) <= 3, f"Expected 1-3 SELECTs, got {len(selects)}: {selects}"


def test_with_relationships_no_cartesian_fanout(db, trade_with_relations):
    """The main query must NOT contain a JOIN to trade_events.

    Regression: `joinedload(Trade.events)` produced one row per event
    (5 events → 5 trade rows). selectinload moves events to a separate
    query, eliminating the row multiplication.
    """
    db.expire_all()
    repo = TradingRepo(db)
    queries = _count_queries(db)
    repo.get_trade(trade_with_relations, with_relationships=True)
    main_select = next(q for q in queries if "FROM trades" in q)
    assert "trade_events" not in main_select, (
        f"Main query should not JOIN trade_events; selectinload should split it: {main_select}"
    )


def test_get_trade_returns_none_for_missing(db):
    repo = TradingRepo(db)
    assert repo.get_trade(99999) is None
    assert repo.get_trade(99999, with_relationships=True) is None
