"""One-shot reconciler for the back-derive phantom-exit corruption pattern.

Symptom: broker_trades rows where `closed_at < ts` (entry timestamped after
close — physically impossible) caused by broker_adapter._log_broker_trade
mutating `price` instead of `entry_px` when broker pnl mismatched tracker pnl.
Produces fake exit prices that no candle ever traded at.

For each victim, queries TopstepX `/api/Trade/search`, finds the real entry
fill via entry_order_id (matching same side), finds the matching closing fill
(opposite side, has profitAndLoss), and UPDATEs the row.

Usage:
    docker cp backend/scripts/reconcile_corrupted_trades.py arnold-backend-1:/app/backend/scripts/reconcile_corrupted_trades.py
    docker exec arnold-backend-1 python /app/backend/scripts/reconcile_corrupted_trades.py

Idempotent: only touches rows still showing `closed_at < ts AND >= 1s delta`.
After fix lands in broker_adapter.py:1313-1315 and is deployed, this script
should rarely have anything to do.
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timezone

_FRAC_RE = re.compile(r"\.(\d{1,5})(?=[+\-Z])")


def _ts(s: str) -> datetime:
    s = _FRAC_RE.sub(lambda m: "." + m.group(1).ljust(6, "0"), s)
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc).replace(tzinfo=None)


async def main() -> int:
    sys.path.insert(0, "/app/backend")
    from sqlalchemy import text

    from src.db.models import get_session
    from src.stocks.config import TopstepXConfig
    from src.stocks.topstepx_client import TopstepXClient

    cfg = TopstepXConfig.from_env()
    client = TopstepXClient(cfg)
    if not await client.connect():
        print("auth failed", file=sys.stderr)
        return 1

    try:
        resp = await client._post("/api/Trade/search", {"accountId": client._account_id})
        fills = resp.get("trades", [])
        fills_by_oid: dict[int, list[dict]] = {}
        for f in fills:
            oid = f.get("orderId")
            if oid is not None:
                fills_by_oid.setdefault(int(oid), []).append(f)

        with get_session() as db:
            rows = db.execute(
                text(
                    "SELECT id, ts, closed_at, side, entry_price, exit_price, "
                    "pnl_dollars, entry_order_id "
                    "FROM broker_trades WHERE closed_at < ts AND closed_at IS NOT NULL "
                    "AND ABS(EXTRACT(EPOCH FROM (closed_at - ts))) >= 1 "
                    "ORDER BY id"
                )
            ).all()

            for r in rows:
                tid, ts_db, closed_db, side, ep_db, xp_db, pnl_db, eoid = r
                if eoid is None:
                    print(f"trade {tid}: no entry_order_id, skipping (manual reconcile needed)")
                    continue
                entry_fills = fills_by_oid.get(int(eoid), [])
                if not entry_fills:
                    print(f"trade {tid}: orderId {eoid} not in Trade/search response (too old?)")
                    continue
                want_entry_side = 0 if side == "long" else 1
                entry_match = next(
                    (f for f in entry_fills if int(f.get("side", -1)) == want_entry_side),
                    None,
                )
                if not entry_match:
                    print(f"trade {tid}: no matching {side}-entry fill in orderId {eoid}")
                    continue
                real_entry_price = float(entry_match["price"])
                real_entry_ts = _ts(entry_match["creationTimestamp"])

                want_exit_side = 1 - want_entry_side
                candidates = [
                    f
                    for f in fills
                    if int(f.get("side", -1)) == want_exit_side
                    and f.get("profitAndLoss") is not None
                    and _ts(f["creationTimestamp"]) > real_entry_ts
                ]
                if not candidates:
                    print(f"trade {tid}: no closing fill found after entry")
                    continue
                exit_match = min(candidates, key=lambda f: _ts(f["creationTimestamp"]))
                real_exit_price = float(exit_match["price"])
                real_exit_ts = _ts(exit_match["creationTimestamp"])
                real_pnl = float(exit_match["profitAndLoss"])

                print(f"trade {tid}: WAS entry={ep_db} exit={xp_db} ts={ts_db} closed={closed_db} pnl={pnl_db}")
                print(
                    f"          NOW entry={real_entry_price} exit={real_exit_price} "
                    f"ts={real_entry_ts} closed={real_exit_ts} pnl={real_pnl}"
                )

                db.execute(
                    text(
                        "UPDATE broker_trades SET entry_price=:ep, exit_price=:xp, "
                        "ts=:ts, closed_at=:cl, pnl_dollars=:pnl, exit_order_id=:xoid "
                        "WHERE id=:tid"
                    ),
                    {
                        "ep": real_entry_price,
                        "xp": real_exit_price,
                        "ts": real_entry_ts,
                        "cl": real_exit_ts,
                        "pnl": real_pnl,
                        "xoid": int(exit_match["orderId"]),
                        "tid": tid,
                    },
                )
            db.commit()
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
