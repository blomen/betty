"""Rainbet provider - Betby-backed sportsbook with Cloudflare + Turnstile bypass.

See:
- docs/superpowers/specs/2026-05-10-rainbet-provider-design.md  (design)
- docs/superpowers/research/2026-05-10-rainbet-discovery.md     (protocol)

The browser orchestration class (RainbetRetriever) is added in a separate task.
This file currently only contains the pure parser functions.
"""

import logging
from datetime import datetime, timezone

from ..core.retriever import StandardEvent
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

PROVIDER_ID = "rainbet"

# Per-sport preference table for moneyline market_id (when both market 1
# [3-way 1x2] and a 2-way "Winner" market are present).
# - football/soccer: keep market 1 (1x2 with draw); no moneyline preference.
# - ice_hockey: prefer 406 (Winner incl. OT and penalties) over 1.
# - baseball: prefer 251 (Winner incl. extra innings).
# - basketball/american_football: prefer 219 (Winner incl. overtime).
# - tennis/mma/boxing/esports: prefer 186 (Winner — no OT/draws).
_MONEYLINE_PREF: dict[str, str] = {
    "ice_hockey": "406",
    "baseball": "251",
    "basketball": "219",
    "american_football": "219",
    "tennis": "186",
    "mma": "186",
    "boxing": "186",
    "esports": "186",
}

# Per-sport preference for spread market_id when an event ships multiple
# Handicap-typed markets that all categorize to "spread".
# - tennis: prefer 188 (Set handicap) over 187 (Game handicap).
# - esports: prefer 327 (Map handicap) over 1000317 (Rounds handicap).
# Other sports typically only ship one spread market_id (16/223/258), so a
# preference entry isn't required.
_SPREAD_PREF: dict[str, list[str]] = {
    "tennis": ["188", "187"],
    "esports": ["327", "1000317"],
}

# Per-sport preference for total market_id (analogous to spread).
# - tennis: prefer 189 (Total games) over 314 (Total sets).
_TOTAL_PREF: dict[str, list[str]] = {
    "tennis": ["189", "314"],
}


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


def _build_outcomes(
    market_type: str,
    chosen_variant: dict,
    specs: dict,
) -> list[dict]:
    """Materialise outcomes for a chosen market variant.

    Outcome ID conventions per discovery doc Section 5.1:
      - 1x2: ``"1"`` home, ``"2"`` draw, ``"3"`` away.
      - moneyline (markets 219/186/251/406): ``"4"`` home, ``"5"`` away.
      - moneyline-fallback from 1x2: if outcomes use ids ``"1"``/``"2"``/``"3"``
        treat ``"1"`` as home and ``"3"`` as away (drop the draw).
      - spread (markets 16/223/188/327/...): ``"1714"`` home with ``+hcp``,
        ``"1715"`` away with ``-hcp``.
      - total (markets 18/225/258/189/...): ``"12"`` over with ``point=total``,
        ``"13"`` under with ``point=total``.

    Outcomes with non-numeric or non-positive odds (<= 1.0) are dropped — a
    decimal odds of 1.0 means "guaranteed", which is invalid for a 2/3-way
    market and indicates a bookmaker placeholder.
    """
    outcomes: list[dict] = []

    if market_type == "1x2":
        for outcome_id, name in (("1", "home"), ("2", "draw"), ("3", "away")):
            payload = chosen_variant.get(outcome_id)
            if not payload:
                continue
            odds = _safe_float(payload.get("k"))
            if odds is None or odds <= 1.0:
                continue
            outcomes.append({"name": name, "odds": odds})
        return outcomes

    if market_type == "moneyline":
        # Outcome ids "4"/"5" — used by markets 219/186/251/406.
        # Fallback: if the variant carries the 1x2 ids "1"/"3" (e.g. the parser
        # was forced to fall back to market 1 because no proper moneyline
        # market was published), treat them as home/away with the draw dropped.
        for outcome_id, name in (("4", "home"), ("5", "away")):
            payload = chosen_variant.get(outcome_id)
            if not payload:
                continue
            odds = _safe_float(payload.get("k"))
            if odds is None or odds <= 1.0:
                continue
            outcomes.append({"name": name, "odds": odds})
        if outcomes:
            return outcomes

        # Fallback to 1x2-as-moneyline (drop draw).
        for outcome_id, name in (("1", "home"), ("3", "away")):
            payload = chosen_variant.get(outcome_id)
            if not payload:
                continue
            odds = _safe_float(payload.get("k"))
            if odds is None or odds <= 1.0:
                continue
            outcomes.append({"name": name, "odds": odds})
        return outcomes

    if market_type == "spread":
        hcp = specs.get("hcp")
        if hcp is None:
            return []
        # Discovery doc Section 4.3 convention:
        #   outcome 1714 = home with template "{$competitor1} ({+hcp})"
        #   outcome 1715 = away with template "{$competitor2} ({-hcp})"
        # i.e. home's signed point is +hcp, away's is -hcp.
        for outcome_id, name, point in (
            ("1714", "home", hcp),
            ("1715", "away", -hcp),
        ):
            payload = chosen_variant.get(outcome_id)
            if not payload:
                continue
            odds = _safe_float(payload.get("k"))
            if odds is None or odds <= 1.0:
                continue
            outcomes.append({"name": name, "odds": odds, "point": point})
        return outcomes

    if market_type == "total":
        total = specs.get("total")
        if total is None:
            return []
        for outcome_id, name in (("12", "over"), ("13", "under")):
            payload = chosen_variant.get(outcome_id)
            if not payload:
                continue
            odds = _safe_float(payload.get("k"))
            if odds is None or odds <= 1.0:
                continue
            outcomes.append({"name": name, "odds": odds, "point": total})
        return outcomes

    return outcomes


def _select_preferred_market(
    sport: str,
    market_type: str,
    candidates: dict[str, dict],
) -> dict | None:
    """From multiple market_ids that all categorize to ``market_type``, pick the
    one preferred for ``sport``.

    ``candidates`` maps market_id (string) -> a parsed market dict (already
    constructed by :func:`parse_event`'s inner loop).

    Returns the chosen market dict, or None if there are no candidates.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return next(iter(candidates.values()))

    if market_type == "spread":
        pref = _SPREAD_PREF.get(sport)
    elif market_type == "total":
        pref = _TOTAL_PREF.get(sport)
    else:
        pref = None

    if pref:
        for market_id in pref:
            if market_id in candidates:
                return candidates[market_id]

    # No preference table or no listed market_id is present — fall back to the
    # market_id that sorts first (deterministic, but rare path).
    first_key = sorted(candidates.keys())[0]
    return candidates[first_key]


def parse_event(
    event_id: str,
    event_data: dict,
    descriptions: dict,
    sports_map: dict,
) -> StandardEvent | None:
    """Parse one Betby prematch event into a :class:`StandardEvent`.

    Args:
        event_id: dict-key under ``chunk["events"]`` (string-encoded int).
        event_data: the event dict from the prematch chunk (``desc``,
            ``markets``, ``state``).
        descriptions: the full markets-descriptions catalogue (string keys).
        sports_map: ``chunk["sports"]`` (informational; not currently used,
            accepted for forward-compat with future league-tagging).

    Returns ``None`` for: live events (``state.status != 0``), non-match types
    (``desc.type != "match"``), out-of-scope sports, missing teams, or events
    with no parseable markets.
    """
    desc = event_data.get("desc") or {}
    state = event_data.get("state") or {}

    # Skip non-match (golf "stage", futures "tournament", etc.).
    if desc.get("type") != "match":
        return None
    # Skip live / cancelled / postponed (status code 0 = "Not started").
    if state.get("status") != 0:
        return None

    # Sport.
    sport = betby_sport_id_to_arnold(desc.get("sport"))
    if sport is None:
        return None

    # Teams.
    competitors = desc.get("competitors") or []
    if len(competitors) < 2:
        return None
    home_raw = (competitors[0] or {}).get("name") or ""
    away_raw = (competitors[1] or {}).get("name") or ""
    if not home_raw or not away_raw:
        return None
    home_team = normalize_team_name(home_raw)
    away_team = normalize_team_name(away_raw)
    event_name = f"{home_raw} vs {away_raw}"

    # Start time -- epoch seconds (UTC) -> ISO with trailing Z.
    scheduled = desc.get("scheduled")
    start_time = ""
    if isinstance(scheduled, (int, float)):
        try:
            dt = datetime.fromtimestamp(scheduled, tz=timezone.utc)
            start_time = dt.isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            start_time = ""

    # ----- Markets -----
    # Build a dict keyed by arnold market_type, where the value is a dict from
    # market_id -> parsed market dict. After the first pass we run sport-specific
    # preference resolution to pick exactly one market per type (e.g. tennis
    # ships 187 + 188; only 188 survives).
    by_type: dict[str, dict[str, dict]] = {}

    for market_id, variants_dict in (event_data.get("markets") or {}).items():
        descriptor = descriptions.get(market_id)
        if not descriptor:
            continue
        arnold_type = categorize_market(descriptor)
        if arnold_type is None:
            continue
        if not isinstance(variants_dict, dict) or not variants_dict:
            continue

        chosen = pick_main_market(market_id, variants_dict, arnold_type)
        if chosen is None:
            continue
        variant_key, chosen_variant = chosen
        specs = parse_variant_key(variant_key)
        outcomes = _build_outcomes(arnold_type, chosen_variant, specs)
        # Need at least 2 outcomes for the market to be useful (over/under,
        # home/away, or 3-way 1x2).
        if len(outcomes) < 2:
            continue

        market: dict = {"type": arnold_type, "outcomes": outcomes}
        by_type.setdefault(arnold_type, {})[market_id] = market

    # ---- 1x2-vs-moneyline disambiguation per discovery doc Section 5.1 ----
    if "1x2" in by_type and "moneyline" in by_type:
        # Both shipped. Soccer is the only sport that prefers 1x2 over moneyline.
        # Everything else drops 1x2.
        if sport != "football":
            del by_type["1x2"]
    elif "1x2" in by_type and sport != "football":
        # 1x2 shipped without a separate moneyline market — for non-soccer
        # sports rebadge as moneyline by re-extracting just home/away from
        # market 1's outcomes (drop draw).
        candidates = by_type.pop("1x2")
        ml_candidates: dict[str, dict] = {}
        for mid, m in candidates.items():
            outcomes = [o for o in m["outcomes"] if o["name"] in ("home", "away")]
            if len(outcomes) >= 2:
                ml_candidates[mid] = {"type": "moneyline", "outcomes": outcomes}
        if ml_candidates:
            by_type["moneyline"] = ml_candidates

    # ---- Hockey-specific: prefer 406 over 1 when both produce a moneyline ----
    if sport == "ice_hockey" and "moneyline" in by_type:
        ml_candidates = by_type["moneyline"]
        if "406" in ml_candidates:
            by_type["moneyline"] = {"406": ml_candidates["406"]}

    # ---- Moneyline preference (when multiple moneyline market_ids survived) --
    if "moneyline" in by_type and len(by_type["moneyline"]) > 1:
        preferred_id = _MONEYLINE_PREF.get(sport)
        if preferred_id and preferred_id in by_type["moneyline"]:
            by_type["moneyline"] = {preferred_id: by_type["moneyline"][preferred_id]}

    # ---- Spread / Total preference resolution per sport ----
    final_markets: list[dict] = []
    for arnold_type, candidates in by_type.items():
        chosen_market = _select_preferred_market(sport, arnold_type, candidates)
        if chosen_market is not None:
            final_markets.append(chosen_market)

    if not final_markets:
        return None

    return StandardEvent(
        id=event_id,
        name=event_name,
        sport=sport,
        markets=final_markets,
        provider=PROVIDER_ID,
        url="",  # Rainbet/Betby has no per-event canonical URL on the SPA.
        start_time=start_time,
        home_team=home_team,
        away_team=away_team,
    )


def parse_prematch_snapshot(
    chunks: list[dict],
    descriptions: dict,
) -> list[StandardEvent]:
    """Parse a complete Betby prematch snapshot into a list of StandardEvents.

    A snapshot is the union of N chunk responses (currently 5 per refresh in
    capture). Each chunk has its own ``events`` block keyed by event_id and
    its own ``sports`` block. The ``descriptions`` catalogue is fetched
    separately from the chunk endpoint and is the same across all chunks.

    Args:
        chunks: list of decoded chunk JSON dicts (one per
            ``GET /api/v4/prematch/brand/{brand}/en/{version}`` response).
        descriptions: full markets-descriptions catalogue (single dict shared
            across all chunks).

    Returns:
        List of StandardEvents in chunk-then-dict-iteration order. Failed
        events (live, non-match, no recognized markets, etc.) are silently
        skipped.
    """
    if not chunks:
        return []

    # Aggregate the sports map across chunks (informational; passed through
    # to parse_event for forward-compat).
    sports_map: dict = {}
    for chunk in chunks:
        sports_block = chunk.get("sports") or {}
        sports_map.update(sports_block)

    events: list[StandardEvent] = []
    for chunk in chunks:
        events_block = chunk.get("events") or {}
        for event_id, event_data in events_block.items():
            try:
                ev = parse_event(event_id, event_data, descriptions, sports_map)
            except Exception:  # pragma: no cover — defensive
                logger.exception("[rainbet] parse_event raised for event_id=%s", event_id)
                continue
            if ev is not None:
                events.append(ev)
    return events
