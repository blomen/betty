"""Extract features for M4 LLM Boost Calibrator.

Calibrates the LLM's probability output based on historical accuracy patterns.
The LLM does all research — this model only adjusts the probability.
"""

SPORT_ENCODING = {
    "football": 0, "basketball": 1, "tennis": 2, "ice_hockey": 3,
    "american_football": 4, "baseball": 5, "mma": 6, "esports": 7,
    "handball": 8, "volleyball": 9,
}


def extract_boost_features(
    llm_raw_probability: float, llm_confidence: int,
    boost_type: str, sport: str, league: str,
    num_legs: int, has_pinnacle_match: bool,
    pinnacle_implied_prob: float | None,
    original_odds: float, boosted_odds: float,
    provider: str, hours_to_event: float = 0.0,
    llm_reasoning_length: int = 0,
    brave_results_count: int = 0,
    legs_matched_ratio: float = 0.0,
    day_of_week: int = 0,
) -> dict:
    boost_margin = (boosted_odds - original_odds) / original_odds if original_odds > 0 else 0

    keyword_anytime_scorer = 1 if "anytime" in (boost_type or "").lower() else 0
    keyword_both_teams = 1 if "both teams" in (boost_type or "").lower() else 0
    keyword_over = 1 if "over" in (boost_type or "").lower() else 0

    return {
        "llm_raw_probability": llm_raw_probability,
        "llm_confidence": llm_confidence,
        "boost_type_single": 1 if boost_type == "single" else 0,
        "boost_type_combo": 1 if "combo" in boost_type or "leg" in boost_type else 0,
        "sport": SPORT_ENCODING.get(sport, len(SPORT_ENCODING)),
        "num_legs": num_legs,
        "has_pinnacle_match": int(has_pinnacle_match),
        "pinnacle_implied_prob": pinnacle_implied_prob or 0.0,
        "legs_matched_ratio": legs_matched_ratio,
        "original_odds": original_odds,
        "boosted_odds": boosted_odds,
        "boost_margin": boost_margin,
        "hours_to_event": hours_to_event,
        "llm_reasoning_length": llm_reasoning_length,
        "brave_results_count": brave_results_count,
        "keyword_anytime_scorer": keyword_anytime_scorer,
        "keyword_both_teams": keyword_both_teams,
        "keyword_over": keyword_over,
        "day_of_week": day_of_week,
    }
