"""Pydantic schemas for API requests and responses."""

from typing import Optional
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


class BalanceAdjustment(BaseModel):
    amount: float  # Can be positive (add) or negative (subtract)


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


class BetUpdate(BaseModel):
    result: str  # "won", "lost", "void"
    payout: float = 0.0


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
