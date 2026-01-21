import asyncio
import logging
import sys
import os
import aiohttp

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_kambi")

async def test_provider(name, base, brand):
    url = f"{base}/{brand}/group.json"
    logger.info(f"Testing {name}: {url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": f"https://www.{name}.com",
        "Referer": f"https://www.{name}.com/"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                logger.info(f"Status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    logger.info("Success! JSON received.")
                    # print first few chars
                    # logger.info(str(data)[:200])
                    
                    # Verify sports existence
                    def find_sport(obj, target):
                        if isinstance(obj, dict):
                            if obj.get("name", "").lower() == target: return True
                            for k,v in obj.items():
                                if find_sport(v, target): return True
                        elif isinstance(obj, list):
                            for i in obj:
                                if find_sport(i, target): return True
                        return False
                        
                    has_football = find_sport(data, "football")
                    logger.info(f"Has Football: {has_football}")
                else:
                    text = await resp.text()
                    logger.info(f"Response: {text[:200]}")
    except Exception as e:
        logger.error(f"Failed: {e}")

async def main():
    providers = [
        ("mrgreen-se", "https://eu1.offering-api.kambicdn.com/offering/v2018", "mrgreense"),
        ("casumo-se", "https://eu1.offering-api.kambicdn.com/offering/v2018", "casumose"),
        ("leovegas-se", "https://eu1.offering-api.kambicdn.com/offering/v2018", "leovegasse"),
    ]
    
    for name, base, brand in providers:
        await test_provider(name, base, brand)

if __name__ == "__main__":
    asyncio.run(main())
