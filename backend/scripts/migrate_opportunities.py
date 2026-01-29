#!/usr/bin/env python3
"""
Migration script to add new columns to the Opportunity table.

Adds:
- outcomes (JSON): Flexible multi-outcome storage
- point (FLOAT): Line value for spread/totals
- total_stake (FLOAT): Recommended total stake

Usage:
    python scripts/migrate_opportunities.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "oddopp.db"


def migrate():
    """Add new columns to opportunities table if they don't exist."""
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        print("Run the extraction pipeline first to create the database.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get existing columns
    cursor.execute("PRAGMA table_info(opportunities)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    migrations = []

    # Add outcomes column (JSON stored as TEXT in SQLite)
    if "outcomes" not in existing_cols:
        cursor.execute("ALTER TABLE opportunities ADD COLUMN outcomes TEXT")
        migrations.append("outcomes")

    # Add point column
    if "point" not in existing_cols:
        cursor.execute("ALTER TABLE opportunities ADD COLUMN point REAL")
        migrations.append("point")

    # Add total_stake column
    if "total_stake" not in existing_cols:
        cursor.execute("ALTER TABLE opportunities ADD COLUMN total_stake REAL")
        migrations.append("total_stake")

    conn.commit()
    conn.close()

    if migrations:
        print(f"Migration complete. Added columns: {', '.join(migrations)}")
    else:
        print("No migration needed. All columns already exist.")


if __name__ == "__main__":
    migrate()
