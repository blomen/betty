#!/usr/bin/env python3
"""
Bankroll Management Script

Manage provider balances and view bankroll statistics.

Usage:
    python scripts/manage_bankroll.py list
    python scripts/manage_bankroll.py set unibet 1000
    python scripts/manage_bankroll.py set-all 500
    python scripts/manage_bankroll.py add unibet 100
    python scripts/manage_bankroll.py stats
    python scripts/manage_bankroll.py reset-all
"""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models import init_db, get_session, Provider, Bet
from datetime import datetime


def list_balances():
    """List all provider balances."""
    db = get_session()
    providers = db.query(Provider).order_by(Provider.id).all()

    print("\n" + "=" * 70)
    print("PROVIDER BALANCES")
    print("=" * 70)

    total = 0
    enabled_total = 0

    for p in providers:
        status = "[ON]" if p.is_enabled else "[OFF]"
        balance_str = f"${p.balance:>10.2f}"
        print(f"{status} {p.id:<20} {p.name:<25} {balance_str}")

        total += p.balance
        if p.is_enabled:
            enabled_total += p.balance

    print("=" * 70)
    print(f"Total (all):         ${total:>10.2f}")
    print(f"Total (enabled):     ${enabled_total:>10.2f}")
    print(f"Providers (enabled): {sum(1 for p in providers if p.is_enabled)}/{len(providers)}")
    print()

    db.close()


def set_balance(provider_id: str, balance: float):
    """Set balance for a specific provider."""
    db = get_session()
    provider = db.query(Provider).filter(Provider.id == provider_id).first()

    if not provider:
        print(f"Error: Provider '{provider_id}' not found")
        print(f"\nAvailable providers:")
        providers = db.query(Provider).all()
        for p in providers:
            print(f"  - {p.id}")
        db.close()
        return

    old_balance = provider.balance
    provider.balance = balance
    provider.updated_at = datetime.utcnow()
    db.commit()

    print(f"Updated {provider_id} balance: ${old_balance:.2f} -> ${balance:.2f}")

    db.close()


def set_all_balances(balance: float):
    """Set balance for all enabled providers."""
    db = get_session()
    providers = db.query(Provider).filter(Provider.is_enabled == True).all()

    print(f"Setting balance to ${balance:.2f} for {len(providers)} enabled providers...")

    for provider in providers:
        provider.balance = balance
        provider.updated_at = datetime.utcnow()

    db.commit()
    print(f"Done! Total bankroll: ${balance * len(providers):.2f}")

    db.close()


def add_balance(provider_id: str, amount: float):
    """Add to balance for a specific provider."""
    db = get_session()
    provider = db.query(Provider).filter(Provider.id == provider_id).first()

    if not provider:
        print(f"Error: Provider '{provider_id}' not found")
        db.close()
        return

    old_balance = provider.balance
    provider.balance += amount
    provider.updated_at = datetime.utcnow()
    db.commit()

    print(f"Updated {provider_id} balance: ${old_balance:.2f} -> ${provider.balance:.2f} ({amount:+.2f})")

    db.close()


def show_stats():
    """Show bankroll statistics with bet history."""
    db = get_session()

    # Provider balances
    providers = db.query(Provider).filter(Provider.is_enabled == True).all()
    total_balance = sum(p.balance for p in providers)

    # Bet statistics
    all_bets = db.query(Bet).all()
    settled_bets = [b for b in all_bets if b.result != "pending"]
    pending_bets = [b for b in all_bets if b.result == "pending"]

    total_staked = sum(b.stake for b in settled_bets)
    total_profit = sum(b.profit for b in settled_bets)
    win_count = len([b for b in settled_bets if b.result == "won"])
    loss_count = len([b for b in settled_bets if b.result == "lost"])
    void_count = len([b for b in settled_bets if b.result == "void"])

    print("\n" + "=" * 70)
    print("BANKROLL STATISTICS")
    print("=" * 70)

    print("\nBANKROLL:")
    print(f"  Total balance:      ${total_balance:>10.2f}")
    print(f"  Active providers:   {len(providers)}")

    print("\nBET HISTORY:")
    print(f"  Total bets:         {len(all_bets):>10}")
    print(f"  Settled:            {len(settled_bets):>10}")
    print(f"  Pending:            {len(pending_bets):>10}")

    if settled_bets:
        print(f"\n  Wins:               {win_count:>10}")
        print(f"  Losses:             {loss_count:>10}")
        print(f"  Voids:              {void_count:>10}")
        print(f"  Win rate:           {win_count / len(settled_bets) * 100:>9.1f}%")

        print(f"\n  Total staked:       ${total_staked:>10.2f}")
        print(f"  Total profit:       ${total_profit:>10.2f}")

        if total_staked > 0:
            roi = total_profit / total_staked * 100
            print(f"  ROI:                {roi:>9.1f}%")

    # Pending exposure
    if pending_bets:
        pending_stake = sum(b.stake for b in pending_bets)
        print(f"\nPENDING EXPOSURE:")
        print(f"  Pending bets:       {len(pending_bets):>10}")
        print(f"  Pending stake:      ${pending_stake:>10.2f}")

    # Per-provider breakdown
    print(f"\nPER-PROVIDER:")
    provider_bets = {}
    for bet in all_bets:
        if bet.provider_id not in provider_bets:
            provider_bets[bet.provider_id] = []
        provider_bets[bet.provider_id].append(bet)

    for p in providers:
        bets = provider_bets.get(p.id, [])
        bet_count = len(bets)
        if bet_count > 0:
            settled = [b for b in bets if b.result != "pending"]
            profit = sum(b.profit for b in settled)
            print(f"  {p.id:<20} Balance: ${p.balance:>8.2f}  Bets: {bet_count:>3}  P/L: ${profit:>8.2f}")
        else:
            print(f"  {p.id:<20} Balance: ${p.balance:>8.2f}  Bets: {bet_count:>3}")

    print()

    db.close()


def reset_all():
    """Reset all balances to 0."""
    db = get_session()
    providers = db.query(Provider).all()

    print(f"Resetting all {len(providers)} providers to $0.00...")

    for provider in providers:
        provider.balance = 0.0
        provider.updated_at = datetime.utcnow()

    db.commit()
    print("Done!")

    db.close()


def main():
    init_db()

    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1]

    if command == "list":
        list_balances()

    elif command == "set":
        if len(sys.argv) < 4:
            print("Usage: manage_bankroll.py set <provider_id> <balance>")
            return
        provider_id = sys.argv[2]
        balance = float(sys.argv[3])
        set_balance(provider_id, balance)

    elif command == "set-all":
        if len(sys.argv) < 3:
            print("Usage: manage_bankroll.py set-all <balance>")
            return
        balance = float(sys.argv[2])
        set_all_balances(balance)

    elif command == "add":
        if len(sys.argv) < 4:
            print("Usage: manage_bankroll.py add <provider_id> <amount>")
            return
        provider_id = sys.argv[2]
        amount = float(sys.argv[3])
        add_balance(provider_id, amount)

    elif command == "stats":
        show_stats()

    elif command == "reset-all":
        confirm = input("Reset all balances to $0.00? (y/n): ")
        if confirm.lower() == 'y':
            reset_all()
        else:
            print("Cancelled")

    else:
        print(f"Unknown command: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
