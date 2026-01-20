"""
Export Polymarket data to CSV.
"""
import csv
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.db.models import init_db, get_session, Event, Odds

def export_polymarket_csv():
    init_db()
    session = get_session()
    
    # Query all events with Polymarket odds
    results = session.query(
        Event.sport,
        Event.league,
        Event.home_team,
        Event.away_team,
        Event.start_time,
        Odds.market,
        Odds.outcome,
        Odds.odds,
        Odds.updated_at
    ).join(Odds).filter(
        Odds.provider_id == 'polymarket'
    ).order_by(
        Event.sport, Event.start_time
    ).all()
    
    filename = f"polymarket_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = Path(__file__).parent / filename
    
    print(f"Exporting {len(results)} rows to {filepath}...")
    
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            "Sport", "League", "Home Team", "Away Team", "Start Time", 
            "Market", "Outcome", "Odds", "Last Updated"
        ])
        
        for row in results:
            writer.writerow([
                row.sport,
                row.league,
                row.home_team,
                row.away_team,
                row.start_time,
                row.market,
                row.outcome,
                f"{row.odds:.3f}",
                row.updated_at
            ])
            
    print("Done!")
    return str(filepath)

if __name__ == "__main__":
    export_polymarket_csv()
