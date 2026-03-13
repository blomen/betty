"""Extract features for M3 Devig Method Selector.

Determines which devigging method (multiplicative, additive, power)
produces the most accurate fair odds for a given market context.
"""

SPORT_ENCODING = {
    "football": 0, "basketball": 1, "tennis": 2, "ice_hockey": 3,
    "american_football": 4, "baseball": 5, "mma": 6, "esports": 7,
    "handball": 8, "volleyball": 9, "boxing": 10, "rugby": 11,
    "cricket": 12, "darts": 13, "table_tennis": 14, "curling": 15,
}

MARKET_ENCODING = {"1x2": 0, "moneyline": 1, "spread": 2, "total": 3}


def extract_devig_features(
    sport: str, market: str, num_outcomes: int,
    pinnacle_overround: float, favourite_odds: float,
    odds_range: float, league: str = "",
    market_age_hours: float = 0.0,
) -> dict:
    is_top_league = 1 if league and any(
        t in league.lower() for t in [
            "premier", "la_liga", "bundesliga", "serie_a", "ligue_1",
            "nba", "nfl", "mlb", "nhl", "champions", "europa",
        ]
    ) else 0

    return {
        "sport": SPORT_ENCODING.get(sport, len(SPORT_ENCODING)),
        "market_type": MARKET_ENCODING.get(market, len(MARKET_ENCODING)),
        "num_outcomes": num_outcomes,
        "pinnacle_overround": pinnacle_overround,
        "favourite_odds": favourite_odds,
        "odds_range": odds_range,
        "league_tier": is_top_league,
        "market_age_hours": market_age_hours,
        "has_draw_option": 1 if num_outcomes == 3 else 0,
    }
