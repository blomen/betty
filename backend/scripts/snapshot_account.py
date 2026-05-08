"""Snapshot TopstepX account state into account_snapshots table.

Run via: docker exec arnold-backend-1 python /app/backend/scripts/snapshot_account.py
"""

from __future__ import annotations

import asyncio
import sys


async def main() -> int:
    sys.path.insert(0, "/app/backend")
    from sqlalchemy import text

    from src.db.models import get_session
    from src.stocks.config import TopstepXConfig
    from src.stocks.topstepx_client import TopstepXClient

    cfg = TopstepXConfig.from_env()
    if not cfg.is_configured:
        print("topstepx not configured", file=sys.stderr)
        return 1

    client = TopstepXClient(cfg)
    if not await client.connect():
        print("auth failed", file=sys.stderr)
        return 1

    try:
        data = await client._post("/api/Account/search", {"onlyActiveAccounts": True})
        accounts = data.get("accounts", [])
        acct = next((a for a in accounts if a["id"] == client._account_id), None)
        if not acct:
            print(f"no matching account {client._account_id}", file=sys.stderr)
            return 1

        balance = acct.get("balance")
        equity = acct.get("equity", balance)
        unreal = acct.get("unrealizedPnl", acct.get("openPnl"))
        daily = acct.get("dailyPnl", acct.get("dayPnl"))

        with get_session() as db:
            db.execute(
                text(
                    "INSERT INTO account_snapshots (account_id, balance, equity, unrealized_pnl, daily_pnl, source) "
                    "VALUES (:aid, :bal, :eq, :unr, :day, 'topstepx_account_search')"
                ),
                {"aid": client._account_id, "bal": balance, "eq": equity, "unr": unreal, "day": daily},
            )
            db.commit()

        print(f"snapshot ok: account={client._account_id} keys={sorted(acct.keys())} balance={balance} equity={equity}")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
