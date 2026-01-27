#!/usr/bin/env python3
"""
Bonus Bet Matching Tool

Find the best hedge for a bonus bet.

Usage:
    python scripts/bonus_matcher.py \
        --event "football:arsenal:chelsea:20260127" \
        --market "1x2" \
        --anchor-provider unibet \
        --anchor-outcome home \
        --anchor-odds 2.5 \
        --stake 100 \
        --free-bet

    python scripts/bonus_matcher.py \
        --event "football:arsenal:chelsea:20260127" \
        --market "1x2" \
        --anchor-provider unibet \
        --anchor-outcome home \
        --anchor-odds 2.5 \
        --stake 100 \
        --counterparts bet365,betsson
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models import init_db, get_session, Odds
from src.analysis.bonus import find_best_hedge


def main():
    parser = argparse.ArgumentParser(description='Find best hedge for bonus bet')
    parser.add_argument('--event', required=True, help='Event canonical ID')
    parser.add_argument('--market', required=True, help='Market type (1x2, over_under_2.5)')
    parser.add_argument('--anchor-provider', required=True, help='Anchor provider ID')
    parser.add_argument('--anchor-outcome', required=True, help='Anchor outcome')
    parser.add_argument('--anchor-odds', required=True, type=float, help='Anchor odds')
    parser.add_argument('--stake', required=True, type=float, help='Stake amount')
    parser.add_argument('--free-bet', action='store_true', help='Is this a free bet?')
    parser.add_argument('--counterparts', help='Comma-separated list of counterpart providers')

    args = parser.parse_args()

    init_db()
    db = get_session()

    # Query opposing odds
    query = db.query(Odds).filter(
        Odds.event_id == args.event,
        Odds.market == args.market,
        Odds.outcome != args.anchor_outcome,
        Odds.provider_id != args.anchor_provider
    )

    if args.counterparts:
        counterpart_list = args.counterparts.split(',')
        query = query.filter(Odds.provider_id.in_(counterpart_list))

    opposing_odds = query.all()

    # Format for find_best_hedge
    opposing_list = [
        {
            "provider": o.provider_id,
            "outcome": o.outcome,
            "odds": o.odds
        }
        for o in opposing_odds
    ]

    # Find best hedge
    result = find_best_hedge(
        event_id=args.event,
        market=args.market,
        anchor_provider=args.anchor_provider,
        anchor_outcome=args.anchor_outcome,
        anchor_odds=args.anchor_odds,
        anchor_stake=args.stake,
        opposing_odds_list=opposing_list,
        is_free_bet=args.free_bet
    )

    if result:
        print("\n" + "=" * 70)
        print("BEST HEDGE FOUND")
        print("=" * 70)
        print(f"\nANCHOR BET:")
        print(f"  Provider:  {result.anchor_provider}")
        print(f"  Outcome:   {result.anchor_outcome}")
        print(f"  Odds:      {result.anchor_odds}")
        print(f"  Stake:     ${result.anchor_stake:.2f}")

        print(f"\nHEDGE BET:")
        print(f"  Provider:  {result.hedge_provider}")
        print(f"  Outcome:   {result.hedge_outcome}")
        print(f"  Odds:      {result.hedge_odds}")
        print(f"  Stake:     ${result.hedge_stake:.2f}")

        print(f"\nRESULTS:")
        if args.free_bet:
            print(f"  Retention: {result.retention_pct:.1f}%")
            print(f"  Profit:    ${-result.qualifying_loss:.2f}")
        else:
            print(f"  Loss:      ${result.qualifying_loss:.2f}")
            print(f"  Retention: {result.retention_pct:.1f}%")
        print()
    else:
        print("\nNo suitable hedge found.")
        print("Reasons:")
        print("  - No opposing odds available")
        print("  - All hedges are same-provider")
        print("  - No counterpart providers match criteria")

    db.close()


if __name__ == "__main__":
    main()
