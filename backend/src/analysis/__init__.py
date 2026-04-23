# Analysis package - value, bonus detection
from .bonus import BonusMatch, find_best_hedge
from .devig import (
    calculate_margin,
    devig_multiplicative,
    get_fair_odds_for_outcome,
)
from .scanner import BonusOpportunity, OpportunityScanner
from .value import ValueBet, find_value
