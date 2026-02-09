"""
Diagnostic: Discover what each betOfferType ID means in Kambi's API.
Fetches events from ice_hockey and basketball groups, logs all betOfferType labels.
"""
import asyncio
import aiohttp
from collections import defaultdict

KAMBI_BASE = "https://eu1.offering-api.kambicdn.com/offering/v2018"
BRAND = "ubse"
PARAMS = {"market": "SE", "lang": "sv_SE", "odds_format": "decimal"}

# Top-level groups with lots of events
GROUPS = {
    "ice_hockey_top": 1000093191,    # Ishockey (top level)
    "ice_hockey_nhl": 1000093657,    # NHL
    "ice_hockey_shl": 1000094968,    # SHL
    "basketball_top": 1000093204,    # Basket (top level)
    "basketball_nba": 1000093652,    # NBA
    "basketball_euroleague": 1000093451,  # Euroleague
}


async def main():
    async with aiohttp.ClientSession() as session:
        for label, group_id in GROUPS.items():
            url = f"{KAMBI_BASE}/{BRAND}/betoffer/group/{group_id}.json"
            print(f"\n{'='*80}")
            print(f"GROUP: {label} (id={group_id})")
            print(f"{'='*80}")

            try:
                async with session.get(url, params=PARAMS) as resp:
                    if resp.status == 429:
                        print("  RATE LIMITED - waiting 10s")
                        await asyncio.sleep(10)
                        async with session.get(url, params=PARAMS) as resp2:
                            data = await resp2.json()
                    elif resp.status != 200:
                        print(f"  HTTP {resp.status}")
                        continue
                    else:
                        data = await resp.json()
            except Exception as e:
                print(f"  Error: {e}")
                continue

            events = data.get("events", [])
            betoffers = data.get("betOffers", [])
            outcomes = data.get("outcomes", [])

            print(f"  Events: {len(events)}, BetOffers: {len(betoffers)}, Outcomes: {len(outcomes)}")

            # Group betOffers by type ID and collect labels
            type_info = defaultdict(lambda: {"count": 0, "labels": set(), "sample_outcomes": []})
            for bo in betoffers:
                type_id = bo.get("betOfferType", {}).get("id", "?")
                type_name = bo.get("betOfferType", {}).get("name", "?")
                criterion = bo.get("criterion", {})
                eng_label = criterion.get("englishLabel", "")
                label_str = criterion.get("label", "")

                info = type_info[type_id]
                info["count"] += 1
                info["type_name"] = type_name
                info["labels"].add(f"{eng_label} | {label_str}")

                # Collect sample outcome names for this type
                if len(info["sample_outcomes"]) < 3:
                    for oc_ref in bo.get("outcomes", [])[:3]:
                        oc = next((o for o in outcomes if o.get("id") == oc_ref.get("id")), oc_ref)
                        info["sample_outcomes"].append(oc.get("label", "?"))

            print(f"\n  BetOfferType breakdown:")
            for type_id in sorted(type_info.keys(), key=lambda x: type_info[x]["count"], reverse=True):
                info = type_info[type_id]
                print(f"\n  TYPE {type_id} ({info.get('type_name', '?')}): {info['count']} betoffers")
                for lbl in sorted(info["labels"])[:5]:
                    print(f"    Label: {lbl}")
                if info["sample_outcomes"]:
                    print(f"    Sample outcomes: {info['sample_outcomes'][:6]}")

            await asyncio.sleep(2)  # Rate limit protection


if __name__ == "__main__":
    asyncio.run(main())
