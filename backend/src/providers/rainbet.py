"""Rainbet provider - Betby-backed sportsbook with Cloudflare + Turnstile bypass.

See:
- docs/superpowers/specs/2026-05-10-rainbet-provider-design.md  (design)
- docs/superpowers/research/2026-05-10-rainbet-discovery.md     (protocol)

The browser orchestration class (RainbetRetriever) is added in a separate task.
This file currently only contains the pure parser functions.
"""

import logging

logger = logging.getLogger(__name__)


# Betby integer sport_id -> arnold internal sport key.
# Reference: discovery doc Section 1 (the 17-row table). Sports not listed here
# are not extracted by arnold (handball, golf, motorsports, cricket variants,
# etc. - see CLAUDE.md scope).
_SPORT_ID_TO_ARNOLD: dict[int, str] = {
    1: "football",  # soccer
    2: "basketball",
    3: "baseball",
    4: "ice_hockey",
    5: "tennis",
    10: "boxing",
    16: "american_football",
    117: "mma",  # NOTE: distinct bucket from `esports` despite Betby grouping
    # All esports collapse to a single arnold sport key:
    109: "esports",  # Counter-Strike
    110: "esports",  # League of Legends
    111: "esports",  # Dota 2
    112: "esports",  # StarCraft 2
    118: "esports",  # Call of Duty
    125: "esports",  # Rainbow Six
    134: "esports",  # King of Glory
    194: "esports",  # Valorant
    201: "esports",  # Mobile Legends
}


def betby_sport_id_to_arnold(sport_id: int | str | None) -> str | None:
    """Map a Betby sport_id (int or string-encoded int) to arnold's sport key.

    Returns None if the sport is not in arnold's extraction scope (see
    discovery doc Section 1) or if the input cannot be parsed as an integer.
    """
    if sport_id is None or sport_id == "":
        return None
    try:
        key = int(sport_id)
    except (TypeError, ValueError):
        return None
    return _SPORT_ID_TO_ARNOLD.get(key)
