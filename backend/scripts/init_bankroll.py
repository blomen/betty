#!/usr/bin/env python3
"""
Initialize bankroll with default balances for testing.

Sets common providers to $500 each for development/testing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models import init_db, get_session, Provider
from datetime import datetime


DEFAULT_BALANCE = 500.0

# Common providers to initialize
DEFAULT_PROVIDERS = [
    "unibet",
    "leovegas",
    "casumo",
    "betsson",
    "mrgreen",
    "888sport",
]


def init_default_bankroll():
    """Initialize default balances for common providers."""
    init_db()
    db = get_session()

    print("Initializing default bankroll...")
    print(f"Setting ${DEFAULT_BALANCE:.2f} for {len(DEFAULT_PROVIDERS)} providers\n")

    total = 0
    updated = 0

    for provider_id in DEFAULT_PROVIDERS:
        provider = db.query(Provider).filter(Provider.id == provider_id).first()

        if provider:
            old_balance = provider.balance
            provider.balance = DEFAULT_BALANCE
            provider.updated_at = datetime.utcnow()
            total += DEFAULT_BALANCE
            updated += 1
            print(f"  [OK] {provider_id:<15} ${old_balance:.2f} -> ${DEFAULT_BALANCE:.2f}")
        else:
            print(f"  [SKIP] {provider_id:<15} (not found in database)")

    db.commit()

    print(f"\nDone!")
    print(f"Updated: {updated}/{len(DEFAULT_PROVIDERS)} providers")
    print(f"Total bankroll: ${total:.2f}")

    db.close()


if __name__ == "__main__":
    init_default_bankroll()
