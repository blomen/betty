"""Extract features for M7 Dynamic Gate Classifier (day type + macro regime)."""


def extract_gate_features(
    rf_after_ib: float | None = None,
    ib_range: float | None = None,
    ib_range_vs_avg: float | None = None,
    opening_type: str | None = None,
    first_hour_delta_total: float | None = None,
    first_hour_volume_vs_avg: float | None = None,
    overnight_range_pct: float | None = None,
    gap_filled_pct: float | None = None,
    yesterday_market_type: str | None = None,
    poor_high_or_low_in_ib: bool | None = None,
    first_hour_big_trades_count: int | None = None,
    session_volume_first_hour: float | None = None,
    vix_level: float | None = None,
    gex: float | None = None,
    value_migration_direction: str | None = None,
    ib_tpo_count: int | None = None,
) -> dict:
    """Extract features for day-type classification."""
    opening_map = {"OD": 0, "OTD": 1, "ORR": 2, "OA": 3}
    mtype_map = {"balanced": 0, "trending_up": 1, "trending_down": 2, "unknown": 3}
    migration_map = {"up": 0, "down": 1, "overlapping": 2}

    return {
        "rf_after_ib": rf_after_ib,
        "ib_range": ib_range,
        "ib_range_vs_avg": ib_range_vs_avg,
        "opening_type_encoded": opening_map.get(opening_type, 4),
        "first_hour_delta_total": first_hour_delta_total,
        "first_hour_volume_vs_avg": first_hour_volume_vs_avg,
        "overnight_range_pct": overnight_range_pct,
        "gap_filled_pct": gap_filled_pct,
        "yesterday_market_type_encoded": mtype_map.get(yesterday_market_type, 3),
        "poor_high_or_low_in_ib": int(poor_high_or_low_in_ib) if poor_high_or_low_in_ib is not None else 0,
        "first_hour_big_trades_count": first_hour_big_trades_count,
        "session_volume_first_hour": session_volume_first_hour,
        "vix_level": vix_level,
        "gex": gex,
        "value_migration_encoded": migration_map.get(value_migration_direction, 2),
        "ib_tpo_count": ib_tpo_count,
    }
