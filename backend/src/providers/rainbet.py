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


def categorize_market(descriptor: dict) -> str | None:
    """Classify a Betby market descriptor into an arnold market type.

    The descriptor is one entry from the descriptions catalogue (e.g.
    descriptions["1"], descriptions["219"]). Returns one of
    {"1x2", "moneyline", "spread", "total"} or None if the market is
    not in ALLOWED_MARKETS.

    Decision tree (per discovery doc Section 5.2):
      - name == "1x2"                                          -> 1x2
      - name starts with "winner"                              -> moneyline
        (covers "Winner", "Winner (incl. overtime)",
         "Winner (incl. extra innings)",
         "Winner (incl. overtime and penalties)")
      - market_type == "Handicap" and specifiers == ["hcp"]    -> spread
        (filters out multi-specifier markets like 555 "{!mapnr} map - kill handicap"
         which uses ["mapnr","hcp"])
      - market_type == "Total"    and specifiers == ["total"]  -> total
      - everything else                                        -> None

    Real-payload note: the catalogue uses ``None`` (not ``[]``) for markets that
    have no specifiers. Both shapes are handled here.
    """
    name = (descriptor.get("name") or "").lower()
    market_type = descriptor.get("market_type") or ""
    specs = descriptor.get("specifiers") or []

    if name == "1x2":
        return "1x2"
    if name.startswith("winner"):
        return "moneyline"
    if market_type == "Handicap" and specs == ["hcp"]:
        return "spread"
    if market_type == "Total" and specs == ["total"]:
        return "total"
    return None


def parse_variant_key(variant_key: str) -> dict:
    """Parse a Betby variant key string into a {specifier: float_value} dict.

    Examples (per discovery doc Section 4.3):
      ""                  -> {}
      "total=2.5"         -> {"total": 2.5}
      "hcp=-1.5"          -> {"hcp": -1.5}
      "hcp=-10.5"         -> {"hcp": -10.5}
      "hcp=0"             -> {"hcp": 0.0}
      "mapnr=1|hcp=-0.5"  -> {"mapnr": 1.0, "hcp": -0.5}
      "setnr=2"           -> {"setnr": 2.0}

    Values are always cast to float for uniformity (mapnr/setnr are conceptually
    integers but storing them as floats keeps the dict shape consistent).
    Unknown specifier names are passed through; the rest of the parser uses
    only ``hcp`` and ``total``.

    Malformed segments (no '=' or non-numeric value) are silently skipped so
    the parser stays tolerant of unexpected payload shapes.
    """
    if not variant_key:
        return {}

    out: dict[str, float] = {}
    for segment in variant_key.split("|"):
        if "=" not in segment:
            continue
        name, _, raw_value = segment.partition("=")
        name = name.strip()
        if not name:
            continue
        try:
            out[name] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return out
