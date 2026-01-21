import asyncio
import logging
import sys
import os
import aiohttp
import json

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from src.factory import ExtractorFactory

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("debug_poly")

async def analyze_sport(session, sport_name: str, series_id: int):
    base_url = "https://gamma-api.polymarket.com/events"
    
    # 1. Raw count (just series_id)
    params_raw = {
        "series_id": series_id,
        "closed": "false",
        "limit": 1000 # Max limit to check volume
    }
    
    # 2. Tag filtered (series_id + tag_id)
    params_tag = {
        "series_id": series_id,
        "tag_id": 100639, # Game Bets
        "closed": "false",
        "limit": 1000
    }
    
    try:
        async with session.get(base_url, params=params_raw) as resp:
            data_raw = await resp.json()
            count_raw = len(data_raw)
            
        async with session.get(base_url, params=params_tag) as resp:
            data_tag = await resp.json()
            count_tag = len(data_tag)
            
        # 3. Liquidity filtered (manual check on tag data)
        count_active = 0
        for item in data_tag:
             markets = item.get("markets", [])
             is_active = False
             for m in markets:
                 prices_raw = m.get("outcomePrices", "[]")
                 prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])
                 prices = [float(p) for p in prices]
                 
                 # Check active logic from code: any(0.02 < p < 0.98)
                 if any(0.02 < p < 0.98 for p in prices):
                     is_active = True
                     break
             if is_active:
                 count_active += 1

        print(f"{sport_name:<20} | Raw: {count_raw:<5} | Tag(100639): {count_tag:<5} | Active(Liq): {count_active:<5} | Lost to Tag: {count_raw - count_tag:<4} | Lost to Liq: {count_tag - count_active:<4}")
        
    except Exception as e:
        print(f"{sport_name:<20} | ERROR: {e}")

async def main():
    factory = ExtractorFactory.get_instance()
    sports = factory.sports
    
    print(f"{'SPORT':<20} | {'RAW':<5} | {'TAGGED':<5} | {'ACTIVE':<5} | {'LOSS(TAG)':<4} | {'LOSS(LIQ)':<4}")
    print("-" * 80)
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for s in sports:
            if not s.polymarket_series_id:
                continue
            # Limit to a few major sports for speed, or run all? 
            # Let's run a batch of important ones.
            if s.name in ["Premier League", "NBA", "NFL", "NHL", "Champions League", "La Liga", "Serie A"]:
                tasks.append(analyze_sport(session, s.name, s.polymarket_series_id))
        
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
