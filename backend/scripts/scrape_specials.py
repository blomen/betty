#!/usr/bin/env python3
"""
Scrape odds boosts and specials from Swedish sportsbook affiliate sites.

Sources:
  - bettingstugan.se/alla-oddsbooster-idag (daily boost aggregator)
  - bettingstugan.se/forhojda-odds (enhanced odds overview)

Output: backend/data/specials.json

Usage:
  python scripts/scrape_specials.py           # Scrape and print results
  python scripts/scrape_specials.py --save    # Save to data/specials.json
  python scripts/scrape_specials.py -v        # Verbose output
"""

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# Provider name normalization map
PROVIDER_ALIASES: dict[str, str] = {
    # Kambi API
    "unibet": "unibet",
    "leovegas": "leovegas",
    "leo vegas": "leovegas",
    "expekt": "expekt",
    "betmgm": "betmgm",
    "speedybet": "speedybet",
    "speedy bet": "speedybet",
    "x3000": "x3000",
    "goldenbull": "goldenbull",
    "golden bull": "goldenbull",
    "1x2": "1x2",
    # Altenar API
    "betinia": "betinia",
    "campobet": "campobet",
    "swiper": "swiper",
    "lodur": "lodur",
    "dbet": "dbet",
    "d-bet": "dbet",
    "quickcasino": "quickcasino",
    "quick casino": "quickcasino",
    # Spectate (888/Evoke)
    "888sport": "888sport",
    "888 sport": "888sport",
    "mr green": "mrgreen",
    "mrgreen": "mrgreen",
    # Gecko V2 (Betsson Group)
    "betsson": "betsson",
    "betsafe": "betsafe",
    "nordicbet": "nordicbet",
    "spelklubben": "spelklubben",
    # SBTech
    "bethard": "bethard",
    "10bet": "10bet",
    "10 bet": "10bet",
    # Snabbare
    "snabbare": "snabbare",
    # ComeOn Group
    "comeon": "comeon",
    "come on": "comeon",
    "hajper": "hajper",
    # BetConstruct
    "vbet": "vbet",
    "v bet": "vbet",
    # Interwetten
    "interwetten": "interwetten",
    "inter wetten": "interwetten",
}

SPORT_KEYWORDS: dict[str, list[str]] = {
    "football": ["fotboll", "football", "soccer", "premier league", "champions league",
                 "allsvenskan", "la liga", "serie a", "bundesliga", "ligue 1",
                 "europa league", "vm", "em", "nations league"],
    "ice_hockey": ["hockey", "ishockey", "shl", "nhl", "hockeyallsvenskan", "world championship"],
    "tennis": ["tennis", "atp", "wta", "grand slam", "wimbledon", "us open", "french open"],
    "basketball": ["basket", "basketball", "nba", "euroleague"],
    "handball": ["handboll", "handball"],
    "mma": ["mma", "ufc", "bellator"],
    "esports": ["esport", "cs2", "counter-strike", "league of legends", "dota"],
}

# Output path
DATA_DIR = Path(__file__).parent.parent / "data"


@dataclass
class Special:
    """A single odds boost or special offer."""
    provider: str
    title: str
    description: str = ""
    original_odds: Optional[float] = None
    boosted_odds: Optional[float] = None
    max_stake: Optional[float] = None
    category: str = "boost"  # boost, superboost, combo_boost, zero_margin
    sport: str = "unknown"
    event: str = ""
    expires_at: Optional[str] = None
    url: str = ""
    scraped_at: str = ""
    source: str = ""


def normalize_provider(name: str) -> str:
    """Normalize provider name to canonical ID."""
    name_lower = name.strip().lower()
    return PROVIDER_ALIASES.get(name_lower, name_lower)


def detect_sport(text: str) -> str:
    """Detect sport from text using keywords."""
    text_lower = text.lower()
    for sport, keywords in SPORT_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return sport
    return "unknown"


def parse_odds(text: str) -> Optional[float]:
    """Parse odds value from text like '3.50' or '3,50'."""
    text = text.replace(",", ".").strip()
    match = re.search(r'(\d+\.\d{1,2})', text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def parse_max_stake(text: str) -> Optional[float]:
    """Parse max stake from text like 'max 250 kr' or 'Max. insats: 500kr'."""
    text_lower = text.lower()
    match = re.search(r'max[.\s:]*(?:insats[.\s:]*)?(\d[\d\s]*)\s*kr', text_lower)
    if match:
        try:
            return float(match.group(1).replace(" ", ""))
        except ValueError:
            pass
    return None


# ============ Source Scrapers ============

def scrape_bettingstugan_today(session: requests.Session, verbose: bool = False) -> list[Special]:
    """
    Scrape bettingstugan.se/alla-oddsbooster-idag for today's boosts.

    Page structure (from inspection Feb 2026):
    The boosts appear as free-form text blocks under the heading
    "Alla dagens oddsboostar". Each boost follows this pattern:

        [Event name]              (e.g. "Arsenal - Sunderland")
        [Date time]               (e.g. "7 feb. 16:00")
        [League] [Provider]       (e.g. "Premier League Bethard")
        * [condition1]            (e.g. "Arsenal leder efter 20 min")
        * [condition2...]
        Maxbet: [amount]          (optional, e.g. "Maxbet: 250")
        [orig] >> [boosted]       (e.g. "3.00 >> 3.35")

    The page also contains a welcome bonus table (Rank/Spelbolag/Bonus)
    which we skip entirely.
    """
    url = "https://bettingstugan.se/alla-oddsbooster-idag/"
    specials = []
    now_iso = datetime.now(tz=None).isoformat()

    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        content = soup.select_one(".content") or soup.select_one(".entry-content")
        if not content:
            if verbose:
                print("  bettingstugan/today: no content container found")
            return specials

        # Get the full text content and split into lines for parsing
        full_text = content.get_text(separator="\n")
        lines = [line.strip() for line in full_text.split("\n") if line.strip()]

        if verbose:
            print(f"  bettingstugan/today: {len(lines)} text lines in content")

        # Find the boost section start
        boost_section_start = None
        for i, line in enumerate(lines):
            if "alla dagens oddsboostar" in line.lower():
                boost_section_start = i + 1
                break

        if boost_section_start is None:
            if verbose:
                print("  bettingstugan/today: could not find 'Alla dagens oddsboostar' heading")
            return specials

        # Find the end of the boost section (next heading or table)
        boost_section_end = len(lines)
        for i in range(boost_section_start, len(lines)):
            line_lower = lines[i].lower()
            if ("alla spelbolag med" in line_lower
                    or "rank" == line_lower
                    or "vad ar en oddsboost" in line_lower.replace("ä", "a")
                    or "las mer om" in line_lower.replace("ä", "a")):
                boost_section_end = i
                break

        boost_lines = lines[boost_section_start:boost_section_end]
        if verbose:
            print(f"  bettingstugan/today: boost section lines {boost_section_start}-{boost_section_end} ({len(boost_lines)} lines)")

        # Parse boost entries from the section
        # The structure is line-by-line with the arrow char on its own line:
        #   [Event name]
        #   [Day] [Month]      (e.g. "7 feb.")
        #   [HH:MM]            (e.g. "16:00")
        #   [League]           (e.g. "Premier League")
        #   [Provider]         (e.g. "Bethard")
        #   [condition1]       (e.g. "Arsenal leder efter 20 min")
        #   ...
        #   Maxbet: NNN        (optional)
        #   [original_odds]    (e.g. "3.00")
        #   [arrow]            (e.g. ">>")
        #   [boosted_odds]     (e.g. "3.35")

        arrow_chars = {'\u00bb', '\u203a', '>', '\u2192'}  # >> and arrow variants
        known_providers = set(PROVIDER_ALIASES.values())
        date_pattern = re.compile(r'^\d{1,2}\s+(?:jan|feb|mar|apr|maj|jun|jul|aug|sep|okt|nov|dec)', re.IGNORECASE)
        time_pattern = re.compile(r'^\d{2}:\d{2}$')
        odds_number = re.compile(r'^\d+[.,]\d{2}$')

        i = 0
        while i < len(boost_lines):
            line = boost_lines[i]

            # Detect arrow lines (the separator between original and boosted odds)
            is_arrow = all(c in arrow_chars or c.isspace() for c in line) and len(line.strip()) > 0

            if is_arrow and i >= 1 and i + 1 < len(boost_lines):
                # Line before arrow = original odds, line after = boosted odds
                orig_line = boost_lines[i - 1].replace(",", ".").strip()
                boost_line = boost_lines[i + 1].replace(",", ".").strip()

                orig_match = odds_number.match(orig_line)
                boost_match = odds_number.match(boost_line)

                if orig_match and boost_match:
                    original = float(orig_line)
                    boosted = float(boost_line)

                    # Now scan backwards from the original odds line to get context
                    event_name = ""
                    date_str = ""
                    time_str = ""
                    provider = ""
                    league = ""
                    conditions = []
                    max_stake = None

                    # Scan backwards from i-2 (line before original odds)
                    for j in range(i - 2, max(0, i - 12) - 1, -1):
                        prev = boost_lines[j]
                        prev_lower = prev.lower().strip()

                        # Check for "Maxbet: NNN" or "Maxbet: 25 SEK INSATS..."
                        maxbet_match = re.search(r'maxbet[:\s]*(\d+)', prev_lower)
                        if maxbet_match:
                            max_stake = float(maxbet_match.group(1))
                            continue

                        # Time line (HH:MM)
                        if time_pattern.match(prev.strip()) and not time_str:
                            time_str = prev.strip()
                            continue

                        # Date line (e.g. "7 feb.")
                        if date_pattern.match(prev.strip()) and not date_str:
                            date_str = prev.strip()
                            continue

                        # Provider line (single known provider name)
                        normalized = normalize_provider(prev.strip())
                        if normalized in known_providers and not provider:
                            provider = normalized
                            continue

                        # League line: if next line was the provider, this is the league
                        # Also skip lines that look like arrow/odds
                        if odds_number.match(prev.strip().replace(",", ".")):
                            # This is an odds from a previous boost entry, stop scanning
                            break

                        all_arrow = all(c in arrow_chars or c.isspace() for c in prev) and len(prev.strip()) > 0
                        if all_arrow:
                            break

                        # Classify remaining lines as event name, league, or condition
                        if not league and not date_pattern.match(prev.strip()) and not time_pattern.match(prev.strip()):
                            cleaned = re.sub(r'^NYKUND[:\s]*', '', prev, flags=re.IGNORECASE).strip()

                            if " - " in cleaned and not event_name:
                                # Contains " - " = event name (e.g. "Arsenal - Sunderland")
                                event_name = cleaned
                            elif not league and len(cleaned.split()) <= 4 and cleaned[0].isupper() and not any(
                                kw in cleaned.lower() for kw in ['vinner', 'mål', 'leder', 'över', 'under', 'gör', 'kvalificerar', 'båda', 'guld']
                            ):
                                # Short capitalized phrase without condition keywords = likely league
                                league = cleaned
                            else:
                                # Everything else is a condition
                                conditions.insert(0, cleaned)

                    # Build the special entry
                    if not event_name and conditions:
                        event_name = conditions.pop(0)
                    if not event_name:
                        event_name = f"Boost ({original:.2f} -> {boosted:.2f})"

                    title = "; ".join(conditions) if conditions else event_name
                    description = event_name
                    if conditions:
                        description += " | " + "; ".join(conditions)
                    if date_str or time_str:
                        description += f" | {date_str} {time_str}".strip()

                    all_text = " ".join([event_name, league, title, description])

                    special = Special(
                        provider=provider or "unknown",
                        title=title[:120],
                        description=description,
                        original_odds=original,
                        boosted_odds=boosted,
                        max_stake=max_stake,
                        category="boost",
                        sport=detect_sport(all_text),
                        event=event_name,
                        source="bettingstugan.se/alla-oddsbooster-idag",
                        scraped_at=now_iso,
                        url=url,
                    )
                    specials.append(special)

                    if verbose:
                        print(f"    BOOST: {provider} | {event_name} | {original:.2f} -> {boosted:.2f} | max {max_stake}")

                    # Skip past the boosted odds line
                    i += 2
                    continue

            i += 1

    except Exception as e:
        if verbose:
            print(f"  ERROR scraping bettingstugan/today: {e}")
            import traceback
            traceback.print_exc()

    return specials


def scrape_bettingstugan_overview(session: requests.Session, verbose: bool = False) -> list[Special]:
    """Scrape bettingstugan.se/forhojda-odds for boost examples and info."""
    url = "https://bettingstugan.se/forhojda-odds/"
    specials = []

    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        if verbose:
            print(f"  bettingstugan/overview: page loaded ({len(resp.text)} bytes)")

        # Look for provider-specific boost sections
        content = soup.select_one(".content") or soup.select_one(".entry-content")
        if not content:
            return specials

        # Find headings that contain provider names
        headings = content.select("h2, h3")
        for heading in headings:
            heading_text = heading.get_text(strip=True)
            provider = normalize_provider(heading_text.split()[0] if heading_text else "")

            if provider not in PROVIDER_ALIASES.values():
                continue

            # Get content between this heading and the next
            sibling = heading.find_next_sibling()
            while sibling and sibling.name not in ("h2", "h3"):
                text = sibling.get_text(strip=True)
                if text and len(text) > 15:
                    # Look for odds patterns
                    odds_match = re.search(r'(\d+[.,]\d{2})\s*(?:->|-->|till|=>|istallet for)\s*(\d+[.,]\d{2})', text)
                    if odds_match:
                        original = float(odds_match.group(1).replace(",", "."))
                        boosted = float(odds_match.group(2).replace(",", "."))
                        special = Special(
                            provider=provider,
                            title=text[:100],
                            description=text,
                            original_odds=original,
                            boosted_odds=boosted,
                            max_stake=parse_max_stake(text),
                            sport=detect_sport(text),
                            source="bettingstugan.se/forhojda-odds",
                            scraped_at=datetime.now(tz=None).isoformat(),
                            url=url,
                        )
                        specials.append(special)

                sibling = sibling.find_next_sibling() if sibling else None

    except Exception as e:
        if verbose:
            print(f"  ERROR scraping bettingstugan/overview: {e}")

    return specials


def scrape_casivo(session: requests.Session, verbose: bool = False) -> list[Special]:
    """Scrape casivo.se/oddsboost for boost info."""
    url = "https://www.casivo.se/oddsboost/"
    specials = []

    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        if verbose:
            print(f"  casivo.se: page loaded ({len(resp.text)} bytes)")

        content = soup.select_one(".content, .entry-content, main, article")
        if not content:
            return specials

        # Look for provider names followed by boost descriptions
        paragraphs = content.select("p, li")
        current_provider = None

        for p in paragraphs:
            text = p.get_text(strip=True)
            if not text:
                continue

            # Check if this paragraph starts with a known provider
            for alias, pid in PROVIDER_ALIASES.items():
                if text.lower().startswith(alias):
                    current_provider = pid
                    break

            # Look for odds patterns
            if current_provider:
                odds_match = re.search(r'(\d+[.,]\d{2})\s*(?:->|-->|till|=>)\s*(\d+[.,]\d{2})', text)
                if odds_match:
                    original = float(odds_match.group(1).replace(",", "."))
                    boosted = float(odds_match.group(2).replace(",", "."))
                    special = Special(
                        provider=current_provider,
                        title=text[:100],
                        description=text,
                        original_odds=original,
                        boosted_odds=boosted,
                        max_stake=parse_max_stake(text),
                        sport=detect_sport(text),
                        source="casivo.se/oddsboost",
                        scraped_at=datetime.now(tz=None).isoformat(),
                        url=url,
                    )
                    specials.append(special)

    except Exception as e:
        if verbose:
            print(f"  ERROR scraping casivo: {e}")

    return specials


def scrape_bettingkollen(session: requests.Session, verbose: bool = False) -> list[Special]:
    """
    Scrape bettingkollen.se boost data from their public Google Sheet.

    The site renders boost cards client-side from a CSV exported from:
    https://docs.google.com/spreadsheets/d/.../export?format=csv

    CSV schema: id, enable, match, outcome, old, new, stop
    - id: provider key (comeon, unibet, betsson, etc.)
    - enable: TRUE for active boosts
    - match: event name (e.g. "Manchester United - Tottenham")
    - outcome: bet description (e.g. "Bruno Fernandes gör mål eller assist")
    - old: original decimal odds
    - new: boosted decimal odds
    - stop: expiry in D/M/YY HH:MM format
    """
    csv_url = "https://docs.google.com/spreadsheets/d/1YJOeSg4QHzDOKLX7CqlfZ-gvoNaxoeg-ZqQ4gqah5LE/export?format=csv"
    specials = []
    now = datetime.now(tz=None)
    now_iso = now.isoformat()
    known_providers = set(PROVIDER_ALIASES.values())

    try:
        resp = session.get(csv_url, timeout=15)
        resp.raise_for_status()

        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)

        if verbose:
            print(f"  bettingkollen: {len(rows)} rows in sheet")

        for row in rows:
            # Only active boosts
            if row.get("enable") != "TRUE":
                continue

            # Must have match, outcome, and odds
            match = row.get("match", "").strip()
            outcome = row.get("outcome", "").strip()
            old_str = row.get("old", "").strip()
            new_str = row.get("new", "").strip()
            stop_str = row.get("stop", "").strip()

            if not match or not outcome or not old_str or not new_str:
                continue

            # Parse odds
            try:
                original = float(old_str.replace(",", "."))
                boosted = float(new_str.replace(",", "."))
            except ValueError:
                continue

            # Normalize provider
            provider_raw = row.get("id", "").strip().lower()
            provider = PROVIDER_ALIASES.get(provider_raw, provider_raw)

            # Skip providers not in our config
            if provider not in known_providers:
                if verbose:
                    print(f"    SKIP: {provider_raw} (not in providers.yaml)")
                continue

            # Parse expiry date (format: D/M/YY HH:MM)
            expires_at = None
            if stop_str:
                try:
                    dt = datetime.strptime(stop_str, "%d/%m/%y %H:%M")
                    # Skip expired boosts
                    if dt < now:
                        continue
                    expires_at = dt.isoformat()
                except ValueError:
                    pass

            all_text = f"{match} {outcome}"

            special = Special(
                provider=provider,
                title=outcome[:120],
                description=f"{match} | {outcome}",
                original_odds=original,
                boosted_odds=boosted,
                max_stake=None,
                category="boost",
                sport=detect_sport(all_text),
                event=match,
                expires_at=expires_at,
                source="bettingkollen.se",
                scraped_at=now_iso,
                url="https://bettingkollen.se/oddsbonus/oddsboost",
            )
            specials.append(special)

            if verbose:
                print(f"    BOOST: {provider} | {match} | {original:.2f} -> {boosted:.2f} | expires {stop_str}")

    except Exception as e:
        if verbose:
            print(f"  ERROR scraping bettingkollen: {e}")
            import traceback
            traceback.print_exc()

    return specials


# ============ Aggregation ============

def deduplicate_specials(specials: list[Special]) -> list[Special]:
    """Remove duplicate specials based on provider + odds or title match."""
    seen_keys = set()
    unique = []
    for s in specials:
        # Primary key: provider + original odds + boosted odds (same bet = same boost)
        odds_key = (s.provider, s.original_odds, s.boosted_odds)
        # Secondary key: provider + title prefix (catches reformulated descriptions)
        title_key = (s.provider, s.title[:40].lower())
        if odds_key not in seen_keys and title_key not in seen_keys:
            seen_keys.add(odds_key)
            seen_keys.add(title_key)
            unique.append(s)
    return unique


def scrape_all(verbose: bool = False) -> list[Special]:
    """Run all scrapers and return aggregated results."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    })

    all_specials = []

    sources = [
        ("bettingkollen", scrape_bettingkollen),
        ("bettingstugan/today", scrape_bettingstugan_today),
        ("bettingstugan/overview", scrape_bettingstugan_overview),
        ("casivo", scrape_casivo),
    ]

    for name, scraper in sources:
        if verbose:
            print(f"Scraping {name}...")
        results = scraper(session, verbose=verbose)
        if verbose:
            print(f"  -> {len(results)} specials found")
        all_specials.extend(results)

    # Deduplicate
    unique = deduplicate_specials(all_specials)
    if verbose:
        print(f"\nTotal: {len(all_specials)} raw, {len(unique)} unique specials")

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
    parser = argparse.ArgumentParser(description="Scrape odds boosts from Swedish betting sites")
    parser.add_argument("--save", action="store_true", help="Save results to data/specials.json")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    specials = scrape_all(verbose=args.verbose)

    if not specials:
        print("No specials found. Sites may have changed structure or no boosts active today.")
        if args.save:
            path = save_specials(specials)
            print(f"Empty results saved to {path}")
        return

    # Print results
    print(f"\n{'='*60}")
    print(f"  ODDS BOOSTS & SPECIALS ({len(specials)} found)")
    print(f"{'='*60}\n")

    # Group by provider
    by_provider: dict[str, list[Special]] = {}
    for s in specials:
        by_provider.setdefault(s.provider, []).append(s)

    for provider, items in sorted(by_provider.items()):
        _print(f"  {provider.upper()} ({len(items)} boosts)")
        for item in items:
            odds_str = ""
            if item.original_odds and item.boosted_odds:
                odds_str = f"  {item.original_odds:.2f} -> {item.boosted_odds:.2f}"
            elif item.boosted_odds:
                odds_str = f"  odds: {item.boosted_odds:.2f}"

            stake_str = f"  max {item.max_stake:.0f} kr" if item.max_stake else ""
            sport_str = f"  [{item.sport}]" if item.sport != "unknown" else ""

            _print(f"    {item.title}")
            if odds_str or stake_str or sport_str:
                _print(f"   {odds_str}{stake_str}{sport_str}")
        print()

    if args.save:
        path = save_specials(specials)
        print(f"Saved to {path}")


if __name__ == "__main__":
    main()
