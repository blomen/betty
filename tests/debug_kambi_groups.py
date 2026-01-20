import asyncio
import sys
import os
import logging
import json

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from backend.src.core.transport import HttpTransport

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_kambi_groups")

async def main():
    transport = HttpTransport()
    url = "https://eu1.offering-api.kambicdn.com/offering/v2018/ubse/group.json"
    
    logger.info(f"Fetching groups from: {url}")
    data = await transport.get(url, params={"lang": "en_US", "market": "US"}) # Use English for easier debugging
    
    if not data:
        logger.error("No data received")
        return

    # Recursive printer to find relevant sports
    def print_sports(groups, depth=0):
        for g in groups:
            name = g.get("name", "N/A")
            english_name = g.get("englishName", name)
            gid = g.get("id")
            sport = g.get("sport", "N/A")
            
            # Filter output to relevant potential matches
            if any(k in english_name.lower() for k in ["mma", "ufc", "fight", "rugby"]):
                print(f"{'  '*depth}- [ID:{gid}] Name: '{name}' | English: '{english_name}' | SportKey: '{sport}'")
            
            # Recurse
            children = g.get("groups", []) or g.get("children", [])
            if children:
                print_sports(children, depth+1)

    print("\n[Searching for MMA/Rugby related groups...]")
    print_sports(data.get("group", {}).get("groups", []))

if __name__ == "__main__":
    asyncio.run(main())
