"""
Risk Management API Routes

Endpoints for:
- Provider risk assessment
- Risk configuration
- Stochastic opportunity selection
- Provider cooldown management
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...db.models import (
    Profile,
    Provider,
    ProviderRiskProfile,
    RiskConfig,
)
from ...risk.calculator import RiskCalculator
from ...risk.selector import StochasticSelector
from ...risk.stake_noise import StakeNoiseInjector
from ..deps import get_db

router = APIRouter(prefix="/api/risk", tags=["risk"])


# ============ Schemas ============


class RiskFeaturesResponse(BaseModel):
    """Behavioral features for a provider."""

    stake_entropy: float
    market_diversity: float
    timing_regularity: float
    outcome_correlation: float
    bonus_usage_ratio: float
    clv_score: float
    win_rate_deviation: float
    account_age_days: int
    total_bets_all_time: int
    bets_analyzed: int
    calculation_window_days: int
    calculated_at: str


class ProviderRiskResponse(BaseModel):
    """Risk assessment for a single provider."""

    provider_id: str
    risk_score: float
    risk_level: str
    features: RiskFeaturesResponse
    recommendations: list[str]
    is_on_cooldown: bool
    cooldown_until: str | None
    cooldown_reason: str | None
    brier_score: float | None


class RiskSummary(BaseModel):
    """Summary of risk across all providers."""

    total_providers: int
    low_risk: int
    medium_risk: int
    high_risk: int
    critical_risk: int
    on_cooldown: int
    avg_risk_score: float


class AllRiskResponse(BaseModel):
    """Risk profiles for all providers."""

    providers: dict[str, ProviderRiskResponse]
    summary: RiskSummary


class RiskConfigResponse(BaseModel):
    """Current risk configuration."""

    lambda_coefficient: float
    stake_noise_pct: float
    softmax_temperature: float
    weight_stake_entropy: float
    weight_market_diversity: float
    weight_timing_regularity: float
    weight_outcome_correlation: float
    weight_bonus_usage: float
    weight_clv: float
    weight_win_rate: float
    threshold_low: float
    threshold_medium: float
    threshold_high: float
    rolling_window_days: int
    cooldown_trigger_score: float
    cooldown_duration_hours: int


class RiskConfigUpdate(BaseModel):
    """Update risk configuration."""

    lambda_coefficient: float | None = Field(None, ge=0, le=1)
    stake_noise_pct: float | None = Field(None, ge=0, le=20)
    softmax_temperature: float | None = Field(None, ge=0.01, le=10)
    weight_stake_entropy: float | None = Field(None, ge=0, le=1)
    weight_market_diversity: float | None = Field(None, ge=0, le=1)
    weight_timing_regularity: float | None = Field(None, ge=0, le=1)
    weight_outcome_correlation: float | None = Field(None, ge=0, le=1)
    weight_bonus_usage: float | None = Field(None, ge=0, le=1)
    weight_clv: float | None = Field(None, ge=0, le=1)
    weight_win_rate: float | None = Field(None, ge=0, le=1)
    threshold_low: float | None = Field(None, ge=0, le=1)
    threshold_medium: float | None = Field(None, ge=0, le=1)
    threshold_high: float | None = Field(None, ge=0, le=1)
    rolling_window_days: int | None = Field(None, ge=7, le=365)
    cooldown_trigger_score: float | None = Field(None, ge=0, le=1)
    cooldown_duration_hours: int | None = Field(None, ge=1, le=720)


class OpportunityInput(BaseModel):
    """Input for opportunity selection."""

    event_id: str
    provider_id: str
    outcome: str
    odds: float
    fair_odds: float


class SelectRequest(BaseModel):
    """Request for stochastic opportunity selection."""

    opportunities: list[OpportunityInput]
    stake: float = Field(..., gt=0)
    temperature: float | None = Field(None, ge=0.01, le=10)
    deterministic: bool = False


class RankedOpportunityResponse(BaseModel):
    """Ranked opportunity with selection probability."""

    event_id: str
    provider_id: str
    outcome: str
    odds: float
    fair_odds: float
    expected_value: float
    edge_pct: float
    risk_score: float
    risk_penalty: float
    utility: float
    base_stake: float
    risk_adjusted_stake: float
    stake_multiplier: float
    selection_probability: float
    rank: int


class SelectResponse(BaseModel):
    """Response from opportunity selection."""

    selected: RankedOpportunityResponse | None
    all_ranked: list[RankedOpportunityResponse]
    selection_entropy: float


class CooldownRequest(BaseModel):
    """Request to set provider cooldown."""

    duration_hours: int = Field(24, ge=1, le=720)
    reason: str | None = None


class StakeNoiseRequest(BaseModel):
    """Request to calculate stake with noise."""

    stake: float = Field(..., gt=0)
    provider_id: str


class StakeNoiseResponse(BaseModel):
    """Response with noisy stake."""

    original_stake: float
    final_stake: float
    noise_applied: float
    noise_pct: float
    was_rounded: bool
    reason: str


# ============ Endpoints ============


@router.get("/provider/{provider_id}", response_model=ProviderRiskResponse)
def get_provider_risk(
    provider_id: str,
    db: Session = Depends(get_db),
):
    """Get risk assessment for a specific provider."""
    # Verify provider exists
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")

    calculator = RiskCalculator(db)
    assessment = calculator.assess_provider(provider_id)

    # Get Brier score
    brier = calculator.calculate_brier_score(provider_id)

    return ProviderRiskResponse(
        provider_id=assessment.provider_id,
        risk_score=assessment.risk_score,
        risk_level=assessment.risk_level,
        features=RiskFeaturesResponse(**assessment.features.to_dict()),
        recommendations=assessment.recommendations,
        is_on_cooldown=assessment.is_on_cooldown,
        cooldown_until=assessment.cooldown_until.isoformat() if assessment.cooldown_until else None,
        cooldown_reason=assessment.cooldown_reason,
        brier_score=brier,
    )


@router.get("/all", response_model=AllRiskResponse)
def get_all_risk(db: Session = Depends(get_db)):
    """Get risk assessments for all providers with bet history."""
    calculator = RiskCalculator(db)
    assessments = calculator.get_all_assessments()

    providers_response = {}
    for provider_id, assessment in assessments.items():
        brier = calculator.calculate_brier_score(provider_id)
        providers_response[provider_id] = ProviderRiskResponse(
            provider_id=assessment.provider_id,
            risk_score=assessment.risk_score,
            risk_level=assessment.risk_level,
            features=RiskFeaturesResponse(**assessment.features.to_dict()),
            recommendations=assessment.recommendations,
            is_on_cooldown=assessment.is_on_cooldown,
            cooldown_until=assessment.cooldown_until.isoformat() if assessment.cooldown_until else None,
            cooldown_reason=assessment.cooldown_reason,
            brier_score=brier,
        )

    # Calculate summary
    total = len(assessments)
    low = sum(1 for a in assessments.values() if a.risk_level == "low")
    medium = sum(1 for a in assessments.values() if a.risk_level == "medium")
    high = sum(1 for a in assessments.values() if a.risk_level == "high")
    critical = sum(1 for a in assessments.values() if a.risk_level == "critical")
    on_cooldown = sum(1 for a in assessments.values() if a.is_on_cooldown)
    avg_score = sum(a.risk_score for a in assessments.values()) / total if total > 0 else 0

    return AllRiskResponse(
        providers=providers_response,
        summary=RiskSummary(
            total_providers=total,
            low_risk=low,
            medium_risk=medium,
            high_risk=high,
            critical_risk=critical,
            on_cooldown=on_cooldown,
            avg_risk_score=avg_score,
        ),
    )


@router.get("/config", response_model=RiskConfigResponse)
def get_risk_config(db: Session = Depends(get_db)):
    """Get current risk configuration."""
    # Get active profile
    profile = db.query(Profile).filter(Profile.is_active).first()
    if not profile:
        profile = db.query(Profile).first()
        if not profile:
            profile = Profile(name="default", is_active=True)
            db.add(profile)
            db.commit()

    # Get or create config
    config = db.query(RiskConfig).filter(RiskConfig.profile_id == profile.id).first()
    if not config:
        config = RiskConfig(profile_id=profile.id)
        db.add(config)
        db.commit()

    return RiskConfigResponse(
        lambda_coefficient=config.lambda_coefficient,
        stake_noise_pct=config.stake_noise_pct,
        softmax_temperature=config.softmax_temperature,
        weight_stake_entropy=config.weight_stake_entropy,
        weight_market_diversity=config.weight_market_diversity,
        weight_timing_regularity=config.weight_timing_regularity,
        weight_outcome_correlation=config.weight_outcome_correlation,
        weight_bonus_usage=config.weight_bonus_usage,
        weight_clv=config.weight_clv,
        weight_win_rate=config.weight_win_rate,
        threshold_low=config.threshold_low,
        threshold_medium=config.threshold_medium,
        threshold_high=config.threshold_high,
        rolling_window_days=config.rolling_window_days,
        cooldown_trigger_score=config.cooldown_trigger_score,
        cooldown_duration_hours=config.cooldown_duration_hours,
    )


@router.put("/config", response_model=RiskConfigResponse)
def update_risk_config(
    update: RiskConfigUpdate,
    db: Session = Depends(get_db),
):
    """Update risk configuration."""
    # Get active profile
    profile = db.query(Profile).filter(Profile.is_active).first()
    if not profile:
        raise HTTPException(status_code=404, detail="No active profile")

    # Get config
    config = db.query(RiskConfig).filter(RiskConfig.profile_id == profile.id).first()
    if not config:
        config = RiskConfig(profile_id=profile.id)
        db.add(config)

    # Update fields
    update_data = update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(config, field, value)

    db.commit()

    return RiskConfigResponse(
        lambda_coefficient=config.lambda_coefficient,
        stake_noise_pct=config.stake_noise_pct,
        softmax_temperature=config.softmax_temperature,
        weight_stake_entropy=config.weight_stake_entropy,
        weight_market_diversity=config.weight_market_diversity,
        weight_timing_regularity=config.weight_timing_regularity,
        weight_outcome_correlation=config.weight_outcome_correlation,
        weight_bonus_usage=config.weight_bonus_usage,
        weight_clv=config.weight_clv,
        weight_win_rate=config.weight_win_rate,
        threshold_low=config.threshold_low,
        threshold_medium=config.threshold_medium,
        threshold_high=config.threshold_high,
        rolling_window_days=config.rolling_window_days,
        cooldown_trigger_score=config.cooldown_trigger_score,
        cooldown_duration_hours=config.cooldown_duration_hours,
    )


@router.post("/select", response_model=SelectResponse)
def select_opportunity(
    request: SelectRequest,
    db: Session = Depends(get_db),
):
    """
    Stochastically select an opportunity.

    Uses softmax distribution based on risk-adjusted utility.
    Returns the selected opportunity plus all ranked options.
    """
    if not request.opportunities:
        raise HTTPException(status_code=400, detail="No opportunities provided")

    selector = StochasticSelector(db)

    # Convert to dicts for selector
    opps = [opp.dict() for opp in request.opportunities]

    # Rank all opportunities
    ranked = selector.rank_opportunities(
        opps,
        request.stake,
        temperature=request.temperature,
    )

    if not ranked:
        return SelectResponse(
            selected=None,
            all_ranked=[],
            selection_entropy=0.0,
        )

    # Select one (or pick deterministic)
    if request.deterministic:
        selected = selector.select_deterministic(opps, request.stake)
    else:
        selected = selector.select(opps, request.stake, temperature=request.temperature)

    # Calculate entropy
    probs = [r.selection_probability for r in ranked]
    entropy = selector.get_entropy(probs)

    # Convert to response models
    all_ranked_response = [
        RankedOpportunityResponse(
            event_id=r.opportunity.event_id,
            provider_id=r.opportunity.provider_id,
            outcome=r.opportunity.outcome,
            odds=r.opportunity.odds,
            fair_odds=r.opportunity.fair_odds,
            expected_value=r.opportunity.expected_value,
            edge_pct=r.opportunity.edge_pct,
            risk_score=r.opportunity.risk_score,
            risk_penalty=r.opportunity.risk_penalty,
            utility=r.opportunity.utility,
            base_stake=r.opportunity.base_stake,
            risk_adjusted_stake=r.opportunity.risk_adjusted_stake,
            stake_multiplier=r.opportunity.stake_multiplier,
            selection_probability=r.selection_probability,
            rank=r.rank,
        )
        for r in ranked
    ]

    selected_response = None
    if selected:
        selected_response = RankedOpportunityResponse(
            event_id=selected.opportunity.event_id,
            provider_id=selected.opportunity.provider_id,
            outcome=selected.opportunity.outcome,
            odds=selected.opportunity.odds,
            fair_odds=selected.opportunity.fair_odds,
            expected_value=selected.opportunity.expected_value,
            edge_pct=selected.opportunity.edge_pct,
            risk_score=selected.opportunity.risk_score,
            risk_penalty=selected.opportunity.risk_penalty,
            utility=selected.opportunity.utility,
            base_stake=selected.opportunity.base_stake,
            risk_adjusted_stake=selected.opportunity.risk_adjusted_stake,
            stake_multiplier=selected.opportunity.stake_multiplier,
            selection_probability=selected.selection_probability,
            rank=selected.rank,
        )

    return SelectResponse(
        selected=selected_response,
        all_ranked=all_ranked_response,
        selection_entropy=entropy,
    )


@router.post("/cooldown/{provider_id}")
def set_provider_cooldown(
    provider_id: str,
    request: CooldownRequest,
    db: Session = Depends(get_db),
):
    """Manually set a provider on cooldown."""
    # Verify provider exists
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")

    # Get or create risk profile
    profile = db.query(ProviderRiskProfile).filter(ProviderRiskProfile.provider_id == provider_id).first()

    if not profile:
        profile = ProviderRiskProfile(provider_id=provider_id)
        db.add(profile)

    # Set cooldown
    profile.is_on_cooldown = True
    profile.cooldown_until = datetime.now(timezone.utc) + timedelta(hours=request.duration_hours)
    profile.cooldown_reason = request.reason or f"Manual cooldown for {request.duration_hours}h"

    db.commit()

    return {
        "success": True,
        "provider_id": provider_id,
        "cooldown_until": profile.cooldown_until.isoformat(),
        "reason": profile.cooldown_reason,
    }


@router.delete("/cooldown/{provider_id}")
def clear_provider_cooldown(
    provider_id: str,
    db: Session = Depends(get_db),
):
    """Clear a provider's cooldown."""
    profile = db.query(ProviderRiskProfile).filter(ProviderRiskProfile.provider_id == provider_id).first()

    if not profile:
        raise HTTPException(status_code=404, detail=f"No risk profile for {provider_id}")

    profile.is_on_cooldown = False
    profile.cooldown_until = None
    profile.cooldown_reason = None

    db.commit()

    return {
        "success": True,
        "provider_id": provider_id,
        "message": "Cooldown cleared",
    }


@router.post("/stake-noise", response_model=StakeNoiseResponse)
def calculate_stake_noise(
    request: StakeNoiseRequest,
    db: Session = Depends(get_db),
):
    """Calculate stake with noise injection."""
    calculator = RiskCalculator(db)
    assessment = calculator.assess_provider(request.provider_id)

    injector = StakeNoiseInjector(db)
    noisy = injector.inject_noise(
        stake=request.stake,
        risk_score=assessment.risk_score,
    )

    return StakeNoiseResponse(
        original_stake=noisy.original_stake,
        final_stake=noisy.final_stake,
        noise_applied=noisy.noise_applied,
        noise_pct=noisy.noise_pct,
        was_rounded=noisy.was_rounded,
        reason=noisy.reason,
    )
