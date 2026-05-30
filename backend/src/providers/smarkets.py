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
    - Smarkets geoblocks datacenter IPs (including Hetzner DE) with a 403
      "Security Check" HTML page. Configure proxy_url in providers.yaml
      (SOCKS5 URL) to route via the Bahnhof residential gost proxy.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from ..core import Retriever, StandardEvent

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
    which Betty doesn't track.
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
    all_ids = set(last_by_id.keys()) | {str(k) for k in quotes_by_id}

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


# Market-type name (Smarkets `market_type.name`) -> our canonical market
# label.
#
# NOTE: Smarkets is signal-only (we're IP-banned from placing), and
# spread/total markets emit one contract per line — the "keep first line
# only" collapse produces misleading data. Until that's fixed as a
# follow-up (review issue I3), we only surface 1x2 / moneyline from
# Smarkets and skip handicap / over_under entirely.
_MARKET_TYPE_TO_LABEL: dict[str, str] = {
    "WINNER_3_WAY": "1x2",
    "WINNER_2_WAY": "moneyline",
    "MATCH_WINNER": "moneyline",
}


def classify_market_type(name: str, market_type: Any) -> str | None:
    """Return the canonical market label ('1x2' / 'moneyline') for a Smarkets
    market, or None to skip it. Spread / total markets are intentionally
    skipped on Smarkets (see module-level note).

    `market_type` may be a dict `{"name": "WINNER_3_WAY"}` (live API) or a
    bare string (defensive — older endpoints).
    """
    mt_name: str | None = None
    if isinstance(market_type, dict):
        mt_name = market_type.get("name")
    elif isinstance(market_type, str):
        mt_name = market_type
    if mt_name and mt_name in _MARKET_TYPE_TO_LABEL:
        return _MARKET_TYPE_TO_LABEL[mt_name]

    # Fallback on human name heuristics — only for winner markets. We do
    # NOT fall back to handicap / totals here (see module note).
    n = (name or "").lower()
    if "winner" in n or "match result" in n or "full-time result" in n:
        return "1x2" if "3-way" in n or "draw" in n else "moneyline"
    return None


def extract_home_away_from_event_name(name: str) -> tuple[str, str]:
    """Split a Smarkets event `name` (e.g. "Nottm Forest vs Burnley") into
    (home, away). Smarkets uses "home vs away" ordering consistently.

    Returns ``("", "")`` if no separator is found.
    """
    for sep in (" vs ", " v. ", " v "):
        if sep in name:
            left, right = name.split(sep, 1)
            return left.strip(), right.strip()
    return "", ""


def _contract_side(contract: dict) -> str | None:
    """Map a Smarkets contract dict to our canonical outcome name
    (``"home"`` / ``"draw"`` / ``"away"``), or None if not one of those.

    Prefers ``contract_type.name`` (authoritative: HOME/DRAW/AWAY), falling
    back to the ``slug`` field. Everything else (OVER/UNDER, YES/NO for
    non-binary markets) returns None.
    """
    ct = contract.get("contract_type") or {}
    ct_name = (ct.get("name") or "").upper().strip() if isinstance(ct, dict) else ""
    if ct_name == "HOME":
        return "home"
    if ct_name == "DRAW":
        return "draw"
    if ct_name == "AWAY":
        return "away"
    slug = (contract.get("slug") or "").lower().strip()
    if slug in ("home", "draw", "away"):
        return slug
    return None


class SmarketsRetriever(Retriever):
    """Smarkets signal-only retriever. Uses public JSON API, no auth.

    Flow:
        1. /events/?state=upcoming&type_domain=<sport>&type=<sport>_match
           -> paginated event list (pagination.next_page).
        2. For each in-scope event, /events/{id}/markets/ -> market list.
        3. For each kept market, concurrently fetch
           /markets/{id}/last_executed_prices/ and /markets/{id}/quotes/.

    Aggregates into StandardEvent. Bounded semaphore limits per-market
    concurrency so we don't hammer the API during pagination.
    """

    DEFAULT_BASE_URL = "https://api.smarkets.com/v3"
    MAX_PAGES = 20
    # Was 8; smarkets' rate limit fires at that concurrency and every
    # /markets/{id}/quotes/ call came back 429. 3 keeps us inside the
    # limit while still being ~2x faster than serial.
    CONCURRENT_MARKET_FETCHES = 3

    def __init__(
        self,
        config: dict,
        circuit_breaker=None,
        rate_limit_config=None,
    ):
        super().__init__(config)
        self.base_url = config.get("base_url", self.DEFAULT_BASE_URL).rstrip("/")

        import os

        # Smarkets geoblocks most IPs (Sweden, Hetzner DE) — always route through
        # the shared Bahnhof SOCKS5 proxy, same as Pinnacle and friends.
        # Config can still override via explicit proxy_url (e.g. for testing).
        proxy = config.get("proxy_url")
        if proxy is None:
            proxy = (config.get("params") or {}).get("proxy_url")
        if not proxy or (isinstance(proxy, str) and proxy.startswith("${")):
            proxy = os.environ.get("PROXY_URL", "")
        self.proxy_url: str | None = proxy if proxy else None

        self.min_trades_24h = int((config.get("params") or {}).get("min_trades_24h", 1))
        self._circuit_breaker = circuit_breaker

    # ------------------------------------------------------------------
    # URL / filter helpers (unit-tested)
    # ------------------------------------------------------------------

    def _sport_to_type_domain(self, sport: str) -> str:
        """Our canonical sport -> Smarkets type_domain slug.

        We map via the inverse of SMARKETS_TYPE_SCOPE_TO_SPORT so the plan's
        hyphenated form (`ice-hockey`) round-trips; however Smarkets'
        `type_domain` parameter accepts the underscored form too, so we
        prefer the sport name as-is (it matches `type=<sport>_match`).
        """
        return sport

    def _get_sport_url(self, sport: str) -> str:
        domain = self._sport_to_type_domain(sport)
        return f"{self.base_url}/events/?state=upcoming&type_domain={domain}&type={domain}_match&limit=100"

    def filter_events_by_sport(self, events: list[dict], sport: str) -> list[dict]:
        """Keep only events whose `type` matches `<sport>_match`.

        `type_scope` is always null in live data — we filter on `type`.
        Returns [] for sports not in SMARKETS_TYPE_SCOPE_TO_SPORT.values().
        """
        if sport not in SMARKETS_TYPE_SCOPE_TO_SPORT.values():
            return []
        target_type = f"{sport}_match"
        return [e for e in events if e.get("type") == target_type]

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _fetch_json(self, session: aiohttp.ClientSession, url: str) -> dict | None:
        # SOCKS5 proxies are wired via ProxyConnector at session construction,
        # not via the aiohttp `proxy=` kwarg (which is HTTP-only). For HTTP/
        # HTTPS proxies we pass `proxy=` as before.
        try:
            kwargs: dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=15)}
            if self.proxy_url and not self.proxy_url.startswith("socks"):
                kwargs["proxy"] = self.proxy_url
            async with session.get(url, **kwargs) as resp:
                if resp.status != 200:
                    logger.warning("[smarkets] %s on %s", resp.status, url)
                    return None
                return await resp.json()
        except Exception as e:
            logger.warning("[smarkets] fetch failed %s: %s", url, e)
            return None

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> list[StandardEvent]:
        if sport not in SMARKETS_TYPE_SCOPE_TO_SPORT.values():
            logger.info("[smarkets] skip unsupported sport=%s", sport)
            return []

        events_raw: list[dict] = []

        # Build a ProxyConnector for SOCKS5 proxies (the aiohttp native `proxy=`
        # kwarg only speaks HTTP/HTTPS; smarkets requires Bahnhof SOCKS5 or
        # every fetch dies with "Server disconnected" on the TLS handshake).
        connector = None
        if self.proxy_url and self.proxy_url.startswith("socks"):
            try:
                from aiohttp_socks import ProxyConnector

                connector = ProxyConnector.from_url(self.proxy_url)
            except ImportError:
                logger.warning("[smarkets] aiohttp_socks not installed; proxy will not be used")

        session_kwargs: dict[str, Any] = {}
        if connector is not None:
            session_kwargs["connector"] = connector

        async with aiohttp.ClientSession(**session_kwargs) as session:
            url = self._get_sport_url(sport)
            in_scope: list[dict] = []
            for _ in range(self.MAX_PAGES):
                body = await self._fetch_json(session, url)
                if not body:
                    break
                page_events = body.get("events", [])
                if not page_events:
                    # Empty page ⇒ no more events for this sport (Smarkets orders
                    # by date). Walking the remaining pages just burns slow proxy
                    # round-trips — bail. Fixes 0-event sports (e.g. tennis) eating
                    # the full MAX_PAGES budget (~20s) every cycle.
                    break
                events_raw.extend(page_events)
                in_scope.extend(self.filter_events_by_sport(page_events, sport))
                # Stop paginating once we have enough — the health check sets
                # limit=1 and pre-fix would walk all 20 pages before bailing.
                if limit and len(in_scope) >= limit:
                    break
                nxt = (body.get("pagination") or {}).get("next_page")
                if not nxt:
                    break
                url = (
                    f"{self.base_url}/events/{nxt}"
                    if nxt.startswith("?")
                    else (f"{self.base_url}{nxt}" if nxt.startswith("/") else nxt)
                )

            logger.info(
                "[smarkets] %s events fetched, %s in-scope for %s",
                len(events_raw),
                len(in_scope),
                sport,
            )

            # Apply the caller's cap BEFORE the per-event fan-out — each event
            # triggers up to 4 HTTP calls, so processing the entire 900+ event
            # list would always time out.
            if limit and len(in_scope) > limit:
                in_scope = in_scope[:limit]

            sem = asyncio.Semaphore(self.CONCURRENT_MARKET_FETCHES)

            async def build_event(ev_raw: dict) -> StandardEvent | None:
                async with sem:
                    return await self._build_event(session, ev_raw, sport)

            results = await asyncio.gather(*(build_event(e) for e in in_scope))
            events = [r for r in results if r is not None]

        return events

    async def _build_event(
        self,
        session: aiohttp.ClientSession,
        ev_raw: dict,
        sport: str,
    ) -> StandardEvent | None:
        eid = ev_raw.get("id")
        if not eid:
            return None

        # Populate home/away from the event name ("Nottm Forest vs Burnley").
        ev_name = ev_raw.get("name", "") or ""
        home_team, away_team = extract_home_away_from_event_name(ev_name)
        if not home_team or not away_team:
            # Without sides we can't label outcomes; signal-only contribution
            # would be zero anyway.
            logger.debug(
                "[smarkets] skip event %s: unresolvable home/away from name=%r",
                eid,
                ev_name,
            )
            return None

        mkts_body = await self._fetch_json(session, f"{self.base_url}/events/{eid}/markets/")
        mkts = (mkts_body or {}).get("markets") or []
        if not mkts:
            return None

        kept: list[dict] = []
        for m in mkts:
            label = classify_market_type(m.get("name", ""), m.get("market_type"))
            if label is None:
                continue
            # Keep only the first occurrence per label (most representative;
            # Smarkets orders by display_order).
            if any(k["type"] == label for k in kept):
                continue
            mid = m.get("id")
            if not mid:
                continue
            contracts_body, prices_body, quotes_body = await asyncio.gather(
                self._fetch_json(session, f"{self.base_url}/markets/{mid}/contracts/"),
                self._fetch_json(
                    session,
                    f"{self.base_url}/markets/{mid}/last_executed_prices/",
                ),
                self._fetch_json(session, f"{self.base_url}/markets/{mid}/quotes/"),
            )
            contracts = (contracts_body or {}).get("contracts") or []
            if not contracts:
                continue

            # contract_id (str) → canonical side ("home"/"draw"/"away")
            side_by_cid: dict[str, str] = {}
            for c in contracts:
                cid = c.get("id")
                if cid is None:
                    continue
                side = _contract_side(c)
                if side is None:
                    continue
                side_by_cid[str(cid)] = side

            odds_by_cid = parse_market_prices(
                {
                    "last_executed_prices": (prices_body or {}).get("last_executed_prices", {}),
                    "quotes": (quotes_body or {}),
                }
            )
            if not odds_by_cid:
                continue

            outcomes: list[dict] = []
            for cid, odds in odds_by_cid.items():
                side = side_by_cid.get(str(cid))
                if side is None:
                    # Unlabeled contract for a winner market = data issue; skip.
                    continue
                outcomes.append({"name": side, "odds": odds})

            # Sanity: enforce expected arity per market label.
            sides = {o["name"] for o in outcomes}
            if label == "1x2" and sides != {"home", "draw", "away"}:
                continue
            if label == "moneyline" and sides != {"home", "away"}:
                continue

            kept.append({"type": label, "outcomes": outcomes})

        if not kept:
            return None

        full_slug = ev_raw.get("full_slug") or ""
        return StandardEvent(
            id=f"smarkets_{eid}",
            name=ev_name,
            sport=sport,
            markets=kept,
            provider="smarkets",
            url=f"https://smarkets.com{full_slug}" if full_slug else "",
            start_time=ev_raw.get("start_datetime") or "",
            home_team=home_team,
            away_team=away_team,
        )

    def parse(self, data: Any, sport: str) -> list[StandardEvent]:
        # Not used — extract() overrides the base flow end-to-end.
        return []
