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


def _safe_float(raw: object) -> float | None:
    """Coerce a Betby ``k`` field (JSON string) to float, or None on failure."""
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def pick_main_market(
    market_id: str,
    variants: dict,
    market_type: str,
) -> tuple[str, dict] | None:
    """Pick the "main line" variant for a given market.

    Args:
        market_id: Betby market id (string-encoded int). Currently informational
            only — picking logic is driven by ``market_type`` and the variant
            shape.
        variants: dict from variant_key (e.g. ``"hcp=-1.5"``) to the variant's
            outcome dict (e.g. ``{"1714": {"k": "1.9"}, "1715": {"k": "1.9"}}``).
        market_type: arnold market type from :func:`categorize_market`.

    Returns:
        ``(variant_key, variant_data)`` of the chosen line, or ``None`` if no
        valid variant exists.

    Selection rules (per discovery doc Section 4.4):
        - 1x2 / moneyline: there is exactly one variant (key ``""``); return it.
        - spread: pick the variant with the smallest ``abs(hcp)``. Tie-break:
            prefer the negative line (favourite laying points). Variants whose
            key cannot be parsed for ``hcp`` are skipped.
        - total: pick the variant with the most balanced odds (smallest absolute
            difference between over (id ``"12"``) and under (id ``"13"``) prices).
            Tie-break: prefer the median total. Variants missing an outcome or
            carrying invalid odds are skipped.
    """
    if not variants:
        return None

    if market_type in ("1x2", "moneyline"):
        # No specifiers — the only valid variant key is the empty string.
        data = variants.get("")
        if not data:
            return None
        return ("", data)

    if market_type == "spread":
        candidates: list[tuple[float, float, str, dict]] = []
        # Priority key order (lower is "more main"):
        #   1) abs(hcp)        — smallest line wins
        #   2) signed hcp      — negative wins on a tie (favourite laying)
        for vkey, vdata in variants.items():
            specs = parse_variant_key(vkey)
            hcp = specs.get("hcp")
            if hcp is None:
                continue
            candidates.append((abs(hcp), hcp, vkey, vdata))
        if not candidates:
            return None
        candidates.sort(key=lambda t: (t[0], t[1]))
        _, _, key, data = candidates[0]
        return (key, data)

    if market_type == "total":
        # Score each variant by |over_odds - under_odds|; lower is better.
        # Tie-break: median total (i.e. abs(total - median(totals))) — keeps the
        # picker stable across snapshots when the bookmaker hasn't moved odds.
        scored: list[tuple[float, str, dict]] = []  # (balance_score, vkey, vdata)
        totals: list[float] = []
        for vkey, vdata in variants.items():
            specs = parse_variant_key(vkey)
            total = specs.get("total")
            if total is None:
                continue
            over = vdata.get("12")
            under = vdata.get("13")
            if not over or not under:
                continue
            over_odds = _safe_float(over.get("k"))
            under_odds = _safe_float(under.get("k"))
            if over_odds is None or under_odds is None:
                continue
            scored.append((abs(over_odds - under_odds), vkey, vdata))
            totals.append(total)

        if not scored:
            return None

        if len(scored) == 1:
            _, key, data = scored[0]
            return (key, data)

        # Apply tie-break with median total.
        sorted_totals = sorted(totals)
        median_total = sorted_totals[len(sorted_totals) // 2]
        scored_with_median: list[tuple[float, float, str, dict]] = []
        for balance, vkey, vdata in scored:
            specs = parse_variant_key(vkey)
            total = specs["total"]
            scored_with_median.append((balance, abs(total - median_total), vkey, vdata))
        scored_with_median.sort(key=lambda t: (t[0], t[1]))
        _, _, key, data = scored_with_median[0]
        return (key, data)

    return None
