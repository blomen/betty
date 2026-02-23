"""
Shared Extraction Metrics

Unified metrics tracking for all provider extractors.
"""

from dataclasses import dataclass, field
from typing import List
import logging

logger = logging.getLogger(__name__)


@dataclass
class ExtractionMetrics:
    """Track extraction statistics for error visibility."""
    events_parsed: int = 0
    events_skipped_live: int = 0
    events_skipped_no_participants: int = 0
    events_skipped_no_teams: int = 0
    events_skipped_no_markets: int = 0
    events_skipped_error: int = 0
    groups_fetched: int = 0
    groups_failed: int = 0
    leagues_fetched: int = 0
    leagues_failed: int = 0
    pagination_warnings: List[str] = field(default_factory=list)

    @property
    def total_skipped(self) -> int:
        return (
            self.events_skipped_live +
            self.events_skipped_no_participants +
            self.events_skipped_no_teams +
            self.events_skipped_no_markets +
            self.events_skipped_error
        )

    def log_summary(self, provider_id: str, sport: str, total_events: int = 0):
        """Log extraction summary at appropriate level."""
        non_live_skipped = self.total_skipped - self.events_skipped_live

        if non_live_skipped > 0:
            logger.debug(
                f"[{provider_id}] {sport}: parsed {self.events_parsed}/{total_events or self.events_parsed} events, "
                f"skipped {non_live_skipped} (no_participants={self.events_skipped_no_participants}, "
                f"no_teams={self.events_skipped_no_teams}, no_markets={self.events_skipped_no_markets}, "
                f"errors={self.events_skipped_error})"
            )

        if self.groups_failed > 0:
            logger.warning(f"[{provider_id}] {sport}: {self.groups_failed}/{self.groups_fetched} groups failed")

        if self.leagues_failed > 0:
            logger.warning(f"[{provider_id}] {sport}: {self.leagues_failed}/{self.leagues_fetched} leagues failed")

        for warning in self.pagination_warnings:
            logger.debug(f"[{provider_id}] PAGINATION: {warning}")
