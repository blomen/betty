"""Test actual TipwinRetriever extraction."""
import asyncio
import logging
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

async def test():
    from src.providers.tipwin import TipwinRetriever
    from src.core.transport import BrowserTransport

    config = {
        "id": "tipwin",
        "name": "Tipwin",
        "site_url": "https://www.tipwin.se",
        "retriever_type": "tipwin",
    }
    transport = BrowserTransport(headless=True)
    retriever = TipwinRetriever(config, transport)

    try:
        # Simulate health check first (like orchestrator does)
        print("=== HEALTH CHECK ===", flush=True)
        await retriever.extract("football", limit=1, run_id="test_run_1")
        print(f"Health check done. session_ready={retriever._session_ready}", flush=True)

        # Now do full extraction
        print("\n=== FULL EXTRACTION ===", flush=True)
        events = await retriever.extract("football", limit=500, run_id="test_run_1")
        print(f"\nFootball events: {len(events)}", flush=True)
        if events:
            e = events[0]
            print(f"  Sample: {e.home_team} vs {e.away_team} ({e.league})", flush=True)
            print(f"  Markets: {[m['type'] for m in e.markets]}", flush=True)

        # Check other sports from cache
        for sport in ["ice_hockey", "basketball", "tennis", "handball"]:
            sport_events = await retriever.extract(sport, limit=500, run_id="test_run_1")
            print(f"{sport}: {len(sport_events)} events", flush=True)

    finally:
        await transport.close()

if __name__ == '__main__':
    asyncio.run(test())
