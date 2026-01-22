
import asyncio
import logging
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.providers.snabbare import SnabbareRetriever
from backend.src.core.transport import BrowserTransport

logging.basicConfig(level=logging.ERROR)

async def main():
    transport = BrowserTransport(headless=True)
    retriever = SnabbareRetriever({"id":"snabbare"}, transport=transport)
    
    # IDs to double check: 
    # Rugby (5?), Baseball (12?), Boxing (31?), Motorsports (65?), Esports (130?)
    target_ids = [5, 12, 31, 65, 130]
    
    try:
        await retriever._ensure_init()
        
        print("Verifying specific IDs...")
        for sid in target_ids:
            url = f"{retriever.api_base}/v2/leagues"
            params = retriever.default_params.copy()
            params.update({
                "filter.sportId": sid,
                "page": 1,
                "pageSize": 5
            })
            
            try:
                data = await retriever._fetch_api("/v2/leagues", params=params)
                if data:
                    count = 0
                    leagues = []
                    if isinstance(data, list):
                        leagues = data
                    elif isinstance(data, dict):
                        leagues = data.get('leagues', data.get('data', []))
                    
                    if leagues:
                        print(f"ID {sid}: Found {len(leagues)} leagues")
                        for l in leagues[:3]:
                            print(f"  - {l.get('name', 'Unknown')}")
                    else:
                        print(f"ID {sid}: No leagues found")
                else:
                    print(f"ID {sid}: No data")
            except Exception as e:
                print(f"ID {sid}: Error {e}")
                
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if transport.browser:
            await transport.browser.close()

if __name__ == "__main__":
    asyncio.run(main())
