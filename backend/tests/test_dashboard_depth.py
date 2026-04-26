"""Regression tests for the L2 depth path in the local dashboard module."""

from __future__ import annotations

import asyncio

import pytest

from src.stocks import dashboard as dash


@pytest.mark.asyncio
async def test_record_depth_appends_and_broadcasts():
    """A depth tick should be coalesced into the latest snapshot and broadcast as `depth`."""
    received: list[dict] = []

    async def fake_broadcast(event: dict) -> None:
        received.append(event)

    dash.bind_loop(asyncio.get_running_loop())
    orig = dash.broadcast
    dash.broadcast = fake_broadcast  # type: ignore[assignment]
    try:
        dash.record_depth({"price": 27400.0, "currentVolume": 5, "type": 1})
        dash.record_depth({"price": 27400.25, "currentVolume": 7, "type": 2})
        dash.record_depth({"price": 27400.0, "currentVolume": 9, "type": 1})

        # Force a flush regardless of throttle.
        dash._last_depth_emit = 0
        dash.record_depth({"price": 27400.5, "currentVolume": 4, "type": 2})

        await asyncio.sleep(0.05)
    finally:
        dash.broadcast = orig  # type: ignore[assignment]

    assert any(e["type"] == "depth" for e in received), "expected at least one depth broadcast"
    last = next(e for e in reversed(received) if e["type"] == "depth")
    bids = {lvl["price"]: lvl["size"] for lvl in last["bids"]}
    asks = {lvl["price"]: lvl["size"] for lvl in last["asks"]}
    assert bids[27400.0] == 9, "second bid update at 27400 should overwrite first"
    assert asks[27400.25] == 7
    assert asks[27400.5] == 4
