"""Tests for ProfileRepo.get_wagering_prognosis.

Locks behavior before refactoring to SQL aggregates (replaces the two
`Bet.*all()` scans + Python sum/min/len with a single GROUP BY query).
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Bet, Profile, ProfileProviderBonus
from src.repositories.profile_repo import ProfileRepo


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Profile(id=1, name="test", is_active=True))
    session.commit()
    yield session
    session.close()


def _add_bonus(
    db,
    *,
    provider_id: str = "unibet",
    status: str = "in_progress",
    requirement: float = 10000.0,
    wagered: float = 1000.0,
    min_odds: float = 1.80,
    expires_in_days: int | None = None,
):
    expires = datetime.now(timezone.utc) + timedelta(days=expires_in_days) if expires_in_days is not None else None
    rec = ProfileProviderBonus(
        profile_id=1,
        provider_id=provider_id,
        bonus_status=status,
        wagering_requirement=requirement,
        wagered_amount=wagered,
        min_odds=min_odds,
        expires_at=expires,
    )
    db.add(rec)
    db.commit()


def _add_bet(db, *, provider_id: str, odds: float, stake: float, days_ago: float):
    """Add a bet `days_ago` days in the past (use float for sub-day precision)."""
    bet = Bet(
        profile_id=1,
        provider_id=provider_id,
        event_id=f"evt_{provider_id}_{days_ago}",
        market="1x2",
        outcome="Home",
        odds=odds,
        stake=stake,
        currency="SEK",
        result="pending",
        placed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago),
    )
    db.add(bet)
    db.commit()


def test_returns_none_when_no_bonus(db):
    repo = ProfileRepo(db)
    assert repo.get_wagering_prognosis(profile_id=1, provider_id="unibet") is None


def test_returns_none_when_bonus_completed(db):
    _add_bonus(db, status="completed")
    repo = ProfileRepo(db)
    assert repo.get_wagering_prognosis(profile_id=1, provider_id="unibet") is None


def test_returns_none_when_already_wagered(db):
    _add_bonus(db, requirement=1000, wagered=1000)
    repo = ProfileRepo(db)
    assert repo.get_wagering_prognosis(profile_id=1, provider_id="unibet") is None


def test_no_recent_bets_returns_zeros(db):
    """In-progress bonus with no qualifying bets — stats are 0, remaining is set."""
    _add_bonus(db)
    repo = ProfileRepo(db)
    result = repo.get_wagering_prognosis(profile_id=1, provider_id="unibet")
    assert result is not None
    assert result["remaining"] == 9000
    assert result["bets_per_week"] == 0.0
    assert result["avg_stake"] == 0.0
    assert result["weekly_wagering"] == 0
    assert result["est_weeks"] is None
    assert result["total_bets_per_week"] == 0.0
    assert result["total_avg_stake"] == 0.0
    assert result["total_weekly_wagering"] == 0


def test_per_provider_filter_excludes_other_provider_and_low_odds(db):
    _add_bonus(db, requirement=10000, wagered=1000, min_odds=1.80)
    # qualifying — unibet, odds >= 1.80, in window
    _add_bet(db, provider_id="unibet", odds=2.0, stake=500, days_ago=7.5)
    _add_bet(db, provider_id="unibet", odds=2.0, stake=500, days_ago=1.0)
    # excluded — odds below min
    _add_bet(db, provider_id="unibet", odds=1.50, stake=200, days_ago=3.0)
    # excluded — different provider (counts toward total but not per-provider)
    _add_bet(db, provider_id="bet365", odds=2.0, stake=300, days_ago=5.0)

    repo = ProfileRepo(db)
    result = repo.get_wagering_prognosis(profile_id=1, provider_id="unibet")
    assert result is not None

    # Per-provider stats (qualifying only — 2 unibet bets at odds=2.0)
    assert result["avg_stake"] == 500
    assert result["bets_per_week"] > 0
    # bets_per_week = 2 / (7/7) = ~2.0 (earliest is ~7.5 days ago → days=7)
    assert result["bets_per_week"] == 2.0
    assert result["weekly_wagering"] == 1000  # 2 * 500

    # Total stats (all 4 bets, regardless of provider/odds)
    # earliest of all 4 = 7.5 days ago, days_span = 7
    # total_bets_per_week = 4 / 1 = 4.0
    # total_avg_stake = (500+500+200+300)/4 = 375.0
    assert result["total_bets_per_week"] == 4.0
    assert result["total_avg_stake"] == 375
    # total_weekly_wagering = 4.0 * 375 = 1500, capped to bankroll if >0
    # bankroll is 0 (no balances) → uncapped
    assert result["total_weekly_wagering"] == 1500


def test_required_weekly_from_deadline(db):
    """expires_at drives required_weekly_wagering = remaining / weeks_remaining."""
    _add_bonus(db, requirement=10000, wagered=1000, expires_in_days=14)
    repo = ProfileRepo(db)
    result = repo.get_wagering_prognosis(profile_id=1, provider_id="unibet")
    assert result is not None
    # 9000 / 2 weeks = 4500
    assert result["required_weekly_wagering"] == 4500


def test_old_bets_outside_30day_window_excluded(db):
    """Bets older than 30 days (the cutoff) don't count even if otherwise qualifying."""
    _add_bonus(db)
    _add_bet(db, provider_id="unibet", odds=2.0, stake=999, days_ago=45)
    _add_bet(db, provider_id="unibet", odds=2.0, stake=100, days_ago=5)

    repo = ProfileRepo(db)
    result = repo.get_wagering_prognosis(profile_id=1, provider_id="unibet")
    assert result is not None
    # Only the 5-day-old bet counts → avg_stake=100, not 549.5
    assert result["avg_stake"] == 100
