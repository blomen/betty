"""
Query the DB for extraction results.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import func
from src.db.models import init_db, get_session, Event, Odds, Provider


def main():
    init_db()
    session = get_session()
    
    print("=" * 70)
    print("DATABASE SUMMARY")
    print("=" * 70)
    
    # Total counts
    total_events = session.query(Event).count()
    total_odds = session.query(Odds).count()
    print(f"\nTotal Events: {total_events}")
    print(f"Total Odds Records: {total_odds}")
    
    # Providers
    print("\n" + "-" * 50)
    print("ODDS BY PROVIDER:")
    print("-" * 50)
    provider_counts = session.query(
        Odds.provider_id, 
        func.count(Odds.id)
    ).group_by(Odds.provider_id).all()
    
    for provider, count in sorted(provider_counts, key=lambda x: -x[1]):
        print(f"  {provider:15} -> {count:6} odds")
    
    # Events by sport
    print("\n" + "-" * 50)
    print("EVENTS BY SPORT:")
    print("-" * 50)
    sport_counts = session.query(
        Event.sport, 
        func.count(Event.id)
    ).group_by(Event.sport).all()
    
    for sport, count in sorted(sport_counts, key=lambda x: -x[1]):
        print(f"  {sport:20} -> {count:5} events")
    
    # Matched events (multi-provider)
    print("\n" + "-" * 50)
    print("MATCHED EVENTS (with odds from multiple providers):")
    print("-" * 50)
    
    matched = session.query(Event).join(Odds).group_by(Event.id).having(
        func.count(func.distinct(Odds.provider_id)) > 1
    ).all()
    
    print(f"\nTotal matched: {len(matched)}")
    
    for event in matched[:15]:
        print(f"\n  {event.home_team} vs {event.away_team}")
        print(f"    Sport: {event.sport}, Start: {event.start_time}")
        
        # Group odds by provider
        odds_by_provider = {}
        for odds in event.odds:
            odds_by_provider.setdefault(odds.provider_id, []).append(
                f"{odds.market}/{odds.outcome}: {odds.odds:.2f}"
            )
        
        for provider, odds_list in odds_by_provider.items():
            print(f"    {provider}: {odds_list[:3]}{'...' if len(odds_list) > 3 else ''}")
    
    if len(matched) > 15:
        print(f"\n  ... and {len(matched) - 15} more matched events")


if __name__ == "__main__":
    main()
