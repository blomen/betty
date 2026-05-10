"""Rainbet provider - Betby-backed sportsbook with Cloudflare + Turnstile bypass.

See:
- docs/superpowers/specs/2026-05-10-rainbet-provider-design.md  (design)
- docs/superpowers/research/2026-05-10-rainbet-discovery.md     (protocol)

Contains the pure parser functions (parse_event, parse_prematch_snapshot, ...)
plus the browser orchestration class (RainbetRetriever) at the bottom.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..core.browser_retriever import BrowserRetriever
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
    if not isinstance(event_data, dict):
        return None
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


class RainbetRetriever(BrowserRetriever):
    """Rainbet (Betby-backed) sportsbook extractor.

    Drives ``self.transport.page`` (patchright Chromium provided by
    :class:`BrowserTransport`). The bt-renderer SPA mounts inside the page
    after we clear Cloudflare Turnstile and makes its own HTTP calls to
    Betby's data backend (``*.sptpub.com``); we capture the listing
    responses and parse them via :func:`parse_prematch_snapshot`.

    Browser lifecycle is owned by :class:`BrowserTransport`: launched on
    first ``_ensure_browser()``, cleaned up by ``transport.close()``
    (called by the orchestrator at the end of a run via the inherited
    :meth:`BrowserRetriever.close`).

    :meth:`extract` is called once per supported sport in a run. We:

    - On first call: navigate to ``rainbet/sportsbook``, clear Turnstile,
      capture all sptpub.com responses while the SPA bootstraps for
      ~30 sec.
    - On all calls (first and subsequent): filter the captured snapshot
      to events matching ``sport``, return ``StandardEvent[]``.

    The full prematch snapshot contains all sports — we don't re-fetch
    per sport. Parser runs once per run, filter many times.
    """

    def __init__(
        self,
        config: dict,
        transport=None,
        circuit_breaker=None,
        rate_limit_config=None,
    ):
        super().__init__(config, transport=transport)

        self._brand_id = config.get("brand_id")
        if not self._brand_id:
            raise ValueError(
                f"[{self.provider_id}] config missing required 'brand_id' (Betby brand id used by sptpub backend)"
            )
        self._site_url = config.get("site_url", "https://rainbet.com/sportsbook")
        self._sport_timeout = config.get("sport_timeout", 600)

        # Cached state across extract() calls within a single run.
        self._turnstile_cleared = False
        self._snapshot_chunks: list[dict] = []
        self._descriptions: dict | None = None
        self._snapshot_complete = False
        self._all_events: list[StandardEvent] | None = None

        # Patchright lifecycle owned by this retriever (NOT via BrowserTransport).
        # Validated empirically: BrowserTransport's launch args + locale/geo
        # tripped Cloudflare's interactive Turnstile re-challenge loop, while
        # the spike v4 args below clear it in ~0.7s. See discovery doc + the
        # rainbet-spike v4 capture for the matching invocation.
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def parse(self, data, sport: str) -> list[StandardEvent]:
        """Pure-data parsing happens via :func:`parse_prematch_snapshot`
        in :meth:`extract`; this method is unused but required by the
        :class:`Retriever` ABC.
        """
        return []

    def _get_sport_url(self, sport: str) -> str:
        """Return the single Betby SPA URL we navigate to.

        We don't navigate per-sport (the prematch snapshot covers all
        sports), but the orchestrator's health-check code calls this
        method so we return the configured site URL.
        """
        return self._site_url

    async def extract(self, sport: str, limit: int = 0, **kwargs) -> list[StandardEvent]:
        """Extract prematch events for ``sport`` from Betby's snapshot.

        Fetches the full snapshot once per run (idempotent) and filters
        the cached parsed events by sport on each call.
        """
        if self._all_events is None:
            await self._fetch_full_snapshot()
            if not self._snapshot_chunks:
                logger.warning(f"[{self.provider_id}] no prematch chunks captured — returning empty event list")
                self._all_events = []
            else:
                self._all_events = parse_prematch_snapshot(self._snapshot_chunks, self._descriptions or {})
                logger.info(
                    f"[{self.provider_id}] parsed {len(self._all_events)} "
                    f"events from snapshot ({len(self._snapshot_chunks)} chunks)"
                )

        events = [e for e in self._all_events if e.sport == sport]
        if limit and len(events) > limit:
            events = events[:limit]
        return events

    async def _ensure_browser(self) -> None:
        """Launch patchright Chromium directly with spike-v4-validated args.

        We bypass BrowserTransport because its launch profile (locale="sv-SE",
        Stockholm geolocation, --disable-blink-features=AutomationControlled,
        no --disable-http2/--disable-quic) caused Cloudflare's Turnstile to
        re-challenge in a loop. Spike v4 with the args below clears Turnstile
        in ~0.7s.
        """
        if self._page is not None:
            return

        from patchright.async_api import async_playwright

        proxy = None
        pu = os.environ.get("PROXY_URL")
        if pu:
            parsed = urlparse(pu)
            proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
            if parsed.username:
                proxy["username"] = parsed.username
                proxy["password"] = parsed.password or ""

        logger.info(f"[{self.provider_id}] launching patchright Chromium (proxy={bool(proxy)})")
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--disable-http2", "--disable-quic"],
            proxy=proxy,
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )
        self._context.set_default_timeout(60_000)
        self._page = await self._context.new_page()

    async def close(self) -> None:
        """Tear down patchright resources owned by this retriever."""
        for attr, method in (
            ("_page", "close"),
            ("_context", "close"),
            ("_browser", "close"),
            ("_pw", "stop"),
        ):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                fn = getattr(obj, method)
                result = fn()
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                pass
            setattr(self, attr, None)
        self._turnstile_cleared = False

        # Also call up to BrowserRetriever.close() in case the parent's
        # transport got initialised by a code path we don't own.
        try:
            await super().close()
        except Exception:
            pass

    async def _fetch_full_snapshot(self) -> None:
        """Drive the page to capture sptpub prematch chunks + descriptions.

        Idempotent within a run: subsequent calls short-circuit once
        ``_snapshot_complete`` is True.
        """
        if self._snapshot_complete:
            return

        await self._ensure_browser()
        page = self._page

        # Captured payloads — use list for chunks (de-dup later by URL),
        # closure-mutable list-of-one for descriptions so the inner
        # handler can rebind without `nonlocal` shenanigans.
        captured: list[tuple[str, dict]] = []
        descriptions_holder: list[dict | None] = [None]

        async def grab(resp):
            try:
                host = urlparse(resp.url).hostname or ""
                if "sptpub.com" not in host:
                    return
                content_type = ""
                try:
                    headers = resp.headers
                    if isinstance(headers, dict):
                        content_type = headers.get("content-type") or headers.get("Content-Type") or ""
                except Exception:
                    pass
                if content_type and "json" not in content_type.lower():
                    return
                try:
                    body = await resp.body()
                except Exception:
                    return
                try:
                    blob = json.loads(body)
                except (ValueError, TypeError):
                    return
                if not isinstance(blob, dict):
                    return

                url = resp.url
                if "/api/v3/descriptions/brand/" in url and "/markets/" in url:
                    descriptions_holder[0] = blob
                    return
                if "/api/v4/prematch/brand/" in url:
                    captured.append((url, blob))
            except Exception:
                # Handler must never raise — Playwright will fail the request
                # callback chain if it does.
                logger.debug(f"[{self.provider_id}] grab() handler error", exc_info=True)

        def handler(resp):
            asyncio.create_task(grab(resp))

        # Counter shared with _clear_turnstile so the click loop can exit
        # on the BEHAVIOURAL signal (any sptpub.com response = page is past
        # the wall and bootstrapping) rather than on the formal cookie/iframe
        # state, which spike v4 showed lags behind real progress.
        sptpub_hits = [0]
        original_grab = grab

        async def _grab_with_counter(resp):
            host = urlparse(resp.url).hostname or ""
            if "sptpub.com" in host:
                sptpub_hits[0] += 1
            await original_grab(resp)

        # Replace the bare grab with the counting wrapper.
        def handler2(resp):
            asyncio.create_task(_grab_with_counter(resp))

        page.remove_listener("response", handler)
        page.on("response", handler2)

        try:
            # Navigate. Patchright passes most CF on its own; Turnstile
            # widget then needs a synthetic click.
            await page.goto(self._site_url, wait_until="domcontentloaded", timeout=60_000)
            await self._clear_turnstile(sptpub_hits)

            # Let the SPA bootstrap and fetch the prematch manifest +
            # version chunks. Poll every 2s up to 30s, exit early once
            # we've got both `snapshot_complete: true` AND descriptions.
            loop = asyncio.get_event_loop()
            deadline = loop.time() + 30.0
            while loop.time() < deadline:
                await page.wait_for_timeout(2000)
                has_complete = any(blob.get("snapshot_complete") is True for _u, blob in captured)
                if has_complete and descriptions_holder[0] is not None:
                    break
        finally:
            try:
                page.remove_listener("response", handler2)
            except Exception:
                pass

        # De-dup captured chunks by URL and drop the bare manifest
        # (no `events` block) — Section 3 of the discovery doc.
        seen_urls: set[str] = set()
        for url, blob in captured:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if blob.get("events"):
                self._snapshot_chunks.append(blob)

        self._descriptions = descriptions_holder[0]
        self._snapshot_complete = bool(self._snapshot_chunks) and self._descriptions is not None

        logger.info(
            f"[{self.provider_id}] snapshot fetched: "
            f"{len(self._snapshot_chunks)} chunks, "
            f"descriptions={'yes' if self._descriptions else 'no'}, "
            f"complete={self._snapshot_complete}"
        )

    async def _clear_turnstile(self, sptpub_hits: list[int]) -> None:
        """Click the Cloudflare Turnstile widget until the page bootstraps.

        Exit condition matches spike v4: ANY response from a ``*.sptpub.com``
        host means the SPA is past the wall and is fetching Betby data.
        Cookie/iframe state is unreliable as a success signal — Cloudflare
        sometimes leaves the iframe in the DOM after the cookie lands but
        before the SPA actually starts loading.

        Caps at 60s of clicking; raises RuntimeError on timeout.
        """
        page = self._page
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 60.0

        while loop.time() < deadline:
            # Behavioral exit: any sptpub response means the wall is breached
            # and the SPA is bootstrapping. This is the criterion spike v4
            # used and that we observed working in 0.7s-8.5s.
            if sptpub_hits[0] > 0:
                if not self._turnstile_cleared:
                    logger.info(f"[{self.provider_id}] Turnstile cleared (sptpub_hits={sptpub_hits[0]})")
                self._turnstile_cleared = True
                return

            ts_iframe = None
            try:
                ts_iframe = await page.query_selector(
                    "iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile']"
                )
            except Exception:
                pass

            # Click the Turnstile checkbox. Primary: bbox-based — query the
            # iframe's bounding box and click near its left edge (the checkbox
            # sits in the leftmost ~50px of the widget). Fallback: hardcoded
            # coord (210, 290) for the case where the iframe selector misses
            # but the widget is still on-page.
            try:
                if ts_iframe is not None:
                    bbox = await ts_iframe.bounding_box()
                    if bbox:
                        cx = bbox["x"] + 30
                        cy = bbox["y"] + bbox["height"] / 2
                        await page.mouse.click(cx, cy)
                    else:
                        await page.mouse.click(*_TURNSTILE_CLICK_COORD)
                else:
                    await page.mouse.click(*_TURNSTILE_CLICK_COORD)
            except Exception as e:
                logger.debug(f"[{self.provider_id}] Turnstile click failed: {e}")
            await page.wait_for_timeout(2000)

        raise RuntimeError(f"[{self.provider_id}] Turnstile not cleared within 60s")
