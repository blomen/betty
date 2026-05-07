"""Regression tests: bankroll_service.get_stats SQL aggregate refactor.

The previous implementation pulled every settled Bet row and ran 5+
Python iterations to compute counts/sums. The refactor uses
`bet_repo.get_settled_aggregates` (one SQL GROUP BY query keyed on
provider_id, currency, result, is_bonus) and derives everything from
the per-group sums.

These tests lock the externally-observable contract of `get_stats` so
the SQL refactor cannot regress totals/counts/CLV math.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Bet, Profile
from src.services.bankroll_service import BankrollService


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    profile = Profile(id=1, name="test", is_active=True, total_deposited=10000.0, total_withdrawn=0.0)
    session.add(profile)
    session.commit()
    yield session
    session.close()


def _add_bet(
    db,
    *,
    provider_id: str = "unibet",
    currency: str = "SEK",
    result: str = "won",
    stake: float = 100.0,
    payout: float = 200.0,
    is_bonus: bool = False,
    clv_pct: float | None = None,
    days_ago: float = 1.0,
):
    bet = Bet(
        profile_id=1,
        provider_id=provider_id,
        event_id=f"evt_{provider_id}_{result}_{days_ago}_{stake}",
        market="1x2",
        outcome="Home",
        odds=2.0,
        stake=stake,
        payout=payout,
        currency=currency,
        result=result,
        is_bonus=is_bonus,
        clv_pct=clv_pct,
        placed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago),
    )
    db.add(bet)
    db.commit()


def test_get_stats_no_bets_returns_zeros(db):
    svc = BankrollService(db)
    result = svc.get_stats()
    assert result["total_bets"] == 0
    assert result["wins"] == 0
    assert result["losses"] == 0
    assert result["voids"] == 0
    assert result["total_staked"] == 0
    assert result["total_profit"] == 0
    assert result["roi_pct"] == 0
    assert result["win_rate"] == 0
    assert result["avg_clv"] == 0
    assert result["clv_positive_pct"] == 0
    assert result["clv_count"] == 0


def test_get_stats_counts_settled_excludes_bonus(db):
    """Regular bets count toward stats; bonus bets don't."""
    _add_bet(db, result="won", stake=100, payout=200)  # +100 profit
    _add_bet(db, result="lost", stake=50, payout=0)  # -50 profit
    _add_bet(db, result="void", stake=30, payout=30)  # 0 profit
    _add_bet(db, result="won", stake=10, payout=20, is_bonus=True)  # bonus, ignored

    svc = BankrollService(db)
    r = svc.get_stats()
    assert r["total_bets"] == 3  # bonus excluded
    assert r["wins"] == 1
    assert r["losses"] == 1
    assert r["voids"] == 1
    # Profit: won (200-100) + lost (-50) + void (0) = 50
    assert r["total_profit"] == 50
    # Staked: 100 + 50 + 30 = 180 (bonus excluded)
    assert r["total_staked"] == 180
    # ROI = 50/180 * 100 = 27.78
    assert r["roi_pct"] == pytest.approx(27.78, abs=0.01)
    # Win rate = 1/3 * 100 = 33.33
    assert r["win_rate"] == pytest.approx(33.33, abs=0.01)


def test_get_stats_pending_bets_excluded(db):
    """Pending bets must NOT be counted (the SQL filter applies)."""
    _add_bet(db, result="pending", stake=999, payout=0)
    _add_bet(db, result="won", stake=100, payout=200)
    svc = BankrollService(db)
    r = svc.get_stats()
    assert r["total_bets"] == 1
    assert r["wins"] == 1
    assert r["total_staked"] == 100


def test_get_stats_clv_metrics(db):
    """avg_clv averages over non-null clv_pct values; positive_pct counts >0."""
    _add_bet(db, result="won", clv_pct=2.0)
    _add_bet(db, result="won", clv_pct=-1.0)
    _add_bet(db, result="lost", clv_pct=4.0)
    _add_bet(db, result="lost", clv_pct=None)  # excluded from CLV stats

    svc = BankrollService(db)
    r = svc.get_stats()
    assert r["clv_count"] == 3
    # avg = (2 + -1 + 4) / 3 = 1.67
    assert r["avg_clv"] == pytest.approx(1.67, abs=0.01)
    # 2/3 positive = 66.7
    assert r["clv_positive_pct"] == pytest.approx(66.7, abs=0.1)


def test_get_stats_currency_conversion_via_aggregate(db, monkeypatch):
    """to_sek must apply per-(provider,currency) group, not per-bet.

    Two Pinnacle USD bets at $100 each should sum to $200, then convert
    once at Pinnacle's rate. The aggregate path returns one row for that
    group; we apply the rate to the group's sum_stake.
    """

    # Mock Pinnacle USD rate = 10.0 (SEK per USD), default = 1.0
    def fake_rate(provider_id: str) -> float:
        return 10.0 if provider_id == "pinnacle" else 1.0

    # bankroll_service does `from ..config import get_exchange_rate` (the
    # re-export from src/config/__init__.py), so we patch that path.
    monkeypatch.setattr("src.config.get_exchange_rate", fake_rate)

    # 2 USD wins at Pinnacle, 1 SEK win at Unibet
    _add_bet(db, provider_id="pinnacle", currency="USD", result="won", stake=100, payout=180)
    _add_bet(db, provider_id="pinnacle", currency="USD", result="won", stake=100, payout=180)
    _add_bet(db, provider_id="unibet", currency="SEK", result="won", stake=50, payout=100)

    svc = BankrollService(db)
    r = svc.get_stats()
    # Staked: (100+100)*10 + 50*1 = 2050 SEK
    assert r["total_staked"] == 2050
    # Profit: pinnacle won group: sum_payout - sum_stake = 360 - 200 = 160 USD * 10 = 1600
    #         unibet won group:   100 - 50 = 50 SEK
    # Total: 1650 SEK
    assert r["total_profit"] == 1650


def test_get_stats_multiple_results_same_provider(db):
    """A provider with mixed results aggregates correctly within currency."""
    _add_bet(db, result="won", stake=100, payout=300)  # +200
    _add_bet(db, result="won", stake=100, payout=250)  # +150
    _add_bet(db, result="lost", stake=200, payout=0)  # -200
    _add_bet(db, result="lost", stake=100, payout=0)  # -100

    svc = BankrollService(db)
    r = svc.get_stats()
    assert r["total_bets"] == 4
    assert r["wins"] == 2
    assert r["losses"] == 2
    # won group:  sum_payout=550, sum_stake=200 -> profit 350
    # lost group: -sum_stake=-300
    # total profit = 50
    assert r["total_profit"] == 50
    assert r["total_staked"] == 500
