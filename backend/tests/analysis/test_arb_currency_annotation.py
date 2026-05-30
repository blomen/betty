"""Arb legs and BatchBets carry currency + native-stake annotations so
cross-currency arbs (cloudbet USDC vs pinnacle SEK) can be placed correctly."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC

from src.services.batch_builder import BatchBet


def test_batchbet_has_currency_fields():
    """BatchBet must expose stake_currency + stake_native alongside stake (SEK)."""
    field_names = {f.name for f in fields(BatchBet)}
    assert "stake_currency" in field_names, "BatchBet missing stake_currency field"
    assert "stake_native" in field_names, "BatchBet missing stake_native field"


def test_arb_leg_has_currency_field():
    """Each leg dict in ArbOpportunity.legs must include 'currency'."""
    from datetime import datetime
    from types import SimpleNamespace

    from src.analysis.scanner import OpportunityScanner

    scanner = OpportunityScanner(session=None)
    odds_pinnacle_home = SimpleNamespace(
        provider_id="pinnacle",
        market="moneyline",
        outcome="home",
        odds=2.56,
        point=None,
        scope="ft",
        updated_at=datetime.now(UTC),
        bid=None,
        ask=None,
        max_stake=None,
        depth_usd=None,
    )
    odds_pinnacle_away = SimpleNamespace(
        provider_id="pinnacle",
        market="moneyline",
        outcome="away",
        odds=1.60,
        point=None,
        scope="ft",
        updated_at=datetime.now(UTC),
        bid=None,
        ask=None,
        max_stake=None,
        depth_usd=None,
    )
    # cloudbet quotes both outcomes so outcome-count matches pinnacle (2 vs 2),
    # avoiding the market-type-mismatch filter. Odds beat pinnacle devigged fair
    # on away (≈1.575) and home (≈2.462), and sum < 1 → guaranteed arb profit.
    odds_cloudbet_home = SimpleNamespace(
        provider_id="cloudbet",
        market="moneyline",
        outcome="home",
        odds=2.65,
        point=None,
        scope="ft",
        updated_at=datetime.now(UTC),
        bid=None,
        ask=None,
        max_stake=None,
        depth_usd=None,
    )
    odds_cloudbet_away = SimpleNamespace(
        provider_id="cloudbet",
        market="moneyline",
        outcome="away",
        odds=1.71,
        point=None,
        scope="ft",
        updated_at=datetime.now(UTC),
        bid=None,
        ask=None,
        max_stake=None,
        depth_usd=None,
    )
    event = SimpleNamespace(
        id="evt:test",
        sport="basketball",
        home_team="A",
        away_team="B",
        league="Test",
        start_time=None,
        odds=[odds_pinnacle_home, odds_pinnacle_away, odds_cloudbet_home, odds_cloudbet_away],
        home_away_validated=True,
    )
    arbs = scanner.scan_arb(events=[event])
    assert arbs, "expected at least one arb in fixture"
    for arb in arbs:
        for leg in arb.legs:
            assert "currency" in leg, f"arb leg missing currency: {leg}"
            assert leg["currency"] in ("SEK", "USDC", "USD", "GBP"), f"unexpected currency: {leg['currency']}"
