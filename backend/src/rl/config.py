"""RL agent configuration — all hyperparameters and constants in one place."""

from enum import Enum


class LevelType(str, Enum):
    """All level types the agent can encounter (31 total)."""

    # Volume profile — daily
    DAILY_POC = "daily_poc"
    DAILY_VAH = "daily_vah"
    DAILY_VAL = "daily_val"
    # Volume profile — weekly
    WEEKLY_POC = "weekly_poc"
    WEEKLY_VAH = "weekly_vah"
    WEEKLY_VAL = "weekly_val"
    # Volume profile — monthly
    MONTHLY_POC = "monthly_poc"
    MONTHLY_VAH = "monthly_vah"
    MONTHLY_VAL = "monthly_val"
    # VWAP bands
    VWAP = "vwap"
    VWAP_SD1 = "vwap_sd1"
    VWAP_SD2 = "vwap_sd2"
    VWAP_SD3 = "vwap_sd3"
    # Session levels
    PDH = "pdh"
    PDL = "pdl"
    TOKYO_HIGH = "tokyo_high"
    TOKYO_LOW = "tokyo_low"
    NYIB_HIGH = "nyib_high"
    NYIB_LOW = "nyib_low"
    # TPO levels
    TPOC = "tpoc"
    TVAH = "tvah"
    TVAL = "tval"
    TIBH = "tibh"
    TIBL = "tibl"
    # Structure
    NAKED_POC = "naked_poc"
    # Swing levels (daily/weekly/monthly)
    DAILY_SWING_HIGH = "daily_swing_high"
    DAILY_SWING_LOW = "daily_swing_low"
    WEEKLY_SWING_HIGH = "weekly_swing_high"
    WEEKLY_SWING_LOW = "weekly_swing_low"
    MONTHLY_SWING_HIGH = "monthly_swing_high"
    MONTHLY_SWING_LOW = "monthly_swing_low"
    # ICT / SMC price-delivery signals. Fed into zone clustering as
    # additional members so we have a single consolidated zone surface
    # instead of maintaining FVG/OB as separate overlays.
    FVG_BULL = "fvg_bull"
    FVG_BEAR = "fvg_bear"
    ORDER_BLOCK_BULL = "order_block_bull"
    ORDER_BLOCK_BEAR = "order_block_bear"


class Action(int, Enum):
    """Agent actions — AMT semantics relative to approach direction."""

    CONTINUATION = 0  # Trade in approach direction (momentum through level)
    REVERSAL = 1  # Trade against approach direction (bounce off level)
    SKIP = 2  # Don't trade


# --- Risk Parameters ---
STOP_TICKS = 10  # Used for cost normalisation (R-multiple denominator)
TICK_SIZE = 0.25

# --- DQN Hyperparameters ---
BATCH_SIZE = 4096
LEARNING_RATE = 3e-4
REPLAY_BUFFER_SIZE = 2_000_000
EPSILON_START = 1.0
EPSILON_END = 0.05
EPSILON_DECAY_STEPS = 5_000
GAMMA = 0.9  # sequential RL — learn hold/trail/exit decisions with temporal discounting
# (was 0.0 which made it a contextual bandit; 0.9 enables multi-step value propagation)

# --- Reward Preprocessing ---
# Aligned with live-stop-aware reward range in episode_builder (-1R live stop,
# +6R max trail). Training sees the same distribution as CV eval and live.
REWARD_CLIP_MIN = -1.0
REWARD_CLIP_MAX = 6.0
REWARD_NORMALIZE = True  # Standardize rewards to mean=0, std=1 before training

# --- Target Network (Polyak soft update) ---
TAU = 0.005  # Soft update coefficient: θ_target ← τ·θ_online + (1-τ)·θ_target
TARGET_NET_UPDATE_FREQ = 1  # Apply soft update every train step

# --- Network Architecture ---
HIDDEN_LAYERS = [128, 128, 64]
NUM_ACTIONS = 3
OBSERVATION_DIM = None  # Computed dynamically in observation.py

# --- Level Touch Detection ---
AT_LEVEL_TICKS = 5

# --- Zone Consolidation ---
ATR_FRACTION = 0.08  # zone radius as fraction of session ATR
ATR_PERIOD = 14  # ATR lookback (30m candles)
MIN_ZONE_RADIUS_TICKS = 4  # floor: never merge tighter than 1 point
MAX_ZONE_RADIUS_TICKS = 20  # cap: never merge wider than 5 points

# --- Reward (velocity-based, computed in episode_builder) ---
# No fixed target/stop/timeout — rewards are continuous movement quality scores

# --- Trading Costs (round-trip per trade, in ticks) ---
# Calibrated 2026-04-23 against real TopstepX fills: fees=$1.40 + commissions=$0.50
# per side = $1.90/side = 0.38 ticks/side at $5/tick. Slippage kept at 0.5 tick/side
# as a conservative estimate (NQ is liquid but not perfectly). RT = $8.80, 1.76 ticks.
SLIPPAGE_TICKS = 0.5  # 0.5 tick each side — conservative for NQ RTH
COMMISSION_TICKS = 0.38  # $1.90/side (was 0.5 = $2.50/side; overestimated by $1.20/RT)
COST_PER_TRADE_TICKS = (SLIPPAGE_TICKS + COMMISSION_TICKS) * 2  # 1.76 ticks RT = $8.80

# --- Data ---
DATABENTO_DATASET = "GLBX.MDP3"
SYMBOL = "NQ.FUT"

# --- V5 Hierarchical Architecture ---
NARRATIVE_UPDATE_INTERVAL_S = 1800  # 30 minutes
NARRATIVE_STRUCTURAL_TRIGGERS = [
    "ib_close",
    "new_swing_high",
    "new_swing_low",
    "value_area_breach",
    "single_print_created",
]
