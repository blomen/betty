"""Test _allocate_batch distributes bets across cluster siblings."""
from src.services.batch_builder import BatchBet, ProviderBalance, BatchBuilder


def _make_bet(provider_id: str, cluster: str, event_id: str, edge: float, stake: float) -> BatchBet:
    return BatchBet(
        rank=0, tier="soft", provider_id=provider_id,
        event_id=event_id, market="1x2", outcome="Home", point=None,
        odds=2.0, fair_odds=1.9, edge_pct=edge,
        stake=stake, expected_profit=stake * edge / 100,
        is_bonus=False, bonus_type=None,
        display_home="Team A", display_away="Team B",
        sport="football", league="Test", start_time=None,
        detected_at=None, odds_age_minutes=None,
        lifecycle="available", cluster=cluster,
    )


def _make_sharp_bet(provider_id: str, event_id: str, edge: float, stake: float) -> BatchBet:
    tier = "polymarket" if provider_id == "polymarket" else "pinnacle"
    return BatchBet(
        rank=0, tier=tier, provider_id=provider_id,
        event_id=event_id, market="1x2", outcome="Home", point=None,
        odds=2.0, fair_odds=1.9, edge_pct=edge,
        stake=stake, expected_profit=stake * edge / 100,
        is_bonus=False, bonus_type=None,
        display_home="Team A", display_away="Team B",
        sport="football", league="Test", start_time=None,
        detected_at=None, odds_age_minutes=None,
        lifecycle="available", cluster=provider_id,
    )


def test_soft_drains_best_funded_first():
    """Soft bets drain the highest-balance sibling first."""
    candidates = [_make_bet("unibet", "kambi", f"evt_{i}", 5.0, 100) for i in range(3)]

    balances = {
        "unibet": ProviderBalance("unibet", "kambi", 500.0),
        "888sport": ProviderBalance("888sport", "kambi", 200.0),
    }

    builder = BatchBuilder.__new__(BatchBuilder)
    batch, missed = builder._allocate_batch(candidates, balances, {"unibet", "888sport"})

    assert len(batch) == 3
    assert all(b.funded for b in batch)
    # All on unibet (highest balance)
    assert all(b.provider_id == "unibet" for b in batch)


def test_soft_spreads_when_cap_reached():
    """When one sibling hits the 10-bet cap, bets overflow to next sibling."""
    candidates = [_make_bet("unibet", "kambi", f"evt_{i}", 5.0, 100) for i in range(12)]

    balances = {
        "unibet": ProviderBalance("unibet", "kambi", 5000.0),
        "888sport": ProviderBalance("888sport", "kambi", 5000.0),
    }

    builder = BatchBuilder.__new__(BatchBuilder)
    batch, missed = builder._allocate_batch(candidates, balances, {"unibet", "888sport"})

    assert len(batch) == 12
    assert all(b.funded for b in batch)
    providers_used = {b.provider_id for b in batch}
    assert len(providers_used) == 2  # Must use both siblings


def test_soft_falls_back_on_insufficient_balance():
    """If preferred sibling has no balance, fall back to next sibling."""
    candidates = [_make_bet("unibet", "kambi", "evt_1", 5.0, 400)]

    balances = {
        "unibet": ProviderBalance("unibet", "kambi", 100.0),
        "888sport": ProviderBalance("888sport", "kambi", 500.0),
    }

    builder = BatchBuilder.__new__(BatchBuilder)
    batch, missed = builder._allocate_batch(candidates, balances, {"unibet", "888sport"})

    assert len(batch) == 1
    assert batch[0].provider_id == "888sport"
    assert batch[0].funded


def test_freebet_skips_balance_check():
    """Freebets placed even with zero balance."""
    candidates = [_make_bet("unibet", "kambi", "evt_1", 5.0, 100)]

    balances = {
        "unibet": ProviderBalance(
            "unibet", "kambi", 0.0,
            is_bonus_phase=True, bonus_amount=100.0,
        ),
    }

    builder = BatchBuilder.__new__(BatchBuilder)
    batch, missed = builder._allocate_batch(candidates, balances, {"unibet"})

    assert len(batch) == 1
    assert batch[0].provider_id == "unibet"
    assert batch[0].funded
    assert batch[0].is_bonus
    assert batch[0].bonus_type == "freebet"
    assert batch[0].stake == 100.0  # bonus_amount override


def test_all_insufficient_goes_to_missed():
    """If no provider in cluster can fund, bet goes to missed."""
    candidates = [_make_bet("unibet", "kambi", "evt_1", 5.0, 600)]

    balances = {
        "unibet": ProviderBalance("unibet", "kambi", 100.0),
        "888sport": ProviderBalance("888sport", "kambi", 100.0),
    }

    builder = BatchBuilder.__new__(BatchBuilder)
    batch, missed = builder._allocate_batch(candidates, balances, {"unibet", "888sport"})

    assert len(batch) == 0
    assert len(missed) == 1
    assert not missed[0].funded


def test_sharp_allocated_independently():
    """Sharp bets go to their own provider, not siblings."""
    candidates = [
        _make_sharp_bet("pinnacle", "evt_1", 5.0, 200),
        _make_sharp_bet("polymarket", "evt_2", 4.0, 150),
    ]

    balances = {
        "pinnacle": ProviderBalance("pinnacle", "pinnacle", 1000.0),
        "polymarket": ProviderBalance("polymarket", "polymarket", 500.0),
    }

    builder = BatchBuilder.__new__(BatchBuilder)
    batch, missed = builder._allocate_batch(candidates, balances, {"pinnacle", "polymarket"})

    assert len(batch) == 2
    assert all(b.funded for b in batch)
    assert {b.provider_id for b in batch} == {"pinnacle", "polymarket"}


def test_sharp_missed_when_no_balance():
    """Sharp bets with no balance go to missed."""
    candidates = [_make_sharp_bet("pinnacle", "evt_1", 5.0, 200)]

    # No balance for pinnacle
    balances = {}

    builder = BatchBuilder.__new__(BatchBuilder)
    batch, missed = builder._allocate_batch(candidates, balances, set())

    assert len(batch) == 0
    assert len(missed) == 1
    assert not missed[0].funded


def test_trigger_respects_min_odds():
    """Trigger provider skips bets below min_odds."""
    # Bet with odds 1.5 — below trigger min_odds of 1.80
    bet = _make_bet("unibet", "kambi", "evt_1", 5.0, 100)
    bet.odds = 1.5  # Below trigger threshold

    balances = {
        "unibet": ProviderBalance(
            "unibet", "kambi", 500.0,
            lifecycle="deposited", trigger_mode="single",
            min_odds=1.80, bonus_amount=50.0,
        ),
    }

    builder = BatchBuilder.__new__(BatchBuilder)
    batch, missed = builder._allocate_batch([bet], balances, {"unibet"})

    # Should be missed — odds too low for trigger
    assert len(batch) == 0
    assert len(missed) == 1


def test_trigger_accepts_qualifying_odds():
    """Trigger provider accepts bets above min_odds."""
    bet = _make_bet("unibet", "kambi", "evt_1", 5.0, 100)
    bet.odds = 2.0  # Above trigger threshold

    balances = {
        "unibet": ProviderBalance(
            "unibet", "kambi", 500.0,
            lifecycle="deposited", trigger_mode="single",
            min_odds=1.80, bonus_amount=50.0,
        ),
    }

    builder = BatchBuilder.__new__(BatchBuilder)
    batch, missed = builder._allocate_batch([bet], balances, {"unibet"})

    assert len(batch) == 1
    assert batch[0].funded
    assert batch[0].bonus_type == "trigger"
    assert batch[0].stake == 50.0  # bonus_amount override
