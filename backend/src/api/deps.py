"""FastAPI dependencies."""

import logging
from typing import AsyncGenerator

from sqlalchemy.orm import Session
from ..db.models import get_session, get_async_session_factory, _is_postgres

logger = logging.getLogger(__name__)

_pipeline_instance = None


# Async dependency (for Postgres)
async def get_db_async() -> AsyncGenerator:
    """Async database session dependency for PostgreSQL."""
    factory = get_async_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Sync dependency (for SQLite fallback)
def get_db_sync():
    """Sync database session dependency for SQLite."""
    db = get_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_writer_sync():
    """Sync session for write-heavy routes (no auto-commit)."""
    db = get_session()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Route-facing dependencies — pick async or sync based on config
def get_db():
    """Database session dependency. Routes use this."""
    if _is_postgres():
        return get_db_async()
    return get_db_sync()


def get_db_writer():
    """Write-heavy session dependency. Routes use this."""
    if _is_postgres():
        return get_db_async()  # Postgres doesn't need special write handling
    return get_db_writer_sync()


def get_pipeline():
    """Get or create pipeline singleton."""
    global _pipeline_instance
    if _pipeline_instance is None:
        from ..pipeline import ExtractionPipeline
        _pipeline_instance = ExtractionPipeline()
    return _pipeline_instance
