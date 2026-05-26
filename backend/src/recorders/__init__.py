"""Server-side API-based bet recorders.

Records user positions from provider portfolio APIs that need no browser:
- Polymarket: public data-api.polymarket.com (wallet-keyed, no auth)

Runs 24/7 as a backend asyncio task (see server_poller.run_position_recorder)
so positions are recorded whether or not the local betty.bat client is open.
"""

from .types import RecorderResult, RecoveredPosition

__all__ = ["RecorderResult", "RecoveredPosition"]
