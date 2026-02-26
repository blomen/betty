"""
Combo Decomposition for Odds Boosts

Decomposes multi-leg combo boosts into individual legs, prices each leg
against Pinnacle data where available, and computes combo fair odds
using independence assumption + correlation adjustments.

Much more accurate than flat margin estimation for combos like:
- "1x2 & Båda lagen gör mål: Draw & Yes" → price 1x2 + BTTS separately
- "1x2 & Totalt antal mål: Home & Över 2.5" → price 1x2 + total separately
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from .devig import get_fair_odds_for_outcome, devig_multiplicative

logger = logging.getLogger(__name__)


# ── Boost type classification ──────────────────────────────────────────

def classify_boost(market_label: str, title: str) -> str:
    """Classify a boost into a type for routing to the right enrichment path.

    Returns one of:
        pure_1x2, pure_total, pure_spread,
        combo_1x2_btts, combo_1x2_total, combo_htft, combo_multi,
        prop, unknown
    """
    ml = market_label.lower().strip()
    tl = title.lower().strip()
    combined = f"{ml} {tl}"

    # Pure single-market types (no combo indicators)
    has_combo = " & " in ml or ", " in ml

    if not has_combo:
        # Pure 1x2 / match winner
        if any(kw in ml for kw in ("1x2", "match result", "matchresultat",
                                     "vinnare", "winner", "att vinna", "to win")):
            return "pure_1x2"

        # Pure total (over/under)
        if any(kw in ml for kw in ("over/under", "över/under", "totalt antal mål",
                                     "total goals")):
            return "pure_total"

        # Pure spread
        if any(kw in ml for kw in ("handicap", "handikapp", "spread")):
            return "pure_spread"

        # Goalscorer / player prop
        if any(kw in combined for kw in ("målgörare", "goalscorer", "first goal",
                                           "första mål", "gör mål", "to score")):
            return "prop"

        # Correct score
        if any(kw in combined for kw in ("rätt resultat", "correct score")):
            return "prop"

        # Clean sheet, cards, corners, shots
        if any(kw in combined for kw in ("nollan", "clean sheet", "kort", "card",
                                           "hörna", "corner", "skott", "shot")):
            return "prop"

    # Combo types
    if has_combo:
        # 1x2 + BTTS
        if ("1x2" in ml or "matchresultat" in ml) and any(
            kw in ml for kw in ("båda lagen", "both teams", "btts")
        ):
            return "combo_1x2_btts"

        # 1x2 + total
        if ("1x2" in ml or "matchresultat" in ml) and any(
            kw in ml for kw in ("totalt", "total", "antal mål", "mål")
        ):
            return "combo_1x2_total"

        # HT/FT (halvtid/fulltid)
        if any(kw in ml for kw in ("halvtid/fulltid", "halftime/fulltime", "ht/ft",
                                     "halvtid/slutställning", "spelförlopp")):
            return "combo_htft"

        # HT/FT + total or other multi-leg
        if any(kw in ml for kw in ("halvtid", "halftime")):
            return "combo_htft"

        # Generic multi-leg combo
        return "combo_multi"

    # Remaining single-market types that are props
    if any(kw in combined for kw in ("halvtid", "halftime", "period",
                                       "tidpunkt", "time of",
                                       "båda halvlekarna", "both halves",
                                       "vinner en av", "win half")):
        return "prop"

    return "unknown"


# Types that can be decomposed into individually-priceable legs
DECOMPOSABLE_TYPES = {
    "combo_1x2_btts", "combo_1x2_total", "combo_htft", "combo_multi"
}


# ── Combo leg parsing ──────────────────────────────────────────────────

@dataclass
class ComboLeg:
    """A single leg of a multi-leg combo boost."""
    market_type: str          # "1x2", "btts", "total", "spread", "htft", "unknown"
    selection: str            # "home", "away", "draw", "yes", "no", "over", "under"
    point: Optional[float]    # For totals/spreads: the line value (e.g., 2.5)
    team: Optional[str]       # Team name if selection is team-specific


# Selection mapping: Swedish/English → normalized
_SELECTION_MAP = {
    # 1x2 outcomes
    "oavgjort": "draw", "draw": "draw", "x": "draw",
    "ja": "yes", "yes": "yes",
    "nej": "no", "no": "no",
    # Over/under
    "över": "over", "over": "over",
    "under": "under",
}

# Point pattern: "över 2,5" or "over 2.5" or "under 3,5"
_POINT_PATTERN = re.compile(r'(?:över|over|under)\s*(\d+[,.]?\d*)', re.IGNORECASE)


def _classify_leg_market(leg_text: str) -> str:
    """Classify a single leg's market type from its label text."""
    lt = leg_text.lower().strip()

    if any(kw in lt for kw in ("1x2", "matchresultat", "match result",
                                 "vinnare", "winner")):
        return "1x2"
    if any(kw in lt for kw in ("båda lagen", "both teams", "btts")):
        return "btts"
    if any(kw in lt for kw in ("totalt antal", "total goals", "antal mål",
                                 "over/under", "över/under")):
        return "total"
    if any(kw in lt for kw in ("handikapp", "handicap", "spread")):
        return "spread"
    if any(kw in lt for kw in ("halvtid", "halftime", "ht/ft")):
        return "htft"
    return "unknown"


def _parse_selection(sel_text: str, home_team: str = "", away_team: str = "") -> tuple[str, Optional[float]]:
    """Parse a selection text into (selection, point).

    Returns:
        (selection_name, point_value) where selection is normalized.
    """
    st = sel_text.strip().lower()

    # Check direct mapping first
    for key, val in _SELECTION_MAP.items():
        if st == key:
            return val, None

    # Check for point value (totals)
    point_match = _POINT_PATTERN.search(st)
    point = None
    if point_match:
        point = float(point_match.group(1).replace(",", "."))
        if "under" in st:
            return "under", point
        return "over", point

    # Team name matching
    if home_team and home_team.lower() in st:
        return "home", None
    if away_team and away_team.lower() in st:
        return "away", None

    # W1/W2 format
    if st in ("w1", "1"):
        return "home", None
    if st in ("w2", "2"):
        return "away", None

    return st, None


def parse_combo_legs(market_label: str, title: str, event: str = "") -> list[ComboLeg]:
    """Decompose a combo boost into individual legs.

    Handles two main formats:
    1. Altenar/Gecko: market_label="1x2 & båda lagen gör mål", title="...Oavgjort & ja"
    2. Interwetten: market_label with comma-separated legs
    """
    ml = market_label.strip()
    tl = title.strip()

    # Extract team names from event
    home_team = away_team = ""
    for sep in [" vs ", " - ", " v "]:
        if sep in event:
            parts = event.split(sep, 1)
            home_team = parts[0].strip()
            away_team = parts[1].strip()
            break

    legs: list[ComboLeg] = []

    # Try " & " splitting first (most common: Altenar/Gecko combos)
    if " & " in ml:
        market_parts = [p.strip() for p in ml.split(" & ")]

        # Extract selection part from title (after the last ":")
        # Title format: "1x2 & båda lagen gör mål: Oavgjort & ja"
        sel_text = tl
        colon_idx = tl.rfind(":")
        if colon_idx >= 0:
            sel_text = tl[colon_idx + 1:].strip()

        # Split selections by " & "
        sel_parts = [p.strip() for p in sel_text.split(" & ")]

        # Match market parts to selection parts
        for i, mkt_part in enumerate(market_parts):
            leg_market = _classify_leg_market(mkt_part)
            sel = sel_parts[i] if i < len(sel_parts) else ""
            selection, point = _parse_selection(sel, home_team, away_team)

            legs.append(ComboLeg(
                market_type=leg_market,
                selection=selection,
                point=point,
                team=home_team if selection == "home" else (
                    away_team if selection == "away" else None
                ),
            ))

    # Try comma splitting (Interwetten, some Altenar)
    elif ", " in ml:
        market_parts = [p.strip() for p in ml.split(", ")]
        sel_text = tl
        colon_idx = tl.rfind(":")
        if colon_idx >= 0:
            sel_text = tl[colon_idx + 1:].strip()

        sel_parts = [p.strip() for p in sel_text.split(", ")]

        for i, mkt_part in enumerate(market_parts):
            leg_market = _classify_leg_market(mkt_part)
            sel = sel_parts[i] if i < len(sel_parts) else ""
            selection, point = _parse_selection(sel, home_team, away_team)

            legs.append(ComboLeg(
                market_type=leg_market,
                selection=selection,
                point=point,
                team=home_team if selection == "home" else (
                    away_team if selection == "away" else None
                ),
            ))

    # HT/FT special format: "Halvtid/fulltid: Team1/Team2"
    elif any(kw in ml.lower() for kw in ("halvtid/fulltid", "ht/ft")):
        sel_text = tl
        colon_idx = tl.rfind(":")
        if colon_idx >= 0:
            sel_text = tl[colon_idx + 1:].strip()

        # HT/FT selections often in "X/Y" format
        if "/" in sel_text:
            ht_sel, ft_sel = sel_text.split("/", 1)
            ht_selection, _ = _parse_selection(ht_sel.strip(), home_team, away_team)
            ft_selection, _ = _parse_selection(ft_sel.strip(), home_team, away_team)

            legs.append(ComboLeg(market_type="1x2_1h", selection=ht_selection,
                                 point=None, team=None))
            legs.append(ComboLeg(market_type="1x2", selection=ft_selection,
                                 point=None, team=None))

    return legs


# ── Correlation factors ────────────────────────────────────────────────

# Adjustments for known correlated combo types.
# Key format: (leg1_market_selection, leg2_market_selection)
# Value: multiplicative factor on combined probability.
CORRELATION_FACTORS: dict[tuple[str, str], float] = {
    # 1x2 + BTTS
    ("1x2_draw", "btts_yes"): 0.85,     # Scoring draws slightly less likely
    ("1x2_draw", "btts_no"): 1.20,      # 0-0 draws more likely than independent
    ("1x2_home", "btts_yes"): 1.05,     # Home win + both score: slight positive
    ("1x2_home", "btts_no"): 0.95,      # Home clean sheet: slight negative
    ("1x2_away", "btts_yes"): 1.05,
    ("1x2_away", "btts_no"): 0.95,
    # 1x2 + Total
    ("1x2_home", "total_over"): 1.08,   # Home wins tend to have more goals
    ("1x2_home", "total_under"): 0.92,
    ("1x2_away", "total_over"): 1.05,
    ("1x2_away", "total_under"): 0.95,
    ("1x2_draw", "total_over"): 0.90,   # Draws tend to be lower scoring
    ("1x2_draw", "total_under"): 1.10,
    # HT/FT (same team both halves vs different)
    ("1x2_1h_home", "1x2_home"): 1.30,  # Same team winning both halves
    ("1x2_1h_away", "1x2_away"): 1.30,
    ("1x2_1h_draw", "1x2_draw"): 1.15,
    ("1x2_1h_home", "1x2_away"): 0.70,  # Comeback: rare
    ("1x2_1h_away", "1x2_home"): 0.70,
    ("1x2_1h_draw", "1x2_home"): 0.90,
    ("1x2_1h_draw", "1x2_away"): 0.90,
    ("1x2_1h_home", "1x2_draw"): 0.80,
    ("1x2_1h_away", "1x2_draw"): 0.80,
}

DEFAULT_CORRELATION = 1.0  # Unknown combos: assume independence


def _get_correlation(legs: list[ComboLeg]) -> float:
    """Get the correlation adjustment factor for a set of combo legs."""
    if len(legs) != 2:
        # For 3+ legs, use independence (correlation compounds unpredictably)
        return DEFAULT_CORRELATION

    key1 = f"{legs[0].market_type}_{legs[0].selection}"
    key2 = f"{legs[1].market_type}_{legs[1].selection}"

    # Try both orderings
    factor = CORRELATION_FACTORS.get((key1, key2))
    if factor is None:
        factor = CORRELATION_FACTORS.get((key2, key1))
    if factor is None:
        factor = DEFAULT_CORRELATION

    return factor


# ── Combo pricing ──────────────────────────────────────────────────────

def price_combo_legs(
    legs: list[ComboLeg],
    pinnacle_data: dict,
    margin_fallback: bool = True,
) -> tuple[Optional[float], str]:
    """Price a combo by looking up each leg in Pinnacle data.

    Args:
        legs: Parsed combo legs
        pinnacle_data: {market_type: {outcome_or_point: odds_dict}} for the matched event
        margin_fallback: If True, estimate margin for legs without Pinnacle data

    Returns:
        (fair_odds, method) where method is "combo_full" if all legs priced from
        Pinnacle, "combo_partial" if some used margin estimation, or None if pricing fails.
    """
    if not legs:
        return None, ""

    fair_probs: list[float] = []
    all_from_pinnacle = True

    for leg in legs:
        prob = _price_single_leg(leg, pinnacle_data)
        if prob is not None and 0 < prob < 1:
            fair_probs.append(prob)
        elif margin_fallback:
            # Can't price this leg from Pinnacle — use rough estimate
            # BTTS yes ≈ 55%, no ≈ 45% (average across football)
            estimated = _estimate_leg_probability(leg)
            if estimated is not None:
                fair_probs.append(estimated)
                all_from_pinnacle = False
            else:
                return None, ""
        else:
            return None, ""

    if len(fair_probs) != len(legs):
        return None, ""

    # Multiply probabilities with correlation adjustment
    combined_prob = 1.0
    for p in fair_probs:
        combined_prob *= p

    correlation = _get_correlation(legs)
    combined_prob *= correlation

    # Clamp to valid range
    if combined_prob <= 0 or combined_prob >= 1:
        return None, ""

    fair_odds = round(1.0 / combined_prob, 3)

    # Sanity: fair_odds should be > 1
    if fair_odds <= 1.0:
        return None, ""

    method = "combo_full" if all_from_pinnacle else "combo_partial"
    return fair_odds, method


def _price_single_leg(leg: ComboLeg, pinnacle_data: dict) -> Optional[float]:
    """Price a single leg from Pinnacle data. Returns fair probability or None."""

    mtype = leg.market_type

    # ── 1x2 / moneyline ──
    if mtype in ("1x2", "moneyline"):
        market = pinnacle_data.get("1x2") or pinnacle_data.get("moneyline")
        if not market or len(market) < 2:
            return None
        fair_odds = get_fair_odds_for_outcome(leg.selection, market, method="multiplicative")
        if fair_odds and fair_odds > 1:
            return 1.0 / fair_odds
        return None

    # ── First-half 1x2 ──
    if mtype in ("1x2_1h", "moneyline_1h"):
        market = pinnacle_data.get("1x2_1h") or pinnacle_data.get("moneyline_1h")
        if not market or len(market) < 2:
            return None
        fair_odds = get_fair_odds_for_outcome(leg.selection, market, method="multiplicative")
        if fair_odds and fair_odds > 1:
            return 1.0 / fair_odds
        return None

    # ── Total (over/under) ──
    if mtype == "total" and leg.point is not None:
        totals = pinnacle_data.get("total", {})
        point_market = totals.get(leg.point)
        if not point_market or len(point_market) < 2:
            return None
        fair_odds = get_fair_odds_for_outcome(leg.selection, point_market, method="multiplicative")
        if fair_odds and fair_odds > 1:
            return 1.0 / fair_odds
        return None

    # ── First-half total ──
    if mtype == "total_1h" and leg.point is not None:
        totals = pinnacle_data.get("total_1h", {})
        point_market = totals.get(leg.point)
        if not point_market or len(point_market) < 2:
            return None
        fair_odds = get_fair_odds_for_outcome(leg.selection, point_market, method="multiplicative")
        if fair_odds and fair_odds > 1:
            return 1.0 / fair_odds
        return None

    # ── BTTS (from team_total data) ──
    if mtype == "btts":
        # BTTS "yes" ≈ both teams score at least 1 goal
        # We can estimate from team totals if available, or from match total
        # For now, try to get from total line: if over 2.5 is low odds, BTTS yes is likely
        # This is a rough proxy — not as good as direct BTTS odds
        totals = pinnacle_data.get("total", {})

        # Find main total line (usually 2.5 for football)
        for point in (2.5, 2.0, 3.0, 3.5):
            point_market = totals.get(point)
            if point_market and len(point_market) >= 2:
                # De-vig the total market
                fair_over = get_fair_odds_for_outcome("over", point_market, method="multiplicative")
                if fair_over and fair_over > 1:
                    over_prob = 1.0 / fair_over
                    # BTTS yes ≈ over_prob * 0.85 (rough: most overs have both teams scoring)
                    # BTTS no ≈ 1 - btts_yes
                    if point == 2.5:
                        btts_yes_prob = over_prob * 0.82
                    elif point == 2.0:
                        btts_yes_prob = over_prob * 0.75
                    else:
                        btts_yes_prob = over_prob * 0.70

                    btts_yes_prob = min(btts_yes_prob, 0.85)  # Clamp

                    if leg.selection == "yes":
                        return btts_yes_prob
                    elif leg.selection == "no":
                        return 1.0 - btts_yes_prob
                break

        return None

    # ── Spread ──
    if mtype == "spread" and leg.point is not None:
        spreads = pinnacle_data.get("spread", {})
        point_market = spreads.get(leg.point)
        if not point_market or len(point_market) < 2:
            return None
        fair_odds = get_fair_odds_for_outcome(leg.selection, point_market, method="multiplicative")
        if fair_odds and fair_odds > 1:
            return 1.0 / fair_odds
        return None

    return None


def _estimate_leg_probability(leg: ComboLeg) -> Optional[float]:
    """Rough probability estimate when Pinnacle data is unavailable for a leg.

    These are population averages across European football — better than nothing
    but less accurate than Pinnacle-derived probabilities.
    """
    mtype = leg.market_type
    sel = leg.selection

    # BTTS averages (across top European leagues)
    if mtype == "btts":
        if sel == "yes":
            return 0.52
        elif sel == "no":
            return 0.48

    # Total goals (most common lines)
    if mtype == "total" and leg.point is not None:
        # Average over/under probabilities for common lines
        estimates = {
            (1.5, "over"): 0.72, (1.5, "under"): 0.28,
            (2.5, "over"): 0.52, (2.5, "under"): 0.48,
            (3.5, "over"): 0.32, (3.5, "under"): 0.68,
            (4.5, "over"): 0.16, (4.5, "under"): 0.84,
        }
        return estimates.get((leg.point, sel))

    # Clean sheet (nollan)
    if mtype == "unknown" and sel in ("yes", "no"):
        # Average clean sheet prob ≈ 28%
        return 0.28 if sel == "yes" else 0.72

    return None
