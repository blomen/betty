"""Seed the test profile with diverse mock bets for UI testing.

Categories:
- SETTLED (won/lost/void) — past events, with CLV data
- UPCOMING — future events, pending result
- LIVE-LIKE — events that just started (start_time in near past), pending
- BONUS bets — freebet + deposit match
- SPREAD/TOTAL bets — with point values
- Multi-sport — football, tennis, basketball, esports, mma
"""

import sqlite3
from datetime import datetime, timedelta
import random

DB_PATH = "data/firev.db"
PROFILE_ID = 6  # test profile


def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # --- Clean up existing bets for test profile ---
    c.execute("DELETE FROM bets WHERE profile_id = ?", (PROFILE_ID,))
    # Also clean orphan bets (profile_id IS NULL)
    c.execute("DELETE FROM bets WHERE profile_id IS NULL")
    print(f"Cleared existing bets")

    # --- Ensure provider balances exist ---
    providers_with_balance = [
        ("pinnacle", 2500),
        ("polymarket", 1200),
        ("tipwin", 3000),
        ("10bet", 2800),
        ("dbet", 1500),
        ("betsson", 2200),
        ("unibet", 4000),
        ("coolbet", 1800),
        ("bethard", 900),
        ("comeon", 1100),
        ("betinia", 750),
        ("888sport", 600),
        ("vbet", 500),
        ("hajper", 400),
    ]
    for prov, bal in providers_with_balance:
        existing = c.execute(
            "SELECT id FROM profile_provider_balances WHERE profile_id=? AND provider_id=?",
            (PROFILE_ID, prov),
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE profile_provider_balances SET balance=? WHERE profile_id=? AND provider_id=?",
                (bal, PROFILE_ID, prov),
            )
        else:
            c.execute(
                "INSERT INTO profile_provider_balances (profile_id, provider_id, balance, updated_at) VALUES (?,?,?,?)",
                (PROFILE_ID, prov, bal, datetime.utcnow().isoformat()),
            )

    now = datetime.utcnow()

    # --- Helper to insert a bet ---
    def insert_bet(
        event_id, provider_id, market, outcome, odds, stake, result, payout,
        placed_at, settled_at=None, is_bonus=False, bonus_type=None,
        closing_odds=None, clv_pct=None, point=None, settlement_source=None,
        utility_score=None, selection_probability=None, stake_noise_applied=None,
    ):
        hour = placed_at.hour
        dow = placed_at.weekday()
        is_round = stake == round(stake) and stake % 5 == 0 and stake >= 10
        risk = round(random.uniform(0.05, 0.45), 3)
        if utility_score is None:
            utility_score = round(random.uniform(0.02, 0.12), 4)
        if selection_probability is None:
            selection_probability = round(1.0 / odds if odds > 1 else 0.5, 4)

        c.execute("""
            INSERT INTO bets (
                profile_id, event_id, provider_id, market, outcome, odds, stake,
                is_bonus, bonus_type, result, payout, placed_at, settled_at,
                hour_of_day, day_of_week, stake_rounded, stake_noise_applied,
                risk_score_at_bet, utility_score, selection_probability,
                closing_odds, clv_pct, point, settlement_source, placement_status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            PROFILE_ID, event_id, provider_id, market, outcome, odds, stake,
            is_bonus, bonus_type, result, payout, placed_at.isoformat(),
            settled_at.isoformat() if settled_at else None,
            hour, dow, is_round, stake_noise_applied or round(random.uniform(-3, 3), 2),
            risk, utility_score, selection_probability,
            closing_odds, clv_pct, point, settlement_source, "manual",
        ))

    # ========================================================================
    # SECTION 1: SETTLED BETS (History) — 20 bets, mixed W/L/V
    # ========================================================================
    print("Inserting settled bets...")

    # --- Won bets ---
    # Football 1x2, big upset win
    insert_bet(
        "football:chichester city:canvey island:20260221", "unibet", "1x2", "away",
        4.20, 250, "won", 1050,
        now - timedelta(days=3, hours=2),
        settled_at=now - timedelta(days=3),
        closing_odds=3.85, clv_pct=9.09, settlement_source="manual",
    )
    # Tennis moneyline, solid CLV
    insert_bet(
        "tennis:zachary svajda:frances tiafoe:20260219", "bethard", "moneyline", "home",
        1.95, 180, "won", 351,
        now - timedelta(days=5, hours=6),
        settled_at=now - timedelta(days=5, hours=3),
        closing_odds=1.78, clv_pct=9.55, settlement_source="manual",
    )
    # Basketball spread, good edge
    insert_bet(
        "basketball:valparaiso:bradley:20260219", "unibet", "spread", "home",
        2.55, 300, "won", 765,
        now - timedelta(days=5, hours=10),
        settled_at=now - timedelta(days=5, hours=7),
        closing_odds=2.30, clv_pct=10.87, point=2.5, settlement_source="manual",
    )
    # Esports moneyline
    insert_bet(
        "esports:alt f4:leviatan academy:20260223", "polymarket", "moneyline", "away",
        1.77, 100, "won", 177,
        now - timedelta(days=1, hours=5),
        settled_at=now - timedelta(days=1, hours=2),
        closing_odds=1.70, clv_pct=4.12, settlement_source="auto_pinnacle",
    )
    # Football draw at long odds
    insert_bet(
        "football:monsoon:avenida:20260223", "10bet", "1x2", "draw",
        2.80, 70, "won", 196,
        now - timedelta(days=1, hours=8),
        settled_at=now - timedelta(days=1, hours=4),
        closing_odds=2.95, clv_pct=-5.08, settlement_source="manual",
    )
    # MMA underdog win
    insert_bet(
        "mma:juliana miller:carli judice:20260221", "pinnacle", "moneyline", "home",
        7.25, 35, "won", 253.75,
        now - timedelta(days=3, hours=15),
        settled_at=now - timedelta(days=3, hours=6),
        closing_odds=6.80, clv_pct=6.62, settlement_source="manual",
    )
    # Football total over, high odds
    insert_bet(
        "football:forte:vitoria es:20260223", "tipwin", "total", "over",
        2.15, 140, "won", 301,
        now - timedelta(days=1, hours=9),
        settled_at=now - timedelta(days=1, hours=6),
        closing_odds=2.05, clv_pct=4.88, point=2.5, settlement_source="manual",
    )
    # Small stake quick win
    insert_bet(
        "esports:2game:equipe caos:20260223", "polymarket", "moneyline", "home",
        1.587, 160, "won", 253.92,
        now - timedelta(days=1, hours=3),
        settled_at=now - timedelta(days=1, hours=1),
        closing_odds=1.55, clv_pct=2.39, settlement_source="auto_pinnacle",
    )

    # --- Lost bets ---
    # Football 1x2 loss
    insert_bet(
        "football:independiente:tauro:20260224", "10bet", "1x2", "draw",
        2.98, 50, "lost", 0,
        now - timedelta(hours=12),
        settled_at=now - timedelta(hours=8),
        closing_odds=3.10, clv_pct=-3.87, settlement_source="manual",
    )
    # Esports loss
    insert_bet(
        "esports:2game:equipe caos:20260223", "coolbet", "moneyline", "away",
        2.45, 120, "lost", 0,
        now - timedelta(days=1, hours=4),
        settled_at=now - timedelta(days=1, hours=1),
        closing_odds=2.55, clv_pct=-3.92, settlement_source="auto_pinnacle",
    )
    # Football total under loss
    insert_bet(
        "football:forte:vitoria es:20260223", "betsson", "total", "under",
        1.85, 200, "lost", 0,
        now - timedelta(days=1, hours=10),
        settled_at=now - timedelta(days=1, hours=7),
        closing_odds=1.80, clv_pct=2.78, point=2.5, settlement_source="manual",
    )
    # Tennis loss
    insert_bet(
        "tennis:zachary svajda:frances tiafoe:20260219", "coolbet", "moneyline", "away",
        2.10, 90, "lost", 0,
        now - timedelta(days=5, hours=8),
        settled_at=now - timedelta(days=5, hours=3),
        closing_odds=2.25, clv_pct=-6.67, settlement_source="manual",
    )
    # High-stake football loss
    insert_bet(
        "football:buriram:sukhothai:20260221", "10bet", "1x2", "draw",
        7.56, 25, "lost", 0,
        now - timedelta(days=3, hours=16),
        settled_at=now - timedelta(days=3, hours=12),
        closing_odds=8.26, clv_pct=-8.47, settlement_source="manual",
    )
    # Spread loss
    insert_bet(
        "basketball:valparaiso:bradley:20260219", "bethard", "spread", "away",
        1.91, 150, "lost", 0,
        now - timedelta(days=5, hours=12),
        settled_at=now - timedelta(days=5, hours=7),
        closing_odds=1.95, clv_pct=-2.05, point=-2.5, settlement_source="manual",
    )

    # --- Void bets ---
    insert_bet(
        "football:deportivo madryn:deportivo moron:20260221", "pinnacle", "1x2", "away",
        5.09, 30, "void", 30,
        now - timedelta(days=3, hours=20),
        settled_at=now - timedelta(days=3, hours=14),
        settlement_source="manual",
    )
    insert_bet(
        "football:mohammedan:goa:20260220", "comeon", "1x2", "home",
        6.50, 45, "void", 45,
        now - timedelta(days=4, hours=14),
        settled_at=now - timedelta(days=4, hours=10),
        settlement_source="manual",
    )

    # --- Freebet (settled, won) ---
    insert_bet(
        "football:monsoon:avenida:20260223", "betsson", "1x2", "home",
        3.40, 200, "won", 680,
        now - timedelta(days=1, hours=7),
        settled_at=now - timedelta(days=1, hours=3),
        is_bonus=True, bonus_type="free_bet",
        closing_odds=3.20, clv_pct=6.25, settlement_source="manual",
    )
    # Freebet loss
    insert_bet(
        "esports:pain academy:folha amarela:20260220", "coolbet", "moneyline", "home",
        3.20, 100, "lost", 0,
        now - timedelta(days=4, hours=10),
        settled_at=now - timedelta(days=4, hours=5),
        is_bonus=True, bonus_type="free_bet",
        closing_odds=3.00, clv_pct=6.67, settlement_source="manual",
    )

    # ========================================================================
    # SECTION 2: UPCOMING BETS — future events, pending
    # ========================================================================
    print("Inserting upcoming bets...")

    # Far future: World Cup matches
    insert_bet(
        "football:germany:curacao:20260614", "betsson", "1x2", "home",
        1.22, 500, "pending", 0,
        now - timedelta(hours=2),
    )
    insert_bet(
        "football:france:senegal:20260616", "unibet", "1x2", "home",
        1.45, 350, "pending", 0,
        now - timedelta(hours=1),
    )
    insert_bet(
        "football:spain:cape verde:20260615", "10bet", "spread", "home",
        1.90, 200, "pending", 0,
        now - timedelta(hours=3),
        point=-2.5,
    )
    insert_bet(
        "football:argentina:algeria:20260617", "comeon", "1x2", "home",
        1.35, 400, "pending", 0,
        now - timedelta(minutes=30),
    )

    # Near future: this week
    insert_bet(
        "football:emelec:delfin:20260301", "tipwin", "1x2", "draw",
        3.10, 85, "pending", 0,
        now - timedelta(hours=4),
    )
    insert_bet(
        "mma:kris moutinho:cristian quinonez:20260301", "pinnacle", "moneyline", "home",
        2.35, 120, "pending", 0,
        now - timedelta(hours=6),
    )
    insert_bet(
        "football:monterrey:cruz azul:20260301", "dbet", "total", "over",
        1.95, 175, "pending", 0,
        now - timedelta(hours=5),
        point=2.5,
    )
    insert_bet(
        "football:salt lake:seattle sounders:20260301", "betinia", "1x2", "away",
        2.75, 110, "pending", 0,
        now - timedelta(hours=3),
    )
    insert_bet(
        "mma:macy chiasson:ailin perez:20260301", "888sport", "moneyline", "away",
        1.65, 200, "pending", 0,
        now - timedelta(hours=7),
    )

    # Upcoming bonus bet (freebet)
    insert_bet(
        "football:leon:necaxa:20260301", "hajper", "1x2", "home",
        2.10, 150, "pending", 0,
        now - timedelta(hours=1),
        is_bonus=True, bonus_type="free_bet",
    )

    # ========================================================================
    # SECTION 3: "SETTLE" BETS — past start_time, still pending (need manual settle)
    # ========================================================================
    print("Inserting settle-needed bets...")

    # These events have start_time in the past but bets are still pending
    insert_bet(
        "esports:bilibili:ninjas in pyjamas:20260225", "polymarket", "moneyline", "away",
        6.667, 60, "pending", 0,
        now - timedelta(hours=8),
        closing_odds=6.10, clv_pct=9.30,
    )
    insert_bet(
        "football:brondby:akademisk boldklub:20260224", "betsson", "1x2", "home",
        1.35, 300, "pending", 0,
        now - timedelta(hours=14),
        closing_odds=1.32, clv_pct=2.27,
    )
    insert_bet(
        "football:zimbabwe:botswana:20260224", "unibet", "1x2", "home",
        1.55, 250, "pending", 0,
        now - timedelta(hours=15),
        closing_odds=1.50, clv_pct=3.33,
    )
    insert_bet(
        "football:zambia:eswatini:20260224", "10bet", "total", "under",
        2.10, 130, "pending", 0,
        now - timedelta(hours=16),
        closing_odds=2.00, clv_pct=5.00,
        point=2.5,
    )
    insert_bet(
        "tennis:pablo carreno busta:denis shapovalov:20260224", "betinia", "moneyline", "home",
        1.55, 220, "pending", 0,
        now - timedelta(hours=13),
        closing_odds=1.48, clv_pct=4.73,
    )

    # ========================================================================
    # SECTION 4: "LIVE" BETS — events currently in play (set match_status)
    # ========================================================================
    print("Setting up live events and bets...")

    # Create a couple of "live" events by updating match_status on near-past events
    live_events = [
        ("football:dagon star:rakhine:20260224", "live", 67, 2, 1),
        ("esports:jd gaming:top esports:20260224", "live", None, 1, 0),
    ]
    for eid, status, minute, hs, aws in live_events:
        c.execute(
            "UPDATE events SET match_status=?, match_minute=?, home_score=?, away_score=? WHERE id=?",
            (status, minute, hs, aws, eid),
        )

    # Bets on live events
    insert_bet(
        "football:dagon star:rakhine:20260224", "tipwin", "1x2", "home",
        2.40, 175, "pending", 0,
        now - timedelta(hours=6),
    )
    insert_bet(
        "football:dagon star:rakhine:20260224", "betsson", "total", "over",
        1.72, 220, "pending", 0,
        now - timedelta(hours=5),
        point=2.5,
    )
    insert_bet(
        "esports:jd gaming:top esports:20260224", "polymarket", "moneyline", "home",
        1.85, 150, "pending", 0,
        now - timedelta(hours=4),
    )

    # ========================================================================
    # SECTION 5: Set up some bonuses
    # ========================================================================
    print("Setting up bonus statuses...")

    bonuses = [
        # (provider_id, status, type, amount, multiplier, wagered, min_odds)
        ("betsson", "in_progress", "bonusdeposit", 500, 10, 2350, 1.80),
        ("unibet", "trigger_needed", "freebet", 300, 1, 0, 1.80),
        ("coolbet", "freebet_available", "freebet", 200, 1, 200, 1.80),
        ("hajper", "completed", "freebet", 150, 1, 150, 1.80),
    ]
    for prov, status, btype, amount, mult, wagered, min_odds in bonuses:
        existing = c.execute(
            "SELECT id FROM profile_provider_bonuses WHERE profile_id=? AND provider_id=?",
            (PROFILE_ID, prov),
        ).fetchone()
        claimed = (now - timedelta(days=10)).isoformat()
        expires = (now + timedelta(days=50)).isoformat()
        wager_req = amount * mult
        if existing:
            c.execute("""
                UPDATE profile_provider_bonuses
                SET bonus_status=?, bonus_type=?, bonus_amount=?, wagering_multiplier=?,
                    wagering_requirement=?, wagered_amount=?, min_odds=?,
                    claimed_at=?, expires_at=?, updated_at=?
                WHERE profile_id=? AND provider_id=?
            """, (status, btype, amount, mult, wager_req, wagered, min_odds,
                  claimed, expires, now.isoformat(), PROFILE_ID, prov))
        else:
            c.execute("""
                INSERT INTO profile_provider_bonuses
                (profile_id, provider_id, bonus_status, bonus_type, bonus_amount,
                 wagering_multiplier, wagering_requirement, wagered_amount, min_odds,
                 claimed_at, expires_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (PROFILE_ID, prov, status, btype, amount, mult, wager_req, wagered, min_odds,
                  claimed, expires, now.isoformat()))

    conn.commit()

    # --- Summary ---
    total = c.execute("SELECT COUNT(*) FROM bets WHERE profile_id=?", (PROFILE_ID,)).fetchone()[0]
    by_result = c.execute(
        "SELECT result, COUNT(*) FROM bets WHERE profile_id=? GROUP BY result", (PROFILE_ID,)
    ).fetchall()
    balances = c.execute(
        "SELECT provider_id, balance FROM profile_provider_balances WHERE profile_id=? ORDER BY balance DESC",
        (PROFILE_ID,),
    ).fetchall()

    print(f"\n=== SEED COMPLETE ===")
    print(f"Total bets: {total}")
    for r, cnt in by_result:
        print(f"  {r}: {cnt}")
    print(f"\nBalances:")
    total_bal = 0
    for prov, bal in balances:
        print(f"  {prov}: {bal:.0f} SEK")
        total_bal += bal
    print(f"  TOTAL: {total_bal:.0f} SEK")
    print(f"\nBonuses:")
    for b in c.execute(
        "SELECT provider_id, bonus_status, bonus_type, bonus_amount, wagered_amount, wagering_requirement FROM profile_provider_bonuses WHERE profile_id=?",
        (PROFILE_ID,),
    ).fetchall():
        print(f"  {b[0]}: {b[1]} ({b[2]}) — {b[4]:.0f}/{b[5]:.0f} wagered, amount={b[3]:.0f}")

    conn.close()


if __name__ == "__main__":
    main()
