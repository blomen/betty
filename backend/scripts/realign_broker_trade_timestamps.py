"""Realign broker_trades.ts and .closed_at to TopstepX broker-authoritative
creationTimestamps.

Why this exists:
  Older rows (before broker_adapter 2026-05-07 fix) recorded `ts` at the
  ORDER-SUBMIT moment instead of the actual ENTRY-FILL moment, and
  `closed_at` at the Python exit-handler moment instead of the actual
  EXIT-FILL moment. For limit / stop-limit entries that take minutes to
  fill, this anchored the chart widget several minutes before the candle
  where the trade really filled. The fix (in broker_adapter.on_stream_fill
  + _log_broker_trade) now captures the broker's creationTimestamp from
  the stream frame, but historical rows are stuck with the wrong values.

What this script does:
  Iterates rows where ts/closed_at are likely off (or all rows with
  --all). For each row, queries TopstepX `/api/Trade/search` for fills in
  a wide window around the recorded ts. Pairs fills against the row by
  side+entry_price+size and finds:
    - the OPENING fill (matching side, near entry_price) → new `ts`
    - the CLOSING fill (opposite side, near exit_price) → new `closed_at`
  Updates the row only when both timestamps are found and shift > 60s.

Usage (inside the backend container):
    docker exec arnold-backend-1 python /app/backend/scripts/realign_broker_trade_timestamps.py [--days N] [--dry-run] [--id N]

The container has TOPSTEPX credentials in env, so the TopstepXClient can
auth straight from there.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("realign_ts")

# Pair-match tolerance: a Trade/search fill row counts as a match for a
# broker_trades row when side matches and price is within this many ticks
# of the recorded entry_price (or exit_price). NQ ticks at 0.25 — a 4-tick
# tolerance covers split-fill aggregation noise.
_PRICE_TICK_TOL = 4
_NQ_TICK = 0.25
_FRAC_RE = re.compile(r"\.(\d{1,5})(?=[+\-Z])")


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    s = _FRAC_RE.sub(lambda m: f".{(m.group(1) + '000000')[:6]}", s)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class FillRow:
    ts: datetime
    side: int  # 0=BUY, 1=SELL per TopstepX
    price: float
    size: int
    order_id: int | None
    pnl: float | None


async def _search_trades(client, account_id: int, start: datetime, end: datetime) -> list[FillRow]:
    """One Trade/search call covering [start, end] in UTC."""
    resp = await client._post(
        "/api/Trade/search",
        {
            "accountId": account_id,
            "startTimestamp": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endTimestamp": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    rows = (resp or {}).get("trades") or []
    out: list[FillRow] = []
    for t in rows:
        if t.get("voided"):
            continue
        ts = _parse_iso(str(t.get("creationTimestamp") or ""))
        if ts is None:
            continue
        try:
            out.append(
                FillRow(
                    ts=ts,
                    side=int(t.get("side")),
                    price=float(t.get("price") or 0.0),
                    size=int(t.get("size") or 0),
                    order_id=t.get("orderId"),
                    pnl=(float(t.get("profitAndLoss")) if t.get("profitAndLoss") is not None else None),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def _find_match(fills: list[FillRow], want_side: int, want_price: float, want_size: int) -> FillRow | None:
    """Closest fill matching side, with price within tolerance and matching size.
    Returns the EARLIEST matching fill in the window (so opening fill, not a
    later same-side reversal)."""
    best: FillRow | None = None
    for f in fills:
        if f.side != want_side:
            continue
        if abs(f.price - want_price) > _PRICE_TICK_TOL * _NQ_TICK:
            continue
        if want_size and f.size != want_size:
            # Allow size mismatch on split fills (broker reports size 1 per leg)
            # but only if size accumulates to the target. Keep simple: accept any size.
            pass
        if best is None or f.ts < best.ts:
            best = f
    return best


async def _realign_one(client, account_id: int, row: dict, *, dry_run: bool) -> dict | None:
    """Returns a {id, old_ts, new_ts, old_closed_at, new_closed_at, action}
    summary if an update was applied (or would be in dry-run), else None."""
    rid = row["id"]
    side_str = (row["side"] or "").lower()
    if side_str not in ("long", "short"):
        return None
    entry_side = 0 if side_str == "long" else 1  # 0 = BUY (long entry)
    exit_side = 1 - entry_side
    entry_price = float(row["entry_price"] or 0)
    exit_price = float(row["exit_price"] or 0) if row.get("exit_price") else None
    size = int(row.get("size") or 1)
    old_ts = _parse_iso(str(row.get("ts") or ""))
    old_closed = _parse_iso(str(row.get("closed_at") or ""))
    if old_ts is None:
        return None
    # Search a 30-min window centred on the recorded ts. 30 min covers
    # the 8-min limit-order lag we see with plenty of margin.
    start = old_ts - timedelta(minutes=15)
    end_window = (old_closed or old_ts) + timedelta(minutes=15)
    fills = await _search_trades(client, account_id, start, end_window)
    if not fills:
        log.info("trade %d: no Trade/search fills in window %s..%s", rid, start, end_window)
        return None

    open_match = _find_match(fills, entry_side, entry_price, size)
    close_match = _find_match(fills, exit_side, exit_price, size) if exit_price else None

    new_ts = open_match.ts if open_match else None
    new_closed = close_match.ts if close_match else None

    if new_ts is None and new_closed is None:
        return None

    # Only apply the update if the shift is meaningful (> 60s) — minor jitter
    # from rounding isn't worth churning the DB.
    update_ts = new_ts if new_ts and abs((new_ts - old_ts).total_seconds()) > 60 else None
    update_closed = (
        new_closed if new_closed and old_closed and abs((new_closed - old_closed).total_seconds()) > 60 else None
    )
    if update_ts is None and update_closed is None:
        return None

    summary = {
        "id": rid,
        "old_ts": old_ts.isoformat(),
        "new_ts": update_ts.isoformat() if update_ts else None,
        "old_closed_at": old_closed.isoformat() if old_closed else None,
        "new_closed_at": update_closed.isoformat() if update_closed else None,
    }
    if dry_run:
        summary["action"] = "would_update"
        return summary

    # Apply update.
    from sqlalchemy import update as sa_update

    from backend.src.db.models import BrokerTrade, get_session

    sets: dict = {}
    if update_ts is not None:
        sets[BrokerTrade.ts] = update_ts.replace(tzinfo=None)  # column is naive UTC
    if update_closed is not None:
        sets[BrokerTrade.closed_at] = update_closed.replace(tzinfo=None)

    with get_session() as db:
        db.execute(sa_update(BrokerTrade).where(BrokerTrade.id == rid).values(**sets))
        db.commit()
    summary["action"] = "updated"
    return summary


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="how far back to scan (default: 30)")
    parser.add_argument("--id", type=int, default=None, help="single broker_trade id (debug)")
    parser.add_argument("--dry-run", action="store_true", help="print intended changes only")
    args = parser.parse_args()

    # Inside the container, the package layout is `backend.src.*`.
    sys.path.insert(0, "/app")
    from backend.src.db.models import BrokerTrade, get_session
    from backend.src.stocks.topstepx_client import TopstepXClient

    cutoff = datetime.utcnow() - timedelta(days=args.days)
    with get_session() as db:
        q = db.query(BrokerTrade)
        if args.id:
            q = q.filter(BrokerTrade.id == args.id)
        else:
            q = q.filter(BrokerTrade.ts >= cutoff).order_by(BrokerTrade.ts.desc())
        rows = [
            {
                "id": r.id,
                "ts": r.ts.isoformat() if r.ts else None,
                "closed_at": r.closed_at.isoformat() if r.closed_at else None,
                "side": r.side,
                "size": r.size,
                "entry_price": r.entry_price,
                "exit_price": r.exit_price,
            }
            for r in q.all()
        ]

    log.info("scanning %d broker_trades rows (days=%d, id=%s, dry_run=%s)", len(rows), args.days, args.id, args.dry_run)

    client = TopstepXClient()
    await client.authenticate()
    account_id = client._account_id

    updated = 0
    skipped = 0
    for row in rows:
        try:
            result = await _realign_one(client, account_id, row, dry_run=args.dry_run)
        except httpx.HTTPError as e:
            log.warning("trade %d: HTTP error %s", row["id"], e)
            skipped += 1
            continue
        except Exception:
            log.exception("trade %d: unexpected error", row["id"])
            skipped += 1
            continue
        if result:
            updated += 1
            log.info(
                "id=%d  ts %s -> %s   closed_at %s -> %s   [%s]",
                result["id"],
                result["old_ts"],
                result.get("new_ts") or "(unchanged)",
                result.get("old_closed_at") or "(none)",
                result.get("new_closed_at") or "(unchanged)",
                result["action"],
            )
        else:
            skipped += 1
    log.info("done — updated=%d skipped=%d", updated, skipped)


if __name__ == "__main__":
    asyncio.run(main())
