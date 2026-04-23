import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .transport import HttpTransport, Transport

logger = logging.getLogger(__name__)


@dataclass
class StandardEvent:
    id: str
    name: str  # Home vs Away or Event Name
    sport: str
    markets: list[dict]  # Normalized markets [{"type": "moneyline", "outcomes": [...]}]
    provider: str
    url: str = ""
    start_time: str = ""
    home_team: str = ""
    away_team: str = ""
    league: str = ""
    live_state: dict = field(default_factory=dict)  # Pinnacle live data: scores, minute, period, stats


class Retriever(ABC):
    """
    Modular Extractor that separates Transport (fetching) from Parsing.
    """

    def __init__(self, config: dict, transport: Transport = None):
        self.config = config
        self.provider_id = config.get("id", "unknown")
        # Default to HTTP Transport if none provided
        self.transport = transport or HttpTransport()

    @abstractmethod
    def _get_sport_url(self, sport: str) -> str:
        """Resolve sport name to URL/Path."""
        pass

    @abstractmethod
    def parse(self, data: Any, sport: str) -> list[StandardEvent]:
        """Parse raw data into StandardEvents."""
        pass

    async def extract(self, sport: str, limit: int = 50, **kwargs) -> list[StandardEvent]:
        url = self._get_sport_url(sport)
        # Some retrievers (like Kambi) might handle fetching internally in extract
        # If _get_sport_url returns empty, we assume extract handled it or it's invalid.

        if not url:
            # Check if subclass overrides extract completely without using _get_sport_url
            # If we are here and have no URL, we might skip.
            # But let's assume if it returns None/Empty for a simple retriever, it's a no-op.
            pass

        data = None
        if url:
            data = await self.transport.get(url)

        if not data and not url:
            # Subclass might have custom fetch logic,
            # but if this base method is called, we expect URL or data.
            return []

        # Parse
        events = self.parse(data, sport)

        # Limit
        if limit and len(events) > limit:
            events = events[:limit]

        return events

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        await self.transport.close()
