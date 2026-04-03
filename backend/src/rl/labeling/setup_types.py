"""Setup type taxonomy for AMT-based trade classification."""
from __future__ import annotations

from enum import Enum


class SetupType(str, Enum):
    """The 8 core setups the model learns to recognize."""
    # Rule-based (mechanical definitions)
    FAILED_AUCTION = "failed_auction"
    LOOK_ABOVE_BELOW_FAIL = "look_above_below_fail"
    IB_EXTENSION = "ib_extension"
    GAP_FILL = "gap_fill"
    SINGLE_PRINT_FILL = "single_print_fill"
    # Cluster-derived (softer definitions)
    ROTATION_TO_POC = "rotation_to_poc"
    EXCESS_TEST = "excess_test"
    BALANCE_BREAK = "balance_break"
    # Fallback
    UNKNOWN = "unknown"


# Priority order for conflict resolution (highest first)
SETUP_PRIORITY = [
    SetupType.FAILED_AUCTION,
    SetupType.LOOK_ABOVE_BELOW_FAIL,
    SetupType.IB_EXTENSION,
    SetupType.GAP_FILL,
    SetupType.SINGLE_PRINT_FILL,
    SetupType.ROTATION_TO_POC,
    SetupType.EXCESS_TEST,
    SetupType.BALANCE_BREAK,
]

NUM_SETUP_TYPES = len(SetupType) - 1  # exclude UNKNOWN
