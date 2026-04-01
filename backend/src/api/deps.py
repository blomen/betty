"""FastAPI dependencies."""

import logging

from ..db.models import get_session

logger = logging.getLogger(__name__)

_pipeline_instance = None


def get_db():
    """Database session dependency. Routes use this."""
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
    """Write-heavy session dependency (no auto-commit)."""
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
