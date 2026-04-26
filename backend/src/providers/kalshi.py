"""Kalshi prediction-market extractor.

Pulls binary YES/NO contracts from Kalshi's public REST API and converts
them to StandardEvent moneyline / 1x2 markets. Extraction is
unauthenticated — only placement (in the mirror workflow) needs API keys.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from ..core import HttpTransport, Retriever, StandardEvent

logger = logging.getLogger(__name__)

# How long the per-extract cache survives. Kalshi runs every 5 min via the
# `kalshi:` tier, and the per-sport orchestrator loop iterates 17 sports back-
# to-back; without caching we'd re-walk the same sport-agnostic /events stream
# 17× per cycle. 60s lets ALL 17 sport calls in one cycle hit the cache while
# still refreshing each cycle.
_EVENTS_CACHE_TTL_SEC = 60.0

# Ticker-prefix → canonical sport. Extend as new series appear.
KALSHI_SERIES_TO_SPORT: dict[str, str] = {
    "KXNBAGAME": "basketball",
    "KXNCAABGAME": "basketball",
    "KXNFLGAME": "american_football",
    "KXNCAAFGAME": "american_football",
    "KXMLBGAME": "baseball",
    "KXNHLGAME": "ice_hockey",
    "KXTENNIS": "tennis",
    "KXUFC": "mma",
    "KXBOXING": "boxing",
    "KXEPL": "football",
    "KXUCL": "football",
    "KXWC": "football",
}

# Sports with no draw outcome → 2-way moneyline; others → 3-way 1x2.
_NO_DRAW_SPORTS = frozenset(
    {
        "basketball",
        "american_football",
        "baseball",
        "ice_hockey",
        "tennis",
        "mma",
        "boxing",
    }
)

# Per-series ticker-suffix → canonical alias (matches aliases.yaml).
# Pinnacle stores team names as the canonical alias (e.g. "rockets", not "Houston").
# Kalshi titles often use city only ("Game 5: Minnesota at Denver"), so we map the
# ticker suffix (the unambiguous team code) to the alias the matcher expects.
_NBA_CODES: dict[str, str] = {
    "ATL": "hawks",
    "BOS": "celtics",
    "BKN": "nets",
    "CHA": "hornets",
    "CHI": "bulls",
    "CLE": "cavaliers",
    "DAL": "mavericks",
    "DEN": "nuggets",
    "DET": "pistons",
    "GSW": "warriors",
    "HOU": "rockets",
    "IND": "pacers",
    "LAC": "clippers",
    "LAL": "lakers",
    "MEM": "grizzlies",
    "MIA": "heat",
    "MIL": "bucks",
    "MIN": "timberwolves",
    "NOP": "pelicans",
    "NYK": "knicks",
    "OKC": "thunder",
    "ORL": "magic",
    "PHI": "76ers",
    "PHX": "suns",
    "POR": "trail blazers",
    "SAC": "kings",
    "SAS": "spurs",
    "TOR": "raptors",
    "UTA": "jazz",
    "WAS": "wizards",
}
_NHL_CODES: dict[str, str] = {
    "ANA": "ducks",
    "ARI": "coyotes",
    "BOS": "bruins",
    "BUF": "sabres",
    "CAR": "hurricanes",
    "CBJ": "blue jackets",
    "CGY": "flames",
    "CHI": "blackhawks",
    "COL": "avalanche",
    "DAL": "stars",
    "DET": "red wings",
    "EDM": "oilers",
    "FLA": "panthers",
    "LA": "kings",
    "MIN": "wild",
    "MTL": "canadiens",
    "NJ": "devils",
    "NSH": "predators",
    "NYI": "islanders",
    "NYR": "rangers",
    "OTT": "senators",
    "PHI": "flyers",
    "PIT": "penguins",
    "SJ": "sharks",
    "SEA": "kraken",
    "STL": "blues",
    "TB": "lightning",
    "TOR": "maple leafs",
    "VAN": "canucks",
    "VGK": "golden knights",
    "WPG": "jets",
    "WSH": "capitals",
}
_MLB_CODES: dict[str, str] = {
    "ARI": "diamondbacks",
    "ATL": "braves",
    "BAL": "orioles",
    "BOS": "red sox",
    "CHC": "cubs",
    "CHW": "white sox",
    "CIN": "reds",
    "CLE": "guardians",
    "COL": "rockies",
    "DET": "tigers",
    "HOU": "astros",
    "KC": "royals",
    "LAA": "angels",
    "LAD": "dodgers",
    "MIA": "marlins",
    "MIL": "brewers",
    "MIN": "twins",
    "NYM": "mets",
    "NYY": "yankees",
    "OAK": "athletics",
    "PHI": "phillies",
    "PIT": "pirates",
    "SD": "padres",
    "SF": "giants",
    "SEA": "mariners",
    "STL": "cardinals",
    "TB": "rays",
    "TEX": "rangers",
    "TOR": "blue jays",
    "WSH": "nationals",
}

KALSHI_TICKER_CODES: dict[str, dict[str, str]] = {
    "KXNBAGAME": _NBA_CODES,
    "KXNHLGAME": _NHL_CODES,
    "KXMLBGAME": _MLB_CODES,
}


def series_to_sport(ticker: str) -> str | None:
    """Resolve a Kalshi event/market ticker to our canonical sport name.

    Uses longest-prefix match so more specific prefixes win (e.g. KXNCAAB
    before KX).
    """
    for prefix in sorted(KALSHI_SERIES_TO_SPORT.keys(), key=len, reverse=True):
        if ticker.startswith(prefix):
            return KALSHI_SERIES_TO_SPORT[prefix]
    return None


def _price_to_odds(price: float, fee_rate: float) -> float:
    """Convert a YES-contract price ($0–$1) to decimal odds with fee adjustment.

    Kalshi's per-trade fee is applied as an incremental cost on the entry price.
    effective_price = price + fee_rate * price * (1 - price)
    decimal_odds = 1 / effective_price
    """
    effective = price + fee_rate * price * (1.0 - price)
    if effective <= 0.0:
        return 0.0
    return round(1.0 / effective, 4)


def _market_price_dollars(m: dict) -> float:
    """Kalshi returns yes_ask_dollars as a float 0–1 (already in dollars).

    Live schema uses string values ("0.7700"), tests use floats — cast both.
    """
    val = m.get("yes_ask_dollars")
    if val is None or val == "":
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _market_volume_usd(m: dict) -> float:
    """Total lifetime volume in USD notional. Kalshi publishes this as volume_fp."""
    val = m.get("volume_fp", 0)
    if val is None or val == "":
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


_TITLE_PREFIX_RE = re.compile(r"^(game|match|leg|set)\s*\d+\s*:\s*", flags=re.IGNORECASE)


def _market_event_start(m: dict) -> datetime | None:
    """Return the underlying game start time for a Kalshi market.

    `expected_expiration_time` lands shortly after the game ends and is the
    closest stable proxy for the game date. Used for canonical-event date
    matching against sharp sources — only date-precision is required.
    """
    val = m.get("expected_expiration_time") or m.get("close_time")
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _strip_title_prefix(s: str) -> str:
    """Drop leading "Game N:", "Match N:" style prefixes from a team segment."""
    # e.g. "Game 4: Los Angeles L" → "Los Angeles L"
    return _TITLE_PREFIX_RE.sub("", s).strip()


def _extract_teams_from_title(title: str) -> tuple[str, str]:
    """Split a Kalshi title into ``(home, away)``, honouring separator convention.

    Conventions:
        - " at " / " @ " → US convention: ``<visitor> at <host>`` → home = right, away = left.
        - " vs " / " v. " / " v " → European convention for sports in our scope
          (soccer): ``<home> vs <away>``. Kalshi uses "vs" for soccer almost
          exclusively; US-sport tickers use " at ".
        - Fallback: returns ``(title.strip(), "")`` so callers can bail out.
    """
    # US "away at home" ordering.
    for sep in (" at ", " @ "):
        if sep in title:
            left, right = title.split(sep, 1)
            right = right.split("?")[0].strip()
            right = right.replace("Winner", "").strip()
            # home = right (host), away = left (visitor)
            return _strip_title_prefix(right), _strip_title_prefix(left)
    # European "home vs away" ordering.
    for sep in (" vs ", " v. ", " v "):
        if sep in title:
            left, right = title.split(sep, 1)
            right = right.split("?")[0].strip()
            right = right.replace("Winner", "").strip()
            return _strip_title_prefix(left), _strip_title_prefix(right)
    return _strip_title_prefix(title.strip()), ""


def _ticker_suffix(ticker: str) -> str:
    """Return the canonical short-code suffix from a Kalshi market ticker.

    Example: ``KXNBAGAME-26APR18LALHOU-HOU`` → ``HOU``.
    Returns an uppercase stripped string; empty if the ticker has no hyphen.
    """
    if not ticker or "-" not in ticker:
        return ""
    return ticker.rsplit("-", 1)[-1].strip().upper()


def _series_prefix(ticker: str) -> str:
    """Return the Kalshi series prefix (chars before the first "-")."""
    if not ticker or "-" not in ticker:
        return ticker.upper()
    return ticker.split("-", 1)[0].upper()


def _resolve_canonical_team(event_ticker: str, market_ticker: str) -> str | None:
    """Map a market's ticker suffix to the matcher's canonical team alias.

    Returns ``None`` if the series has no code map or the suffix isn't recognized,
    so callers can fall back to title-derived names.
    """
    code_map = KALSHI_TICKER_CODES.get(_series_prefix(event_ticker))
    if not code_map:
        return None
    return code_map.get(_ticker_suffix(market_ticker).upper())


def _match_market_to_side(m: dict, home: str, away: str) -> str | None:
    """Return ``"home"`` / ``"away"`` if the market's ``yes_sub_title`` or
    ticker suffix identifies which team it represents. ``None`` if ambiguous.

    Strategy:
        1. Token-overlap match on ``yes_sub_title`` against home/away.
        2. Ticker suffix prefix-match against home/away tokens.
    Substring matching previously collided when teams shared a token
    ("Real" → both Real Madrid and Real Sociedad). Now requires shared
    3+ char tokens and forbids both-sides ambiguity.
    """
    home_l = (home or "").lower().strip()
    away_l = (away or "").lower().strip()
    if not home_l or not away_l:
        return None

    def _tokens(name: str) -> set[str]:
        return {w for w in name.split() if len(w) >= 3}

    home_tokens = _tokens(home_l)
    away_tokens = _tokens(away_l)

    sub = str(m.get("yes_sub_title", "") or "").lower().strip()
    if sub:
        sub_tokens = _tokens(sub)
        home_overlap = sub_tokens & home_tokens
        away_overlap = sub_tokens & away_tokens
        if home_overlap and not away_overlap:
            return "home"
        if away_overlap and not home_overlap:
            return "away"

    suffix = _ticker_suffix(m.get("ticker", "")).lower()
    if suffix and len(suffix) >= 2:
        # Ticker suffixes are short codes (HOU, LAL); accept prefix-of-token.
        starts_home = any(t.startswith(suffix) for t in home_tokens)
        starts_away = any(t.startswith(suffix) for t in away_tokens)
        if starts_home and not starts_away:
            return "home"
        if starts_away and not starts_home:
            return "away"

    return None


def parse_event(
    raw: dict,
    min_volume_usd: float = 100.0,
    fee_rate: float = 0.02,
) -> StandardEvent | None:
    """Parse one Kalshi event (container of binary markets) into a StandardEvent.

    Returns None if:
    - Series ticker not in KALSHI_SERIES_TO_SPORT
    - All markets below volume threshold
    - All prices exactly $0.50 (untraded)
    - Not enough active markets to form a valid moneyline/1x2
    """
    event_ticker = raw.get("event_ticker", "")
    sport = series_to_sport(event_ticker)
    if sport is None:
        return None

    raw_markets = [
        m for m in raw.get("markets", []) if m.get("status") == "active" and _market_volume_usd(m) >= min_volume_usd
    ]
    if not raw_markets:
        return None

    # Drop if all prices are exactly 0.50 (untraded).
    if all(_market_price_dollars(m) == 0.50 for m in raw_markets):
        return None

    is_no_draw = sport in _NO_DRAW_SPORTS
    title = raw.get("title", "")
    home, away = _extract_teams_from_title(title)
    if not home or not away:
        logger.info(
            "[kalshi] unresolved title (no home/away split): ticker=%s title=%r",
            event_ticker,
            title,
        )
        return None

    def _meta(m: dict) -> dict:
        return {
            "ticker": m.get("ticker"),
            "volume": _market_volume_usd(m),
        }

    # 2-way moneyline: exactly two contracts, complementary sides.
    # 3-way 1x2 (soccer): three contracts (home/draw/away).
    if is_no_draw and len(raw_markets) >= 2:
        # Match each of the top-two-by-volume markets to home or away by name.
        sorted_mkts = sorted(raw_markets, key=_market_volume_usd, reverse=True)[:2]
        sides = [_match_market_to_side(m, home, away) for m in sorted_mkts]
        if set(sides) != {"home", "away"}:
            logger.info(
                "[kalshi] unresolved moneyline sides: ticker=%s home=%r away=%r contracts=%s",
                event_ticker,
                home,
                away,
                [(m.get("ticker"), m.get("yes_sub_title")) for m in sorted_mkts],
            )
            return None
        # Override title-derived city names with canonical aliases when the
        # series has a ticker-code map (NBA/NHL/MLB). Pinnacle stores aliases
        # not cities, so this is required for matching.
        home_mkt = next(m for m, s in zip(sorted_mkts, sides, strict=False) if s == "home")
        away_mkt = next(m for m, s in zip(sorted_mkts, sides, strict=False) if s == "away")
        canonical_home = _resolve_canonical_team(event_ticker, home_mkt.get("ticker", ""))
        canonical_away = _resolve_canonical_team(event_ticker, away_mkt.get("ticker", ""))
        if canonical_home and canonical_away:
            home, away = canonical_home, canonical_away
        outcomes = [
            {
                "name": side,
                "odds": _price_to_odds(_market_price_dollars(m), fee_rate),
                "provider_meta": _meta(m),
            }
            for side, m in zip(sides, sorted_mkts, strict=False)
        ]
        market = {"type": "moneyline", "outcomes": outcomes}
    elif not is_no_draw and len(raw_markets) >= 3:
        # Identify draw market by the literal "draw" keyword in yes_sub_title.
        def is_draw(m: dict) -> bool:
            return "draw" in str(m.get("yes_sub_title", "")).lower()

        draw_mkts = [m for m in raw_markets if is_draw(m)]
        non_draw = [m for m in raw_markets if not is_draw(m)]
        if len(draw_mkts) != 1 or len(non_draw) < 2:
            return None
        # Sort non-draw markets by volume and assign sides by name matching.
        non_draw.sort(key=_market_volume_usd, reverse=True)
        top_two = non_draw[:2]
        sides = [_match_market_to_side(m, home, away) for m in top_two]
        if set(sides) != {"home", "away"}:
            logger.info(
                "[kalshi] unresolved 1x2 sides: ticker=%s home=%r away=%r contracts=%s",
                event_ticker,
                home,
                away,
                [(m.get("ticker"), m.get("yes_sub_title")) for m in top_two],
            )
            return None
        # Build ordered list: home, draw, away
        home_mkt = top_two[sides.index("home")]
        away_mkt = top_two[sides.index("away")]
        ordered = [
            ("home", home_mkt),
            ("draw", draw_mkts[0]),
            ("away", away_mkt),
        ]
        outcomes = [
            {
                "name": n,
                "odds": _price_to_odds(_market_price_dollars(m), fee_rate),
                "provider_meta": _meta(m),
            }
            for n, m in ordered
        ]
        market = {"type": "1x2", "outcomes": outcomes}
    else:
        return None

    start_time = next(
        (t for t in (_market_event_start(m) for m in raw_markets) if t is not None),
        None,
    )

    return StandardEvent(
        id=f"kalshi_{event_ticker}",
        name=raw.get("title", ""),
        sport=sport,
        markets=[market],
        provider="kalshi",
        url=f"https://kalshi.com/markets/{event_ticker}",
        home_team=home,
        away_team=away,
        start_time=start_time,
    )


class KalshiRetriever(Retriever):
    """Kalshi event-level retriever. Unauthenticated — market data is public.

    Paginates `/events?with_nested_markets=true&status=open` once per
    extraction cycle (cached for 60s) and dispatches by sport in `parse()`.
    Pre-fix this provider re-walked the entire ~10k-event paginated stream
    once per sport (17 times per cycle) AND bypassed HttpTransport entirely;
    now it uses the inherited transport's circuit breaker + 429 retry and
    reuses parsed events across sports within a cycle.
    """

    DEFAULT_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEFAULT_PAGE_LIMIT = 200
    # Hard cap on pagination loops — protects against malformed cursors.
    # 50 pages × 200 = 10k events, well above any observed event count.
    _MAX_PAGES = 50

    def __init__(self, config: dict, transport=None, circuit_breaker=None, rate_limit_config=None):
        if transport is None:
            transport = HttpTransport(circuit_breaker=circuit_breaker, rate_limit_config=rate_limit_config)
        super().__init__(config, transport)
        self.base_url = config.get("base_url", self.DEFAULT_BASE_URL)
        self.min_volume_usd = float(config.get("params", {}).get("min_volume_usd", 100))
        from ..constants import KALSHI_FEE_RATE

        self.fee_rate = float(config.get("params", {}).get("fee_rate", KALSHI_FEE_RATE))

        # Per-instance cross-sport cache. The factory builds a fresh retriever
        # at the start of every pipeline run, so the cache is naturally bounded
        # to one cycle's lifetime. Within a cycle, all 17 sport calls share it.
        self._cached_events_by_sport: dict[str, list[StandardEvent]] | None = None
        self._cache_at: float = 0.0

    def _get_sport_url(self, sport: str) -> str:
        # Kalshi's /events endpoint is sport-agnostic; we filter in parse().
        return f"{self.base_url}/events?status=open&with_nested_markets=true&limit={self.DEFAULT_PAGE_LIMIT}"

    async def _populate_cache(self) -> None:
        """Walk the sport-agnostic /events feed once; index by sport for O(1) dispatch."""
        url = self._get_sport_url("")
        cursor: str | None = None
        total_raw = 0
        events_by_sport: dict[str, list[StandardEvent]] = {}

        for page in range(self._MAX_PAGES):
            page_url = url + (f"&cursor={cursor}" if cursor else "")
            body = await self.transport.get(page_url, provider_id=self.provider_id, timeout=15)
            if not isinstance(body, dict):
                if page == 0:
                    logger.warning(f"[{self.provider_id}] events fetch returned no data")
                break
            events = body.get("events", [])
            total_raw += len(events)
            for raw in events:
                ev = parse_event(raw, min_volume_usd=self.min_volume_usd, fee_rate=self.fee_rate)
                if ev is None:
                    continue
                events_by_sport.setdefault(ev.sport, []).append(ev)
            cursor = body.get("cursor") or None
            if not cursor or not events:
                break
        else:
            logger.warning(
                f"[{self.provider_id}] hit MAX_PAGES={self._MAX_PAGES} cap "
                f"({total_raw} raw events) — pagination may be truncated"
            )

        self._cached_events_by_sport = events_by_sport
        self._cache_at = time.time()
        logger.info(
            f"[{self.provider_id}] cached {sum(len(v) for v in events_by_sport.values())} parsed events "
            f"across {len(events_by_sport)} sports (raw={total_raw})"
        )

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> list[StandardEvent]:
        """Return parsed events for a sport from the cycle-shared cache."""
        if self._cached_events_by_sport is None or (time.time() - self._cache_at) > _EVENTS_CACHE_TTL_SEC:
            await self._populate_cache()
        events = (self._cached_events_by_sport or {}).get(sport, [])
        return events[:limit] if limit and len(events) > limit else events

    def parse(self, data: dict, sport: str) -> list[StandardEvent]:
        # Kept for back-compat / unit tests. Live extraction goes through
        # _populate_cache which calls parse_event directly.
        out: list[StandardEvent] = []
        for raw in data.get("events", []):
            ev = parse_event(
                raw,
                min_volume_usd=self.min_volume_usd,
                fee_rate=self.fee_rate,
            )
            if ev is None:
                continue
            if ev.sport != sport:
                continue
            out.append(ev)
        return out
