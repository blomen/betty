"""Broadcaster diff-and-emit logic — fake WS sink."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))

from arnold.tv_overlay.broadcaster import OverlayBroadcaster  # noqa: E402


@pytest.mark.asyncio
async def test_zone_diff_only_emits_changed():
    sent: list[dict] = []

    async def fake_emit(event: dict) -> None:
        sent.append(event)

    b = OverlayBroadcaster(emit=fake_emit)

    await b.reconcile_zones(
        [
            {"price": 27400.0, "members": 3, "lower": 27395, "upper": 27405, "hierarchy": 0.6, "name": "x"},
            {"price": 27450.0, "members": 2, "lower": 27445, "upper": 27455, "hierarchy": 0.4, "name": "y"},
        ]
    )
    upserts = [e for e in sent if e["type"] == "zone_upsert"]
    removes = [e for e in sent if e["type"] == "zone_remove"]
    assert len(upserts) == 2
    assert len(removes) == 0

    sent.clear()
    # Same first zone, second zone updated members count, third zone added,
    # nothing removed.
    await b.reconcile_zones(
        [
            {"price": 27400.0, "members": 3, "lower": 27395, "upper": 27405, "hierarchy": 0.6, "name": "x"},
            {"price": 27450.0, "members": 5, "lower": 27445, "upper": 27455, "hierarchy": 0.7, "name": "y"},
            {"price": 27500.0, "members": 1, "lower": 27498, "upper": 27502, "hierarchy": 0.2, "name": "z"},
        ]
    )
    upserts = [e for e in sent if e["type"] == "zone_upsert"]
    removes = [e for e in sent if e["type"] == "zone_remove"]
    assert len(upserts) == 2  # 27450 (changed), 27500 (new)
    assert len(removes) == 0


@pytest.mark.asyncio
async def test_position_close_emits_remove():
    sent: list[dict] = []

    async def fake_emit(event: dict) -> None:
        sent.append(event)

    b = OverlayBroadcaster(emit=fake_emit)

    await b.reconcile_position(
        positions=[{"side": "long", "size": 1, "price": 27400.0}],
        model_status={"entry_price": 27400.0, "stop_price": 27380.0},
    )
    upserts = [e for e in sent if e["type"] == "position_upsert"]
    assert len(upserts) == 1

    sent.clear()
    await b.reconcile_position(positions=[{"side": "long", "size": 0, "price": 0.0}], model_status={})
    removes = [e for e in sent if e["type"] == "position_remove"]
    assert len(removes) == 1
