"""One-shot recovery: fetch polymarket open positions via public API and insert into DB.

For each position:
1. Compute odds via the same fee-adjusted formula as polymarket extractor
2. Compute stake = avgPrice × size (USDC)
3. Match to existing event by team-name fuzzy compare → set event_id + outcome
4. POST to /api/bets with external_placement=True

Run: python backend/scripts/recover_polymarket_positions.py [--wallet 0x...] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

DEFAULT_WALLET = "0x71fca29E6B31a93d262D2972C9b361Af371D426d"
POLY_FEE_RATE = 0.02
DEFAULT_API = os.environ.get("BETTY_API_BASE", "https://148.251.40.251")
API_KEY = os.environ.get("BETTY_API_KEY", "aqxorczyd8rLzomW94nBjHWaa6tUh6NZ8aMktDbKMgI")


def fee_adjusted_odds(price: float) -> float:
    """Same formula as backend.providers.polymarket._price_to_odds."""
    if price <= 0.01 or price >= 0.99:
        return 1.01
    raw = 1.0 / price
    return round(1 + (raw - 1) * (1 - POLY_FEE_RATE), 4)


def fetch_positions(wallet: str) -> list[dict]:
    url = f"https://data-api.polymarket.com/positions?user={wallet}&sizeThreshold=.1&limit=50"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_events_by_team(team_a: str, team_b: str, days_window: int = 14) -> list[dict]:
    """Find events in arnold DB where home/away matches team_a/team_b in either order."""
    # Build a SQL query via the running API. Simpler: just hit a custom endpoint
    # or use the existing extract events query. For one-shot, we use direct
    # psql via the production server. This script expects to be run on host
    # that can SSH to the server, OR adapted to use a custom backend endpoint.
    raise NotImplementedError("Use --inline-events to provide events list")


def find_event_id(pos: dict, events_by_id: dict[str, dict]) -> tuple[str | None, str | None]:
    """Match a polymarket position to (event_id, outcome) by team-name fuzzy.

    pos: polymarket position dict (title, outcome)
    events_by_id: {event_id: {home_team, away_team, sport}}

    Returns (event_id or None, "home"/"away" or None)
    """
    outcome_name = (pos.get("outcome") or "").lower().strip()
    if not outcome_name:
        return None, None

    title = (pos.get("title") or "").lower()
    # Try each event — match if outcome_name appears in home_team or away_team
    best = None
    for eid, ev in events_by_id.items():
        home = (ev.get("home_team") or "").lower()
        away = (ev.get("away_team") or "").lower()
        if not home or not away:
            continue
        # Title check: both teams should appear in poly title
        if home not in title and away not in title:
            continue
        # Outcome match
        if outcome_name == home or (home in outcome_name and len(home) >= 4):
            score = len(home) + len(away)
            if best is None or score > best[0]:
                best = (score, eid, "home")
        elif outcome_name == away or (away in outcome_name and len(away) >= 4):
            score = len(home) + len(away)
            if best is None or score > best[0]:
                best = (score, eid, "away")
    if best:
        return best[1], best[2]
    return None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", default=DEFAULT_WALLET)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--events-json", help="Path to JSON with {event_id: {home_team, away_team}}")
    args = parser.parse_args()

    positions = fetch_positions(args.wallet)
    print(f"Fetched {len(positions)} polymarket open positions", flush=True)

    if not args.events_json:
        print("ERROR: --events-json required (export events from DB first)", file=sys.stderr)
        sys.exit(1)

    import json

    with open(args.events_json) as f:
        events_by_id = json.load(f)
    print(f"Loaded {len(events_by_id)} events for matching", flush=True)

    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    for pos in positions:
        title = pos.get("title", "")[:60]
        outcome_name = pos.get("outcome", "")
        size = float(pos.get("size") or 0)
        avg_price = float(pos.get("avgPrice") or 0)
        if size <= 0 or avg_price <= 0:
            print(f"SKIP (zero size/price): {title}", flush=True)
            continue

        odds = fee_adjusted_odds(avg_price)
        stake = round(avg_price * size, 2)
        event_id, outcome = find_event_id(pos, events_by_id)

        if not event_id or not outcome:
            print(f"NO_MATCH: {title} / outcome={outcome_name} → stored without event_id", flush=True)

        payload = {
            "provider_id": "polymarket",
            "event_id": event_id or "",
            "market": "moneyline",
            "outcome": outcome or "",
            "odds": odds,
            "stake": stake,
            "external_placement": True,
            "boost_event": title,
            "fair_odds_at_placement": None,
        }

        if args.dry_run:
            print(f"DRY: would POST {payload}", flush=True)
            continue

        try:
            r = requests.post(
                f"{DEFAULT_API}/api/bets",
                headers=headers,
                json=payload,
                timeout=10,
                verify=False,
            )
            if r.status_code in (200, 201):
                bid = r.json().get("bet_id")
                print(f"OK  bet#{bid}: {title} {outcome_name}→{outcome} odds={odds} stake=${stake}", flush=True)
            else:
                print(f"FAIL {r.status_code}: {title}: {r.text[:200]}", flush=True)
        except Exception as e:
            print(f"ERR  {title}: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
