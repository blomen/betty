"""Extract macro/news features for M9 Macro & News Context Engine."""
from datetime import datetime, timezone


def extract_macro_features(
    vix_level: float | None = None,
    vix_change_1d: float | None = None,
    vix_term_structure: str | None = None,
    dxy_level: float | None = None,
    dxy_change_1d: float | None = None,
    us10y_level: float | None = None,
    us10y_change_1d: float | None = None,
    us02y_level: float | None = None,
    yield_curve_spread: float | None = None,
    gex: float | None = None,
    gex_flip_distance_ticks: float | None = None,
    net_options_delta: float | None = None,
    put_call_ratio: float | None = None,
    es_nq_ratio_change: float | None = None,
    cot_net_position: int | None = None,
    cot_change_1w: int | None = None,
) -> dict:
    """Extract macro regime features."""
    term_map = {"contango": 0, "backwardation": 1}
    return {
        "vix_level": vix_level,
        "vix_change_1d": vix_change_1d,
        "vix_term_structure_encoded": term_map.get(vix_term_structure, -1),
        "dxy_level": dxy_level,
        "dxy_change_1d": dxy_change_1d,
        "us10y_level": us10y_level,
        "us10y_change_1d": us10y_change_1d,
        "us02y_level": us02y_level,
        "yield_curve_spread": yield_curve_spread,
        "gex": gex,
        "gex_flip_distance_ticks": gex_flip_distance_ticks,
        "net_options_delta": net_options_delta,
        "put_call_ratio": put_call_ratio,
        "es_nq_ratio_change": es_nq_ratio_change,
        "cot_net_position": cot_net_position,
        "cot_change_1w": cot_change_1w,
    }


def extract_news_impact_features(
    event_name: str | None = None,
    importance: int | None = None,
    surprise: float | None = None,
    vix_at_event: float | None = None,
    delta_1m_after: float | None = None,
    volume_1m_after: float | None = None,
    immediate_impact_pct: float | None = None,
    sustained_impact_pct: float | None = None,
    reversal_pct: float | None = None,
    minutes_since_event: float | None = None,
) -> dict:
    """Extract features from a recent economic event for scoring adjustment."""
    # Event name as simple hash for model (LightGBM handles this as categorical)
    event_map = {
        "FOMC Rate Decision": 0, "Non-Farm Payrolls": 1, "CPI": 2,
        "Core CPI": 3, "PPI": 4, "Jobless Claims": 5, "GDP": 6,
        "Retail Sales": 7, "ISM Manufacturing PMI": 8, "ISM Services PMI": 9,
    }
    return {
        "event_type_encoded": event_map.get(event_name, -1),
        "importance": importance,
        "surprise": surprise,
        "vix_at_event": vix_at_event,
        "delta_1m_after": delta_1m_after,
        "volume_1m_after": volume_1m_after,
        "immediate_impact_pct": immediate_impact_pct,
        "sustained_impact_pct": sustained_impact_pct,
        "reversal_pct": reversal_pct,
        "minutes_since_event": minutes_since_event,
    }
