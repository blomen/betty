"""RL agent configuration — all hyperparameters and constants in one place."""

from enum import Enum


class LevelType(str, Enum):
    """All level types the agent can encounter."""
    POC_SESSION = "poc_session"
    POC_DAILY = "poc_daily"
    POC_WEEKLY = "poc_weekly"
    POC_MONTHLY = "poc_monthly"
    POC_MACRO = "poc_macro"
    VAH = "vah"
    VAL = "val"
    VWAP = "vwap"
    VWAP_SD1 = "vwap_sd1"
    VWAP_SD2 = "vwap_sd2"
    VWAP_SD3 = "vwap_sd3"
    IB_HIGH = "ib_high"
    IB_LOW = "ib_low"
    PDH = "pdh"
    PDL = "pdl"
    TOKYO_HL = "tokyo_hl"
    LONDON_HL = "london_hl"
    GLOBEX_HL = "globex_hl"
    OVERNIGHT_HL = "overnight_hl"
    WEEKLY_HL = "weekly_hl"
    MONTHLY_HL = "monthly_hl"
    NAKED_POC = "naked_poc"
    SINGLE_PRINT = "single_print"
    FVG = "fvg"
    ORDER_BLOCK = "order_block"


class Action(int, Enum):
    """Agent actions."""
    LONG = 0
    SHORT = 1
    SKIP = 2


# --- Risk Parameters (Phase 1: fixed) ---
STOP_TICKS = 10
TARGET_TICKS = 20
TIMEOUT_MINUTES = 30
TICK_SIZE = 0.25

# --- DQN Hyperparameters ---
BATCH_SIZE = 64
LEARNING_RATE = 1e-4
REPLAY_BUFFER_SIZE = 100_000
EPSILON_START = 1.0
EPSILON_END = 0.05
EPSILON_DECAY_STEPS = 5000
TARGET_NET_UPDATE_FREQ = 500
GAMMA = 0.0

# --- Network Architecture ---
HIDDEN_LAYERS = [128, 128, 64]
NUM_ACTIONS = 3
OBSERVATION_DIM = None  # Computed dynamically in observation.py

# --- Level Touch Detection ---
AT_LEVEL_TICKS = 5

# --- Reward Values ---
REWARD_TARGET_HIT = 2.0
REWARD_STOP_HIT = -1.0
REWARD_TIMEOUT = 0.0

# --- Data ---
DATABENTO_DATASET = "GLBX.MDP3"
SYMBOL = "NQ.FUT"
