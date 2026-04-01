"""
One-time migration: SQLite → PostgreSQL.

Reads all data from SQLite firev.db and inserts into PostgreSQL.
Run with both databases accessible:

    SQLITE_PATH=/tmp/firev.db DATABASE_URL=postgresql+asyncpg://firev:pw@postgres:5432/firev \
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

# Tables to migrate (in FK dependency order)
TABLES = [
    "providers",
    "profiles",
    "events",
    "odds",
    "bets",
    "profile_provider_bonuses",
    "profile_provider_balances",
    "profile_provider_limits",
    "opportunities",
    "bet_postmortems",
    "extraction_runs",
    "provider_run_metrics",
    "sport_run_metrics",
    "deferred_events",
    "specials",
    "provider_risk_profiles",
    "provider_extraction_settings",
]


def get_pg_column_types(pg_cur, table):
    """Get column names and types for a Postgres table."""
    pg_cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = %s ORDER BY ordinal_position",
        (table,)
    )
    return {row[0]: row[1] for row in pg_cur.fetchall()}


def coerce_value(val, pg_type):
    """Coerce SQLite value to Postgres type."""
    if val is None:
        return None
    if pg_type == "boolean":
        return bool(val)
    if pg_type == "integer" and isinstance(val, str):
        try:
            return int(val)
        except (ValueError, TypeError):
            return None  # Skip invalid values like "NS" in integer columns
    return val


def migrate():
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = psycopg2.connect(PG_URL)
    pg_cur = pg_conn.cursor()

    # Disable FK checks during migration
    pg_cur.execute("SET session_replication_role = 'replica'")

    for table in TABLES:
        try:
            rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            print(f"  SKIP {table} (not in SQLite)")
            continue

        if not rows:
            print(f"  SKIP {table} (0 rows)")
            continue

        # Get Postgres column types
        pg_col_types = get_pg_column_types(pg_cur, table)
        if not pg_col_types:
            print(f"  SKIP {table} (not in Postgres)")
            continue

        sqlite_cols = rows[0].keys()
        common_cols = [c for c in sqlite_cols if c in pg_col_types]
        if not common_cols:
            print(f"  SKIP {table} (no common columns)")
            continue

        col_str = ", ".join(common_cols)
        template = "(" + ", ".join(["%s"] * len(common_cols)) + ")"

        # Truncate target first
        pg_cur.execute(f"TRUNCATE {table} CASCADE")

        # Coerce values to match Postgres types
        values = []
        skipped = 0
        for row in rows:
            coerced = []
            skip = False
            for c in common_cols:
                v = coerce_value(row[c], pg_col_types[c])
                coerced.append(v)
            values.append(tuple(coerced))

        try:
            execute_values(
                pg_cur,
                f"INSERT INTO {table} ({col_str}) VALUES %s",
                values,
                template=template,
                page_size=1000,
            )
            pg_conn.commit()
            print(f"  OK {table}: {len(values)} rows")
        except Exception as e:
            pg_conn.rollback()
            err = str(e).split("\n")[0]
            print(f"  FAIL {table}: {err}")

    # Re-enable FK checks
    pg_cur.execute("SET session_replication_role = 'origin'")
    pg_conn.commit()

    sqlite_conn.close()
    pg_conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()
