"""Kalshi prediction-market extractor.

Pulls binary YES/NO contracts from Kalshi's public REST API and converts
them to StandardEvent moneyline / 1x2 / spread / total markets. Extraction
is unauthenticated — only placement (in the mirror workflow) needs API keys.

Spread/total markets live in entirely separate series from ML (e.g.
KXNBASPREAD-26MAY19CLENYK is separate from KXNBAGAME-26MAY19CLENYK). Each
spread/total event is a ladder of binary contracts at different point
lines ("Cleveland wins by over 7.5 points"). To stay consistent with
Pinnacle's main-line-only convention (and to make scanner comparison
direct), we only emit the rung whose absolute point is closest to
Pinnacle's current main line. Events Pinnacle doesn't cover are skipped
entirely — there is no fair-odds baseline to compare them against.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

import aiohttp

from ..core import Retriever, StandardEvent

logger = logging.getLogger(__name__)

# Ticker-prefix → canonical sport. Extend as new series appear.
KALSHI_SERIES_TO_SPORT: dict[str, str] = {
    # Re-discovered 2026-04-28 from kalshi /trade-api/v2/series listing
    # (9,862 open series). Previous mapping had 12 entries; many h2h-game
    # series were missing entirely (especially KXATPGAME / KXWTAGAME for
    # tennis — was only mapping KXTENNIS which is futures-only).
    # Basketball
    "KXNBAGAME": "basketball",
    "KXWNBAGAME": "basketball",
    "KXNCAABGAME": "basketball",
    "KXEUROLEAGUEGAME": "basketball",
    "KXABAGAME": "basketball",
    "KXACBGAME": "basketball",
    "KXARGLNBGAME": "basketball",
    "KXCBAGAME": "basketball",
    "KXVTBGAME": "basketball",
    "KXNBLGAME": "basketball",
    # American football
    "KXNFLGAME": "american_football",
    "KXNCAAFGAME": "american_football",
    # Baseball
    "KXMLBGAME": "baseball",
    "KXKBOGAME": "baseball",
    "KXNPBGAME": "baseball",
    # Ice hockey
    "KXNHLGAME": "ice_hockey",
    "KXAHLGAME": "ice_hockey",
    "KXKHLGAME": "ice_hockey",
    "KXSHLGAME": "ice_hockey",
    # Tennis — switched from KXTENNIS (futures-only) to actual h2h match series
    "KXATPGAME": "tennis",
    "KXATPMATCH": "tennis",
    "KXWTAGAME": "tennis",
    "KXWTAMATCH": "tennis",
    # Combat sports
    "KXUFC": "mma",
    "KXBOXING": "boxing",
    # Football — kept legacy KXEPL/KXUCL/KXWC + added live game-series
    "KXEPL": "football",
    "KXUCL": "football",
    "KXWC": "football",
    "KXUEL": "football",
    "KXUECL": "football",
    "KXLALIGAGAME": "football",
    "KXSERIEAGAME": "football",
    "KXBUNDESLIGAGAME": "football",
    "KXLIGUE1GAME": "football",
    "KXEREDIVISIE": "football",
    "KXMLSGAME": "football",
    "KXALLSVENSKANGAME": "football",
    "KXALEAGUEGAME": "football",
    "KXJLEAGUEGAME": "football",
    "KXKLEAGUEGAME": "football",
    "KXARGPREMDIVGAME": "football",
    "KXAFCACGAME": "football",
    "KXAFCCLGAME": "football",
    "KXAFCONGAME": "football",
}

# Spread series → sport. Mainline only — halves/quarters/team-totals/series-totals
# are out of scope per CLAUDE.md (we only support 1x2/ML/spread/total main lines).
KALSHI_SPREAD_SERIES_TO_SPORT: dict[str, str] = {
    # Basketball
    "KXNBASPREAD": "basketball",
    "KXWNBASPREAD": "basketball",
    "KXNCAAMBSPREAD": "basketball",
    "KXNCAAWBSPREAD": "basketball",
    "KXCBASPREAD": "basketball",
    "KXEUROLEAGUESPREAD": "basketball",
    "KXEUROCUPSPREAD": "basketball",
    "KXKBLSPREAD": "basketball",
    # American football
    "KXNFLSPREAD": "american_football",
    "KXNCAAFSPREAD": "american_football",
    "KXUFLSPREAD": "american_football",
    # Baseball
    "KXMLBSPREAD": "baseball",
    "KXKBOSPREAD": "baseball",
    "KXNPBSPREAD": "baseball",
    "KXWBCSPREAD": "baseball",
    "KXNCAABBSPREAD": "baseball",
    # Ice hockey
    "KXNHLSPREAD": "ice_hockey",
    "KXWOMHOCKEYSPREAD": "ice_hockey",
    # Tennis — two spellings exist in Kalshi's catalog
    "KXATPGAMESPREAD": "tennis",
    "KXATPGSPREAD": "tennis",
    # Football (soccer)
    "KXEPLSPREAD": "football",
    "KXLALIGASPREAD": "football",
    "KXBUNDESLIGASPREAD": "football",
    "KXLIGUE1SPREAD": "football",
    "KXSERIEASPREAD": "football",
    "KXMLSSPREAD": "football",
    "KXALEAGUESPREAD": "football",
    "KXEREDIVISIESPREAD": "football",
    "KXEFLCUPSPREAD": "football",
    "KXEFLCHAMPIONSHIPSPREAD": "football",
    "KXFACUPSPREAD": "football",
    "KXUCLSPREAD": "football",
    "KXUELSPREAD": "football",
    "KXUECLSPREAD": "football",
    "KXLIGAMXSPREAD": "football",
    "KXSAUDIPLSPREAD": "football",
    "KXFIFASPREAD": "football",
    "KXSOCCERSPREAD": "football",
    "KXINTLFRIENDLYSPREAD": "football",
    "KXTACAPORTSPREAD": "football",
    "KXCOPADELREYSPREAD": "football",
    "KXCOPADOBRASILSPREAD": "football",
    "KXCOPPAITALIASPREAD": "football",
    "KXCOUPEDEFRANCESPREAD": "football",
    "KXDFBPOKALSPREAD": "football",
    "KXARGPREMDIVSPREAD": "football",
    "KXBRASILEIROSPREAD": "football",
}

# Total series → sport. Same scope rules as spread.
KALSHI_TOTAL_SERIES_TO_SPORT: dict[str, str] = {
    # Basketball
    "KXNBATOTAL": "basketball",
    "KXWNBATOTAL": "basketball",
    "KXNCAAMBTOTAL": "basketball",
    "KXNCAAWBTOTAL": "basketball",
    "KXCBATOTAL": "basketball",
    "KXEUROLEAGUETOTAL": "basketball",
    "KXEUROCUPTOTAL": "basketball",
    "KXKBLTOTAL": "basketball",
    # American football
    "KXNFLTOTAL": "american_football",
    "KXNCAAFTOTAL": "american_football",
    "KXUFLTOTAL": "american_football",
    # Baseball
    "KXMLBTOTAL": "baseball",
    "KXKBOTOTAL": "baseball",
    "KXWBCTOTAL": "baseball",
    "KXNCAABBTOTAL": "baseball",
    # Ice hockey
    "KXNHLTOTAL": "ice_hockey",
    "KXWOMHOCKEYTOTAL": "ice_hockey",
    # Tennis — two spellings
    "KXATPGAMETOTAL": "tennis",
    "KXATPGTOTAL": "tennis",
    # Football (soccer)
    "KXEPLTOTAL": "football",
    "KXLALIGATOTAL": "football",
    "KXBUNDESLIGATOTAL": "football",
    "KXLIGUE1TOTAL": "football",
    "KXSERIEATOTAL": "football",
    "KXMLSTOTAL": "football",
    "KXALEAGUETOTAL": "football",
    "KXEREDIVISIETOTAL": "football",
    "KXEFLCUPTOTAL": "football",
    "KXEFLCHAMPIONSHIPTOTAL": "football",
    "KXFACUPTOTAL": "football",
    "KXUCLTOTAL": "football",
    "KXUELTOTAL": "football",
    "KXUECLTOTAL": "football",
    "KXLIGAMXTOTAL": "football",
    "KXSAUDIPLTOTAL": "football",
    "KXFIFATOTAL": "football",
    "KXSOCCERTOTAL": "football",
    "KXINTLFRIENDLYTOTAL": "football",
    "KXTACAPORTTOTAL": "football",
    "KXCOPADELREYTOTAL": "football",
    "KXCOPADOBRASILTOTAL": "football",
    "KXCOPPAITALIATOTAL": "football",
    "KXCOUPEDEFRANCETOTAL": "football",
    "KXDFBPOKALTOTAL": "football",
    "KXARGPREMDIVTOTAL": "football",
    "KXBRASILEIROTOTAL": "football",
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
    """Resolve a Kalshi ML event/market ticker to our canonical sport name.

    Uses longest-prefix match so more specific prefixes win (e.g. KXNCAAB
    before KX).
    """
    for prefix in sorted(KALSHI_SERIES_TO_SPORT.keys(), key=len, reverse=True):
        if ticker.startswith(prefix):
            return KALSHI_SERIES_TO_SPORT[prefix]
    return None


def spread_series_to_sport(ticker: str) -> str | None:
    """Resolve a Kalshi spread series ticker (e.g. KXNBASPREAD) to a sport."""
    for prefix in sorted(KALSHI_SPREAD_SERIES_TO_SPORT.keys(), key=len, reverse=True):
        if ticker.startswith(prefix):
            return KALSHI_SPREAD_SERIES_TO_SPORT[prefix]
    return None


def total_series_to_sport(ticker: str) -> str | None:
    """Resolve a Kalshi total series ticker (e.g. KXNBATOTAL) to a sport."""
    for prefix in sorted(KALSHI_TOTAL_SERIES_TO_SPORT.keys(), key=len, reverse=True):
        if ticker.startswith(prefix):
            return KALSHI_TOTAL_SERIES_TO_SPORT[prefix]
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

# Spread/total events tack a market label onto the title, e.g.
# "Game 1: Cleveland at New York: Spread", "...: Total Points",
# "...: Total Goals", "...: Total Runs", "...: Total Games".
# Strip these before feeding the title to _extract_teams_from_title.
_MARKET_LABEL_RE = re.compile(
    r"\s*:?\s*(?:spread|total(?:\s+\w+)?|handicap|over\s*/\s*under|o\s*/\s*u)\s*$",
    flags=re.IGNORECASE,
)


def _strip_market_label(title: str) -> str:
    """Remove trailing market-type label (": Spread", " Total", etc.) from a Kalshi title."""
    return _MARKET_LABEL_RE.sub("", title or "").strip()


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
        1. ``yes_sub_title`` substring match against home/away (case-insensitive).
        2. Ticker suffix substring match against home/away.
    Both sides must not match simultaneously for the match to count.
    """
    home_l = (home or "").lower().strip()
    away_l = (away or "").lower().strip()
    if not home_l or not away_l:
        return None

    sub = str(m.get("yes_sub_title", "") or "").lower().strip()
    if sub:
        in_home = sub in home_l or home_l in sub
        in_away = sub in away_l or away_l in sub
        if in_home and not in_away:
            return "home"
        if in_away and not in_home:
            return "away"

    suffix = _ticker_suffix(m.get("ticker", "")).lower()
    if suffix:
        in_home = suffix in home_l or home_l.startswith(suffix)
        in_away = suffix in away_l or away_l.startswith(suffix)
        if in_home and not in_away:
            return "home"
        if in_away and not in_home:
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


# ── Spread / total parsing ──────────────────────────────────────────────────
#
# Kalshi's spread / total markets are binary YES/NO contracts on a fixed
# point line, e.g. "Cleveland wins by over 7.5 points" or "Over 16.5 games".
# A single event ships as a ladder of these contracts at different point
# values (a typical NBA spread event has ~20 rungs from ±3 to ±29).
#
# For a contract "X wins by over N.5":
#   YES_price implies P(X covers spread of -N.5) → home odds at line -N.5
#   NO_price  implies P(X does NOT cover)        → away odds at line +N.5
# (since "X does not win by over N.5" = "Y wins or X by ≤ N.5" = "Y covers +N.5")
#
# For a contract "Over N.5 games/points/runs":
#   YES_price → over outcome at point N.5
#   NO_price  → under outcome at point N.5
#
# We pick the rung whose absolute point is closest to Pinnacle's current
# main line so the scanner can compare line-for-line. Events Pinnacle
# doesn't cover yield no rung-target and are dropped upstream.


# "Boston wins by over 7.5 points" / "New York wins by over 12.5 points" etc.
_SPREAD_SUB_RE = re.compile(
    r"^(?P<team>.+?)\s+wins?\s+by\s+over\s+(?P<line>\d+(?:\.\d+)?)\b",
    flags=re.IGNORECASE,
)

# "Over 16.5 games", "Over 210.5 points", "Over 8.5 goals", "Over 7.5 runs"
_TOTAL_SUB_RE = re.compile(
    r"^over\s+(?P<line>\d+(?:\.\d+)?)\b",
    flags=re.IGNORECASE,
)


def _market_no_price_dollars(m: dict) -> float:
    """The NO contract's ask price ($0–1) — what you PAY to buy NO.

    Mirror of _market_price_dollars (yes_ask). Live schema uses string values
    ("0.2500"), tests use floats — cast both. 0 if unquoted/degenerate.
    """
    val = m.get("no_ask_dollars")
    if val is None or val == "":
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _no_side_odds(m: dict, fee_rate: float) -> float:
    """Decimal odds for BUYING the NO contract, priced off its no_ask.

    The NO ask is ``1 - yes_BID`` — NOT ``1 - yes_ask``. Deriving the NO price
    from yes_ask yields the NO *bid* (the sell price) and overstates every
    under/away value bet by the full bid-ask spread (a ~5¢ phantom edge on a
    typical Kalshi soccer total). Always price the NO side off the published
    ``no_ask_dollars``. Returns 0 if there is no NO ask quoted or it is
    degenerate (≤0 or ≥1) — the caller then drops the market.
    """
    no_price = _market_no_price_dollars(m)
    if not (0.0 < no_price < 1.0):
        return 0.0
    return _price_to_odds(no_price, fee_rate)


def _parse_spread_rung(m: dict, home: str, away: str) -> tuple[str, float] | None:
    """Return (side, abs_point) for a spread rung, or None if unparseable.

    side ∈ {"home", "away"} indicates which side the YES contract favors:
        "home" → YES = home wins by over <abs_point>  → home spread -abs_point
        "away" → YES = away wins by over <abs_point>  → away spread -abs_point
    """
    sub = str(m.get("yes_sub_title", "") or "").strip()
    match = _SPREAD_SUB_RE.match(sub)
    if not match:
        return None
    team_str = match.group("team").strip().lower()
    try:
        abs_point = float(match.group("line"))
    except ValueError:
        return None
    home_l = (home or "").lower().strip()
    away_l = (away or "").lower().strip()
    if not home_l or not away_l:
        return None
    in_home = team_str in home_l or home_l in team_str
    in_away = team_str in away_l or away_l in team_str
    if in_home and not in_away:
        return "home", abs_point
    if in_away and not in_home:
        return "away", abs_point
    return None


def _parse_total_rung(m: dict) -> float | None:
    """Return the point value for a total rung, or None if unparseable."""
    sub = str(m.get("yes_sub_title", "") or "").strip()
    match = _TOTAL_SUB_RE.match(sub)
    if not match:
        return None
    try:
        return float(match.group("line"))
    except ValueError:
        return None


def _pick_closest_rung(candidates: list[tuple[float, dict]], target_abs: float) -> tuple[float, dict] | None:
    """From a list of (abs_point, market) rungs, return the one closest to target_abs."""
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(c[0] - target_abs))


def parse_spread_event(
    raw: dict,
    home: str,
    away: str,
    target_abs_point: float,
    min_volume_usd: float = 100.0,
    fee_rate: float = 0.02,
) -> dict | None:
    """Parse a Kalshi spread event into a single {"type":"spread", ...} market dict.

    Picks the rung whose absolute point is closest to `target_abs_point`
    (Pinnacle's current main line) and emits home/away outcomes for that line.
    Returns None if no rung parses cleanly or all are sub-volume.
    """
    raw_markets = [
        m for m in raw.get("markets", []) if m.get("status") == "active" and _market_volume_usd(m) >= min_volume_usd
    ]
    if not raw_markets:
        return None

    home_rungs: list[tuple[float, dict]] = []
    away_rungs: list[tuple[float, dict]] = []
    for m in raw_markets:
        parsed = _parse_spread_rung(m, home, away)
        if parsed is None:
            continue
        side, abs_pt = parsed
        if side == "home":
            home_rungs.append((abs_pt, m))
        else:
            away_rungs.append((abs_pt, m))

    # Pick the closest-to-target on whichever side has matching rungs.
    # Both sides should be present for a symmetric spread, but Kalshi often
    # ladders only the favored side. We can still emit both outcomes off a
    # single rung — YES = favored covers, NO = underdog covers same line.
    chosen = _pick_closest_rung(home_rungs, target_abs_point) or _pick_closest_rung(away_rungs, target_abs_point)
    if chosen is None:
        return None
    abs_point, mkt = chosen
    yes_side = "home" if (chosen in home_rungs) else "away"
    yes_price = _market_price_dollars(mkt)
    if yes_price <= 0.0 or yes_price >= 1.0:
        return None
    yes_odds = _price_to_odds(yes_price, fee_rate)
    no_odds = _no_side_odds(mkt, fee_rate)
    if no_odds <= 0.0:
        return None
    if yes_side == "home":
        home_odds, home_point = yes_odds, -abs_point
        away_odds, away_point = no_odds, abs_point
    else:
        away_odds, away_point = yes_odds, -abs_point
        home_odds, home_point = no_odds, abs_point
    outcomes = [
        {
            "name": "home",
            "odds": home_odds,
            "point": home_point,
            "provider_meta": {"ticker": mkt.get("ticker"), "volume": _market_volume_usd(mkt), "yes_side": yes_side},
        },
        {
            "name": "away",
            "odds": away_odds,
            "point": away_point,
            "provider_meta": {"ticker": mkt.get("ticker"), "volume": _market_volume_usd(mkt), "yes_side": yes_side},
        },
    ]
    return {"type": "spread", "outcomes": outcomes}


def parse_total_event(
    raw: dict,
    target_point: float,
    min_volume_usd: float = 100.0,
    fee_rate: float = 0.02,
) -> dict | None:
    """Parse a Kalshi total event into a single {"type":"total", ...} market dict.

    Picks the rung whose point is closest to `target_point` (Pinnacle's
    current main line) and emits over/under outcomes at that line.
    """
    raw_markets = [
        m for m in raw.get("markets", []) if m.get("status") == "active" and _market_volume_usd(m) >= min_volume_usd
    ]
    if not raw_markets:
        return None

    rungs: list[tuple[float, dict]] = []
    for m in raw_markets:
        pt = _parse_total_rung(m)
        if pt is None:
            continue
        rungs.append((pt, m))

    chosen = _pick_closest_rung(rungs, target_point)
    if chosen is None:
        return None
    point, mkt = chosen
    yes_price = _market_price_dollars(mkt)
    if yes_price <= 0.0 or yes_price >= 1.0:
        return None
    over_odds = _price_to_odds(yes_price, fee_rate)
    under_odds = _no_side_odds(mkt, fee_rate)
    if under_odds <= 0.0:
        return None
    meta = {"ticker": mkt.get("ticker"), "volume": _market_volume_usd(mkt)}
    return {
        "type": "total",
        "outcomes": [
            {"name": "over", "odds": over_odds, "point": point, "provider_meta": meta},
            {"name": "under", "odds": under_odds, "point": point, "provider_meta": meta},
        ],
    }


# ── Pinnacle event-index loader ─────────────────────────────────────────────
#
# To honour the "only fetch what Pinnacle has" constraint, we load a snapshot
# of Pinnacle events for the sport (plus their current spread/total main
# lines) once per extract() run, then filter every Kalshi event against it.


def _load_pinnacle_event_index(sport: str, lookahead_days: int = 14) -> dict:
    """Return a dict keyed by (home_team, away_team, YYYYMMDD) → {spread_point, total_point}.

    home_team and away_team are the canonical normalized aliases as stored on
    `events.home_team` / `events.away_team` (already normalized by the matcher
    at insertion time). spread_point and total_point are the most-recent
    smallest-absolute-value lines for that event (the de-facto main line).
    """
    from sqlalchemy import text

    from ..db.models import get_session

    session = get_session()
    try:
        rows = session.execute(
            text(
                """
                WITH pin AS (
                    SELECT e.home_team, e.away_team, e.start_time,
                           o.market, o.point,
                           ROW_NUMBER() OVER (
                               PARTITION BY e.id, o.market
                               ORDER BY ABS(o.point) ASC, o.updated_at DESC
                           ) AS rn
                    FROM events e
                    JOIN odds o ON o.event_id = e.id
                    WHERE o.provider_id = 'pinnacle'
                      AND e.sport = :sport
                      AND e.start_time BETWEEN NOW() AND NOW() + (:days || ' days')::INTERVAL
                      AND o.market IN ('spread', 'total')
                )
                SELECT home_team, away_team, start_time, market, point
                FROM pin
                WHERE rn = 1
                """
            ),
            {"sport": sport, "days": lookahead_days},
        ).fetchall()
    finally:
        session.close()

    index: dict[tuple[str, str, str], dict] = {}
    for home_team, away_team, start_time, market, point in rows:
        date_str = start_time.strftime("%Y%m%d")
        key = (home_team, away_team, date_str)
        entry = index.setdefault(key, {"spread_point": None, "total_point": None})
        if market == "spread" and point is not None:
            entry["spread_point"] = abs(float(point))
        elif market == "total" and point is not None:
            entry["total_point"] = float(point)
    return index


def _load_pinnacle_event_keys(sport: str, lookahead_days: int = 14) -> set[tuple[str, str, str]]:
    """Return just the set of (home, away, YYYYMMDD) keys Pinnacle has for `sport`.

    Used by the ML pre-filter where we don't need line points, only event
    presence. A separate query (no JOIN on odds.market) so ML-only events
    (where Pinnacle has moneyline but no spread/total yet) are still included.
    """
    from sqlalchemy import text

    from ..db.models import get_session

    session = get_session()
    try:
        rows = session.execute(
            text(
                """
                SELECT DISTINCT e.home_team, e.away_team, e.start_time
                FROM events e
                JOIN odds o ON o.event_id = e.id
                WHERE o.provider_id = 'pinnacle'
                  AND e.sport = :sport
                  AND e.start_time BETWEEN NOW() AND NOW() + (:days || ' days')::INTERVAL
                """
            ),
            {"sport": sport, "days": lookahead_days},
        ).fetchall()
    finally:
        session.close()
    return {(h, a, t.strftime("%Y%m%d")) for h, a, t in rows}


def _kalshi_event_key(
    home: str,
    away: str,
    start_time: datetime | None,
) -> tuple[str, str, str] | None:
    """Build the lookup key from a Kalshi-parsed event for matching against
    the Pinnacle index. Returns None if normalization fails or start_time
    is missing.
    """
    if not home or not away or start_time is None:
        return None
    from ..matching.normalizer import normalize_team_name

    return (
        normalize_team_name(home),
        normalize_team_name(away),
        start_time.strftime("%Y%m%d"),
    )


class KalshiRetriever(Retriever):
    """Kalshi event-level retriever. Unauthenticated — market data is public.

    Paginates `/events?with_nested_markets=true&status=open` until the API
    stops returning a `cursor`. Filters by sport post-fetch.
    """

    DEFAULT_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEFAULT_PAGE_LIMIT = 200

    def __init__(self, config: dict, circuit_breaker=None, rate_limit_config=None):
        super().__init__(config)
        self.base_url = config.get("base_url", self.DEFAULT_BASE_URL)
        self.min_volume_usd = float(config.get("params", {}).get("min_volume_usd", 100))
        from ..constants import KALSHI_FEE_RATE

        self.fee_rate = float(config.get("params", {}).get("fee_rate", KALSHI_FEE_RATE))
        self._circuit_breaker = circuit_breaker

    # Inter-request spacing to avoid kalshi's per-IP rate limit. With 12+
    # series_tickers across all sports + multi-page pagination, hammering
    # /events back-to-back trips 429 within seconds (verified in production
    # logs 2026-04-28). 1.5s delay between fetches keeps us under the cap
    # while only adding ~15-25s to a full kalshi run.
    INTER_REQUEST_DELAY_S = 1.5

    def _series_tickers_for_sport(self, sport: str) -> list[str]:
        """Inverse of KALSHI_SERIES_TO_SPORT — ML series_tickers for this sport."""
        return [prefix for prefix, mapped_sport in KALSHI_SERIES_TO_SPORT.items() if mapped_sport == sport]

    def _spread_series_for_sport(self, sport: str) -> list[str]:
        return [prefix for prefix, mapped_sport in KALSHI_SPREAD_SERIES_TO_SPORT.items() if mapped_sport == sport]

    def _total_series_for_sport(self, sport: str) -> list[str]:
        return [prefix for prefix, mapped_sport in KALSHI_TOTAL_SERIES_TO_SPORT.items() if mapped_sport == sport]

    def _get_sport_url(self, sport: str) -> str:
        # Legacy method kept for compatibility; the new extract() uses
        # series_ticker filtering directly per sport.
        return f"{self.base_url}/events?status=open&with_nested_markets=true&limit={self.DEFAULT_PAGE_LIMIT}"

    async def _fetch_series_pages(
        self,
        session: aiohttp.ClientSession,
        series_ticker: str,
        delay_first: bool,
    ):
        """Async generator: yield each raw event dict from /events?series_ticker=X across pages.

        Rate-limit-aware: sleeps INTER_REQUEST_DELAY_S before each request
        (skips the first if delay_first=False so back-to-back series fetches
        don't double-pay the cooldown). On 429, backs off 5s and retries once
        before giving up the series.
        """
        cursor: str | None = None
        base = (
            f"{self.base_url}/events?status=open&with_nested_markets=true"
            f"&limit={self.DEFAULT_PAGE_LIMIT}&series_ticker={series_ticker}"
        )
        # Per series, allow up to 5 pages (1000 events).
        for page_idx in range(5):
            if delay_first or page_idx > 0:
                await asyncio.sleep(self.INTER_REQUEST_DELAY_S)
            page_url = base + (f"&cursor={cursor}" if cursor else "")
            try:
                async with session.get(page_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 429:
                        logger.info(f"[kalshi] 429 on series={series_ticker}; backing off 5s")
                        await asyncio.sleep(5.0)
                        async with session.get(page_url, timeout=aiohttp.ClientTimeout(total=15)) as resp2:
                            if resp2.status == 429:
                                logger.warning(f"[kalshi] 429 persistent on series={series_ticker}; skipping")
                                return
                            resp2.raise_for_status()
                            body = await resp2.json()
                    else:
                        resp.raise_for_status()
                        body = await resp.json()
            except Exception as e:
                logger.warning(f"[kalshi] fetch failed series={series_ticker} cursor={cursor}: {e}")
                return
            events = body.get("events", [])
            for raw in events:
                yield raw
            cursor = body.get("cursor") or None
            if not cursor or not events:
                return

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> list[StandardEvent]:
        """Fetch open Kalshi events for the sport, filtered by Pinnacle coverage.

        Three series families are queried per sport:
          1. ML series (KXNBAGAME, KXATPMATCH, …) → moneyline / 1x2 markets
          2. SPREAD series (KXNBASPREAD, KXATPGAMESPREAD, …) → spread market
          3. TOTAL series (KXNBATOTAL, KXATPGAMETOTAL, …) → total market

        All three are pre-filtered by querying Pinnacle's events for the
        sport up front: events whose (normalized_home, normalized_away,
        YYYYMMDD) tuple isn't in Pinnacle's set are dropped before parsing
        further. For spread/total, the rung closest to Pinnacle's current
        main line is picked (CLAUDE.md scope: main lines only). If Pinnacle
        has no events for the sport, all Kalshi fetches are skipped — there
        is no fair-odds baseline to compare against.
        """
        # Load Pinnacle keys + spread/total line index in parallel (single DB
        # connection each, indexed query, takes <50ms in production).
        pin_keys, pin_index = await asyncio.gather(
            asyncio.to_thread(_load_pinnacle_event_keys, sport),
            asyncio.to_thread(_load_pinnacle_event_index, sport),
        )
        if not pin_keys:
            logger.info(f"[kalshi] no Pinnacle events for sport={sport}; skipping all Kalshi fetches")
            return []

        ml_series = self._series_tickers_for_sport(sport)
        spread_series = self._spread_series_for_sport(sport)
        total_series = self._total_series_for_sport(sport)
        all_series = ml_series + spread_series + total_series
        if not all_series:
            logger.debug(f"[kalshi] no series configured for sport={sport}")
            return []

        parsed: list[StandardEvent] = []
        total_raw = 0
        first_request = True

        async with aiohttp.ClientSession() as session:
            # --- ML series ---
            for series in ml_series:
                async for raw in self._fetch_series_pages(session, series, delay_first=not first_request):
                    first_request = False
                    total_raw += 1
                    ev = parse_event(raw, min_volume_usd=self.min_volume_usd, fee_rate=self.fee_rate)
                    if ev is None or ev.sport != sport:
                        continue
                    key = _kalshi_event_key(ev.home_team, ev.away_team, ev.start_time)
                    if key is None or key not in pin_keys:
                        continue
                    parsed.append(ev)
                    if limit and len(parsed) >= limit:
                        break
                if limit and len(parsed) >= limit:
                    break

            # --- SPREAD series ---
            for series in spread_series:
                if limit and len(parsed) >= limit:
                    break
                async for raw in self._fetch_series_pages(session, series, delay_first=not first_request):
                    first_request = False
                    total_raw += 1
                    ev = self._parse_line_event(raw, sport, market_kind="spread", pin_index=pin_index)
                    if ev is not None:
                        parsed.append(ev)
                        if limit and len(parsed) >= limit:
                            break

            # --- TOTAL series ---
            for series in total_series:
                if limit and len(parsed) >= limit:
                    break
                async for raw in self._fetch_series_pages(session, series, delay_first=not first_request):
                    first_request = False
                    total_raw += 1
                    ev = self._parse_line_event(raw, sport, market_kind="total", pin_index=pin_index)
                    if ev is not None:
                        parsed.append(ev)
                        if limit and len(parsed) >= limit:
                            break

        logger.info(
            f"[kalshi] sport={sport}: fetched {total_raw} raw events across "
            f"{len(ml_series)} ML + {len(spread_series)} spread + {len(total_series)} total series; "
            f"{len(parsed)} kept (Pinnacle-matched)"
        )
        if limit and len(parsed) > limit:
            parsed = parsed[:limit]
        return parsed

    def _parse_line_event(
        self,
        raw: dict,
        sport: str,
        market_kind: str,
        pin_index: dict,
    ) -> StandardEvent | None:
        """Parse a Kalshi spread or total event into a StandardEvent.

        Returns None unless (a) we can extract home/away/date from the title,
        (b) the canonical key is in Pinnacle's index, and (c) at least one
        ladder rung parses cleanly. Picks the rung whose point is closest to
        Pinnacle's current main line.
        """
        title = raw.get("title", "") or ""
        clean = _strip_market_label(title)
        home, away = _extract_teams_from_title(clean)
        if not home or not away:
            return None
        # Override with canonical aliases when ticker codes exist (NBA/NHL/MLB).
        # spread/total events use the same {SERIES}-{YYMMMDD}{AWAYTEAM}{HOMETEAM}
        # event_ticker convention as ML, so the series prefix maps via the
        # same KALSHI_TICKER_CODES dict but keyed on the ML-series prefix.
        # We can't reliably override here without knowing the ML prefix; fall
        # back to the title strings — the normalizer + matcher handle the
        # canonicalisation on lookup.
        start_time = next(
            (t for t in (_market_event_start(m) for m in raw.get("markets", [])) if t is not None),
            None,
        )
        key = _kalshi_event_key(home, away, start_time)
        if key is None or key not in pin_index:
            return None
        pin_entry = pin_index[key]
        if market_kind == "spread":
            target = pin_entry.get("spread_point")
            if target is None:
                return None
            market = parse_spread_event(
                raw,
                home=home,
                away=away,
                target_abs_point=target,
                min_volume_usd=self.min_volume_usd,
                fee_rate=self.fee_rate,
            )
        elif market_kind == "total":
            target = pin_entry.get("total_point")
            if target is None:
                return None
            market = parse_total_event(
                raw,
                target_point=target,
                min_volume_usd=self.min_volume_usd,
                fee_rate=self.fee_rate,
            )
        else:
            return None
        if market is None:
            return None
        event_ticker = raw.get("event_ticker", "")
        return StandardEvent(
            id=f"kalshi_{event_ticker}",
            name=clean,
            sport=sport,
            markets=[market],
            provider="kalshi",
            url=f"https://kalshi.com/markets/{event_ticker}",
            home_team=home,
            away_team=away,
            start_time=start_time,
        )

    def parse(self, data: dict, sport: str) -> list[StandardEvent]:
        """Legacy ML-only parser. Kept for callers that pass a pre-fetched
        payload. The new extract() flow parses inline with Pinnacle-aware
        filtering, so production traffic does not flow through here.
        """
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
