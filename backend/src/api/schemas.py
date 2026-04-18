"""Pydantic schemas for API requests and responses."""

from typing import Literal, Optional

from pydantic import BaseModel


# ============ Provider Schemas ============

class ProviderCreate(BaseModel):
    id: str
    name: str
    url: Optional[str] = None
    balance: float = 0.0


class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    is_enabled: Optional[bool] = None
    balance: Optional[float] = None


# ============ Bankroll Schemas ============

class BulkBalanceUpdate(BaseModel):
    balance: float
    provider_ids: Optional[list[str]] = None  # If None, updates all enabled providers


class BalanceSet(BaseModel):
    balance: float  # Absolute balance to set


class DepositRequest(BaseModel):
    amount: float  # Deposit amount (positive)


class AllocateRequest(BaseModel):
    liquid_amount: float  # Cash in bank to allocate across providers


class BonusTransitionRequest(BaseModel):
    action: Literal["start_freebet", "trigger_settled", "freebet_used"]


class StakePreviewRequest(BaseModel):
    """Request to preview stake for an opportunity."""
    edge_pct: float  # Edge percentage (e.g., 5.0 for 5%)
    odds: float  # Decimal odds
    event_id: Optional[str] = None  # For exposure tracking
    provider_id: Optional[str] = None  # For bonus status checking


class RecordBetRequest(BaseModel):
    """Request to record a bet for exposure tracking."""
    event_id: str
    provider_id: str
    stake: float
    odds: float


# ============ Bet Schemas ============

class BetCreate(BaseModel):
    event_id: Optional[str] = None
    provider_id: str
    market: Optional[str] = None
    outcome: Optional[str] = None
    odds: float
    point: Optional[float] = None  # Spread/total line value
    stake: float
    is_bonus: bool = False
    bonus_type: Optional[str] = None
    # Risk management (optional, populated by auto-stake flow)
    utility_score: Optional[float] = None
    selection_probability: Optional[float] = None
    stake_noise_applied: Optional[float] = None
    fair_odds_at_placement: Optional[float] = None  # For boosts: pass LLM fair odds directly
    boost_event: Optional[str] = None  # For boosts: event name at placement (e.g. "Arsenal vs Sunderland")
    boost_title: Optional[str] = None  # For boosts: LLM-simplified title at placement
    bet_type: Optional[str] = None  # "value", "arb", "reverse", "polymarket", "boost"
    start_time: Optional[str] = None  # ISO datetime — persisted on Bet for boost lifecycle tracking


class BatchBetLeg(BaseModel):
    """Single leg in a batch (arb) bet placement."""
    event_id: Optional[str] = None
    provider_id: str
    market: Optional[str] = None
    outcome: Optional[str] = None
    odds: float
    point: Optional[float] = None
    stake: float
    is_bonus: bool = False
    bonus_type: Optional[str] = None
    utility_score: Optional[float] = None
    selection_probability: Optional[float] = None
    bet_type: Optional[str] = None  # "value", "arb", "reverse", "polymarket", "boost"


class BatchBetCreate(BaseModel):
    """Place multiple legs at once (arb bet)."""
    legs: list[BatchBetLeg]


class BetUpdate(BaseModel):
    result: str  # "won", "lost", "void"
    payout: float = 0.0


class BetEdit(BaseModel):
    """Edit a bet's stake, odds, or result (for correcting auto-stake errors)."""
    stake: Optional[float] = None
    odds: Optional[float] = None
    result: Optional[str] = None  # "won", "lost", "void", "pending"
    payout: Optional[float] = None  # Override payout (e.g. cashout amount)



# ============ Profile Schemas ============

class ProfileCreate(BaseModel):
    name: str
    bankroll: Optional[float] = 1000.0
    currency: Optional[str] = "USD"
    kelly_fraction: Optional[float] = 0.25
    min_edge_pct: Optional[float] = 2.0
    min_arb_pct: Optional[float] = 0.5
    max_stake_pct: Optional[float] = 5.0
    min_retention_pct: Optional[float] = 80.0
    preferred_counterparts: Optional[list[str]] = None
    bonus_enabled: Optional[bool] = True
    bonus_deposit: Optional[float] = 0.0
    color: Optional[str] = None


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    bankroll: Optional[float] = None
    currency: Optional[str] = None
    kelly_fraction: Optional[float] = None
    min_edge_pct: Optional[float] = None
    min_arb_pct: Optional[float] = None
    max_stake_pct: Optional[float] = None
    min_retention_pct: Optional[float] = None
    preferred_counterparts: Optional[list[str]] = None
    bonus_enabled: Optional[bool] = None
    bonus_deposit: Optional[float] = None
    total_deposited: Optional[float] = None
    total_withdrawn: Optional[float] = None
    color: Optional[str] = None


# ============ Opportunity Schemas ============

class BonusMatchRequest(BaseModel):
    event_id: str
    market: str
    anchor_provider: str
    anchor_outcome: str
    anchor_odds: float
    anchor_stake: float
    is_free_bet: bool = False
    counterpart_providers: Optional[list[str]] = None


# ============ Chat Schemas ============

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    system: Optional[str] = None
    messages: list[ChatMessage]
    stream: bool = True


# ============ Trading Schemas ============

class TradingAccountUpdate(BaseModel):
    name: Optional[str] = None
    risk_per_trade_pct: Optional[float] = None
    max_daily_loss_pct: Optional[float] = None
    max_weekly_loss_pct: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    stop_after_consecutive_losses: Optional[int] = None


class TradingBalanceAdjust(BaseModel):
    amount: float  # Positive = deposit, negative = withdraw


class RoutineUpdate(BaseModel):
    macro_notes: Optional[dict] = None
    overnight_high: Optional[float] = None
    overnight_low: Optional[float] = None
    key_levels: Optional[list] = None
    prev_value_area: Optional[dict] = None
    bias_text: Optional[str] = None
    bias_direction: Optional[str] = None
    bias_confidence: Optional[int] = None
    sleep_score: Optional[int] = None
    focus_score: Optional[int] = None
    emotional_score: Optional[int] = None
    psych_override: Optional[str] = None
    checklist_completion: Optional[dict] = None
    is_complete: Optional[bool] = None


class TradeCreate(BaseModel):
    account_id: int
    instrument: str
    direction: str  # "long" or "short"
    setup_type: str
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    targets: Optional[list] = None
    contracts: int = 1
    confirmations: Optional[dict] = None
    notes: Optional[str] = None
    dry_run: bool = False  # Validate only, don't persist


class TradeTransition(BaseModel):
    to_state: str
    notes: Optional[str] = None


class PartialExitRequest(BaseModel):
    contracts: int
    exit_price: float
    notes: Optional[str] = None


class CloseTradeRequest(BaseModel):
    exit_price: float
    commission: float = 0.0
    notes: Optional[str] = None


class TrailStopRequest(BaseModel):
    new_stop: float
    notes: Optional[str] = None


class AddPositionRequest(BaseModel):
    contracts: int
    entry_price: float
    notes: Optional[str] = None


class TradeReviewCreate(BaseModel):
    thesis_recap: Optional[str] = None
    followed_rules: Optional[bool] = None
    what_to_improve: Optional[str] = None
    grade: Optional[int] = None


# ============ Limit Schemas ============

class LimitCreate(BaseModel):
    provider_id: str
    limit_type: Literal["stake_limited", "market_restricted", "odds_restricted", "fully_banned"]
    limit_level: int  # 1-5
    detected_at: Optional[str] = None  # ISO datetime string, defaults to now
    notes: Optional[str] = None


class LimitUpdate(BaseModel):
    limit_level: Optional[int] = None
    notes: Optional[str] = None


class BanProviderRequest(BaseModel):
    provider_id: str
    notes: Optional[str] = None


class LimitRiskUpdate(BaseModel):
    limit_risk: Literal["low", "medium", "high", "instant"]
    limit_notes: Optional[str] = None
