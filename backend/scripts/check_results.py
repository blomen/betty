"""Quick validation script for extraction results."""
import sqlite3

conn = sqlite3.connect('data/arnold.db')
c = conn.cursor()

print("=== Provider Odds Count ===")
c.execute("""
    SELECT p.name, COUNT(o.id) as odds, COUNT(DISTINCT o.event_id) as events,
           ROUND(CAST(COUNT(o.id) AS FLOAT) / COUNT(DISTINCT o.event_id), 2) as ratio
    FROM odds o JOIN providers p ON o.provider_id = p.id
    GROUP BY p.name
    ORDER BY COUNT(o.id) DESC
""")
print(f"{'Provider':16} | {'Odds':>5} | {'Events':>6} | {'Ratio':>5}")
print("-" * 45)
for row in c.fetchall():
    print(f"{row[0]:16} | {row[1]:5} | {row[2]:6} | {row[3]}")

print("\n=== Sport Breakdown (non-Pinnacle) ===")
c.execute("""
    SELECT e.sport, COUNT(DISTINCT e.id) as events, COUNT(o.id) as odds,
           COUNT(DISTINCT o.provider_id) as providers
    FROM events e
    JOIN odds o ON e.id = o.event_id
    JOIN providers p ON o.provider_id = p.id
    WHERE p.name != 'Pinnacle'
    GROUP BY e.sport
    ORDER BY COUNT(DISTINCT e.id) DESC
""")
print(f"{'Sport':20} | {'Events':>6} | {'Odds':>5} | {'Providers':>9}")
print("-" * 50)
for row in c.fetchall():
    print(f"{row[0]:20} | {row[1]:6} | {row[2]:5} | {row[3]:9}")

print("\n=== MrGreen Sport Breakdown ===")
c.execute("""
    SELECT e.sport, COUNT(DISTINCT e.id) as events, COUNT(o.id) as odds
    FROM events e
    JOIN odds o ON e.id = o.event_id
    JOIN providers p ON o.provider_id = p.id
    WHERE p.name = 'Mr Green'
    GROUP BY e.sport
    ORDER BY COUNT(DISTINCT e.id) DESC
""")
for row in c.fetchall():
    print(f"  {row[0]:20} | {row[1]:4} events | {row[2]:5} odds")

print("\n=== Outcome Normalization ===")
c.execute("""
    SELECT p.name,
           ROUND(100.0 * SUM(CASE WHEN outcome IN ('home','away','draw','over','under') THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
    FROM odds o
    JOIN providers p ON o.provider_id = p.id
    GROUP BY p.name
""")
for row in c.fetchall():
    print(f"  {row[0]:16}: {row[1]}%")

conn.close()
