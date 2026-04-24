"""Backfill broker_trades from TopstepX trade history.

TopstepX returns one row per FILL (single buy or single sell). We pair them
into ROUND-TRIPS via FIFO matching against position state, then POST each
round-trip to /api/stocks/broker-trades. The endpoint is idempotent on
(closed_at, symbol, side, entry_price, size) so this is safe to re-run.

Historical trades won't have signal_action / signal_confidence / signal_zone
since TopstepX doesn't carry our model context — those fields stay NULL.

Usage (inside the backend container):
    python -m backend.scripts.backfill_broker_trades

Or:
    docker exec arnold-backend-1 python /app/backend/scripts/backfill_broker_trades.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("backfill")


@dataclass
class OpenLeg:
    """A still-open partial position waiting to be closed in FIFO order."""

    ts: datetime
    side: str  # "long" | "short"
    price: float
    size_remaining: int
    fees_paid: float


_FRAC_RE = re.compile(r"\.(\d{1,5})(?=[+\-Z])")


def _ts(s: str) -> datetime:
    # Python 3.10's fromisoformat requires fractional seconds to be exactly 3 or
    # 6 digits; TopstepX returns 5. Pad to 6 before parsing.
    s = _FRAC_RE.sub(lambda m: "." + m.group(1).ljust(6, "0"), s)
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc).replace(tzinfo=None)


def _side(side_int: int) -> str:
    """TopstepX side: 0 = Buy, 1 = Sell."""
    return "long" if side_int == 0 else "short"


def pair_fifo(fills: list[dict]) -> list[dict]:
    """Walk fills in time order, pair openers and closers, emit round-trips.

    Each round-trip carries entry_price (FIFO-matched opener), exit_price
    (closing fill), the closing fill's profitAndLoss, and the open + close
    timestamps. Partial closes are handled by splitting the queue.
    """
    fills_sorted = sorted(fills, key=lambda f: _ts(f["creationTimestamp"]))
    queue: deque[OpenLeg] = deque()  # FIFO of open legs
    current_side: str | None = None  # what side the queue holds; resets to None when flat
    round_trips: list[dict] = []

    for fill in fills_sorted:
        if fill.get("voided"):
            continue
        ts = _ts(fill["creationTimestamp"])
        side = _side(fill["side"])
        size = int(fill["size"])
        price = float(fill["price"])
        fees = float(fill.get("fees", 0)) + float(fill.get("commissions", 0))
        pnl = fill.get("profitAndLoss")

        if not queue or current_side == side:
            # Opening or adding to the same side
            queue.append(OpenLeg(ts=ts, side=side, price=price, size_remaining=size, fees_paid=fees))
            current_side = side
            continue

        # Opposite side — closing some/all of the open position via FIFO
        remaining_to_close = size
        cumulative_pnl = pnl  # TopstepX gives us the realized pnl on this whole closing fill
        # We split it proportionally across the matched legs (only matters
        # when the closing fill spans multiple openers — same average price).
        original_close_size = size

        while remaining_to_close > 0 and queue:
            open_leg = queue[0]
            matched = min(remaining_to_close, open_leg.size_remaining)

            # Slice of the closer's pnl proportional to matched / original_close_size
            slice_pnl = (
                (cumulative_pnl * (matched / original_close_size))
                if cumulative_pnl is not None
                else None
            )

            entry_side = open_leg.side
            direction = 1 if entry_side == "long" else -1
            pnl_pts = direction * (price - open_leg.price)
            # PnL in dollars (NQ: $20/point, $5/tick) — fall back to manual calc
            # if TopstepX didn't give us pnl on this fill.
            if slice_pnl is None:
                slice_pnl = pnl_pts * 20.0 * matched

            round_trips.append(
                {
                    "ts": open_leg.ts.isoformat(),
                    "session_date": open_leg.ts.strftime("%Y-%m-%d"),
                    "symbol": "NQ",
                    "side": entry_side,
                    "size": matched,
                    "entry_price": open_leg.price,
                    "exit_price": price,
                    "pnl_dollars": round(float(slice_pnl), 2),
                    # pnl_r is unknowable without the realized stop distance —
                    # which we don't store and TopstepX doesn't tell us.
                    "pnl_r": None,
                    "closed_at": ts.isoformat(),
                    # No signal context — these are historical trades from
                    # before the persistence hook existed.
                    "signal_action": None,
                    "signal_confidence": None,
                    "signal_zone": None,
                    "was_stop": None,
                }
            )

            open_leg.size_remaining -= matched
            remaining_to_close -= matched
            if open_leg.size_remaining == 0:
                queue.popleft()

        if not queue:
            current_side = None
        elif remaining_to_close > 0:
            # The closer flipped past flat — open a fresh leg the other way.
            queue.append(
                OpenLeg(
                    ts=ts, side=side, price=price,
                    size_remaining=remaining_to_close, fees_paid=fees,
                )
            )
            current_side = side

    return round_trips


async def fetch_topstepx_trades(client) -> list[dict]:
    """Pull all trades from TopstepX. Single call — endpoint returns full list."""
    resp = await client._post("/api/Trade/search", {"accountId": client._account_id})
    if not isinstance(resp, dict):
        raise RuntimeError(f"unexpected response: {type(resp)}")
    if not resp.get("success", True):
        raise RuntimeError(f"TopstepX error: {resp.get('errorMessage')}")
    return resp.get("trades", [])


async def post_trade(api_base: str, api_key: str, payload: dict, http: httpx.AsyncClient) -> tuple[int, bool]:
    headers = {"X-API-Key": api_key} if api_key else {}
    r = await http.post(f"{api_base}/api/stocks/broker-trades", json=payload, headers=headers, timeout=15)
    if r.status_code >= 400:
        log.warning("POST %d: %s", r.status_code, r.text[:200])
        return (0, False)
    j = r.json()
    return (j.get("id", 0), j.get("deduped", False))


async def main():
    from src.stocks.config import TopstepXConfig
    from src.stocks.topstepx_client import TopstepXClient

    cfg = TopstepXConfig.from_env()
    if not cfg.is_configured:
        log.error("TopstepX not configured (TOPSTEPX_USERNAME / TOPSTEPX_API_KEY missing)")
        sys.exit(1)

    api_base = os.environ.get("BACKFILL_API_BASE", "http://localhost:8000")
    api_key = os.environ.get("ARNOLD_API_KEY", "")

    log.info("Authenticating with TopstepX...")
    client = TopstepXClient(cfg)
    if not await client.connect():
        log.error("auth failed")
        sys.exit(1)
    log.info("authenticated as account_id=%s", client._account_id)

    log.info("Fetching trade history...")
    fills = await fetch_topstepx_trades(client)
    log.info("got %d fills (raw)", len(fills))
    await client.close()

    if not fills:
        log.info("nothing to backfill")
        return

    round_trips = pair_fifo(fills)
    log.info("paired into %d round-trips", len(round_trips))

    if not round_trips:
        return

    inserted = 0
    deduped = 0
    failed = 0
    async with httpx.AsyncClient() as http:
        for rt in round_trips:
            try:
                _, was_dedupe = await post_trade(api_base, api_key, rt, http)
                if was_dedupe:
                    deduped += 1
                else:
                    inserted += 1
            except Exception as e:
                log.warning("post failed: %s", e)
                failed += 1

    log.info("=== backfill done ===")
    log.info("  inserted = %d", inserted)
    log.info("  deduped  = %d (already in DB)", deduped)
    log.info("  failed   = %d", failed)


if __name__ == "__main__":
    asyncio.run(main())
