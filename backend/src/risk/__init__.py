"""
Risk Management Module

Implements risk-aware bankroll management to maximize long-term EV
while staying below bookmaker detection thresholds.

Key components:
- FeatureExtractor: Behavioral feature extraction from bet history
- RiskCalculator: Computes provider risk scores from features
- UtilityRegularizer: Applies risk penalty to expected value
- StochasticSelector: Softmax-based opportunity selection
- StakeNoiseInjector: Adds entropy to stake amounts
"""

from .allocator import AllocationResult, ProviderAllocator
from .calculator import RiskAssessment, RiskCalculator
from .features import BehavioralFeatures, FeatureExtractor
from .regularizer import RegularizedOpportunity, UtilityRegularizer
from .selector import StochasticSelector
from .stake_noise import StakeNoiseInjector

__all__ = [
    "FeatureExtractor",
    "BehavioralFeatures",
    "RiskCalculator",
    "RiskAssessment",
    "UtilityRegularizer",
    "RegularizedOpportunity",
    "StochasticSelector",
    "StakeNoiseInjector",
    "ProviderAllocator",
    "AllocationResult",
]
