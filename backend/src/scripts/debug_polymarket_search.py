import aiohttp
import asyncio
import json

async def debug_search():
    base_url = "https://gamma-api.polymarket.com/events"
    
    queries = ["UFC", "Boxing", "Formula 1"]
    
    async with aiohttp.ClientSession() as session:
        for q in queries:
            print(f"Searching for q='{q}'...")
            params = {"q": q, "limit": 2, "closed": "false"}
            async with session.get(base_url, params=params) as resp:
                data = await resp.json()
                if data:
                    print(f"  Found {len(data)} events.")
                    e = data[0]
                    print(f"  Title: {e.get('title')}")
                    print(f"  SeriesSlug: {e.get('seriesSlug')}")
                    # Dump all likely ID fields
                    print(f"  IDs found: {[k for k in e.keys() if 'id' in k.lower()]}")
                    # Print raw to find hidden series ID
                    # print(json.dumps(e, indent=2)) 
                    
                    # Check if 'markets' contains series info?
                    markets = e.get("markets", [])
                    if markets:
                        print(f"    Market 0 keys: {markets[0].keys()}")
                else:
                    print("  Found 0 events.")
            print("-" * 40)

        # Test Series Endpoint?
        print("Testing /series endpoint...")
        async with session.get("https://gamma-api.polymarket.com/series") as resp:
            if resp.status == 200:
                print("  /series endpoint exists! Fetching...")
                series_data = await resp.json()
                print(f"  Fetched {len(series_data)} series.")
                # Search for UFC
                for s in series_data:
                    if "UFC" in s.get("title", "") or "ufc" in s.get("slug", ""):
                        print(f"  Found Series: {s}")
            else:
                print(f"  /series endpoint returned {resp.status}")

if __name__ == "__main__":
    asyncio.run(debug_search())
