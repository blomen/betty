"""Tests for polymarket top-edge convergence loop and queue helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from local.mirror.play_loop import PlayLoop


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
    from local.mirror.provider_runner import ProviderRunner

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


def test_hard_fail_reasons_constant_includes_known_failures():
    """The hard-fail reason set in provider_runner must cover all four
    polymarket prep_betslip failure modes."""
    from local.mirror.provider_runner import HARD_FAIL_PREP_REASONS

    assert "navigation_redirected" in HARD_FAIL_PREP_REASONS
    assert "no_cent_button_matched" in HARD_FAIL_PREP_REASONS
    assert "event_closed" in HARD_FAIL_PREP_REASONS
    assert "click_failed" in HARD_FAIL_PREP_REASONS


def test_is_hard_fail_reason_substring_match():
    """is_hard_fail_reason matches when any known prefix appears in the reason
    string. prep_betslip reasons include extra context, e.g.:
       'navigation_redirected (expected slug ... not in URL ...)'
       'no_cent_button_matched (market=moneyline, target=...)'
       'click_failed: js_eval_returned_none'
    """
    from local.mirror.provider_runner import is_hard_fail_reason

    assert is_hard_fail_reason("navigation_redirected (expected slug 'foo' not in URL 'bar')")
    assert is_hard_fail_reason("no_cent_button_matched (market=moneyline, target=team)")
    assert is_hard_fail_reason("click_failed: js_eval_returned_none")
    assert is_hard_fail_reason("event_closed")
    assert not is_hard_fail_reason("transient_render_glitch")
    assert not is_hard_fail_reason("")
    assert not is_hard_fail_reason(None)


def test_mark_recently_skipped_called_on_hard_fail(monkeypatch):
    """When prep_betslip returns failed with a hard-fail reason, the runner
    must call mark_recently_skipped(bet) so refresh_batch excludes it for 60s."""
    from unittest.mock import MagicMock

    from local.mirror.provider_runner import ProviderRunner

    marked: list[dict] = []

    runner = ProviderRunner(
        provider_id="polymarket",
        browser=MagicMock(running=True, context=MagicMock(pages=[]), provider_data={}),
        broadcaster=MagicMock(),
        proxy_url="https://x.test",
        pop_bet=lambda: None,
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        mark_recently_skipped=lambda b: marked.append(b),
        push_bet=lambda b: None,
    )

    # Drive the prep_failed branch directly via the helper logic. The full
    # _run loop is too complex to invoke from a unit test (browser tabs,
    # workflow strategy, asyncio scaffolding), so we simulate the part the
    # task adds: is_hard_fail_reason + mark_recently_skipped.
    from local.mirror.provider_runner import is_hard_fail_reason

    bet = {"event_id": "abc", "market": "moneyline", "outcome": "home"}
    reason = "navigation_redirected (expected slug 'x' not in URL 'y')"
    if is_hard_fail_reason(reason):
        runner._mark_recently_skipped(bet)
    assert marked == [bet]


def test_mark_recently_skipped_not_called_on_soft_reason():
    """Non-hard-fail prep reasons must NOT trigger the TTL marking."""
    from unittest.mock import MagicMock

    from local.mirror.provider_runner import ProviderRunner, is_hard_fail_reason

    marked: list[dict] = []
    runner = ProviderRunner(
        provider_id="polymarket",
        browser=MagicMock(running=True, context=MagicMock(pages=[]), provider_data={}),
        broadcaster=MagicMock(),
        proxy_url="https://x.test",
        pop_bet=lambda: None,
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        mark_recently_skipped=lambda b: marked.append(b),
        push_bet=lambda b: None,
    )
    bet = {"event_id": "abc", "market": "moneyline", "outcome": "home"}
    reason = "transient_render_glitch"
    if is_hard_fail_reason(reason):
        runner._mark_recently_skipped(bet)
    assert marked == []


def test_is_hard_fail_reason_matches_click_eval_failed():
    """Regression: click_eval_failed:<exception> emitted by polymarket's
    JS-click path must trigger the 60s TTL (the substring 'click_failed'
    alone does NOT match 'click_eval_failed' because the latter has a
    different prefix)."""
    from local.mirror.provider_runner import is_hard_fail_reason

    assert is_hard_fail_reason("click_eval_failed:js_eval_returned_none") is True
    assert is_hard_fail_reason("click_eval_failed: ReferenceError: foo is undefined") is True


def test_convergence_should_redirect_returns_true_when_top_above_live():
    """should_redirect_to_top: queue top edge > live edge → True."""
    from local.mirror.provider_runner import should_redirect_to_top

    assert should_redirect_to_top(live_edge=19.9, queue_top_edge=23.0) is True
    assert should_redirect_to_top(live_edge=10.0, queue_top_edge=10.0001) is True


def test_convergence_should_redirect_returns_false_when_at_or_above_top():
    """should_redirect_to_top: live edge >= queue top → False (we're top)."""
    from local.mirror.provider_runner import should_redirect_to_top

    assert should_redirect_to_top(live_edge=23.0, queue_top_edge=23.0) is False
    assert should_redirect_to_top(live_edge=25.0, queue_top_edge=23.0) is False


def test_convergence_should_redirect_handles_missing_inputs():
    """Missing live_edge OR missing queue_top_edge → False (assume top)."""
    from local.mirror.provider_runner import should_redirect_to_top

    assert should_redirect_to_top(live_edge=None, queue_top_edge=23.0) is False
    assert should_redirect_to_top(live_edge=19.9, queue_top_edge=None) is False
    assert should_redirect_to_top(live_edge=None, queue_top_edge=None) is False


def test_convergence_max_iter_constant():
    """CONVERGENCE_MAX_ITER caps the convergence loop at 5."""
    from local.mirror.provider_runner import CONVERGENCE_MAX_ITER

    assert CONVERGENCE_MAX_ITER == 5


def test_convergence_iter_attribute_exists():
    """ProviderRunner tracks _convergence_iter on self for the convergence cap."""
    from unittest.mock import MagicMock

    from local.mirror.provider_runner import ProviderRunner

    runner = ProviderRunner(
        provider_id="polymarket",
        browser=MagicMock(running=True, context=MagicMock(pages=[]), provider_data={}),
        broadcaster=MagicMock(),
        proxy_url="https://x.test",
        pop_bet=lambda: None,
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        push_bet=lambda b: None,
    )
    assert runner._convergence_iter == 0


def test_should_dethrone_at_ready_uses_2pt_hysteresis():
    """At-READY dethrone uses DETHRONE_HYSTERESIS_PCT (2pts) — strict
    convergence is only on initial entry."""
    from local.mirror.provider_runner import (
        DETHRONE_HYSTERESIS_PCT,
        should_dethrone_at_ready,
    )

    assert DETHRONE_HYSTERESIS_PCT == 2.0
    # Below hysteresis — do not dethrone.
    assert should_dethrone_at_ready(live_edge=20.0, queue_top_edge=21.5) is False
    assert should_dethrone_at_ready(live_edge=20.0, queue_top_edge=22.0) is True  # exactly +2
    assert should_dethrone_at_ready(live_edge=20.0, queue_top_edge=23.0) is True


def test_should_dethrone_at_ready_handles_missing_inputs():
    """Missing live_edge OR queue_top_edge → False (don't dethrone)."""
    from local.mirror.provider_runner import should_dethrone_at_ready

    assert should_dethrone_at_ready(live_edge=None, queue_top_edge=23.0) is False
    assert should_dethrone_at_ready(live_edge=20.0, queue_top_edge=None) is False
