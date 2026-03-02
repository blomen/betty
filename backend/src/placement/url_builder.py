"""
URL Builder — constructs event page and landing URLs for each sportsbook.

Given a provider_id and optional provider_meta, returns the URL that opens
the provider's website in the user's browser. Uses named browser windows
per provider so the frontend can reuse existing sessions.

Deposit/withdraw/transfer/my-bets are always manual — those functions just
open the provider's landing page so the user navigates from there.

Provider migration status (discovered 2026-03-01):
  DEAD/REDIRECTED:
  - expekt.se → redirects to campobet.se (no longer Kambi)
  - campobet.se → redirects to speedybet.com (different operator)
  - nordicbet.com → redirects to campobet.se (no longer Gecko V2)
  - bethard.com → redirects to dbet.com (now Altenar, not Gecko V2)
  - dbet.com → redirects to spelklubben.se (no longer standalone Altenar)
  - swiper.se → redirects to betmgm.se (DEAD — 404 on betmgm)

  PLATFORM CHANGES:
  - betmgm.se → NOT Kambi; LeoVegas/MGM platform, sportsbook at /sport
  - goldenbull.se → PAF platform (login-gated, may not be Kambi)
  - 1x2.se → PAF platform (login-gated, may not be Kambi)
  - lyllo → ComeOn Group proprietary sportsbook (NOT Altenar)

  URL FIXES:
  - x3000.se → redirects to x3000.com
  - hajper.com/sv/odds → sportsbook at /sportsbook
  - 888sport.se/betting → fails; root loads marketing page only
  - lodur.se/sportsbook → correct is /sv/sport
  - quickcasino.se/sportsbook → correct is /sv/sport
  - lyllocasino.com/sv/odds → correct is /sv/sportsbook
  - spelklubben.se/sport → correct is /sv/betting
"""

from ..constants import PLATFORM_MAP

# Provider-specific "My Bets" URLs (verified via browser exploration)
# Kambi brands share the /betting/sports/bethistory path
PROVIDER_MY_BETS_URLS: dict[str, str] = {
    "unibet": "https://www.unibet.se/betting/sports/bethistory",
    "leovegas": "https://www.leovegas.com/sv-se/betting/bethistory",
    "speedybet": "https://www.speedybet.com/sv/betting/bethistory",
    "x3000": "https://www.x3000.com/betting/bethistory",
    "goldenbull": "https://www.goldenbull.se/en/betting/bethistory",
    "1x2": "https://www.1x2.se/en/betting/bethistory",
}

# Kambi brand → direct event URL (SPA hash routing — these actually work)
# NOTE: expekt removed — redirects to campobet (Altenar)
# NOTE: betmgm removed — NOT Kambi, it's LeoVegas/MGM platform
# NOTE: x3000 domain changed .se → .com
KAMBI_EVENT_URLS: dict[str, str] = {
    "unibet": "https://www.unibet.se/betting/sports/event",
    "leovegas": "https://www.leovegas.com/sv-se/betting/event",
    "speedybet": "https://www.speedybet.com/sv/betting/event",
    "x3000": "https://www.x3000.com/betting/event",
    "goldenbull": "https://www.goldenbull.se/en/betting/event",
    "1x2": "https://www.1x2.se/en/betting/event",
}

# Altenar brand → base sportsbook URL (event URL built via sportRoutingParams)
# URL pattern: {base}/sv/sport?sportRoutingParams=page~event__sportId~{s}__categoryIds~{c}__championshipIds~{ch}__eventId~{e}
# NOTE: campobet removed — redirects to speedybet (different operator)
# NOTE: dbet removed — redirects to spelklubben (Gecko V2)
# NOTE: swiper removed — redirects to betmgm (DEAD)
ALTENAR_SPORTSBOOK_URLS: dict[str, str] = {
    "betinia": "https://www.betinia.se/sv/sport",
    "lodur": "https://www.lodur.se/sv/sport",
    "quickcasino": "https://www.quickcasino.se/sv/sport",
}

# Provider landing pages — open the site so user can search manually
PROVIDER_LANDING_URLS: dict[str, str] = {
    # Kambi
    "unibet": "https://www.unibet.se/betting/sports/home",
    "leovegas": "https://www.leovegas.com/sv-se/betting",
    "speedybet": "https://www.speedybet.com/sv/betting",
    "x3000": "https://www.x3000.com/betting",
    "goldenbull": "https://www.goldenbull.se/en/betting",
    "1x2": "https://www.1x2.se/en/betting",
    # BetMGM (NOT Kambi — LeoVegas/MGM platform)
    "betmgm": "https://www.betmgm.se/sport",
    # Altenar
    "betinia": "https://www.betinia.se/sv/sport",
    "lodur": "https://www.lodur.se/sv/sport",
    "quickcasino": "https://www.quickcasino.se/sv/sport",
    # Spectate (888sport root only — /betting fails)
    "888sport": "https://www.888sport.se",
    "mrgreen": "https://www.mrgreen.se/sport",
    # Gecko V2
    "betsson": "https://www.betsson.com/sv/odds",
    "spelklubben": "https://www.spelklubben.se/sv/betting",
    # ComeOn Group
    "comeon": "https://www.comeon.com/sv/sportsbook",
    "hajper": "https://www.hajper.com/sportsbook",
    "lyllo": "https://www.lyllocasino.com/sv/sportsbook",
    "snabbare": "https://www.snabbare.com/sportsbook",
    # Standalone
    "vbet": "https://www.vbet.se/sv/pre-match",
    "interwetten": "https://www.interwetten.se/sv/sportsbook",
    "10bet": "https://www.10bet.se/sports",
    "coolbet": "https://www.coolbet.com/sv/odds",
    "tipwin": "https://www.tipwin.se/sv/sports",
    # Sharp
    "pinnacle": "https://www.pinnacle.com/en/sports",
    # Prediction markets
    "polymarket": "https://polymarket.com/portfolio",
}

async def build_match_url(
    provider_id: str,
    provider_meta: dict | None = None,
    home_team: str = "",
    away_team: str = "",
    event_id: str = "",
) -> str | None:
    """
    Build a URL for a provider event page. Returns the most specific URL we can:
    1. Direct event page (Kambi/Altenar — deep-link with event_id from provider_meta)
    2. Provider landing page (everything else — user searches manually)
    """
    platform = PLATFORM_MAP.get(provider_id)

    if provider_meta:
        # Polymarket deep links — uses event slug
        # URL pattern: https://polymarket.com/event/{slug}
        if platform == "polymarket":
            event_slug = provider_meta.get("event_slug")
            if event_slug:
                return f"https://polymarket.com/event/{event_slug}"

        eid = provider_meta.get("event_id")
        if eid:
            # Kambi deep links
            if platform == "kambi":
                base = KAMBI_EVENT_URLS.get(provider_id)
                if base:
                    return f"{base}/{eid}"

            # Altenar deep links — uses sportRoutingParams query string
            # Requires: sportId, categoryId, championshipId, eventId
            if platform == "altenar":
                base = ALTENAR_SPORTSBOOK_URLS.get(provider_id)
                if base:
                    sport_id = provider_meta.get("sport_id")
                    category_id = provider_meta.get("category_id")
                    championship_id = provider_meta.get("championship_id")
                    if sport_id and category_id and championship_id:
                        params = (
                            f"page~event"
                            f"__sportId~{sport_id}"
                            f"__categoryIds~{category_id}"
                            f"__championshipIds~{championship_id}"
                            f"__eventId~{eid}"
                        )
                        return f"{base}?sportRoutingParams={params}"
                    # Fallback: just event ID (will need search)
                    return base

    # Everything else: just open the site
    return PROVIDER_LANDING_URLS.get(provider_id)


async def build_deposit_url(provider_id: str) -> str | None:
    """Open provider's landing page — deposit/withdraw is always manual."""
    return PROVIDER_LANDING_URLS.get(provider_id)


async def build_my_bets_url(provider_id: str) -> str | None:
    """Return provider-specific my bets URL, or fallback to landing page."""
    return PROVIDER_MY_BETS_URLS.get(provider_id) or PROVIDER_LANDING_URLS.get(provider_id)


async def build_results_url(provider_id: str) -> str | None:
    """Open provider's landing page — results navigation is manual."""
    return PROVIDER_LANDING_URLS.get(provider_id)
