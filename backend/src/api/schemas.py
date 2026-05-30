"""Pydantic schemas for API requests and responses."""

from typing import Literal

from pydantic import BaseModel

# ============ Provider Schemas ============


class ProviderCreate(BaseModel):
    id: str
    name: str
    url: str | None = None
    balance: float = 0.0


class ProviderUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    is_enabled: bool | None = None
    balance: float | None = None


# ============ Bankroll Schemas ============


class BulkBalanceUpdate(BaseModel):
    balance: float
    provider_ids: list[str] | None = None  # If None, updates all enabled providers


class BalanceSet(BaseModel):
    balance: float  # Absolute balance to set


class DepositRequest(BaseModel):
    amount: float  # Deposit amount (positive)


class AllocateRequest(BaseModel):
    liquid_amount: float | None = None  # Cash in bank to allocate; None = unbounded "recommended" mode


class BonusTransitionRequest(BaseModel):
    action: Literal["start_freebet", "trigger_settled", "freebet_used"]


class StakePreviewRequest(BaseModel):
    """Request to preview stake for an opportunity."""

    edge_pct: float  # Edge percentage (e.g., 5.0 for 5%)
    odds: float  # Decimal odds
    event_id: str | None = None  # For exposure tracking
    provider_id: str | None = None  # For bonus status checking


class RecordBetRequest(BaseModel):
    """Request to record a bet for exposure tracking."""

    event_id: str
    provider_id: str
    stake: float
    odds: float


# ============ Bet Schemas ============


class BetCreate(BaseModel):
    event_id: str | None = None
    provider_id: str
    market: str | None = None
    outcome: str | None = None
    odds: float
    point: float | None = None  # Spread/total line value
    stake: float
    is_bonus: bool = False
    bonus_type: str | None = None
    # Risk management (optional, populated by auto-stake flow)
    utility_score: float | None = None
    selection_probability: float | None = None
    fair_odds_at_placement: float | None = None  # For boosts: pass LLM fair odds directly
    boost_event: str | None = None  # For boosts: event name at placement (e.g. "Arsenal vs Sunderland")
    boost_title: str | None = None  # For boosts: LLM-simplified title at placement
    bet_type: str | None = None  # "value", "arb_anchor", "arb_counter", "mirror", "boost"
    start_time: str | None = None  # ISO datetime — persisted on Bet for boost lifecycle tracking
    provider_bet_id: str | None = None  # Coupon/bet ref from placement response — enables exact-ID settlement matching
    arb_group_id: str | None = None  # Set by arb_runner to link anchor + counter legs at insert-time
    #                                  (instead of relying on the after-the-fact correlate_arbs sweep)
    # Skip the balance-sufficiency check. Used by mirror's reactive sync
    # when recording bets the user already placed manually on the
    # bookmaker's site — the bookmaker already accepted the stake, our
    # balance number may already reflect the deduction, and rejecting on
    # "insufficient balance" would silently drop the record (the pinnacle
    # 0-kr issue, 2026-05-15).
    external_placement: bool = False


class BatchBetLeg(BaseModel):
    """Single leg in a batch (arb) bet placement."""

    event_id: str | None = None
    provider_id: str
    market: str | None = None
    outcome: str | None = None
    odds: float
    point: float | None = None
    stake: float
    is_bonus: bool = False
    bonus_type: str | None = None
    utility_score: float | None = None
    selection_probability: float | None = None
    fair_odds_at_placement: float | None = None
    bet_type: str | None = None  # "value", "arb_anchor", "arb_counter", "reverse", "polymarket", "boost"
    provider_bet_id: str | None = None  # Coupon/bet ref from placement response
    boost_event: str | None = None  # Free-text event name when no Event row exists
    start_time: str | None = None  # ISO datetime — for boost / no-event-row bets
    external_placement: bool = False  # Skip balance-sufficiency check (user already placed on site)


class BatchBetCreate(BaseModel):
    """Place multiple legs at once (arb bet)."""

    legs: list[BatchBetLeg]
    # Shared group ID applied to every leg so the arb is link-grouped at
    # insert-time (instead of relying on the after-the-fact correlate_arbs
    # sweep). Frontend generates a UUID and passes it for a manual arb
    # placement from the PlayPage calculator widget.
    arb_group_id: str | None = None


class BetUpdate(BaseModel):
    result: str  # "won", "lost", "void"
    payout: float = 0.0


class BetEdit(BaseModel):
    """Edit a bet's stake, odds, or result (for correcting auto-stake errors)."""

    stake: float | None = None
    odds: float | None = None
    result: str | None = None  # "won", "lost", "void", "pending"
    payout: float | None = None  # Override payout (e.g. cashout amount)
    provider_bet_id: str | None = None  # Backfill from history reconciliation


# ============ Profile Schemas ============


class ProfileCreate(BaseModel):
    name: str
    bankroll: float | None = 1000.0
    currency: str | None = "USD"
    kelly_fraction: float | None = 0.25
    min_edge_pct: float | None = 2.0
    min_arb_pct: float | None = 0.5
    max_stake_pct: float | None = 5.0
    min_retention_pct: float | None = 80.0
    preferred_counterparts: list[str] | None = None
    bonus_enabled: bool | None = True
    bonus_deposit: float | None = 0.0
    color: str | None = None
    # Account-layer provisioning (multi-profile sharp accounts):
    kind: str | None = "edge"  # "edge" | "bonus" — drives Rule-B ROI bucketing
    use_shared_sharp: bool | None = True  # link the existing shared sharp pool
    fresh_sharp_label: str | None = None  # else create fresh sharp accounts under this label
    soft_providers: list[str] | None = None  # soft books this campaign signs up for


class ProfileUpdate(BaseModel):
    name: str | None = None
    bankroll: float | None = None
    currency: str | None = None
    kelly_fraction: float | None = None
    min_edge_pct: float | None = None
    min_arb_pct: float | None = None
    max_stake_pct: float | None = None
    min_retention_pct: float | None = None
    preferred_counterparts: list[str] | None = None
    bonus_enabled: bool | None = None
    bonus_deposit: float | None = None
    total_deposited: float | None = None
    total_withdrawn: float | None = None
    color: str | None = None


# ============ Opportunity Schemas ============


class BonusMatchRequest(BaseModel):
    event_id: str
    market: str
    anchor_provider: str
    anchor_outcome: str
    anchor_odds: float
    anchor_stake: float
    is_free_bet: bool = False
    counterpart_providers: list[str] | None = None


# ============ Chat Schemas ============


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    system: str | None = None
    messages: list[ChatMessage]
    stream: bool = True


# ============ Limit Schemas ============


class LimitCreate(BaseModel):
    provider_id: str
    limit_type: Literal["stake_limited", "market_restricted", "odds_restricted", "fully_banned"]
    limit_level: int  # 1-5
    detected_at: str | None = None  # ISO datetime string, defaults to now
    notes: str | None = None


class LimitUpdate(BaseModel):
    limit_level: int | None = None
    notes: str | None = None


class BanProviderRequest(BaseModel):
    provider_id: str
    notes: str | None = None


class LimitRiskUpdate(BaseModel):
    limit_risk: Literal["low", "medium", "high", "instant"]
    limit_notes: str | None = None
