import aiohttp
import asyncio
import json

async def test_params():
    url = "https://gamma-api.polymarket.com/events"
    
    # Test 1: Slug ONLY
    params1 = {
        "closed": "false",
        "limit": 1,
        "series_slug": "ufc"
    }
    
    # Test 2: Slug + Tag
    params2 = {
        "closed": "false",
        "limit": 1,
        "series_slug": "ufc",
        "tag_id": 100639 # Game Bets
    }
    
    async with aiohttp.ClientSession() as session:
        print("Test 1: Slug ONLY")
        async with session.get(url, params=params1) as resp:
            data = await resp.json()
            if data:
                print(f"  Got {len(data)} events. Title: {data[0].get('title')}")
            else:
                print("  Got 0 events.")

        print("\nTest 2: Slug + Tag (100639)")
        async with session.get(url, params=params2) as resp:
            data = await resp.json()
            if data:
                print(f"  Got {len(data)} events. Title: {data[0].get('title')}")
                if "Gamecocks" in data[0].get('title', ''):
                    print("  [FAIL] Returned Basketball event! Parameter ignored?")
            else:
                print("  Got 0 events (Tag might be filtering it out of existence).")

if __name__ == "__main__":
    asyncio.run(test_params())
