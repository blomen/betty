"""
URL Builder — constructs match page URLs for each sportsbook platform.

Given a provider_id and provider_meta (from Odds table), returns the URL
that opens the provider's website in the user's browser. The user navigates
to the specific bet manually.
"""

from ..constants import PLATFORM_MAP

# Kambi brand → direct event URL (SPA hash routing — these actually work)
KAMBI_EVENT_URLS: dict[str, str] = {
    "unibet": "https://www.unibet.se/betting/sports/event",
    "leovegas": "https://www.leovegas.com/sv-se/betting/event",
    "expekt": "https://www.expekt.se/betting/event",
    "betmgm": "https://www.betmgm.se/betting/event",
    "speedybet": "https://www.speedybet.com/sv/betting/event",
    "x3000": "https://www.x3000.se/betting/event",
    "goldenbull": "https://www.goldenbull.se/betting/event",
    "1x2": "https://www.1x2.se/betting/event",
}

# Provider landing pages — open the site so user can search manually
PROVIDER_LANDING_URLS: dict[str, str] = {
    # Kambi
    "unibet": "https://www.unibet.se/betting/sports",
    "leovegas": "https://www.leovegas.com/sv-se/betting",
    "expekt": "https://www.expekt.se/betting",
    "betmgm": "https://www.betmgm.se/betting",
    "speedybet": "https://www.speedybet.com/sv/betting",
    "x3000": "https://www.x3000.se/betting",
    "goldenbull": "https://www.goldenbull.se/betting",
    "1x2": "https://www.1x2.se/betting",
    # Altenar
    "betinia": "https://www.betinia.se/sportsbook",
    "campobet": "https://www.campobet.se/sportsbook",
    "swiper": "https://www.swiper.se/sportsbook",
    "lodur": "https://www.lodur.se/sportsbook",
    "dbet": "https://www.dbet.com/sportsbook",
    "quickcasino": "https://www.quickcasino.se/sportsbook",
    # Spectate
    "888sport": "https://www.888sport.se/betting",
    "mrgreen": "https://www.mrgreen.se/sport",
    # Gecko V2
    "betsson": "https://www.betsson.com/sv/odds",
    "nordicbet": "https://www.nordicbet.com/sv/odds",
    "bethard": "https://www.bethard.com/sv/sport",
    "spelklubben": "https://www.spelklubben.se/sport",
    # ComeOn Group
    "comeon": "https://www.comeon.com/sv/sportsbook",
    "hajper": "https://www.hajper.com/sv/odds",
    "lyllo": "https://www.lyllocasino.com/sv/odds",
    # Standalone
    "vbet": "https://www.vbet.se/sv/sports",
    "interwetten": "https://www.interwetten.se/sv/sportsbook",
    "10bet": "https://www.10bet.se/sports",
    "snabbare": "https://www.snabbare.com/sv/sport",
    "coolbet": "https://www.coolbet.com/sv/odds",
    "tipwin": "https://www.tipwin.se/sv/sports",
    # Sharp
    "pinnacle": "https://www.pinnacle.com/en/sports",
}


async def build_match_url(
    provider_id: str,
    provider_meta: dict | None = None,
    home_team: str = "",
    away_team: str = "",
    event_id: str = "",
) -> str | None:
    """
    Build a URL for a provider. Returns the most specific URL we can:
    1. Direct event page (Kambi — these reliably deep-link)
    2. Provider landing page (everything else — user searches manually)
    """
    platform = PLATFORM_MAP.get(provider_id)

    # Kambi deep links actually work reliably
    if platform == "kambi" and provider_meta:
        eid = provider_meta.get("event_id")
        base = KAMBI_EVENT_URLS.get(provider_id)
        if eid and base:
            return f"{base}/{eid}"

    # Everything else: just open the site
    return PROVIDER_LANDING_URLS.get(provider_id)
