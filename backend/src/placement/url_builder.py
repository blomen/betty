"""
URL Builder — constructs match/deposit/my-bets page URLs for each sportsbook.

Given a provider_id and optional provider_meta, returns the URL that opens
the provider's website in the user's browser. Uses named browser windows
per provider so the frontend can reuse existing sessions.

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

# Altenar brand → direct event URL
# NOTE: campobet removed — redirects to speedybet (different operator)
# NOTE: dbet removed — redirects to spelklubben (Gecko V2)
# NOTE: swiper removed — redirects to betmgm (DEAD)
ALTENAR_EVENT_URLS: dict[str, str] = {
    "betinia": "https://www.betinia.se/sportsbook/event",
    "lodur": "https://www.lodur.se/sportsbook/event",
    "quickcasino": "https://www.quickcasino.se/sportsbook/event",
}

# Provider landing pages — open the site so user can search manually
PROVIDER_LANDING_URLS: dict[str, str] = {
    # Kambi
    "unibet": "https://www.unibet.se/betting/sports",
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

# Deposit/cashier pages — for bankroll management
PROVIDER_DEPOSIT_URLS: dict[str, str] = {
    # Kambi
    "unibet": "https://www.unibet.se/myaccount/cashier",
    "leovegas": "https://www.leovegas.com/sv-se/my-account/deposit",
    "speedybet": "https://www.speedybet.com/sv/betting?flowType=deposit",
    "x3000": "https://www.x3000.com/betting?flowType=deposit",
    "goldenbull": "https://www.goldenbull.se/en/betting?flowType=deposit",
    "1x2": "https://www.1x2.se/en/betting?flowType=deposit",
    # BetMGM (LeoVegas/MGM)
    "betmgm": "https://www.betmgm.se/auth?intent=SIGNUP",
    # Altenar
    "betinia": "https://www.betinia.se/account/deposit",
    "lodur": "https://www.lodur.se/account/deposit",
    "quickcasino": "https://www.quickcasino.se/account/deposit",
    # Spectate
    "888sport": "https://www.888sport.se/cashier",
    "mrgreen": "https://www.mrgreen.se/insattning",
    # Gecko V2
    "betsson": "https://www.betsson.com/sv/konto/insattning",
    "spelklubben": "https://www.spelklubben.se/account/deposit",
    # ComeOn Group
    "comeon": "https://www.comeon.com/sv/cashier/deposit",
    "hajper": "https://www.hajper.com/sv/cashier/deposit",
    "lyllo": "https://www.lyllocasino.com/sv/cashier/deposit",
    "snabbare": "https://www.snabbare.com/sv/konto/insattning",
    # Standalone
    "vbet": "https://www.vbet.se/sv/account/deposit",
    "interwetten": "https://www.interwetten.se/sv/account/deposit",
    "10bet": "https://www.10bet.se/account/deposit",
    "coolbet": "https://www.coolbet.com/sv/konto/insattning",
    "tipwin": "https://www.tipwin.se/sv/account/deposit",
    # Sharp
    "pinnacle": "https://www.pinnacle.com/en/funds/deposit",
    # Prediction markets
    "polymarket": "https://polymarket.com/portfolio",
}

# My Bets / Bet History pages — for settlement verification
PROVIDER_MY_BETS_URLS: dict[str, str] = {
    # Kambi
    "unibet": "https://www.unibet.se/betting/sports/mybets",
    "leovegas": "https://www.leovegas.com/sv-se/betting/mybets",
    "speedybet": "https://www.speedybet.com/sv/betting/mybets",
    "x3000": "https://www.x3000.com/betting/mybets",
    "goldenbull": "https://www.goldenbull.se/en/betting/mybets",
    "1x2": "https://www.1x2.se/en/betting/mybets",
    # BetMGM
    "betmgm": "https://www.betmgm.se/sport",
    # Altenar — account section
    "betinia": "https://www.betinia.se/account/my-bets",
    "lodur": "https://www.lodur.se/account/my-bets",
    "quickcasino": "https://www.quickcasino.se/account/my-bets",
    # Spectate — within iframe
    "888sport": "https://www.888sport.se/my-bets",
    "mrgreen": "https://www.mrgreen.se/sport",
    # Gecko V2 — "Öppna spel" tab in bet slip sidebar
    "betsson": "https://www.betsson.com/sv/odds",
    "spelklubben": "https://www.spelklubben.se/sv/betting",
    # ComeOn Group
    "comeon": "https://www.comeon.com/sv/sportsbook/my-bets",
    "hajper": "https://www.hajper.com/sportsbook/my-bets",
    "lyllo": "https://www.lyllocasino.com/sv/sportsbook/my-bets",
    "snabbare": "https://www.snabbare.com/sportsbook/my-bets",
    # Standalone — my bets typically in sidebar or account menu
    "vbet": "https://www.vbet.se/sv/pre-match",
    "interwetten": "https://www.interwetten.se/sv/sportsbook",
    "10bet": "https://www.10bet.se/sports",
    "coolbet": "https://www.coolbet.com/sv/odds",
    "tipwin": "https://www.tipwin.se/sv/sports",
    # Sharp
    "pinnacle": "https://www.pinnacle.com/en/my-bets",
    # Prediction markets
    "polymarket": "https://polymarket.com/portfolio",
}

# Results/scores pages — for settlement data
PROVIDER_RESULTS_URLS: dict[str, str] = {
    "vbet": "https://www.vbet.se/sv/pre-match",  # "Resultat" tab in sub-nav
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


async def build_my_bets_url(provider_id: str) -> str | None:
    """Build a URL for the provider's my bets / bet history page."""
    return PROVIDER_MY_BETS_URLS.get(provider_id)


async def build_results_url(provider_id: str) -> str | None:
    """Build a URL for the provider's results/scores page (if available)."""
    return PROVIDER_RESULTS_URLS.get(provider_id)
