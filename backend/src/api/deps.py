"""FastAPI dependencies."""

import logging

from ..db.models import get_session

logger = logging.getLogger(__name__)

# Global pipeline instance for accessing metrics/circuit breaker/cache
_pipeline_instance = None


def get_db():
    """
    Database session dependency with proper lifecycle management.

    - Creates a new session per request
    - Commits on success (no exceptions)
    - Rolls back on error
    - Always closes the session
    """
    db = get_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_writer():
    """Database session for write-heavy routes (bet placement).

    Does NOT auto-commit — the route handles commit + retry itself.
    This prevents SQLite lock contention from silently losing writes
    (extraction bulk-inserts hold write locks for seconds at a time).
    """
    db = get_session()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_pipeline():
    """Get or create pipeline singleton."""
    global _pipeline_instance
    if _pipeline_instance is None:
        from ..pipeline import ExtractionPipeline
        _pipeline_instance = ExtractionPipeline()
    return _pipeline_instance
