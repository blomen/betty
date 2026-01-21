import aiohttp
import asyncio
import json

async def brute_force_series():
    url = "https://gamma-api.polymarket.com/series"
    
    keywords = ["UFC", "MMA", "Boxing", "Formula", "F1", "Fight", "Fury", "Usyk", "Verstappen", "Hamilton"]
    
    with open("backend/src/series_dump.txt", "w", encoding="utf-8") as f:
        async with aiohttp.ClientSession() as session:
            for offset in range(0, 5000, 100): # Scan more
                print(f"Scanning offset {offset}...")
                params = {"limit": 100, "offset": offset}
                
                async with session.get(url, params=params) as resp:
                    data = await resp.json()
                    if not data:
                        print("End of data.")
                        break
                    
                    for s in data:
                        title = s.get("title", "")
                        slug = s.get("slug", "")
                        sid = s.get("id")
                        f.write(f"SERIES: ID={sid} | Slug={slug} | Title={title}\n")

if __name__ == "__main__":
    asyncio.run(brute_force_series())
