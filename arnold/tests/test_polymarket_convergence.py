"""Tests for polymarket top-edge convergence loop and queue helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from arnold.mirror.play_loop import PlayLoop


def _make_loop() -> PlayLoop:
    return PlayLoop(
        browser=MagicMock(running=False, context=None, provider_data={}),
        broadcaster=MagicMock(publish=MagicMock()),
        proxy_url="https://x.test",
    )


def _bet(event_id: str, edge: float, market: str = "moneyline", outcome: str = "home") -> dict:
    return {
        "event_id": event_id,
        "provider_id": "polymarket",
        "market": market,
        "outcome": outcome,
        "edge_pct": edge,
        "fair_odds": 2.0,
        "stake": 10.0,
    }


def test_make_push_bet_appends_and_sorts_desc():
    """push_bet inserts a bet and re-sorts the cluster queue by edge_pct desc."""
    loop = _make_loop()
    loop._cluster_queues["polymarket"] = [_bet("a", 10.0), _bet("b", 5.0)]
    push = loop._make_push_bet("polymarket")
    push(_bet("c", 7.5))
    edges = [b["edge_pct"] for b in loop._cluster_queues["polymarket"]]
    assert edges == [10.0, 7.5, 5.0]


def test_make_push_bet_replaces_existing_key_in_place():
    """If a bet with the same (event_id, market, outcome) is in the queue,
    push_bet replaces its edge_pct instead of duplicating."""
    loop = _make_loop()
    loop._cluster_queues["polymarket"] = [_bet("a", 23.0), _bet("b", 5.0)]
    push = loop._make_push_bet("polymarket")
    push(_bet("a", 18.0))  # same event_id, lower edge
    queue = loop._cluster_queues["polymarket"]
    assert len(queue) == 2  # not duplicated
    by_id = {b["event_id"]: b["edge_pct"] for b in queue}
    assert by_id == {"a": 18.0, "b": 5.0}
    edges = [b["edge_pct"] for b in queue]
    assert edges == [18.0, 5.0]


def test_make_push_bet_updates_queue_total():
    """push_bet bumps _queue_total when adding a new bet."""
    loop = _make_loop()
    loop._cluster_queues["polymarket"] = [_bet("a", 10.0)]
    loop._queue_total = 1
    push = loop._make_push_bet("polymarket")
    push(_bet("b", 5.0))
    assert loop._queue_total == 2


def test_make_push_bet_no_total_bump_on_replace():
    """Replacing an existing bet must not bump _queue_total."""
    loop = _make_loop()
    loop._cluster_queues["polymarket"] = [_bet("a", 10.0)]
    loop._queue_total = 1
    push = loop._make_push_bet("polymarket")
    push(_bet("a", 5.0))
    assert loop._queue_total == 1


def test_provider_runner_accepts_push_bet_param():
    """ProviderRunner constructor accepts push_bet callable."""
    from arnold.mirror.provider_runner import ProviderRunner

    push_calls = []

    def push(bet: dict) -> None:
        push_calls.append(bet)

    runner = ProviderRunner(
        provider_id="polymarket",
        browser=MagicMock(running=True, context=MagicMock(pages=[]), provider_data={}),
        broadcaster=MagicMock(),
        proxy_url="https://x.test",
        pop_bet=lambda: None,
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        push_bet=push,
    )
    assert runner._push_bet is push
    runner._push_bet({"event_id": "x", "edge_pct": 5.0})
    assert push_calls == [{"event_id": "x", "edge_pct": 5.0}]
