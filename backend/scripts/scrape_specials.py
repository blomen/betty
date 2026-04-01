#!/usr/bin/env python3
"""
Scrape odds boosts directly from provider sportsbook websites.

Sources:
  - betsson.com/sv/odds/odds-boost (Gecko V2 API — Betsson/Betsafe/NordicBet)

Output: backend/data/specials.json

Usage:
  python scripts/scrape_specials.py           # Scrape and print results
  python scripts/scrape_specials.py --save    # Save to data/specials.json
  python scripts/scrape_specials.py -v        # Verbose output
"""

import argparse
import asyncio
import json
import re
import time
from dataclasses import dataclass, asdict, replace, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# Provider name normalization map
PROVIDER_ALIASES: dict[str, str] = {
    "betsson": "betsson",
    "betsafe": "betsafe",
    "nordicbet": "nordicbet",
    "spelklubben": "spelklubben",
    "unibet": "unibet",
    "leovegas": "leovegas",
    "comeon": "comeon",
    "hajper": "hajper",
    "lyllo": "lyllo",
    "bethard": "bethard",
    "betinia": "betinia",
    "campobet": "campobet",
    "swiper": "swiper",
    "lodur": "lodur",
    "dbet": "dbet",
    "quickcasino": "quickcasino",
    "vbet": "vbet",
    "interwetten": "interwetten",
    "mrgreen": "mrgreen",
}

SPORT_KEYWORDS: dict[str, list[str]] = {
    "football": [
        "fotboll", "football", "soccer", "futbol", "fútbol",
        "premier league", "champions league",
        "allsvenskan", "la liga", "serie a", "bundesliga", "ligue 1",
        "europa league", "vm kval", "nations league", "conference league",
        "fa cup", "carabao", "copa del rey", "superettan", "eredivisie",
        "süper lig", "super lig", "lig a",
        "manchester", "arsenal", "liverpool", "chelsea", "tottenham",
        "barcelona", "real madrid", "atletico", "juventus", "inter milan",
        "ac milan", "bayern", "dortmund", "psg", "napoli",
        "aston villa", "newcastle", "west ham", "brighton", "wolves",
        "crystal palace", "everton", "fulham", "brentford", "bournemouth",
        "sunderland", "leicester", "nottingham", "ipswich", "southampton",
        "malmö ff", "aik", "djurgården", "hammarby", "ifk göteborg",
        "häcken", "elfsborg", "norrköping", "kalmar", "sirius",
        "playoff fotbolls-vm", "vm-kval",
        "liga mx", "primera division", "primera a", "pro league",
        "superligaen", "liga professionell",
        "bologna", "valencia", "lyon", "marseille", "roma", "lazio",
        "fiorentina", "atalanta", "sporting", "benfica", "porto",
        "ajax", "feyenoord", "psv", "celtic", "rangers",
        "sevilla", "villarreal", "betis", "sociedad",
        "svenska cupen", "fa cup", "coppa italia", "dfb pokal",
        "coupe de france", "copa del rey",
        "halvtid", "hörnor", "slutställning", "resultat",
        "vinner", "båda vinner",
    ],
    "ice_hockey": [
        "hockey", "ishockey", "shl", "nhl", "hockeyallsvenskan",
        "vinterspelen", "winter olympics", "tre kronor", "os herrar",
        "rögle", "växjö", "brynäs", "färjestad", "frölunda",
        "luleå", "skellefteå", "örebro", "linköping", "leksand",
        "timrå", "oskarshamn", "hv71", "modo",
    ],
    "tennis": ["tennis", "atp", "wta", "grand slam", "wimbledon", "us open",
               "french open", "australian open", "roland garros"],
    "basketball": ["basket", "basketball", "nba", "euroleague", "ncaa"],
    "handball": ["handboll", "handball"],
    "mma": ["mma", "ufc", "bellator"],
    "esports": ["esport", "cs2", "counter-strike", "league of legends", "dota"],
    "american_football": ["nfl", "super bowl", "american football",
                          "patriots", "seahawks", "touchdown"],
}

# Output path — use centralized paths for bundled mode support
def _get_data_dir() -> Path:
    try:
        from src.paths import get_data_dir
        return get_data_dir()
    except ImportError:
        return Path(__file__).parent.parent / "data"

DATA_DIR = _get_data_dir()


@dataclass
class Special:
    """A single odds boost."""
    provider: str
    title: str              # enriched: "market_label: selection_label"
    description: str = ""
    original_odds: Optional[float] = None
    boosted_odds: Optional[float] = None
    boost_pct: Optional[float] = None   # pre-calculated boost percentage
    max_stake: Optional[float] = None
    category: str = "boost"   # boost, superboost
    sport: str = "unknown"
    league: str = ""          # e.g. "Premier League"
    event: str = ""           # e.g. "Arsenal vs Sunderland"
    event_time: Optional[str] = None  # ISO datetime of the event
    expires_at: Optional[str] = None
    url: str = ""
    scraped_at: str = ""
    source: str = ""
    market_label: str = ""              # raw market label
    shared_providers: Optional[list] = None  # providers sharing this boost


@dataclass
class BoostProviderLog:
    """Metrics for a single provider's boost scrape."""
    provider_id: str
    scraper_type: str
    status: str = "success"  # success, failed, skipped
    duration_seconds: float = 0.0
    boosts_found: int = 0
    error_message: Optional[str] = None


@dataclass
class BoostRunLog:
    """Aggregate log for an entire boost scrape run."""
    run_id: str = ""
    scraped_at: str = ""
    total_boosts: int = 0
    duration_seconds: float = 0.0
    providers: list = field(default_factory=list)  # list[BoostProviderLog]


def detect_sport(text: str) -> str:
    """Detect sport from text using keywords."""
    text_lower = text.lower()
    for sport, keywords in SPORT_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return sport
    return "unknown"


# ============ Provider Boost Pages ============

# Config path for boost definitions — use centralized paths for bundled mode
def _get_config_dir() -> Path:
    try:
        from src.paths import get_config_dir
        return get_config_dir()
    except ImportError:
        return Path(__file__).parent.parent / "src" / "config"

CONFIG_DIR = _get_config_dir()


def _load_boost_config() -> list[dict]:
    """Load enabled boost entries from providers.yaml."""
    config_path = CONFIG_DIR / "providers.yaml"
    if not config_path.exists():
        return []
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        boosts_cfg = config.get("boosts", {})
        entries = []
        for name, entry in boosts_cfg.items():
            if entry.get("enabled") and entry.get("type"):
                entries.append({
                    "name": name,
                    "type": entry["type"],
                    "url": entry.get("url", ""),
                    "primary_provider": entry.get("primary_provider", name),
                    "shared_with": entry.get("shared_with", []),
                    "integration": entry.get("integration", ""),
                })
        return entries
    except Exception:
        return []


async def scrape_provider_boosts(verbose: bool = False) -> tuple[list[Special], list[BoostProviderLog]]:
    """Scrape odds boosts from all configured providers in providers.yaml.

    Returns (specials, provider_logs) tuple.
    """
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            if verbose:
                print("  [provider_boosts] playwright not installed, skipping")
            return [], []

    boost_configs = _load_boost_config()
    if not boost_configs:
        if verbose:
            print("  No enabled boost configs found in providers.yaml")
        return [], []

    all_boosts: list[Special] = []
    provider_logs: list[BoostProviderLog] = []
    now_iso = datetime.now().isoformat()

    # Separate API-based scrapers (no browser needed) from browser-based ones
    api_types = {"kambi", "altenar", "betconstruct"}
    api_configs = [c for c in boost_configs if c["type"] in api_types]
    browser_configs = [c for c in boost_configs if c["type"] not in api_types]

    # Run API-based scrapers first (fast, no browser)
    last_altenar = False
    for cfg in api_configs:
        provider_id = cfg["primary_provider"]
        boost_url = cfg["url"]
        shared = cfg["shared_with"]
        t0 = time.monotonic()

        # Delay between consecutive Altenar scrapers to avoid rate limiting
        if cfg["type"] == "altenar" and last_altenar:
            await asyncio.sleep(3)

        if verbose:
            print(f"  [{cfg['name']}] {provider_id}: {boost_url or cfg.get('integration','')} (type={cfg['type']})")

        try:
            if cfg["type"] == "kambi":
                boosts = await _scrape_kambi_boosts(
                    provider_id, boost_url, now_iso, verbose
                )
            elif cfg["type"] == "altenar":
                integration = cfg.get("integration", "")
                boosts = await _scrape_altenar_boosts(
                    provider_id, integration, now_iso, verbose
                )
            elif cfg["type"] == "betconstruct":
                boosts = await _scrape_betconstruct_boosts(
                    provider_id, now_iso, verbose
                )
            else:
                continue
            for b in boosts:
                b.shared_providers = shared if shared else None
            all_boosts.extend(boosts)
            provider_logs.append(BoostProviderLog(
                provider_id=provider_id, scraper_type=cfg["type"],
                status="success", duration_seconds=round(time.monotonic() - t0, 2),
                boosts_found=len(boosts),
            ))
            if verbose:
                print(f"  {provider_id}: {len(boosts)} boosts found")
        except Exception as e:
            provider_logs.append(BoostProviderLog(
                provider_id=provider_id, scraper_type=cfg["type"],
                status="failed", duration_seconds=round(time.monotonic() - t0, 2),
                error_message=str(e)[:500],
            ))
            if verbose:
                print(f"  {provider_id} failed: {e}")
        last_altenar = cfg["type"] == "altenar"

    # Run browser-based scrapers (Gecko V2 etc.)
    if browser_configs:
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled'],
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    locale='sv-SE',
                )

                for cfg in browser_configs:
                    provider_id = cfg["primary_provider"]
                    boost_url = cfg["url"]
                    boost_type = cfg["type"]
                    shared = cfg["shared_with"]
                    t0 = time.monotonic()

                    if verbose:
                        print(f"  [{cfg['name']}] {provider_id}: {boost_url} (type={boost_type})")

                    try:
                        if boost_type == "gecko_v2":
                            boosts = await _scrape_gecko_boosts(
                                context, provider_id, boost_url, now_iso, verbose
                            )
                        elif boost_type == "interwetten":
                            boosts = await _scrape_interwetten_boosts(
                                context, provider_id, boost_url, now_iso, verbose
                            )
                        elif boost_type == "comeon":
                            boosts = await _scrape_comeon_boosts(
                                context, provider_id, boost_url, now_iso, verbose
                            )
                        elif boost_type == "spectate":
                            boosts = await _scrape_spectate_boosts(
                                context, provider_id, boost_url, now_iso, verbose
                            )
                        else:
                            if verbose:
                                print(f"    Unsupported boost type: {boost_type}")
                            continue

                        # Tag shared providers
                        for b in boosts:
                            b.shared_providers = shared if shared else None

                        all_boosts.extend(boosts)
                        provider_logs.append(BoostProviderLog(
                            provider_id=provider_id, scraper_type=boost_type,
                            status="success", duration_seconds=round(time.monotonic() - t0, 2),
                            boosts_found=len(boosts),
                        ))
                        if verbose:
                            print(f"  {provider_id}: {len(boosts)} boosts found")
                            if shared:
                                print(f"    (also available on: {', '.join(shared)})")
                    except Exception as e:
                        provider_logs.append(BoostProviderLog(
                            provider_id=provider_id, scraper_type=boost_type,
                            status="failed", duration_seconds=round(time.monotonic() - t0, 2),
                            error_message=str(e)[:500],
                        ))
                        if verbose:
                            print(f"  {provider_id} failed: {e}")
                            import traceback
                            traceback.print_exc()

                await browser.close()
        except Exception as e:
            if verbose:
                print(f"  Browser launch failed: {e}")

    return all_boosts, provider_logs


async def _scrape_kambi_boosts(
    provider_id: str, api_url: str, now_iso: str, verbose: bool
) -> list[Special]:
    """
    Scrape Kambi odds boosts via the public offering API.

    Kambi operators (Unibet, etc.) expose boosted selections through named
    groups like "unibet_featured". The API returns events with betOffers
    containing the boosted odds. Original (pre-boost) odds are NOT available.

    These are typically prop/special bets (player goals, BTTS combos, etc.)
    """
    import aiohttp

    boosts: list[Special] = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                api_url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"Accept": "application/json"},
            ) as resp:
                if resp.status != 200:
                    if verbose:
                        print(f"    [{provider_id}] API returned {resp.status}")
                    return boosts

                data = await resp.json()

        events = data.get("events", [])
        if verbose:
            print(f"    [{provider_id}] {len(events)} boost events from API")

        for ev in events:
            event_obj = ev.get("event", {})
            event_name = event_obj.get("name", "")
            event_start = event_obj.get("start")
            sport_name = event_obj.get("sport", "")
            group_name = event_obj.get("group", "")

            # Detect sport from Kambi sport name + group
            sport = detect_sport(f"{event_name} {sport_name} {group_name}")

            for offer in ev.get("betOffers", []):
                criterion = offer.get("criterion", {})
                crit_label = criterion.get("label", "")
                tags = offer.get("tags", [])

                for outcome in offer.get("outcomes", []):
                    odds_milli = outcome.get("odds", 0)
                    if odds_milli <= 0:
                        continue

                    odds = odds_milli / 1000.0
                    label = outcome.get("label", "")

                    # Build descriptive title
                    if crit_label and label and label.lower() != crit_label.lower():
                        title = f"{crit_label}: {label}"
                    elif crit_label:
                        title = crit_label
                    elif label:
                        title = label
                    else:
                        continue

                    boosts.append(Special(
                        provider=provider_id,
                        title=title,
                        event=event_name,
                        original_odds=None,  # Kambi doesn't expose pre-boost odds
                        boosted_odds=odds,
                        boost_pct=None,  # Can't calculate without original
                        max_stake=None,
                        sport=sport,
                        league=group_name,
                        category="boost",
                        expires_at=None,
                        event_time=event_start,
                        source=provider_id,
                        scraped_at=now_iso,
                        url=api_url,
                        market_label=crit_label,
                    ))

    except Exception as e:
        if verbose:
            print(f"    [{provider_id}] Error: {e}")

    return boosts


ALTENAR_API_BASE = "https://sb2frontend-altenar2.biahosted.com/api"

# Altenar sport IDs to canonical names
ALTENAR_SPORT_MAP: dict[int, str] = {
    66: "football", 67: "basketball", 68: "tennis", 70: "ice_hockey",
    73: "handball", 75: "american_football", 76: "baseball", 84: "mma",
    101: "rugby", 145: "esports",
}


async def _scrape_altenar_boosts(
    provider_id: str, integration: str, now_iso: str, verbose: bool
) -> list[Special]:
    """
    Scrape Altenar odds boosts via the public widget API.

    Strategy:
    1. GetEvents per sport → ALL events (not just 20 highlighted ones)
    2. GetEventDetails per event (parallel, 30 concurrent) → boosts[] array

    GetHighlights only returns 20 events/sport, missing ~80% of boosts.
    GetEvents returns the full event list (~1500 across 3 sports), and
    parallel detail fetches complete in ~4 seconds.

    Boost object structure:
      - price: original (pre-boost) odds
      - boostInfo.price: boosted (enhanced) odds
      - boostInfo.isBetOfTheDay: "dagens spel" flag
      - boostInfo.isLimitedTime: limited time offer
      - boostInfo.property: 1=standard, 3=guldboost
      - boostInfo.endDate: expiry datetime
      - odds[].marketId + selectionId: which selection is boosted
    """
    import aiohttp

    boosts: list[Special] = []
    base_params = {
        "culture": "sv-SE",
        "timezoneOffset": "0",
        "integration": integration,
        "deviceType": "1",
        "numFormat": "sv-SE",
    }

    try:
        connector = aiohttp.TCPConnector(limit=20)
        async with aiohttp.ClientSession(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=30),
            connector=connector,
        ) as session:
            # Collect ALL events across sports via GetEvents (full list)
            event_map: dict[int, dict] = {}  # event_id -> event data
            sport_for_event: dict[int, str] = {}

            # Only scan the 3 sports that actually have boosts
            boost_sport_ids = [66, 67, 70]  # football, basketball, ice_hockey
            for sport_id in boost_sport_ids:
                params = {**base_params, "sportId": str(sport_id)}
                try:
                    async with session.get(
                        f"{ALTENAR_API_BASE}/widget/GetEvents",
                        params=params,
                    ) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                except Exception:
                    continue

                events = data.get("events", [])
                competitors = {c["id"]: c for c in data.get("competitors", [])}
                champs = {c["id"]: c for c in data.get("champs", [])}

                for ev in events:
                    eid = ev["id"]
                    if eid in event_map:
                        continue
                    comp_ids = ev.get("competitorIds", [])
                    comp_names = [
                        competitors.get(cid, {}).get("name", "").strip()
                        for cid in comp_ids
                    ]
                    champ = champs.get(ev.get("champId", 0), {})
                    ev["_comp_names"] = comp_names
                    ev["_league"] = champ.get("name", "")
                    event_map[eid] = ev
                    sport_for_event[eid] = ALTENAR_SPORT_MAP.get(sport_id, "unknown")

            if verbose:
                print(f"    [{provider_id}] {len(event_map)} events across "
                      f"{len(set(sport_for_event.values()))} sports")

            # Fetch event details in batches to find boosts
            # Altenar API drops connections above ~30 concurrent, so
            # process in batches of 50 with semaphore=20.
            BATCH_SIZE = 50
            sem = asyncio.Semaphore(20)

            async def _fetch_boosts(eid: int) -> tuple[int, list, dict] | None:
                async with sem:
                    params = {**base_params, "eventId": str(eid)}
                    try:
                        async with session.get(
                            f"{ALTENAR_API_BASE}/widget/GetEventDetails",
                            params=params,
                        ) as resp:
                            if resp.status != 200:
                                return None
                            detail = await resp.json()
                    except Exception:
                        return None
                    ev_boosts = detail.get("boosts", [])
                    if not ev_boosts:
                        return None
                    return (eid, ev_boosts, detail)

            all_eids = list(event_map.keys())
            results: list = []
            for i in range(0, len(all_eids), BATCH_SIZE):
                batch = all_eids[i : i + BATCH_SIZE]
                batch_results = await asyncio.gather(
                    *[_fetch_boosts(eid) for eid in batch],
                    return_exceptions=True,
                )
                results.extend(batch_results)

            events_with_boosts = 0
            seen_sel_keys: set[tuple] = set()  # deduplicate boosts across events by selectionIds
            for result in results:
                if result is None or isinstance(result, Exception):
                    continue

                eid, ev_boosts, detail = result
                events_with_boosts += 1
                ev = event_map[eid]

                markets = {m["id"]: m for m in detail.get("markets", [])}
                odds_idx = {o["id"]: o for o in detail.get("odds", [])}

                comp_names = ev.get("_comp_names", [])
                event_name = " vs ".join(comp_names) if comp_names else ev.get("name", "")
                league = ev.get("_league", "")
                sport = sport_for_event.get(eid, "unknown")
                event_start = ev.get("startDate")

                for b in ev_boosts:
                    original_price = b.get("price")
                    bi = b.get("boostInfo", {})
                    boosted_price = bi.get("price")

                    if not original_price or not boosted_price:
                        continue
                    if float(original_price) >= float(boosted_price):
                        continue

                    # Deduplicate: same boost returned by multiple event detail responses
                    sel_key = tuple(sorted(o.get("selectionId", 0) for o in b.get("odds", [])))
                    if sel_key in seen_sel_keys:
                        continue
                    seen_sel_keys.add(sel_key)

                    is_bet_of_day = bi.get("isBetOfTheDay", False)
                    prop = bi.get("property", 0)
                    end_date = bi.get("endDate")

                    # Resolve selection labels
                    sel_labels = []
                    market_labels = []
                    for oi in b.get("odds", []):
                        mid = oi.get("marketId")
                        sid = oi.get("selectionId")
                        market = markets.get(mid, {})
                        sel = odds_idx.get(sid, {})
                        market_name = market.get("name", "")
                        sel_name = sel.get("name", "").strip()

                        if market_name and sel_name and market_name.lower() != sel_name.lower():
                            sel_labels.append(f"{market_name}: {sel_name}")
                        elif sel_name:
                            sel_labels.append(sel_name)
                        elif market_name:
                            sel_labels.append(market_name)
                        if market_name:
                            market_labels.append(market_name)

                    if not sel_labels:
                        continue

                    title = ", ".join(sel_labels)

                    # Category: guldboost (property=3), bet of day, or standard
                    if is_bet_of_day:
                        category = "superboost"
                    elif prop == 3:
                        category = "superboost"
                    else:
                        category = "boost"

                    orig_f = float(original_price)
                    boosted_f = float(boosted_price)
                    boost_pct_val = ((boosted_f / orig_f) - 1) * 100

                    boosts.append(Special(
                        provider=provider_id,
                        title=title,
                        event=event_name,
                        original_odds=orig_f,
                        boosted_odds=boosted_f,
                        boost_pct=round(boost_pct_val, 1),
                        max_stake=200,
                        sport=sport if sport != "unknown" else detect_sport(
                            f"{title} {event_name} {league}"
                        ),
                        league=league,
                        category=category,
                        expires_at=end_date,
                        event_time=event_start,
                        source=provider_id,
                        scraped_at=now_iso,
                        url=f"https://www.{provider_id}.se",
                        market_label=", ".join(market_labels) if market_labels else "",
                    ))

            if verbose:
                print(f"    [{provider_id}] {events_with_boosts}/{len(event_map)} events had boosts")

    except Exception as e:
        if verbose:
            print(f"    [{provider_id}] Error: {e}")
            import traceback
            traceback.print_exc()

    if verbose:
        with_orig = sum(1 for b in boosts if b.original_odds is not None)
        print(f"    [{provider_id}] {len(boosts)} boosts parsed ({with_orig} with original odds)")

    return boosts


def _parse_gecko_time(time_info: str) -> Optional[str]:
    """Parse Gecko V2 timeInfo into ISO datetime string.

    Formats: "HH:MM", "Ikväll", "Imorgon", "YYYY-MM-DD...", or empty.
    Times are in CET/CEST, converted to UTC.
    """
    if not time_info or not time_info.strip():
        return None

    time_info = time_info.strip()
    cet = ZoneInfo("Europe/Stockholm")
    now_cet = datetime.now(cet)

    if time_info in ("Ikväll", "Ikvall"):
        # "Tonight" — use today 23:59 CET
        dt = now_cet.replace(hour=23, minute=59, second=0, microsecond=0)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    if time_info in ("Imorgon",):
        # "Tomorrow" — use tomorrow 23:59 CET
        dt = (now_cet + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    # "HH:MM" — today at that time CET
    hm_match = re.match(r'^(\d{2}):(\d{2})$', time_info)
    if hm_match:
        h, m = int(hm_match.group(1)), int(hm_match.group(2))
        dt = now_cet.replace(hour=h, minute=m, second=0, microsecond=0)
        # Don't bump to tomorrow — if time passed, it's today's event (in progress).
        # filter_expired() downstream handles removing started events.
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    # "YYYY-MM-DD..." — full date string
    if re.match(r'^\d{4}-\d{2}-\d{2}', time_info):
        try:
            dt = datetime.fromisoformat(time_info)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=cet)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass

    return None


async def _scrape_gecko_boosts(
    context, provider_id: str, boost_url: str, now_iso: str, verbose: bool
) -> list[Special]:
    """
    Scrape Gecko V2 (Betsson group) odds boosts from the rendered DOM.

    The boost page renders inside Shadow DOM web components. We pierce the
    shadow DOM to extract all text, then parse the repeating card pattern:

        [League]
        [Team1]
        [Team2]
        [Boost description]
        Förut  Nu
        [original_odds]  [boosted_odds]
        Maxinsats [amount]
        [time info]

    The globalbonuses API only returns betslip campaign boosts ("Boost to 7",
    "Boost to 10") which are NOT the boosts visible on the odds-boost page.
    DOM scraping is the only reliable way to get the actual page boosts.
    """
    import asyncio

    page = await context.new_page()
    boosts: list[Special] = []

    try:
        await page.goto(boost_url, wait_until='load', timeout=30000)
        await asyncio.sleep(3)

        # Handle cookie consent (try multiple common selectors)
        for selector in [
            '#onetrust-accept-btn-handler',
            '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
            'button:has-text("Acceptera")', 'button:has-text("Accept")',
            'button:has-text("Godkänn")', 'button:has-text("Tillåt")',
            '[data-testid="cookie-accept"]',
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue

        # Wait for page to render after cookie dismissal
        await asyncio.sleep(4)

        # Determine target frame: main page or sportsbook iframe
        # Some OBG sites (Bethard) load the sportsbook in a cross-origin iframe
        target_frame = page
        for f in page.frames:
            if f.name == 'sb-iframe' or 'playground' in f.url:
                target_frame = f
                if verbose:
                    print(f"    [{provider_id}] Using sportsbook iframe: {f.url[:80]}")
                await asyncio.sleep(3)
                break

        # Wait for boost content to render inside the SPA.
        # The OBG Angular SPA loads the root sports page first, then internally
        # navigates to the boost tab. Fixed sleeps are unreliable — poll for the
        # "Förut" text node which only appears once boost cards render.
        for attempt in range(15):  # up to 15s
            has_content = await target_frame.evaluate(r"""() => {
                function checkForut(root) {
                    if (!root) return false;
                    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                    while (walker.nextNode()) {
                        if (walker.currentNode.textContent.trim() === 'Förut') return true;
                    }
                    const elements = root.querySelectorAll('*');
                    for (const el of elements) {
                        if (el.shadowRoot && checkForut(el.shadowRoot)) return true;
                    }
                    return false;
                }
                return checkForut(document);
            }""")
            if has_content:
                if verbose:
                    print(f"    [{provider_id}] Boost content detected after {attempt + 1}s")
                break
            await asyncio.sleep(1)
        else:
            if verbose:
                print(f"    [{provider_id}] Boost content not detected after 15s — may have 0 boosts")

        # Scroll incrementally to trigger lazy loading of all boost cards
        for i in range(12):
            await target_frame.evaluate(f"window.scrollTo(0, {(i + 1) * 500})")
            await asyncio.sleep(1)

        # Final scroll to bottom
        await target_frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(3)

        # Extract boost card data from shadow DOM (pierce all shadow roots)
        raw_cards = await target_frame.evaluate(r"""() => {
            // Recursively extract all text from shadow DOMs
            function getAllLines(root) {
                const lines = [];
                if (!root) return lines;
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) {
                    const text = walker.currentNode.textContent.trim();
                    if (text) lines.push(text);
                }
                const elements = root.querySelectorAll('*');
                for (const el of elements) {
                    if (el.shadowRoot) {
                        lines.push(...getAllLines(el.shadowRoot));
                    }
                }
                return lines;
            }

            const lines = getAllLines(document);

            // Find all boost cards by the "Förut" / "Nu" pattern
            const cards = [];
            for (let i = 0; i < lines.length; i++) {
                if (lines[i] !== 'Förut') continue;
                // Expected pattern: "Förut", "Nu", odds1, odds2
                if (i + 3 >= lines.length || lines[i + 1] !== 'Nu') continue;

                const forut = parseFloat(lines[i + 2]);
                const nu = parseFloat(lines[i + 3]);
                if (isNaN(forut) || isNaN(nu) || nu <= forut) continue;

                // Look backward for card context
                let description = '';
                let team1 = '';
                let team2 = '';
                let league = '';
                let leaguePath = '';
                let maxStake = null;

                // The description is the line right before "Förut"
                if (i - 1 >= 0) description = lines[i - 1];

                // Team names are typically 2 lines before description
                // Pattern: ...League, Team1, Team2, Description, Förut, Nu, odds, odds...
                if (i - 2 >= 0) team2 = lines[i - 2];
                if (i - 3 >= 0) team1 = lines[i - 3];

                // League is before team names, look for "Fotboll / ..." pattern
                for (let j = i - 4; j >= Math.max(0, i - 8); j--) {
                    if (lines[j].includes(' / ') || lines[j].includes('Fotboll') ||
                        lines[j].includes('Premier League') || lines[j].includes('La Liga') ||
                        lines[j].includes('Serie A') || lines[j].includes('Bundesliga') ||
                        lines[j].includes('Champions League') || lines[j].includes('Copa')) {
                        leaguePath = lines[j];
                        break;
                    }
                }

                // Look forward for max stake and time info
                let timeInfo = '';
                for (let j = i + 4; j < Math.min(lines.length, i + 12); j++) {
                    const line = lines[j];
                    if (line.startsWith('Maxinsats')) {
                        const m = line.match(/[\d,.]+/);
                        if (m) maxStake = parseFloat(m[0].replace(',', '.'));
                    }
                    if (/^\d{2}:\d{2}$/.test(line) || line.includes('Ikväll') ||
                        line.includes('Imorgon') || /^\d{4}-\d{2}-\d{2}/.test(line)) {
                        timeInfo = line;
                    }
                    // Stop at next card boundary
                    if (line === 'Förut') break;
                }

                cards.push({
                    forut: forut,
                    nu: nu,
                    description: description,
                    team1: team1,
                    team2: team2,
                    leaguePath: leaguePath,
                    maxStake: maxStake,
                    timeInfo: timeInfo,
                });
            }
            return cards;
        }""")

        if verbose or len(raw_cards) == 0:
            # Debug: dump frame info and line count when 0 cards
            frame_info = f"frame={'iframe' if target_frame != page else 'main'}"
            line_count = await target_frame.evaluate("() => { function getAllLines(root) { const lines = []; if (!root) return lines; const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); while (walker.nextNode()) { const text = walker.currentNode.textContent.trim(); if (text) lines.push(text); } const elements = root.querySelectorAll('*'); for (const el of elements) { if (el.shadowRoot) { lines.push(...getAllLines(el.shadowRoot)); } } return lines; } return getAllLines(document).length; }")
            print(f"    [{provider_id}] {len(raw_cards)} boost cards found in DOM ({frame_info}, {line_count} text lines)")
            if len(raw_cards) == 0 and line_count > 0:
                # Dump first 30 lines to see what the page actually contains
                sample = await target_frame.evaluate("() => { function getAllLines(root) { const lines = []; if (!root) return lines; const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); while (walker.nextNode()) { const text = walker.currentNode.textContent.trim(); if (text) lines.push(text); } const elements = root.querySelectorAll('*'); for (const el of elements) { if (el.shadowRoot) { lines.push(...getAllLines(el.shadowRoot)); } } return lines; } return getAllLines(document).slice(0, 50); }")
                print(f"    [{provider_id}] First 50 lines: {sample}")

        # Convert raw cards to Special objects
        for card in raw_cards:
            original_odds = card.get('forut')
            boosted_odds = card.get('nu')
            if not original_odds or not boosted_odds:
                continue

            # Sanity check: real boosts are 3-80%
            boost_ratio = boosted_odds / original_odds
            if boost_ratio > 2.0 or boost_ratio < 1.01:
                continue

            description = card.get('description', '')
            team1 = card.get('team1', '')
            team2 = card.get('team2', '')
            league_path = card.get('leaguePath', '')
            max_stake = card.get('maxStake')

            # Build event name from team names
            event_name = f"{team1} vs {team2}" if team1 and team2 else ''

            # Extract league from path like "Fotboll / Premier League (EPL)"
            league = ''
            if league_path:
                parts = league_path.split(' / ')
                league = parts[-1].strip() if len(parts) > 1 else league_path.strip()

            # Title is the boost description
            title = description if description else event_name

            # Calculate boost percentage
            boost_pct_val = ((boosted_odds / original_odds) - 1) * 100

            # Sport detection
            sport = detect_sport(
                f"{title} {event_name} {league} {league_path}"
            )

            boosts.append(Special(
                provider=provider_id,
                title=title,
                event=event_name,
                original_odds=round(original_odds, 2),
                boosted_odds=round(boosted_odds, 2),
                boost_pct=round(boost_pct_val, 1),
                max_stake=max_stake,
                sport=sport,
                league=league,
                category="boost",
                expires_at=None,
                event_time=_parse_gecko_time(card.get('timeInfo', '')),
                source=provider_id,
                scraped_at=now_iso,
                url=boost_url,
                market_label="",
            ))

    except Exception as e:
        if verbose:
            print(f"    [{provider_id}] Error: {e}")
            import traceback
            traceback.print_exc()
    finally:
        await page.close()

    if verbose:
        with_orig = sum(1 for b in boosts if b.original_odds is not None)
        without = len(boosts) - with_orig
        print(f"    [{provider_id}] {len(boosts)} boosts parsed "
              f"({with_orig} with original odds, {without} without)")

    return boosts


async def _scrape_betconstruct_boosts(
    provider_id: str, now_iso: str, verbose: bool
) -> list[Special]:
    """
    Scrape BetConstruct (Vbet) odds boosts via Swarm WebSocket.

    Two-step approach:
    1. get_boosted_selections (params={}) → returns boosted selection IDs per match
       - details: {matchId: [{Id, Name, MatchId, SportId, BoostPrmOnly, BoostType}]}
    2. Fetch full game data for those matches via "get" command
    3. Cross-reference: only include selections that appear in boosted set

    Note: BetConstruct boost API only returns boosted odds (no original/pre-boost price).
    """
    import websockets
    from datetime import datetime as dt, timezone as tz

    boosts: list[Special] = []
    ws_url = "wss://eu-swarm-newm.vbet.se/"
    site_id = 1088
    rid = 1000

    # BetConstruct sport alias -> canonical name
    BC_SPORT_MAP = {
        "Soccer": "football", "Basketball": "basketball", "IceHockey": "ice_hockey",
        "Tennis": "tennis", "Baseball": "baseball", "AmericanFootball": "american_football",
        "Handball": "handball", "MMA": "mma", "Esports": "esports",
    }

    try:
        async with websockets.connect(
            ws_url,
            additional_headers={
                "Origin": "https://www.vbet.se",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            },
            max_size=20 * 1024 * 1024,
            close_timeout=10,
        ) as ws:
            # 1. Request session (language=swe required for Swedish site boosts)
            rid += 1
            await ws.send(json.dumps({
                "command": "request_session",
                "params": {
                    "source": 42,
                    "language": "swe",
                    "site_id": site_id,
                },
            }))
            resp = json.loads(await ws.recv())
            if resp.get("code") != 0:
                if verbose:
                    print(f"    [{provider_id}] Session request failed: {resp}")
                return boosts

            if verbose:
                print(f"    [{provider_id}] Swarm session established")

            # 2. Get boosted selection IDs
            rid += 1
            await ws.send(json.dumps({
                "command": "get_boosted_selections",
                "params": {},
                "rid": rid,
            }))
            boost_resp = json.loads(await ws.recv())

            if boost_resp.get("code") != 0:
                if verbose:
                    print(f"    [{provider_id}] get_boosted_selections failed: {boost_resp.get('msg', 'unknown')}")
                return boosts

            details = boost_resp.get("data", {}).get("details", {})
            if not details:
                if verbose:
                    print(f"    [{provider_id}] No boosted selections found")
                return boosts

            # Build lookup: selection_id -> boost info
            boosted_sel_ids: set[int] = set()
            match_ids: list[int] = []
            for match_id_str, sels in details.items():
                match_ids.append(int(match_id_str))
                for s in sels:
                    boosted_sel_ids.add(s["Id"])

            total_boosted_sels = len(boosted_sel_ids)
            if verbose:
                print(f"    [{provider_id}] {len(match_ids)} matches, {total_boosted_sels} boosted selections")

            # 3. Fetch 1x2/moneyline data for boosted matches
            rid += 1
            await ws.send(json.dumps({
                "command": "get",
                "params": {
                    "source": "betting",
                    "what": {
                        "sport": ["id", "name", "alias"],
                        "region": ["id", "name"],
                        "competition": ["id", "name"],
                        "game": [
                            "id", "team1_name", "team2_name", "start_ts",
                            "is_live", "type",
                        ],
                        "market": ["id", "type", "name", "base"],
                        "event": ["id", "name", "price", "type", "base"],
                    },
                    "where": {
                        "game": {"id": {"@in": match_ids}},
                        "market": {"type": {"@in": ["P1XP2", "P1P2"]}},
                    },
                    "subscribe": False,
                },
                "rid": rid,
            }))
            game_resp = json.loads(await ws.recv())

            if game_resp.get("code") != 0:
                if verbose:
                    print(f"    [{provider_id}] Game fetch failed: {game_resp.get('msg', 'unknown')}")
                return boosts

            # 4. Parse game data, only include boosted selections
            inner = game_resp.get("data", {}).get("data", game_resp.get("data", {}))
            sport_data = inner.get("sport", inner)
            if not isinstance(sport_data, dict):
                return boosts

            for sport_id, sport_obj in sport_data.items():
                sport_alias = sport_obj.get("alias", "")
                sport = BC_SPORT_MAP.get(sport_alias, "unknown")
                if sport == "unknown":
                    sport = detect_sport(sport_alias)

                regions = sport_obj.get("region", {})
                if not isinstance(regions, dict):
                    continue

                for reg_id, region in regions.items():
                    region_name = region.get("name", "")
                    competitions = region.get("competition", {})
                    if not isinstance(competitions, dict):
                        continue

                    for comp_id, comp in competitions.items():
                        comp_name = comp.get("name", "")
                        league = f"{region_name} - {comp_name}" if region_name else comp_name

                        games = comp.get("game", {})
                        if not isinstance(games, dict):
                            continue

                        for game_id, game in games.items():
                            # Skip live games — pre-match only
                            if game.get("is_live"):
                                continue

                            team1 = game.get("team1_name", "")
                            team2 = game.get("team2_name", "")
                            event_name = f"{team1} vs {team2}" if team1 and team2 else ""
                            start_ts = game.get("start_ts")
                            event_time = None
                            if start_ts:
                                try:
                                    event_time = dt.fromtimestamp(int(start_ts), tz=tz.utc).isoformat()
                                except (ValueError, TypeError, OSError):
                                    pass

                            game_markets = game.get("market", {})
                            if not isinstance(game_markets, dict):
                                continue

                            for mkt_id, market in game_markets.items():
                                mkt_name = market.get("name", "")
                                market_events = market.get("event", {})
                                if not isinstance(market_events, dict):
                                    continue

                                for ev_id, ev in market_events.items():
                                    # Only include boosted selections
                                    if int(ev_id) not in boosted_sel_ids:
                                        continue

                                    price = ev.get("price")
                                    if not price or float(price) <= 1.0:
                                        continue

                                    sel_name = ev.get("name", "").strip()
                                    title = f"{mkt_name}: {sel_name}" if mkt_name and sel_name else sel_name or mkt_name

                                    boosts.append(Special(
                                        provider=provider_id,
                                        title=title,
                                        event=event_name,
                                        original_odds=None,
                                        boosted_odds=float(price),
                                        boost_pct=None,
                                        max_stake=None,
                                        sport=sport if sport != "unknown" else detect_sport(
                                            f"{title} {event_name} {league}"
                                        ),
                                        league=league,
                                        category="boost",
                                        expires_at=None,
                                        event_time=event_time,
                                        source=provider_id,
                                        scraped_at=now_iso,
                                        url="https://www.vbet.se",
                                        market_label=mkt_name,
                                    ))

    except Exception as e:
        if verbose:
            print(f"    [{provider_id}] Error: {e}")
            import traceback
            traceback.print_exc()

    if verbose:
        print(f"    [{provider_id}] Total: {len(boosts)} boosts")
    return boosts


async def _scrape_interwetten_boosts(
    context, provider_id: str, boost_url: str, now_iso: str, verbose: bool
) -> list[Special]:
    """
    Scrape Interwetten odds boosts from the frontpage via DOM parsing.

    Interwetten shows boost promotions directly on the startpage as visible
    elements with "ODDS BOOST" labels and arrows showing original → boosted odds.

    Strategy:
    1. Navigate to interwetten.se
    2. Accept cookies
    3. Search DOM for boost elements (cards with original + boosted prices)
    4. Parse visible text to extract boost data
    """
    import asyncio

    page = await context.new_page()
    boosts: list[Special] = []

    try:
        await page.goto(boost_url, wait_until='load', timeout=30000)
        await asyncio.sleep(2)

        # Handle cookie consent
        for selector in [
            '#onetrust-accept-btn-handler',
            'button:has-text("Acceptera")', 'button:has-text("Accept")',
            'button:has-text("Godkänn")', '[class*="cookie"] button',
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                continue

        await asyncio.sleep(3)

        # Scroll to load more content
        for i in range(5):
            await page.evaluate(f"window.scrollTo(0, {(i + 1) * 600})")
            await asyncio.sleep(0.8)

        # Extract boost data from the page
        # Interwetten shows boosts in cards with "ODDS BOOST" and strikethrough original odds
        boost_data = await page.evaluate(r'''() => {
            const results = [];

            // Method 1: Look for elements with "odds boost" or "oddsboost" text
            const allElements = document.querySelectorAll('*');
            const boostSections = [];

            for (const el of allElements) {
                const text = (el.textContent || '').toLowerCase();
                const cls = (typeof el.className === 'string' ? el.className : '').toLowerCase();
                if ((text.includes('odds boost') || text.includes('oddsboost') ||
                     cls.includes('boost') || cls.includes('enhanced'))
                    && el.children.length > 0) {
                    // Check if this is a container (not a leaf with huge text)
                    if (el.textContent.length < 500 && el.textContent.length > 10) {
                        boostSections.push(el);
                    }
                }
            }

            // Method 2: Look for price pairs (original → boosted)
            // Interwetten uses strikethrough for original and highlighted for boosted
            const pricePattern = /(\d+[.,]\d+)\s*(?:→|->|⟶|►)?\s*(\d+[.,]\d+)/;
            const strikeThroughs = document.querySelectorAll('del, s, [style*="line-through"]');

            for (const del_el of strikeThroughs) {
                const origText = del_el.textContent.trim();
                const origMatch = origText.match(/(\d+[.,]\d+)/);
                if (!origMatch) continue;

                // Look at sibling/parent for the boosted price
                const parent = del_el.parentElement;
                if (!parent) continue;

                const parentText = parent.textContent;
                const prices = parentText.match(/(\d+[.,]\d+)/g);
                if (!prices || prices.length < 2) continue;

                const origOdds = parseFloat(origMatch[1].replace(',', '.'));
                // Find the higher price (boosted)
                let boostedOdds = null;
                for (const p of prices) {
                    const pf = parseFloat(p.replace(',', '.'));
                    if (pf > origOdds) {
                        boostedOdds = pf;
                        break;
                    }
                }

                if (boostedOdds) {
                    // Walk up to find event context
                    let container = parent;
                    let context = '';
                    for (let i = 0; i < 5 && container; i++) {
                        container = container.parentElement;
                        if (container) {
                            context = container.textContent.substring(0, 300);
                            if (context.length > 40) break;
                        }
                    }
                    results.push({
                        original: origOdds,
                        boosted: boostedOdds,
                        context: context.trim(),
                    });
                }
            }

            // Method 3: Broader search for boost cards with multiple prices
            const cards = document.querySelectorAll(
                '[class*="boost"], [class*="Boost"], [class*="enhanced"], [data-boost]'
            );
            for (const card of cards) {
                const text = card.textContent || '';
                const prices = text.match(/(\d+[.,]\d+)/g);
                if (prices && prices.length >= 2) {
                    const nums = prices.map(p => parseFloat(p.replace(',', '.')))
                        .filter(n => n >= 1.01 && n < 100);
                    if (nums.length >= 2) {
                        nums.sort((a, b) => a - b);
                        results.push({
                            original: nums[0],
                            boosted: nums[nums.length - 1],
                            context: text.substring(0, 300).trim(),
                        });
                    }
                }
            }

            return results;
        }''')

        if verbose:
            print(f"    [{provider_id}] Found {len(boost_data)} boost elements in DOM")

        seen_keys = set()
        for item in boost_data:
            orig = item.get("original")
            boosted = item.get("boosted")
            context_text = item.get("context", "")

            if not orig or not boosted or boosted <= orig:
                continue

            # Deduplicate
            key = (round(orig, 2), round(boosted, 2))
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Parse context for event/market info
            boost_pct_val = ((boosted / orig) - 1) * 100

            # Try to extract a meaningful title from context
            title = context_text[:100].strip() if context_text else f"Odds Boost {orig:.2f} → {boosted:.2f}"
            # Clean up multiline
            title = ' '.join(title.split())

            # Extract event name from title: "Team A - Team B Market: Selection"
            event_name = ""
            market_kws = [
                "halvtid", "resultat", "totalt", "vinnare", "poäng",
                "mål", "tips", "tip", "1x2", "över", "under",
                "antal", "båda", "first", "sista", "handicap",
            ]
            if " - " in title:
                # Find where the market description starts after the teams
                title_lower = title.lower()
                split_pos = len(title)
                for kw in market_kws:
                    idx = title_lower.find(kw)
                    if idx > 0 and idx < split_pos:
                        split_pos = idx
                event_name = title[:split_pos].strip().rstrip(" -–—")
                # Normalize "Team A - Team B" to "Team A vs Team B"
                if " - " in event_name:
                    parts = event_name.split(" - ", 1)
                    event_name = f"{parts[0].strip()} vs {parts[1].strip()}"

            sport = detect_sport(f"{title} {event_name} {context_text}")

            boosts.append(Special(
                provider=provider_id,
                title=title,
                event=event_name,
                original_odds=round(orig, 2),
                boosted_odds=round(boosted, 2),
                boost_pct=round(boost_pct_val, 1),
                max_stake=None,
                sport=sport,
                league="",
                category="boost",
                expires_at=None,
                event_time=None,
                source=provider_id,
                scraped_at=now_iso,
                url=boost_url,
                market_label="",
            ))

    except Exception as e:
        if verbose:
            print(f"    [{provider_id}] Error: {e}")
            import traceback
            traceback.print_exc()
    finally:
        await page.close()

    return boosts


async def _scrape_comeon_boosts(
    context, provider_id: str, boost_url: str, now_iso: str, verbose: bool
) -> list[Special]:
    """
    Scrape ComeOn Group odds boosts from sport/85-odds-boost page.

    ComeOn Group (ComeOn, Hajper) has a dedicated boost section at
    /sv/sportsbook/sport/85-odds-boost. The page is an RSocket SPA
    that delivers event/market/selection data via WebSocket.

    Boost structure:
    - Events are combo/parlay bets: "Sunderland & Fulham - båda vinner"
    - Market type 1038 = "Specialare" (special market)
    - Single "Ja" (Yes) selection per market at the boosted odds
    - No original/pre-boost odds available in WS data
    - League names include "Odds Boost" or "Odds Boost Plus" prefix

    NOTE: sport/37 = MMA, NOT boosts (previous config was wrong).
    Date buttons exist but boosts are typically today-only.
    """
    import asyncio

    page = await context.new_page()
    boosts: list[Special] = []
    ws_messages: list[dict] = []

    def _try_decode_rsocket(data: bytes) -> list | None:
        """Minimal RSocket frame decoder — extract JSON payloads."""
        results = []
        try:
            text = data.decode('utf-8', errors='ignore')
            depth = 0
            start = None
            for i, c in enumerate(text):
                if c in ('{', '['):
                    if depth == 0:
                        start = i
                    depth += 1
                elif c in ('}', ']'):
                    depth -= 1
                    if depth == 0 and start is not None:
                        fragment = text[start:i+1]
                        try:
                            parsed = json.loads(fragment)
                            results.append(parsed)
                        except json.JSONDecodeError:
                            pass
                        start = None
        except Exception:
            pass
        return results if results else None

    try:
        # Setup WS interception
        def on_websocket(ws):
            def on_frame_received(payload):
                if isinstance(payload, bytes):
                    decoded = _try_decode_rsocket(payload)
                    if decoded:
                        for item in decoded:
                            ws_messages.append(item)
                elif isinstance(payload, str):
                    try:
                        ws_messages.append(json.loads(payload))
                    except json.JSONDecodeError:
                        pass
            ws.on("framereceived", on_frame_received)
        page.on("websocket", on_websocket)

        await page.goto(boost_url, wait_until='load', timeout=30000)
        await asyncio.sleep(2)

        # Handle cookie consent
        for selector in [
            '#onetrust-accept-btn-handler',
            'button:has-text("Acceptera")', 'button:has-text("Accept")',
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                continue

        # Force-remove overlay
        try:
            await page.evaluate('''() => {
                const filter = document.querySelector('.onetrust-pc-dark-filter');
                if (filter) filter.remove();
                const sdk = document.querySelector('#onetrust-consent-sdk');
                if (sdk) sdk.remove();
            }''')
        except Exception:
            pass

        # Check if we got redirected, navigate back if needed
        current_url = page.url
        if '85' not in current_url and 'odds-boost' not in current_url:
            if verbose:
                print(f"    [{provider_id}] Redirected to {current_url}, navigating back")
            await page.goto(boost_url, wait_until='load', timeout=30000)

        # Wait for WS data
        await asyncio.sleep(5)

        # Click through date buttons to find future boosts
        # Scroll date container to reveal all dates first
        await page.evaluate(r'''() => {
            const allBtns = document.querySelectorAll('button');
            let dateBtn = null;
            for (const btn of allBtns) {
                const text = btn.textContent.trim().toLowerCase();
                if (/\d+\s+\w{3}\.?$/.test(text)) { dateBtn = btn; break; }
            }
            if (!dateBtn) return;
            let container = dateBtn.parentElement;
            for (let i = 0; i < 5 && container; i++) {
                const style = window.getComputedStyle(container);
                if (style.overflowX === 'auto' || style.overflowX === 'scroll' ||
                    container.scrollWidth > container.clientWidth) {
                    container.scrollLeft = container.scrollWidth;
                    return;
                }
                container = container.parentElement;
            }
        }''')
        await asyncio.sleep(0.3)

        # Discover date buttons and click through them
        date_labels = await page.evaluate(r'''() => {
            const labels = [];
            document.querySelectorAll('button').forEach(btn => {
                const text = btn.textContent.trim();
                const lower = text.toLowerCase();
                if (/\d+\s+\w{3}\.?$/.test(lower) && !lower.startsWith('idag')) {
                    labels.push(text);
                }
            });
            return labels;
        }''')

        if date_labels and verbose:
            print(f"    [{provider_id}] Scanning {len(date_labels)} future dates for boosts")

        for label in date_labels[:7]:  # Check ~1 week ahead
            try:
                clicked = await page.evaluate('''(targetLabel) => {
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {
                        if (btn.textContent.trim() === targetLabel) { btn.click(); return true; }
                    }
                    return false;
                }''', label)
                if clicked:
                    await asyncio.sleep(1.5)
            except Exception:
                pass

        if verbose:
            print(f"    [{provider_id}] Captured {len(ws_messages)} WS messages")

        # Parse WS data — extract events, markets, selections
        all_events = {}
        all_markets = {}
        all_selections = {}

        for msg in ws_messages:
            if isinstance(msg, list):
                for item in msg:
                    _extract_comeon_entities(item, all_events, all_markets, all_selections)
            elif isinstance(msg, dict):
                _extract_comeon_entities(msg, all_events, all_markets, all_selections)

        if verbose:
            print(f"    [{provider_id}] WS entities: {len(all_events)} events, "
                  f"{len(all_markets)} markets, {len(all_selections)} selections")

        # Build mappings
        event_markets: dict[int, list] = {}
        for mid, mkt in all_markets.items():
            eid = mkt.get('eventId')
            if eid:
                event_markets.setdefault(eid, []).append(mid)

        market_sels: dict[int, list] = {}
        for sid, sel in all_selections.items():
            mid = sel.get('marketId')
            if mid:
                market_sels.setdefault(mid, []).append(sel)

        # Parse events into boosts
        for eid, event_data in all_events.items():
            event_name_raw = event_data.get('eventName', '')
            league_raw = event_data.get('leagueName', '')
            start_time = event_data.get('startingOn') or event_data.get('startTime')

            # Detect "Odds Boost Plus" as superboost category
            is_super = 'plus' in league_raw.lower() or 'super' in league_raw.lower()

            # Use event name directly — boost events are combos like
            # "Sunderland & Fulham - båda vinner (Ord.speltid)"
            # These don't have standard home/away participants
            event_name = event_name_raw

            # Clean league: "Odds Boost - FA Cup" → "FA Cup"
            league = league_raw
            for prefix in ('Odds Boost Plus - ', 'Odds Boost - ', 'Odds Boost Plus', 'Odds Boost'):
                if league.startswith(prefix):
                    league = league[len(prefix):].strip()
                    break

            # Sport detection from event name + league
            sport = detect_sport(f"{event_name} {league}")

            # Get markets for this event
            mkt_ids = event_markets.get(eid, [])
            for mid in mkt_ids:
                mkt = all_markets.get(mid, {})
                mt = mkt.get('marketType', {})
                mkt_name = mt.get('name', '') if isinstance(mt, dict) else ''

                sels = market_sels.get(mid, [])
                for sel in sels:
                    odds = sel.get('trueOdds', 0)
                    if not odds or float(odds) <= 1.0:
                        continue

                    # Use event name as title for combo boosts
                    # (market name is just "Specialare", selection is just "Ja")
                    title = event_name_raw
                    if not title:
                        sel_name = sel.get('name', '')
                        title = f"{mkt_name}: {sel_name}" if mkt_name and sel_name else sel_name or mkt_name
                    if not title:
                        continue

                    boosts.append(Special(
                        provider=provider_id,
                        title=title,
                        event=event_name,
                        original_odds=None,  # ComeOn boost page only shows boosted odds
                        boosted_odds=float(odds),
                        boost_pct=None,
                        max_stake=None,
                        sport=sport,
                        league=league,
                        category="superboost" if is_super else "boost",
                        expires_at=None,
                        event_time=start_time,
                        source=provider_id,
                        scraped_at=now_iso,
                        url=boost_url,
                        market_label=mkt_name,
                    ))

    except Exception as e:
        if verbose:
            print(f"    [{provider_id}] Error: {e}")
            import traceback
            traceback.print_exc()
    finally:
        await page.close()

    return boosts


def _extract_comeon_entities(msg: dict, events: dict, markets: dict, selections: dict) -> None:
    """Extract events/markets/selections from a ComeOn WS message payload."""
    if not isinstance(msg, dict):
        return

    payload = msg.get('payload', msg)  # Payload might be top-level or nested

    for ev in payload.get('events', []):
        eid = ev.get('id')
        if eid:
            events[eid] = ev

    for mkt in payload.get('markets', []):
        mid = mkt.get('id')
        if mid:
            markets[mid] = mkt

    for sel in payload.get('selections', []):
        sid = sel.get('id')
        if sid:
            selections[sid] = sel

    # Also check nested 'data' or 'body' fields
    for key in ('data', 'body', 'result'):
        nested = msg.get(key)
        if isinstance(nested, dict):
            _extract_comeon_entities(nested, events, markets, selections)
        elif isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    _extract_comeon_entities(item, events, markets, selections)


async def _scrape_spectate_boosts(
    context, provider_id: str, boost_url: str, now_iso: str, verbose: bool
) -> list[Special]:
    """
    Scrape MrGreen/Spectate odds boosts from the boost page.

    MrGreen has a dedicated /sport/odds-boost/ page with section s-8337.
    The page loads boost data through the Spectate API with browser cookies.

    Strategy:
    1. Navigate to boost page (needs browser cookies for auth)
    2. Intercept API responses for event/odds data
    3. Also parse visible DOM as fallback
    """
    import asyncio

    page = await context.new_page()
    boosts: list[Special] = []
    api_responses: list[dict] = []

    try:
        # Intercept API responses
        async def capture_response(response):
            url = response.url.lower()
            if response.status != 200:
                return
            ct = response.headers.get('content-type', '')
            if 'json' not in ct:
                return
            try:
                if ('spectate' in url or 'event' in url or 'boost' in url
                    or 'offer' in url or 'odds' in url):
                    data = await response.json()
                    api_responses.append({'url': response.url, 'data': data})
            except Exception:
                pass

        page.on('response', capture_response)
        await page.goto(boost_url, wait_until='load', timeout=30000)
        await asyncio.sleep(3)

        # Handle cookie consent
        for selector in [
            '#onetrust-accept-btn-handler',
            'button:has-text("Acceptera")', 'button:has-text("Accept")',
            'button:has-text("Godkänn")',
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                continue

        await asyncio.sleep(3)

        # Scroll to load all content
        for i in range(6):
            await page.evaluate(f"window.scrollTo(0, {(i + 1) * 800})")
            await asyncio.sleep(1)

        await asyncio.sleep(2)

        if verbose:
            print(f"    [{provider_id}] Captured {len(api_responses)} API responses")

        # Parse API responses for boost data
        for resp_item in api_responses:
            data = resp_item.get('data', {})
            _parse_spectate_api_response(data, boosts, provider_id, now_iso, verbose)

        # Fallback: Parse visible DOM for boost information
        if not boosts:
            dom_boosts = await page.evaluate(r'''() => {
                const results = [];
                // Look for boost card elements
                const cards = document.querySelectorAll(
                    '[class*="boost"], [class*="Boost"], [data-test*="boost"], ' +
                    '[class*="enhanced"], [class*="special"], [class*="promo"]'
                );

                for (const card of cards) {
                    const text = card.textContent || '';
                    if (text.length < 10 || text.length > 500) continue;

                    const prices = text.match(/(\d+[.,]\d+)/g);
                    let original = null, boosted = null;

                    if (prices && prices.length >= 2) {
                        const nums = prices.map(p => parseFloat(p.replace(',', '.')))
                            .filter(n => n >= 1.01 && n < 100);
                        if (nums.length >= 2) {
                            nums.sort((a, b) => a - b);
                            original = nums[0];
                            boosted = nums[nums.length - 1];
                        }
                    } else if (prices && prices.length === 1) {
                        boosted = parseFloat(prices[0].replace(',', '.'));
                    }

                    if (boosted && boosted > 1.0) {
                        results.push({
                            text: text.substring(0, 200).trim(),
                            original: original,
                            boosted: boosted,
                        });
                    }
                }

                // Also look for generic event cards with "boost" nearby
                const headings = document.querySelectorAll('h1, h2, h3, h4, [class*="title"]');
                for (const h of headings) {
                    const hText = (h.textContent || '').toLowerCase();
                    if (!hText.includes('boost') && !hText.includes('förhöj')) continue;

                    // Search siblings for event data
                    const parent = h.parentElement;
                    if (!parent) continue;

                    const eventCards = parent.querySelectorAll('[class*="event"], [class*="match"]');
                    for (const ec of eventCards) {
                        const ecText = ec.textContent || '';
                        const prices = ecText.match(/(\d+[.,]\d+)/g);
                        if (prices) {
                            const nums = prices.map(p => parseFloat(p.replace(',', '.')))
                                .filter(n => n >= 1.01 && n < 100);
                            if (nums.length >= 1) {
                                results.push({
                                    text: ecText.substring(0, 200).trim(),
                                    original: nums.length >= 2 ? nums[0] : null,
                                    boosted: nums[nums.length - 1],
                                });
                            }
                        }
                    }
                }

                return results;
            }''')

            if verbose:
                print(f"    [{provider_id}] DOM fallback: {len(dom_boosts)} boost elements")

            seen_keys = set()
            for item in dom_boosts:
                text = item.get('text', '')
                orig = item.get('original')
                boosted = item.get('boosted')
                if not boosted:
                    continue

                key = (round(boosted, 2), text[:50])
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                sport = detect_sport(text)
                boost_pct_val = None
                if orig and boosted > orig:
                    boost_pct_val = round(((boosted / orig) - 1) * 100, 1)

                title = ' '.join(text.split())[:120]

                boosts.append(Special(
                    provider=provider_id,
                    title=title,
                    event="",
                    original_odds=round(orig, 2) if orig else None,
                    boosted_odds=round(boosted, 2),
                    boost_pct=boost_pct_val,
                    max_stake=None,
                    sport=sport,
                    league="",
                    category="boost",
                    expires_at=None,
                    event_time=None,
                    source=provider_id,
                    scraped_at=now_iso,
                    url=boost_url,
                    market_label="",
                ))

    except Exception as e:
        if verbose:
            print(f"    [{provider_id}] Error: {e}")
            import traceback
            traceback.print_exc()
    finally:
        await page.close()

    return boosts


def _parse_spectate_api_response(
    data,
    boosts: list,
    provider_id: str,
    now_iso: str,
    verbose: bool,
) -> None:
    """Parse Spectate API response for boost/enhanced odds data."""
    # Handle list at top level
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _parse_spectate_api_response(item, boosts, provider_id, now_iso, verbose)
        return

    if not isinstance(data, dict):
        return

    # Spectate responses can contain events, markets, selections in various formats
    events = data.get('events', [])
    if isinstance(events, list):
        for ev in events:
            _parse_spectate_event(ev, boosts, provider_id, now_iso)
    elif isinstance(events, dict):
        for eid, ev in events.items():
            _parse_spectate_event(ev, boosts, provider_id, now_iso)

    # Check nested data structures
    for key in ('data', 'body', 'sections', 'offers', 'results'):
        nested = data.get(key)
        if isinstance(nested, dict):
            _parse_spectate_api_response(nested, boosts, provider_id, now_iso, verbose)
        elif isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    _parse_spectate_api_response(item, boosts, provider_id, now_iso, verbose)


def _parse_spectate_event(
    ev: dict, boosts: list, provider_id: str, now_iso: str
) -> None:
    """Parse a single Spectate event for boost data."""
    if not isinstance(ev, dict):
        return

    event_name = ev.get('name', '')
    league = ev.get('competition', ev.get('league', ''))
    start_time = ev.get('startTime', ev.get('startDate'))
    sport = detect_sport(f"{event_name} {league}")

    markets = ev.get('markets', ev.get('offers', []))
    if isinstance(markets, dict):
        markets = list(markets.values())

    for mkt in markets:
        if not isinstance(mkt, dict):
            continue
        mkt_name = mkt.get('name', mkt.get('label', ''))

        outcomes = mkt.get('outcomes', mkt.get('selections', []))
        if isinstance(outcomes, dict):
            outcomes = list(outcomes.values())

        for out in outcomes:
            if not isinstance(out, dict):
                continue

            odds = out.get('odds', out.get('price'))
            if not odds or float(odds) <= 1.0:
                continue

            out_name = out.get('name', out.get('label', ''))
            title = f"{mkt_name}: {out_name}" if mkt_name and out_name else out_name or mkt_name
            if not title:
                continue

            boosts.append(Special(
                provider=provider_id,
                title=title,
                event=event_name,
                original_odds=None,
                boosted_odds=float(odds),
                boost_pct=None,
                max_stake=None,
                sport=sport,
                league=league if isinstance(league, str) else "",
                category="boost",
                expires_at=None,
                event_time=start_time,
                source=provider_id,
                scraped_at=now_iso,
                url="https://www.mrgreen.se/sport/odds-boost/",
                market_label=mkt_name,
            ))


# ============ Aggregation ============

def deduplicate_specials(specials: list[Special]) -> list[Special]:
    """Remove duplicate specials based on provider + event + title + boosted odds."""
    seen_keys = set()
    unique = []
    for s in specials:
        key = (s.provider, s.event.lower(), s.title.lower(), s.boosted_odds)
        if key not in seen_keys:
            seen_keys.add(key)
            unique.append(s)
    return unique


def scrape_all(verbose: bool = False) -> tuple[list[Special], BoostRunLog]:
    """Run all scrapers and return (specials, run_log) tuple."""
    import asyncio
    import uuid

    run_start = time.monotonic()
    all_specials: list[Special] = []
    provider_logs: list[BoostProviderLog] = []

    if verbose:
        print("Scraping provider boost pages...")

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(
                    lambda: asyncio.run(scrape_provider_boosts(verbose=verbose))
                ).result(timeout=180)
        else:
            result = asyncio.run(scrape_provider_boosts(verbose=verbose))

        provider_boosts, provider_logs = result
    except RuntimeError:
        provider_boosts, provider_logs = asyncio.run(scrape_provider_boosts(verbose=verbose))
    except Exception as e:
        if verbose:
            print(f"  Provider scraping failed: {e}")
        provider_boosts = []

    all_specials.extend(provider_boosts)

    # Deduplicate
    unique = deduplicate_specials(all_specials)
    if verbose:
        print(f"\nTotal: {len(all_specials)} raw, {len(unique)} unique boosts")

    run_log = BoostRunLog(
        run_id=str(uuid.uuid4())[:8],
        scraped_at=datetime.now().isoformat(),
        total_boosts=len(unique),
        duration_seconds=round(time.monotonic() - run_start, 2),
        providers=provider_logs,
    )

    return unique, run_log


def save_specials(specials: list[Special], path: Optional[Path] = None) -> Path:
    """Save specials to JSON file."""
    if path is None:
        path = DATA_DIR / "specials.json"

    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "specials": [asdict(s) for s in specials],
        "count": len(specials),
        "scraped_at": datetime.now(tz=None).isoformat(),
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return path


def load_specials(path: Optional[Path] = None) -> list[dict]:
    """Load specials from JSON file."""
    if path is None:
        path = DATA_DIR / "specials.json"

    if not path.exists():
        return []

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("specials", [])
    except Exception:
        return []


# ============ CLI ============

def _print(text: str):
    """Print with fallback for Windows console encoding issues."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def main():
    parser = argparse.ArgumentParser(description="Scrape odds boosts from provider sites")
    parser.add_argument("--save", action="store_true", help="Save results to data/specials.json")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    specials, run_log = scrape_all(verbose=args.verbose)

    if args.verbose and run_log.providers:
        print(f"\n  Run: {run_log.duration_seconds:.1f}s total, {run_log.total_boosts} boosts")
        for pl in run_log.providers:
            status_str = f"{pl.boosts_found} boosts" if pl.status == "success" else f"FAILED: {pl.error_message}"
            print(f"    {pl.provider_id}: {status_str} ({pl.duration_seconds:.1f}s)")

    if not specials:
        print("No boosts found.")
        if args.save:
            path = save_specials(specials)
            print(f"Empty results saved to {path}")
        return

    print(f"\n{'='*60}")
    print(f"  ODDS BOOSTS ({len(specials)} found)")
    print(f"{'='*60}\n")

    by_provider: dict[str, list[Special]] = {}
    for s in specials:
        by_provider.setdefault(s.provider, []).append(s)

    for provider, items in sorted(by_provider.items()):
        _print(f"  {provider.upper()} ({len(items)} boosts)")
        for item in items:
            odds_str = ""
            if item.original_odds and item.boosted_odds:
                boost_pct = (item.boosted_odds / item.original_odds - 1) * 100
                odds_str = f"  {item.original_odds:.2f} -> {item.boosted_odds:.2f} (+{boost_pct:.0f}%)"
            elif item.boosted_odds:
                odds_str = f"  -> {item.boosted_odds:.2f}"

            stake_str = f"  max {item.max_stake:.0f} kr" if item.max_stake else ""
            sport_str = f"  [{item.sport}]" if item.sport != "unknown" else ""
            league_str = f"  {item.league}" if item.league else ""
            cat_str = " [SUPER]" if item.category == "superboost" else ""

            _print(f"    {item.event or '?'}{cat_str}")
            _print(f"      {item.title}")
            _print(f"     {odds_str}{stake_str}{sport_str}{league_str}")
        print()

    if args.save:
        path = save_specials(specials)
        print(f"Saved to {path}")


if __name__ == "__main__":
    main()
