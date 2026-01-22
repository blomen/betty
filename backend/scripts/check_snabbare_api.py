
import asyncio
import logging
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.providers.snabbare import SnabbareRetriever
from backend.src.core.transport import BrowserTransport

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("check_api")

async def main():
    transport = BrowserTransport(headless=True)
    retriever = SnabbareRetriever({"id":"snabbare"}, transport=transport)
    
    try:
        await retriever._ensure_init()
        
        # Get a valid league ID first (e.g. Premier League or similar)
        leagues = await retriever._fetch_api("/v2/leagues", params={
             "filter.sportId": 1, 
             "page": 1, 
             "pageSize": 5
        })
        
        target_league = None
        if leagues and isinstance(leagues, dict) and 'leagues' in leagues:
            target_league = leagues['leagues'][0]
        elif leagues and isinstance(leagues, list):
            target_league = leagues[0]
            
        if not target_league:
            print("No leagues found to test.")
            return

        lid = target_league.get('id') or target_league.get('_id')
        print(f"Testing with League: {target_league.get('name')} (ID: {lid})")
        
        # Try endpoints
        endpoints = [
            f"/v2/leagues/{lid}",
            f"/v2/leagues/{lid}/events",
            f"/v2/events?leagueId={lid}",
            f"/v2/events?filter.leagueId={lid}",
             f"/v2/seasons/{lid}/events", # Kambi style
             f"/v2/sb/leagues/{lid}/events"
        ]
        
        for ep in endpoints:
            print(f"Trying {ep}...")
            res = await retriever._fetch_api(ep)
            if res:
                print(f"--- GOT RESPONSE for {ep} ---")
                print(str(res)[:500])
                # Check if it has events
                has_events = False
                if isinstance(res, dict):
                    if 'events' in res and res['events']: has_events = True
                    if 'data' in res and res['data']: has_events = True
                if isinstance(res, list) and len(res) > 0: has_events = True
                
                if has_events:
                    print(">>> APPEARS TO CONTAIN EVENTS! <<<")
            else:
                print(f"No response for {ep}")

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if transport.browser:
            await transport.browser.close()

if __name__ == "__main__":
    asyncio.run(main())
