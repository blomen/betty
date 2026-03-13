"""Extract features for M8 Adaptive Kelly Sizing.

Cross-domain model — serves both sports betting and trading.
Features capture opportunity quality, recent performance, and risk context.
"""


def extract_kelly_features(
    domain: str,
    model_confidence: float,
    predicted_edge: float,
    historical_win_rate: float,
    historical_avg_return: float,
    recent_drawdown_pct: float,
    consecutive_wins: int,
    consecutive_losses: int,
    daily_pnl: float,
    weekly_pnl: float,
    account_utilization: float,
    volatility_regime: float,
    time_of_day: int = 12,
    # Sports-specific (optional)
    provider_remaining_lifetime: float | None = None,
    is_freebet: bool = False,
    bonus_wagering_remaining: float = 0.0,
    # Trading-specific (optional)
    setup_type: str | None = None,
    gex: float | None = None,
    correlation_with_open: float = 0.0,
    session_volume_regime: float = 1.0,
) -> dict:
    return {
        "domain_betting": 1 if domain == "betting" else 0,
        "domain_trading": 1 if domain == "trading" else 0,
        "model_confidence": model_confidence,
        "predicted_edge": predicted_edge,
        "historical_win_rate": historical_win_rate,
        "historical_avg_return": historical_avg_return,
        "recent_drawdown_pct": recent_drawdown_pct,
        "consecutive_wins": consecutive_wins,
        "consecutive_losses": consecutive_losses,
        "daily_pnl": daily_pnl,
        "weekly_pnl": weekly_pnl,
        "account_utilization": account_utilization,
        "volatility_regime": volatility_regime,
        "time_of_day": time_of_day,
        # Sports
        "provider_remaining_lifetime": provider_remaining_lifetime or 0.0,
        "is_freebet": int(is_freebet),
        "bonus_wagering_remaining": bonus_wagering_remaining,
        # Trading
        "gex": gex or 0.0,
        "correlation_with_open": correlation_with_open,
        "session_volume_regime": session_volume_regime,
    }
