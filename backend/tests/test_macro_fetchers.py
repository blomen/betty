"""Test macro data fetchers (economic calendar, options flow)."""


def test_parse_economic_event():
    from src.ml.macro.economic_calendar import parse_event
    raw = {"title": "CPI m/m", "date": "2026-03-12T08:30:00-04:00", "impact": "High",
           "forecast": "0.3%", "actual": "0.4%", "previous": "0.3%"}
    parsed = parse_event(raw)
    assert parsed["event_name"] == "CPI m/m"
    assert parsed["importance"] == 3
    assert parsed["forecast"] == 0.3
    assert parsed["actual"] == 0.4
    assert parsed["surprise"] == 0.1


def test_parse_importance_mapping():
    from src.ml.macro.economic_calendar import _parse_importance
    assert _parse_importance("High") == 3
    assert _parse_importance("Medium") == 2
    assert _parse_importance("Low") == 1
    assert _parse_importance("Holiday") == 0


def test_parse_percentage_value():
    from src.ml.macro.economic_calendar import _parse_numeric
    assert _parse_numeric("0.3%") == 0.3
    assert _parse_numeric("-1.2%") == -1.2
    assert _parse_numeric("250K") == 250.0
    assert _parse_numeric("1.5M") == 1.5
    assert _parse_numeric("") is None
    assert _parse_numeric(None) is None


def test_build_options_flow_row():
    from src.ml.macro.options_flow import build_options_flow_row
    row = build_options_flow_row(date="2026-03-12", vix_level=18.5, vix_1d_change=-0.3,
                                  dxy_level=103.2, dxy_1d_change=0.15,
                                  us10y_level=4.25, us10y_1d_change=-0.02, us02y_level=4.50)
    assert row["date"] == "2026-03-12"
    assert row["vix_level"] == 18.5
    assert row["yield_curve_spread"] == -0.25
    assert row["symbol"] == "NQ"
