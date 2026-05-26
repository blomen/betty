import json, re

d = json.load(open("c:/Users/rasmu/arnold/tmp_poly.json"))
dom = d.get("dom_preview", "")
lines = dom.split("\n")
print(f"Total lines: {len(lines)}")
for i, line in enumerate(lines):
    a = line.strip()
    if a in ("Lost", "Claimed"):
        ctx = [lines[j].strip() for j in range(i+1, min(i+5, len(lines)))]
        print(f"  [{i}] {a}: {ctx}")
