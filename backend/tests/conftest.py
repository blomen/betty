"""Shared test fixtures for Betty tests."""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base


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
