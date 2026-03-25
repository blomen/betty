"""Test capital plan generates correct recommendation types and priorities."""
from src.services.batch_builder import BatchBet, ProviderBalance, BatchBuilder, SHARP_PROVIDERS


def _make_balance(pid, cluster, balance, lifecycle="playing", wagering_remaining=0, days_remaining=None):
    pb = ProviderBalance(pid, cluster, balance, lifecycle=lifecycle)
    pb.wagering_remaining = wagering_remaining
    pb.days_remaining = days_remaining
    return pb


def _make_missed_bet(provider_id, cluster, tier, stake=500, ev=40):
    return BatchBet(
        rank=0, tier=tier, provider_id=provider_id,
        event_id="e1", market="1x2", outcome="Home", point=None,
        odds=2.0, fair_odds=1.85, edge_pct=8.0, stake=stake,
        expected_profit=ev, is_bonus=False, bonus_type=None,
        display_home="A", display_away="B", sport="football",
        league="Test", start_time=None, lifecycle="playing",
        cluster=cluster, skip_reason="insufficient balance",
    )


def test_sharp_shortfall_is_priority_1():
    balances = {"polymarket": _make_balance("polymarket", "polymarket", 100)}
    balances["polymarket"].missed_bets = 3
    balances["polymarket"].missed_ev = 50.0
    missed = [_make_missed_bet("polymarket", "polymarket", "polymarket", 200, 25)]

    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances, missed=missed, total_bankroll=10000,
        cluster_opp_stats={}, avg_daily_wager=1000, has_wager_history=True,
    )
    sharp_deposits = [a for a in plan["actions"] if a["provider_id"] == "polymarket" and a["type"] == "deposit"]
    assert len(sharp_deposits) >= 1
    assert sharp_deposits[0]["priority"] == 1
    assert sharp_deposits[0]["currency"] == "USDC"


def test_withdraw_dormant():
    balances = {"spectate": _make_balance("spectate", "spectate", 3000, lifecycle="dormant")}
    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances, missed=[], total_bankroll=10000,
        cluster_opp_stats={}, avg_daily_wager=1000, has_wager_history=True,
    )
    withdrawals = [a for a in plan["actions"] if a["type"] == "withdraw"]
    assert len(withdrawals) >= 1
    assert withdrawals[0]["provider_id"] == "spectate"


def test_transfer_from_excess_to_shortfall():
    balances = {
        "spectate": _make_balance("spectate", "spectate", 3000, lifecycle="dormant"),
        "unibet": _make_balance("unibet", "kambi", 0, lifecycle="playing"),
    }
    balances["unibet"].missed_bets = 5
    balances["unibet"].missed_ev = 80.0
    missed = [_make_missed_bet("unibet", "kambi", "soft")]

    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances, missed=missed, total_bankroll=10000,
        cluster_opp_stats={"kambi": {"unique_opps": 5, "total_ev": 80, "avg_edge": 8.0, "avg_stake": 500}},
        avg_daily_wager=1000, has_wager_history=True,
    )
    transfers = [a for a in plan["actions"] if a["type"] == "transfer"]
    assert len(transfers) >= 1
    assert transfers[0]["from_provider_id"] == "spectate"


def test_actions_sorted_by_priority():
    balances = {
        "polymarket": _make_balance("polymarket", "polymarket", 100),
        "spectate": _make_balance("spectate", "spectate", 3000, lifecycle="dormant"),
        "unibet": _make_balance("unibet", "kambi", 0, lifecycle="playing"),
    }
    balances["polymarket"].missed_bets = 2
    balances["polymarket"].missed_ev = 30.0
    balances["unibet"].missed_bets = 3
    balances["unibet"].missed_ev = 40.0
    missed = [
        _make_missed_bet("polymarket", "polymarket", "polymarket"),
        _make_missed_bet("unibet", "kambi", "soft"),
    ]

    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances, missed=missed, total_bankroll=10000,
        cluster_opp_stats={}, avg_daily_wager=1000, has_wager_history=True,
    )
    priorities = [a["priority"] for a in plan["actions"]]
    assert priorities == sorted(priorities), "Actions must be sorted by priority"


def test_bonus_deposit_priority_2():
    """Provider with active bonus wagering gets priority 2."""
    balances = {
        "unibet": _make_balance("unibet", "kambi", 0, lifecycle="wagering",
                                wagering_remaining=5000, days_remaining=30),
    }
    balances["unibet"].missed_bets = 2
    balances["unibet"].missed_ev = 30.0
    missed = [_make_missed_bet("unibet", "kambi", "soft")]

    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances, missed=missed, total_bankroll=10000,
        cluster_opp_stats={}, avg_daily_wager=1000, has_wager_history=True,
    )
    bonus_deposits = [a for a in plan["actions"] if a["type"] == "deposit" and a["priority"] == 2]
    assert len(bonus_deposits) >= 1


def test_infeasible_bonus_skipped():
    """Provider with active bonus but impossible wagering timeline gets skipped."""
    balances = {
        "unibet": _make_balance("unibet", "kambi", 0, lifecycle="wagering",
                                wagering_remaining=50000, days_remaining=2),
    }
    balances["unibet"].missed_bets = 2
    balances["unibet"].missed_ev = 30.0
    missed = [_make_missed_bet("unibet", "kambi", "soft")]

    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances, missed=missed, total_bankroll=10000,
        cluster_opp_stats={}, avg_daily_wager=1000, has_wager_history=True,
    )
    # Should have no bonus deposit actions (infeasible)
    bonus_deposits = [a for a in plan["actions"] if a["type"] == "deposit" and a["priority"] == 2]
    assert len(bonus_deposits) == 0


def test_no_actions_when_fully_allocated():
    """Fully allocated provider with no missed bets = no actions."""
    balances = {
        "unibet": _make_balance("unibet", "kambi", 5000, lifecycle="playing"),
    }
    # Simulate full allocation
    balances["unibet"].allocated = 5000
    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances, missed=[], total_bankroll=10000,
        cluster_opp_stats={}, avg_daily_wager=1000, has_wager_history=True,
    )
    assert plan["actions"] == []


def test_idle_playing_provider_gets_withdraw():
    """Playing provider with 0 allocated bets should get withdraw recommendation."""
    balances = {
        "unibet": _make_balance("unibet", "kambi", 5000, lifecycle="playing"),
    }
    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances, missed=[], total_bankroll=10000,
        cluster_opp_stats={}, avg_daily_wager=1000, has_wager_history=True,
    )
    withdrawals = [a for a in plan["actions"] if a["type"] == "withdraw"]
    assert len(withdrawals) == 1
    assert withdrawals[0]["provider_id"] == "unibet"
    assert withdrawals[0]["amount"] == 5000


def test_polymarket_uses_usdc():
    """Polymarket deposits must use USDC currency."""
    balances = {"polymarket": _make_balance("polymarket", "polymarket", 0)}
    balances["polymarket"].missed_bets = 1
    balances["polymarket"].missed_ev = 20.0
    missed = [_make_missed_bet("polymarket", "polymarket", "polymarket")]

    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances, missed=missed, total_bankroll=10000,
        cluster_opp_stats={}, avg_daily_wager=1000, has_wager_history=True,
    )
    deposits = [a for a in plan["actions"] if a["type"] == "deposit"]
    for d in deposits:
        if d["provider_id"] == "polymarket":
            assert d["currency"] == "USDC"


def test_pinnacle_uses_sek():
    """Pinnacle deposits use SEK."""
    balances = {"pinnacle": _make_balance("pinnacle", "pinnacle", 0)}
    balances["pinnacle"].missed_bets = 1
    balances["pinnacle"].missed_ev = 20.0
    missed = [_make_missed_bet("pinnacle", "pinnacle", "pinnacle")]

    plan = BatchBuilder._build_capital_plan_v3(
        provider_balances=balances, missed=missed, total_bankroll=10000,
        cluster_opp_stats={}, avg_daily_wager=1000, has_wager_history=True,
    )
    deposits = [a for a in plan["actions"] if a["type"] == "deposit" and a["provider_id"] == "pinnacle"]
    assert len(deposits) >= 1
    assert deposits[0]["currency"] == "SEK"
