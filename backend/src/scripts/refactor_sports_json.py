import json
import os

def refactor():
    path = "backend/src/config/sports.json"
    if not os.path.exists(path):
        print("f{path} not found")
        return

    with open(path, "r") as f:
        data = json.load(f)

    # Define groups
    groups = {
        "football": {"name": "Football", "kambi": "football", "888": 2, "leagues": []},
        "basketball": {"name": "Basketball", "kambi": "basketball", "888": 229, "leagues": []},
        "ice_hockey": {"name": "Ice Hockey", "kambi": "ice_hockey", "888": 362, "leagues": []},
        "american_football": {"name": "American Football", "kambi": "american_football", "888": 6, "leagues": []},
        "baseball": {"name": "Baseball", "kambi": "baseball", "888": 363, "leagues": []},
        "tennis": {"name": "Tennis", "kambi": "tennis", "888": 4, "leagues": []},
        "cricket": {"name": "Cricket", "kambi": "cricket", "888": 416, "leagues": []},
        "rugby": {"name": "Rugby", "kambi": "rugby", "888": 447, "leagues": []},
        "esports": {"name": "Esports", "kambi": "esports", "888": 8229, "leagues": []},
        "mma": {"name": "MMA", "kambi": "mma", "888": 438, "leagues": []},
        "boxing": {"name": "Boxing", "kambi": "boxing", "888": 5, "leagues": []},
        "motorsports": {"name": "Motorsports", "kambi": "motorsports", "888": 8, "leagues": []},
    }

    # Group items
    for item in data:
        kambi_sport = item.get("kambi_sport", "").lower()
        
        # Override for specific codes/names if kambi_sport is missing or weird
        if not kambi_sport:
            # Try to guess
            if "football" in item["name"].lower(): kambi_sport = "football"
        
        target_group = groups.get(kambi_sport)
        if not target_group:
            print(f"WARNING: Unknown group for {item['name']} ({kambi_sport})")
            continue
            
        # Create lean league object
        league = {"name": item["name"], "code": item["code"]}
        
        # Keep specialized IDs
        if "polymarket_series_id" in item:
            league["polymarket_series_id"] = item["polymarket_series_id"]
        if "polymarket_slug" in item:
             league["polymarket_slug"] = item["polymarket_slug"]
        if "polymarket_tag_id" in item:
             league["polymarket_tag_id"] = item["polymarket_tag_id"]
             
        # Only add provider overrides if different from default
        if item.get("sport888_id") != target_group["888"]:
             league["sport888_id"] = item.get("sport888_id")
             
        target_group["leagues"].append(league)

    # Build final list
    final_output = []
    for key, g in groups.items():
        if not g["leagues"]: continue
        
        group_obj = {
            "key": key,
            "name": g["name"],
            "defaults": {
                "kambi_sport": g["kambi"],
                "sport888_id": g["888"]
            },
            "leagues": g["leagues"]
        }
        final_output.append(group_obj)

    # Write back
    with open(path, "w", encoding='utf-8') as f:
        json.dump(final_output, f, indent=4)
    print("Refactoring complete.")

if __name__ == "__main__":
    refactor()
