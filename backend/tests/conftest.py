"""Shared test fixtures for Betty tests."""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base


@pytest.fixture(autouse=True)
def _clear_profile_bankroll_caches():
    """Clear ProfileRepo's module-global bankroll/stake caches before each test.

    These caches are keyed only by profile_id with a 30s TTL. Tests use fresh
    in-memory DBs that all reuse profile_id=1, so a value cached by one test can
    leak into the next (e.g. a 0 bankroll masking a freshly-set balance). Clearing
    per-test makes balance-dependent tests order-independent. No production impact.
    """
    from src.repositories import profile_repo as _pr

    _pr._bankroll_cache.clear()
    _pr._stake_bankroll_cache.clear()
    yield


@pytest.fixture
def db_session():
    """Database session — uses Postgres if DATABASE_URL set, else in-memory SQLite."""
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        sync_url = db_url.replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)
    else:
        engine = create_engine("sqlite:///:memory:")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

    if db_url:
        # Clean up tables after test
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    engine.dispose()
