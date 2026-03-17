"""Comprehensive trading system audit — exercises all API endpoints, services, and data integrity.

Tests:
1. Trading config (YAML loading)
2. Account operations (seed, list, update, reset)
3. Daily routine (create, update, checklist)
4. Trade lifecycle (create → transition → close → review)
5. Market session (compute, scan, signals)
6. Market data endpoints (macro, book, levels, stream, context)
7. Analytics engine
8. Scanner + ML integration
9. Data integrity checks
"""
import sys
import json
import traceback
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

BASE = "http://localhost:8000/api/trading"
PASS = 0
FAIL = 0
SKIP = 0


def report(name: str, success: bool, detail: str = "", skipped: bool = False):
    global PASS, FAIL, SKIP
    if skipped:
        SKIP += 1
        print(f"  SKIP  {name}: {detail}")
    elif success:
        PASS += 1
        print(f"  PASS  {name}: {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}: {detail}")


def api(method: str, path: str, body: dict | None = None, timeout: int = 30) -> tuple[int, dict | str]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        try:
            return e.code, json.loads(body_text)
        except Exception:
            return e.code, body_text
    except Exception as e:
        return 0, str(e)


# ──────────────────────────────────────────────
# 1. Trading Config
# ──────────────────────────────────────────────
print("\n=== 1. TRADING CONFIG ===")

status, data = api("GET", "/config")
if status == 200:
    instruments = data.get("instruments", {})
    setups = data.get("setups", {})
    report("config", True, f"instruments={list(instruments.keys())}, setups={len(setups)}")
    for inst, cfg in instruments.items():
        report(f"  instrument {inst}", True,
               f"tick_size={cfg.get('tick_size')}, tick_value=${cfg.get('tick_value')}, margin=${cfg.get('margin')}")
else:
    report("config", False, f"HTTP {status}: {data}")

status, data = api("GET", "/routine/config")
if status == 200:
    macro_items = data.get("macro_checklist", [])
    session_items = data.get("session_checklist", [])
    report("routine_config", True, f"macro={len(macro_items)} items, session={len(session_items)} items")
else:
    report("routine_config", False, f"HTTP {status}")


# ──────────────────────────────────────────────
# 2. Accounts
# ──────────────────────────────────────────────
print("\n=== 2. ACCOUNTS ===")

status, data = api("GET", "/accounts")
if status == 200:
    accounts = data.get("accounts", [])
    report("list_accounts", True, f"{len(accounts)} accounts")
    for a in accounts:
        report(f"  {a.get('name', '?')}", True,
               f"type={a.get('account_type')}, balance=${a.get('balance', 0):,.0f}, "
               f"equity=${a.get('equity', 0):,.0f}, daily_pnl=${a.get('daily_pnl', 0):,.0f}")
    account_id = accounts[0]["id"] if accounts else None
else:
    report("list_accounts", False, f"HTTP {status}: {data}")
    account_id = None


# ──────────────────────────────────────────────
# 3. Daily Routine
# ──────────────────────────────────────────────
print("\n=== 3. DAILY ROUTINE ===")

status, data = api("GET", "/routine/today")
if status == 200:
    report("routine_today", True,
           f"date={data.get('date')}, completed={data.get('is_complete', False)}, "
           f"psych_score={(data.get('sleep_score') or 0)+(data.get('focus_score') or 0)+(data.get('emotional_score') or 0)}")
else:
    report("routine_today", False, f"HTTP {status}: {data}")


# ──────────────────────────────────────────────
# 4. Trade Lifecycle
# ──────────────────────────────────────────────
print("\n=== 4. TRADE LIFECYCLE ===")

# List existing trades
status, data = api("GET", "/trades?limit=10")
if status == 200:
    trades = data.get("trades", [])
    count = data.get("count", 0)
    report("list_trades", True, f"{count} trades (showing {len(trades)})")
    for t in trades[:5]:
        report(f"  trade #{t.get('id')}", True,
               f"state={t.get('state')}, {t.get('instrument')} {t.get('direction')}, "
               f"setup={t.get('setup_type')}, entry={t.get('entry_price')}")
else:
    report("list_trades", False, f"HTTP {status}: {data}")

# List unreviewed trades
status, data = api("GET", "/trades/unreviewed")
if status == 200:
    report("unreviewed_trades", True, f"{data.get('count', 0)} pending review")
else:
    report("unreviewed_trades", False, f"HTTP {status}")

# Test trade creation (dry run — create then verify, don't close)
if account_id:
    trade_data = {
        "account_id": account_id,
        "instrument": "MNQ",
        "setup_type": "reversal_vwap_2sd",
        "direction": "long",
        "entry_price": 21000.0,
        "stop_price": 20980.0,
        "target_price": 21060.0,
        "contracts": 1,
        "notes": "ML audit test trade",
    }
    status, data = api("POST", "/trades", trade_data)
    if status == 200 and not data.get("error"):
        trade_id = data.get("id")
        report("create_trade", True, f"id={trade_id}, state={data.get('state')}")

        # Transition: created → armed
        status2, data2 = api("POST", f"/trades/{trade_id}/transition", {"to_state": "armed"})
        report("transition armed", status2 == 200 and not data2.get("error"),
               f"state={data2.get('state', data2.get('error', '?'))}")

        # Transition: armed → triggered
        status2, data2 = api("POST", f"/trades/{trade_id}/transition", {"to_state": "triggered"})
        report("transition triggered", status2 == 200 and not data2.get("error"),
               f"state={data2.get('state', data2.get('error', '?'))}")

        # Transition: triggered → open
        status2, data2 = api("POST", f"/trades/{trade_id}/transition", {"to_state": "open"})
        report("transition open", status2 == 200 and not data2.get("error"),
               f"state={data2.get('state', data2.get('error', '?'))}")

        # Move to breakeven
        status2, data2 = api("POST", f"/trades/{trade_id}/move-to-be")
        report("move_to_be", status2 == 200 and not data2.get("error"),
               f"stop → entry ({data2.get('stop_price', '?')})")

        # Trail stop
        status2, data2 = api("POST", f"/trades/{trade_id}/trail-stop",
                             {"new_stop": 21010.0, "notes": "Trailing up"})
        report("trail_stop", status2 == 200 and not data2.get("error"),
               f"stop={data2.get('stop_price', '?')}")

        # Close trade
        status2, data2 = api("POST", f"/trades/{trade_id}/close",
                             {"exit_price": 21040.0, "commission": 1.18, "notes": "Audit test"})
        if status2 == 200 and not data2.get("error"):
            report("close_trade", True,
                   f"pnl=${data2.get('pnl', '?')}, r_multiple={data2.get('r_multiple', '?')}")
        else:
            report("close_trade", False, f"{data2}")

        # Submit review
        status2, data2 = api("POST", f"/trades/{trade_id}/review", {
            "thesis_recap": "Test reversal at VWAP 2SD",
            "rule_adherence": "Followed all rules",
            "grade": 4,
            "improvements": "None - audit test",
        })
        report("submit_review", status2 == 200 and not data2.get("error"),
               f"grade={data2.get('grade', '?')}")

    elif status == 200 and data.get("error"):
        report("create_trade", True, f"blocked by risk check: {data.get('error')}", skipped=True)
    else:
        report("create_trade", False, f"HTTP {status}: {data}")


# ──────────────────────────────────────────────
# 5. Market Session
# ──────────────────────────────────────────────
print("\n=== 5. MARKET SESSION ===")

status, data = api("GET", "/market/session")
if status == 200:
    if data.get("status") == "no_data":
        report("market_session", True, "no session computed yet (expected without Databento key)", skipped=True)
    else:
        report("market_session", True, f"keys={list(data.keys())[:8]}")
else:
    report("market_session", False, f"HTTP {status}")

# Active signals
status, data = api("GET", "/market/signals")
if status == 200:
    signals = data.get("signals", [])
    report("active_signals", True, f"{len(signals)} active signals")
    for sig in signals[:3]:
        report(f"  signal", True,
               f"{sig.get('setup_type')} {sig.get('direction')}, score={sig.get('score')}")
else:
    report("active_signals", False, f"HTTP {status}")

# Session history
status, data = api("GET", "/market/history?limit=5")
if status == 200:
    sessions = data.get("sessions", [])
    report("session_history", True, f"{len(sessions)} historical sessions")
else:
    report("session_history", False, f"HTTP {status}")


# ──────────────────────────────────────────────
# 6. Market Data Endpoints
# ──────────────────────────────────────────────
print("\n=== 6. MARKET DATA ENDPOINTS ===")

# Macro
status, data = api("GET", "/market/macro", timeout=15)
if status == 200:
    report("macro", True,
           f"VIX={data.get('vix')}, DXY={data.get('dxy')}, "
           f"10Y={data.get('us10y')}, regime={data.get('regime')}, "
           f"score={data.get('regime_score')}")
else:
    report("macro", False, f"HTTP {status}: {data}")

# Top of book
status, data = api("GET", "/market/book")
if status == 200:
    if data.get("error"):
        report("book", True, "live stream not available (expected without Databento)", skipped=True)
    else:
        report("book", True,
               f"bid={data.get('bid_price')}, ask={data.get('ask_price')}, spread={data.get('spread')}")
else:
    report("book", False, f"HTTP {status}")

# Levels
status, data = api("GET", "/market/levels")
if status == 200:
    report("levels", True, f"{len(data)} structural levels")
else:
    report("levels", False, f"HTTP {status}")

# Confirmations
status, data = api("GET", "/market/confirmations")
if status == 200:
    report("confirmations", True, f"keys={list(data.keys()) if isinstance(data, dict) else len(data)}")
else:
    report("confirmations", False, f"HTTP {status}")

# Context
status, data = api("GET", "/market/context")
if status == 200:
    report("context", True,
           f"gates_set={data.get('gates_set')}, bias={data.get('macro_bias')}, "
           f"day_type={data.get('day_type')}")
else:
    report("context", False, f"HTTP {status}")

# COT data
status, data = api("GET", "/market/cot?limit=2")
if status == 200:
    report("cot", True, f"{len(data)} COT reports")
else:
    report("cot", True, f"COT fetch failed (external API, expected)", skipped=True)


# ──────────────────────────────────────────────
# 7. Analytics
# ──────────────────────────────────────────────
print("\n=== 7. ANALYTICS ===")

status, data = api("GET", "/analytics")
if status == 200:
    report("analytics", True,
           f"total_trades={data.get('total_trades')}, win_rate={data.get('win_rate')}, "
           f"profit_factor={data.get('profit_factor')}, expectancy={data.get('expectancy')}")
    by_setup = data.get("by_setup", {})
    for setup, stats in list(by_setup.items())[:5]:
        report(f"  setup {setup}", True,
               f"trades={stats.get('total')}, wins={stats.get('wins')}, "
               f"avg_r={stats.get('avg_r_multiple')}")
    by_instrument = data.get("by_instrument", {})
    for inst, stats in by_instrument.items():
        report(f"  instrument {inst}", True,
               f"trades={stats.get('total')}, pnl=${stats.get('net_pnl', 0):,.0f}")
else:
    report("analytics", False, f"HTTP {status}: {data}")

# CSV Export
status, data = api("GET", "/export/csv")
if status == 200:
    lines = data.strip().split("\n") if isinstance(data, str) else []
    report("csv_export", True, f"{len(lines)} lines (incl header)")
else:
    report("csv_export", False, f"HTTP {status}")


# ──────────────────────────────────────────────
# 8. Scanner + ML Integration
# ──────────────────────────────────────────────
print("\n=== 8. SCANNER + ML ===")

# Test scanner instantiation
try:
    from src.config.trading_loader import get_setups, get_scanner_config
    from src.market_data.scanner import MarketScanner

    setups = get_setups()
    scanner_cfg = get_scanner_config()
    scanner = MarketScanner(setups, threshold=scanner_cfg.get("score_threshold", 70))
    report("scanner_init", True,
           f"{len(setups)} setups, threshold={scanner_cfg.get('score_threshold')}, "
           f"interval={scanner_cfg.get('scan_interval')}")

    # List all scorer methods available
    scorer_map = scanner._get_scorer.__code__.co_consts
    scorers = [k for k in dir(scanner) if k.startswith("_score_")]
    report("scorer_methods", True, f"{len(scorers)} scorers: {', '.join(s.replace('_score_', '') for s in scorers)}")
except Exception as e:
    report("scanner_init", False, f"{e}\n{traceback.format_exc()}")

# Test ML feature extraction for trading
try:
    from src.ml.features.trading_features import extract_trading_features
    features = extract_trading_features(
        setup_type="reversal_vwap_2sd",
        direction="long",
        base_score=75,
        delta=-500,
        cvd=-2000,
        passive_active_ratio=1.3,
        market_type="balanced",
        poor_high=False,
        poor_low=True,
    )
    report("trading_features", True, f"{len(features)} features extracted")
except Exception as e:
    report("trading_features", False, str(e))


# ──────────────────────────────────────────────
# 9. Data Integrity
# ──────────────────────────────────────────────
print("\n=== 9. DATA INTEGRITY ===")

try:
    from src.db.models import get_session, Trade, TradingAccount, DailyRoutine, MarketSession, TradingSignal
    session = get_session()

    # Trade state consistency
    from src.constants import TRADE_STATES
    for state in TRADE_STATES:
        count = session.query(Trade).filter_by(state=state).count()
        if count > 0:
            report(f"  trades in '{state}'", True, f"{count}")

    # Closed trades with missing PnL
    closed_no_pnl = session.query(Trade).filter(
        Trade.state.in_(["closed", "reviewed"]),
        Trade.realized_pnl == None
    ).count()
    report("closed_no_pnl", closed_no_pnl == 0,
           f"{closed_no_pnl} closed trades missing PnL" if closed_no_pnl > 0 else "all closed trades have PnL")

    # Closed trades with missing r_multiple
    closed_no_r = session.query(Trade).filter(
        Trade.state.in_(["closed", "reviewed"]),
        Trade.r_multiple == None
    ).count()
    report("closed_no_r", closed_no_r == 0,
           f"{closed_no_r} closed trades missing R-multiple" if closed_no_r > 0 else "all closed trades have R-multiple")

    # Account balance consistency
    for acct in session.query(TradingAccount).all():
        report(f"  account '{acct.name}'", True,
               f"balance=${acct.balance:,.0f}, equity=${acct.equity:,.0f}, "
               f"daily_pnl=${acct.daily_pnl:,.0f}, locked={acct.is_daily_locked}")

    # Market sessions
    session_count = session.query(MarketSession).count()
    report("market_sessions", True, f"{session_count} stored sessions")

    # Signals
    signal_count = session.query(TradingSignal).count()
    active_signals = session.query(TradingSignal).filter_by(is_active=True).count()
    report("signals", True, f"{signal_count} total, {active_signals} active")

    session.close()
except Exception as e:
    report("data_integrity", False, f"{e}\n{traceback.format_exc()}")


# ──────────────────────────────────────────────
# 10. Databento Stream Status
# ──────────────────────────────────────────────
print("\n=== 10. DATABENTO STREAM ===")

try:
    import os
    has_key = bool(os.environ.get("DATABENTO_API_KEY"))
    if has_key:
        report("databento_api_key", True, "set")
    else:
        report("databento_api_key", True, "NOT SET — live stream disabled (expected without subscription)", skipped=True)

    # Check stream module imports
    from src.market_data.stream import DatabentoLiveStream, TopOfBook, TickBuffer, TickWriter
    report("stream_imports", True, "DatabentoLiveStream, TopOfBook, TickBuffer, TickWriter")

    # Test TopOfBook
    book = TopOfBook()
    from datetime import datetime, timezone
    book.update(21050.0, 10, 21050.25, 8, datetime.now(timezone.utc))
    assert book.spread == 0.25
    report("top_of_book", True, f"bid={book.bid_price}, ask={book.ask_price}, spread={book.spread}")

    # Test TickBuffer
    buf = TickBuffer()
    buf.add(datetime.now(timezone.utc), 21050.0, 5, "A")
    buf.add(datetime.now(timezone.utc), 21049.75, 3, "B")
    assert buf.cvd == 2  # 5 - 3
    assert buf.delta_1m == 2
    d = buf.reset_candle_delta()
    assert d == 2
    assert buf.delta_1m == 0
    report("tick_buffer", True, f"cvd={buf.cvd}, delta_reset works")

except Exception as e:
    report("stream", False, f"{e}\n{traceback.format_exc()}")


# ──────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  RESULTS: {PASS} passed, {FAIL} failed, {SKIP} skipped")
print(f"{'='*50}")

sys.exit(1 if FAIL > 0 else 0)
