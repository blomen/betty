"""
One-time migration: SQLite → PostgreSQL.

Reads all data from SQLite firev.db and inserts into PostgreSQL.
Run with both databases accessible:

    DATABASE_URL=postgresql+psycopg2://firev:pw@localhost:5432/firev \
    python scripts/migrate_sqlite_to_postgres.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
import psycopg2
from psycopg2.extras import execute_values

SQLITE_PATH = os.environ.get("SQLITE_PATH", "data/firev.db")
PG_URL = os.environ["DATABASE_URL"].replace("+asyncpg", "").replace("+psycopg2", "")
# Parse PG_URL: postgresql://user:pw@host:port/db
# psycopg2 accepts this format directly

# Tables to migrate in dependency order (FKs)
TABLES = [
    "provider",
    "profile",
    "event",
    "odds",
    "bet",
    "profile_provider_bonuses",
    "profile_provider_balances",
    "profile_provider_limits",
    "opportunity",
    "bet_postmortem",
    "extraction_runs",
    "provider_run_metrics",
    "sport_run_metrics",
    "deferred_events",
    "special_odds",
    "provider_risk_profiles",
]


def migrate():
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = psycopg2.connect(PG_URL)
    pg_cur = pg_conn.cursor()

    for table in TABLES:
        try:
            rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            print(f"  SKIP {table} (not in SQLite)")
            continue

        if not rows:
            print(f"  SKIP {table} (0 rows)")
            continue

        cols = rows[0].keys()
        col_str = ", ".join(cols)
        template = "(" + ", ".join(["%s"] * len(cols)) + ")"

        # Truncate target first
        pg_cur.execute(f"TRUNCATE {table} CASCADE")

        # Batch insert
        values = [tuple(row) for row in rows]
        execute_values(
            pg_cur,
            f"INSERT INTO {table} ({col_str}) VALUES %s",
            values,
            template=template,
            page_size=1000,
        )
        pg_conn.commit()
        print(f"  OK {table}: {len(rows)} rows")

    sqlite_conn.close()
    pg_conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()
