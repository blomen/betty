"""Tests for COT fix, economic calendar wiring, and news impact recorder."""
import pytest
from datetime import datetime, timedelta, timezone, date
from unittest.mock import MagicMock, AsyncMock, patch


# --- COT Bug Fix ---

def test_cot_store_computes_net_change(db_session):
    """net_change should be computed from previous report, not left None."""
    from src.market_data.cot import COTReport, store_cot_data

    reports = [
        COTReport(
            report_date=date(2026, 3, 24),
            net_commercial=-50000,
            net_non_commercial=120000,
            net_non_reportable=10000,
            open_interest=500000,
        ),
        COTReport(
            report_date=date(2026, 3, 17),
            net_commercial=-48000,
            net_non_commercial=115000,
            net_non_reportable=9000,
            open_interest=490000,
        ),
    ]

    # Store older report first
    store_cot_data(db_session, [reports[1]], symbol="NQ")
    db_session.commit()

    # Store newer report — should compute net_change
    store_cot_data(db_session, [reports[0]], symbol="NQ")
    db_session.commit()

    from src.db.models import CotData
    latest = db_session.query(CotData).filter_by(
        report_date="2026-03-24"
    ).first()

    assert latest is not None
    assert latest.net_position == 120000
    assert latest.net_change == 5000  # 120000 - 115000


def test_cot_store_first_report_no_net_change(db_session):
    """First-ever report should have net_change=None (no previous)."""
    from src.market_data.cot import COTReport, store_cot_data

    report = COTReport(
        report_date=date(2026, 3, 17),
        net_commercial=-48000,
        net_non_commercial=115000,
        net_non_reportable=9000,
        open_interest=490000,
    )
    store_cot_data(db_session, [report], symbol="NQ")
    db_session.commit()

    from src.db.models import CotData
    row = db_session.query(CotData).first()
    assert row.net_position == 115000
    assert row.net_change is None


def test_cot_summary_queries_correct_table(db_session):
    """_get_cot_summary should query cot_data (not cot_reports)."""
    from src.db.models import CotData

    # Insert two rows
    db_session.add(CotData(report_date="2026-03-17", symbol="NQ", net_position=115000))
    db_session.add(CotData(report_date="2026-03-24", symbol="NQ", net_position=120000))
    db_session.commit()

    from src.services.market_service import MarketService
    svc = MarketService(db_session)
    result = svc._get_cot_summary()

    assert result is not None
    assert result["net_non_commercial"] == 120000
    assert result["change_1w"] == 5000  # 120000 - 115000


def test_cot_summary_single_report(db_session):
    """With only one report, change_1w should be None."""
    from src.db.models import CotData

    db_session.add(CotData(report_date="2026-03-24", symbol="NQ", net_position=120000))
    db_session.commit()

    from src.services.market_service import MarketService
    svc = MarketService(db_session)
    result = svc._get_cot_summary()

    assert result is not None
    assert result["net_non_commercial"] == 120000
    assert result["change_1w"] is None


# --- Economic Calendar Wiring ---

def test_calendar_fetch_stores_events(db_session):
    """_fetch_and_store_sync should store ForexFactory events to DB."""
    mock_events = [
        {
            "event_name": "CPI m/m",
            "event_date": datetime(2026, 4, 1, 12, 30, tzinfo=timezone.utc),
            "importance": 3,
            "currency": "USD",
            "forecast": 0.3,
            "actual": 0.4,
            "previous": 0.3,
            "surprise": 0.1,
        },
        {
            "event_name": "ECB Rate",
            "event_date": datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc),
            "importance": 3,
            "currency": "EUR",
            "forecast": None,
            "actual": None,
            "previous": None,
            "surprise": None,
        },
    ]

    with patch("src.ml.macro.economic_calendar.fetch_events", new_callable=AsyncMock, return_value=mock_events):
        from src.data.economic_calendar import _fetch_and_store_sync
        count = _fetch_and_store_sync(db_session, days_ahead=7)

    # Should store only USD events
    from src.db.models import EconomicEvent
    events = db_session.query(EconomicEvent).all()
    assert len(events) == 1
    assert events[0].event_name == "CPI m/m"
    assert events[0].importance == 3
    assert events[0].actual == 0.4
    assert events[0].surprise == 0.1


def test_calendar_updates_actuals(db_session):
    """Re-fetching should update actual/surprise if newly released."""
    from src.db.models import EconomicEvent

    # Pre-existing event without actual
    evt_dt = datetime(2026, 4, 1, 12, 30, tzinfo=timezone.utc)
    db_session.add(EconomicEvent(
        event_name="CPI m/m",
        event_datetime=evt_dt,
        importance=3,
        forecast=0.3,
        actual=None,
        surprise=None,
    ))
    db_session.commit()

    mock_events = [{
        "event_name": "CPI m/m",
        "event_date": evt_dt,
        "importance": 3,
        "currency": "USD",
        "forecast": 0.3,
        "actual": 0.4,
        "previous": 0.3,
        "surprise": 0.1,
    }]

    with patch("src.ml.macro.economic_calendar.fetch_events", new_callable=AsyncMock, return_value=mock_events):
        from src.data.economic_calendar import _fetch_and_store_sync
        count = _fetch_and_store_sync(db_session, days_ahead=7)

    evt = db_session.query(EconomicEvent).first()
    assert evt.actual == 0.4
    assert evt.surprise == 0.1


# --- News Impact Recorder ---

@pytest.mark.asyncio
async def test_news_impact_creates_row(db_session):
    """Should create a NewsImpact row for a recent economic event."""
    from src.db.models import EconomicEvent, NewsImpact

    # Event that happened 3 minutes ago
    evt_time = datetime.now(timezone.utc) - timedelta(minutes=3)
    db_session.add(EconomicEvent(
        event_name="NFP",
        event_datetime=evt_time,
        importance=3,
    ))
    db_session.commit()

    # Mock stream with live price
    mock_stream = MagicMock()
    mock_stream.buffer.ticks = [{"price": 20000.0, "ts": datetime.now(timezone.utc)}]

    def mock_factory():
        return db_session

    with patch("src.ml.macro.news_impact_recorder._get_vix_level", return_value=18.5):
        from src.ml.macro.news_impact_recorder import record_news_impacts
        count = await record_news_impacts(mock_factory, mock_stream)

    assert count >= 1
    impact = db_session.query(NewsImpact).first()
    assert impact is not None
    assert impact.symbol == "NQ"
    assert impact.vix_at_event == 18.5
    # price_1m should be filled (3 min elapsed > 1 min interval)
    assert impact.price_1m == 20000.0


@pytest.mark.asyncio
async def test_news_impact_fills_intervals(db_session):
    """Should progressively fill price columns as time elapses."""
    from src.db.models import EconomicEvent, NewsImpact

    # Event 20 minutes ago — should fill 1m, 5m, 15m but not 30m/60m
    evt_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    evt = EconomicEvent(
        event_name="Jobless Claims",
        event_datetime=evt_time,
        importance=2,
    )
    db_session.add(evt)
    db_session.commit()

    # Pre-create impact row (simulating earlier run)
    db_session.add(NewsImpact(
        event_id=evt.id,
        symbol="NQ",
        price_before=19950.0,
        vix_at_event=17.0,
    ))
    db_session.commit()

    mock_stream = MagicMock()
    mock_stream.buffer.ticks = [{"price": 20050.0}]

    def mock_factory():
        return db_session

    with patch("src.ml.macro.news_impact_recorder._get_vix_level", return_value=17.0):
        from src.ml.macro.news_impact_recorder import record_news_impacts
        count = await record_news_impacts(mock_factory, mock_stream)

    impact = db_session.query(NewsImpact).first()
    assert impact.price_1m == 20050.0
    assert impact.price_5m == 20050.0
    assert impact.price_15m == 20050.0
    assert impact.price_30m is None  # 20 min < 30 min
    assert impact.price_60m is None

    # Check derived metrics
    assert impact.immediate_impact_pct is not None
    assert abs(impact.immediate_impact_pct - 0.5013) < 0.01  # (20050-19950)/19950*100


@pytest.mark.asyncio
async def test_news_impact_ignores_low_importance(db_session):
    """Should skip importance=1 events."""
    from src.db.models import EconomicEvent, NewsImpact

    evt_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.add(EconomicEvent(
        event_name="Some Low Event",
        event_datetime=evt_time,
        importance=1,
    ))
    db_session.commit()

    mock_stream = MagicMock()
    mock_stream.buffer.ticks = [{"price": 20000.0}]

    def mock_factory():
        return db_session

    from src.ml.macro.news_impact_recorder import record_news_impacts
    count = await record_news_impacts(mock_factory, mock_stream)

    assert count == 0
    assert db_session.query(NewsImpact).count() == 0


@pytest.mark.asyncio
async def test_news_impact_no_price_available(db_session):
    """Should gracefully handle no live price."""
    from src.db.models import EconomicEvent, NewsImpact

    evt_time = datetime.now(timezone.utc) - timedelta(minutes=3)
    db_session.add(EconomicEvent(
        event_name="GDP",
        event_datetime=evt_time,
        importance=3,
    ))
    db_session.commit()

    # Empty tick buffer
    mock_stream = MagicMock()
    mock_stream.buffer.ticks = []

    def mock_factory():
        return db_session

    from src.ml.macro.news_impact_recorder import record_news_impacts
    count = await record_news_impacts(mock_factory, mock_stream)

    assert count == 0
    assert db_session.query(NewsImpact).count() == 0
