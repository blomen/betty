
import asyncio
import logging
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.providers.snabbare import SnabbareRetriever
from backend.src.core.transport import BrowserTransport

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("verify_ids")
logger.setLevel(logging.INFO)

async def main():
    transport = BrowserTransport(headless=True)
    # Mock config
    retriever = SnabbareRetriever({"id":"snabbare"}, transport=transport)
    
    try:
        await retriever._ensure_init()
        
        # Range of IDs to test
        # Based on Sportradar, usually < 100
        ids_to_check = list(range(1, 50)) + [65, 130] # + existing knowns
        
        valid_sports = {}
        
        print("Checking IDs...")
        for sid in ids_to_check:
            # We use the internal API directly
            url = f"{retriever.api_base}/v2/leagues"
            params = retriever.default_params.copy()
            params.update({
                "filter.sportId": sid,
                "page": 1,
                "pageSize": 1
            })
            
            try:
                data = await retriever._fetch_api("/v2/leagues", params=params)
                count = 0
                if data:
                    if isinstance(data, list):
                        count = len(data)
                    elif isinstance(data, dict):
                         # Snabbare response often has { "leagues": [...], "page": ... }
                         if 'leagues' in data:
                             count = len(data['leagues'])
                         elif 'data' in data:
                             count = len(data['data'])
                
                if count > 0:
                    lname = "Unknown"
                    if isinstance(data, list) and len(data) > 0:
                        lname = data[0].get('name', 'Unknown')
                    elif isinstance(data, dict):
                         if 'leagues' in data and len(data['leagues']) > 0:
                             lname = data['leagues'][0].get('name', 'Unknown')
                         elif 'data' in data and len(data['data']) > 0:
                             lname = data['data'][0].get('name', 'Unknown')
                    print(f"ID {sid}: Found {count} leagues (Sample: {lname})")
                    valid_sports[sid] = lname
            except Exception as e:
                pass
                
        print("--- SUMMARY ---")
        for k, v in valid_sports.items():
            print(f"ID {k}: {v} leagues")

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if transport.browser:
            await transport.browser.close()

if __name__ == "__main__":
    asyncio.run(main())
