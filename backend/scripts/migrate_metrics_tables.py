"""Create metrics tables for extraction run persistence."""
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models import Base, init_db, ExtractionRun, ProviderRunMetrics, SportRunMetrics


def migrate():
    """Create metrics tables."""
    print("Creating metrics tables...")
    engine = init_db()

    # Create only the new tables
    Base.metadata.create_all(engine, tables=[
        ExtractionRun.__table__,
        ProviderRunMetrics.__table__,
        SportRunMetrics.__table__
    ])

    print(" - extraction_runs")
    print(" - provider_run_metrics")
    print(" - sport_run_metrics")
    print("Migration complete")


if __name__ == "__main__":
    migrate()
