# Analysis package - value, bonus detection
from .value import find_value, ValueBet
from .bonus import find_best_hedge, BonusMatch
from .devig import (
    calculate_margin,
    devig_multiplicative,
    get_fair_odds_for_outcome,
)
from .scanner import OpportunityScanner, BonusOpportunity
