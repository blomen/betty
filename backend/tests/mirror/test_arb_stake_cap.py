"""Anchor stake cap tests — ArbRunner must respect _stake_caps when sizing the anchor."""

from __future__ import annotations

import pytest


@pytest.fixture
def runner_with_balance():
    """Build an ArbRunner with a fake browser whose betinia balance is 200 SEK."""
    from arnold.mirror.arb_runner import ArbRunner

    class _FakeBrowser:
        provider_data = {"betinia": {"balance": 200.0}}
        context = None

    def _block(_b):  # pragma: no cover
        pass

    def _is_blocked(_b):
        return False

    class _FakeBroadcaster:
        def publish(self, *_a, **_k):
            pass

    def _build(stake_caps: dict[str, float] | None):
        return ArbRunner(
            provider_id="betinia",
            browser=_FakeBrowser(),
            broadcaster=_FakeBroadcaster(),
            proxy_url="http://localhost:18000",
            block_event_market=_block,
            is_blocked=_is_blocked,
            placed_today={},
            active_providers=["betinia", "pinnacle"],
            stake_caps=stake_caps,
        )

    return _build


def _compute_anchor_stake(runner) -> float:
    """Lift the stake calc from _load_all_legs so we can unit-test it without async."""
    balance = runner._browser.provider_data.get(runner.provider_id, {}).get("balance") or 0.0
    cap = runner._stake_caps.get(runner.provider_id)
    return round(min(balance, cap) if cap else balance, 2)


def test_anchor_stake_uses_balance_when_no_cap(runner_with_balance):
    runner = runner_with_balance(stake_caps={})
    assert _compute_anchor_stake(runner) == 200.0


def test_anchor_stake_clamped_to_cap_when_cap_lower(runner_with_balance):
    runner = runner_with_balance(stake_caps={"betinia": 50.0})
    assert _compute_anchor_stake(runner) == 50.0


def test_anchor_stake_uses_balance_when_balance_lower(runner_with_balance):
    runner = runner_with_balance(stake_caps={"betinia": 500.0})
    assert _compute_anchor_stake(runner) == 200.0


def test_anchor_stake_none_cap_treated_as_no_cap(runner_with_balance):
    runner = runner_with_balance(stake_caps={"betinia": None})
    assert _compute_anchor_stake(runner) == 200.0
