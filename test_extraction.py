"""Simple extraction test script without Rich UI"""
import asyncio
from backend.src.db.models import init_db, get_session, Event, Odds
from backend.src.pipeline import ExtractionPipeline
from sqlalchemy import func

async def main():
    print("Initializing database...")
    init_db()

    print("Running Polymarket extraction...")
    pipeline = ExtractionPipeline()

    results = await pipeline.run(
        polymarket=True,
        providers=None,
        on_progress=lambda msg: print(f"  {msg}")
    )

    print("\n" + "="*60)
    print("EXTRACTION RESULTS")
    print("="*60)

    # Show extraction results
    if "polymarket" in results:
        poly = results["polymarket"]
        print(f"\nPolymarket:")
        print(f"  Events processed: {poly.get('events_processed', 0)}")
        print(f"  New odds: {poly.get('odds_new', 0)}")

    print(f"\nTotal events: {results.get('total_events', 0)}")
    print(f"Matched events: {results.get('matched_events', 0)}")

    # Query database for sport breakdown
    print("\n" + "="*60)
    print("DATABASE SPORT BREAKDOWN")
    print("="*60)

    session = get_session()

    # Get events grouped by sport
    sport_counts = session.query(
        Event.sport,
        func.count(Event.id).label('count')
    ).group_by(Event.sport).order_by(func.count(Event.id).desc()).all()

    if sport_counts:
        print(f"\n{'Sport':<20} {'Event Count':>12}")
        print("-" * 35)
        for sport, count in sport_counts:
            print(f"{sport:<20} {count:>12}")

        total = sum(count for _, count in sport_counts)
        print("-" * 35)
        print(f"{'TOTAL':<20} {total:>12}")
    else:
        print("\nNo events found in database")

    # Show sample events
    print("\n" + "="*60)
    print("SAMPLE EVENTS (first 10)")
    print("="*60)

    sample_events = session.query(Event).limit(10).all()
    for event in sample_events:
        print(f"\n{event.sport}: {event.home_team} vs {event.away_team}")
        print(f"  Canonical ID: {event.canonical_id}")
        print(f"  Start time: {event.start_time}")

        # Count odds for this event
        odds_count = session.query(Odds).filter(Odds.event_id == event.id).count()
        print(f"  Odds entries: {odds_count}")

    session.close()

if __name__ == "__main__":
    asyncio.run(main())
