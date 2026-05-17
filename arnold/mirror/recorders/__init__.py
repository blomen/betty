"""API-based bet recorders.

Replaces DOM-scraping for providers that expose proper user portfolio APIs:
- Polymarket: public data-api.polymarket.com/positions (wallet-keyed, no auth)
- Kalshi:    authenticated trade-api.kalshi.com/portfolio/positions (RSA-signed)

Each recorder produces RecorderResult{fetched, inserted, skipped, errors} and
inserts via /api/bets with external_placement=True. Idempotent — provider_bet_id
+ event_id dedup guards against double-insert.
"""

from .types import RecorderResult, RecoveredPosition

__all__ = ["RecorderResult", "RecoveredPosition"]
