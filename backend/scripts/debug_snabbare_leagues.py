
import asyncio
import logging
import sys
import os
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.providers.snabbare import SnabbareRetriever
from backend.src.core.transport import BrowserTransport

logging.basicConfig(level=logging.INFO)

async def main():
    transport = BrowserTransport(headless=True)
    retriever = SnabbareRetriever({"id":"snabbare"}, transport=transport)
    
    try:
        await retriever._ensure_init()
        
        print("Fetching leagues...")
        # Fetch raw
        res = await retriever._fetch_api("/v2/leagues", params={
             "filter.sportId": 1, 
             "page": 1, 
             "pageSize": 5
        })
        
        print("--- RAW LEAGUES RESPONSE ---")
        print(json.dumps(res, indent=2, default=str)[:2000])
        
        if res and isinstance(res, dict) and 'leagues' in res:
            lid = res['leagues'][0]['id']
            print(f"Trying events for league {lid}")
            
            # Try a direct events endpoint guess
            # Kambi/MTS often use /events?leagueId=X
            res2 = await retriever._fetch_api("/v2/events", params={
                "leagueId": lid
            })
            print("--- EVENTS RESPONSE (leagueId) ---")
            print(json.dumps(res2, indent=2, default=str)[:1000])
            
            # Try filter.leagueId
            res3 = await retriever._fetch_api("/v2/events", params={
                "filter.leagueIds": lid
            })
            print("--- EVENTS RESPONSE (filter.leagueIds) ---")
            print(json.dumps(res3, indent=2, default=str)[:1000])

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if transport.browser:
            await transport.browser.close()

if __name__ == "__main__":
    asyncio.run(main())
