import aiohttp
import asyncio
import json

async def fetch_tags():
    url = "https://gamma-api.polymarket.com/tags"
    
    async with aiohttp.ClientSession() as session:
        keywords = ["Boxing", "MMA", "UFC", "Formula", "F1", "Racing", "Combat", "Motor"]
        for offset in range(0, 1000, 100):
            params = {"offset": offset}
            print(f"Scanning offset {offset}...")
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                if not data:
                    print("End of data.")
                    break
                
                for t in data:
                    label = t.get("label", "")
                    slug = t.get("slug", "")
                    tid = t.get("id")
                    
                    for k in keywords:
                        if k.lower() in label.lower() or k.lower() in slug.lower():
                            print(f"TAG: ID={tid} | Slug={slug} | Label={label}")
                            # Don't break here, find all matches

if __name__ == "__main__":
    asyncio.run(fetch_tags())
