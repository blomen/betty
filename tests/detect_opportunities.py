import sys
import os
from pathlib import Path
from datetime import datetime
import collections
import logging

# Add backend to path
sys.path.append(os.path.join(str(Path(__file__).parent.parent), "backend"))

from src.db.models import get_session, Event, Odds, Provider
from src.analysis.arbitrage import find_arbitrage
from src.analysis.value import scan_for_value

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def detect():
    session = get_session()
    print("="*60)
    print(f"OPPORTUNITY DETECTION REPORT - {datetime.now()}")
    print("="*60)
    
    # 1. Fetch all matched events (odds from > 1 provider)
    # We need Python logic to group them properly for the analysis functions
    
    # Get all odds first (might be large in prod, but fine for test)
    all_odds = session.query(Odds).all()
    
    # Group by Event -> Market -> Outcome
    # Structure needed: {event_id: {market: {outcome: [odds_objs]}}}
    events_data = collections.defaultdict(
        lambda: collections.defaultdict(
            lambda: collections.defaultdict(list)
        )
    )
    
    for o in all_odds:
        events_data[o.event_id][o.market][o.outcome].append({
            "provider": o.provider_id,
            "odds": o.odds,
            "obj": o
        })
        
    print(f"Analyzing {len(events_data)} events...")
    
    arb_count = 0
    value_count = 0
    
    for event_id, markets in events_data.items():
        event = session.query(Event).get(event_id)
        if not event:
            continue
            
        for market, outcomes in markets.items():
            # --- Arbitrage Check ---
            # Prepare data: {outcome: [{provider, odds}]}
            arb_data = {}
            for out, odds_list in outcomes.items():
                arb_data[out] = [{"provider": x["provider"], "odds": x["odds"]} for x in odds_list]
                
            arb = find_arbitrage(event_id, market, arb_data, min_profit_pct=0.0)
            if arb:
                arb_count += 1
                print(f"\n[ARB] {event.sport} | {event.home_team} vs {event.away_team}")
                print(f"  Market: {market} | Profit: {arb.profit_pct}%")
                for s in arb.stakes:
                    print(f"    Bet {s['outcome']} @ {s['provider']} ({s['stake']:.2f} -> {s['return']:.2f})")
                    
            # --- Value Bet Check ---
            # We need Polymarket odds as "fair_odds"
            # Check if we have polymarket odds for this market
            poly_odds_map = {}
            for out, odds_list in outcomes.items():
                for o in odds_list:
                    if o["provider"] == "polymarket":
                        poly_odds_map[out] = o["odds"]
                        break
            
            if poly_odds_map:
                # We have fair odds, check other providers
                for out, odds_list in outcomes.items():
                    if out not in poly_odds_map:
                        continue
                        
                    fair_odds = poly_odds_map[out]
                    
                    # Filter out polymarket itself
                    other_providers = [x for x in odds_list if x["provider"] != "polymarket"]
                    
                    value_bets = scan_for_value(
                        event_id, market, out, fair_odds, other_providers, min_edge_pct=1.0
                    )
                    
                    for vb in value_bets:
                        value_count += 1
                        print(f"\n[VALUE] {event.sport} | {event.home_team} vs {event.away_team}")
                        print(f"  {vb.market} {vb.outcome}")
                        print(f"  {vb.provider}: {vb.provider_odds} (poly: {vb.fair_odds})")
                        print(f"  Edge: {vb.edge_pct}% | EV: {vb.expected_value:.2f}")

    print("\n" + "="*60)
    print(f"Scan Complete.")
    print(f"Arbitrage Opportunities: {arb_count}")
    print(f"Value Bets Found: {value_count}")
    
    session.close()

if __name__ == "__main__":
    detect()
