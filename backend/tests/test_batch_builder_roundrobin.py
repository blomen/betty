"""Test round-robin distributes bets across cluster siblings."""
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
        lifecycle="playing", cluster=cluster,
    )


def test_below_threshold_uses_best_funded():
    """Below ROUND_ROBIN_THRESHOLD, all bets go to best-funded provider."""
    candidates = []
    for evt in ["evt_1", "evt_2", "evt_3"]:
        for pid, edge_offset in [("unibet", 0), ("888sport", -0.2), ("leovegas", -0.5)]:
            candidates.append(_make_bet(pid, "kambi", evt, 5.0 + edge_offset, 100))

    balances = {
        "unibet": ProviderBalance("unibet", "kambi", 500.0),
        "888sport": ProviderBalance("888sport", "kambi", 500.0),
        "leovegas": ProviderBalance("leovegas", "kambi", 500.0),
    }

    ranked = sorted(candidates, key=lambda b: -b.expected_profit)
    batch, missed = BatchBuilder._allocate_with_round_robin(ranked, balances)

    assert len(batch) == 3
    # All on unibet (best-funded, below threshold so no spreading)
    providers_used = {b.provider_id for b in batch}
    assert providers_used == {"unibet"}


def test_round_robin_alternates_above_threshold():
    """Above ROUND_ROBIN_THRESHOLD, bets spread across providers."""
    candidates = []
    for i in range(12):  # 12 > threshold of 10
        for pid, edge_offset in [("unibet", 0), ("888sport", -0.2), ("leovegas", -0.5)]:
            candidates.append(_make_bet(pid, "kambi", f"evt_{i}", 5.0 + edge_offset, 100))

    balances = {
        "unibet": ProviderBalance("unibet", "kambi", 5000.0),
        "888sport": ProviderBalance("888sport", "kambi", 5000.0),
        "leovegas": ProviderBalance("leovegas", "kambi", 5000.0),
    }

    ranked = sorted(candidates, key=lambda b: -b.expected_profit)
    batch, missed = BatchBuilder._allocate_with_round_robin(ranked, balances)

    assert len(batch) == 12
    providers_used = {b.provider_id for b in batch}
    assert len(providers_used) >= 2


def test_round_robin_falls_back_on_insufficient_balance():
    """If preferred provider has no balance, fall back to sibling."""
    candidates = [
        _make_bet("unibet", "kambi", "evt_1", 5.0, 400),
        _make_bet("888sport", "kambi", "evt_1", 4.8, 400),
    ]

    balances = {
        "unibet": ProviderBalance("unibet", "kambi", 100.0),  # Not enough
        "888sport": ProviderBalance("888sport", "kambi", 500.0),  # Enough
    }

    ranked = sorted(candidates, key=lambda b: -b.expected_profit)
    batch, missed = BatchBuilder._allocate_with_round_robin(ranked, balances)

    assert len(batch) == 1
    assert batch[0].provider_id == "888sport"  # Fell back to funded sibling


def test_round_robin_freebet_skips_balance_check():
    """Freebets should be placed regardless of balance."""
    bet = _make_bet("unibet", "kambi", "evt_1", 5.0, 100)
    bet.is_bonus = True
    bet.bonus_type = "freebet"

    balances = {
        "unibet": ProviderBalance("unibet", "kambi", 0.0),  # No balance
    }

    batch, missed = BatchBuilder._allocate_with_round_robin([bet], balances)

    assert len(batch) == 1
    assert batch[0].provider_id == "unibet"


def test_round_robin_all_insufficient_goes_to_missed():
    """If no provider in cluster has enough balance, bet goes to missed."""
    candidates = [
        _make_bet("unibet", "kambi", "evt_1", 5.0, 600),
        _make_bet("888sport", "kambi", "evt_1", 4.8, 600),
    ]

    balances = {
        "unibet": ProviderBalance("unibet", "kambi", 100.0),
        "888sport": ProviderBalance("888sport", "kambi", 100.0),
    }

    ranked = sorted(candidates, key=lambda b: -b.expected_profit)
    batch, missed = BatchBuilder._allocate_with_round_robin(ranked, balances)

    assert len(batch) == 0
    assert len(missed) == 1
