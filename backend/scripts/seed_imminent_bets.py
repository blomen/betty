"""Seed bets on events starting in seconds/minutes for live transition testing.

Run this and immediately watch the Monitor page to see:
- Upcoming -> Settle transitions as start_time passes
- CLV calculation once events pass start_time
- Different TTK countdowns (30s, 1m, 2m, 4m, 8m)
"""

import sqlite3
from datetime import datetime, timedelta
import random

DB_PATH = "data/bankrollbbq.db"
PROFILE_ID = 6  # test profile


def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    now = datetime.utcnow()
    today = now.strftime("%Y%m%d")

    # --- Define imminent events with staggered start times ---
    # Each event includes full Pinnacle market odds so CLV can be computed
    # when snapshot_closing_odds runs after start_time passes.
    # "pinnacle_market" defines the full Pinnacle odds for the market.
    imminent = [
        {
            "id": f"football:ajax:feyenoord:{today}",
            "sport": "football", "league": "Eredivisie",
            "home": "Ajax", "away": "Feyenoord",
            "start": now + timedelta(seconds=30),
            "provider": "betsson", "market": "1x2", "outcome": "home",
            "odds": 2.15, "stake": 180,
            "pinnacle_market": {"home": 2.05, "draw": 3.50, "away": 3.80},
        },
        {
            "id": f"tennis:djokovic:alcaraz:{today}",
            "sport": "tennis", "league": "ATP Masters",
            "home": "Novak Djokovic", "away": "Carlos Alcaraz",
            "start": now + timedelta(minutes=1, seconds=15),
            "provider": "unibet", "market": "moneyline", "outcome": "away",
            "odds": 1.72, "stake": 250,
            "pinnacle_market": {"home": 2.30, "away": 1.65},
        },
        {
            "id": f"basketball:lakers:celtics:{today}",
            "sport": "basketball", "league": "NBA",
            "home": "Los Angeles Lakers", "away": "Boston Celtics",
            "start": now + timedelta(minutes=2, seconds=30),
            "provider": "10bet", "market": "spread", "outcome": "home",
            "odds": 1.95, "stake": 200, "point": 3.5,
            "pinnacle_market": {"home": 1.90, "away": 1.95},
        },
        {
            "id": f"ice_hockey:rangers:bruins:{today}",
            "sport": "ice_hockey", "league": "NHL",
            "home": "New York Rangers", "away": "Boston Bruins",
            "start": now + timedelta(minutes=4),
            "provider": "coolbet", "market": "moneyline", "outcome": "home",
            "odds": 2.40, "stake": 130,
            "pinnacle_market": {"home": 2.25, "away": 1.68},
        },
        {
            "id": f"football:real madrid:barcelona:{today}",
            "sport": "football", "league": "La Liga",
            "home": "Real Madrid", "away": "Barcelona",
            "start": now + timedelta(minutes=8),
            "provider": "tipwin", "market": "1x2", "outcome": "draw",
            "odds": 3.60, "stake": 75,
            "pinnacle_market": {"home": 2.40, "draw": 3.40, "away": 3.10},
        },
        {
            "id": f"esports:t1:gen g:{today}",
            "sport": "esports", "league": "LCK",
            "home": "T1", "away": "Gen.G",
            "start": now + timedelta(minutes=1, seconds=45),
            "provider": "polymarket", "market": "moneyline", "outcome": "home",
            "odds": 1.55, "stake": 300,
            "pinnacle_market": {"home": 1.48, "away": 2.75},
        },
        {
            "id": f"mma:jones:miocic:{today}",
            "sport": "mma", "league": "UFC 310",
            "home": "Jon Jones", "away": "Stipe Miocic",
            "start": now + timedelta(seconds=45),
            "provider": "pinnacle", "market": "moneyline", "outcome": "home",
            "odds": 1.28, "stake": 400,
            "pinnacle_market": {"home": 1.22, "away": 4.50},
        },
    ]

    print(f"Now: {now.strftime('%H:%M:%S')} UTC")
    print(f"Creating {len(imminent)} imminent events + bets...\n")

    for ev in imminent:
        start = ev["start"]
        delta = (start - now).total_seconds()

        # Upsert event
        existing = c.execute("SELECT id FROM events WHERE id=?", (ev["id"],)).fetchone()
        if existing:
            c.execute("""
                UPDATE events SET sport=?, league=?, home_team=?, away_team=?,
                    start_time=?, match_status='pre_match', match_minute=NULL,
                    home_score=NULL, away_score=NULL
                WHERE id=?
            """, (ev["sport"], ev["league"], ev["home"], ev["away"],
                  start.strftime("%Y-%m-%d %H:%M:%S"), ev["id"]))
        else:
            c.execute("""
                INSERT INTO events (id, sport, league, home_team, away_team, start_time, match_status)
                VALUES (?,?,?,?,?,?,?)
            """, (ev["id"], ev["sport"], ev["league"], ev["home"], ev["away"],
                  start.strftime("%Y-%m-%d %H:%M:%S"), "pre_match"))

        # Remove existing bet on this event for this profile (clean re-run)
        c.execute("""
            DELETE FROM bets WHERE profile_id=? AND event_id=? AND provider_id=?
        """, (PROFILE_ID, ev["id"], ev["provider"]))

        # Insert bet (reset closing_odds/clv_pct for fresh CLV computation)
        sel_prob = round(1.0 / ev["odds"], 4)
        c.execute("""
            INSERT INTO bets (
                profile_id, event_id, provider_id, market, outcome, odds, stake,
                is_bonus, bonus_type, result, payout, placed_at,
                hour_of_day, day_of_week, stake_rounded, stake_noise_applied,
                risk_score_at_bet, utility_score, selection_probability,
                point, placement_status, closing_odds, clv_pct
            ) VALUES (?,?,?,?,?,?,?,0,NULL,'pending',0,?,?,?,?,?,?,?,?,?,?,NULL,NULL)
        """, (
            PROFILE_ID, ev["id"], ev["provider"], ev["market"], ev["outcome"],
            ev["odds"], ev["stake"],
            (now - timedelta(minutes=random.randint(10, 60))).isoformat(),
            now.hour, now.weekday(),
            ev["stake"] % 5 == 0,
            round(random.uniform(-2, 2), 2),
            round(random.uniform(0.05, 0.35), 3),
            round(random.uniform(0.03, 0.10), 4),
            sel_prob,
            ev.get("point"),
            "manual",
        ))

        # Seed Pinnacle odds for this event (required for CLV computation)
        pin_market = ev.get("pinnacle_market", {})
        if pin_market:
            # Remove existing Pinnacle odds for clean re-run
            c.execute("""
                DELETE FROM odds WHERE event_id=? AND provider_id='pinnacle' AND market=?
            """, (ev["id"], ev["market"]))

            for outcome, pin_odds in pin_market.items():
                c.execute("""
                    INSERT INTO odds (event_id, provider_id, market, outcome, odds, point, updated_at)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    ev["id"], "pinnacle", ev["market"], outcome, pin_odds,
                    ev.get("point"), now.isoformat(),
                ))

        mins = int(delta // 60)
        secs = int(delta % 60)
        pin_str = f"  pin={pin_market.get(ev['outcome'], '?')}" if pin_market else ""
        print(f"  +{mins}m{secs:02d}s  {ev['home']} v {ev['away']}  ({ev['sport']})  @{ev['odds']}{pin_str}  {ev['provider']}")

    conn.commit()

    total = c.execute(
        "SELECT COUNT(*) FROM bets WHERE profile_id=? AND result='pending'",
        (PROFILE_ID,),
    ).fetchone()[0]
    print(f"\nTotal pending bets for profile {PROFILE_ID}: {total}")
    print("\nWatch the Monitor page - bets will transition from Upcoming -> Settle as start_time passes.")
    print("The 30-second poll interval will pick up changes automatically.")

    conn.close()


if __name__ == "__main__":
    main()
