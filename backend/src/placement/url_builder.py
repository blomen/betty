"""
URL Builder — constructs match/deposit page URLs for each sportsbook platform.

Given a provider_id and optional provider_meta, returns the URL that opens
the provider's website in the user's browser. Uses named browser windows
per provider so the frontend can reuse existing sessions.
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

# Altenar brand → direct event URL
# dbet uses hash routing (/sports/#/event/ID), others use path routing (/sportsbook/event/ID)
ALTENAR_EVENT_URLS: dict[str, str] = {
    "dbet": "https://www.dbet.com/sports/#/event",
    "betinia": "https://www.betinia.se/sportsbook/event",
    "campobet": "https://www.campobet.se/sportsbook/event",
    "swiper": "https://www.swiper.se/sportsbook/event",
    "lodur": "https://www.lodur.se/sportsbook/event",
    "quickcasino": "https://www.quickcasino.se/sportsbook/event",
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
    "dbet": "https://www.dbet.com/sports/#/overview/",
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
    # Prediction markets
    "polymarket": "https://polymarket.com/sports",
}

# Deposit/cashier pages — for bankroll management
PROVIDER_DEPOSIT_URLS: dict[str, str] = {
    # Kambi
    "unibet": "https://www.unibet.se/myaccount/cashier",
    "leovegas": "https://www.leovegas.com/sv-se/my-account/deposit",
    "expekt": "https://www.expekt.se/myaccount/cashier",
    "betmgm": "https://www.betmgm.se/myaccount/cashier",
    "speedybet": "https://www.speedybet.com/sv/myaccount/cashier",
    "x3000": "https://www.x3000.se/myaccount/cashier",
    "goldenbull": "https://www.goldenbull.se/myaccount/cashier",
    "1x2": "https://www.1x2.se/myaccount/cashier",
    # Altenar
    "betinia": "https://www.betinia.se/account/deposit",
    "campobet": "https://www.campobet.se/account/deposit",
    "swiper": "https://www.swiper.se/account/deposit",
    "lodur": "https://www.lodur.se/account/deposit",
    "dbet": "https://www.dbet.com/cashier",
    "quickcasino": "https://www.quickcasino.se/account/deposit",
    # Spectate
    "888sport": "https://www.888sport.se/cashier",
    "mrgreen": "https://www.mrgreen.se/insattning",
    # Gecko V2
    "betsson": "https://www.betsson.com/sv/konto/insattning",
    "nordicbet": "https://www.nordicbet.com/sv/konto/insattning",
    "bethard": "https://www.bethard.com/sv/account/deposit",
    "spelklubben": "https://www.spelklubben.se/account/deposit",
    # ComeOn Group
    "comeon": "https://www.comeon.com/sv/cashier/deposit",
    "hajper": "https://www.hajper.com/sv/cashier/deposit",
    "lyllo": "https://www.lyllocasino.com/sv/cashier/deposit",
    # Standalone
    "vbet": "https://www.vbet.se/sv/account/deposit",
    "interwetten": "https://www.interwetten.se/sv/account/deposit",
    "10bet": "https://www.10bet.se/account/deposit",
    "snabbare": "https://www.snabbare.com/sv/konto/insattning",
    "coolbet": "https://www.coolbet.com/sv/konto/insattning",
    "tipwin": "https://www.tipwin.se/sv/account/deposit",
    # Sharp
    "pinnacle": "https://www.pinnacle.com/en/funds/deposit",
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
        eid = provider_meta.get("event_id")
        if eid:
            # Kambi deep links
            if platform == "kambi":
                base = KAMBI_EVENT_URLS.get(provider_id)
                if base:
                    return f"{base}/{eid}"

            # Altenar deep links
            if platform == "altenar":
                base = ALTENAR_EVENT_URLS.get(provider_id)
                if base:
                    return f"{base}/{eid}"

    # Everything else: just open the site
    return PROVIDER_LANDING_URLS.get(provider_id)


async def build_deposit_url(provider_id: str) -> str | None:
    """Build a URL for the provider's deposit/cashier page."""
    return PROVIDER_DEPOSIT_URLS.get(provider_id)
