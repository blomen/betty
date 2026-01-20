import asyncio
import sys
import os
import logging
import json

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from backend.src.core.transport import HttpTransport

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_kambi_lines")

async def main():
    transport = HttpTransport()
    # Use a known group ID for a popular sport (e.g. Premier League or NBA)
    # NBA Group ID usually can be found or we can just fetch a high level one
    # From previous logs: NBA matches were found.
    # Let's try fetching events for a known group or just search for one.
    
    # URL for "Any" group (often root works or we recurse) - Let's use the one from unibet logs if possible
    # But better to just get the group list again and pick one valid one.
    
    base = "https://eu1.offering-api.kambicdn.com/offering/v2018/ubse"
    
    # 1. Get Groups to find a valid one
    logger.info("Fetching groups...")
    groups = await transport.get(f"{base}/group.json", params={"lang": "en_US", "market": "US"})
    
    # Flatten and find NBA or Premier League
    def find_group(g, target):
        if target.lower() in g.get("name", "").lower(): return g["id"]
        for child in g.get("groups", []) or g.get("children", []):
            res = find_group(child, target)
            if res: return res
        return None
        
    group_id = find_group(groups.get("group", {}), "NBA")
    if not group_id:
        group_id = find_group(groups.get("group", {}), "Premier League")
        
    logger.info(f"Using Group ID: {group_id}")
    
    # 2. Fetch Events
    url = f"{base}/betoffer/group/{group_id}.json"
    logger.info(f"Fetching events from: {url}")
    data = await transport.get(url, params={"lang": "en_US", "market": "US"})
    
    # 3. Inspect BetOffers for Lines
    betoffers = data.get("betOffers", [])
    
    found = 0
    for bo in betoffers:
        criterion = bo.get("criterion", {})
        label = criterion.get("label", "")
        
        # Look for Spread or Total
        if "Total" in label or "Handicap" in label or "Spread" in label:
            print(f"\n[Market: {label}] ID: {bo.get('id')}")
            # print raw outcome structure
            outcomes = bo.get("outcomes", [])
            for out in outcomes:
                print(f"  - Label: {out.get('label')} | Odds: {out.get('odds')} | Line: {out.get('line')} | Sc: {out.get('score')}")
                # Check for other potential fields
                if 'line' in out:
                    print(f"    -> FOUND LINE: {out['line']}")
                
            found += 1
            if found > 5: break
            
    await transport.close()

if __name__ == "__main__":
    asyncio.run(main())
