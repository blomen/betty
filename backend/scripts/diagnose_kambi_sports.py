"""
Diagnostic script: Discover what Kambi group.json actually returns for each sport.
Compares Kambi's group tree against Pinnacle's league coverage.
"""
import asyncio
import json
import aiohttp
import sys

KAMBI_BASE = "https://eu1.offering-api.kambicdn.com/offering/v2018"
BRAND = "ubse"  # Unibet
PARAMS = {"market": "SE", "lang": "sv_SE", "odds_format": "decimal"}


def extract_groups_recursive(obj, groups, depth=0):
    """Extract all groups from nested tree."""
    if isinstance(obj, dict):
        if "id" in obj and "name" in obj:
            groups.append({
                "id": obj["id"],
                "name": obj.get("name", obj.get("englishName", "")),
                "englishName": obj.get("englishName", ""),
                "sport": obj.get("sport", ""),
                "depth": depth,
                "termKey": obj.get("termKey", ""),
                "boCount": obj.get("boCount", 0),
                "eventCount": obj.get("eventCount", 0),
            })
        for key in ["group", "groups", "children"]:
            if key in obj and isinstance(obj[key], (list, dict)):
                extract_groups_recursive(obj[key], groups, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            extract_groups_recursive(item, groups, depth)


async def main():
    target_sports = sys.argv[1:] if len(sys.argv) > 1 else [
        "ice_hockey", "basketball", "football", "tennis", "esports",
        "handball", "volleyball", "cricket", "american_football", "curling"
    ]

    async with aiohttp.ClientSession() as session:
        # Fetch group tree
        url = f"{KAMBI_BASE}/{BRAND}/group.json"
        print(f"Fetching: {url}")
        async with session.get(url, params=PARAMS) as resp:
            data = await resp.json()

        # Extract all groups
        groups = []
        extract_groups_recursive(data, groups)
        print(f"\nTotal groups in tree: {len(groups)}")

        # Get unique sport names
        sport_names = sorted(set(g["sport"].lower() for g in groups if g["sport"]))
        print(f"\nAll sport names in Kambi group tree:")
        for s in sport_names:
            count = len([g for g in groups if g["sport"].lower() == s])
            print(f"  {s}: {count} groups")

        print("\n" + "=" * 80)

        # For each target sport, show the groups
        for sport in target_sports:
            sport_lower = sport.lower()
            # Match including aliases
            aliases = {
                "ice_hockey": ["ice_hockey", "ishockey"],
                "mma": ["mma", "martial_arts"],
                "esports": ["esports", "counter_strike", "dota", "league_of_legends",
                            "valorant", "call_of_duty", "rainbow_six", "e_basketball"],
                "rugby": ["rugby", "rugby_union", "rugby_league"],
            }
            match_names = aliases.get(sport_lower, [sport_lower])

            matching = [g for g in groups if g["sport"].lower() in match_names]

            print(f"\n{'=' * 80}")
            print(f"SPORT: {sport} (matching: {match_names})")
            print(f"Groups found: {len(matching)}")
            print(f"{'=' * 80}")

            if not matching:
                # Show what sports ARE available that might be related
                print(f"  *** NO GROUPS FOUND ***")
                print(f"  Available sport names: {sport_names}")
                continue

            # Sort by depth then name
            matching.sort(key=lambda g: (g["depth"], g["name"]))

            # Show hierarchy
            for g in matching:
                indent = "  " * g["depth"]
                event_info = f" [{g['eventCount']} events, {g['boCount']} betoffers]" if g['eventCount'] else ""
                print(f"  {indent}[depth={g['depth']}] {g['name']} (id={g['id']}, sport={g['sport']}){event_info}")

            # Count leaf groups (those with events)
            leaf_groups = [g for g in matching if g["eventCount"] > 0]
            total_events = sum(g["eventCount"] for g in leaf_groups)
            print(f"\n  Summary: {len(leaf_groups)} groups with events, ~{total_events} total events")

            # Now fetch actual event count from a few top groups
            if leaf_groups:
                print(f"\n  Fetching actual events from top groups...")
                for g in leaf_groups[:5]:
                    event_url = f"{KAMBI_BASE}/{BRAND}/betoffer/group/{g['id']}.json"
                    try:
                        async with session.get(event_url, params=PARAMS) as resp:
                            if resp.status == 200:
                                event_data = await resp.json()
                                events = event_data.get("events", [])
                                betoffers = event_data.get("betOffers", [])
                                # Count betOfferType IDs
                                type_ids = {}
                                for bo in betoffers:
                                    tid = bo.get("betOfferType", {}).get("id", "?")
                                    type_ids[tid] = type_ids.get(tid, 0) + 1
                                print(f"    {g['name']} (id={g['id']}): {len(events)} events, {len(betoffers)} betoffers, types={type_ids}")
                            elif resp.status == 429:
                                print(f"    {g['name']}: RATE LIMITED (429)")
                                await asyncio.sleep(5)
                            else:
                                print(f"    {g['name']}: HTTP {resp.status}")
                    except Exception as e:
                        print(f"    {g['name']}: Error: {e}")
                    await asyncio.sleep(1)  # Rate limit protection


if __name__ == "__main__":
    asyncio.run(main())
