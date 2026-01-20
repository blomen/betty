"""Debug script to compare event IDs between Polymarket and Kambi."""
from src.db.models import get_session, Event, Odds

session = get_session()

# Get sample Polymarket events
print("=" * 70)
print("POLYMARKET EVENTS (sample)")
print("=" * 70)
poly_events = session.query(Event).join(Odds).filter(
    Odds.provider_id == "polymarket"
).limit(20).all()

for e in poly_events[:10]:
    print(f"\n  ID: {e.id}")
    print(f"  {e.home_team} vs {e.away_team}")
    print(f"  Sport: {e.sport}")

# Get sample Kambi events
print("\n" + "=" * 70)
print("KAMBI (UNIBET) EVENTS (sample)")
print("=" * 70)
kambi_events = session.query(Event).join(Odds).filter(
    Odds.provider_id == "unibet"
).limit(20).all()

for e in kambi_events[:10]:
    print(f"\n  ID: {e.id}")
    print(f"  {e.home_team} vs {e.away_team}")
    print(f"  Sport: {e.sport}")

# Look for similar teams
print("\n" + "=" * 70)
print("SEARCHING FOR POTENTIAL MATCHES")
print("=" * 70)

poly_ids = {e.id for e in session.query(Event).join(Odds).filter(Odds.provider_id == "polymarket").all()}
kambi_ids = {e.id for e in session.query(Event).join(Odds).filter(Odds.provider_id == "unibet").all()}

# Check intersection
common = poly_ids & kambi_ids
print(f"\nExact matches: {len(common)}")

# Find similar IDs (same sport + date)
poly_prefixes = {id.rsplit(":", 1)[0]: id for id in poly_ids}  # sport:home:away
kambi_prefixes = {id.rsplit(":", 1)[0]: id for id in kambi_ids}

print(f"\nPolymarket unique prefixes: {len(poly_prefixes)}")
print(f"Kambi unique prefixes: {len(kambi_prefixes)}")

# Look at football events specifically
print("\n" + "=" * 70)
print("FOOTBALL/EPL EVENTS COMPARISON")
print("=" * 70)

poly_football = [id for id in poly_ids if "Premier League" in id or ":football:" in id or ":epl:" in id]
kambi_football = [id for id in kambi_ids if ":football:" in id]

print(f"\nPolymarket football events: {len(poly_football)}")
for id in list(poly_football)[:5]:
    print(f"  {id}")

print(f"\nKambi football events: {len(kambi_football)}")
for id in list(kambi_football)[:5]:
    print(f"  {id}")

session.close()
