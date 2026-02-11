"""Pydantic schemas for API requests and responses."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, ConfigDict


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


class BalanceAdjustment(BaseModel):
    amount: float  # Can be positive (add) or negative (subtract)


class DepositRequest(BaseModel):
    amount: float  # Deposit amount (positive)


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
    stake: float
    is_bonus: bool = False
    bonus_type: Optional[str] = None
    # Risk management (optional, populated by auto-stake flow)
    utility_score: Optional[float] = None
    selection_probability: Optional[float] = None
    stake_noise_applied: Optional[float] = None


class BetUpdate(BaseModel):
    result: str  # "won", "lost", "void"
    payout: float = 0.0


class AutoPlaceBetRequest(BaseModel):
    """Auto-place a bet with full pipeline: edge → Kelly stake → risk → noise → record."""
    event_id: str
    provider_id: str
    market: str
    outcome: str
    odds: float
    is_bonus: bool = False
    bonus_type: Optional[str] = None


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


# ============ Response Models ============

class EventSummaryResponse(BaseModel):
    """Summary of a sporting event."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    sport: str
    league: Optional[str] = None
    home_team: str
    away_team: str
    start_time: Optional[datetime] = None
    odds_count: int = 0


class OddsEntryResponse(BaseModel):
    """Single odds entry from a provider."""
    model_config = ConfigDict(from_attributes=True)

    provider: str
    outcome: str
    odds: float
    point: Optional[float] = None


class ArbitrageLegResponse(BaseModel):
    """Single leg of an arbitrage opportunity."""
    outcome: str
    provider: str
    odds: float
    stake: float = Field(description="Recommended stake for $100 total")
    return_amount: float = Field(alias="return", description="Expected return")

    model_config = ConfigDict(populate_by_name=True)


class OpportunityResponse(BaseModel):
    """Arbitrage or value bet opportunity."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: Literal["arbitrage", "value", "bonus"]
    event_id: str
    market: str
    profit_pct: Optional[float] = Field(None, description="Profit percentage for arbitrage")
    edge_pct: Optional[float] = Field(None, description="Edge percentage for value bets")
    detected_at: datetime
    is_active: bool = True

    # Legacy single-leg fields
    provider1: Optional[str] = None
    provider2: Optional[str] = None
    odds1: Optional[float] = None
    odds2: Optional[float] = None
    outcome1: Optional[str] = None
    outcome2: Optional[str] = None

    # Multi-leg support
    legs: Optional[list[ArbitrageLegResponse]] = None
    total_stake: Optional[float] = None


class FullArbitrageResponse(BaseModel):
    """Full arbitrage opportunity with all legs."""
    event_id: str
    market: str
    profit_pct: float
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    sport: Optional[str] = None
    start_time: Optional[datetime] = None
    legs: list[ArbitrageLegResponse]


class BetResponse(BaseModel):
    """Bet information."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: Optional[str] = None
    provider_id: str
    market: Optional[str] = None
    outcome: Optional[str] = None
    odds: float
    stake: float
    is_bonus: bool = False
    bonus_type: Optional[str] = None
    result: str = "pending"
    payout: float = 0.0
    profit: float = 0.0
    roi_pct: float = 0.0
    placed_at: datetime
    settled_at: Optional[datetime] = None


class ProviderResponse(BaseModel):
    """Provider information."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    url: Optional[str] = None
    is_enabled: bool = True
    balance: float = 0.0


class ProviderExposureResponse(BaseModel):
    """Provider balance and pending exposure."""
    provider_id: str
    provider_name: str
    total_balance: float
    pending_exposure: float
    pending_bets_count: int
    available: float


class BankrollExposureResponse(BaseModel):
    """Total bankroll with provider breakdown."""
    total_balance: float
    total_pending: float
    total_available: float
    providers: list[ProviderExposureResponse]


class BankrollStatsResponse(BaseModel):
    """Betting performance statistics."""
    total_bets: int
    wins: int
    losses: int
    voids: int
    total_staked: float
    total_profit: float
    roi_pct: float
    win_rate: float


class ProfileResponse(BaseModel):
    """Profile information."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    bankroll: float
    currency: str
    kelly_fraction: float
    min_edge_pct: float
    min_arb_pct: float
    max_stake_pct: float
    min_retention_pct: float = 80.0
    preferred_counterparts: Optional[list[str]] = None
    bonus_enabled: bool = True
    bonus_deposit: float = 0.0
    is_active: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProviderProgressResponse(BaseModel):
    """Extraction progress for a single provider."""
    status: Literal["pending", "running", "completed", "failed"]
    events: int = 0
    odds: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None
    sports_completed: int = 0
    sports_total: int = 0


class ExtractionStatusResponse(BaseModel):
    """Current extraction status."""
    running: bool
    last_run: Optional[datetime] = None
    start_time: Optional[datetime] = None
    elapsed_seconds: float = 0.0
    progress_pct: float = 0.0
    total_events: int = 0
    total_odds: int = 0
    current_provider: Optional[str] = None
    completed_providers: int = 0
    total_providers: int = 0
    providers: dict[str, ProviderProgressResponse] = {}


class HealthResponse(BaseModel):
    """Basic health check response."""
    status: str = "ok"
    time: datetime


class ReadinessResponse(BaseModel):
    """Readiness check with dependencies."""
    status: str
    database: bool
    database_latency_ms: float
    providers_available: int
    providers_total: int


class LivenessResponse(BaseModel):
    """Liveness check."""
    status: str = "alive"
    uptime_seconds: float


class CircuitBreakerStatusResponse(BaseModel):
    """Circuit breaker status for a provider."""
    state: Literal["CLOSED", "OPEN", "HALF_OPEN"]
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    opened_at: Optional[datetime] = None


class SuccessResponse(BaseModel):
    """Generic success response."""
    success: bool = True
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None
