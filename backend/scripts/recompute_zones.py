"""Standalone script to recompute session levels and update LevelMonitor zones.

Run this every 5 minutes via the scheduler or a cron job.
It calls the same compute logic as the /api/market/compute endpoint
but doesn't require HTTP auth.
"""
import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from src.services.market_service import MarketService
    from src.db.models import get_session
    from datetime import date, timedelta

    svc = MarketService(get_session())
    try:
        # Try yesterday first (pre-market has no today data)
        session_data = None
        expanded = None
        for attempt in ["yesterday", "today"]:
            try:
                if attempt == "yesterday":
                    d = (date.today() - timedelta(days=1)).isoformat()
                    session_data = await svc.compute_session(d)
                else:
                    session_data = await svc.compute_session()
                expanded = await svc.build_expanded_session()
                if expanded:
                    print("OK: %s session loaded, %d levels" % (
                        attempt, len(expanded.get("levels", []))
                    ))
                    break
            except Exception as e:
                print("SKIP: %s failed: %s" % (attempt, e))
                continue

        if not expanded:
            print("FAIL: no session data available")
            return

        # Write levels to a file the LevelMonitor can read
        import json
        levels_file = "/app/data/rl/current_levels.json"
        with open(levels_file, "w") as f:
            json.dump({
                "levels": expanded.get("levels", []),
                "session": session_data if isinstance(session_data, dict) else {},
                "atr": session_data.get("atr", 40.0) if isinstance(session_data, dict) else 40.0,
            }, f, default=str)
        print("Wrote %s" % levels_file)

    finally:
        svc.db.close()


if __name__ == "__main__":
    asyncio.run(main())
