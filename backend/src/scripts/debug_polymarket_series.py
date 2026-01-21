import aiohttp
import asyncio
import json

async def fetch_all_series():
    url = "https://gamma-api.polymarket.com/series"
    params = {"q": "UFC"}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            series_list = await resp.json()
            print(f"Fetched {len(series_list)} series total.")
            
            keywords = ["UFC", "MMA", "Boxing", "Formula", "F1", "Fight"]
            
            for s in series_list:
                title = s.get("title", "")
                slug = s.get("slug", "")
                
                # Check keywords
                matched = False
                for k in keywords:
                    if k.lower() in title.lower() or k.lower() in slug.lower():
                        matched = True
                        break
                
                if matched:
                    print(f"FOUND: ID={s.get('id')} | Slug={s.get('slug')} | Title={s.get('title')}")

if __name__ == "__main__":
    asyncio.run(fetch_all_series())
