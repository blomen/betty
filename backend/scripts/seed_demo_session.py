"""Seed the DB with realistic NQ demo session data to test the trading dashboard.

Run: python -m scripts.seed_demo_session
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.models import get_session, MarketSession, MarketLevel, TradingSignal

# === Realistic NQ session data (trending up day, 2026-03-14) ===

TODAY = "2026-03-14"
SYMBOL = "NQ"
LAST_PRICE = 19847.50

# Volume profile
POC = 19820.00
VAH = 19865.00
VAL = 19780.00

# VWAP bands
VWAP = 19822.50
SD1 = 28.0  # ~28pt per SD
VWAP_1SD_UPPER = VWAP + SD1
VWAP_1SD_LOWER = VWAP - SD1
VWAP_2SD_UPPER = VWAP + SD1 * 2
VWAP_2SD_LOWER = VWAP - SD1 * 2
VWAP_3SD_UPPER = VWAP + SD1 * 3
VWAP_3SD_LOWER = VWAP - SD1 * 3

# Initial balance (first 60 min RTH)
IB_HIGH = 19860.00
IB_LOW = 19818.00

# Overnight
ON_HIGH = 19810.00
ON_LOW = 19760.00

# Prior day
PDH = 19890.00
PDL = 19720.00

# TPO
TPO_POC = 19825.00
TPO_VAH = 19858.00
TPO_VAL = 19785.00

# Session JSON — the full blob that build_expanded_session reads
SESSION_JSON = {
    "date": TODAY,
    "symbol": SYMBOL,
    "last_price": LAST_PRICE,
    # VP
    "poc": POC,
    "vah": VAH,
    "val": VAL,
    # VWAP
    "vwap": VWAP,
    "vwap_1sd_upper": VWAP_1SD_UPPER,
    "vwap_1sd_lower": VWAP_1SD_LOWER,
    "vwap_2sd_upper": VWAP_2SD_UPPER,
    "vwap_2sd_lower": VWAP_2SD_LOWER,
    "vwap_3sd_upper": VWAP_3SD_UPPER,
    "vwap_3sd_lower": VWAP_3SD_LOWER,
    # IB
    "ib_high": IB_HIGH,
    "ib_low": IB_LOW,
    "ib_range": IB_HIGH - IB_LOW,
    # Overnight
    "overnight_high": ON_HIGH,
    "overnight_low": ON_LOW,
    # Delta
    "total_delta": 4250,
    "delta_divergence": False,
    # Classifications
    "market_type": "trending_up",
    "opening_type": "OTD",
    "poor_high": False,
    "poor_low": True,
    "single_prints": [[19700, 19720]],
    "value_migration": "up",
    "distribution_type": "p_shape",
    # Price position
    "price_vs_va": "above",
    "price_vs_vwap": "above_1sd",
    "price_vs_ib": "above",
    # TPO
    "tpo_poc": TPO_POC,
    "tpo_vah": TPO_VAH,
    "tpo_val": TPO_VAL,
    # Macro
    "macro": {
        "regime": "risk_on",
        "regime_score": 0.65,
        "vix": 14.2,
        "vix_change_pct": -3.1,
        "dxy": 104.1,
        "dxy_change_pct": 0.2,
        "us10y": 4.32,
        "us10y_change_bps": 2,
        "us2y": 4.65,
        "yield_curve_spread": -33,
        "gex": None,
        "put_call_ratio": None,
    },
}

# Structural levels
LEVELS = [
    {"level_type": "pdh", "price_low": PDH, "price_high": PDH, "direction": "resistance"},
    {"level_type": "pdl", "price_low": PDL, "price_high": PDL, "direction": "support"},
    {"level_type": "tokyo_high", "price_low": 19795.00, "price_high": 19795.00, "direction": None, "session_name": "tokyo"},
    {"level_type": "tokyo_low", "price_low": 19762.00, "price_high": 19762.00, "direction": None, "session_name": "tokyo"},
    {"level_type": "london_high", "price_low": 19840.00, "price_high": 19840.00, "direction": None, "session_name": "london"},
    {"level_type": "london_low", "price_low": 19775.00, "price_high": 19775.00, "direction": None, "session_name": "london"},
    {"level_type": "weekly_high", "price_low": 19905.00, "price_high": 19905.00, "direction": "resistance"},
    {"level_type": "weekly_low", "price_low": 19580.00, "price_high": 19580.00, "direction": "support"},
    {"level_type": "monthly_high", "price_low": 19950.00, "price_high": 19950.00, "direction": "resistance"},
    # Order blocks
    {"level_type": "order_block", "price_low": 19760.00, "price_high": 19775.00, "direction": "bullish"},
    {"level_type": "order_block", "price_low": 19880.00, "price_high": 19895.00, "direction": "bearish"},
    # Fair value gaps
    {"level_type": "fvg", "price_low": 19700.00, "price_high": 19720.00, "direction": "bullish"},
    {"level_type": "fvg", "price_low": 19870.00, "price_high": 19885.00, "direction": "bearish"},
    # Single print zone
    {"level_type": "single_print", "price_low": 19700.00, "price_high": 19720.00, "direction": None},
]

# Trading signals
SIGNALS = [
    {
        "setup_type": "ib_extension",
        "setup_name": "IB Extension Long",
        "category": "ib_break",
        "setup_category": "ib_break",
        "direction": "long",
        "score": 85.0,
        "price_at_signal": 19862.00,
        "suggested_entry": 19860.00,
        "suggested_stop": 19818.00,
        "suggested_target": 19920.00,
        "suggested_target_2": 19950.00,
        "level_touched": "ib_high",
        "rr_tp1": 1.4,
        "conditions": [
            {"name": "IB breakout confirmed", "score": 0.9, "weight": 2.0, "is_auto": True},
            {"name": "Macro regime aligned", "score": 0.75, "weight": 1.5, "is_auto": True},
            {"name": "Delta supporting move", "score": 0.8, "weight": 1.5, "is_auto": True},
            {"name": "Volume above average", "score": 0.7, "weight": 1.0, "is_auto": True},
            {"name": "Price above VWAP", "score": 0.85, "weight": 1.0, "is_auto": True},
        ],
    },
    {
        "setup_type": "reversal_vwap_2sd",
        "setup_name": "VWAP +1SD Bounce",
        "category": "spring",
        "setup_category": "spring",
        "direction": "long",
        "score": 78.0,
        "price_at_signal": 19852.00,
        "suggested_entry": 19850.00,
        "suggested_stop": 19822.00,
        "suggested_target": 19890.00,
        "level_touched": "+1sd",
        "rr_tp1": 1.4,
        "conditions": [
            {"name": "Price at VWAP +1SD", "score": 0.85, "weight": 2.0, "is_auto": True},
            {"name": "Bounce from level", "score": 0.7, "weight": 1.5, "is_auto": True},
            {"name": "CVD rising on bounce", "score": 0.65, "weight": 1.0, "is_auto": True},
            {"name": "Trapped shorts detected", "score": 0.6, "weight": 1.0, "is_auto": True},
        ],
    },
    {
        "setup_type": "spring",
        "setup_name": "Spring @ Bull OB",
        "category": "spring",
        "setup_category": "spring",
        "direction": "long",
        "score": 72.0,
        "price_at_signal": 19772.00,
        "suggested_entry": 19775.00,
        "suggested_stop": 19750.00,
        "suggested_target": 19840.00,
        "suggested_target_2": 19865.00,
        "level_touched": "order_block",
        "rr_tp1": 2.6,
        "conditions": [
            {"name": "Spring at order block", "score": 0.8, "weight": 2.0, "is_auto": True},
            {"name": "VSA absorption present", "score": 0.7, "weight": 1.5, "is_auto": True},
            {"name": "Structure still bullish", "score": 0.65, "weight": 1.0, "is_auto": True},
            {"name": "Poor low present", "score": 0.6, "weight": 1.0, "is_auto": True},
        ],
    },
    {
        "setup_type": "poor_extreme",
        "setup_name": "Poor Low Retest",
        "category": "poor_extreme",
        "setup_category": "poor_extreme",
        "direction": "short",
        "score": 65.0,
        "price_at_signal": 19830.00,
        "suggested_entry": 19830.00,
        "suggested_stop": 19868.00,
        "suggested_target": 19760.00,
        "level_touched": "weekly_vah",
        "rr_tp1": 1.8,
        "conditions": [
            {"name": "Poor low identified", "score": 0.7, "weight": 2.0, "is_auto": True},
            {"name": "Rejection at level", "score": 0.55, "weight": 1.5, "is_auto": True},
            {"name": "Contra macro (penalized)", "score": 0.3, "weight": 1.0, "is_auto": True},
        ],
    },
]


def seed():
    db = get_session()
    try:
        # Clean existing demo data for today
        db.query(TradingSignal).filter(
            TradingSignal.session_id.in_(
                db.query(MarketSession.id).filter_by(date=TODAY, symbol=SYMBOL)
            )
        ).delete(synchronize_session=False)
        db.query(MarketSession).filter_by(date=TODAY, symbol=SYMBOL).delete()
        db.query(MarketLevel).filter_by(date=TODAY, symbol=SYMBOL).delete()
        db.flush()

        # Insert session
        ms = MarketSession(
            date=TODAY,
            symbol=SYMBOL,
            poc=POC, vah=VAH, val=VAL,
            vwap=VWAP,
            vwap_1sd_upper=VWAP_1SD_UPPER, vwap_1sd_lower=VWAP_1SD_LOWER,
            vwap_2sd_upper=VWAP_2SD_UPPER, vwap_2sd_lower=VWAP_2SD_LOWER,
            vwap_3sd_upper=VWAP_3SD_UPPER, vwap_3sd_lower=VWAP_3SD_LOWER,
            ib_high=IB_HIGH, ib_low=IB_LOW, ib_range=IB_HIGH - IB_LOW,
            overnight_high=ON_HIGH, overnight_low=ON_LOW,
            total_delta=4250, delta_divergence=False,
            market_type="trending_up", opening_type="OTD",
            poor_high=False, poor_low=True,
            rotation_factor=4, aspr=28.5, aspr_percentile=0.72,
            ib_tpo_count=3, value_migration="up",
            pdh=PDH, pdl=PDL,
            tokyo_high=19795.00, tokyo_low=19762.00,
            london_high=19840.00, london_low=19775.00,
            session_json=SESSION_JSON,
        )
        db.add(ms)
        db.flush()

        # Insert levels
        for lv in LEVELS:
            db.add(MarketLevel(
                symbol=SYMBOL,
                date=TODAY,
                level_type=lv["level_type"],
                session=lv.get("session_name"),
                price_low=lv["price_low"],
                price_high=lv["price_high"],
                direction=lv.get("direction"),
                is_filled=False,
            ))

        # Insert signals
        now = datetime.now(timezone.utc)
        for sig in SIGNALS:
            db.add(TradingSignal(
                session_id=ms.id,
                setup_type=sig["setup_type"],
                setup_name=sig["setup_name"],
                category=sig["category"],
                setup_category=sig.get("setup_category"),
                direction=sig["direction"],
                score=sig["score"],
                conditions=json.dumps(sig["conditions"]),
                price_at_signal=sig.get("price_at_signal"),
                suggested_entry=sig.get("suggested_entry"),
                suggested_stop=sig.get("suggested_stop"),
                suggested_target=sig.get("suggested_target"),
                suggested_target_2=sig.get("suggested_target_2"),
                level_touched=sig.get("level_touched"),
                rr_tp1=sig.get("rr_tp1"),
                vwap=VWAP, poc=POC, vah=VAH, val=VAL,
                ib_high=IB_HIGH, ib_low=IB_LOW,
                cumulative_delta=4250,
                is_active=True,
                triggered_at=now,
            ))

        db.commit()
        print(f"Seeded: 1 session, {len(LEVELS)} levels, {len(SIGNALS)} signals for {TODAY}")

    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
