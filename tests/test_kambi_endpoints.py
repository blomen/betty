import asyncio
import aiohttp
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kambi_test")

BRANDS = {
    "unibet": "ub",
    "leovegas": "leovegas",
    "casumo": "casumo",
    "expekt": "expekt", # or expektse?
    "mrgreen": "mrgreen",
}

BASE_URL = "https://eu1.offering-api.kambicdn.com/offering/v2018"

PROPOSED_CONFIGS = {
    "leovegas": "leovegas",
    "casumo": "casumo",
    "expekt": "expekt",
    "mrgreen": "mrgreen",
}

async def check_brand(session, name, code):
    url = f"{BASE_URL}/{code}/group.json"
    params = {"lang": "en_GB", "market": "GB"} # Generic params
    try:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                if hasattr(data, 'get') and data.get('group'):
                    logger.info(f"[SUCCESS] {name} -> {code} (Status: {resp.status})")
                    return True
            logger.warning(f"[FAIL] {name} -> {code} (Status: {resp.status})")
            # logger.info(f"Response: {await resp.text()}")
    except Exception as e:
        logger.error(f"[ERROR] {name} -> {code}: {e}")
    return False

async def main():
    async with aiohttp.ClientSession() as session:
        # 1. Test what we have in configs
        logger.info("Testing current assumptions...")
        
        # Unibet (Control)
        await check_brand(session, "Unibet", "ub") 
        
        # Test others
        await check_brand(session, "LeoVegas", "leovegas")
        await check_brand(session, "Casumo", "casumo")
        await check_brand(session, "Expekt", "expekt")
        await check_brand(session, "MrGreen", "mrgreen")


        # Proven working
        await check_brand(session, "Expekt", "expektse")
        await check_brand(session, "Unibet", "ubse")

        # Variations
        logger.info("Testing SE variations...")
        brands = ["leovegas", "casumo", "mrgreen", "888", "paf"]
        suffixes = ["se", "_se", "-se", ""]
        
        for brand in brands:
            for suffix in suffixes:
                code = f"{brand}{suffix}"
                await check_brand(session, brand.title(), code)

if __name__ == "__main__":
    asyncio.run(main())
