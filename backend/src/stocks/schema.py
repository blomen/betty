"""Database tables for recorded market data (ticks + L2 depth)."""

from __future__ import annotations

import logging

from sqlalchemy import text

log = logging.getLogger(__name__)


def ensure_recording_tables(db_session_factory) -> None:
    """Create recording tables if they don't exist.

    Called on arnoldstocks startup. Uses the market database.
    """
    db = db_session_factory()
    try:
        db.execute(
            text("""
            CREATE TABLE IF NOT EXISTS recorded_ticks (
                id BIGSERIAL PRIMARY KEY,
                symbol VARCHAR(10) NOT NULL DEFAULT 'NQ',
                price DOUBLE PRECISION NOT NULL,
                size INTEGER NOT NULL,
                ts TIMESTAMPTZ NOT NULL
            )
        """)
        )
        db.execute(
            text("""
            CREATE INDEX IF NOT EXISTS idx_recorded_ticks_ts
            ON recorded_ticks (ts)
        """)
        )
        db.execute(
            text("""
            CREATE TABLE IF NOT EXISTS recorded_depth (
                id BIGSERIAL PRIMARY KEY,
                symbol VARCHAR(10) NOT NULL DEFAULT 'NQ',
                price DOUBLE PRECISION NOT NULL,
                volume INTEGER NOT NULL,
                current_volume INTEGER NOT NULL,
                side VARCHAR(3) NOT NULL,
                ts TIMESTAMPTZ NOT NULL
            )
        """)
        )
        db.execute(
            text("""
            CREATE INDEX IF NOT EXISTS idx_recorded_depth_ts
            ON recorded_depth (ts)
        """)
        )
        db.commit()
        log.info("Recording tables ready (recorded_ticks, recorded_depth)")
    except Exception:
        log.exception("Failed to create recording tables")
        db.rollback()
    finally:
        db.close()
