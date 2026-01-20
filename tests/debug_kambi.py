"""Debug Kambi API - find match events with participants."""
import asyncio
import json
from src.utils.http import HTTPClient

async def debug_kambi():
    BASE_URL = "https://eu1.offering-api.kambicdn.com/offering/v2018"
    brand = "ubse"
    
    headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "accept": "application/json",
        "referer": "https://unibet.se/",
    }
    params = {"market": "SE", "lang": "sv_SE", "channel_id": "1", "client_id": "2"}
    
    async with HTTPClient({"rpm": 60}, headers) as client:
        # Get groups
        groups_url = f"{BASE_URL}/{brand}/group.json"
        groups_data = await client.get(groups_url, params=params)
        
        # Find football league groups (depth > 1)
        def find_groups(obj, results, depth=0):
            if isinstance(obj, dict):
                if "id" in obj and obj.get("sport") == "FOOTBALL":
                    results.append({"id": obj["id"], "name": obj.get("name", ""), "depth": depth})
                for v in obj.values():
                    find_groups(v, results, depth+1)
            elif isinstance(obj, list):
                for item in obj:
                    find_groups(item, results, depth)
        
        all_groups = []
        find_groups(groups_data, all_groups)
        
        # Filter to league level (depth > 1)
        league_groups = [g for g in all_groups if g["depth"] > 1][:5]
        print(f"Found {len(league_groups)} league groups")
        
        for group in league_groups:
            print(f"\n--- Checking group: {group['name']} (depth {group['depth']}) ---")
            
            events_url = f"{BASE_URL}/{brand}/betoffer/group/{group['id']}.json"
            data = await client.get(events_url, params=params)
            
            if not data:
                continue
            
            events = data.get("events", [])
            
            # Find events with awayName (actual matches)
            matches = [e for e in events if e.get("awayName")]
            print(f"Total events: {len(events)}, Matches with awayName: {len(matches)}")
            
            if matches:
                match = matches[0]
                print(f"\nMatch found:")
                print(f"  homeName: {match.get('homeName')}")
                print(f"  awayName: {match.get('awayName')}")
                print(f"  name: {match.get('name')}")
                print(f"  participants: {match.get('participants', [])}")
                break

if __name__ == "__main__":
    asyncio.run(debug_kambi())
