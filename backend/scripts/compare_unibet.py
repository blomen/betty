"""Compare scraped unibet bet history against DB."""
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
db = sqlite3.connect("data/firev.db")

# Scraped from unibet DOM (56 bets from earlier successful scrape)
scraped = [
    {"odds": 1.84, "stake": 140, "result": "won", "payout": 258},
    {"odds": 4.70, "stake": 30, "result": "lost", "payout": 0},
    {"odds": 4.70, "stake": 35, "result": "lost", "payout": 0},
    {"odds": 4.75, "stake": 35, "result": "lost", "payout": 0},
    {"odds": 3.50, "stake": 32, "result": "lost", "payout": 0},
    {"odds": 3.00, "stake": 80, "result": "won", "payout": 240},
    {"odds": 12.50, "stake": 14, "result": "lost", "payout": 0},
    {"odds": 3.25, "stake": 50, "result": "won", "payout": 162},
    {"odds": 2.15, "stake": 130, "result": "lost", "payout": 0},
    {"odds": 3.25, "stake": 60, "result": "won", "payout": 195},
    {"odds": 4.35, "stake": 40, "result": "lost", "payout": 0},
    {"odds": 5.50, "stake": 30, "result": "lost", "payout": 0},
    {"odds": 4.00, "stake": 50, "result": "lost", "payout": 0},
    {"odds": 9.00, "stake": 20, "result": "lost", "payout": 0},
    {"odds": 6.75, "stake": 30, "result": "lost", "payout": 0},
    {"odds": 6.10, "stake": 35, "result": "lost", "payout": 0},
    {"odds": 5.50, "stake": 40, "result": "won", "payout": 220},
    {"odds": 8.50, "stake": 25, "result": "lost", "payout": 0},
    {"odds": 5.60, "stake": 60, "result": "lost", "payout": 0},
    {"odds": 8.50, "stake": 40, "result": "lost", "payout": 0},
    {"odds": 4.10, "stake": 35, "result": "won", "payout": 144},
    {"odds": 4.75, "stake": 45, "result": "lost", "payout": 0},
    {"odds": 4.00, "stake": 70, "result": "lost", "payout": 0},
    {"odds": 13.00, "stake": 20, "result": "lost", "payout": 0},
    {"odds": 4.50, "stake": 35, "result": "lost", "payout": 0},
    {"odds": 6.00, "stake": 25, "result": "lost", "payout": 0},
    {"odds": 4.35, "stake": 40, "result": "lost", "payout": 0},
    {"odds": 6.40, "stake": 25, "result": "lost", "payout": 0},
    {"odds": 5.00, "stake": 40, "result": "lost", "payout": 0},
    {"odds": 2.08, "stake": 170, "result": "won", "payout": 354},
    {"odds": 2.06, "stake": 225, "result": "won", "payout": 464},
    {"odds": 3.85, "stake": 60, "result": "won", "payout": 231},
    {"odds": 3.85, "stake": 60, "result": "lost", "payout": 0},
    {"odds": 7.50, "stake": 40, "result": "lost", "payout": 0},
    {"odds": 2.60, "stake": 140, "result": "won", "payout": 364},
    {"odds": 4.20, "stake": 80, "result": "lost", "payout": 0},
    {"odds": 3.65, "stake": 100, "result": "lost", "payout": 0},
    {"odds": 8.50, "stake": 40, "result": "lost", "payout": 0},
    {"odds": 5.50, "stake": 60, "result": "lost", "payout": 0},
    {"odds": 6.00, "stake": 60, "result": "lost", "payout": 0},
    {"odds": 4.50, "stake": 40, "result": "lost", "payout": 0},
    {"odds": 6.00, "stake": 35, "result": "won", "payout": 210},
    {"odds": 2.12, "stake": 100, "result": "won", "payout": 212},
    {"odds": 4.70, "stake": 70, "result": "lost", "payout": 0},
    {"odds": 9.00, "stake": 35, "result": "won", "payout": 315},
    {"odds": 4.60, "stake": 80, "result": "won", "payout": 368},
    {"odds": 2.90, "stake": 170, "result": "lost", "payout": 0},
    {"odds": 12.00, "stake": 30, "result": "lost", "payout": 0},
    {"odds": 7.50, "stake": 40, "result": "won", "payout": 300},
    {"odds": 14.00, "stake": 15, "result": "lost", "payout": 0},
    {"odds": 7.00, "stake": 60, "result": "lost", "payout": 0},
    {"odds": 3.35, "stake": 100, "result": "lost", "payout": 0},
    {"odds": 5.30, "stake": 45, "result": "lost", "payout": 0},
    {"odds": 6.40, "stake": 40, "result": "won", "payout": 256},
    {"odds": 2.02, "stake": 170, "result": "lost", "payout": 0},
    {"odds": 2.35, "stake": 1000, "result": "lost", "payout": 0},
]

db_bets = db.execute("""
    SELECT id, odds, stake, result, payout, placed_at
    FROM bets WHERE provider_id = 'unibet' AND profile_id = 5 ORDER BY placed_at
""").fetchall()

print(f"MIRROR: {len(scraped)} bets | DB: {len(db_bets)} bets")
print()

# Find discrepancies
print("=== DISCREPANCIES ===")
used_scraped = set()
for bid, odds, stake, db_result, db_payout, placed in db_bets:
    for i, sb in enumerate(scraped):
        if i in used_scraped:
            continue
        if abs(sb["odds"] - odds) < 0.02 and abs(sb["stake"] - stake) < 0.02:
            issues = []
            if sb["result"] != db_result:
                issues.append(f"result: db={db_result} -> mirror={sb['result']}")
            if abs((db_payout or 0) - sb["payout"]) > 1:
                issues.append(f"payout: db={db_payout or 0:.0f} -> mirror={sb['payout']:.0f}")
            if issues:
                print(f"  #{bid}: {' | '.join(issues)}")
            used_scraped.add(i)
            break

# DB bets not in mirror
print()
print("=== DB BETS NOT IN MIRROR ===")
mirror_used = [False] * len(scraped)
for bid, odds, stake, result, payout, placed in db_bets:
    found = False
    for i, sb in enumerate(scraped):
        if mirror_used[i]:
            continue
        if abs(sb["odds"] - odds) < 0.02 and abs(sb["stake"] - stake) < 0.02:
            mirror_used[i] = True
            found = True
            break
    if not found:
        dt = placed[:10] if placed else "?"
        print(f"  #{bid} {dt} odds={odds} stake={stake} {result} pay={payout or 0:.0f}")

# Mirror bets not in DB
print()
print("=== MIRROR BETS NOT IN DB ===")
db_used = [False] * len(db_bets)
for sb in scraped:
    found = False
    for j, (bid, odds, stake, *_) in enumerate(db_bets):
        if db_used[j]:
            continue
        if abs(sb["odds"] - odds) < 0.02 and abs(sb["stake"] - stake) < 0.02:
            db_used[j] = True
            found = True
            break
    if not found:
        print(f"  odds={sb['odds']} stake={sb['stake']} {sb['result']} pay={sb['payout']:.0f}")
