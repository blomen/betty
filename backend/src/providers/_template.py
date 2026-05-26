"""
Provider Template - Copy this file when adding a new provider.

This template demonstrates the standard patterns for Betty provider implementations.
Replace all instances of 'NewProvider' with your provider name.

IMPORTANT: We ONLY extract 1x2/moneyline markets. All other markets are skipped.

Integration Steps:
1. Copy this file to backend/src/providers/{provider_name}.py
2. Update class name, SPORT_MAPPING, and parsing logic
3. Register in backend/src/factory.py
4. Add config entry to config/providers.yaml
5. Test with: python scripts/test_provider.py <provider_id> --sport football

Validation Criteria (must pass before integration):
- Odds/event ratio: 2.4-3.0 (red flag if >4.0 or <2.0)
- Outcome normalization: >95% (home/away/draw)
- Score-like outcomes: 0 (correct score markets leaking)
"""

import logging
from datetime import datetime
from typing import Any

from ..core import Retriever, StandardEvent
from ..matching.normalizer import normalize_outcome, normalize_team_name
from .shared.metrics import ExtractionMetrics

logger = logging.getLogger(__name__)


class NewProviderRetriever(Retriever):
    """
    NewProvider retriever implementation.

    Replace this docstring with provider-specific documentation:
    - API base URL and authentication requirements
    - Rate limiting behavior
    - Any special handling needed

    Providers using this type:
    - List providers that share this retriever type
    """

    # Map our sport keys to provider's sport identifiers
    # Update these values based on the provider's API
    SPORT_MAPPING = {
        "football": 1,  # Replace with actual sport ID
        "basketball": 2,  # Replace with actual sport ID
        "tennis": 3,  # Replace with actual sport ID
        "ice_hockey": 4,  # Replace with actual sport ID
        # Add more sports as discovered
    }

    # Map provider's market type IDs to our standard types
    # ONLY 1x2 and moneyline are supported
    MARKET_TYPE_MAPPING = {
        1: "1x2",  # Match result (with draw) - football
        2: "moneyline",  # Winner (no draw) - basketball, etc.
        # Add provider-specific market type IDs here
    }

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the retriever.

        Args:
            config: Provider configuration from providers.yaml
        """
        super().__init__(config)

        # API configuration - pull from config dict
        self.api_base = config.get("api_base", "https://api.example.com")
        self.api_key = config.get("api_key")  # If authentication needed

        # Optional: provider-specific settings
        self.timeout = config.get("timeout", 15)

    def _get_sport_url(self, sport: str) -> str:
        """
        Get API URL for a specific sport.

        For simple REST APIs that use URL-based sport routing.
        Return "" if you override extract() completely.

        Args:
            sport: Sport key (e.g., 'football')

        Returns:
            Full API URL for the sport, or "" if not supported
        """
        sport_id = self.SPORT_MAPPING.get(sport)
        if not sport_id:
            return ""
        return f"{self.api_base}/events?sport={sport_id}"

    def parse(self, data: Any, sport: str) -> list[StandardEvent]:
        """
        Parse API response into StandardEvents.

        For simple APIs, this is called by the base extract() method.
        Override extract() instead for complex multi-step APIs.

        Args:
            data: Raw API response (JSON dict or list)
            sport: Sport being extracted

        Returns:
            List of StandardEvents (only 1x2/moneyline markets)
        """
        if not data:
            return []

        metrics = ExtractionMetrics()
        events = []

        # Adapt this based on API response structure
        raw_events = data.get("events", []) if isinstance(data, dict) else data

        for raw_event in raw_events:
            try:
                event = self._parse_single_event(raw_event, sport, metrics)
                if event:
                    events.append(event)
                    metrics.events_parsed += 1
            except Exception as e:
                logger.debug(f"[{self.provider_id}] Failed to parse event: {e}")
                metrics.events_skipped_error += 1

        metrics.log_summary(self.provider_id, sport, len(raw_events))
        return events

    def _parse_single_event(self, raw_event: dict, sport: str, metrics: ExtractionMetrics) -> StandardEvent | None:
        """
        Parse a single event from API response.

        Adapt this method to the provider's specific API structure.

        Args:
            raw_event: Single event dict from API
            sport: Sport key
            metrics: Metrics tracker for skip counts

        Returns:
            StandardEvent or None if invalid/filtered
        """
        # Skip live events
        if raw_event.get("is_live") or raw_event.get("state") == "STARTED":
            metrics.events_skipped_live += 1
            return None

        # Extract event ID
        event_id = str(raw_event.get("id", ""))
        if not event_id:
            return None

        # Extract team names - adapt to API structure
        # Common patterns:
        #   raw_event['home_team'], raw_event['away_team']
        #   raw_event['participants'][0], raw_event['participants'][1]
        #   raw_event['name'].split(' vs ')
        home_raw = raw_event.get("home_team", "")
        away_raw = raw_event.get("away_team", "")

        if not home_raw or not away_raw:
            metrics.events_skipped_no_teams += 1
            return None

        # Normalize team names
        home_team = normalize_team_name(home_raw)
        away_team = normalize_team_name(away_raw)

        # Extract league/competition
        league = raw_event.get("league", "") or raw_event.get("competition", "") or "Unknown"

        # Extract start time - adapt to API date format
        start_time = None
        start_time_raw = raw_event.get("start_time") or raw_event.get("startDate")
        if start_time_raw:
            try:
                # Common formats: ISO 8601, Unix timestamp
                if isinstance(start_time_raw, int):
                    start_time = datetime.utcfromtimestamp(start_time_raw)
                else:
                    start_time = datetime.fromisoformat(start_time_raw.replace("Z", "+00:00"))
            except Exception as e:
                logger.debug(f"[{self.provider_id}] Failed to parse start time: {e}")

        # Parse markets - ONLY 1x2/moneyline
        markets = self._parse_markets(raw_event, home_raw, away_raw)

        if not markets:
            metrics.events_skipped_no_markets += 1
            return None

        return StandardEvent(
            id=event_id,
            name=f"{home_raw} vs {away_raw}",
            provider=self.provider_id,
            sport=sport,
            league=league,
            home_team=home_team,
            away_team=away_team,
            start_time=start_time,
            markets=markets,
            url=raw_event.get("url", ""),
        )

    def _parse_markets(self, raw_event: dict, home_raw: str, away_raw: str) -> list[dict]:
        """
        Parse markets from event data.

        IMPORTANT: Only return 1x2/moneyline markets.
        Skip all other market types.

        Args:
            raw_event: Raw event dict
            home_raw: Raw home team name (for outcome matching)
            away_raw: Raw away team name (for outcome matching)

        Returns:
            List of market dicts with type and outcomes
        """
        markets = []

        # Adapt to API structure - common patterns:
        #   raw_event['markets'], raw_event['odds'], raw_event['bet_offers']
        raw_markets = raw_event.get("markets", [])

        for raw_market in raw_markets:
            # Check market type - ONLY 1x2/moneyline
            market_type_id = raw_market.get("type_id") or raw_market.get("marketTypeId")
            market_type = self.MARKET_TYPE_MAPPING.get(market_type_id, "other")

            if market_type == "other":
                # Skip non-1x2/moneyline markets
                continue

            # Parse outcomes
            outcomes = []
            raw_outcomes = raw_market.get("outcomes", []) or raw_market.get("odds", [])

            for raw_outcome in raw_outcomes:
                outcome_name = raw_outcome.get("name", "") or raw_outcome.get("label", "")
                odds = raw_outcome.get("odds", 0) or raw_outcome.get("price", 0)

                if odds <= 1.0:
                    continue  # Invalid odds

                # Normalize outcome to home/away/draw
                normalized = normalize_outcome(outcome_name, home_raw, away_raw)

                outcomes.append({"name": normalized, "odds": float(odds)})

            # Only add market if we have valid outcomes
            if outcomes:
                markets.append({"type": market_type, "outcomes": outcomes})

        return markets
