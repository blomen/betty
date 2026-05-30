"""Rule B accounting: bonus-profile bets (both legs) excluded from true ROI and
summed into a separate bonus_profit total."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Bet, Profile, Provider
from src.services.bankroll_service import BankrollService


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add_all([Provider(id="pinnacle", name="Pin"), Provider(id="betinia", name="B")])
    # Active EDGE profile defines true ROI; a BONUS campaign profile holds the
    # bonus-extraction play.
    s.add(Profile(id=1, name="edge", kind="edge", is_active=True))
    s.add(Profile(id=2, name="camp", kind="bonus", is_active=False))
    s.commit()
    return s


def _edge_value_bet(s):
    # Genuine edge bet: stake 100 @ 2.0, won -> profit 100, staked 100.
    s.add(
        Bet(
            profile_id=1,
            provider_id="pinnacle",
            odds=2.0,
            stake=100,
            currency="SEK",
            result="won",
            payout=200,
            is_bonus=False,
        )
    )


def _bonus_play(s, hedge_result="won"):
    # Soft free-bet leg (is_bonus): stake 1000 @ 3.0.
    s.add(
        Bet(
            profile_id=2,
            provider_id="betinia",
            odds=3.0,
            stake=1000,
            currency="SEK",
            result="lost" if hedge_result == "won" else "won",
            payout=0 if hedge_result == "won" else 3000,
            is_bonus=True,
        )
    )
    # Real-money sharp HEDGE leg (NOT is_bonus — classified bonus only via profile.kind):
    # stake 1290 @ 1.55.
    s.add(
        Bet(
            profile_id=2,
            provider_id="pinnacle",
            odds=1.55,
            stake=1290,
            currency="SEK",
            result=hedge_result,
            payout=1290 * 1.55 if hedge_result == "won" else 0,
            is_bonus=False,
        )
    )


def test_roi_excludes_bonus_profile_legs(session):
    s = session
    _edge_value_bet(s)
    _bonus_play(s, hedge_result="won")
    s.commit()

    stats = BankrollService(s).get_stats()
    # ROI denominator must be the edge bet ONLY (100), not 100 + 1290 hedge.
    assert stats["total_staked"] == 100
    assert stats["total_profit"] == 100
    assert stats["roi_pct"] == 100.0
    assert stats["total_bets"] == 1  # only the edge bet counts


def test_bonus_profit_captures_both_legs(session):
    s = session
    _bonus_play(s, hedge_result="won")
    s.commit()
    stats = BankrollService(s).get_stats()
    # hedge won: +709.5 (1290*1.55 - 1290); soft free-bet leg lost: 0 (no stake loss).
    assert stats["bonus_profit"] == pytest.approx(709.5, abs=0.5)
    # bonus play must not leak into ROI
    assert stats["total_staked"] == 0
    assert stats["roi_pct"] == 0


def test_stray_is_bonus_bet_on_edge_profile_counts_as_bonus_profit(session):
    """A free-bet (is_bonus=True) placed on the EDGE profile must land in
    bonus_profit, not vanish. It is excluded from ROI (is_bonus) and the edge
    profile isn't kind='bonus', so without the is_bonus union its profit would be
    reported nowhere."""
    s = session
    _edge_value_bet(s)  # genuine edge bet: +100 profit, 100 staked
    # Stray free-bet on the EDGE profile (profile_id=1): won @ 2.0, payout 200.
    # Bonus stake is free, so profit = payout = 200.
    s.add(
        Bet(
            profile_id=1,
            provider_id="betinia",
            odds=2.0,
            stake=200,
            currency="SEK",
            result="won",
            payout=200,
            is_bonus=True,
        )
    )
    s.commit()

    stats = BankrollService(s).get_stats()
    # ROI is the edge bet alone — the stray free-bet is excluded.
    assert stats["total_staked"] == 100
    assert stats["roi_pct"] == 100.0
    # The free-bet's profit is captured in bonus_profit, not lost.
    assert stats["bonus_profit"] == pytest.approx(200.0, abs=0.5)


def test_rule_b_roi_invariant_to_hedge_outcome(session):
    """The whole point: flipping which leg of the bonus play won must NOT move
    true ROI (only redistribute within bonus_profit)."""
    s = session
    _edge_value_bet(s)
    _bonus_play(s, hedge_result="won")
    s.commit()
    roi_a = BankrollService(s).get_stats()["roi_pct"]

    # rebuild with the other outcome
    s2_eng = create_engine("sqlite://")
    Base.metadata.create_all(s2_eng)
    s2 = sessionmaker(bind=s2_eng)()
    s2.add_all([Provider(id="pinnacle", name="Pin"), Provider(id="betinia", name="B")])
    s2.add(Profile(id=1, name="edge", kind="edge", is_active=True))
    s2.add(Profile(id=2, name="camp", kind="bonus", is_active=False))
    s2.commit()
    _edge_value_bet(s2)
    _bonus_play(s2, hedge_result="lost")
    s2.commit()
    roi_b = BankrollService(s2).get_stats()["roi_pct"]

    assert roi_a == roi_b == 100.0  # edge ROI unchanged regardless of hedge outcome
