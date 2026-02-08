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
import json
import re
from dataclasses import dataclass, asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Optional

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
    "bethard": "bethard",
}

SPORT_KEYWORDS: dict[str, list[str]] = {
    "football": [
        "fotboll", "football", "soccer", "premier league", "champions league",
        "allsvenskan", "la liga", "serie a", "bundesliga", "ligue 1",
        "europa league", "vm kval", "nations league", "conference league",
        "fa cup", "carabao", "copa del rey", "superettan", "eredivisie",
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

# Output path
DATA_DIR = Path(__file__).parent.parent / "data"


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


def detect_sport(text: str) -> str:
    """Detect sport from text using keywords."""
    text_lower = text.lower()
    for sport, keywords in SPORT_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return sport
    return "unknown"


# ============ Provider Boost Pages ============

# Config path for boost definitions
CONFIG_DIR = Path(__file__).parent.parent / "src" / "config"


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
            if entry.get("enabled") and entry.get("type") and entry.get("url"):
                entries.append({
                    "name": name,
                    "type": entry["type"],
                    "url": entry["url"],
                    "primary_provider": entry.get("primary_provider", name),
                    "shared_with": entry.get("shared_with", []),
                })
        return entries
    except Exception:
        return []


async def scrape_provider_boosts(verbose: bool = False) -> list[Special]:
    """Scrape odds boosts from all configured providers in providers.yaml."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        if verbose:
            print("  [provider_boosts] playwright not installed, skipping")
        return []

    boost_configs = _load_boost_config()
    if not boost_configs:
        if verbose:
            print("  No enabled boost configs found in providers.yaml")
        return []

    all_boosts: list[Special] = []
    now_iso = datetime.now().isoformat()

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

            for cfg in boost_configs:
                provider_id = cfg["primary_provider"]
                boost_url = cfg["url"]
                boost_type = cfg["type"]
                shared = cfg["shared_with"]

                if verbose:
                    print(f"  [{cfg['name']}] {provider_id}: {boost_url} (type={boost_type})")

                try:
                    if boost_type == "gecko_v2":
                        boosts = await _scrape_gecko_boosts(
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
                    if verbose:
                        print(f"  {provider_id}: {len(boosts)} boosts found")
                        if shared:
                            print(f"    (also available on: {', '.join(shared)})")
                except Exception as e:
                    if verbose:
                        print(f"  {provider_id} failed: {e}")
                        import traceback
                        traceback.print_exc()

            await browser.close()
    except Exception as e:
        if verbose:
            print(f"  Browser launch failed: {e}")

    return all_boosts


async def _scrape_gecko_boosts(
    context, provider_id: str, boost_url: str, now_iso: str, verbose: bool
) -> list[Special]:
    """
    Scrape Gecko V2 (Betsson group) odds boosts via API interception.

    The boost page makes two key API calls:
    1. globalbonuses — returns PriceBoost bonus objects with:
       - bonusData.boostedOdds = the boosted (enhanced) odds
       - bonusData.type = "Multiplier" or "FixedOdds"
       - bonusData.isSuperBoost = true/false
       - criteria.criteriaEntityDetails[].marketSelectionId = selection to match
       - conditions.maximumStake = max bet amount

    2. event-market — returns events, markets, and selections with:
       - selection.odds = the ORIGINAL (pre-boost) odds
       - selection.label = bet description
       - event.participants[].label = team names
       - event.competitionName = league name
       - event.deadline = event start time

    The page only loads event-market data for visible cards. We need to
    scroll to load all cards, then fetch any remaining missing selections
    by requesting their market IDs directly.
    """
    import asyncio

    page = await context.new_page()
    boosts: list[Special] = []

    bonus_data = None
    all_events: dict[str, dict] = {}
    all_markets: dict[str, dict] = {}
    all_selections: dict[str, dict] = {}

    try:
        async def capture_response(response):
            nonlocal bonus_data
            if response.status != 200:
                return
            ct = response.headers.get('content-type', '')
            if 'json' not in ct:
                return
            url = response.url
            try:
                data = await response.json()
            except Exception:
                return

            if 'globalbonuses' in url:
                bonus_data = data
            elif 'event-market' in url:
                _collect_event_market(data, all_events, all_markets, all_selections)

        page.on('response', capture_response)
        await page.goto(boost_url, wait_until='load', timeout=30000)

        # Handle cookie consent
        for selector in [
            '#onetrust-accept-btn-handler',
            'button:has-text("Acceptera")', 'button:has-text("Accept")',
            'button:has-text("Godkänn")', '[data-testid="cookie-accept"]',
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                continue

        # Wait for initial API load
        await asyncio.sleep(5)

        # Scroll incrementally to trigger lazy loading of all boost cards
        for i in range(8):
            await page.evaluate(f"window.scrollTo(0, {(i + 1) * 1000})")
            await asyncio.sleep(1)

        # Final scroll to bottom
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(3)

        page.remove_listener('response', capture_response)

        if not bonus_data:
            if verbose:
                print(f"    [{provider_id}] No globalbonuses response captured")
            await page.close()
            return boosts

        bonuses = bonus_data.get('data', {}).get('bonuses', [])
        price_boosts = [b for b in bonuses if b.get('type') == 'PriceBoost']

        if verbose:
            print(f"    [{provider_id}] {len(price_boosts)} PriceBoost bonuses, "
                  f"{len(all_selections)} selections loaded")

        # Parse boosts with full data
        boosts = _parse_gecko_boosts(
            price_boosts, all_events, all_markets, all_selections,
            provider_id, boost_url, now_iso, verbose
        )

    except Exception as e:
        if verbose:
            print(f"    [{provider_id}] Error: {e}")
            import traceback
            traceback.print_exc()
    finally:
        await page.close()

    return boosts


def _collect_event_market(
    data: dict,
    events: dict[str, dict],
    markets: dict[str, dict],
    selections: dict[str, dict],
) -> None:
    """Collect events, markets, and selections from an event-market API response."""
    d = data.get('data', {})

    evts = d.get('events', [])
    if isinstance(evts, list):
        for e in evts:
            if isinstance(e, dict) and 'id' in e:
                events[e['id']] = e
    elif isinstance(evts, dict):
        events.update(evts)

    mkts = d.get('markets', [])
    if isinstance(mkts, list):
        for m in mkts:
            if isinstance(m, dict) and 'id' in m:
                markets[m['id']] = m
    elif isinstance(mkts, dict):
        markets.update(mkts)

    sels = d.get('marketSelections', [])
    if isinstance(sels, list):
        for s in sels:
            if isinstance(s, dict) and 'id' in s:
                selections[s['id']] = s
    elif isinstance(sels, dict):
        selections.update(sels)


def _parse_gecko_boosts(
    price_boosts: list[dict],
    events: dict[str, dict],
    markets: dict[str, dict],
    selections: dict[str, dict],
    provider_id: str,
    boost_url: str,
    now_iso: str,
    verbose: bool,
) -> list[Special]:
    """
    Parse Gecko V2 PriceBoost bonuses into Special objects.

    Key mapping:
      - bonusData.boostedOdds = the BOOSTED (enhanced) odds
      - selection.odds = the ORIGINAL (pre-boost) odds
      - bonusData.isSuperBoost = true for super boosts
      - bonusData.type = "Multiplier" (most) or "FixedOdds"

    Boosts whose selections weren't loaded by the page (combo/prop markets
    like MWBTTS, AGSNAB, etc.) have no original odds and no bet description.
    These are skipped since they're not useful without edge calculation.
    """
    boosts = []

    for bonus in price_boosts:
        bonus_d = bonus.get('bonusData', {})
        boosted_odds = bonus_d.get('boostedOdds')
        is_super = bonus_d.get('isSuperBoost', False)

        if not boosted_odds:
            continue

        max_stake = bonus.get('conditions', {}).get('maximumStake')
        expiry = bonus.get('expiryDate')
        bonus_name = bonus.get('name', '')

        details = bonus.get('criteria', {}).get('criteriaEntityDetails', [])
        if not details:
            continue

        # Collect enriched selection labels for multi-leg boosts
        # Combine market.label + selection.label for descriptive titles
        sel_labels = []
        market_labels = []
        original_odds = None
        event_id = None
        for detail in details:
            sel_id = detail.get('marketSelectionId', '')
            if not event_id:
                event_id = detail.get('eventId', '')

            sel = selections.get(sel_id, {})
            sel_label = sel.get('label', '')
            if sel_label:
                # Look up parent market for context
                market_id = sel.get('marketId', sel.get('market_id', ''))
                market = markets.get(market_id, {})
                market_label = market.get('label', '')

                # Combine: "Båda lagen gör mål: Ja" instead of just "Ja"
                # Skip generic labels like "Pre-built" that add no context
                generic_labels = {'pre-built', 'custom', 'special'}
                if (market_label
                    and market_label.lower() not in generic_labels
                    and market_label.lower() != sel_label.lower()):
                    sel_labels.append(f"{market_label}: {sel_label}")
                else:
                    sel_labels.append(sel_label)
                if market_label:
                    market_labels.append(market_label)
            # Use odds from first selection that has them
            if original_odds is None and sel.get('odds'):
                original_odds = sel.get('odds')

        # Skip boosts without original odds — these are combo/prop markets
        # where selections weren't loaded, so we can't calculate edge
        if original_odds is None:
            continue

        # Skip if original >= boosted (data anomaly)
        if float(original_odds) >= float(boosted_odds):
            continue

        # Build title from selection labels or fall back to bonus name
        if sel_labels:
            title = ','.join(sel_labels)
        elif bonus_name:
            title = bonus_name
        else:
            continue

        # Get event info
        event = events.get(event_id, {}) if event_id else {}
        participants = event.get('participants', [])
        part_names = [p.get('label', '') for p in participants if p.get('label')]
        event_name = ' vs '.join(part_names) if part_names else ''
        category_name = event.get('categoryName', '')
        competition_name = event.get('competitionName', '')
        # startDate = actual match kickoff, deadline = often same as bonus expiry
        event_start = event.get('startDate') or event.get('deadline')

        # Fallback: extract event name from CCRM-style bonus name
        # e.g. "CCRM PB Man Utd v Tottenham" -> "Man Utd vs Tottenham"
        if not event_name and bonus_name:
            m = re.match(r'^(?:CCRM\s+)?PB\s+(.+?)\s+v\s+(.+)$', bonus_name, re.IGNORECASE)
            if m:
                event_name = f"{m.group(1).strip()} vs {m.group(2).strip()}"
            elif not bonus_name.startswith('CCRM'):
                event_name = bonus_name

        # Clean CCRM prefix from title if it leaked through
        if title.startswith('CCRM '):
            m = re.match(r'^(?:CCRM\s+)?PB\s+(.+?)\s+v\s+(.+)$', title, re.IGNORECASE)
            if m:
                title = f"{m.group(1).strip()} vs {m.group(2).strip()}"

        # Sport detection
        sport = detect_sport(
            f"{title} {event_name} {category_name} {competition_name}"
        )

        # Use startDate as event_time (actual kickoff), skip if same as expiry
        real_event_time = event_start if (event_start and event_start != expiry) else None

        # Calculate boost percentage
        orig_f = float(original_odds) if original_odds else None
        boosted_f = float(boosted_odds)
        boost_pct_val = ((boosted_f / orig_f) - 1) * 100 if orig_f else None

        boosts.append(Special(
            provider=provider_id,
            title=title,
            event=event_name,
            original_odds=orig_f,
            boosted_odds=boosted_f,
            boost_pct=round(boost_pct_val, 1) if boost_pct_val is not None else None,
            max_stake=float(max_stake) if max_stake else None,
            sport=sport,
            league=competition_name,
            category="superboost" if is_super else "boost",
            expires_at=expiry,
            event_time=real_event_time,
            source=f"{provider_id}",
            scraped_at=now_iso,
            url=boost_url,
            market_label=', '.join(market_labels) if market_labels else "",
        ))

    if verbose:
        with_orig = sum(1 for b in boosts if b.original_odds is not None)
        without = len(boosts) - with_orig
        print(f"    [{provider_id}] {len(boosts)} boosts parsed "
              f"({with_orig} with original odds, {without} without)")

    return boosts


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


def scrape_all(verbose: bool = False) -> list[Special]:
    """Run all scrapers and return aggregated results."""
    import asyncio

    all_specials: list[Special] = []

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
                provider_boosts = pool.submit(
                    lambda: asyncio.run(scrape_provider_boosts(verbose=verbose))
                ).result(timeout=180)
        else:
            provider_boosts = asyncio.run(scrape_provider_boosts(verbose=verbose))
    except RuntimeError:
        provider_boosts = asyncio.run(scrape_provider_boosts(verbose=verbose))
    except Exception as e:
        if verbose:
            print(f"  Provider scraping failed: {e}")
        provider_boosts = []

    all_specials.extend(provider_boosts)

    # Deduplicate
    unique = deduplicate_specials(all_specials)
    if verbose:
        print(f"\nTotal: {len(all_specials)} raw, {len(unique)} unique boosts")

    return unique


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

    specials = scrape_all(verbose=args.verbose)

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
