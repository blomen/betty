# backend/src/stocks/tracker_reconciler.py
"""Reconcile broker_adapter.tracker state from TopstepX REST on bootstrap."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    matched: bool = False
    broker_only: bool = False
    disk_only: bool = False
    divergence_logged: bool = False
    degraded: bool = False


async def reconcile_tracker_from_broker(
    adapter,
    client,
    contract_id: str,
) -> ReconcileResult:
    """Populate adapter.tracker from TopstepX REST. Return what happened.

    Order of operations:
      1. Query open positions on the contract.
      2. Look up matching stop order (if any) for stop_price + stop_order_id.
      3. If broker has position: tracker.on_fill(...), reconcile against disk.
      4. If broker has no position but disk does: clear disk.
      5. On REST failure: return degraded; caller may fall back to Layer 2.
    """
    result = ReconcileResult()

    try:
        positions = await client.search_open_positions()
    except Exception as e:
        logger.warning("reconcile: REST query failed (%s); returning degraded", e)
        result.degraded = True
        return result

    matching = [p for p in positions if p.get("contractId") == contract_id]

    pending = adapter._pending_trade

    if not matching:
        if pending:
            logger.info("reconcile: no broker position; clearing stale _pending_trade")
            adapter._set_pending_trade(None)
            result.disk_only = True
        return result

    # Broker has an open position
    pos = matching[0]
    pos_type = pos.get("type")
    side = "long" if pos_type == 1 else "short" if pos_type == 2 else None
    if side is None:
        logger.warning("reconcile: unknown position type=%s; skipping", pos_type)
        result.degraded = True
        return result

    avg_price = float(pos.get("averagePrice") or 0.0)
    size = int(pos.get("size") or 0)

    # Find the matching stop order
    stop_price = 0.0
    stop_order_id = None
    try:
        orders = await client.search_open_orders()
        # type=4 is stop order; side opposite to position
        # TopstepX: SIDE_BUY=0, SIDE_SELL=1
        # long stop = sell (1), short stop = buy (0)
        opposite_side = 1 if side == "long" else 0
        for o in orders:
            if o.get("type") == 4 and o.get("side") == opposite_side:
                stop_price = float(o.get("stopPrice") or 0.0)
                stop_order_id = o.get("orderId")
                break
    except Exception as e:
        logger.warning("reconcile: stop-order lookup failed (%s); leaving stop=0", e)

    # Apply to tracker
    adapter.tracker.on_fill(side, avg_price, size, stop_price)
    if stop_order_id is not None:
        adapter.tracker.stop_order_id = stop_order_id

    # Tracker was populated from broker — matched is True regardless of disk state
    result.matched = True

    # Reconcile against disk
    if pending:
        disk_size = int(pending.get("size") or 0)
        disk_entry = float(pending.get("entry_price") or 0.0)
        disk_side = pending.get("side")
        if (disk_size != size) or (disk_side != side) or abs(disk_entry - avg_price) > 0.5:
            logger.warning(
                "reconcile: broker/disk divergence — broker=(side=%s, size=%d, avg=%.2f) disk=(side=%s, size=%d, avg=%.2f); broker wins",
                side, size, avg_price, disk_side, disk_size, disk_entry,
            )
            result.divergence_logged = True
    else:
        result.broker_only = True

    logger.info(
        "reconcile: tracker populated from broker — side=%s entry=%.2f size=%d stop=%.2f stop_order_id=%s",
        side, avg_price, size, stop_price, stop_order_id,
    )
    return result
