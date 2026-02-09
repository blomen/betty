"""
Discover OBG/Gecko V2 sport category IDs by checking the API responses.

Uses the most-popular-competitions/v1 and category-by-slug APIs to map
sport names to category IDs. Then verifies event counts per category.
"""
import asyncio
import aiohttp
import json

# Betsson API base (after session init, this is where API calls go)
# We'll try the public-facing API first
SITE = "https://www.betsson.com"
API_BASE = "https://www.betsson.com/api/sb/v1"

# Sport slugs used in Betsson URLs
SPORT_SLUGS = {
    "football": "fotboll",
    "basketball": "basket",
    "tennis": "tennis",
    "ice_hockey": "ishockey",
    "american_football": "amerikansk-fotboll",
    "baseball": "baseboll",
    "mma": "mma",
    "esports": "esports",
    "rugby": "rugby",
    "cricket": "cricket",
    "boxing": "boxning",
    "handball": "handboll",
    "volleyball": "volleyboll",
    "darts": "dart",
    "curling": "curling",
    "golf": "golf",
    "table_tennis": "bordtennis",
}

# Known category IDs from the extractor (to verify)
KNOWN_CATEGORIES = {
    "football": 1,
    "basketball": 3,
    "tennis": 4,
    "ice_hockey": 5,
    "rugby": 6,
    "handball": 7,
    "american_football": 10,
    "baseball": 12,
    "mma": 14,
    "esports": 20,
    "boxing": 16,
    "cricket": 15,
}


async def main():
    async with aiohttp.ClientSession() as session:
        # Try the category-by-slug endpoint for each sport
        print("=== Discovering category IDs via slug lookup ===\n")

        for sport, slug in sorted(SPORT_SLUGS.items()):
            url = f"{API_BASE}/widgets/category-by-slug/sv/{slug}"
            try:
                async with session.get(url, headers={
                    "accept": "application/json",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        cat_data = data.get("data", {})
                        cat_id = cat_data.get("id", "?")
                        name = cat_data.get("name", "?")
                        event_count = cat_data.get("eventCount", 0)
                        known = KNOWN_CATEGORIES.get(sport, "NOT SET")
                        match = "✓" if cat_id == known else "✗ MISMATCH" if known != "NOT SET" else "NEW"
                        print(f"  {sport:25s} slug={slug:20s} -> id={cat_id:>4} name={name:20s} events={event_count:>5} [{match}] (known={known})")
                    elif resp.status == 403:
                        print(f"  {sport:25s} slug={slug:20s} -> 403 FORBIDDEN (need session headers)")
                    else:
                        print(f"  {sport:25s} slug={slug:20s} -> HTTP {resp.status}")
            except Exception as e:
                print(f"  {sport:25s} slug={slug:20s} -> Error: {e}")

            await asyncio.sleep(0.5)

        # Try events-table API with different category IDs to see what returns events
        print("\n\n=== Verifying category IDs via events-table/v2 (first 50 IDs) ===\n")

        for cat_id in range(1, 51):
            url = f"{API_BASE}/widgets/events-table/v2?categoryIds={cat_id}&phase=4&pageNumber=1&marketTemplateIds=MW3W,MW2W"
            try:
                async with session.get(url, headers={
                    "accept": "application/json",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        events = data.get("data", {}).get("events", [])
                        total = data.get("data", {}).get("totalItemCount", 0)
                        if total > 0:
                            # Get sport name from first event
                            first_sport = events[0].get("categoryName", "?") if events else "?"
                            print(f"  Category {cat_id:>3}: {total:>5} events ({first_sport})")
                    elif resp.status == 403:
                        pass  # Skip silently for forbidden
            except Exception:
                pass

            await asyncio.sleep(0.3)


if __name__ == "__main__":
    asyncio.run(main())
