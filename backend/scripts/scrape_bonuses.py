#!/usr/bin/env python3
"""
Bonus Scraper - Multi-source bonus data aggregation

Scrapes bonus information from Swedish betting affiliate sites and updates
providers.yaml with wagering requirements, min odds, and bonus amounts.

Sources:
    - speltips.se/bettingsidor/bonusar
    - rekatochklart.com/betting-bonusar/
    - bettingstugan.se/oddsbonusar
    - betting.se/bonus
    - tvmatchen.nu/betting/oddsbonusar/

Usage:
    python scripts/scrape_bonuses.py              # Scrape and show diff
    python scripts/scrape_bonuses.py --apply       # Scrape and update providers.yaml
    python scripts/scrape_bonuses.py --dry-run     # Show what would change (default)
"""

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

# Provider name aliases -> canonical provider ID in providers.yaml
PROVIDER_ALIASES = {
    # Kambi API
    "unibet": "unibet",
    "leovegas": "leovegas",
    "leo vegas": "leovegas",
    "expekt": "expekt",
    "betmgm": "betmgm",
    "bet mgm": "betmgm",
    "speedybet": "speedybet",
    "speedy bet": "speedybet",
    "speedbet": "speedybet",
    "speedy": "speedybet",
    "x3000": "x3000",
    "golden bull": "goldenbull",
    "goldenbull": "goldenbull",
    "1x2": "1x2",
    "1 x 2": "1x2",
    # Altenar API
    "betinia": "betinia",
    "campobet": "campobet",
    "campo bet": "campobet",
    "swiper": "swiper",
    "lodur": "lodur",
    "dbet": "dbet",
    "d-bet": "dbet",
    "d bet": "dbet",
    "quickcasino": "quickcasino",
    "quick casino": "quickcasino",
    # Spectate (888/Evoke)
    "mr green": "mrgreen",
    "mrgreen": "mrgreen",
    "888sport": "888sport",
    "888 sport": "888sport",
    # Gecko V2 (Betsson Group)
    "betsson": "betsson",
    "betsafe": "betsafe",
    "nordicbet": "nordicbet",
    "nordic bet": "nordicbet",
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


@dataclass
class BonusInfo:
    """Scraped bonus information for a provider."""
    provider_name: str
    provider_id: Optional[str] = None  # Canonical ID in providers.yaml
    bonus_type: str = "bonusdeposit"  # bonusdeposit, freebet, riskfree
    amount: int = 0  # SEK
    wagering_multiplier: float = 1.0  # e.g., 6.0 means 6x
    min_odds: float = 1.80  # Minimum odds for wagering qualification
    validity_days: int = 60
    sources: list = field(default_factory=list)  # Which sites reported this


def normalize_provider_name(name: str) -> Optional[str]:
    """Map scraped provider name to canonical provider ID."""
    cleaned = name.lower().strip()
    # Remove common suffixes
    cleaned = re.sub(r'\s*(sport|casino|betting|se)$', '', cleaned).strip()
    return PROVIDER_ALIASES.get(cleaned)


def normalize_provider_name_or_raw(name: str) -> tuple[str, bool]:
    """Map scraped provider name to canonical ID, or return cleaned name for unknowns.

    Returns (provider_id, is_known).
    """
    canonical = normalize_provider_name(name)
    if canonical:
        return canonical, True

    # --- Filter out junk that isn't a provider name ---
    raw = name.strip()

    # Pure numbers (table row indices)
    if re.fullmatch(r'\d+', raw):
        return "", False

    # Too short (single char/word fragments) or too long (full sentences)
    if len(raw) < 3 or len(raw) > 40:
        return "", False

    # Contains emoji
    if re.search(r'[\U0001F300-\U0001FAFF\u2600-\u27BF]', raw):
        return "", False

    # Swedish UI/table labels that aren't providers
    _JUNK_NAMES = {
        "betalningsmetoder", "kundtjanst", "kundtjänst", "spelutbud",
        "mobilupplevelse", "spelupplevelse", "helhetsbetyg",
        "bonuserbjudande", "omsattningskrav", "omsättningskrav",
        "minsta insattning", "minsta insättning", "bonus", "bonusar",
        "spelsida", "spelsidor", "bettingsida", "bettingsidor",
        "oddsbonus", "oddsbonusar", "välkomstbonus", "freebet",
        "gratisspel", "riskfritt", "riskfria", "webbsida",
        "uttag", "insattning", "insättning", "licens",
        "fördelar", "nackdelar", "rank", "spelbolag",
        "jämförelse av oddsbonusar", "jämförelse",
        "free spins", "freespins", "cashback",
        "sbk bonus", "alexsnacke",
    }
    lowered = raw.lower()
    if lowered in _JUNK_NAMES:
        return "", False

    # Multi-word phrases (4+ words) are likely descriptions, not provider names
    if len(lowered.split()) >= 4:
        return "", False

    # Contains colon (like "Ägare:PAF MT Limited") — not a provider name
    if ":" in raw:
        return "", False

    # Starts with common junk prefixes (category headers, descriptive text)
    _JUNK_PREFIXES = (
        "störst ", "största ", "nya ", "bästa ", "generös ", "topp ",
        "webbsida:", "🎲", "🎁", "💰", "♠",
        "inget ", "kan ", "ägare",
    )
    for prefix in _JUNK_PREFIXES:
        if lowered.startswith(prefix):
            return "", False

    # Generate a stable ID from the raw name
    cleaned = lowered
    cleaned = re.sub(r'\s*(sport|casino|betting|se)$', '', cleaned).strip()
    cleaned = re.sub(r'[^a-z0-9]', '', cleaned)
    if not cleaned or len(cleaned) < 3:
        return "", False
    return cleaned, False


def parse_amount(text: str) -> int:
    """Extract SEK amount from text like '1 000 kr', '500', 'Up to 1,000'."""
    # Remove common prefixes/suffixes
    text = re.sub(r'(upp?\s*till?|up\s*to|max)\s*', '', text, flags=re.IGNORECASE)
    # Normalize unicode whitespace and separators
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace(',', '').replace('.', '')
    # Find number (potentially with spaces as thousand separator)
    match = re.search(r'(\d[\d\s]*\d|\d+)', text)
    if match:
        return int(match.group(1).replace(' ', ''))
    return 0


def parse_wagering(text: str) -> float:
    """Extract wagering multiplier from text like '6x omsättning', '12x bonus', '20x'."""
    # More specific patterns to avoid matching "1x2" provider names
    # Pattern 1: "Nx omsättning/bonus/insättning" (most reliable)
    match = re.search(r'(\d{1,2})x\s*(?:omsättning|bonus|insättning|gånger)', text, re.IGNORECASE)
    if match:
        val = int(match.group(1))
        if 1 <= val <= 50:  # Sanity check
            return float(val)

    # Pattern 2: "omsättningskrav: Nx" or "omsättning Nx"
    match = re.search(r'omsättning(?:skrav)?\s*[:=]?\s*(\d{1,2})x?', text, re.IGNORECASE)
    if match:
        val = int(match.group(1))
        if 1 <= val <= 50:
            return float(val)

    # Pattern 3: Standalone "Nx" where N > 1 and followed by word boundary (not "1x2")
    match = re.search(r'(?<!\d)([2-9]\d?)x\b(?!\d)', text, re.IGNORECASE)
    if match:
        val = int(match.group(1))
        if 2 <= val <= 50:
            return float(val)

    # "no requirement" / "omsättningsfritt"
    if re.search(r'(omsättningsfri|utan\s*omsättning|no\s*req|inga\s*omsättning)', text, re.IGNORECASE):
        return 1.0

    # Return 0 = unknown (don't assume 1x)
    return 0.0


def parse_min_odds(text: str) -> float:
    """Extract minimum odds from text like '1.80', 'min odds 1.90', 'odds >= 1.80'."""
    # Pattern 1: Explicit "min odds" / "lägsta odds" pattern
    match = re.search(r'(?:min(?:st|imum)?\s*odds?|lägst[a]?\s*odds?|odds\s*(?:>=?|minst))\s*[:=]?\s*(\d+[.,]\d+)', text, re.IGNORECASE)
    if match:
        val = float(match.group(1).replace(',', '.'))
        if 1.10 <= val <= 3.00:
            return val

    # Pattern 2: "odds Nx" or standalone odds in likely context
    match = re.search(r'(?:odds|oddskrav)\s*[:=]?\s*(\d+[.,]\d+)', text, re.IGNORECASE)
    if match:
        val = float(match.group(1).replace(',', '.'))
        if 1.10 <= val <= 3.00:
            return val

    # Return 0 = unknown (don't assume 1.80)
    return 0.0


def detect_bonus_type(text: str) -> str:
    """Detect bonus type from description text."""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ['free bet', 'freebet', 'gratisspel', 'gratis spel', 'gratis insats',
                                        'riskfri', 'risk-free', 'free play', 'riskfritt']):
        return 'freebet'
    if any(kw in text_lower for kw in ['dubbla', 'dubbl', 'double', 'matcha', 'match deposit', 'insättningsbonus']):
        return 'bonusdeposit'
    # Default: unknown
    return 'unknown'


# ============ Source Scrapers ============

def scrape_speltips(session: requests.Session) -> list[BonusInfo]:
    """Scrape speltips.se/bettingsidor/bonusar."""
    url = "https://speltips.se/bettingsidor/bonusar"
    bonuses = []

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # Find bonus cards/rows - speltips uses WordPress with structured review boxes
        # Look for common patterns: tables, review cards, or structured divs
        rows = _extract_bonus_rows(soup)

        for row in rows:
            provider_name = row.get("provider", "").strip()
            provider_id, _is_known = normalize_provider_name_or_raw(provider_name)
            if not provider_id:
                continue

            bonuses.append(BonusInfo(
                provider_name=provider_name,
                provider_id=provider_id,
                bonus_type=detect_bonus_type(row.get("description", "")),
                amount=parse_amount(row.get("amount", "0")),
                wagering_multiplier=parse_wagering(row.get("wagering", "1x")),
                min_odds=parse_min_odds(row.get("min_odds", "1.80")),
                sources=["speltips.se"],
            ))

        logger.info(f"[speltips.se] Scraped {len(bonuses)} provider bonuses")
    except Exception as e:
        logger.warning(f"[speltips.se] Scrape failed: {e}")

    return bonuses


def scrape_rekatochklart(session: requests.Session) -> list[BonusInfo]:
    """Scrape rekatochklart.com/betting-bonusar/."""
    url = "https://www.rekatochklart.com/betting-bonusar/"
    bonuses = []

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = _extract_bonus_rows(soup)

        for row in rows:
            provider_name = row.get("provider", "").strip()
            provider_id, _is_known = normalize_provider_name_or_raw(provider_name)
            if not provider_id:
                continue

            bonuses.append(BonusInfo(
                provider_name=provider_name,
                provider_id=provider_id,
                bonus_type=detect_bonus_type(row.get("description", "")),
                amount=parse_amount(row.get("amount", "0")),
                wagering_multiplier=parse_wagering(row.get("wagering", "1x")),
                min_odds=parse_min_odds(row.get("min_odds", "1.80")),
                sources=["rekatochklart.com"],
            ))

        logger.info(f"[rekatochklart.com] Scraped {len(bonuses)} provider bonuses")
    except Exception as e:
        logger.warning(f"[rekatochklart.com] Scrape failed: {e}")

    return bonuses


def scrape_bettingstugan(session: requests.Session) -> list[BonusInfo]:
    """Scrape bettingstugan.se/oddsbonusar."""
    url = "https://bettingstugan.se/oddsbonusar"
    bonuses = []

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = _extract_bonus_rows(soup)

        for row in rows:
            provider_name = row.get("provider", "").strip()
            provider_id, _is_known = normalize_provider_name_or_raw(provider_name)
            if not provider_id:
                continue

            bonuses.append(BonusInfo(
                provider_name=provider_name,
                provider_id=provider_id,
                bonus_type=detect_bonus_type(row.get("description", "")),
                amount=parse_amount(row.get("amount", "0")),
                wagering_multiplier=parse_wagering(row.get("wagering", "1x")),
                min_odds=parse_min_odds(row.get("min_odds", "1.80")),
                sources=["bettingstugan.se"],
            ))

        logger.info(f"[bettingstugan.se] Scraped {len(bonuses)} provider bonuses")
    except Exception as e:
        logger.warning(f"[bettingstugan.se] Scrape failed: {e}")

    return bonuses


def scrape_betting_se(session: requests.Session) -> list[BonusInfo]:
    """Scrape betting.se/bonus."""
    url = "https://www.betting.se/bonus"
    bonuses = []

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = _extract_bonus_rows(soup)

        for row in rows:
            provider_name = row.get("provider", "").strip()
            provider_id, _is_known = normalize_provider_name_or_raw(provider_name)
            if not provider_id:
                continue

            bonuses.append(BonusInfo(
                provider_name=provider_name,
                provider_id=provider_id,
                bonus_type=detect_bonus_type(row.get("description", "")),
                amount=parse_amount(row.get("amount", "0")),
                wagering_multiplier=parse_wagering(row.get("wagering", "1x")),
                min_odds=parse_min_odds(row.get("min_odds", "1.80")),
                sources=["betting.se"],
            ))

        logger.info(f"[betting.se] Scraped {len(bonuses)} provider bonuses")
    except Exception as e:
        logger.warning(f"[betting.se] Scrape failed: {e}")

    return bonuses


def scrape_tvmatchen(session: requests.Session) -> list[BonusInfo]:
    """Scrape tvmatchen.nu/betting/oddsbonusar/."""
    url = "https://www.tvmatchen.nu/betting/oddsbonusar/"
    bonuses = []

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = _extract_bonus_rows(soup)

        for row in rows:
            provider_name = row.get("provider", "").strip()
            provider_id, _is_known = normalize_provider_name_or_raw(provider_name)
            if not provider_id:
                continue

            bonuses.append(BonusInfo(
                provider_name=provider_name,
                provider_id=provider_id,
                bonus_type=detect_bonus_type(row.get("description", "")),
                amount=parse_amount(row.get("amount", "0")),
                wagering_multiplier=parse_wagering(row.get("wagering", "1x")),
                min_odds=parse_min_odds(row.get("min_odds", "1.80")),
                sources=["tvmatchen.nu"],
            ))

        logger.info(f"[tvmatchen.nu] Scraped {len(bonuses)} provider bonuses")
    except Exception as e:
        logger.warning(f"[tvmatchen.nu] Scrape failed: {e}")

    return bonuses


# ============ HTML Extraction Helpers ============

def _extract_bonus_rows(soup: BeautifulSoup) -> list[dict]:
    """
    Extract bonus info from HTML using multiple strategies.

    These Swedish affiliate sites typically use:
    1. HTML tables with provider rows
    2. Review cards/boxes with structured data
    3. Accordion/FAQ sections with details

    Returns list of dicts with keys: provider, amount, wagering, min_odds, description
    """
    rows = []

    # Strategy 1: Look for HTML tables
    for table in soup.find_all("table"):
        headers = []
        for th in table.find_all("th"):
            headers.append(th.get_text(strip=True).lower())

        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            cell_texts = [c.get_text(strip=True) for c in cells]

            # Try to identify provider name (usually first column)
            row_data = _parse_table_row(cell_texts, headers, tr)
            if row_data and row_data.get("provider"):
                rows.append(row_data)

    # Strategy 2: Look for review/bonus cards
    card_selectors = [
        ".bonus-card", ".review-box", ".betting-bonus",
        ".operator-card", ".sportsbook-bonus", ".bonus-offer",
        "[class*='bonus']", "[class*='review']", "[class*='operator']",
    ]
    for selector in card_selectors:
        for card in soup.select(selector):
            row_data = _parse_bonus_card(card)
            if row_data and row_data.get("provider"):
                rows.append(row_data)

    # Strategy 3: Look for structured content with headings
    for heading in soup.find_all(["h2", "h3", "h4"]):
        text = heading.get_text(strip=True)
        provider_id = normalize_provider_name(text)
        if provider_id:
            # Get following content until next heading
            content = []
            sibling = heading.find_next_sibling()
            while sibling and sibling.name not in ["h2", "h3", "h4"]:
                content.append(sibling.get_text(strip=True))
                sibling = sibling.find_next_sibling()

            full_text = " ".join(content)
            row_data = _parse_text_block(text, full_text)
            if row_data:
                rows.append(row_data)

    return rows


def _parse_table_row(cells: list[str], headers: list[str], tr=None) -> Optional[dict]:
    """Parse a table row into bonus info dict."""
    if not cells:
        return None

    provider = cells[0]

    # If cells[0] doesn't look like a provider name, try extracting from the row DOM
    pid, is_known = normalize_provider_name_or_raw(provider)
    if not is_known and tr is not None:
        # Try image alt text
        for img in tr.find_all("img"):
            alt = img.get("alt", "")
            cleaned = re.sub(r'^(besök|gå till|visit)\s+', '', alt, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r'\s*(logo|ikon|icon|bonus)$', '', cleaned, flags=re.IGNORECASE).strip()
            if cleaned and len(cleaned) >= 3 and len(cleaned) <= 40:
                provider = cleaned
                break

        # Try link hrefs (e.g., /lyllo-casino, /spelbolag/bet365)
        if provider == cells[0]:  # Still unchanged
            for a in tr.find_all("a", href=True):
                href = a["href"]
                for pattern in [r'/spelbolag/([a-z0-9-]+)', r'/bettingsidor/([a-z0-9-]+)',
                                r'/(?:hamta|besok)/([a-z0-9-]+)', r'\.se/([a-z0-9-]+)/?$']:
                    m = re.search(pattern, href, re.IGNORECASE)
                    if m:
                        slug = m.group(1)
                        name = re.sub(r'-(casino|sport|betting|se|odds)$', '', slug)
                        name = name.replace('-', ' ').strip().title()
                        if name and len(name) >= 3:
                            provider = name
                            break
                if provider != cells[0]:
                    break

    row = {"provider": provider}
    full_text = " ".join(cells)

    # Extract amount
    for cell in cells[1:]:
        amount = parse_amount(cell)
        if amount > 0:
            row["amount"] = str(amount)
            break

    # Also try full text for amount if cells didn't have it
    if "amount" not in row:
        amount = parse_amount(full_text)
        if amount > 0:
            row["amount"] = str(amount)

    # Extract wagering using improved parser
    wager_val = parse_wagering(full_text)
    if wager_val > 0:
        row["wagering"] = f"{int(wager_val)}x"

    # Extract min odds using improved parser
    min_odds_val = parse_min_odds(full_text)
    if min_odds_val > 0:
        row["min_odds"] = str(min_odds_val)

    row["description"] = full_text
    return row


def _parse_bonus_card(card) -> Optional[dict]:
    """Parse a bonus card/review box element."""
    text = card.get_text(" ", strip=True)
    if len(text) < 10:
        return None

    # Try to find provider name from multiple sources
    provider = ""

    # 1. Known providers from links, headings, images
    for el in card.find_all(["a", "h2", "h3", "h4", "strong", "b"]):
        el_text = el.get_text(strip=True)
        if normalize_provider_name(el_text):
            provider = el_text
            break

    if not provider:
        for img in card.find_all("img"):
            alt = img.get("alt", "")
            if normalize_provider_name(alt):
                provider = alt
                break

    # 2. Unknown providers: extract name from link hrefs (e.g., /spelbolag/lyllo-casino)
    if not provider:
        for a in card.find_all("a", href=True):
            href = a["href"]
            for pattern in [r'/spelbolag/([a-z0-9-]+)', r'/bettingsidor/([a-z0-9-]+)', r'/r/([a-z0-9-]+)/']:
                m = re.search(pattern, href, re.IGNORECASE)
                if m:
                    slug = m.group(1)
                    # Clean slug: remove trailing -casino, -sport, -betting etc.
                    name = re.sub(r'-(casino|sport|betting|se|odds)$', '', slug)
                    name = name.replace('-', ' ').strip().title()
                    if name and len(name) >= 3:
                        provider = name
                        break
            if provider:
                break

    # 3. Try image alt text for unknowns (e.g., alt="Besök Lyllo Casino")
    if not provider:
        for img in card.find_all("img"):
            alt = img.get("alt", "")
            # Strip common prefixes: "Besök X", "Gå till X"
            cleaned = re.sub(r'^(besök|gå till|visit)\s+', '', alt, flags=re.IGNORECASE).strip()
            # Strip suffixes
            cleaned = re.sub(r'\s*(casino|sport|betting|logo|ikon)$', '', cleaned, flags=re.IGNORECASE).strip()
            if cleaned and len(cleaned) >= 3 and len(cleaned) <= 30:
                provider = cleaned
                break

    if not provider:
        return None

    return _parse_text_block(provider, text)


def _parse_text_block(provider: str, text: str) -> Optional[dict]:
    """Parse a text block to extract bonus info."""
    row = {"provider": provider, "description": text}

    # Amount - look for "N kr" / "N SEK" patterns
    amount_match = re.search(r'(\d[\d\s\xa0\u202f]*\d|\d+)\s*(?:kr|sek|kronor)', text, re.IGNORECASE)
    if amount_match:
        row["amount"] = amount_match.group(1).replace(' ', '').replace('\xa0', '').replace('\u202f', '')

    # Wagering - use the improved parse_wagering function
    wager_val = parse_wagering(text)
    if wager_val > 0:
        row["wagering"] = f"{int(wager_val)}x"

    # Min odds - use the improved parse_min_odds function
    min_odds_val = parse_min_odds(text)
    if min_odds_val > 0:
        row["min_odds"] = str(min_odds_val)

    return row


# ============ Aggregation ============

def aggregate_bonuses(all_bonuses: list[BonusInfo]) -> dict[str, BonusInfo]:
    """
    Aggregate bonus data from multiple sources per provider.

    Strategy:
    1. Filter out unknown/zero values from scraping
    2. Use majority vote for values that were actually extracted
    3. Fall back to KNOWN_BONUSES baseline for any missing values
    4. Only override baseline when 2+ sources agree on a different value
    """
    by_provider: dict[str, list[BonusInfo]] = {}
    for b in all_bonuses:
        if b.provider_id:
            by_provider.setdefault(b.provider_id, []).append(b)

    aggregated = {}
    for pid, entries in by_provider.items():
        # Filter to only values that were actually extracted (non-zero/non-unknown)
        amounts = [e.amount for e in entries if e.amount > 0]
        wagerings = [e.wagering_multiplier for e in entries if e.wagering_multiplier > 0]
        min_odds_list = [e.min_odds for e in entries if e.min_odds > 0]
        types = [e.bonus_type for e in entries if e.bonus_type != "unknown"]
        sources = []
        for e in entries:
            sources.extend(e.sources)

        # Get baseline values
        baseline = KNOWN_BONUSES.get(pid, {})
        is_known = pid in KNOWN_BONUSES or pid in PROVIDER_ALIASES.values()

        if is_known:
            # Known providers: require 2+ source agreement, fall back to baseline
            baseline_type = baseline.get("type", "bonusdeposit")
            baseline_amount = baseline.get("amount", 500)
            baseline_wagering = baseline.get("wagering_multiplier", 1.0)
            baseline_min_odds = baseline.get("min_odds", 1.80)

            scraped_type = _most_common(types, None) if len(types) >= 2 else None
            scraped_amount = _most_common(amounts, None) if len(amounts) >= 2 else None
            scraped_wagering = _most_common(wagerings, None) if len(wagerings) >= 2 else None
            scraped_min_odds = _most_common(min_odds_list, None) if len(min_odds_list) >= 2 else None

            aggregated[pid] = BonusInfo(
                provider_name=entries[0].provider_name,
                provider_id=pid,
                bonus_type=scraped_type or baseline_type,
                amount=int(scraped_amount) if scraped_amount else baseline_amount,
                wagering_multiplier=scraped_wagering if scraped_wagering else baseline_wagering,
                min_odds=scraped_min_odds if scraped_min_odds else baseline_min_odds,
                sources=list(set(sources)),
            )
        else:
            # Unknown providers: use best available scraped data (even single source)
            aggregated[pid] = BonusInfo(
                provider_name=entries[0].provider_name,
                provider_id=pid,
                bonus_type=_most_common(types, "unknown") if types else "unknown",
                amount=int(_most_common(amounts, 0)) if amounts else 0,
                wagering_multiplier=_most_common(wagerings, 0) if wagerings else 0,
                min_odds=_most_common(min_odds_list, 0) if min_odds_list else 0,
                sources=list(set(sources)),
            )

    return aggregated


def _most_common(values: list, default=None):
    """Return most common value in list, or default if empty."""
    if not values:
        return default
    from collections import Counter
    counter = Counter(values)
    return counter.most_common(1)[0][0]


# ============ YAML Update ============

def update_providers_yaml(
    aggregated: dict[str, BonusInfo],
    config_path: Path,
    apply: bool = False,
) -> list[str]:
    """
    Update providers.yaml with scraped bonus data.

    Args:
        aggregated: Provider ID -> BonusInfo mapping
        config_path: Path to providers.yaml
        apply: If True, write changes. If False, dry-run.

    Returns:
        List of change descriptions
    """
    with open(config_path) as f:
        raw_yaml = f.read()
        config = yaml.safe_load(raw_yaml)

    providers = config.get("providers", {})
    changes = []

    for pid, bonus in aggregated.items():
        if pid not in providers:
            continue

        provider = providers[pid]
        current_bonus = provider.get("bonus", {})

        # Build new bonus config
        new_bonus = {
            "type": bonus.bonus_type,
            "amount": bonus.amount,
            "wagering_multiplier": bonus.wagering_multiplier,
            "min_odds": bonus.min_odds,
        }

        # Check what changed
        old_type = current_bonus.get("type", "unknown")
        old_amount = current_bonus.get("amount", 0)
        old_wagering = current_bonus.get("wagering_multiplier", None)
        old_min_odds = current_bonus.get("min_odds", None)

        diffs = []
        warnings = []
        if old_type != new_bonus["type"]:
            diffs.append(f"type: {old_type} -> {new_bonus['type']}")
        if old_amount != new_bonus["amount"]:
            diffs.append(f"amount: {old_amount} -> {new_bonus['amount']}")
        if old_wagering != new_bonus["wagering_multiplier"]:
            # Flag suspicious wagering jumps (likely casino bonus contamination)
            if new_bonus["wagering_multiplier"] > 25:
                warnings.append(f"SUSPICIOUS wagering {new_bonus['wagering_multiplier']}x (>25x, likely casino bonus)")
            else:
                diffs.append(f"wagering: {old_wagering} -> {new_bonus['wagering_multiplier']}")
        if old_min_odds != new_bonus["min_odds"]:
            diffs.append(f"min_odds: {old_min_odds} -> {new_bonus['min_odds']}")

        if warnings:
            for w in warnings:
                changes.append(f"  {pid}: {w} (SKIPPED)")
            # Don't apply suspicious changes - keep current YAML values
            continue

        if diffs:
            changes.append(f"  {pid}: {', '.join(diffs)} (sources: {', '.join(bonus.sources)})")
            provider["bonus"] = new_bonus

    if apply and changes:
        # Write back - use custom YAML dump to preserve structure
        _write_yaml_preserving_structure(config_path, config)
        logger.info(f"Updated {len(changes)} providers in {config_path}")

    return changes


def _write_yaml_preserving_structure(path: Path, config: dict):
    """Write YAML config back, preserving comments and structure as much as possible."""
    # Read original to preserve comments at the top
    with open(path) as f:
        original = f.read()

    # Extract header comments (lines before first key)
    header_lines = []
    for line in original.split('\n'):
        if line.startswith('#') or line.strip() == '':
            header_lines.append(line)
        else:
            break

    # Custom YAML representer for clean output
    class CleanDumper(yaml.SafeDumper):
        pass

    def str_representer(dumper, data):
        if '\n' in data:
            return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
        return dumper.represent_scalar('tag:yaml.org,2002:str', data)

    CleanDumper.add_representer(str, str_representer)

    yaml_content = yaml.dump(
        config,
        Dumper=CleanDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )

    with open(path, 'w') as f:
        f.write('\n'.join(header_lines) + '\n' if header_lines else '')
        f.write(yaml_content)


# ============ Hardcoded Fallback Data ============

# Cross-referenced from 7 sources (Feb 2026)
# Used as fallback when scraping fails or as validation baseline
KNOWN_BONUSES: dict[str, dict] = {
    # Kambi providers
    "unibet": {"type": "freebet", "amount": 1000, "wagering_multiplier": 1.0, "min_odds": 1.80},
    "leovegas": {"type": "bonusdeposit", "amount": 600, "wagering_multiplier": 6.0, "min_odds": 1.80},
    "expekt": {"type": "bonusdeposit", "amount": 1000, "wagering_multiplier": 20.0, "min_odds": 1.80},
    "betmgm": {"type": "freebet", "amount": 500, "wagering_multiplier": 1.0, "min_odds": 1.80},
    "speedybet": {"type": "bonusdeposit", "amount": 500, "wagering_multiplier": 12.0, "min_odds": 1.80},
    "x3000": {"type": "bonusdeposit", "amount": 500, "wagering_multiplier": 12.0, "min_odds": 1.80},
    "goldenbull": {"type": "bonusdeposit", "amount": 500, "wagering_multiplier": 12.0, "min_odds": 1.80},
    "1x2": {"type": "bonusdeposit", "amount": 500, "wagering_multiplier": 12.0, "min_odds": 1.80},
    # Spectate providers
    "mrgreen": {"type": "freebet", "amount": 500, "wagering_multiplier": 1.0, "min_odds": 1.80},
    "888sport": {"type": "bonusdeposit", "amount": 500, "wagering_multiplier": 1.0, "min_odds": 1.80},
    # Gecko V2 (Betsson Group)
    "betsson": {"type": "freebet", "amount": 250, "wagering_multiplier": 1.0, "min_odds": 1.80},
    "betsafe": {"type": "freebet", "amount": 100, "wagering_multiplier": 1.0, "min_odds": 1.80},
    "nordicbet": {"type": "freebet", "amount": 100, "wagering_multiplier": 1.0, "min_odds": 1.80},
    "spelklubben": {"type": "bonusdeposit", "amount": 500, "wagering_multiplier": 15.0, "min_odds": 1.90},
    # SBTech
    "bethard": {"type": "bonusdeposit", "amount": 500, "wagering_multiplier": 15.0, "min_odds": 1.90},
    "10bet": {"type": "bonusdeposit", "amount": 1000, "wagering_multiplier": 8.0, "min_odds": 1.80},
    # Snabbare
    "snabbare": {"type": "bonusdeposit", "amount": 600, "wagering_multiplier": 8.0, "min_odds": 1.80},
    # ComeOn group
    "comeon": {"type": "bonusdeposit", "amount": 500, "wagering_multiplier": 6.0, "min_odds": 1.80},
    "hajper": {"type": "freebet", "amount": 500, "wagering_multiplier": 1.0, "min_odds": 1.80},
    # Altenar
    "betinia": {"type": "bonusdeposit", "amount": 1000, "wagering_multiplier": 6.0, "min_odds": 1.80},
    "campobet": {"type": "bonusdeposit", "amount": 500, "wagering_multiplier": 6.0, "min_odds": 1.80},
    "swiper": {"type": "bonusdeposit", "amount": 500, "wagering_multiplier": 6.0, "min_odds": 1.80},
    "lodur": {"type": "bonusdeposit", "amount": 1000, "wagering_multiplier": 6.0, "min_odds": 1.80},
    "dbet": {"type": "freebet", "amount": 500, "wagering_multiplier": 1.0, "min_odds": 1.80},
    "quickcasino": {"type": "bonusdeposit", "amount": 500, "wagering_multiplier": 6.0, "min_odds": 1.80},
    # BetConstruct
    "vbet": {"type": "freebet", "amount": 1500, "wagering_multiplier": 1.0, "min_odds": 1.80},
    # Interwetten
    "interwetten": {"type": "bonusdeposit", "amount": 1000, "wagering_multiplier": 5.0, "min_odds": 1.70},
}


def get_fallback_bonuses() -> dict[str, BonusInfo]:
    """Return hardcoded bonus data as fallback."""
    result = {}
    for pid, data in KNOWN_BONUSES.items():
        result[pid] = BonusInfo(
            provider_name=pid,
            provider_id=pid,
            bonus_type=data["type"],
            amount=data["amount"],
            wagering_multiplier=data["wagering_multiplier"],
            min_odds=data["min_odds"],
            sources=["hardcoded_baseline"],
        )
    return result


# ============ API-callable Functions ============

DATA_DIR = Path(__file__).parent.parent / "data"


def scrape_all_bonuses(verbose: bool = False) -> dict[str, BonusInfo]:
    """
    Run all bonus scrapers and return aggregated results.

    Returns dict of provider_id -> BonusInfo with aggregated data.
    Falls back to KNOWN_BONUSES if scraping yields nothing.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    })

    all_bonuses = []
    scrapers = [
        ("speltips.se", scrape_speltips),
        ("rekatochklart.com", scrape_rekatochklart),
        ("bettingstugan.se", scrape_bettingstugan),
        ("betting.se", scrape_betting_se),
        ("tvmatchen.nu", scrape_tvmatchen),
    ]

    for name, scraper_fn in scrapers:
        if verbose:
            logger.info(f"Scraping {name}...")
        bonuses = scraper_fn(session)
        all_bonuses.extend(bonuses)
        if verbose:
            logger.info(f"  -> {len(bonuses)} providers found")

    if all_bonuses:
        return aggregate_bonuses(all_bonuses)
    else:
        logger.warning("No data scraped from any source. Using hardcoded fallback.")
        return get_fallback_bonuses()


def validate_bonuses(scraped: dict[str, BonusInfo] | None = None) -> dict:
    """
    Compare scraped/baseline bonus data against providers.yaml config.

    Returns a validation report with:
    - per-provider status (match/mismatch/missing)
    - list of changes detected
    - alerts for new or removed bonuses

    If scraped is None, uses KNOWN_BONUSES baseline.
    """
    import json
    from datetime import datetime

    config_path = Path(__file__).parent.parent / "src" / "config" / "providers.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    providers = config.get("providers", {})

    if scraped is None:
        scraped = get_fallback_bonuses()

    results = {
        "validated_at": datetime.now(tz=None).isoformat(),
        "providers_checked": 0,
        "matches": 0,
        "mismatches": 0,
        "missing_from_yaml": [],
        "missing_from_scrape": [],
        "changes": [],
        "provider_status": {},
    }

    # Check all providers in YAML that have bonuses
    yaml_bonus_providers = {
        pid for pid, p in providers.items()
        if "bonus" in p and pid not in ("pinnacle", "polymarket")
    }
    # ALL provider IDs in YAML (for filtering new-provider suggestions)
    all_yaml_providers = set(providers.keys())

    scraped_providers = set(scraped.keys())

    # Providers in scrape but not in YAML at all (truly new/unknown providers)
    for pid in scraped_providers - all_yaml_providers:
        s = scraped[pid]
        # Require meaningful bonus data: amount >= 50 kr
        if s.amount < 50:
            continue
        results["missing_from_yaml"].append({
            "provider_id": pid,
            "provider_name": s.provider_name,
            "scraped_bonus": {
                "type": s.bonus_type,
                "amount": s.amount,
                "wagering_multiplier": s.wagering_multiplier,
                "min_odds": s.min_odds,
            },
            "sources": s.sources,
        })

    # Providers in YAML but not in scrape
    for pid in yaml_bonus_providers - scraped_providers:
        yaml_bonus = providers[pid].get("bonus", {})
        results["missing_from_scrape"].append({
            "provider_id": pid,
            "yaml_bonus": yaml_bonus,
        })

    # Compare matched providers
    for pid in yaml_bonus_providers & scraped_providers:
        results["providers_checked"] += 1
        yaml_bonus = providers[pid].get("bonus", {})
        s = scraped[pid]

        diffs = []
        suspicious = False

        # Skip wagering diffs >25x — likely casino bonus contamination from scrapers
        if yaml_bonus.get("wagering_multiplier") != s.wagering_multiplier and s.wagering_multiplier > 0:
            if s.wagering_multiplier > 25:
                suspicious = True
            else:
                diffs.append({
                    "field": "wagering_multiplier",
                    "yaml": yaml_bonus.get("wagering_multiplier"),
                    "scraped": s.wagering_multiplier,
                })

        if yaml_bonus.get("type") != s.bonus_type and s.bonus_type != "unknown":
            diffs.append({
                "field": "type",
                "yaml": yaml_bonus.get("type"),
                "scraped": s.bonus_type,
            })
        if yaml_bonus.get("amount") != s.amount and s.amount > 0:
            diffs.append({
                "field": "amount",
                "yaml": yaml_bonus.get("amount"),
                "scraped": s.amount,
            })
        if yaml_bonus.get("min_odds") != s.min_odds and s.min_odds > 0:
            diffs.append({
                "field": "min_odds",
                "yaml": yaml_bonus.get("min_odds"),
                "scraped": s.min_odds,
            })

        status = "match" if not diffs else "mismatch"

        if diffs:
            results["mismatches"] += 1
            results["changes"].append({
                "provider_id": pid,
                "diffs": diffs,
                "sources": s.sources,
            })
        else:
            results["matches"] += 1

        results["provider_status"][pid] = {
            "status": status,
            "yaml_bonus": {
                "type": yaml_bonus.get("type"),
                "amount": yaml_bonus.get("amount"),
                "wagering_multiplier": yaml_bonus.get("wagering_multiplier"),
                "min_odds": yaml_bonus.get("min_odds"),
            },
            "scraped_bonus": {
                "type": s.bonus_type,
                "amount": s.amount,
                "wagering_multiplier": s.wagering_multiplier,
                "min_odds": s.min_odds,
            } if s.amount > 0 else None,
            "sources": s.sources,
            "diffs": diffs,
        }

    return results


def save_bonus_validation(report: dict, path: Path | None = None) -> Path:
    """Save bonus validation report to JSON."""
    import json
    if path is None:
        path = DATA_DIR / "bonus_validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return path


def load_bonus_validation(path: Path | None = None) -> dict | None:
    """Load last bonus validation report from JSON."""
    import json
    if path is None:
        path = DATA_DIR / "bonus_validation.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============ Main ============

def main():
    parser = argparse.ArgumentParser(
        description="Scrape bonus data from affiliate sites and update providers.yaml"
    )
    parser.add_argument("--apply", action="store_true", help="Apply changes to providers.yaml")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Show changes without applying (default)")
    parser.add_argument("--fallback-only", action="store_true", help="Use hardcoded data only (no scraping)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = Path(__file__).parent.parent / "src" / "config" / "providers.yaml"

    if not config_path.exists():
        logger.error(f"providers.yaml not found at {config_path}")
        sys.exit(1)

    if args.fallback_only:
        logger.info("Using hardcoded baseline data (no scraping)")
        aggregated = get_fallback_bonuses()
    else:
        # Scrape all sources
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
        })

        all_bonuses = []
        scrapers = [
            ("speltips.se", scrape_speltips),
            ("rekatochklart.com", scrape_rekatochklart),
            ("bettingstugan.se", scrape_bettingstugan),
            ("betting.se", scrape_betting_se),
            ("tvmatchen.nu", scrape_tvmatchen),
        ]

        for name, scraper_fn in scrapers:
            logger.info(f"Scraping {name}...")
            bonuses = scraper_fn(session)
            all_bonuses.extend(bonuses)
            logger.info(f"  -> {len(bonuses)} providers found")

        if all_bonuses:
            aggregated = aggregate_bonuses(all_bonuses)
            logger.info(f"\nAggregated data for {len(aggregated)} providers from {len(all_bonuses)} entries")
        else:
            logger.warning("No data scraped from any source. Using hardcoded fallback.")
            aggregated = get_fallback_bonuses()

    # Show consolidated data
    print("\n" + "=" * 80)
    print("BONUS DATA SUMMARY")
    print("=" * 80)
    print(f"{'Provider':<15} {'Type':<15} {'Amount':>8} {'Wager':>6} {'MinOdds':>8} {'Sources'}")
    print("-" * 80)

    for pid in sorted(aggregated.keys()):
        b = aggregated[pid]
        sources_str = ", ".join(b.sources[:3])
        if len(b.sources) > 3:
            sources_str += f" +{len(b.sources)-3}"
        print(
            f"{pid:<15} {b.bonus_type:<15} {b.amount:>6} kr "
            f"{b.wagering_multiplier:>4.0f}x  {b.min_odds:>6.2f}  {sources_str}"
        )

    # Compare with current config and show diff
    apply = args.apply
    changes = update_providers_yaml(aggregated, config_path, apply=apply)

    if changes:
        print(f"\n{'APPLIED' if apply else 'PENDING'} CHANGES:")
        for change in changes:
            print(change)
        if not apply:
            print(f"\nRun with --apply to write changes to {config_path}")
    else:
        print("\nNo changes needed - providers.yaml is up to date!")


if __name__ == "__main__":
    main()
