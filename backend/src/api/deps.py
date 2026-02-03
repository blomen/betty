"""FastAPI dependencies."""

from ..db.models import get_session

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


def get_pipeline():
    """Get or create pipeline singleton."""
    global _pipeline_instance
    if _pipeline_instance is None:
        from ..pipeline import ExtractionPipeline
        _pipeline_instance = ExtractionPipeline()
    return _pipeline_instance
