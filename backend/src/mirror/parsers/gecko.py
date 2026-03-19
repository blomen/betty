"""Gecko V2 (OBG) bet response parser.

Parses bet placement API responses from Betsson Group sites
(Betsson, Betsafe, NordicBet, Spelklubben).

NOTE: The exact response schema will be confirmed during the discovery phase.
Field paths in parse() are best-guess based on the Gecko events-table API
structure and may need adjustment after a real bet placement is captured.
"""

import logging
from typing import Any

from ...matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

# URL path segments that indicate bet placement (not odds browsing)
_BET_URL_KEYWORDS = ("betslip", "bet/place", "coupon", "wager")


class GeckoBetParser:
    """Parse Gecko V2 bet placement API responses."""

    def is_bet_placement_url(self, url: str) -> bool:
        """Check if URL is a bet placement endpoint (not odds/events)."""
        lower = url.lower()
        return "/api/sb/" in lower and any(kw in lower for kw in _BET_URL_KEYWORDS)

    def is_rejection(self, body: dict) -> bool:
        """Check if response indicates a rejected bet."""
        data = body.get("data", {})
        status = data.get("status", "").lower()
        return status in ("rejected", "failed", "error", "declined")

    def parse(self, body: dict) -> dict[str, Any] | None:
        """Parse a confirmed bet response into structured fields.

        Returns dict with bet fields, or None if rejected/unparseable.
        """
        data = body.get("data", {})

        # Check for rejection
        if self.is_rejection(body):
            return None

        bet_id = data.get("betId")
        if not bet_id:
            logger.warning("No betId in response — cannot parse")
            return None

        # Extract stake
        stakes = data.get("stakes", [])
        stake = stakes[0].get("amount", 0.0) if stakes else 0.0

        # Extract selection details (first selection for singles)
        selections = data.get("selections", [])
        if not selections:
            logger.warning(f"No selections in bet {bet_id}")
            return None

        sel = selections[0]
        odds = sel.get("odds", 0.0)
        event_name = sel.get("eventName", "")
        gecko_event_id = sel.get("eventId", "")

        # Parse participants
        participants = sel.get("participants", [])
        home_team = None
        away_team = None
        if len(participants) >= 2:
            sorted_p = sorted(participants, key=lambda p: p.get("side", 0))
            home_team = normalize_team_name(sorted_p[0].get("label", ""))
            away_team = normalize_team_name(sorted_p[1].get("label", ""))
        elif event_name and " vs " in event_name:
            parts = event_name.split(" vs ", 1)
            home_team = normalize_team_name(parts[0])
            away_team = normalize_team_name(parts[1])

        # Map market type
        market_template = sel.get("marketTemplateName", "").lower()
        market = self._map_market(market_template)

        # Map outcome
        outcome = self._map_outcome(sel.get("selectionName", ""), home_team, away_team)

        # Extract point for spread/total
        point = sel.get("lineValue") or sel.get("handicap")
        if point is not None:
            point = float(point)

        return {
            "confirmation_id": str(bet_id),
            "odds": float(odds),
            "stake": float(stake),
            "market": market,
            "outcome": outcome,
            "point": point,
            "home_team": home_team,
            "away_team": away_team,
            "event_name": event_name,
            "gecko_event_id": str(gecko_event_id),
        }

    def _map_market(self, template_name: str) -> str | None:
        """Map Gecko market template name to standard market type."""
        t = template_name.lower()
        if any(kw in t for kw in ("winner", "1x2", "match result")):
            return "1x2" if "draw" not in t else "1x2"
        if any(kw in t for kw in ("moneyline", "2-way")):
            return "moneyline"
        if any(kw in t for kw in ("total", "over/under", "over under")):
            return "total"
        if any(kw in t for kw in ("handicap", "spread", "hcp")):
            return "spread"
        return None

    def _map_outcome(
        self, selection_name: str, home_team: str | None, away_team: str | None
    ) -> str | None:
        """Map selection name to standard outcome."""
        lower = selection_name.lower()
        if lower in ("draw", "x", "tie"):
            return "draw"
        if lower in ("over",):
            return "over"
        if lower in ("under",):
            return "under"
        # Match against team names
        if home_team and normalize_team_name(selection_name) == home_team:
            return "home"
        if away_team and normalize_team_name(selection_name) == away_team:
            return "away"
        # Fallback: "1" = home, "2" = away
        if lower == "1":
            return "home"
        if lower == "2":
            return "away"
        return selection_name
