"""Smarkets signal-only extractor.

Reads last-executed prices from Smarkets' public JSON API (unauthenticated).
User is IP-banned from their account, so Smarkets is never a placement target
— odds feed consensus via SIGNAL_ONLY_PROVIDERS only.

Schema notes (verified against live API 2026-04-18):
    - Events listing requires `type_domain=<sport_slug>` (e.g. football,
      basketball, ice_hockey) — NOT the literal word "sport".
    - To filter to match events only, pass `type=<sport>_match` as well.
    - `type_scope` is null in the live data; the `type_scope_to_sport`
      mapping below is kept for API parity and accepts the Smarkets
      public type-slug values (with hyphens) mapping to our canonical
      sport names (with underscores).
    - Prices on /last_executed_prices/ are STRING PERCENTAGES like "65.36"
      (= 65.36% implied probability → decimal odds 100/65.36).
    - Quotes are nested by contract_id with `bids` / `offers` arrays of
      `{"price": int 0-10000, "quantity": int}`. best_back = highest bid,
      best_lay = lowest offer.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Map from Smarkets public type-slug (hyphenated, as seen in type_domain /
# URL path) to our canonical sport name (underscored).
SMARKETS_TYPE_SCOPE_TO_SPORT: dict[str, str] = {
    "football": "football",
    "basketball": "basketball",
    "tennis": "tennis",
    "ice-hockey": "ice_hockey",
    "american-football": "american_football",
    "baseball": "baseball",
    "mma": "mma",
    "boxing": "boxing",
}


def type_scope_to_sport(scope: str) -> str | None:
    """Resolve a Smarkets type-slug to our canonical sport name.

    Returns None for non-sport scopes (politics, entertainment, etc.)
    which Firev doesn't track.
    """
    return SMARKETS_TYPE_SCOPE_TO_SPORT.get(scope)
