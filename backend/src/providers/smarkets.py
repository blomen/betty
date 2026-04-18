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


def price_integer_to_odds(price: int) -> float:
    """Convert a Smarkets quote integer price (0-10000, percent x 100)
    to decimal odds.

    5500 -> 55% implied -> decimal odds 10000/5500 ~= 1.818.
    Returns 0.0 for non-positive inputs.
    """
    if price <= 0:
        return 0.0
    return round(10000.0 / price, 4)


def _price_percent_string_to_odds(raw: str | float | int | None) -> float:
    """Convert a Smarkets last-executed-price percentage ("65.36" = 65.36%)
    to decimal odds. Returns 0.0 on missing / non-positive / unparseable."""
    if raw is None or raw == "":
        return 0.0
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if pct <= 0.0:
        return 0.0
    return round(100.0 / pct, 4)


def parse_market_prices(raw: dict) -> dict[str, float]:
    """Extract {contract_id: decimal_odds} from a Smarkets prices payload.

    Expects pre-merged raw dict of the form::

        {
          "last_executed_prices": {"<market_id>": [
              {"contract_id": "...", "last_executed_price": "<pct>" | None},
              ...
          ]},
          "quotes": {"<contract_id>": {
              "bids":   [{"price": int 0-10000, "quantity": int}, ...],
              "offers": [{"price": int 0-10000, "quantity": int}, ...]
          }},
        }

    Prefers `last_executed_price` (revealed trade price, percent-string).
    Falls back to mid of (best_back=max bid, best_lay=min offer) from
    /quotes/. Drops contracts with neither.
    """
    out: dict[str, float] = {}

    # Flatten last_executed_prices: iterate all markets -> all contracts.
    last_by_id: dict[str, str | float | int | None] = {}
    for _market_id, entries in (raw.get("last_executed_prices") or {}).items():
        if not entries:
            continue
        for entry in entries:
            cid = entry.get("contract_id")
            if cid is None:
                continue
            last_by_id[str(cid)] = entry.get("last_executed_price")

    quotes_by_id = raw.get("quotes") or {}

    # Union of ids seen — a contract may appear in quotes but not in trades.
    all_ids = set(last_by_id.keys()) | {str(k) for k in quotes_by_id.keys()}

    for cid in all_ids:
        last = last_by_id.get(cid)
        odds = _price_percent_string_to_odds(last)
        if odds > 0.0:
            out[cid] = odds
            continue
        q = quotes_by_id.get(cid) or quotes_by_id.get(int(cid)) if cid.isdigit() else quotes_by_id.get(cid)
        # Ensure dict — guard against None or wrong shape.
        if not isinstance(q, dict):
            continue
        bids = q.get("bids") or []
        offers = q.get("offers") or []
        best_back = max((int(b.get("price", 0)) for b in bids), default=0)
        best_lay = min((int(o.get("price", 0)) for o in offers), default=0)
        if best_back > 0 and best_lay > 0:
            mid = (best_back + best_lay) // 2
            out[cid] = price_integer_to_odds(mid)

    return out
