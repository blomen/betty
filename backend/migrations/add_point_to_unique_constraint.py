"""
Migration: Add point to odds unique constraint

Drops existing constraint and recreates with point column.
"""
from sqlalchemy import create_engine, text
from pathlib import Path


def migrate():
    """Run migration to add point to unique constraint."""
    # Get database path
    db_path = Path(__file__).parent.parent / "data" / "oddopp.db"
    engine = create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
        # Drop old constraint (SQLite uses DROP INDEX for unique constraints)
        try:
            conn.execute(text("DROP INDEX IF EXISTS uq_odds"))
            print("Dropped old constraint 'uq_odds'")
        except Exception as e:
            print(f"Note: Could not drop old constraint (may not exist): {e}")

        # Create new unique index with point
        try:
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_odds_with_point
                ON odds(event_id, provider_id, market, outcome, point)
            """))
            print("Created new constraint 'uq_odds_with_point'")
        except Exception as e:
            print(f"Error creating new constraint: {e}")
            raise

    print("Migration completed successfully")


if __name__ == "__main__":
    migrate()
