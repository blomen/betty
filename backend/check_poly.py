import csv

# Check for events without away_team (likely futures)
with open('data/polymarket_events.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    no_away = []
    for row in reader:
        if not row['away_team'].strip():
            no_away.append(row)
    
    print(f'Found {len(no_away)} events without away_team:')
    for r in no_away[:30]:
        print(f"  {r['home_team']}")
