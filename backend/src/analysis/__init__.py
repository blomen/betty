# Analysis package - arb, value, bonus detection
from .arbitrage import find_arbitrage, scan_for_arbitrage, ArbitrageOpportunity
from .value import find_value, find_best_value, scan_for_value, get_fair_odds, ValueBet
from .bonus import find_best_hedge, calculate_free_bet_value, BonusMatch
from .devig import (
    calculate_margin,
    devig_multiplicative,
    devig_additive,
    devig_power,
    get_fair_odds_for_outcome,
)
from .scanner import OpportunityScanner, BonusOpportunity
