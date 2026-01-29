#!/usr/bin/env python3
"""
Migrate odds table to include 'point' in unique constraint.

This migration:
1. Creates a new odds table with the correct constraint
2. Copies data from old table (deduplicating where needed)
3. Replaces old table with new one

Run from backend directory:
    python scripts/migrate_odds_constraint.py
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "oddopp.db"


def migrate():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    print(f"Migrating database: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Check current schema
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='odds'")
        current_schema = cursor.fetchone()
        if current_schema:
            print(f"Current schema:\n{current_schema[0][:200]}...")

        # Check if migration already done by looking at table schema
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='odds'")
        schema = cursor.fetchone()[0]
        if "uq_odds UNIQUE (event_id, provider_id, market, outcome)" not in schema:
            print("Migration already applied (old constraint not found)")
            return
        print("Found old 4-column constraint, proceeding with migration...")

        print("\nStep 1: Creating new odds table with correct constraint...")
        cursor.execute("""
            CREATE TABLE odds_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id VARCHAR NOT NULL,
                provider_id VARCHAR NOT NULL,
                market VARCHAR NOT NULL,
                outcome VARCHAR NOT NULL,
                odds FLOAT NOT NULL,
                point FLOAT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (event_id) REFERENCES events(id),
                FOREIGN KEY (provider_id) REFERENCES providers(id),
                UNIQUE (event_id, provider_id, market, outcome, point)
            )
        """)

        print("Step 2: Copying data (keeping latest for duplicates)...")
        # Group by the new key and take the row with max updated_at
        cursor.execute("""
            INSERT INTO odds_new (event_id, provider_id, market, outcome, odds, point, updated_at)
            SELECT event_id, provider_id, market, outcome, odds, point, updated_at
            FROM odds
            WHERE id IN (
                SELECT MAX(id)
                FROM odds
                GROUP BY event_id, provider_id, market, outcome, COALESCE(point, -999999)
            )
        """)
        rows_copied = cursor.rowcount
        print(f"   Copied {rows_copied} rows")

        # Count how many were duplicates
        cursor.execute("SELECT COUNT(*) FROM odds")
        total_old = cursor.fetchone()[0]
        duplicates = total_old - rows_copied
        if duplicates > 0:
            print(f"   Removed {duplicates} duplicate rows")

        print("Step 3: Replacing old table...")
        cursor.execute("DROP TABLE odds")
        cursor.execute("ALTER TABLE odds_new RENAME TO odds")

        print("Step 4: Creating indexes...")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_odds_event_id ON odds (event_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_odds_provider_id ON odds (provider_id)")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_odds_with_point ON odds (event_id, provider_id, market, outcome, point)")

        conn.commit()
        print("\nMigration complete!")

    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
