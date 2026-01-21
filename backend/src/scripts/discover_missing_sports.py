import aiohttp
import asyncio
import json

async def discover_series():
    base_url = "https://gamma-api.polymarket.com/events"
    
    test_slugs = [
        "ufc", "mma", "boxing", "f1", "formula-1", "formula1", 
        "nascar", "motorsports", "fighting", "combat-sports"
    ]
    
    print("Testing series_slug parameter...")
    
    found_slugs = []

    async with aiohttp.ClientSession() as session:
        for slug in test_slugs:
            # Try filtering by slug. Note: Parameter might be 'slug' or 'series_slug'? 
            # Docs say 'series_id', but maybe 'series_slug' works?
            # Or maybe just 'slug'?
            
            # Test 1: series_slug
            params = {"closed": "false", "limit": 1, "series_slug": slug}
            async with session.get(base_url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if len(data) > 0:
                        print(f"[SUCCESS] Found events for series_slug='{slug}'")
                        found_slugs.append(slug)
                        # Inspect first event to see if we can find the Series ID?
                        print("Event keys:", data[0].keys())
                        print("SeriesSlug in event:", data[0].get('seriesSlug'))
                        continue
            
            # Test 2: slug (this is usually event slug, likely wont work for series)
            
    if not found_slugs:
        print("No slugs worked.")
    else:
        print(f"Working slugs: {found_slugs}")

if __name__ == "__main__":
    asyncio.run(discover_series())
