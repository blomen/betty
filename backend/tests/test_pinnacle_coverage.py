"""Test Pinnacle coverage delta logging for M10d."""


def test_compute_coverage_delta():
    from src.ml.features.pinnacle_coverage import compute_coverage_delta

    result = compute_coverage_delta(
        pinnacle_events=100,
        pinnacle_ml=100,
        pinnacle_spread=80,
        pinnacle_total=90,
        provider_matched=65,
        provider_ml=65,
        provider_spread=30,
        provider_total=45,
    )

    assert result["event_coverage_pct"] == 65.0
    assert result["ml_coverage_pct"] == 65.0
    assert result["spread_coverage_pct"] == 37.5
    assert result["total_coverage_pct"] == 50.0
    assert result["missing_events"] == 35
    assert result["missing_spread"] == 50
    assert result["missing_total"] == 45


def test_compute_coverage_delta_zero_pinnacle():
    from src.ml.features.pinnacle_coverage import compute_coverage_delta

    result = compute_coverage_delta(
        pinnacle_events=0,
        pinnacle_ml=0,
        pinnacle_spread=0,
        pinnacle_total=0,
        provider_matched=0,
        provider_ml=0,
        provider_spread=0,
        provider_total=0,
    )

    assert result["event_coverage_pct"] == 0.0
    assert result["missing_events"] == 0
