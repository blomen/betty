"""RL Trading Agent CLI — fetch, replay, train, eval."""

from __future__ import annotations

import datetime as _dt_mod
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import torch
import typer

if TYPE_CHECKING:
    import numpy as np

_ET = ZoneInfo("US/Eastern")

rl_app = typer.Typer(help="RL Trading Agent — fetch, replay, train, eval")


def _generate_historical_news_events(start_year: int, end_year: int) -> list:
    """Generate NFP (first Friday 8:30 ET) + FOMC-proxy dates for historical
    replay. We don't have a full historical economic calendar going back to
    2011, so use the recurring high-impact events that dominate NQ:
    - NFP: first Friday of each month, 8:30 ET (12:30 UTC after DST adjust)
    - FOMC: 8 meetings/year, typically Wed at 14:00 ET; approximate as the
      4th-to-last Wed of Jan/Mar/May/Jun/Jul/Sep/Oct/Dec (rough but captures
      the timeslot and frequency).

    Returns list of dicts with ts_utc, importance (3 for FOMC, 2 for NFP).
    """
    from datetime import datetime, timedelta
    from datetime import timezone as _tz

    events = []
    for year in range(start_year, end_year + 1):
        # NFP: first Friday of each month, 8:30 ET = 13:30 UTC (EST) / 12:30 UTC (EDT)
        for month in range(1, 13):
            d = datetime(year, month, 1, tzinfo=_tz.utc)
            # find first Friday
            while d.weekday() != 4:
                d += timedelta(days=1)
            # 13:30 UTC approximation
            events.append(
                {
                    "ts_utc": d.replace(hour=13, minute=30),
                    "importance": 2,
                    "name": "NFP",
                }
            )
        # FOMC: approx 8/year, every 6 weeks starting late-January
        d = datetime(year, 1, 29, 19, 0, tzinfo=_tz.utc)  # 2:00 PM ET = 19:00 UTC
        for _ in range(8):
            # snap to Wednesday
            while d.weekday() != 2:
                d += timedelta(days=1)
            events.append({"ts_utc": d, "importance": 3, "name": "FOMC"})
            d += timedelta(weeks=6)
    events.sort(key=lambda e: e["ts_utc"])
    return events


def _compute_news_features(session_date, events: list) -> tuple[float, float]:
    """For a given session date, return (news_proximity, news_importance).

    news_proximity = 1 - minutes_to_nearest_event/120, clipped [0, 1]
    news_importance = nearest_event.importance / 3
    """
    from datetime import datetime
    from datetime import timezone as _tz

    if not events:
        return 0.0, 0.0
    # session midpoint: approximate 12:00 UTC on session_date
    if hasattr(session_date, "year"):
        ref = datetime(session_date.year, session_date.month, session_date.day, 12, 0, tzinfo=_tz.utc)
    else:
        return 0.0, 0.0
    # find nearest event within ±2h (events only matter intraday)
    nearest = None
    nearest_delta = None
    for e in events:
        delta_min = abs((e["ts_utc"] - ref).total_seconds()) / 60.0
        if delta_min > 24 * 60:
            continue
        if nearest_delta is None or delta_min < nearest_delta:
            nearest = e
            nearest_delta = delta_min
    if nearest is None or nearest_delta > 120:
        return 0.0, 0.0
    news_proximity = max(0.0, min(1.0, 1.0 - nearest_delta / 120.0))
    news_importance = nearest["importance"] / 3.0
    return news_proximity, news_importance


def _prepare_macro_data(macro_df, cot_df=None, stats_df=None) -> dict:
    """Convert raw macro parquet (VIX, DXY, US10Y, US2Y levels) into
    the dict format expected by extract_macro_features().

    Computes daily changes, yield curve spread, regime score,
    and merges weekly COT data (forward-filled to daily).

    The source parquet has separate rows per ticker per day (Yahoo bars
    arrive at 05:00 UTC for DXY and 06:00 UTC for VIX/yields, never
    merged). Without pre-processing, each row iteration sees only one
    field populated → dxy/us10y/us2y changes were all NaN and rendered
    as zeros in training. Collapse to one row per calendar date and
    forward-fill missing columns before computing changes.
    """
    import pandas as pd

    # Collapse to one row per ET-aware calendar date, forward-fill so every
    # row has every field populated (recovers 4 dead dims in macro[3..6]).
    if macro_df is not None and not macro_df.empty:
        df = macro_df.copy()
        # index is tz-aware UTC; use its date component as the key.
        df["_date"] = df.index.date
        df = df.groupby("_date").last().sort_index()
        df = df.ffill()  # fill gaps where a day only had one ticker reported
        df.index.name = "date"
        macro_df = df

    # Build COT lookup: forward-fill weekly COT to daily resolution
    cot_lookup: dict = {}
    if cot_df is not None and not cot_df.empty:
        # Reindex COT to daily frequency, forward-fill
        daily_idx = pd.date_range(cot_df.index.min(), cot_df.index.max(), freq="D")
        cot_daily = cot_df.reindex(daily_idx, method="ffill")
        for date_idx, row in cot_daily.iterrows():
            cot_lookup[str(date_idx)[:10]] = {
                "cot_net_position": float(row.get("cot_net_position", 0)),
                "cot_net_change": float(row.get("cot_net_change", 0)),
            }

    # Build statistics lookup: forward-fill daily stats
    stats_lookup: dict = {}
    if stats_df is not None and not stats_df.empty:
        import pandas as pd

        daily_idx = pd.date_range(stats_df.index.min(), stats_df.index.max(), freq="D")
        stats_daily = stats_df.reindex(daily_idx, method="ffill")
        for date_idx, row in stats_daily.iterrows():
            stats_lookup[str(date_idx)[:10]] = {
                "oi": float(row.get("open_interest", 0)),
                "oi_change": float(row.get("oi_change", 0)),
                "settlement_price": float(row.get("settlement_price", 0)),
                "cleared_volume": float(row.get("cleared_volume", 0)),
                "block_volume": float(row.get("block_volume", 0)),
            }

    # Pre-generate recurring US economic events (NFP + FOMC) for the macro
    # date range — recovers news_proximity / news_importance dims that were
    # hardcoded to 0.0 in historical replay.
    news_events = []
    if macro_df is not None and not macro_df.empty:
        idx = macro_df.index
        try:
            start_year = int(idx.min().year) if hasattr(idx.min(), "year") else 2011
            end_year = int(idx.max().year) if hasattr(idx.max(), "year") else 2026
        except Exception:
            start_year, end_year = 2011, 2026
        news_events = _generate_historical_news_events(start_year, end_year)

    macro_data: dict = {}
    prev_row = None
    for date_idx, row in macro_df.iterrows():
        date_str = str(date_idx)[:10]
        vix = float(row.get("VIX", 20.0))
        dxy = float(row.get("DXY", 100.0))
        us10y = float(row.get("US10Y", 4.0))
        us2y = float(row.get("US2Y", 4.0))

        if prev_row is not None:
            vix_change = vix - float(prev_row.get("VIX", vix))
            dxy_change = (dxy / float(prev_row.get("DXY", dxy)) - 1) * 100
            us10y_change = (us10y - float(prev_row.get("US10Y", us10y))) * 100  # bps
            us2y_change = (us2y - float(prev_row.get("US2Y", us2y))) * 100
        else:
            vix_change = 0.0
            dxy_change = 0.0
            us10y_change = 0.0
            us2y_change = 0.0

        # Simple regime score: risk_off when VIX high + yields rising
        regime_score = max(0.0, min(1.0, 0.5 + (vix - 20) / 40 + vix_change / 10))

        entry = {
            "vix": vix,
            "vix_change": vix_change,
            "regime_score": regime_score,
            "dxy_change": dxy_change,
            "us10y_change": us10y_change,
            "us2y_change": us2y_change,
            "us10y": us10y,
            "us2y": us2y,
            "yield_curve_spread": us10y - us2y,
            # COT defaults (overwritten if available)
            "cot_net_position": 0.0,
            "cot_net_change": 0.0,
            # News defaults (populated in live only)
            "news_proximity": 0.0,
            "news_importance": 0.0,
            # Exchange stats defaults (overwritten if available)
            "oi": 0.0,
            "oi_change": 0.0,
            "settlement_price": 0.0,
            "cleared_volume": 0.0,
            "block_volume": 0.0,
        }

        # Merge COT if available for this date
        cot = cot_lookup.get(date_str)
        if cot:
            entry["cot_net_position"] = cot["cot_net_position"]
            entry["cot_net_change"] = cot["cot_net_change"]

        # News proximity/importance from recurring event schedule
        try:
            news_prox, news_imp = _compute_news_features(date_idx, news_events)
            entry["news_proximity"] = news_prox
            entry["news_importance"] = news_imp
        except Exception:
            pass

        # Merge exchange stats if available for this date
        stats = stats_lookup.get(date_str)
        if stats:
            entry.update(stats)

        macro_data[date_str] = entry
        prev_row = row
    return macro_data


def _assign_session_date(ts_et):
    """Assign a tick to its futures session date based on 18:00 ET cutoff.

    NQ futures sessions run 18:00 ET → 17:00 ET next day.
    Ticks at/after 18:00 ET belong to the NEXT business day's session.
    Weekend ticks are dropped (returns None).
    """
    t = ts_et.time()
    d = ts_et.date()
    if t.hour >= 18:
        d = d + _dt_mod.timedelta(days=1)
        while d.weekday() >= 5:
            d = d + _dt_mod.timedelta(days=1)
    if d.weekday() >= 5:
        return None
    return d


# Paths
_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "rl"
_TICKS_DIR = _DATA_DIR / "ticks"
_EPISODES_DIR = _DATA_DIR / "episodes"
_MODELS_DIR = _DATA_DIR / "models"


def _target_obs_dim() -> int | None:
    """Dim that merge-live will accept (the main pool's dim, if it exists).
    Returns None when no main pool — caller falls back to the most common
    dim observed in the candidate rows.
    """
    main_path = _EPISODES_DIR / "observations.npy"
    if not main_path.exists():
        return None
    try:
        import numpy as _np

        with open(main_path, "rb") as _f:
            _v = _np.lib.format.read_magic(_f)
            shape, _, _ = _np.lib.format._read_array_header(_f, _v)
        return int(shape[1]) if len(shape) >= 2 else None
    except Exception:
        return None


def _simulate_session_position_states(
    touch_epochs,
    rewards_cont,
    rewards_rev,
    stop_targets,
    session_gap_s: float = 3600.0,
):
    """Greedy session-aware simulation of position state for each touch.

    Walks episodes in chronological order, maintains per-session trackers, and
    returns an (N, 8) array mirroring build_position_state's output at each
    touch. "Session boundary" = gap > session_gap_s since the last touch or a
    different ET calendar date.

    The simulation takes the greedy action at each touch (argmax of the three
    rewards) and carries session_pnl / consecutive_losses / trade_count forward.
    The observed state at touch t reflects the OUTCOME of touch t-1 — so the
    model sees realistic carry-over from prior decisions.
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    import numpy as np

    _ET = ZoneInfo("America/New_York")
    n = len(touch_epochs)
    states = np.zeros((n, 8), dtype=np.float32)
    if n == 0:
        return states

    # Chronological order
    order = np.argsort(touch_epochs)

    # Per-session trackers
    pos_side = "flat"
    entry_ts = 0.0
    session_pnl = 0.0
    consec_losses = 0
    trade_count = 0
    last_ts = 0.0
    last_date = None

    for idx in order:
        ts = float(touch_epochs[idx])
        if ts <= 0:
            # missing timestamp — observe zeros
            continue
        dt_et = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_ET).date()
        # Session boundary: different ET date OR long gap since last touch
        if last_date is None or dt_et != last_date or (ts - last_ts) > session_gap_s:
            pos_side = "flat"
            entry_ts = 0.0
            session_pnl = 0.0
            consec_losses = 0
            trade_count = 0
        last_ts = ts
        last_date = dt_et

        # Observation at this touch reflects state BEFORE this touch resolves
        pos_flat = 1.0 if pos_side == "flat" else 0.0
        pos_long = 1.0 if pos_side == "long" else 0.0
        pos_short = 1.0 if pos_side == "short" else 0.0
        # Unrealized R: episodes are point-in-time in training; treat as 0 at touch
        unrealized_r = 0.0
        time_in_trade = 0.0 if entry_ts == 0.0 else min((ts - entry_ts) / 3600.0, 1.0)
        session_pnl_norm = float(np.clip(session_pnl / 10.0, -1.0, 1.0))
        consec_norm = min(consec_losses / 3.0, 1.0)
        progress = min(trade_count / 20.0, 1.0)

        states[idx] = (
            pos_flat,
            pos_long,
            pos_short,
            unrealized_r,
            time_in_trade,
            session_pnl_norm,
            consec_norm,
            progress,
        )

        # Resolve greedy action and update trackers for next touch
        rc = float(rewards_cont[idx])
        rr = float(rewards_rev[idx])
        best = max(rc, rr, 0.0)  # skip reward = 0
        if best == 0.0:
            # SKIP: position unchanged, no trade
            continue
        trade_count += 1
        session_pnl += best
        if best <= 0.0:
            consec_losses += 1
        else:
            consec_losses = 0
        # Flip position for this simulated trade; assume flat-out at next touch
        # (we don't have forward ticks to trail a live position between episodes).
        # pos_side recorded is what the NEXT touch observes — reset to flat here
        # so carry-over is session_pnl/consec only, not a lingering position.
        pos_side = "flat"
        entry_ts = 0.0

    return states


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


@rl_app.command()
def fetch(
    months: int = typer.Option(6, help="Number of months of history to fetch"),
    symbol: str = typer.Option("NQ", help="Symbol to fetch (default: NQ)"),
    only: str | None = typer.Option(None, help="Comma-separated YYYY-MM months to fetch (overrides --months)"),
) -> None:
    """Fetch historical tick data and macro history from Databento / yfinance."""
    from src.rl.data.fetcher import fetch_macro_history, fetch_ticks

    if only:
        # Parse explicit month list and build date ranges
        import calendar

        month_labels = [m.strip() for m in only.split(",")]
        all_ranges = []
        for label in month_labels:
            year, month = int(label[:4]), int(label[5:7])
            m_start = datetime(year, month, 1, tzinfo=timezone.utc)
            _, last_day = calendar.monthrange(year, month)
            m_end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
            all_ranges.append((m_start, m_end))

        # Use the full span for the fetch call (skips existing files)
        start = min(r[0] for r in all_ranges)
        end = max(r[1] for r in all_ranges)

        typer.echo(f"Fetching {len(month_labels)} specific months for {symbol} ...")

        # Temporarily filter _month_ranges to only requested months
        import src.rl.data.fetcher as _fetcher_mod

        _orig_month_ranges = _fetcher_mod._month_ranges

        def _filtered_month_ranges(s, e):
            all_mr = _orig_month_ranges(s, e)
            requested = set(month_labels)
            return [(ms, me) for ms, me in all_mr if ms.strftime("%Y-%m") in requested]

        _fetcher_mod._month_ranges = _filtered_month_ranges
        try:
            tick_files = fetch_ticks(start, end)
        finally:
            _fetcher_mod._month_ranges = _orig_month_ranges
    else:
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=(months * 30 + 30))
        typer.echo(f"Fetching {symbol} ticks from {start.date()} to {end.date()} ...")
        tick_files = fetch_ticks(start, end)

    typer.echo(f"  Tick files written: {len(tick_files)}")
    for p in tick_files:
        typer.echo(f"    {p}")

    typer.echo("Fetching macro history (VIX, DXY, US10Y, US2Y) ...")
    macro_path = fetch_macro_history(start, end)
    if macro_path:
        typer.echo(f"  Macro file: {macro_path}")
    else:
        typer.echo("  Macro fetch failed or yfinance unavailable.")

    typer.echo("Fetching COT history (CFTC NQ positioning) ...")
    from src.rl.data.fetcher import fetch_cot_history

    cot_path = fetch_cot_history(start, end)
    if cot_path:
        typer.echo(f"  COT file: {cot_path}")
    else:
        typer.echo("  COT fetch failed.")

    # Fetch exchange statistics
    from src.rl.data.fetcher import fetch_statistics_history

    typer.echo("Fetching exchange statistics from Databento...")
    stats_path = fetch_statistics_history(start, end)
    if stats_path:
        typer.echo(f"Wrote statistics to {stats_path}")
    else:
        typer.echo("Warning: statistics fetch returned no data")


# ---------------------------------------------------------------------------
# export-trades — dump market_trades from DB to parquet files for replay
# ---------------------------------------------------------------------------


@rl_app.command("compare-models")
def compare_models() -> None:
    """List archived training runs with CV metrics for A/B comparison.

    Each pipeline run archives models + metrics to data/rl/archive/{timestamp}.
    This command prints a summary sorted by total R to make regressions obvious.
    """
    import json
    from pathlib import Path

    archive = Path("/app/backend/data/rl/archive")
    if not archive.exists():
        archive = Path("backend/data/rl/archive")
    if not archive.exists():
        typer.echo("No archive directory found. Runs archive after step 8.")
        return

    rows = []
    for d in sorted(archive.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        m_path = d / "metrics.json"
        if not m_path.exists():
            continue
        try:
            m = json.loads(m_path.read_text())
            rows.append(
                {
                    "ts": m.get("timestamp", d.name),
                    "trades": m.get("trades", "?"),
                    "win": m.get("win_rate_pct", "?"),
                    "avgR": m.get("avg_r", "?"),
                    "totalR": m.get("total_r", "?"),
                    "PF": m.get("profit_factor", "?"),
                    "maxDD": m.get("max_dd_r", "?"),
                }
            )
        except Exception:
            continue

    if not rows:
        typer.echo("No archived metrics found.")
        return

    typer.echo(f"{'Timestamp':<20} {'Trades':>10} {'Win%':>6} {'AvgR':>8} {'TotalR':>10} {'PF':>5} {'MaxDD':>8}")
    typer.echo("-" * 78)

    # Sort by total_r descending so best runs are at top
    def _to_float(v: str) -> float:
        try:
            return float(str(v).replace(",", "").replace("+", ""))
        except Exception:
            return 0.0

    for r in sorted(rows, key=lambda x: -_to_float(x["totalR"])):
        typer.echo(
            f"{r['ts']:<20} {r['trades']:>10} {r['win']:>5}% {r['avgR']:>8} "
            f"{r['totalR']:>10} {r['PF']:>5} {r['maxDD']:>7}R"
        )


@rl_app.command("export-trades")
def export_trades(
    symbol: str = typer.Option("NQ", help="Symbol to export"),
) -> None:
    """Export market_trades from DB to monthly parquet files for RL replay.

    Reads trades from the market database (TopstepX live data) and writes
    them as NQ_YYYY-MM.parquet files in the ticks directory.  Existing
    parquet files for a month are skipped unless the DB has newer data.
    """
    import calendar

    import pandas as pd
    from sqlalchemy import create_engine, text

    ticks_dir = _TICKS_DIR
    ticks_dir.mkdir(parents=True, exist_ok=True)

    # Connect to market DB
    import os

    pw = os.environ.get("DB_PASSWORD", "")
    db_url = f"postgresql://arnold:{pw}@postgres:5432/market"
    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            # Get date range of available trades
            row = conn.execute(
                text("SELECT MIN(ts), MAX(ts), COUNT(*) FROM market_trades WHERE ts > '2020-01-01'")
            ).fetchone()
            if not row or row[2] == 0:
                typer.echo("No trades in market_trades table.")
                return
            min_ts, max_ts, total = row
            typer.echo(f"Market trades: {total:,} rows, {min_ts} → {max_ts}")

            # Build month list
            from datetime import datetime

            current = datetime(min_ts.year, min_ts.month, 1)
            end = datetime(max_ts.year, max_ts.month, 1)
            months = []
            while current <= end:
                months.append(current)
                if current.month == 12:
                    current = datetime(current.year + 1, 1, 1)
                else:
                    current = datetime(current.year, current.month + 1, 1)

            exported = 0
            for month_start in months:
                label = month_start.strftime("%Y-%m")
                pfile = ticks_dir / f"{symbol}_{label}.parquet"
                _, last_day = calendar.monthrange(month_start.year, month_start.month)
                month_end = datetime(month_start.year, month_start.month, last_day, 23, 59, 59)

                # Skip if parquet exists and is newer than the month end
                # (means we already have complete data for this month)
                if pfile.exists():
                    # Check if DB has newer data than the parquet file
                    pfile_mtime = datetime.fromtimestamp(pfile.stat().st_mtime)
                    if month_end < datetime.now() and pfile_mtime > month_end:
                        typer.echo(f"  {label}: exists (complete month), skipping.")
                        continue

                # Query trades for this month
                df = pd.read_sql(
                    text(
                        "SELECT ts AS timestamp, price, size, side "
                        "FROM market_trades "
                        "WHERE ts >= :start AND ts < :end "
                        "ORDER BY ts"
                    ),
                    conn,
                    params={"start": month_start, "end": month_end},
                )
                if df.empty:
                    typer.echo(f"  {label}: no trades, skipping.")
                    continue

                # Ensure timestamp is UTC
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

                if pfile.exists():
                    # Merge with existing parquet (Databento + TopstepX)
                    existing = pd.read_parquet(pfile)
                    existing["timestamp"] = pd.to_datetime(existing["timestamp"], utc=True)
                    merged = (
                        pd.concat([existing, df])
                        .drop_duplicates(subset=["timestamp", "price", "size"], keep="first")
                        .sort_values("timestamp")
                        .reset_index(drop=True)
                    )
                    typer.echo(f"  {label}: merged {len(existing):,} existing + {len(df):,} DB → {len(merged):,} ticks")
                    merged.to_parquet(pfile, index=False)
                else:
                    df.to_parquet(pfile, index=False)
                    typer.echo(f"  {label}: exported {len(df):,} ticks")
                exported += 1

            typer.echo(f"\nExported/updated {exported} month(s).")
    except Exception as exc:
        typer.echo(f"Failed to export trades: {exc}", err=True)


# ---------------------------------------------------------------------------
# verify-levels
# ---------------------------------------------------------------------------


@rl_app.command("verify-levels")
def verify_levels(
    date: str = typer.Argument(help="Session date YYYY-MM-DD to verify"),
) -> None:
    """Replay a single session and print all computed levels for visual verification.

    This helps confirm session levels (PDH/PDL, Tokyo, London, IB, VWAP, VP,
    FVGs, OBs, swing points) are correct before training the RL agent.
    """
    import json

    import pandas as pd

    from src.rl.data.fetcher import TICKS_DIR
    from src.rl.data.replay_engine import ReplayEngine

    # Find the parquet file containing this date
    target = pd.Timestamp(date)
    month_str = target.strftime("%Y-%m")
    pfile = TICKS_DIR / f"NQ_{month_str}.parquet"

    if not pfile.exists():
        typer.echo(f"Tick file not found: {pfile}", err=True)
        typer.echo("Run 'rl fetch' first to download historical data.", err=True)
        raise typer.Exit(1)

    df = pd.read_parquet(pfile)
    if "timestamp" not in df.columns:
        typer.echo(f"No 'timestamp' column in {pfile.name}", err=True)
        raise typer.Exit(1)

    df["_ts_et"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(_ET)
    df["_session_date"] = df["_ts_et"].apply(_assign_session_date)
    df = df.dropna(subset=["_session_date"])
    target_date = target.date()
    day_df = df[df["_session_date"] == target_date].drop(columns=["_session_date", "_ts_et"], errors="ignore")

    if day_df.empty:
        typer.echo(f"No ticks found for {date} in {pfile.name}", err=True)
        raise typer.Exit(1)

    ticks = day_df.rename(columns={"timestamp": "ts"}).to_dict(orient="records")
    typer.echo(f"Replaying {len(ticks):,} ticks for {date} ...")

    session_dt = datetime(target_date.year, target_date.month, target_date.day, 12, 0, 0, tzinfo=_ET)

    engine = ReplayEngine()

    # Load precomputed levels if available
    from src.rl.data.session_store import compute_precomputed_levels, load_summaries

    summaries_path = _DATA_DIR / "session_summaries.json"
    summaries = load_summaries(summaries_path)
    precomputed = None
    if summaries:
        precomputed = compute_precomputed_levels(summaries, date)
        typer.echo(f"Loaded precomputed levels from {len(summaries)} sessions.")

    episodes = engine.replay_session(ticks, session_dt, precomputed_levels=precomputed)
    snapshot = engine.get_level_snapshot()

    typer.echo(f"\n{'=' * 60}")
    typer.echo(f"SESSION LEVELS — {date}")
    typer.echo(f"{'=' * 60}")

    sl = snapshot["session_levels"]
    for name, val in sl.items():
        if val is not None:
            typer.echo(f"  {name:20s}  {val:>12.2f}")

    typer.echo(f"\n{'─' * 60}")
    typer.echo("VWAP BANDS")
    for name, val in snapshot["vwap"].items():
        if val is not None:
            typer.echo(f"  {name:20s}  {val:>12.2f}")

    typer.echo(f"\n{'─' * 60}")
    typer.echo("VOLUME PROFILE")
    for name, val in snapshot["volume_profile"].items():
        if val is not None:
            typer.echo(f"  {name:20s}  {val:>12.2f}")

    typer.echo(f"\n{'─' * 60}")
    typer.echo(f"ACTIVE LEVELS ({len(snapshot['active_levels'])} total)")
    # Sort by price for easy visual checking
    sorted_levels = sorted(snapshot["active_levels"], key=lambda x: x["price"], reverse=True)
    for lv in sorted_levels:
        typer.echo(f"  {lv['price']:>12.2f}  {lv['type']:20s}  {lv['name']}")

    typer.echo(f"\n{'─' * 60}")
    typer.echo(f"FVGs: {len(snapshot['fvgs'])}  |  Order Blocks: {len(snapshot['order_blocks'])}")
    for fvg in snapshot["fvgs"][:5]:
        typer.echo(f"  FVG  {fvg['direction']:8s}  {fvg['low']:.2f} – {fvg['high']:.2f}")
    for ob in snapshot["order_blocks"][:5]:
        typer.echo(f"  OB   {ob['direction']:8s}  {ob['low']:.2f} – {ob['high']:.2f}")

    typer.echo(f"\n{'─' * 60}")
    typer.echo(f"EPISODES: {len(episodes)} level touches detected")
    for i, ep in enumerate(episodes[:10]):
        typer.echo(f"  {i + 1}. {ep.level_type:20s}  @ {ep.touch_ts}  best={ep.best_action}")

    # Also write JSON for frontend consumption
    out_path = _DATA_DIR / f"levels_{date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)
    typer.echo(f"\nJSON written to: {out_path}")


# ---------------------------------------------------------------------------
# analyze-be  — sweep breakeven trigger to find optimal R threshold
# ---------------------------------------------------------------------------


@rl_app.command("analyze-be")
def analyze_be_trigger(
    sample_files: int = typer.Option(10, help="Number of parquet files to sample (most recent first)"),
    be_values: str = typer.Option("1.0,1.25,1.5,1.75,2.0,2.5", help="Comma-separated BE trigger R values to test"),
) -> None:
    """Find the optimal breakeven trigger by sweeping R values across real tick data.

    For each level touch, runs the full stop lifecycle at every requested BE
    trigger and computes mean reward, fraction stopped-at-BE, and fraction of
    full losses.  Prints a comparison table so you can pick the value that
    maximises reward.

    Example::

        python -m src.app rl analyze-be --sample-files 15
        python -m src.app rl analyze-be --be-values "1.0,1.5,2.0,2.5"
    """
    import gc

    import numpy as np
    import pandas as pd

    from src.rl.config import COST_PER_TRADE_TICKS, STOP_TICKS
    from src.rl.data.episode_builder import (
        _TRAIL_BONUS_PER_LEVEL,
        _count_levels_captured,
        _measure_movement,
        _score_velocity,
    )
    from src.rl.data.fetcher import MACRO_DIR, TICKS_DIR
    from src.rl.data.replay_engine import ReplayEngine
    from src.rl.data.session_store import compute_precomputed_levels, load_summaries
    from src.rl.data.tick_array import TickArray

    triggers = [float(x.strip()) for x in be_values.split(",")]
    cost_r = COST_PER_TRADE_TICKS / max(STOP_TICKS, 1)

    # Load macro data for realistic replay context
    macro_data: dict = {}
    macro_path = MACRO_DIR / "macro_daily.parquet"
    if macro_path.exists():
        macro_df = pd.read_parquet(macro_path)
        macro_data = _prepare_macro_data(macro_df)

    summaries_path = _DATA_DIR / "session_summaries.json"
    summaries = load_summaries(summaries_path)

    # Pick files to sample (most recent first)
    all_files = sorted(TICKS_DIR.glob("NQ_*.parquet"))
    files = all_files[-sample_files:] if len(all_files) > sample_files else all_files
    typer.echo(f"Analyzing {len(files)} file(s) across {len(triggers)} BE trigger values: {triggers}")

    # Per-trigger accumulators: list of (reward, stopped_at_be, full_loss)
    stats: dict[float, list[tuple[float, bool, bool]]] = {t: [] for t in triggers}

    engine = ReplayEngine(macro_data=macro_data)
    prior_levels = None

    for pfile in files:
        typer.echo(f"  {pfile.name} ...", nl=False)
        df = pd.read_parquet(pfile)
        if "timestamp" not in df.columns:
            typer.echo(" skip (no timestamp)")
            continue

        df["_ts_et"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(_ET)
        df["_session_date"] = df["_ts_et"].apply(_assign_session_date)
        df = df.dropna(subset=["_session_date"])

        # Build raw_ticks once for the entire file (used by the BE sweep below)
        raw_ticks_file = [
            {"ts": row["_ts_et"], "price": float(row["price"])} for row in df[["_ts_et", "price"]].to_dict("records")
        ]
        # Index raw_ticks by position for O(1) session slicing
        raw_ts_array = [t["ts"] for t in raw_ticks_file]

        file_touches = 0
        for session_date in sorted(df["_session_date"].unique()):
            session_df = df[df["_session_date"] == session_date].drop(
                columns=["_session_date", "_ts_et"], errors="ignore"
            )
            session_dt = datetime(
                session_date.year,
                session_date.month,
                session_date.day,
                12,
                0,
                0,
                tzinfo=_ET,
            )
            date_str = str(session_date)
            ticks = TickArray.from_dataframe(session_df)

            precomputed = compute_precomputed_levels(summaries, date_str) if summaries else None

            try:
                episodes = engine.replay_session(
                    ticks,
                    session_dt,
                    prior_session_levels=prior_levels,
                    precomputed_levels=precomputed,
                )
            except Exception as exc:
                typer.echo(f"\n    replay failed for {session_date}: {exc}", err=True)
                continue
            finally:
                del ticks
                gc.collect()

            prior_levels = engine.get_prior_session_for_chaining()

            if not episodes:
                continue

            for ep in episodes:
                # Find the touch index in raw_ticks
                touch_ts = ep.touch_ts
                touch_price = ep.touch_price
                approach = ep.approach_direction
                direction = 1 if approach == "up" else -1

                # Locate touch in file-level raw_ticks (nearest ts)
                start = 0
                for k, ts in enumerate(raw_ts_array):
                    if ts >= touch_ts:
                        start = k
                        break
                end = min(start + 50_000, len(raw_ticks_file))

                # ep.state is None by default — use empty levels_ahead.
                # _count_levels_captured now handles empty lists (early return removed)
                # so the BE lifecycle still runs and at_be is correctly measured.
                levels_ahead: list[float] = []

                # Measure base velocity (same for all BE values)
                profiles = _measure_movement(touch_price, raw_ticks_file, start, end, touch_ts, direction)
                base_vel = _score_velocity(profiles)

                for be_r in triggers:
                    levels, at_be = _count_levels_captured(
                        touch_price,
                        raw_ticks_file,
                        start,
                        end,
                        touch_ts,
                        direction=direction,
                        levels_ahead=levels_ahead,
                        be_trigger_r=be_r,
                    )
                    reward = base_vel + levels * _TRAIL_BONUS_PER_LEVEL - cost_r

                    # Full loss: reward < -0.5R and not stopped at BE
                    # (proxy: reward ≈ base_vel - cost without BE protection)
                    full_loss = reward < -0.5 and not at_be

                    stats[be_r].append((reward, at_be, full_loss))

                file_touches += 1

        del df
        gc.collect()
        typer.echo(f" {file_touches} touches")

    # --- Print results table ---
    typer.echo(f"\n{'=' * 72}")
    typer.echo(f"{'BE trigger':>12}  {'N':>7}  {'Mean reward':>12}  {'Stopped@BE':>11}  {'Full losses':>11}")
    typer.echo(f"{'─' * 72}")

    best_trigger = triggers[0]
    best_reward = -999.0
    for be_r in triggers:
        entries = stats[be_r]
        if not entries:
            typer.echo(f"{be_r:>11.2f}R  {'—':>7}  {'no data':>12}")
            continue
        rewards = [e[0] for e in entries]
        n_be = sum(1 for e in entries if e[1])
        n_full = sum(1 for e in entries if e[2])
        mean_r = float(np.mean(rewards))
        pct_be = 100.0 * n_be / len(entries)
        pct_full = 100.0 * n_full / len(entries)
        marker = " ◄ best" if mean_r > best_reward else ""
        typer.echo(
            f"{be_r:>11.2f}R  {len(entries):>7,}  {mean_r:>+12.3f}R  {pct_be:>10.1f}%  {pct_full:>10.1f}%{marker}"
        )
        if mean_r > best_reward:
            best_reward = mean_r
            best_trigger = be_r

    typer.echo(f"{'=' * 72}")
    typer.echo(f"\nRecommended BE trigger: {best_trigger}R  (mean reward {best_reward:+.3f}R)")
    typer.echo(
        f"\nTo apply: set _BE_TRIGGER_R = {best_trigger} in episode_builder.py "
        "then re-run 'rl replay --all --clean && rl train'"
    )


# ---------------------------------------------------------------------------
# precompute
# ---------------------------------------------------------------------------


@rl_app.command()
def precompute(
    all_months: bool = typer.Option(False, "--all", help="Process all Parquet files"),
    month: str | None = typer.Option(None, help="Process a specific month YYYY-MM"),
) -> None:
    """Build session summaries from tick data for cross-session level computation."""
    import pandas as pd

    from src.rl.data.fetcher import TICKS_DIR
    from src.rl.data.session_store import build_session_summary, load_summaries, save_summaries

    ticks_dir = TICKS_DIR
    summaries_path = _DATA_DIR / "session_summaries.json"

    existing = load_summaries(summaries_path)
    typer.echo(f"Loaded {len(existing)} existing session summaries.")

    if all_months:
        parquet_files = sorted(ticks_dir.glob("NQ_*.parquet"))
    elif month:
        p = ticks_dir / f"NQ_{month}.parquet"
        if not p.exists():
            typer.echo(f"File not found: {p}", err=True)
            raise typer.Exit(1)
        parquet_files = [p]
    else:
        parquet_files = sorted(ticks_dir.glob("NQ_*.parquet"))

    if not parquet_files:
        typer.echo(f"No Parquet files found in {ticks_dir}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Processing {len(parquet_files)} tick file(s) ...")

    new_count = 0
    for pfile in parquet_files:
        try:
            df = pd.read_parquet(pfile)
        except Exception as exc:
            typer.echo(f"  Skipping {pfile.name}: {exc}")
            continue

        if "timestamp" not in df.columns:
            typer.echo(f"  Skipping {pfile.name}: no 'timestamp' column")
            continue

        df["_ts_et"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(_ET)
        df["_session_date"] = df["_ts_et"].apply(_assign_session_date)
        df = df.dropna(subset=["_session_date"])

        dates = sorted(df["_session_date"].unique())

        for session_date in dates:
            date_str = str(session_date)[:10]
            if date_str in existing:
                continue

            day_df = df[df["_session_date"] == session_date].copy()
            day_df["ts"] = day_df["_ts_et"]
            ticks = day_df[["ts", "price", "size", "side"]].to_dict(orient="records")

            if not ticks:
                continue

            summary = build_session_summary(date_str, ticks)
            existing[date_str] = summary
            new_count += 1

        typer.echo(f"  {pfile.name}: processed")

    save_summaries(existing, summaries_path)
    typer.echo(f"\nDone. {new_count} new sessions added. Total: {len(existing)} sessions.")
    typer.echo(f"Saved to: {summaries_path}")


# ---------------------------------------------------------------------------
# replay (parallel-capable)
# ---------------------------------------------------------------------------


def _replay_single_file(
    pfile_path: str,
    chunk_dir: str,
    chunk_idx: int,
    macro_data: dict,
    summaries: dict,
    gbt_path: str | None = None,
) -> tuple[int, int]:
    """Replay a single parquet file into episode chunks. Runs in subprocess.

    Memory-optimised: uses TickArray (column arrays) instead of
    to_dict(orient='records') — ~7x less RAM per session.

    Returns (n_episodes, n_sessions).
    """
    import gc
    import sys
    from datetime import datetime
    from pathlib import Path

    import numpy as np

    from src.rl.data.replay_engine import ReplayEngine
    from src.rl.data.session_store import compute_precomputed_levels
    from src.rl.data.tick_array import TickArray
    from src.rl.features.observation import augment_observation, build_position_state
    from src.rl.features.trigger_features import build_trigger_observation

    pfile = Path(pfile_path)
    out_dir = Path(chunk_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load GBT in this subprocess if needed
    gbt_model = None
    gbt_is_trigger = False
    if gbt_path:
        import joblib as _jl

        _gbt_data = _jl.load(Path(gbt_path))
        if isinstance(_gbt_data, dict) and str(_gbt_data.get("version", "")).startswith("v5_trigger"):
            from src.rl.agent.trigger_gbt import TriggerGBT

            gbt_model = TriggerGBT.load(Path(gbt_path))
            gbt_is_trigger = True
        else:
            from src.rl.agent.gbt_model import GBTModel

            gbt_model = GBTModel.load(Path(gbt_path))

    engine = ReplayEngine(macro_data=macro_data)

    # --- Phase 1: read parquet, compute session dates, group by date ---
    import pandas as pd

    df = pd.read_parquet(pfile)
    if "timestamp" not in df.columns:
        return 0, 0
    df["_ts_et"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(_ET)
    df["_session_date"] = df["_ts_et"].apply(_assign_session_date)
    df = df.dropna(subset=["_session_date"])
    df = df.rename(columns={"timestamp": "ts"})
    sorted_dates = sorted(df["_session_date"].unique())

    # Group by session and release the full DataFrame immediately
    session_groups: dict = {}
    for sd in sorted_dates:
        session_groups[sd] = df.loc[df["_session_date"] == sd, ["ts", "price", "size", "side"]]
    del df
    gc.collect()

    # --- Phase 2: replay each session using TickArray (not to_dict) ---
    month_obs, month_trig, month_rc, month_rr = [], [], [], []
    month_lt, month_st, month_be, month_lc = [], [], [], []
    month_gap, month_te = [], []  # overnight_gap, touch_epoch for labeler
    month_peak_c, month_peak_r = [], []  # Phase 3c peak_R per side for early_exit_model
    session_count = 0
    prior_levels = None

    for date_idx, session_date in enumerate(sorted_dates):
        session_df = session_groups.pop(session_date)
        ticks = TickArray.from_dataframe(session_df)
        del session_df
        gc.collect()

        if len(ticks) == 0:
            continue

        session_dt = datetime(
            session_date.year,
            session_date.month,
            session_date.day,
            12,
            0,
            0,
            tzinfo=_ET,
        )

        precomputed = None
        if summaries:
            precomputed = compute_precomputed_levels(summaries, str(session_date))

        try:
            episodes = engine.replay_session(
                ticks,
                session_dt,
                prior_session_levels=prior_levels,
                precomputed_levels=precomputed,
            )
        except Exception as _replay_exc:
            print(f"  replay_session FAILED for {session_date}: {_replay_exc}", file=sys.stderr)
            continue
        finally:
            del ticks
            gc.collect()

        prior_levels = engine.get_prior_session_for_chaining()

        # Reset weekly/monthly at boundaries
        if date_idx + 1 < len(sorted_dates):
            nd = sorted_dates[date_idx + 1]
            if hasattr(nd, "weekday") and nd.weekday() == 0:
                prior_levels["weekly_high"] = None
                prior_levels["weekly_low"] = None
            if hasattr(nd, "day") and nd.day == 1:
                prior_levels["monthly_high"] = None
                prior_levels["monthly_low"] = None

        for ep in episodes:
            obs = ep.observation

            # Build 118-dim trigger observation from the stored state. Phase 3b:
            # trigger obs no longer includes narrative or narrative-derived
            # setup_probs — the trigger layer identifies setups from
            # orderflow+level alignment, not narrative priors.
            trigger_obs = build_trigger_observation(ep.state, obs)

            if gbt_model is not None:
                if gbt_is_trigger:
                    gbt_forecast = gbt_model.predict_full(trigger_obs)
                    trigger_obs = build_trigger_observation(ep.state, obs, gbt_forecast)
                else:
                    gbt_forecast = gbt_model.predict_full(obs)
                    pos_state = build_position_state()
                    obs = augment_observation(obs, gbt_forecast, pos_state)

            # Extract label-relevant metadata before freeing state
            session_ctx = (ep.state or {}).get("session_context", {}) if ep.state else {}
            overnight_gap = float(session_ctx.get("overnight_gap", 0.0) or 0.0)
            touch_epoch = ep.touch_ts.timestamp() if ep.touch_ts else 0.0

            ep.state = None  # free large state dict — not needed after trigger_obs built

            month_obs.append(obs)
            month_trig.append(trigger_obs)
            month_rc.append(ep.reward_continuation)
            month_rr.append(ep.reward_reversal)
            month_lt.append(ep.level_type)
            month_st.append(ep.optimal_stop_ticks)
            month_be.append(float(ep.breakeven_reached))
            month_lc.append(float(ep.levels_captured_best))
            month_gap.append(overnight_gap)
            month_te.append(touch_epoch)
            month_peak_c.append(float(getattr(ep, "peak_R_cont", 0.0)))
            month_peak_r.append(float(getattr(ep, "peak_R_rev", 0.0)))

        del episodes
        gc.collect()
        session_count += 1

    gc.collect()

    n_eps = len(month_obs)
    if n_eps > 0:
        np.save(out_dir / f"obs_{chunk_idx:04d}.npy", np.array(month_obs, dtype=np.float32))
        np.save(out_dir / f"trig_{chunk_idx:04d}.npy", np.array(month_trig, dtype=np.float32))
        np.save(out_dir / f"rc_{chunk_idx:04d}.npy", np.array(month_rc, dtype=np.float32))
        np.save(out_dir / f"rr_{chunk_idx:04d}.npy", np.array(month_rr, dtype=np.float32))
        np.save(out_dir / f"lt_{chunk_idx:04d}.npy", np.array(month_lt))
        np.save(out_dir / f"st_{chunk_idx:04d}.npy", np.array(month_st, dtype=np.float32))
        np.save(out_dir / f"be_{chunk_idx:04d}.npy", np.array(month_be, dtype=np.float32))
        np.save(out_dir / f"lc_{chunk_idx:04d}.npy", np.array(month_lc, dtype=np.float32))
        np.save(out_dir / f"gap_{chunk_idx:04d}.npy", np.array(month_gap, dtype=np.float32))
        np.save(out_dir / f"te_{chunk_idx:04d}.npy", np.array(month_te, dtype=np.float64))
        np.save(out_dir / f"peakc_{chunk_idx:04d}.npy", np.array(month_peak_c, dtype=np.float32))
        np.save(out_dir / f"peakr_{chunk_idx:04d}.npy", np.array(month_peak_r, dtype=np.float32))

    return n_eps, len(sorted_dates)


@rl_app.command()
def replay(
    all_months: bool = typer.Option(False, "--all", help="Replay all Parquet files in TICKS_DIR"),
    month: str | None = typer.Option(None, help="Replay a specific month YYYY-MM"),
    gbt: str | None = typer.Option(None, help="GBT model for augmented observations (hybrid GBT+DQN)"),
    workers: int = typer.Option(0, help="Parallel workers (0 = auto, 1 = sequential)"),
    clean: bool = typer.Option(False, help="Delete existing chunks before replaying (fresh start)"),
) -> None:
    """Replay tick sessions through ReplayEngine and save episodes as .npy files.

    With --gbt: produces augmented episodes (base + 8 GBT forecast + 8 position state).
    Without --gbt: produces base episodes (market features only).
    Uses parallel workers for multi-core replay (default: auto = CPU count / 2).
    """
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed

    import numpy as np
    import pandas as pd

    from src.rl.data.fetcher import MACRO_DIR, TICKS_DIR
    from src.rl.data.normalization import RunningNormalizer
    from src.rl.features.observation import (
        AUGMENTED_OBSERVATION_DIM,
        OBSERVATION_DIM,
    )

    ticks_dir = TICKS_DIR
    episodes_dir = _EPISODES_DIR
    episodes_dir.mkdir(parents=True, exist_ok=True)

    # Collect Parquet files to replay
    if all_months:
        parquet_files = sorted(ticks_dir.glob("NQ_*.parquet"))
    elif month:
        p = ticks_dir / f"NQ_{month}.parquet"
        if not p.exists():
            typer.echo(f"File not found: {p}", err=True)
            raise typer.Exit(1)
        parquet_files = [p]
    else:
        parquet_files = sorted(ticks_dir.glob("NQ_*.parquet"))

    if not parquet_files:
        typer.echo(f"No Parquet files found in {ticks_dir}", err=True)
        raise typer.Exit(1)

    # Auto-detect worker count: 75% of CPUs (extraction is I/O-bound, RL is nice 19)
    if workers <= 0:
        workers = max(1, int(multiprocessing.cpu_count() * 0.75))
    workers = min(workers, len(parquet_files))

    typer.echo(f"Found {len(parquet_files)} tick file(s) to replay with {workers} worker(s).")

    # Load macro data
    macro_path = MACRO_DIR / "macro_daily.parquet"
    cot_path = MACRO_DIR / "cot_weekly.parquet"
    macro_data: dict = {}
    if macro_path.exists():
        try:
            macro_df = pd.read_parquet(macro_path)
            cot_df = pd.read_parquet(cot_path) if cot_path.exists() else None
            stats_path = MACRO_DIR / "statistics_daily.parquet"
            stats_df = None
            if stats_path.exists():
                try:
                    stats_df = pd.read_parquet(stats_path)
                    typer.echo(f"Loaded exchange statistics: {len(stats_df)} days.")
                except Exception as exc:
                    typer.echo(f"Warning: could not load statistics data: {exc}")
            else:
                typer.echo("No statistics_daily.parquet found — exchange stats features will be zeroed.")
            macro_data = _prepare_macro_data(macro_df, cot_df=cot_df, stats_df=stats_df)
            typer.echo(
                f"Loaded macro data: {len(macro_data)} days"
                + (f" (COT: {len(cot_df)} weeks)" if cot_df is not None else " (no COT)")
                + "."
            )
        except Exception as exc:
            typer.echo(f"Warning: could not load macro data: {exc}")
    else:
        typer.echo("No macro_daily.parquet found — macro features will be zeroed.")

    # Load session summaries
    from src.rl.data.session_store import load_summaries

    summaries_path = _DATA_DIR / "session_summaries.json"
    summaries = load_summaries(summaries_path)
    if summaries:
        typer.echo(f"Loaded session summaries: {len(summaries)} sessions.")
    else:
        typer.echo("No session_summaries.json found — precomputed levels disabled.")

    # GBT model path for augmentation
    gbt_path_str = None
    if gbt:
        gbt_path = Path(gbt) if Path(gbt).exists() else _MODELS_DIR / gbt
        if gbt_path.exists():
            gbt_path_str = str(gbt_path)
            typer.echo(f"Loaded GBT for augmentation: {gbt_path}")
        else:
            typer.echo(f"GBT not found: {gbt}. Replaying without augmentation.", err=True)

    obs_dim = AUGMENTED_OBSERVATION_DIM if gbt_path_str else OBSERVATION_DIM
    typer.echo(f"Observation dim: {obs_dim} ({'augmented' if gbt_path_str else 'base'})")

    # Chunk dir for results — RESUME-SAFE: skip files that already have chunks
    chunk_dir = episodes_dir / "_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        for old in chunk_dir.glob("*.npy"):
            old.unlink()
        typer.echo("Cleaned existing chunks (--clean flag).")
    set(chunk_dir.glob("obs_*.npy"))

    # Build file→chunk_idx mapping and skip already-completed files
    all_files_indexed = list(enumerate(parquet_files))
    todo_files = []
    skipped = 0
    for idx, pfile in all_files_indexed:
        chunk_path = chunk_dir / f"obs_{idx:04d}.npy"
        if chunk_path.exists():
            skipped += 1
        else:
            todo_files.append((idx, pfile))

    if skipped:
        typer.echo(f"\nResuming: {skipped} file(s) already have chunks, {len(todo_files)} remaining.")
    if not todo_files:
        typer.echo("All files already replayed — skipping to concatenation.")
    else:
        # Split into small (parallel) and large (subprocess-isolated)
        _LARGE_FILE_THRESHOLD = 30 * 1024 * 1024  # 30MB
        small_todo = [(i, p) for i, p in todo_files if p.stat().st_size <= _LARGE_FILE_THRESHOLD]
        large_todo = [(i, p) for i, p in todo_files if p.stat().st_size > _LARGE_FILE_THRESHOLD]
        if large_todo:
            typer.echo(f"\n{len(large_todo)} large file(s) will use subprocess isolation:")
            for _, lf in large_todo:
                typer.echo(f"  {lf.name} ({lf.stat().st_size / 1024 / 1024:.0f}MB)")

        # Parallel replay for small files
        if workers > 1 and small_todo:
            typer.echo(f"\nParallel replay: {workers} workers across {len(small_todo)} files...")
            try:
                with ProcessPoolExecutor(max_workers=workers) as executor:
                    futures = {}
                    for idx, pfile in small_todo:
                        future = executor.submit(
                            _replay_single_file,
                            pfile_path=str(pfile),
                            chunk_dir=str(chunk_dir),
                            chunk_idx=idx,
                            macro_data=macro_data,
                            summaries=summaries,
                            gbt_path=gbt_path_str,
                        )
                        futures[future] = pfile.name
                    for future in as_completed(futures):
                        fname = futures[future]
                        try:
                            n_eps, n_sessions = future.result()
                            typer.echo(f"  {fname}: {n_eps} episodes across {n_sessions} session(s)")
                        except Exception as exc:
                            typer.echo(f"  {fname}: FAILED — {exc}")
            except Exception as pool_exc:
                typer.echo(f"  ProcessPool crashed: {pool_exc}")

        elif small_todo:
            typer.echo(f"\nSequential replay: {len(small_todo)} files...")
            for idx, pfile in small_todo:
                try:
                    n_eps, n_sessions = _replay_single_file(
                        pfile_path=str(pfile),
                        chunk_dir=str(chunk_dir),
                        chunk_idx=idx,
                        macro_data=macro_data,
                        summaries=summaries,
                        gbt_path=gbt_path_str,
                    )
                    typer.echo(f"  {pfile.name}: {n_eps} episodes across {n_sessions} session(s)")
                except Exception as exc:
                    typer.echo(f"  {pfile.name}: FAILED — {exc}")

        # Large files: subprocess-isolated (OOM kills only the subprocess).
        # Run up to `workers` files in parallel — each subprocess is independent,
        # so a crash in one doesn't affect the others. Caps at len(large_todo) so
        # we never spawn idle workers.
        if large_todo:
            n_parallel = min(workers, len(large_todo))
            typer.echo(f"\nSubprocess replay for {len(large_todo)} large file(s) ({n_parallel} parallel)...")
            with ProcessPoolExecutor(max_workers=n_parallel) as pool:
                future_to_file = {
                    pool.submit(
                        _replay_single_file,
                        pfile_path=str(pfile),
                        chunk_dir=str(chunk_dir),
                        chunk_idx=idx,
                        macro_data=macro_data,
                        summaries=summaries,
                        gbt_path=gbt_path_str,
                    ): pfile
                    for idx, pfile in large_todo
                }
                for future in as_completed(future_to_file):
                    pfile = future_to_file[future]
                    try:
                        n_eps, n_sessions = future.result(timeout=7200)
                        typer.echo(f"  {pfile.name}: {n_eps} episodes across {n_sessions} session(s)")
                    except Exception as exc:
                        typer.echo(f"  {pfile.name}: FAILED — {exc}")

    # Concatenate all chunks from disk (including previously completed + new)
    chunk_indices = sorted(int(f.stem.split("_")[1]) for f in chunk_dir.glob("obs_*.npy"))
    if not chunk_indices:
        typer.echo("No episodes generated. Check tick data and replay engine.")
        raise typer.Exit(1)

    n_chunks = len(chunk_indices)
    total_episodes = sum(len(np.load(chunk_dir / f"obs_{i:04d}.npy")) for i in chunk_indices)
    typer.echo(f"\nConcatenating {n_chunks} chunks ({total_episodes} episodes)...")

    obs_array = np.concatenate([np.load(chunk_dir / f"obs_{i:04d}.npy") for i in chunk_indices])
    np.save(episodes_dir / "observations.npy", obs_array)

    # Trigger observations (118-dim, Phase 3b) — used by train-trigger-gbt
    trig_chunks = [chunk_dir / f"trig_{i:04d}.npy" for i in chunk_indices]
    if all(p.exists() for p in trig_chunks):
        trig_array = np.concatenate([np.load(p) for p in trig_chunks])
        np.save(episodes_dir / "trigger_observations.npy", trig_array)
        typer.echo(f"Trigger observations shape: {trig_array.shape}")
    else:
        typer.echo("Warning: some trig_*.npy chunks missing — trigger_observations.npy not saved.")

    np.save(
        episodes_dir / "rewards_cont.npy",
        np.concatenate([np.load(chunk_dir / f"rc_{i:04d}.npy") for i in chunk_indices]),
    )
    np.save(
        episodes_dir / "rewards_rev.npy",
        np.concatenate([np.load(chunk_dir / f"rr_{i:04d}.npy") for i in chunk_indices]),
    )
    np.save(
        episodes_dir / "level_types.npy",
        np.concatenate([np.load(chunk_dir / f"lt_{i:04d}.npy", allow_pickle=True) for i in chunk_indices]),
    )
    np.save(
        episodes_dir / "stop_targets.npy",
        np.concatenate([np.load(chunk_dir / f"st_{i:04d}.npy") for i in chunk_indices]),
    )
    np.save(
        episodes_dir / "breakeven_reached.npy",
        np.concatenate([np.load(chunk_dir / f"be_{i:04d}.npy") for i in chunk_indices]),
    )
    np.save(
        episodes_dir / "levels_captured.npy",
        np.concatenate([np.load(chunk_dir / f"lc_{i:04d}.npy") for i in chunk_indices]),
    )

    # Label metadata for setup labeler (has_gap proxy + touch_epoch → touch_time_et)
    gap_chunks = [chunk_dir / f"gap_{i:04d}.npy" for i in chunk_indices]
    te_chunks = [chunk_dir / f"te_{i:04d}.npy" for i in chunk_indices]
    if all(p.exists() for p in gap_chunks) and all(p.exists() for p in te_chunks):
        np.save(episodes_dir / "overnight_gap.npy", np.concatenate([np.load(p) for p in gap_chunks]))
        np.save(episodes_dir / "touch_epochs.npy", np.concatenate([np.load(p) for p in te_chunks]))

    # Phase 3c: peak_R arrays (max favorable R per side) for early_exit_model.
    peakc_chunks = [chunk_dir / f"peakc_{i:04d}.npy" for i in chunk_indices]
    peakr_chunks = [chunk_dir / f"peakr_{i:04d}.npy" for i in chunk_indices]
    if all(p.exists() for p in peakc_chunks) and all(p.exists() for p in peakr_chunks):
        np.save(episodes_dir / "peak_R_cont.npy", np.concatenate([np.load(p) for p in peakc_chunks]))
        np.save(episodes_dir / "peak_R_rev.npy", np.concatenate([np.load(p) for p in peakr_chunks]))

    # Clean up chunks
    for old in chunk_dir.glob("*.npy"):
        old.unlink()
    chunk_dir.rmdir()

    # Build normalizer from all observations
    normalizer = RunningNormalizer(dim=obs_array.shape[1])
    for obs in obs_array:
        normalizer.update(obs)
    normalizer.save(episodes_dir / "normalizer.json")

    typer.echo(f"\nTotal episodes: {total_episodes}")
    typer.echo(f"Observation shape: {obs_array.shape}")
    typer.echo(f"Saved to: {episodes_dir}")


# ---------------------------------------------------------------------------
# augment-trigger-obs — fast GBT forecast injection without re-replay
# ---------------------------------------------------------------------------


@rl_app.command("augment-trigger-obs")
def augment_trigger_obs(
    gbt_name: str = typer.Option("trigger_gbt_v5.joblib", help="Trigger GBT model filename"),
) -> None:
    """Fast replacement for step 5 (re-replay with GBT augmentation).

    Instead of re-replaying 39 parquets (~5h), this loads the saved
    trigger_observations.npy, runs TriggerGBT inference in batch to get
    the 8-dim forecast, and writes the forecast into the trigger_gbt slot
    (derived from the schema: TRIGGER_DIM − EXEC − TRIGGER_GBT_DIM = 107 in Phase 3b).

    Takes ~1 minute for 500K episodes vs 5 hours for re-replay.
    """
    import numpy as np

    from src.rl.agent.trigger_gbt import TriggerGBT

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR

    trigger_path = episodes_dir / "trigger_observations.npy"
    if not trigger_path.exists():
        typer.echo(f"No trigger_observations.npy in {episodes_dir}", err=True)
        raise typer.Exit(1)

    gbt_path = models_dir / gbt_name
    if not gbt_path.exists():
        typer.echo(f"No {gbt_name} in {models_dir}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Loading {trigger_path}...")
    trigger_obs = np.load(trigger_path)
    typer.echo(f"Loaded {len(trigger_obs):,} episodes, shape={trigger_obs.shape}")

    typer.echo(f"Loading {gbt_path}...")
    gbt = TriggerGBT.load(gbt_path)

    # Derive the GBT forecast slot from the trigger schema instead of hardcoding.
    from src.rl.features.trigger_features import EXEC_PASSTHROUGH_DIM, TRIGGER_DIM, TRIGGER_GBT_DIM

    gbt_start = TRIGGER_DIM - EXEC_PASSTHROUGH_DIM - TRIGGER_GBT_DIM
    gbt_end = gbt_start + TRIGGER_GBT_DIM

    trigger_obs_no_forecast = trigger_obs.copy()
    trigger_obs_no_forecast[:, gbt_start:gbt_end] = 0.0

    typer.echo("Running GBT inference in batches...")
    batch_size = 50000
    forecasts = []
    for i in range(0, len(trigger_obs_no_forecast), batch_size):
        chunk = trigger_obs_no_forecast[i : i + batch_size]
        fc = gbt.predict_full_batch(chunk)
        forecasts.append(fc)
        typer.echo(f"  {min(i + batch_size, len(trigger_obs_no_forecast)):,} / {len(trigger_obs_no_forecast):,}")
    forecasts = np.concatenate(forecasts, axis=0).astype(np.float32)

    trigger_obs[:, gbt_start:gbt_end] = forecasts

    typer.echo(f"Saving augmented trigger_obs to {trigger_path}...")
    np.save(trigger_path, trigger_obs)
    typer.echo(f"Done. {len(trigger_obs):,} episodes × {TRIGGER_DIM} dims with GBT forecast embedded.")


# ---------------------------------------------------------------------------
# merge-live — merge live episodes into the main episode pool
# ---------------------------------------------------------------------------


@rl_app.command("merge-live")
def merge_live() -> None:
    """Merge live-collected episodes into the main episode pool for training."""
    import numpy as np

    episodes_dir = _EPISODES_DIR
    live_dir = _DATA_DIR / "live_episodes"

    if not live_dir.exists():
        typer.echo("No live_episodes directory found.")
        raise typer.Exit(0)

    live_chunks = sorted(live_dir.glob("obs_*.npy"))
    if not live_chunks:
        typer.echo("No live episode chunks found.")
        raise typer.Exit(0)

    typer.echo(f"Found {len(live_chunks)} live episode chunks.")

    # Load live episodes
    live_obs = np.concatenate([np.load(f) for f in live_chunks])
    chunk_ids = [f.stem.split("_")[1] for f in live_chunks]
    live_rc = np.concatenate([np.load(live_dir / f"rc_{cid}.npy") for cid in chunk_ids])
    live_rr = np.concatenate([np.load(live_dir / f"rr_{cid}.npy") for cid in chunk_ids])
    live_lt = np.concatenate([np.load(live_dir / f"lt_{cid}.npy", allow_pickle=True) for cid in chunk_ids])
    live_st = np.concatenate([np.load(live_dir / f"st_{cid}.npy") for cid in chunk_ids])
    # Optional arrays (breakeven, levels_captured, trigger_obs)
    live_be = (
        np.concatenate([np.load(live_dir / f"be_{cid}.npy") for cid in chunk_ids])
        if all((live_dir / f"be_{cid}.npy").exists() for cid in chunk_ids)
        else None
    )
    live_lc = (
        np.concatenate([np.load(live_dir / f"lc_{cid}.npy") for cid in chunk_ids])
        if all((live_dir / f"lc_{cid}.npy").exists() for cid in chunk_ids)
        else None
    )
    live_trig_chunks = [live_dir / f"trig_{cid}.npy" for cid in chunk_ids]
    live_trig = (
        np.concatenate([np.load(f) for f in live_trig_chunks]) if all(f.exists() for f in live_trig_chunks) else None
    )

    # Tier-1 enrichment arrays from ingest-live-trades (LT* chunks). All
    # optional — simulator chunks won't have them, so default to zeros.
    def _load_optional(name: str):
        paths = [live_dir / f"{name}_{cid}.npy" for cid in chunk_ids]
        if not all(p.exists() for p in paths):
            return None
        return np.concatenate([np.load(p) for p in paths])

    live_pk_c = _load_optional("pk_c")
    live_pk_r = _load_optional("pk_r")
    live_ws = _load_optional("ws")
    live_tc = _load_optional("tc")
    live_sl = _load_optional("sl")
    live_placed_st = _load_optional("placed_st")

    typer.echo(
        f"Live episodes: {len(live_obs)} ({live_obs.shape[1]}-dim, trig={'yes' if live_trig is not None else 'no'})"
    )

    # Load existing main episodes
    main_obs_path = episodes_dir / "observations.npy"
    if main_obs_path.exists():
        main_obs = np.load(main_obs_path)
        main_rc = np.load(episodes_dir / "rewards_cont.npy")
        main_rr = np.load(episodes_dir / "rewards_rev.npy")
        main_lt = np.load(episodes_dir / "level_types.npy", allow_pickle=True)
        main_st = np.load(episodes_dir / "stop_targets.npy")
        main_be_path = episodes_dir / "breakeven_reached.npy"
        main_be = np.load(main_be_path) if main_be_path.exists() else None
        main_lc_path = episodes_dir / "levels_captured.npy"
        main_lc = np.load(main_lc_path) if main_lc_path.exists() else None
        main_trig_path = episodes_dir / "trigger_observations.npy"
        main_trig = np.load(main_trig_path) if main_trig_path.exists() else None
        typer.echo(f"Main episodes: {len(main_obs)} ({main_obs.shape[1]}-dim)")

        # Check dim compatibility
        if live_obs.shape[1] != main_obs.shape[1]:
            typer.echo(
                f"Dimension mismatch: live={live_obs.shape[1]} vs main={main_obs.shape[1]}. Cannot merge.", err=True
            )
            raise typer.Exit(1)

        # Concatenate core arrays
        merged_obs = np.concatenate([main_obs, live_obs])
        merged_rc = np.concatenate([main_rc, live_rc])
        merged_rr = np.concatenate([main_rr, live_rr])
        merged_lt = np.concatenate([main_lt, live_lt])
        merged_st = np.concatenate([main_st, live_st])

        # Merge optional arrays (pad with defaults if one side is missing)
        n_main, n_live = len(main_obs), len(live_obs)
        if live_be is not None or main_be is not None:
            m_be = main_be if main_be is not None else np.zeros(n_main, dtype=np.float32)
            l_be = live_be if live_be is not None else np.zeros(n_live, dtype=np.float32)
            np.save(episodes_dir / "breakeven_reached.npy", np.concatenate([m_be, l_be]))
        if live_lc is not None or main_lc is not None:
            m_lc = main_lc if main_lc is not None else np.zeros(n_main, dtype=np.float32)
            l_lc = live_lc if live_lc is not None else np.zeros(n_live, dtype=np.float32)
            np.save(episodes_dir / "levels_captured.npy", np.concatenate([m_lc, l_lc]))
        if live_trig is not None and main_trig is not None:
            if live_trig.shape[1] == main_trig.shape[1]:
                np.save(episodes_dir / "trigger_observations.npy", np.concatenate([main_trig, live_trig]))
                typer.echo(f"Trigger observations merged: {n_main + n_live} × {main_trig.shape[1]}-dim")
            else:
                typer.echo(
                    f"Trigger dim mismatch: main={main_trig.shape[1]} vs live={live_trig.shape[1]}, skipping trigger merge."
                )

        # Tier-1 enrichment merge — all optional, default-zero on the side
        # that lacks the array. Existing pipeline reads peak_R_cont/_rev
        # (used by early_exit_model); the other arrays land alongside main
        # pool so future training steps can pick them up.
        def _merge_aux(name: str, live_arr, default_dtype=np.float32):
            existing_path = episodes_dir / f"{name}.npy"
            existing = np.load(existing_path) if existing_path.exists() else None
            if live_arr is None and existing is None:
                return
            m = existing if existing is not None else np.zeros(n_main, dtype=default_dtype)
            l = live_arr if live_arr is not None else np.zeros(n_live, dtype=default_dtype)
            if len(m) != n_main:
                # Shape drift — pad with zeros to match current pool length
                m = np.zeros(n_main, dtype=default_dtype)
            np.save(existing_path, np.concatenate([m, l]))

        _merge_aux("peak_R_cont", live_pk_c)
        _merge_aux("peak_R_rev", live_pk_r)
        _merge_aux("was_stop", live_ws, default_dtype=np.int32)
        _merge_aux("trail_count", live_tc, default_dtype=np.int32)
        _merge_aux("slippage_ticks", live_sl)
        _merge_aux("placed_stop_ticks", live_placed_st)
    else:
        merged_obs = live_obs
        merged_rc = live_rc
        merged_rr = live_rr
        merged_lt = live_lt
        merged_st = live_st
        if live_be is not None:
            np.save(episodes_dir / "breakeven_reached.npy", live_be)
        if live_lc is not None:
            np.save(episodes_dir / "levels_captured.npy", live_lc)
        if live_trig is not None:
            np.save(episodes_dir / "trigger_observations.npy", live_trig)
        if live_pk_c is not None:
            np.save(episodes_dir / "peak_R_cont.npy", live_pk_c)
        if live_pk_r is not None:
            np.save(episodes_dir / "peak_R_rev.npy", live_pk_r)
        if live_ws is not None:
            np.save(episodes_dir / "was_stop.npy", live_ws)
        if live_tc is not None:
            np.save(episodes_dir / "trail_count.npy", live_tc)
        if live_sl is not None:
            np.save(episodes_dir / "slippage_ticks.npy", live_sl)
        if live_placed_st is not None:
            np.save(episodes_dir / "placed_stop_ticks.npy", live_placed_st)

    # Save merged core arrays
    np.save(episodes_dir / "observations.npy", merged_obs)
    np.save(episodes_dir / "rewards_cont.npy", merged_rc)
    np.save(episodes_dir / "rewards_rev.npy", merged_rr)
    np.save(episodes_dir / "level_types.npy", merged_lt)
    np.save(episodes_dir / "stop_targets.npy", merged_st)

    # Update normalizer
    from src.rl.data.normalization import RunningNormalizer

    normalizer = RunningNormalizer(dim=merged_obs.shape[1])
    for obs in merged_obs:
        normalizer.update(obs)
    normalizer.save(episodes_dir / "normalizer.json")

    typer.echo(
        f"Merged: {len(merged_obs)} total episodes ({len(live_obs)} live + {len(merged_obs) - len(live_obs)} historical)"
    )

    # Clean up live chunks (already merged)
    for f in live_dir.glob("*.npy"):
        f.unlink()
    typer.echo("Live episode chunks cleaned up.")


# ---------------------------------------------------------------------------
# ingest-live-trades — convert (signal, observation, realized_trade) into
# live_episodes chunks the existing merge-live + train pipeline consumes
# ---------------------------------------------------------------------------


@rl_app.command("ingest-live-trades")
def ingest_live_trades() -> None:
    """Pull (signal + observation + realized broker_trade) tuples from the
    DB and emit a live_episodes chunk that the standard merge-live → train
    pipeline picks up. Realized PnL becomes ground-truth reward; the
    captured observation becomes the state the model learns from.

    Idempotent — tracks ingested trade_ids in
    /app/data/rl/live_episodes/.ingested_trade_ids and skips duplicates.
    """
    import base64
    import os
    import time

    import numpy as np
    from sqlalchemy import create_engine, text

    live_dir = _DATA_DIR / "live_episodes"
    live_dir.mkdir(parents=True, exist_ok=True)
    seen_path = live_dir / ".ingested_trade_ids"
    seen: set[int] = set()
    if seen_path.exists():
        with seen_path.open() as f:
            seen = {int(x) for x in f.read().split() if x.strip().isdigit()}

    pw = os.environ.get("DB_PASSWORD", "")
    db_url = f"postgresql://arnold:{pw}@postgres:5432/arnold"
    engine = create_engine(db_url)

    sql = text(
        "SELECT s.id AS sid, s.action, s.confidence, s.cont_p, s.rev_p,"
        "       s.observation_b64, s.observation_dim, s.stop_ticks AS placed_stop_ticks,"
        "       t.id AS tid, t.pnl_dollars, t.pnl_r, t.exit_price, t.entry_price,"
        "       t.was_stop, t.trail_count, t.slippage_ticks, t.stop_ticks AS trade_stop_ticks,"
        "       t.ts AS trade_ts, t.closed_at, t.side AS trade_side, "
        # 2026-05-07: pull exit_reason + signal_trigger so the trainer can
        # distinguish how a trade closed (stop vs reversal-signal vs adverse-
        # slip kill) and filter recovery rows that aren't real model
        # decisions (orphan_recovery_winner/loss are SQL-inserted reconciliation
        # rows when bookkeeping bugs leave a broker fill unaccounted for).
        # Day 2 audit fix: also pull reasoning JSONB. Contains zone.families,
        # macro.regime_score, session_phase, trend_context per timeframe —
        # all valuable training signal currently being thrown away.
        "       t.exit_reason, t.signal_trigger, t.reasoning "
        "FROM stock_signals s "
        "JOIN broker_trades t ON t.id = s.trade_id "
        "WHERE s.observation_b64 IS NOT NULL "
        "  AND t.pnl_dollars IS NOT NULL"
    )
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()

    # Helper: query market_trades for the trade's lifetime to compute
    # MFE (max favorable excursion) and MAE (max adverse excursion).
    # Used to label peak_R (favourable extreme reached) and the
    # retrospective optimal stop distance (just past MAE).
    market_url = f"postgresql://arnold:{pw}@postgres:5432/market"
    market_engine = create_engine(market_url)
    mfe_mae_sql = text(
        "SELECT MIN(price) AS lo, MAX(price) AS hi "
        "FROM market_trades "
        "WHERE symbol = 'NQ' AND ts >= :start_ts AND ts <= :end_ts"
    )

    def _excursion(start_ts, end_ts, side: str, entry: float) -> tuple[float, float]:
        """Returns (mfe_pts, mae_pts) — both POSITIVE values measured
        relative to entry in the favourable / adverse direction."""
        if not start_ts or not end_ts or not entry:
            return 0.0, 0.0
        try:
            with market_engine.connect() as mconn:
                row = mconn.execute(mfe_mae_sql, {"start_ts": start_ts, "end_ts": end_ts}).fetchone()
        except Exception:
            return 0.0, 0.0
        if not row or row.lo is None or row.hi is None:
            return 0.0, 0.0
        if side == "long":
            mfe = max(0.0, float(row.hi) - float(entry))
            mae = max(0.0, float(entry) - float(row.lo))
        else:
            mfe = max(0.0, float(entry) - float(row.lo))
            mae = max(0.0, float(row.hi) - float(entry))
        return mfe, mae

    new_rows = [r for r in rows if r.tid not in seen]
    typer.echo(f"Found {len(rows)} labelled pairs total ({len(new_rows)} new since last ingest).")
    if not new_rows:
        return

    target_dim = _target_obs_dim()
    if target_dim is None:
        from collections import Counter

        dim_counts = Counter(int(r.observation_dim or 0) for r in new_rows if r.observation_dim)
        if dim_counts:
            target_dim = max(dim_counts, key=lambda d: (dim_counts[d], d))
    if target_dim:
        typer.echo(f"Target obs dim: {target_dim} (rows at other dims will be skipped).")

    obs_list: list[np.ndarray] = []
    rc_list: list[float] = []
    rr_list: list[float] = []
    lt_list: list[int] = []
    st_list: list[float] = []
    # Tier-1 enrichment arrays: ground-truth signals from broker_trades +
    # market_trades excursion analysis. Lets the trainer learn:
    # - was_stop      → did the placed stop actually get hit?
    # - peak_R        → favourable extreme reached during the trade
    # - placed_st     → stop the model actually placed
    # - trail_count   → did the trade get trailed, how many times?
    # - slippage_ticks→ adverse fill slip on entry
    pk_c_list: list[float] = []
    pk_r_list: list[float] = []
    ws_list: list[int] = []  # was_stop, 0/1
    tc_list: list[int] = []  # trail_count
    sl_list: list[float] = []  # slippage_ticks
    placed_st_list: list[float] = []
    er_list: list[int] = []  # exit_reason int code (Day 1 audit fix)
    tg_list: list[int] = []  # signal_trigger int code
    # Day 2 audit fix: reasoning JSONB → structured arrays
    fam_list: list[np.ndarray] = []  # multi-hot zone families (13 dims)
    reg_list: list[float] = []  # macro.regime_score
    phs_list: list[int] = []  # session_phase int code
    tnd_list: list[np.ndarray] = []  # 3 ints: daily/weekly/monthly trend
    skipped_dim = 0
    skipped_recovery = 0

    # 2026-05-07: encode exit_reason + signal_trigger as int codes so the
    # trainer can use them as auxiliary labels alongside the realized R.
    # A row with exit_reason=STOP is a fundamentally different training
    # signal than one that exited via REVERSAL_SIGNALS even when both have
    # R≈-1; the trainer should be able to distinguish "got stopped on
    # noise" from "model correctly faded the level."
    _EXIT_REASON_CODES = {
        "SIGNAL": 0,
        "STOP": 1,
        "REVERSAL_SIGNALS": 2,
        "FLIP_ON_REVERSAL": 3,
        "MANUAL": 4,
        "EOD_FLATTEN": 5,
        "ADVERSE_SLIP_KILL": 6,
        "SIZE_MISMATCH_RECOVERY": 7,
        "ORPHAN_POSITION": 8,
        "EARLY_EXIT_LOCK": 9,
        "MANUAL_RECOVER": 10,
    }
    _SIGNAL_TRIGGER_CODES = {
        "zone_entry": 0,
        "orphan_recovery_winner": 1,
        "orphan_recovery_loss": 2,
        "orphan_recovery": 3,  # generic
        "recovered": 4,
    }

    def _encode_exit_reason(s: str | None) -> int:
        if not s:
            return 99
        # Strip trail-suffix annotations like "STOP/TRAIL2"
        base = s.split("/")[0].upper().strip()
        return _EXIT_REASON_CODES.get(base, 99)

    def _encode_signal_trigger(s: str | None) -> int:
        if not s:
            return 99
        return _SIGNAL_TRIGGER_CODES.get(s.lower().strip(), 99)

    # Day 2: zone family multi-hot. The 13 distinct families in
    # zone_builder._LEVEL_FAMILY mapped to fixed indices.
    _ZONE_FAMILY_INDEX = {
        "daily_vp": 0,
        "weekly_vp": 1,
        "monthly_vp": 2,
        "vwap_band": 3,
        "prior_session": 4,
        "tokyo": 5,
        "nyib": 6,
        "tpo": 7,
        "naked_poc": 8,
        "daily_swing": 9,
        "weekly_swing": 10,
        "monthly_swing": 11,
        "fvg": 12,
        "order_block": 13,
    }
    _N_FAMILIES = 14

    def _encode_families(reasoning: dict | None) -> np.ndarray:
        out = np.zeros(_N_FAMILIES, dtype=np.float32)
        if not isinstance(reasoning, dict):
            return out
        zone = reasoning.get("zone") or {}
        families = zone.get("families") or []
        for fam in families:
            idx = _ZONE_FAMILY_INDEX.get(str(fam).lower().strip())
            if idx is not None:
                out[idx] = 1.0
        return out

    # Six session_phase buckets per _classify_session_phase in level_monitor.
    _SESSION_PHASE_CODES = {
        "tokyo": 0,
        "eu": 1,
        "rth_open": 2,
        "rth_mid": 3,
        "rth_close": 4,
        "post_close": 5,
    }

    def _encode_phase(reasoning: dict | None) -> int:
        if not isinstance(reasoning, dict):
            return 99
        return _SESSION_PHASE_CODES.get(str(reasoning.get("session_phase") or "").lower().strip(), 99)

    def _encode_regime_score(reasoning: dict | None) -> float:
        if not isinstance(reasoning, dict):
            return 0.0
        macro = reasoning.get("macro") or {}
        try:
            return float(macro.get("regime_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    # Trend per timeframe: ranging=0, trending_up=1, trending_down=2,
    # reversing_up=3, reversing_down=4. Unknown=99.
    _TREND_CODES = {
        "ranging": 0,
        "trending_up": 1,
        "trending_down": 2,
        "reversing_up": 3,
        "reversing_down": 4,
    }

    def _encode_trend(reasoning: dict | None) -> np.ndarray:
        out = np.full(3, 99, dtype=np.int32)
        if not isinstance(reasoning, dict):
            return out
        tc = reasoning.get("trend_context") or {}
        for i, key in enumerate(("daily", "weekly", "monthly")):
            v = str(tc.get(key) or "").lower().strip()
            out[i] = _TREND_CODES.get(v, 99)
        return out

    for r in new_rows:
        # 2026-05-07: filter out orphan_recovery rows. These were inserted
        # via SQL when a broker fill was missed by on_stream_fill — they
        # represent reconciliation, not real model decisions. The trainer
        # would learn from them as if they were normal entries, which
        # corrupts calibration.
        if r.signal_trigger and "orphan_recovery" in str(r.signal_trigger).lower():
            skipped_recovery += 1
            continue

        try:
            arr = np.frombuffer(base64.b64decode(r.observation_b64), dtype=np.float32)
            if r.observation_dim and r.observation_dim > 0 and arr.size != r.observation_dim:
                continue
            if target_dim and arr.size != target_dim:
                skipped_dim += 1
                continue
        except Exception:
            continue

        # stop_ticks resolution: signal-time placed value (s) takes
        # precedence; broker-side placed value (t) is the fallback.
        placed_stop_ticks = float(r.placed_stop_ticks or r.trade_stop_ticks or 25)
        risk_pts = max(placed_stop_ticks * 0.25, 0.25)

        # Reward: realized R-multiple if available, else derived from $ + stop_ticks.
        if r.pnl_r is not None:
            reward_r = float(r.pnl_r)
        else:
            stop_dollars = placed_stop_ticks * 5.0  # $5/tick NQ
            reward_r = float(r.pnl_dollars) / max(stop_dollars, 1.0)

        # Action label: pick CONT (0) vs REV (1) from the model's own
        # cont_p / rev_p at signal time — that's the decision the model
        # actually made. Falling back to CONT only when both are missing,
        # which keeps very-old rows trainable instead of dropping them.
        cont_p = float(r.cont_p) if r.cont_p is not None else 0.0
        rev_p = float(r.rev_p) if r.rev_p is not None else 0.0
        action_label = 1 if rev_p > cont_p else 0  # 0=CONT, 1=REV

        # rc / rr: realized reward attributed to the head the model picked.
        # Other head gets 0 (no information). Trainer's loss masks unobserved
        # heads, so this preserves the model's own CONT-vs-REV calibration in
        # the live training signal — losing it (the prior bug labeled every
        # live trade as CONT) makes the model unable to learn that its REV
        # picks were profitable / unprofitable distinctly from CONT.
        rc = reward_r if action_label == 0 else 0.0
        rr = reward_r if action_label == 1 else 0.0

        # Excursion analysis from market_trades — gives us peak_R + the
        # retrospective optimal stop distance (just past MAE). Falls back
        # to the realized R when market data is unavailable.
        side = r.trade_side or ("long" if action_label == 0 else "short")
        mfe_pts, mae_pts = _excursion(r.trade_ts, r.closed_at, side, float(r.entry_price or 0))
        peak_r_realized = mfe_pts / risk_pts if mfe_pts > 0 else max(reward_r, 0.0)
        # Optimal stop = MAE + 1-tick buffer, clamped to [10, 60] ticks so
        # one weird tick can't poison the label. Default = placed value when
        # no market data is available.
        if mae_pts > 0:
            optimal_stop_ticks = float(np.clip(round(mae_pts / 0.25) + 1, 10, 60))
        else:
            optimal_stop_ticks = placed_stop_ticks
        # Stop-target label that the trainer learns: optimal (truth)
        # rather than placed (what we did).
        stop_target = optimal_stop_ticks

        # peak_R per-side mirrors the simulator path's peak_R_cont /
        # peak_R_rev arrays so early_exit_model picks them up
        # automatically via merge-live.
        pk_c = peak_r_realized if action_label == 0 else 0.0
        pk_r = peak_r_realized if action_label == 1 else 0.0

        obs_list.append(arr.astype(np.float32))
        rc_list.append(np.float32(rc))
        rr_list.append(np.float32(rr))
        lt_list.append(int(action_label))
        st_list.append(np.float32(stop_target))
        pk_c_list.append(np.float32(pk_c))
        pk_r_list.append(np.float32(pk_r))
        ws_list.append(1 if r.was_stop else 0)
        tc_list.append(int(r.trail_count or 0))
        sl_list.append(np.float32(float(r.slippage_ticks or 0.0)))
        placed_st_list.append(np.float32(placed_stop_ticks))
        er_list.append(_encode_exit_reason(getattr(r, "exit_reason", None)))
        tg_list.append(_encode_signal_trigger(getattr(r, "signal_trigger", None)))
        # Day 2: structured features from reasoning JSONB
        reasoning_dict = getattr(r, "reasoning", None)
        if isinstance(reasoning_dict, str):
            try:
                import json as _json

                reasoning_dict = _json.loads(reasoning_dict)
            except Exception:
                reasoning_dict = None
        fam_list.append(_encode_families(reasoning_dict))
        reg_list.append(np.float32(_encode_regime_score(reasoning_dict)))
        phs_list.append(_encode_phase(reasoning_dict))
        tnd_list.append(_encode_trend(reasoning_dict))

    if skipped_dim:
        typer.echo(f"Skipped {skipped_dim} row(s) at non-target obs dim — marking ingested so they don't requeue.")

    if not obs_list:
        # Still persist the seen-set so dim-mismatched rows don't requeue forever.
        if skipped_dim:
            seen.update(r.tid for r in new_rows)
            with seen_path.open("w") as f:
                f.write(" ".join(str(x) for x in sorted(seen)))
        typer.echo("No usable rows after filtering.")
        return

    chunk_id = f"LT{int(time.time())}"  # LT = Live Trade
    np.save(live_dir / f"obs_{chunk_id}.npy", np.stack(obs_list))
    np.save(live_dir / f"rc_{chunk_id}.npy", np.array(rc_list, dtype=np.float32))
    np.save(live_dir / f"rr_{chunk_id}.npy", np.array(rr_list, dtype=np.float32))
    np.save(live_dir / f"lt_{chunk_id}.npy", np.array(lt_list, dtype=np.int32))
    np.save(live_dir / f"st_{chunk_id}.npy", np.array(st_list, dtype=np.float32))
    # Tier-1 enrichment: peak_R goes through the existing peak_R_cont /
    # peak_R_rev pipeline (used by early_exit_model). The other arrays are
    # auxiliary — merge-live writes them alongside the main pool so a
    # future training step can pick them up without re-ingesting.
    np.save(live_dir / f"pk_c_{chunk_id}.npy", np.array(pk_c_list, dtype=np.float32))
    np.save(live_dir / f"pk_r_{chunk_id}.npy", np.array(pk_r_list, dtype=np.float32))
    np.save(live_dir / f"ws_{chunk_id}.npy", np.array(ws_list, dtype=np.int32))
    np.save(live_dir / f"tc_{chunk_id}.npy", np.array(tc_list, dtype=np.int32))
    np.save(live_dir / f"sl_{chunk_id}.npy", np.array(sl_list, dtype=np.float32))
    np.save(live_dir / f"placed_st_{chunk_id}.npy", np.array(placed_st_list, dtype=np.float32))
    # 2026-05-07: exit_reason + signal_trigger as auxiliary training labels.
    # Trainer can use these to weight loss differently per outcome class
    # (e.g. STOP exits get full -1R signal, ADVERSE_SLIP_KILL gets reduced
    # weight because the kill was a safety mechanism not a strategy
    # decision). For now we just persist them; the next training step can
    # opt in to using them.
    np.save(live_dir / f"er_{chunk_id}.npy", np.array(er_list, dtype=np.int32))
    np.save(live_dir / f"tg_{chunk_id}.npy", np.array(tg_list, dtype=np.int32))
    # Day 2: reasoning JSONB → structured arrays
    np.save(
        live_dir / f"fam_{chunk_id}.npy",
        np.stack(fam_list) if fam_list else np.zeros((0, _N_FAMILIES), dtype=np.float32),
    )
    np.save(live_dir / f"reg_{chunk_id}.npy", np.array(reg_list, dtype=np.float32))
    np.save(live_dir / f"phs_{chunk_id}.npy", np.array(phs_list, dtype=np.int32))
    np.save(live_dir / f"tnd_{chunk_id}.npy", np.stack(tnd_list) if tnd_list else np.zeros((0, 3), dtype=np.int32))

    # Update seen set — include filtered-out recovery rows so they don't
    # requeue forever if more come in (the SQL filter is by trade_id, not
    # by signal_trigger).
    seen.update(r.tid for r in new_rows)
    with seen_path.open("w") as f:
        f.write(" ".join(str(x) for x in sorted(seen)))

    typer.echo(
        f"Wrote chunk {chunk_id}: {len(obs_list)} live-trade examples "
        f"(realized rewards + Tier-1 enrichment: was_stop, peak_R, trail_count, "
        f"slippage_ticks, optimal_stop, exit_reason, signal_trigger; "
        f"reasoning: families, regime_score, session_phase, trend_context). "
        f"Skipped {skipped_recovery} orphan_recovery row(s). "
        f"merge-live + train will pick them up."
    )


# ---------------------------------------------------------------------------
# train
# ---------------------------------------------------------------------------


@rl_app.command()
def train(
    epochs: int = typer.Option(100, help="Number of training epochs"),
    checkpoint: str = typer.Option("v1", help="Checkpoint name for saved model"),
    warm_start: bool = typer.Option(
        True,
        "--warm-start/--fresh",
        help="Warm-start from dqn_latest.pt if compatible (ONLINE1). Use --fresh to force random init.",
    ),
) -> None:
    """Train the DQN agent on replayed episodes.

    Warm-starts from dqn_latest.pt by default when the input dim matches —
    each retrain refines the prior model instead of restarting from scratch,
    which kills the ~8% run-to-run variance we had from random init.
    """
    import numpy as np

    from src.rl.agent.dqn import DQNAgent
    from src.rl.config import BATCH_SIZE, REWARD_CLIP_MAX, REWARD_CLIP_MIN, REWARD_NORMALIZE, Action
    from src.rl.data.normalization import RunningNormalizer

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    # Load episode arrays
    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy found in {episodes_dir}. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    observations = np.load(episodes_dir / "observations.npy")
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    np.load(episodes_dir / "level_types.npy", allow_pickle=True)
    stop_path = episodes_dir / "stop_targets.npy"
    stop_targets = np.load(stop_path) if stop_path.exists() else np.full(len(observations), 10.0, dtype=np.float32)

    # HYBRID MODE: augment base observation with GBT forecast + position state
    # base (302) + gbt_forecast (8) + position_state (8) = 318-dim
    trigger_path = episodes_dir / "trigger_observations.npy"
    if trigger_path.exists():
        trigger_obs = np.load(trigger_path)
        if len(trigger_obs) == len(observations):
            # Derive the GBT forecast slot from the current trigger schema instead
            # of hardcoding — keeps this aligned with augment-trigger-obs.
            from src.rl.features.trigger_features import EXEC_PASSTHROUGH_DIM, TRIGGER_DIM, TRIGGER_GBT_DIM

            _gbt_start = TRIGGER_DIM - EXEC_PASSTHROUGH_DIM - TRIGGER_GBT_DIM
            _gbt_end = _gbt_start + TRIGGER_GBT_DIM
            gbt_forecast = trigger_obs[:, _gbt_start:_gbt_end]  # (N, 8)
            # Session-aware position state: simulate greedy execution across touches in
            # chronological order, carrying position/session context forward. Previously
            # this was zeros, which made the 8 position dims dead weight.
            te_path = episodes_dir / "touch_epochs.npy"
            if te_path.exists():
                touch_epochs = np.load(te_path)
                position_state = _simulate_session_position_states(
                    touch_epochs=touch_epochs,
                    rewards_cont=rewards_cont,
                    rewards_rev=rewards_rev,
                    stop_targets=stop_targets,
                )
                typer.echo(f"Position state: session-aware simulation over {len(position_state)} episodes")
                # SESSION MEMORY (Phase 3c): chronological rolling win-rate, DD
                # from peak, consec-loss streak, etc. Teaches heads to recognise
                # hostile regimes from session context.
                from src.rl.features.session_memory_features import simulate_session_memory

                session_memory = simulate_session_memory(
                    touch_epochs=touch_epochs,
                    rewards_cont=rewards_cont,
                    rewards_rev=rewards_rev,
                )
                typer.echo(f"Session memory: {session_memory.shape[1]}-dim rolling context")
            else:
                position_state = np.zeros((len(observations), 8), dtype=np.float32)
                session_memory = np.zeros((len(observations), 6), dtype=np.float32)
                typer.echo("Position state + session memory: zeros (touch_epochs.npy missing — re-replay to enable)")
            observations = np.concatenate([observations, gbt_forecast, position_state, session_memory], axis=1).astype(
                np.float32
            )
            typer.echo(f"HYBRID: augmented obs (base + GBT + position + session_memory) → {observations.shape[1]}-dim")
        else:
            typer.echo("Warning: trigger_obs size mismatch, training DQN on base obs only")

    n = len(observations)
    typer.echo(f"Loaded {n} episodes ({observations.shape[1]}-dim) from {episodes_dir}")

    # --- Reward preprocessing: clip + normalize ---
    rewards_cont = np.clip(rewards_cont, REWARD_CLIP_MIN, REWARD_CLIP_MAX)
    rewards_rev = np.clip(rewards_rev, REWARD_CLIP_MIN, REWARD_CLIP_MAX)
    typer.echo(f"Rewards clipped to [{REWARD_CLIP_MIN}, {REWARD_CLIP_MAX}]")

    if REWARD_NORMALIZE:
        all_rewards = np.concatenate([rewards_cont, rewards_rev])
        r_mean = all_rewards.mean()
        r_std = all_rewards.std() + 1e-8
        rewards_cont = (rewards_cont - r_mean) / r_std
        rewards_rev = (rewards_rev - r_mean) / r_std
        typer.echo(f"Rewards normalized: mean={r_mean:.3f}, std={r_std:.3f}")

    # Load and apply normalizer — use actual obs dim from data
    obs_dim = observations.shape[1]
    normalizer_path = episodes_dir / "normalizer.json"
    normalizer = RunningNormalizer(dim=obs_dim)
    if normalizer_path.exists():
        import json as _json

        saved = _json.loads(normalizer_path.read_text())
        saved_dim = saved.get("dim", obs_dim)
        if saved_dim == obs_dim:
            normalizer.load(normalizer_path)
            typer.echo(f"Loaded normalizer (count={normalizer.count})")
        elif saved_dim < obs_dim:
            # Hybrid DQN: base obs was 279-dim, augmented to 295-dim.
            # Load saved stats for base dims; use identity (mean=0, std=1) for extras.
            base_norm = RunningNormalizer(dim=saved_dim)
            base_norm.load(normalizer_path)
            normalizer.count = base_norm.count
            normalizer.ewm_mean[:saved_dim] = base_norm.ewm_mean
            normalizer.ewm_var[:saved_dim] = base_norm.ewm_var
            # Extra dims (GBT forecast + position state) already scaled — pass-through
            typer.echo(f"Extended normalizer {saved_dim}→{obs_dim} (identity for {obs_dim - saved_dim} augmented dims)")
        else:
            raise ValueError(f"Saved normalizer dim {saved_dim} > expected {obs_dim}")
    else:
        typer.echo("Warning: no normalizer.json found — using raw observations.")

    normalized_obs = np.stack([normalizer.normalize(obs) for obs in observations])

    # Chronological split: 67% train, 16% val, 17% test
    train_end = int(n * 0.67)
    val_end = int(n * 0.83)

    train_obs = normalized_obs[:train_end]
    train_rc = rewards_cont[:train_end]
    train_rr = rewards_rev[:train_end]
    train_stops = stop_targets[:train_end]

    val_obs = normalized_obs[train_end:val_end]
    val_rc = rewards_cont[train_end:val_end]
    val_rr = rewards_rev[train_end:val_end]

    typer.echo(f"Split: train={len(train_obs)}, val={len(val_obs)}, test={n - val_end}")
    typer.echo(f"Architecture: Dueling Double DQN ({obs_dim}-dim) + stop head")

    agent = DQNAgent(observation_dim=obs_dim)

    # ONLINE1: warm-start from dqn_latest.pt if present + compatible.
    # Tries `dqn_{checkpoint}.pt` first (same-checkpoint resume), then
    # `dqn_latest.pt` (cross-checkpoint warm-start). Shape mismatch → cold start.
    start_epoch = 1
    model_path = models_dir / f"dqn_{checkpoint}.pt"
    latest_path = models_dir / "dqn_latest.pt"

    warm_start_source = None
    if warm_start:
        import torch as _torch

        for candidate in [model_path, latest_path]:
            if not candidate.exists():
                continue
            try:
                ckpt = _torch.load(candidate, weights_only=False, map_location="cpu")
                ckpt_dim = ckpt["q_network"]["encoder.0.weight"].shape[1]
                if ckpt_dim != obs_dim:
                    typer.echo(f"Skipping warm-start from {candidate.name}: input_dim {ckpt_dim} != {obs_dim}")
                    continue
                agent.load(candidate)
                start_epoch = ckpt.get("epoch", 0) + 1
                warm_start_source = candidate
                typer.echo(
                    f"Warm-started from {candidate.name} (epoch {start_epoch - 1}, "
                    f"epsilon={agent.epsilon:.3f}, steps={agent.train_steps})"
                )
                break
            except Exception as exc:
                typer.echo(f"Warm-start from {candidate.name} failed: {exc}", err=True)
    if warm_start_source is None:
        typer.echo("Cold start (no compatible checkpoint found or --fresh specified).")

    for i in range(len(train_obs)):
        rc = float(train_rc[i])
        rr = float(train_rr[i])
        st = float(train_stops[i])
        agent.store(train_obs[i], Action.CONTINUATION.value, rc, stop_target=st)
        agent.store(train_obs[i], Action.REVERSAL.value, rr, stop_target=st)
        # Store SKIP with reward=0 so Q(SKIP) learns to stay near zero
        agent.store(train_obs[i], Action.SKIP.value, 0.0, stop_target=st)

    typer.echo(f"Buffer loaded: {agent.buffer.size} transitions ({len(train_obs)} episodes x 3 actions)")

    if agent.buffer.size < BATCH_SIZE:
        typer.echo(f"Buffer too small ({agent.buffer.size} < {BATCH_SIZE}). Need more training data.", err=True)
        raise typer.Exit(1)

    # Steps per epoch: sweep the buffer ~once per epoch
    steps_per_epoch = max(1, agent.buffer.size // BATCH_SIZE)
    total_steps = epochs * steps_per_epoch

    # Set up cosine annealing LR scheduler
    from torch.optim.lr_scheduler import CosineAnnealingLR

    scheduler = CosineAnnealingLR(agent.optimizer, T_max=total_steps, eta_min=3e-5)
    # Fast-forward scheduler if resuming
    if start_epoch > 1:
        for _ in range((start_epoch - 1) * steps_per_epoch):
            scheduler.step()

    # Training loop with per-epoch val accuracy tracking + best-by-val checkpoint
    remaining = epochs - start_epoch + 1
    typer.echo(f"\nTraining for {remaining} epochs ({start_epoch}-{epochs}) x {steps_per_epoch} steps/epoch ...")
    typer.echo(f"LR: {scheduler.get_last_lr()[0]:.2e} -> 1e-5 cosine | Epsilon: {agent.epsilon:.2f} -> 0.05")
    best_val_acc = -1.0
    best_path = models_dir / f"dqn_{checkpoint}_best.pt"
    for epoch in range(start_epoch, epochs + 1):
        epoch_loss = 0.0
        for _step in range(steps_per_epoch):
            loss = agent.train_step()
            scheduler.step()
            epoch_loss += loss
        avg_loss = epoch_loss / steps_per_epoch
        # Per-epoch val accuracy so overfitting is visible as train-loss-falls/val-acc-stalls
        val_correct = 0
        for i in range(len(val_obs)):
            q = agent.q_network.predict(val_obs[i])[0]
            pred = int(np.argmax(q[:2]))
            actual = 0 if float(val_rc[i]) >= float(val_rr[i]) else 1
            if pred == actual:
                val_correct += 1
        val_acc = val_correct / max(len(val_obs), 1)
        if epoch % max(1, epochs // 20) == 0 or epoch == 1:
            lr = scheduler.get_last_lr()[0]
            typer.echo(
                f"  Epoch {epoch:>5}/{epochs}  train_loss={avg_loss:.4f}  val_acc={val_acc:.3f}  "
                f"epsilon={agent.epsilon:.3f}  lr={lr:.2e}"
            )
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            agent.save(best_path, epoch=epoch)
        if epoch % 5 == 0:
            ckpt_path = models_dir / f"dqn_{checkpoint}.pt"
            agent.save(ckpt_path, epoch=epoch)
            typer.echo(f"  [checkpoint saved: epoch {epoch}]")
    typer.echo(f"\nBest val accuracy during training: {best_val_acc:.3f} → saved to {best_path.name}")

    # Validation: check if model predicts the better direction correctly
    typer.echo("\nRunning validation ...")
    correct = 0
    for i in range(len(val_obs)):
        q_values = agent.q_network.predict(val_obs[i])[0]  # (NUM_ACTIONS,)
        predicted = int(np.argmax(q_values[:2]))  # Only CONT vs REV
        rc = float(val_rc[i])
        rr = float(val_rr[i])
        actual_best = 0 if rc >= rr else 1  # CONT vs REV
        if predicted == actual_best:
            correct += 1

    val_accuracy = correct / max(len(val_obs), 1)
    typer.echo(f"  Validation accuracy (CONT vs REV): {val_accuracy:.1%} ({correct}/{len(val_obs)})")

    # Save model
    model_path = models_dir / f"dqn_{checkpoint}.pt"
    agent.save(model_path, epoch=epochs)
    typer.echo(f"\nModel saved to: {model_path}")

    # Persist feature schema alongside model for compatibility checks.
    # Lets live_inference fail fast on dim/version mismatch instead of crashing
    # in a matmul deep inside PyTorch.
    try:
        from src.rl.features.registry import save_schema

        schema_path = models_dir / f"dqn_{checkpoint}_schema.json"
        save_schema(schema_path)
        typer.echo(f"Feature schema saved to: {schema_path}")
    except Exception as exc:
        typer.echo(f"Warning: could not save feature schema: {exc}")


# ---------------------------------------------------------------------------
# train-specialists
# ---------------------------------------------------------------------------


@rl_app.command("train-specialists")
def train_specialists(
    checkpoint: str = typer.Option("v5", help="Checkpoint name"),
    trees: int = typer.Option(300, help="Trees per specialist"),
    depth: int = typer.Option(5, help="Max depth"),
    lr: float = typer.Option(0.05, help="Learning rate"),
) -> None:
    """Train CONT and REV specialist models for binary direction prediction."""
    import numpy as np

    from src.rl.agent.specialists import (
        ContinuationSpecialist,
        ReversalSpecialist,
        SpecialistEnsemble,
        StopSpecialist,
    )

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    observations = np.load(episodes_dir / "observations.npy")
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    stop_path = episodes_dir / "stop_targets.npy"
    stop_targets = np.load(stop_path) if stop_path.exists() else None

    n = len(observations)
    typer.echo(f"Loaded {n:,} episodes ({observations.shape[1]}-dim)")

    # Subsample for memory safety
    MAX_SAMPLES = 250_000
    if n > MAX_SAMPLES:
        rng = np.random.RandomState(42)
        idx = rng.choice(n, MAX_SAMPLES, replace=False)
        idx.sort()
        observations = observations[idx]
        rewards_cont = rewards_cont[idx]
        rewards_rev = rewards_rev[idx]
        if stop_targets is not None:
            stop_targets = stop_targets[idx] if len(stop_targets) >= n else stop_targets
        n = MAX_SAMPLES
        typer.echo(f"Subsampled to {n:,}")

    # Chronological split
    train_end = int(n * 0.67)
    X_train = observations[:train_end]
    rc_train = rewards_cont[:train_end]
    rr_train = rewards_rev[:train_end]

    # --- Continuation Specialist ---
    typer.echo("\n=== Training Continuation Specialist ===")
    cont_success = (rc_train > 0).astype(np.int32)
    typer.echo(f"  Samples: {len(X_train):,}, win_rate: {cont_success.mean() * 100:.1f}%")

    cont_spec = ContinuationSpecialist()
    cont_metrics = cont_spec.train(
        X_train, cont_success, rc_train, n_estimators=trees, max_depth=depth, learning_rate=lr
    )
    typer.echo(f"  Results: {cont_metrics}")

    # --- Reversal Specialist ---
    typer.echo("\n=== Training Reversal Specialist ===")
    rev_success = (rr_train > 0).astype(np.int32)
    typer.echo(f"  Samples: {len(X_train):,}, win_rate: {rev_success.mean() * 100:.1f}%")

    rev_spec = ReversalSpecialist()
    rev_metrics = rev_spec.train(X_train, rev_success, rr_train, n_estimators=trees, max_depth=depth, learning_rate=lr)
    typer.echo(f"  Results: {rev_metrics}")

    # --- Stop Specialist ---
    stop_spec = None
    if stop_targets is not None and len(stop_targets) >= train_end:
        typer.echo("\n=== Training Stop Specialist ===")
        st_train = stop_targets[:train_end]
        typer.echo(f"  Samples: {len(X_train):,}, mean_stop: {st_train.mean():.1f} ticks")

        stop_spec = StopSpecialist()
        stop_metrics = stop_spec.train(X_train, st_train, n_estimators=trees, max_depth=depth, learning_rate=lr)
        typer.echo(f"  Results: {stop_metrics}")

    # --- Ensemble Evaluation ---
    ensemble = SpecialistEnsemble(cont_spec, rev_spec, stop_spec)

    # Evaluate on test set
    val_start = int(n * 0.67)
    val_end = int(n * 0.83)
    X_val = observations[val_start:val_end]
    rc_val = rewards_cont[val_start:val_end]
    rr_val = rewards_rev[val_start:val_end]

    actions, confidences, sizing = ensemble.decide_batch(X_val)
    val_n = len(X_val)

    typer.echo(f"\n=== Ensemble Validation ({val_n:,} episodes) ===")
    from collections import Counter

    ac = Counter(actions.tolist())
    typer.echo(f"  CONT: {ac.get(0, 0)} ({ac.get(0, 0) / val_n * 100:.1f}%)")
    typer.echo(f"  REV:  {ac.get(1, 0)} ({ac.get(1, 0) / val_n * 100:.1f}%)")
    typer.echo(f"  SKIP: {ac.get(2, 0)} ({ac.get(2, 0) / val_n * 100:.1f}%)")

    # Performance
    trade_mask = actions != 2
    traded_r = np.where(actions[trade_mask] == 0, rc_val[trade_mask], rr_val[trade_mask])
    if len(traded_r) > 0:
        wins = (traded_r > 0).sum()
        typer.echo(f"\n  Trades: {trade_mask.sum():,}")
        typer.echo(f"  Win rate: {wins / len(traded_r) * 100:.1f}%")
        typer.echo(f"  Avg R: {traded_r.mean():.3f}")
        typer.echo(f"  Total R: {traded_r.sum():.1f}")
        pf = traded_r[traded_r > 0].sum() / abs(traded_r[traded_r < 0].sum()) if (traded_r < 0).any() else float("inf")
        typer.echo(f"  Profit factor: {pf:.2f}")

        # CONT vs REV breakdown
        cont_mask = actions[trade_mask] == 0
        rev_mask = actions[trade_mask] == 1
        if cont_mask.sum() > 0:
            cr = rc_val[trade_mask][cont_mask]
            typer.echo(
                f"\n  CONT trades: n={cont_mask.sum()}, win={((cr > 0).sum() / len(cr)) * 100:.1f}%, avg_R={cr.mean():.3f}"
            )
        if rev_mask.sum() > 0:
            rr2 = rr_val[trade_mask][rev_mask]
            typer.echo(
                f"  REV trades:  n={rev_mask.sum()}, win={((rr2 > 0).sum() / len(rr2)) * 100:.1f}%, avg_R={rr2.mean():.3f}"
            )

        # Direction accuracy
        trade_idx = np.where(trade_mask)[0]
        correct = sum(
            1
            for i in trade_idx
            if (actions[i] == 0 and rc_val[i] >= rr_val[i]) or (actions[i] == 1 and rr_val[i] > rc_val[i])
        )
        typer.echo(f"\n  Direction accuracy: {correct}/{len(trade_idx)} ({correct / len(trade_idx) * 100:.1f}%)")

    # Save
    path = models_dir / f"specialists_{checkpoint}.joblib"
    ensemble.save(path)
    typer.echo(f"\nSaved to {path}")


# ---------------------------------------------------------------------------
# train-gbt
# ---------------------------------------------------------------------------


@rl_app.command("train-gbt")
def train_gbt(
    checkpoint: str = typer.Option("v1", help="Checkpoint name for saved model"),
    trees: int = typer.Option(500, help="Number of boosting rounds"),
    depth: int = typer.Option(5, help="Max tree depth"),
    lr: float = typer.Option(0.05, help="Learning rate (shrinkage)"),
) -> None:
    """Train multi-target GBT forecaster on replayed episodes."""
    import numpy as np

    from src.rl.agent.gbt_model import GBTModel
    from src.rl.config import REWARD_CLIP_MAX, REWARD_CLIP_MIN
    from src.rl.data.normalization import RunningNormalizer

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy found in {episodes_dir}. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    observations = np.load(episodes_dir / "observations.npy")
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    np.load(episodes_dir / "level_types.npy", allow_pickle=True)
    stop_path = episodes_dir / "stop_targets.npy"
    stop_targets = np.load(stop_path) if stop_path.exists() else np.full(len(observations), 10.0, dtype=np.float32)

    # Load additional targets for multi-head GBT (optional — backward compatible)
    be_path = episodes_dir / "breakeven_reached.npy"
    breakeven_reached = np.load(be_path) if be_path.exists() else None
    lc_path = episodes_dir / "levels_captured.npy"
    levels_captured = np.load(lc_path) if lc_path.exists() else None

    n = len(observations)
    typer.echo(f"Loaded {n:,} episodes ({observations.shape[1]}-dim) from {episodes_dir}")

    # Clip rewards
    rewards_cont = np.clip(rewards_cont, REWARD_CLIP_MIN, REWARD_CLIP_MAX)
    rewards_rev = np.clip(rewards_rev, REWARD_CLIP_MIN, REWARD_CLIP_MAX)

    # Normalize observations — use data dim, not code-computed OBSERVATION_DIM
    obs_dim = observations.shape[1]
    normalizer_path = episodes_dir / "normalizer.json"
    normalizer = RunningNormalizer(dim=obs_dim)
    if normalizer_path.exists():
        normalizer.load(normalizer_path)
        typer.echo(f"Loaded normalizer (count={normalizer.count})")

    normalized_obs = np.stack([normalizer.normalize(obs) for obs in observations])

    # Chronological split: 67% train, 16% val, 17% test
    train_end = int(n * 0.67)
    val_end = int(n * 0.83)

    train_obs = normalized_obs[:train_end]
    train_rc = rewards_cont[:train_end]
    train_rr = rewards_rev[:train_end]
    train_stops = stop_targets[:train_end]

    val_obs = normalized_obs[train_end:val_end]
    val_rc = rewards_cont[train_end:val_end]
    val_rr = rewards_rev[train_end:val_end]

    test_obs = normalized_obs[val_end:]
    test_rc = rewards_cont[val_end:]
    test_rr = rewards_rev[val_end:]

    typer.echo(f"Split: train={len(train_obs):,}, val={len(val_obs):,}, test={len(test_obs):,}")

    # Labels: 0=CONT better, 1=REV better
    y_train = np.where(train_rc >= train_rr, 0, 1).astype(np.int32)
    y_val = np.where(val_rc >= val_rr, 0, 1).astype(np.int32)
    y_test = np.where(test_rc >= test_rr, 0, 1).astype(np.int32)

    # Reward gap for sample weighting
    reward_gap = train_rc - train_rr

    # Split additional targets
    train_be = breakeven_reached[:train_end] if breakeven_reached is not None else None
    train_lc = levels_captured[:train_end] if levels_captured is not None else None

    typer.echo(f"\nTraining multi-target GBT: {trees} trees, depth={depth}, lr={lr} ...")
    model = GBTModel()
    metrics = model.train(
        X_train=train_obs,
        y_direction=y_train,
        stop_targets=train_stops,
        rewards_cont=train_rc,
        rewards_rev=train_rr,
        breakeven_reached=train_be,
        levels_captured=train_lc,
        reward_gap=reward_gap,
        n_estimators=trees,
        max_depth=depth,
        learning_rate=lr,
    )

    typer.echo(f"  Features: {metrics['alive_features']}/{metrics['total_features']} alive")
    typer.echo(f"  Trees used: {metrics['direction_trees']} (early stopping may reduce)")
    typer.echo(f"  Direction accuracy: {metrics['direction_accuracy']}%")
    if "breakeven_accuracy" in metrics:
        typer.echo(f"  Breakeven accuracy: {metrics['breakeven_accuracy']}%")

    # Validation
    typer.echo("\nValidation:")
    val_actions, val_conf, _ = model.predict_direction_batch(val_obs)
    val_acc = np.mean(val_actions == y_val) * 100
    val_reward = np.where(val_actions == 0, val_rc, val_rr)
    typer.echo(f"  Accuracy: {val_acc:.1f}%  avg_R={val_reward.mean():+.3f}")

    # Test
    typer.echo("\nTest:")
    test_actions, test_conf, test_probs = model.predict_direction_batch(test_obs)
    test_acc = np.mean(test_actions == y_test) * 100
    test_reward = np.where(test_actions == 0, test_rc, test_rr)
    typer.echo(f"  Accuracy: {test_acc:.1f}%  avg_R={test_reward.mean():+.3f}")

    # Confidence-filtered results
    typer.echo(f"\n  {'thresh':>8s}  {'n':>7s}  {'acc':>5s}  {'win%':>5s}  {'avg_R':>7s}  {'PF':>5s}")
    for thresh in [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50]:
        mask = test_conf >= thresh
        if mask.sum() < 10:
            continue
        acc = np.mean(test_actions[mask] == y_test[mask]) * 100
        chosen = np.where(test_actions[mask] == 0, test_rc[mask], test_rr[mask])
        wr = np.mean(chosen > 0) * 100
        wins = chosen[chosen > 0].sum()
        losses = abs(chosen[chosen < 0].sum())
        pf = wins / losses if losses > 0 else float("inf")
        typer.echo(f"  >={thresh:.2f}  {mask.sum():>7,}  {acc:5.1f}  {wr:5.1f}  {chosen.mean():+7.3f}  {pf:5.2f}")

    # Baselines
    typer.echo("\n  Baselines:")
    typer.echo(f"    always-REV:  avg_R={test_rr.mean():+.3f}")
    typer.echo(f"    always-CONT: avg_R={test_rc.mean():+.3f}")
    typer.echo(f"    oracle:      avg_R={np.maximum(test_rc, test_rr).mean():+.3f}")

    # Feature importance
    typer.echo("\n  Top 15 features:")
    segments = [
        (0, 31, "Zone composition"),
        (31, 52, "Orderflow"),
        (52, 116, "Dow/Session"),
        (116, 154, "TPO"),
        (154, 169, "Candle window"),
        (169, 173, "Zone features"),
        (173, 178, "Confluence"),
        (178, 189, "Macro"),
        (189, 194, "Exchange stats"),
        (194, 208, "Setup detection"),
        (208, 221, "AMT"),
        (221, 241, "Micro"),
        (241, 242, "Approach dir"),
        (242, 249, "Execution ctx"),
    ]
    for orig_idx, imp in model.feature_importance(top_n=15):
        seg_name = "unknown"
        for s, e, name in segments:
            if s <= orig_idx < e:
                seg_name = f"{name}[{orig_idx - s}]"
                break
        typer.echo(f"    dim {orig_idx:3d} ({seg_name:30s}): {imp:.4f}")

    # Stop prediction quality
    test_stops = stop_targets[val_end:]
    pred_stops = model.predict_stop_batch(test_obs)
    stop_mae = np.mean(np.abs(pred_stops - test_stops))
    typer.echo(f"\n  Stop prediction MAE: {stop_mae:.1f} ticks")

    # Save
    model_path = models_dir / f"gbt_{checkpoint}.joblib"
    model.save(model_path)
    typer.echo(f"\nModel saved to: {model_path}")


# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------


@rl_app.command()
def eval(
    checkpoint: str = typer.Option("v1", help="Checkpoint name to load"),
    skip_threshold: float = typer.Option(
        0.15,
        help="Min Q-spread to trade. 0.15 matches live gate after threshold sweep showed 7× total R vs 0.30.",
    ),
) -> None:
    """Evaluate the trained DQN agent on the test split.

    The model predicts Q(CONT) and Q(REV). If |Q_cont - Q_rev| < skip_threshold,
    the model is uncertain about direction and the episode is SKIPped.
    """
    import numpy as np

    from src.rl.agent.dqn import DQNAgent
    from src.rl.agent.evaluate import compute_metrics, print_evaluation_report
    from src.rl.config import Action
    from src.rl.data.normalization import RunningNormalizer

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR
    model_path = models_dir / f"dqn_{checkpoint}.pt"

    if not model_path.exists():
        typer.echo(f"Model not found: {model_path}. Run 'rl train' first.", err=True)
        raise typer.Exit(1)

    # Load episodes
    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo("No observations.npy found. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    observations = np.load(episodes_dir / "observations.npy")
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    level_types = np.load(episodes_dir / "level_types.npy", allow_pickle=True)

    # HYBRID: augment base obs with GBT forecast + position state (mirror of train)
    trigger_path = episodes_dir / "trigger_observations.npy"
    if trigger_path.exists():
        trigger_obs = np.load(trigger_path)
        if len(trigger_obs) == len(observations):
            from src.rl.features.trigger_features import EXEC_PASSTHROUGH_DIM, TRIGGER_DIM, TRIGGER_GBT_DIM

            _gbt_start = TRIGGER_DIM - EXEC_PASSTHROUGH_DIM - TRIGGER_GBT_DIM
            _gbt_end = _gbt_start + TRIGGER_GBT_DIM
            gbt_forecast = trigger_obs[:, _gbt_start:_gbt_end]
            position_state = np.zeros((len(observations), 8), dtype=np.float32)
            # Session memory: use real simulation if touch_epochs are available,
            # else zeros (match train behaviour).
            _te_path = episodes_dir / "touch_epochs.npy"
            if _te_path.exists():
                from src.rl.features.session_memory_features import simulate_session_memory

                _touch_epochs = np.load(_te_path)
                session_memory = simulate_session_memory(
                    touch_epochs=_touch_epochs[: len(observations)],
                    rewards_cont=rewards_cont[: len(observations)],
                    rewards_rev=rewards_rev[: len(observations)],
                )
            else:
                session_memory = np.zeros((len(observations), 6), dtype=np.float32)
            observations = np.concatenate([observations, gbt_forecast, position_state, session_memory], axis=1).astype(
                np.float32
            )
            typer.echo(f"HYBRID: augmented eval obs → {observations.shape[1]}-dim")

    n = len(observations)
    obs_dim = observations.shape[1]

    # Load normalizer — extend if saved dim smaller than augmented
    normalizer_path = episodes_dir / "normalizer.json"
    normalizer = RunningNormalizer(dim=obs_dim)
    if normalizer_path.exists():
        import json as _json

        saved = _json.loads(normalizer_path.read_text())
        saved_dim = saved.get("dim", obs_dim)
        if saved_dim == obs_dim:
            normalizer.load(normalizer_path)
        elif saved_dim < obs_dim:
            base_norm = RunningNormalizer(dim=saved_dim)
            base_norm.load(normalizer_path)
            normalizer.count = base_norm.count
            normalizer.ewm_mean[:saved_dim] = base_norm.ewm_mean
            normalizer.ewm_var[:saved_dim] = base_norm.ewm_var

    normalized_obs = np.stack([normalizer.normalize(obs) for obs in observations])

    # Test split: last 17%
    val_end = int(n * 0.83)
    test_obs = normalized_obs[val_end:]
    test_rc = rewards_cont[val_end:]
    test_rr = rewards_rev[val_end:]
    test_lt = level_types[val_end:]

    typer.echo(f"Test split: {len(test_obs)} episodes (last 17% of {n})")
    typer.echo(f"Skip threshold: {skip_threshold}")

    # Load agent with greedy policy
    agent = DQNAgent(observation_dim=obs_dim, epsilon=0.0)
    agent.load(model_path)
    agent.epsilon = 0.0
    typer.echo(f"Loaded model: {model_path}")

    # Run evaluation with confidence-based skipping
    episode_dicts: list[dict] = []
    for i in range(len(test_obs)):
        q_values = agent.q_network.predict(test_obs[i])[0]  # (NUM_ACTIONS,)
        # Only consider CONT and REV Q-values
        q_cont = float(q_values[Action.CONTINUATION.value])
        q_rev = float(q_values[Action.REVERSAL.value])
        q_spread = abs(q_cont - q_rev)

        rc = float(test_rc[i])
        rr = float(test_rr[i])

        if q_spread < skip_threshold:
            # Model uncertain about direction — skip
            action = Action.SKIP.value
            reward = 0.0
        elif q_cont >= q_rev:
            action = Action.CONTINUATION.value
            reward = rc
        else:
            action = Action.REVERSAL.value
            reward = rr

        episode_dicts.append(
            {
                "action": action,
                "reward": reward,
                "level_type": str(test_lt[i]),
            }
        )

    metrics = compute_metrics(episode_dicts)
    print_evaluation_report(metrics)


# ---------------------------------------------------------------------------
# backtest (SessionManager with position flipping, trailing, compounding)
# ---------------------------------------------------------------------------


@rl_app.command()
def backtest(
    checkpoint: str = typer.Option("v1", help="Checkpoint name to load"),
    min_spread: float = typer.Option(0.01, help="Min Q-spread to enter a trade"),
) -> None:
    """Backtest the SessionManager on historical tick data.

    Unlike `eval` which treats each level touch independently, backtest
    simulates a full trading session with position flipping, trailing stops,
    confidence-based sizing, and intraday compounding.
    """
    import pandas as pd

    from src.rl.agent.gbt_model import GBTModel
    from src.rl.agent.network import DQNetwork
    from src.rl.data.fetcher import MACRO_DIR, TICKS_DIR
    from src.rl.data.normalization import RunningNormalizer
    from src.rl.data.replay_engine import ReplayEngine
    from src.rl.data.session_store import compute_precomputed_levels, load_summaries
    from src.rl.features.observation import OBSERVATION_DIM
    from src.rl.session_manager import SessionManager

    models_dir = _MODELS_DIR

    # Try GBT first, fall back to DQN
    gbt_path = models_dir / f"gbt_{checkpoint}.joblib"
    dqn_path = models_dir / f"dqn_{checkpoint}.pt"

    if gbt_path.exists():
        network = GBTModel.load(gbt_path)
        typer.echo(f"Loaded GBT model: {gbt_path}")
    elif dqn_path.exists():
        ckpt = torch.load(dqn_path, weights_only=False, map_location="cpu")
        obs_dim = ckpt["q_network"]["encoder.0.weight"].shape[1]
        network = DQNetwork(input_dim=obs_dim)
        network.load_state_dict(ckpt["q_network"])
        network.eval()
        typer.echo(f"Loaded DQN model: {dqn_path} ({obs_dim}-dim)")
    else:
        typer.echo(f"No model found: tried {gbt_path} and {dqn_path}", err=True)
        raise typer.Exit(1)

    # Load normalizer — match dim to loaded model
    _norm_dim = obs_dim if "obs_dim" in dir() else OBSERVATION_DIM
    normalizer = RunningNormalizer(dim=_norm_dim)
    norm_path = _EPISODES_DIR / "normalizer.json"
    if norm_path.exists():
        normalizer.load(norm_path)

    # Load macro + COT
    macro_path = MACRO_DIR / "macro_daily.parquet"
    cot_path = MACRO_DIR / "cot_weekly.parquet"
    macro_data: dict = {}
    if macro_path.exists():
        macro_df = pd.read_parquet(macro_path)
        cot_df = pd.read_parquet(cot_path) if cot_path.exists() else None
        # Load exchange statistics
        stats_path = MACRO_DIR / "statistics_daily.parquet"
        stats_df = None
        if stats_path.exists():
            try:
                stats_df = pd.read_parquet(stats_path)
                typer.echo(f"Loaded exchange statistics: {len(stats_df)} days.")
            except Exception as exc:
                typer.echo(f"Warning: could not load statistics data: {exc}")
        else:
            typer.echo("No statistics_daily.parquet found — exchange stats features will be zeroed.")
        macro_data = _prepare_macro_data(macro_df, cot_df=cot_df, stats_df=stats_df)

    # Load summaries
    summaries = load_summaries(_DATA_DIR / "session_summaries.json")

    # Use test split months only (last ~17% chronologically)
    # Get all parquet files and take the last few
    parquet_files = sorted(TICKS_DIR.glob("NQ_*.parquet"))
    test_start = int(len(parquet_files) * 0.83)
    test_files = parquet_files[test_start:]

    if not test_files:
        typer.echo("No test files found. Using last 3 files.", err=True)
        test_files = parquet_files[-3:]

    typer.echo(f"Backtesting on {len(test_files)} files: {[f.name for f in test_files]}")

    sm = SessionManager(network, normalizer)
    sm.MIN_Q_SPREAD = min_spread

    engine = ReplayEngine(macro_data=macro_data)
    all_sessions: list[dict] = []
    prior_levels = None

    for pfile in test_files:
        df = pd.read_parquet(pfile)
        if "timestamp" not in df.columns:
            continue

        df["_ts_et"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(_ET)
        df["_session_date"] = df["_ts_et"].apply(_assign_session_date)
        df = df.dropna(subset=["_session_date"])
        df_renamed = df.rename(columns={"timestamp": "ts"})
        dates = sorted(df_renamed["_session_date"].unique())

        for session_date in dates:
            day_df = df_renamed[df_renamed["_session_date"] == session_date].drop(
                columns=["_session_date", "_ts_et"], errors="ignore"
            )
            ticks = day_df.to_dict(orient="records")
            if not ticks:
                continue

            session_dt = datetime(
                session_date.year,
                session_date.month,
                session_date.day,
                12,
                0,
                0,
                tzinfo=_ET,
            )

            precomputed = None
            if summaries:
                precomputed = compute_precomputed_levels(summaries, str(session_date))

            try:
                episodes = engine.replay_session(
                    ticks,
                    session_dt,
                    prior_session_levels=prior_levels,
                    precomputed_levels=precomputed,
                )
            except Exception:
                continue

            prior_levels = engine.get_prior_session_for_chaining()

            # Run SessionManager through the episodes
            sm.reset_session()
            for ep in episodes:
                # Build state from episode's stored state
                state = ep.state if hasattr(ep, "state") else {}
                if not state:
                    continue
                price = float(state.get("price", 0.0))
                if price <= 0:
                    continue

                sm.on_level_touch(state, price)

                # Check stop on each tick between episodes (simplified: use episode prices)
                # In live trading this would be tick-by-tick
                if sm.position.is_open:
                    sm.on_price_update(price)

            # Close at session end
            if sm.position.is_open and ticks:
                last_price = float(ticks[-1].get("price", 0))
                if last_price > 0:
                    sm.on_session_end(last_price)

            summary = sm.get_session_summary()
            summary["date"] = str(session_date)
            all_sessions.append(summary)

    # Print aggregate results
    typer.echo(f"\n{'=' * 60}")
    typer.echo("  SESSION MANAGER BACKTEST REPORT")
    typer.echo(f"{'=' * 60}")

    total_trades = sum(s["trades"] for s in all_sessions)
    total_pnl = sum(s["total_pnl_r"] for s in all_sessions)
    total_winners = sum(s["winners"] for s in all_sessions)
    total_losers = sum(s["losers"] for s in all_sessions)
    total_flips = sum(s["flips"] for s in all_sessions)
    sessions_positive = sum(1 for s in all_sessions if s["total_pnl_r"] > 0)

    wr = total_winners / max(total_trades, 1) * 100
    avg_session_pnl = total_pnl / max(len(all_sessions), 1)

    typer.echo(f"  Sessions         : {len(all_sessions)}")
    typer.echo(f"  Sessions +       : {sessions_positive} ({sessions_positive / max(len(all_sessions), 1) * 100:.0f}%)")
    typer.echo(f"  Total trades     : {total_trades}")
    typer.echo(f"  Winners          : {total_winners}")
    typer.echo(f"  Losers           : {total_losers}")
    typer.echo(f"  Position flips   : {total_flips}")
    typer.echo(f"  Win rate         : {wr:.1f}%")
    typer.echo(f"  Total P&L        : {total_pnl:+.1f} R")
    typer.echo(f"  Avg session P&L  : {avg_session_pnl:+.2f} R")
    typer.echo(f"{'=' * 60}")

    # Top 10 best and worst sessions
    sorted_sessions = sorted(all_sessions, key=lambda s: s["total_pnl_r"], reverse=True)
    typer.echo("\n  BEST SESSIONS:")
    for s in sorted_sessions[:5]:
        typer.echo(f"    {s['date']}  {s['total_pnl_r']:+6.1f}R  trades={s['trades']}  flips={s['flips']}")
    typer.echo("\n  WORST SESSIONS:")
    for s in sorted_sessions[-5:]:
        typer.echo(f"    {s['date']}  {s['total_pnl_r']:+6.1f}R  trades={s['trades']}  flips={s['flips']}")


# ---------------------------------------------------------------------------
# label-setups
# ---------------------------------------------------------------------------


@rl_app.command("label-setups")
def label_setups() -> None:
    """Label all episodes with setup types (rule-based + clustering)."""
    from collections import Counter

    import numpy as np

    from src.rl.config import LevelType
    from src.rl.labeling.setup_labeler import label_episode
    from src.rl.labeling.setup_types import SetupType

    episodes_dir = _EPISODES_DIR

    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy in {episodes_dir}. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    observations = np.load(episodes_dir / "observations.npy")
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    np.load(episodes_dir / "level_types.npy", allow_pickle=True)

    # Optional per-episode metadata for setup labeler (new in post-2026-04-18 replay)
    gap_path = episodes_dir / "overnight_gap.npy"
    te_path = episodes_dir / "touch_epochs.npy"
    overnight_gap = np.load(gap_path) if gap_path.exists() else None
    touch_epochs = np.load(te_path) if te_path.exists() else None
    if overnight_gap is None or touch_epochs is None:
        typer.echo("WARNING: overnight_gap.npy / touch_epochs.npy missing — gap_fill and ib_extension will be starved.")

    from datetime import datetime as _dt
    from datetime import timezone as _tz
    from zoneinfo import ZoneInfo

    _ET_TZ = ZoneInfo("America/New_York")

    n = len(observations)
    typer.echo(f"Loaded {n:,} episodes ({observations.shape[1]}-dim)")

    # Decode zone composition multi-hot (indices 0:31) to zone type name lists
    all_level_types = list(LevelType)  # 31 members, same order as multi-hot
    zone_comp = observations[:, :31]

    labels = np.empty(n, dtype=object)
    for i in range(n):
        # Decode zone types from multi-hot composition vector
        active_mask = zone_comp[i] > 0.5
        zone_types = [all_level_types[j].value for j in range(31) if active_mask[j]]

        # Approach direction: index 268 (1.0=up, -1.0=down)
        approach_dir = "up" if observations[i, 268] >= 0 else "down"

        # Approximate forward_reversal_speed: |reward_rev| * 5 when reversal is better
        rev_better = rewards_rev[i] > rewards_cont[i]
        fwd_rev_speed = abs(float(rewards_rev[i])) * 5.0 if rev_better else 0.0

        # Single print: check if zone_conf single_print_overlap (index 177) is active
        has_sp = bool(observations[i, 177] > 0.5)

        # Gap flag: overnight_gap is normalized by IB range; |gap| > 0.2 is a real gap.
        gap_val = float(overnight_gap[i]) if overnight_gap is not None else 0.0
        has_gap = abs(gap_val) > 0.2
        # Touch time in ET — needed for ib_extension + gap_fill time gates.
        touch_time_et = None
        if touch_epochs is not None and touch_epochs[i] > 0:
            touch_time_et = _dt.fromtimestamp(float(touch_epochs[i]), tz=_tz.utc).astimezone(_ET_TZ)

        ep_dict = {
            "zone_types": zone_types,
            "approach_direction": approach_dir,
            "reward_cont": float(rewards_cont[i]),
            "reward_rev": float(rewards_rev[i]),
            "has_single_print": has_sp,
            "forward_reversal_speed": fwd_rev_speed,
            "price_vs_value": float(observations[i, 52]),  # struct_0: price_vs_vwap
            "has_gap": has_gap,
            "ib_closed": bool(observations[i, 57] > 0),  # struct_5: IB distance > 0
            "delta_ratio": float(observations[i, 31]),  # orderflow index 0
            "touch_time_et": touch_time_et,
        }

        labels[i] = label_episode(ep_dict).value

    # Print distribution
    counts = Counter(labels)
    typer.echo("\n  Setup Label Distribution:")
    for setup_type in SetupType:
        c = counts.get(setup_type.value, 0)
        pct = c / n * 100 if n > 0 else 0
        flag = " *" if setup_type == SetupType.UNKNOWN else ""
        typer.echo(f"    {setup_type.value:30s} {c:>7,}  ({pct:5.1f}%){flag}")

    # Cluster unknowns if there are enough
    unknown_count = counts.get(SetupType.UNKNOWN.value, 0)
    if unknown_count > 1000:
        typer.echo(f"\n  Clustering {unknown_count:,} unknown episodes...")
        from src.rl.labeling.setup_clusterer import cluster_and_label

        unknown_mask = np.array([lb == SetupType.UNKNOWN.value for lb in labels])
        unknown_idx = np.where(unknown_mask)[0]

        # Structure + TPO portion of observations (indices 52:154)
        unknown_obs = observations[unknown_idx, 52:154]
        unknown_zone_types = [
            [all_level_types[j].value for j in range(31) if zone_comp[idx][j] > 0.5] for idx in unknown_idx
        ]
        unknown_rc = rewards_cont[unknown_idx]
        unknown_rr = rewards_rev[unknown_idx]
        # price_vs_value from struct_0 (index 52)
        unknown_pvv = observations[unknown_idx, 52]
        # balance_width from AMT dynamics index 15 → observation index 228+15=243
        unknown_bw = observations[unknown_idx, 243]

        cluster_labels = cluster_and_label(
            observations=unknown_obs,
            zone_types_list=unknown_zone_types,
            rewards_cont=unknown_rc,
            rewards_rev=unknown_rr,
            price_vs_value=unknown_pvv,
            balance_widths=unknown_bw,
            min_cluster_size=200,
        )

        # Merge cluster labels back
        for i, idx in enumerate(unknown_idx):
            labels[idx] = cluster_labels[i]

        # Print updated distribution
        counts = Counter(labels)
        typer.echo("\n  Updated Distribution (after clustering):")
        for setup_type in SetupType:
            c = counts.get(setup_type.value, 0)
            pct = c / n * 100 if n > 0 else 0
            typer.echo(f"    {setup_type.value:30s} {c:>7,}  ({pct:5.1f}%)")

    # Save
    out_path = episodes_dir / "setup_labels.npy"
    np.save(out_path, labels)
    typer.echo(f"\n  Saved setup labels to {out_path}")


# ---------------------------------------------------------------------------
# train-trigger-gbt
# ---------------------------------------------------------------------------


@rl_app.command("train-trigger-gbt")
def train_trigger_gbt(
    checkpoint: str = typer.Option("v5", help="Checkpoint name"),
    trees: int = typer.Option(1000, help="Number of trees"),
    depth: int = typer.Option(6, help="Max depth"),
    lr: float = typer.Option(0.05, help="Learning rate"),
) -> None:
    """Train the Trigger GBT on trigger-layer features -> direction/reward forecast."""
    import numpy as np

    from src.rl.agent.trigger_gbt import TriggerGBT

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy in {episodes_dir}. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    observations = np.load(episodes_dir / "observations.npy")
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    stop_path = episodes_dir / "stop_targets.npy"
    stop_targets = np.load(stop_path) if stop_path.exists() else np.full(len(observations), 10.0, dtype=np.float32)
    be_path = episodes_dir / "breakeven_reached.npy"
    breakeven_reached = np.load(be_path) if be_path.exists() else None
    lc_path = episodes_dir / "levels_captured.npy"
    levels_captured = np.load(lc_path) if lc_path.exists() else None

    n = len(observations)
    # Auto-fix size mismatches from interrupted replays
    for _name, arr in [("stop_targets", stop_targets)]:
        if len(arr) != n:
            padded = np.full(n, 10.0, dtype=np.float32)
            padded[: len(arr)] = arr
            stop_targets = padded
    if breakeven_reached is not None and len(breakeven_reached) != n:
        padded = np.zeros(n, dtype=breakeven_reached.dtype)
        padded[: len(breakeven_reached)] = breakeven_reached
        breakeven_reached = padded
    if levels_captured is not None and len(levels_captured) != n:
        padded = np.zeros(n, dtype=levels_captured.dtype)
        padded[: len(levels_captured)] = levels_captured
        levels_captured = padded

    typer.echo(f"Loaded {n:,} episodes ({observations.shape[1]}-dim)")

    # --- Load trigger observations (118-dim, built during replay) ---
    trig_path = episodes_dir / "trigger_observations.npy"
    if not trig_path.exists():
        typer.echo(
            f"No trigger_observations.npy in {episodes_dir}.\n"
            "Run 'rl replay --all --clean' to regenerate episodes with proper trigger features.",
            err=True,
        )
        raise typer.Exit(1)
    X = np.load(trig_path)
    if len(X) != n:
        typer.echo(
            f"trigger_observations.npy has {len(X)} rows but observations.npy has {n}.\n"
            "Run 'rl replay --all --clean' to regenerate.",
            err=True,
        )
        raise typer.Exit(1)

    # Subsample to fit in memory (LightGBM duplicates data per thread)
    MAX_GBT_SAMPLES = 250_000
    if n > MAX_GBT_SAMPLES:
        rng = np.random.RandomState(42)
        idx = rng.choice(n, MAX_GBT_SAMPLES, replace=False)
        idx.sort()  # preserve chronological order
        observations = observations[idx]
        rewards_cont = rewards_cont[idx]
        rewards_rev = rewards_rev[idx]
        stop_targets = stop_targets[idx]
        X = X[idx]
        if breakeven_reached is not None:
            breakeven_reached = breakeven_reached[idx]
        if levels_captured is not None:
            levels_captured = levels_captured[idx]
        n = MAX_GBT_SAMPLES
        typer.echo(f"Subsampled to {n:,} episodes for memory safety.")
    typer.echo(f"Trigger features: {X.shape[1]} dims (loaded from trigger_observations.npy)")

    # --- Labels ---
    y_direction = (rewards_cont > rewards_rev).astype(np.int32)
    reward_gap = np.abs(rewards_cont - rewards_rev)

    cont_pct = y_direction.mean() * 100
    typer.echo(f"Direction split: {cont_pct:.1f}% continuation, {100 - cont_pct:.1f}% reversal")

    # --- Train ---
    model = TriggerGBT()
    typer.echo(f"\nTraining TriggerGBT (engine={model.engine}, trees={trees}, depth={depth}, lr={lr})...")
    metrics = model.train(
        X=X,
        y_direction=y_direction,
        rewards_cont=rewards_cont,
        rewards_rev=rewards_rev,
        stop_targets=stop_targets,
        breakeven_reached=breakeven_reached,
        levels_captured=levels_captured,
        reward_gap=reward_gap,
        n_estimators=trees,
        max_depth=depth,
        learning_rate=lr,
    )

    # Print metrics
    typer.echo("\n  Results:")
    typer.echo(f"    Engine           : {metrics['engine']}")
    typer.echo(f"    Alive features   : {metrics['alive_features']} / {metrics['total_features']}")
    typer.echo(f"    Direction acc    : {metrics['direction_accuracy']}%")
    if "breakeven_accuracy" in metrics:
        typer.echo(f"    Breakeven acc    : {metrics['breakeven_accuracy']}%")

    # Feature importance
    top_features = model.feature_importance(top_n=10)
    typer.echo("\n  Top 10 feature importances (direction head):")
    for idx, imp in top_features:
        typer.echo(f"    feature[{idx:3d}] = {imp:.4f}")

    # Save
    save_path = models_dir / f"trigger_gbt_{checkpoint}.joblib"
    model.save(save_path)
    typer.echo(f"\n  Saved to {save_path}")


# ---------------------------------------------------------------------------
# Shared helpers for Phase 3c risk heads — action-conditioned reward labels
# ---------------------------------------------------------------------------


def _build_augmented_obs(observations, episodes_dir):
    """Return (X, trigger_obs_or_None) where X is the 324-dim DQN input.

    Augmented layout matches AUGMENTED_SCHEMA:
        base (302) + gbt_forecast (8) + position_state (8) + session_memory (6)

    Falls back to lower-dim combinations when the required files are missing
    — callers/heads will detect alive_mask sizes at training time.
    """
    import numpy as _np

    trigger_path = episodes_dir / "trigger_observations.npy"
    if trigger_path.exists():
        trigger_obs = _np.load(trigger_path)
        if len(trigger_obs) == len(observations):
            from src.rl.features.trigger_features import EXEC_PASSTHROUGH_DIM, TRIGGER_DIM, TRIGGER_GBT_DIM

            _gs = TRIGGER_DIM - EXEC_PASSTHROUGH_DIM - TRIGGER_GBT_DIM
            gbt_forecast = trigger_obs[:, _gs : _gs + TRIGGER_GBT_DIM]
            position_state = _np.zeros((len(observations), 8), dtype=_np.float32)
            # Session memory: use the chronological simulator if touch_epochs
            # + rewards exist, otherwise fall back to zeros.
            # Action-conditioned when a TriggerGBT is available (mirrors H4),
            # so the simulator produces realistic loss streaks the heads can
            # learn to recognise. Falls back to greedy-best otherwise.
            te_path = episodes_dir / "touch_epochs.npy"
            rc_path = episodes_dir / "rewards_cont.npy"
            rr_path = episodes_dir / "rewards_rev.npy"
            if te_path.exists() and rc_path.exists() and rr_path.exists():
                from src.rl.features.session_memory_features import simulate_session_memory

                te = _np.load(te_path)
                rc = _np.load(rc_path)
                rr = _np.load(rr_path)
                m = min(len(observations), len(te), len(rc), len(rr))

                # Derive actions from TriggerGBT when available for realistic
                # session state.
                sim_actions = None
                try:
                    from src.rl.agent.trigger_gbt import TriggerGBT

                    tgbt_path = _MODELS_DIR / "trigger_gbt_v5.joblib"
                    if tgbt_path.exists() and trigger_obs is not None:
                        gbt = TriggerGBT.load(tgbt_path)
                        sim_actions, _c, _p = gbt.predict_direction_batch(trigger_obs[:m].astype(_np.float32))
                except Exception:
                    sim_actions = None

                session_memory = simulate_session_memory(
                    touch_epochs=te[:m],
                    rewards_cont=rc[:m],
                    rewards_rev=rr[:m],
                    actions=sim_actions,
                )
                if m < len(observations):
                    pad = _np.zeros((len(observations) - m, session_memory.shape[1]), dtype=_np.float32)
                    session_memory = _np.concatenate([session_memory, pad], axis=0)
            else:
                session_memory = _np.zeros((len(observations), 6), dtype=_np.float32)
            X = _np.concatenate([observations, gbt_forecast, position_state, session_memory], axis=1).astype(
                _np.float32
            )
            return X, trigger_obs
    return observations.astype(_np.float32), None


def _compute_confluence_weights(
    observations,
    *,
    min_weight: float = 1.0,
    max_weight: float = 3.0,
) -> np.ndarray:
    """Per-episode sample weight based on zone member count (H8).

    Reads obs[0:31] (level_composition multi-hot), counts members, maps to
    an upweight factor in [min_weight, max_weight]. Formula:
        w = min_weight + (members - 1) * 0.5, clamped to [min, max]

    Concretely: 1m→1.0, 2m→1.5, 3m→2.0, 4m→2.5, 5m→3.0, anything beyond 5m→3.0.
    Designed so multi-member zones (trades worth ≥+1R/trade per I1 analysis)
    get enough weight that the 74%-dominant 1m-zone volume doesn't dictate
    the fit.
    """
    import numpy as _np

    comp = (_np.asarray(observations[:, 0:31]) > 0.5).astype(_np.int8)
    members = comp.sum(axis=1).astype(_np.float32)
    w = min_weight + (members - 1.0) * 0.5
    return _np.clip(w, min_weight, max_weight).astype(_np.float32)


def _compute_action_conditioned_R(
    trigger_obs,
    rewards_cont,
    rewards_rev,
    models_dir,
):
    """Return (R, source_tag).

    H4 fix: use the reward of the action the TriggerGBT WOULD pick, not the
    post-hoc max(cont, rev). That's the realistic label — the policy can't
    see which side will win.

    Falls back to max(cont, rev) if trigger_gbt_v5.joblib isn't on disk yet
    (e.g. first pipeline run where step 4 hasn't produced it or standalone
    H4 backfill on an older checkpoint).
    """
    import numpy as _np

    tgbt_path = models_dir / "trigger_gbt_v5.joblib"
    if trigger_obs is None or not tgbt_path.exists():
        return _np.maximum(rewards_cont, rewards_rev).astype(_np.float32), "max(cont,rev) fallback"

    try:
        from src.rl.agent.trigger_gbt import TriggerGBT

        gbt = TriggerGBT.load(tgbt_path)
        # predict_direction_batch returns (actions, confidences, probs).
        # action=0 → continuation, action=1 → reversal.
        actions, _conf, _probs = gbt.predict_direction_batch(trigger_obs.astype(_np.float32))
        R = _np.where(actions == 0, rewards_cont, rewards_rev).astype(_np.float32)
        cont_frac = float((actions == 0).mean())
        return R, f"action-conditioned (TriggerGBT: {cont_frac:.1%} CONT / {1 - cont_frac:.1%} REV)"
    except Exception as exc:
        log = logging.getLogger(__name__)
        log.warning("action-conditioned R fallback due to: %s", exc)
        return _np.maximum(rewards_cont, rewards_rev).astype(_np.float32), f"max fallback ({exc})"


# ---------------------------------------------------------------------------
# train-size-model (Phase 3c: trained position-sizing head)
# ---------------------------------------------------------------------------


@rl_app.command("train-size-model")
def train_size_model(
    checkpoint: str = typer.Option("v5", help="Checkpoint name"),
    trees: int = typer.Option(400, help="Number of trees"),
    depth: int = typer.Option(4, help="Max depth"),
    lr: float = typer.Option(0.05, help="Learning rate"),
) -> None:
    """Train the SizeModel — 5-class LGBM classifier predicting position size tier.

    Trains on the augmented observation (base + GBT forecast + position state)
    that the DQN consumes. Labels are derived from realized R by bucketing
    into {0.0, 0.3, 0.6, 1.0, 1.5}x size tiers.
    """
    import numpy as np

    from src.rl.agent.size_model import SizeModel

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy in {episodes_dir}. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    observations = np.load(obs_path)
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")

    X, trigger_obs = _build_augmented_obs(observations, episodes_dir)

    # H4: action-conditioned realized R — the reward on the side the
    # TriggerGBT WOULD pick, not the post-hoc best. Falls back to max()
    # if no trigger_gbt checkpoint is available yet (first-ever run).
    realized_R, label_source = _compute_action_conditioned_R(
        trigger_obs=trigger_obs,
        rewards_cont=rewards_cont,
        rewards_rev=rewards_rev,
        models_dir=models_dir,
    )
    typer.echo(f"Label source: {label_source}")

    # H8: confluence-weighted per-sample training weights — multi-member
    # zones are ~10% of episodes but produce ~+1R/trade vs single-member's
    # noisier +0.93. Upweight them so they pull their weight in the fit.
    confluence_w = _compute_confluence_weights(observations)
    typer.echo(
        f"Confluence weights: mean={confluence_w.mean():.2f}, min={confluence_w.min():.2f}, max={confluence_w.max():.2f}"
    )

    n = len(X)
    typer.echo(f"Loaded {n:,} episodes ({X.shape[1]}-dim)")
    typer.echo(f"Realized R: mean={realized_R.mean():+.3f}, median={np.median(realized_R):+.3f}")

    MAX_SAMPLES = 300_000
    if n > MAX_SAMPLES:
        rng = np.random.RandomState(42)
        idx = rng.choice(n, MAX_SAMPLES, replace=False)
        idx.sort()
        X = X[idx]
        realized_R = realized_R[idx]
        confluence_w = confluence_w[idx]
        n = MAX_SAMPLES
        typer.echo(f"Subsampled to {n:,} for memory safety.")

    # ONLINE1: warm-start from prior _latest.joblib if it exists. Kills random-init
    # variance — each retrain refines the prior model instead of starting fresh.
    init_path = models_dir / "size_model_latest.joblib"
    if not init_path.exists():
        init_path = None

    model = SizeModel()
    typer.echo(f"\nTraining SizeModel (engine={model.engine}, trees={trees}, depth={depth}, lr={lr})...")
    metrics = model.train(
        X=X,
        realized_R=realized_R,
        n_estimators=trees,
        max_depth=depth,
        learning_rate=lr,
        init_model_path=init_path,
        confluence_weights=confluence_w,
    )

    typer.echo("\n  Results:")
    typer.echo(f"    Engine           : {metrics['engine']}")
    typer.echo(f"    Alive features   : {metrics['alive_features']} / {metrics['total_features']}")
    typer.echo(f"    Warm-start       : {metrics.get('warm_start', False)}  ({init_path})")
    typer.echo(f"    Train accuracy   : {metrics['train_accuracy']}%")
    typer.echo(f"    Val accuracy     : {metrics['val_accuracy']}%")
    typer.echo(f"    Class distrib    : {metrics['class_distribution']}")
    typer.echo(f"    Val mean mult    : {metrics['val_mean_size_multiplier']}")
    typer.echo(f"    Val weighted R   : {metrics['val_mean_weighted_R']}")

    top_features = model.feature_importance(top_n=10)
    typer.echo("\n  Top 10 feature importances:")
    for idx, imp in top_features:
        typer.echo(f"    feature[{idx:3d}] = {imp:.4f}")

    save_path = models_dir / f"size_model_{checkpoint}.joblib"
    model.save(save_path)
    typer.echo(f"\n  Saved to {save_path}")


# ---------------------------------------------------------------------------
# train-early-exit-model (Phase 3c: pump-and-retrace detector)
# ---------------------------------------------------------------------------


@rl_app.command("train-early-exit-model")
def train_early_exit_model(
    checkpoint: str = typer.Option("v5", help="Checkpoint name"),
    trees: int = typer.Option(400, help="Number of trees"),
    depth: int = typer.Option(4, help="Max depth"),
    lr: float = typer.Option(0.05, help="Learning rate"),
) -> None:
    """Train the EarlyExitModel — predicts P(trade pumps then retraces).

    Requires peak_R_cont.npy + peak_R_rev.npy (written during replay in
    Phase 3c). Labels each episode 1 if peak_R ≥ 0.5 AND realized_R < 0.5.
    """
    import numpy as np

    from src.rl.agent.early_exit_model import EarlyExitModel

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy in {episodes_dir}. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    peakc_path = episodes_dir / "peak_R_cont.npy"
    peakr_path = episodes_dir / "peak_R_rev.npy"
    if not (peakc_path.exists() and peakr_path.exists()):
        typer.echo(
            f"Missing peak_R_cont.npy / peak_R_rev.npy in {episodes_dir}. "
            "These are written during replay in Phase 3c — re-replay first.",
            err=True,
        )
        raise typer.Exit(1)

    observations = np.load(obs_path)
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    peak_R_cont = np.load(peakc_path)
    peak_R_rev = np.load(peakr_path)

    X, trigger_obs = _build_augmented_obs(observations, episodes_dir)

    # H4: action-conditioned peak + realized — use the side the TriggerGBT
    # actually picks, not max(cont, rev). That's the realistic pump-and-
    # retrace outcome given the policy's direction choice, so training
    # teaches the EarlyExit head to flag trades on the REAL side taken.
    try:
        from src.rl.agent.trigger_gbt import TriggerGBT as _TGBT

        tgbt_path = models_dir / "trigger_gbt_v5.joblib"
        if trigger_obs is not None and tgbt_path.exists():
            gbt = _TGBT.load(tgbt_path)
            actions, _conf, _probs = gbt.predict_direction_batch(trigger_obs.astype(np.float32))
            peak_R = np.where(actions == 0, peak_R_cont, peak_R_rev).astype(np.float32)
            realized_R = np.where(actions == 0, rewards_cont, rewards_rev).astype(np.float32)
            label_source = f"action-conditioned ({(actions == 0).mean():.1%} CONT / {(actions == 1).mean():.1%} REV)"
        else:
            raise RuntimeError("trigger_gbt not available")
    except Exception as exc:
        peak_R = np.maximum(peak_R_cont, peak_R_rev).astype(np.float32)
        realized_R = np.maximum(rewards_cont, rewards_rev).astype(np.float32)
        label_source = f"max(cont,rev) fallback ({exc})"

    typer.echo(f"Label source: {label_source}")

    # H8: confluence sample weights
    confluence_w = _compute_confluence_weights(observations)
    typer.echo(
        f"Confluence weights: mean={confluence_w.mean():.2f}, min={confluence_w.min():.2f}, max={confluence_w.max():.2f}"
    )

    n = len(X)
    typer.echo(f"Loaded {n:,} episodes ({X.shape[1]}-dim)")
    typer.echo(f"peak_R: mean={peak_R.mean():+.3f}, median={np.median(peak_R):+.3f}")
    typer.echo(f"realized_R: mean={realized_R.mean():+.3f}, median={np.median(realized_R):+.3f}")

    MAX_SAMPLES = 300_000
    if n > MAX_SAMPLES:
        rng = np.random.RandomState(42)
        idx = rng.choice(n, MAX_SAMPLES, replace=False)
        idx.sort()
        X = X[idx]
        peak_R = peak_R[idx]
        realized_R = realized_R[idx]
        confluence_w = confluence_w[idx]
        n = MAX_SAMPLES
        typer.echo(f"Subsampled to {n:,} for memory safety.")

    # ONLINE1: warm-start from prior _latest.joblib if it exists.
    init_path = models_dir / "early_exit_model_latest.joblib"
    if not init_path.exists():
        init_path = None

    model = EarlyExitModel()
    typer.echo(f"\nTraining EarlyExitModel (engine={model.engine}, trees={trees}, depth={depth}, lr={lr})...")
    metrics = model.train(
        X=X,
        peak_R=peak_R,
        realized_R=realized_R,
        n_estimators=trees,
        max_depth=depth,
        learning_rate=lr,
        init_model_path=init_path,
        confluence_weights=confluence_w,
    )

    typer.echo("\n  Results:")
    typer.echo(f"    Engine           : {metrics['engine']}")
    typer.echo(f"    Alive features   : {metrics['alive_features']} / {metrics['total_features']}")
    typer.echo(f"    Warm-start       : {metrics.get('warm_start', False)}  ({init_path})")
    typer.echo(f"    Train positives  : {metrics['train_positive_pct']}%")
    typer.echo(f"    Val positives    : {metrics['val_positive_pct']}%")
    typer.echo(f"    Val AUC          : {metrics['val_auc']}")
    typer.echo(
        f"    P/R @ 0.3        : {metrics['val_precision@0.3']} / {metrics['val_recall@0.3']}  (flagged={metrics['val_flagged@0.3']})"
    )
    typer.echo(
        f"    P/R @ 0.5        : {metrics['val_precision@0.5']} / {metrics['val_recall@0.5']}  (flagged={metrics['val_flagged@0.5']})"
    )
    typer.echo(
        f"    P/R @ 0.7        : {metrics['val_precision@0.7']} / {metrics['val_recall@0.7']}  (flagged={metrics['val_flagged@0.7']})"
    )

    top_features = model.feature_importance(top_n=10)
    typer.echo("\n  Top 10 feature importances:")
    for idx, imp in top_features:
        typer.echo(f"    feature[{idx:3d}] = {imp:.4f}")

    save_path = models_dir / f"early_exit_model_{checkpoint}.joblib"
    model.save(save_path)
    typer.echo(f"\n  Saved to {save_path}")


# ---------------------------------------------------------------------------
# analyze-dim-correlation — per-dim R-correlation scan
# ---------------------------------------------------------------------------


@rl_app.command("analyze-dim-correlation")
def analyze_dim_correlation(
    top_n: int = typer.Option(25, help="Top N positive/negative dims to report"),
    output_csv: str | None = typer.Option(None, help="Optional CSV output path"),
) -> None:
    """Compute per-dim Pearson correlation with realized R.

    Uses the 318-dim augmented observation (base + GBT forecast + position
    state) and matches it against max(rewards_cont, rewards_rev). Cross-checks
    top dims with feature_importance from a saved SizeModel to see whether
    the model is actually using the dims that correlate with R.

    Read-only. Takes ~30s for 524k episodes.
    """

    import numpy as np

    from src.rl.features.feature_names import pretty_augmented

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR

    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy in {episodes_dir}. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    typer.echo("Loading observations + rewards...")
    observations = np.load(obs_path, mmap_mode="r")
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")

    trigger_path = episodes_dir / "trigger_observations.npy"
    if trigger_path.exists() and len(np.load(trigger_path, mmap_mode="r")) == len(observations):
        from src.rl.features.trigger_features import EXEC_PASSTHROUGH_DIM, TRIGGER_DIM, TRIGGER_GBT_DIM

        trigger_obs = np.load(trigger_path, mmap_mode="r")
        _gs = TRIGGER_DIM - EXEC_PASSTHROUGH_DIM - TRIGGER_GBT_DIM
        gbt_forecast = np.array(trigger_obs[:, _gs : _gs + TRIGGER_GBT_DIM])
        position_state = np.zeros((len(observations), 8), dtype=np.float32)
        # Session memory: chronological simulation when touch_epochs exist
        te_path = episodes_dir / "touch_epochs.npy"
        if te_path.exists():
            from src.rl.features.session_memory_features import simulate_session_memory

            te = np.load(te_path)
            m = min(len(observations), len(te), len(rewards_cont), len(rewards_rev))
            session_memory = simulate_session_memory(
                touch_epochs=te[:m],
                rewards_cont=rewards_cont[:m],
                rewards_rev=rewards_rev[:m],
            )
            if m < len(observations):
                pad = np.zeros((len(observations) - m, session_memory.shape[1]), dtype=np.float32)
                session_memory = np.concatenate([session_memory, pad], axis=0)
        else:
            session_memory = np.zeros((len(observations), 6), dtype=np.float32)
        X = np.concatenate([np.array(observations), gbt_forecast, position_state, session_memory], axis=1).astype(
            np.float32
        )
        typer.echo(f"Built augmented obs: {X.shape[1]}-dim (base + GBT + position_state + session_memory)")
    else:
        X = np.array(observations).astype(np.float32)
        typer.echo(f"Using base obs: {X.shape[1]}-dim (no trigger_observations.npy)")

    realized_R = np.maximum(rewards_cont, rewards_rev).astype(np.float32)
    typer.echo(f"Aligned {len(X):,} episodes vs realized R (mean={realized_R.mean():+.3f}, std={realized_R.std():.3f})")

    # Vectorized Pearson correlation via np.corrcoef on subsample for memory
    rng = np.random.default_rng(42)
    sample_n = min(len(X), 250_000)
    idx = rng.choice(len(X), sample_n, replace=False) if len(X) > sample_n else slice(None)
    Xs = X[idx]
    Rs = realized_R[idx]

    # Standardize
    x_mean = Xs.mean(axis=0)
    x_std = Xs.std(axis=0) + 1e-10
    r_mean = Rs.mean()
    r_std = Rs.std() + 1e-10

    # Pearson correlation per dim (vectorized)
    corr = ((Xs - x_mean) * (Rs - r_mean)[:, None]).mean(axis=0) / (x_std * r_std)
    # Dead features have zero variance; mask
    alive = Xs.std(axis=0) > 1e-8
    corr_masked = np.where(alive, corr, 0.0)

    # Rank
    order_pos = np.argsort(-corr_masked)
    order_neg = np.argsort(corr_masked)

    typer.echo(f"\n  Alive dims: {int(alive.sum())} / {X.shape[1]}")
    typer.echo(
        f"  |corr| distribution: median={np.median(np.abs(corr_masked[alive])):.4f}, max={np.max(np.abs(corr_masked[alive])):.4f}"
    )

    typer.echo(f"\n  Top {top_n} POSITIVE correlations with realized R:")
    for rank in range(min(top_n, X.shape[1])):
        i = int(order_pos[rank])
        typer.echo(f"    [{i:>3d}]  corr={corr_masked[i]:+.4f}  {pretty_augmented(i)}")

    typer.echo(f"\n  Top {top_n} NEGATIVE correlations with realized R:")
    for rank in range(min(top_n, X.shape[1])):
        i = int(order_neg[rank])
        typer.echo(f"    [{i:>3d}]  corr={corr_masked[i]:+.4f}  {pretty_augmented(i)}")

    # Cross-check with SizeModel importance if available
    size_path = models_dir / "size_model_latest.joblib"
    if size_path.exists():
        from src.rl.agent.size_model import SizeModel

        m = SizeModel.load(size_path)
        imps = m.feature_importance(top_n=top_n)
        typer.echo(f"\n  SizeModel top-{top_n} feature importances vs corr rank:")
        for rank_i, (idx_abs, imp_val) in enumerate(imps):
            corr_val = float(corr_masked[idx_abs])
            int(np.argsort(-np.abs(corr_masked))[idx_abs]) if idx_abs < len(corr_masked) else -1
            typer.echo(
                f"    #{rank_i + 1:>2d}  imp={imp_val:>8.1f}  corr={corr_val:+.4f}  "
                f"[{idx_abs:>3d}] {pretty_augmented(idx_abs)}"
            )

    if output_csv:
        from pathlib import Path as _P

        csv_path = _P(output_csv)
        with csv_path.open("w") as f:
            f.write("idx,corr_with_R,alive,segment_feature\n")
            for i in range(X.shape[1]):
                f.write(f"{i},{corr_masked[i]:.6f},{int(alive[i])},{pretty_augmented(i)}\n")
        typer.echo(f"\n  Wrote full table to {csv_path}")


# ---------------------------------------------------------------------------
# analyze-combinations — empirical level-type combination table (I1)
# ---------------------------------------------------------------------------


@rl_app.command("analyze-combinations")
def analyze_combinations(
    top_n: int = typer.Option(30, help="Top N combinations to report by avg R"),
    min_samples: int = typer.Option(50, help="Min sample count for a combination to report"),
    output_csv: str | None = typer.Option(None, help="Optional CSV output path"),
) -> None:
    """Mine the empirical (level composition) → realized R table.

    Reads obs[0:31] (level_composition multi-hot) + rewards and buckets every
    episode by its exact composition pattern + member count. Produces:
    - per-combination stats (count, mean_R, win%, peak_R)
    - per-single-level stats (marginal effect of each level type)
    - confluence sensitivity (how R scales with member count)

    Read-only, no retrain.
    """
    import numpy as np

    from src.rl.features.feature_names import level_composition_names

    episodes_dir = _EPISODES_DIR
    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy in {episodes_dir}. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    typer.echo("Loading composition + rewards...")
    observations = np.load(obs_path, mmap_mode="r")
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    np.load(episodes_dir / "level_types.npy", allow_pickle=True)
    peakc_path = episodes_dir / "peak_R_cont.npy"
    peakr_path = episodes_dir / "peak_R_rev.npy"
    peak_cont = np.load(peakc_path) if peakc_path.exists() else None
    peak_rev = np.load(peakr_path) if peakr_path.exists() else None

    comp = np.array(observations[:, 0:31]).astype(np.int8)
    comp = (comp > 0.5).astype(np.int8)  # binarize to multi-hot
    realized_R = np.maximum(rewards_cont, rewards_rev).astype(np.float32)
    peak_R = (
        np.maximum(peak_cont, peak_rev).astype(np.float32) if (peak_cont is not None and peak_rev is not None) else None
    )
    member_count = comp.sum(axis=1)

    n = len(comp)
    typer.echo(f"Loaded {n:,} episodes")
    typer.echo(f"Member-count distribution: {dict(zip(*np.unique(member_count, return_counts=True), strict=False))}")

    names = level_composition_names()
    # ------------------------------------------------------------------
    # 1. Per-single-level MARGINAL stats (each level type separately)
    # ------------------------------------------------------------------
    typer.echo(f"\n  PER-LEVEL-TYPE MARGINAL (episode count ≥ {min_samples}):")
    typer.echo(f"    {'level':<24s}  {'present_n':>9s}  {'mean_R':>8s}  {'win%':>6s}  {'peak_R':>8s}")
    typer.echo(f"    {'─' * 24}  {'─' * 9}  {'─' * 8}  {'─' * 6}  {'─' * 8}")

    marg_stats = []
    for i, name in enumerate(names):
        mask = comp[:, i] == 1
        count = int(mask.sum())
        if count < min_samples:
            continue
        mean_r = float(realized_R[mask].mean())
        win_r = float((realized_R[mask] > 0).mean() * 100)
        peak_mean = float(peak_R[mask].mean()) if peak_R is not None else 0.0
        marg_stats.append((name, count, mean_r, win_r, peak_mean))

    # sort by mean_R desc
    marg_stats.sort(key=lambda r: -r[2])
    for name, count, mean_r, win_r, peak_mean in marg_stats:
        typer.echo(f"    {name:<24s}  {count:>9,d}  {mean_r:>+8.3f}  {win_r:>5.1f}%  {peak_mean:>+8.3f}")

    # ------------------------------------------------------------------
    # 2. Per-member-count confluence stats (1m / 2m / ...)
    # ------------------------------------------------------------------
    typer.echo("\n  CONFLUENCE-COUNT STATS:")
    typer.echo(f"    {'members':>8s}  {'count':>8s}  {'mean_R':>8s}  {'win%':>6s}  {'peak_R':>8s}")
    typer.echo(f"    {'─' * 8}  {'─' * 8}  {'─' * 8}  {'─' * 6}  {'─' * 8}")
    for k in sorted(np.unique(member_count)):
        mask = member_count == k
        c = int(mask.sum())
        if c < min_samples:
            continue
        mean_r = float(realized_R[mask].mean())
        win_r = float((realized_R[mask] > 0).mean() * 100)
        peak_mean = float(peak_R[mask].mean()) if peak_R is not None else 0.0
        typer.echo(f"    {k:>8d}  {c:>8,d}  {mean_r:>+8.3f}  {win_r:>5.1f}%  {peak_mean:>+8.3f}")

    # ------------------------------------------------------------------
    # 3. Top EXACT combinations (unique composition signature)
    # ------------------------------------------------------------------
    # Pack the multi-hot into int64 signatures for fast grouping
    # 31 dims → fits in int64 trivially
    sig = np.zeros(n, dtype=np.int64)
    for i in range(31):
        sig += comp[:, i].astype(np.int64) << i

    unique_sigs, inv, counts = np.unique(sig, return_inverse=True, return_counts=True)
    typer.echo(f"\n  Unique exact combinations: {len(unique_sigs):,}")

    # Aggregate per combination
    sums_r = np.zeros(len(unique_sigs), dtype=np.float64)
    wins = np.zeros(len(unique_sigs), dtype=np.int64)
    sums_peak = np.zeros(len(unique_sigs), dtype=np.float64)
    np.add.at(sums_r, inv, realized_R)
    np.add.at(wins, inv, (realized_R > 0).astype(np.int64))
    if peak_R is not None:
        np.add.at(sums_peak, inv, peak_R)

    mean_r = sums_r / counts
    win_r = 100.0 * wins / counts
    peak_mean = sums_peak / counts if peak_R is not None else np.zeros_like(counts, dtype=float)

    # Filter by min_samples, sort by mean_R
    ok = counts >= min_samples
    rows = [(unique_sigs[i], counts[i], mean_r[i], win_r[i], peak_mean[i]) for i in np.where(ok)[0]]
    rows.sort(key=lambda r: -r[2])

    def _decode(sig_val: int) -> str:
        members = [names[i] for i in range(31) if (sig_val >> i) & 1]
        if not members:
            return "(empty)"
        return "+".join(members)

    typer.echo(f"\n  TOP {top_n} COMBINATIONS (n ≥ {min_samples}):")
    typer.echo(f"    {'count':>7s}  {'mbrs':>4s}  {'mean_R':>8s}  {'win%':>6s}  {'peak_R':>8s}  composition")
    typer.echo(f"    {'─' * 7}  {'─' * 4}  {'─' * 8}  {'─' * 6}  {'─' * 8}  {'─' * 40}")
    for i, (sig_val, c, m_r, w_r, p_m) in enumerate(rows[:top_n]):
        n_members = bin(int(sig_val)).count("1")
        comp_str = _decode(int(sig_val))
        typer.echo(
            f"    {int(c):>7,d}  {n_members:>4d}  {float(m_r):>+8.3f}  {float(w_r):>5.1f}%  {float(p_m):>+8.3f}  {comp_str}"
        )

    typer.echo(f"\n  BOTTOM 10 COMBINATIONS (n ≥ {min_samples}):")
    for i, (sig_val, c, m_r, w_r, p_m) in enumerate(rows[-10:]):
        n_members = bin(int(sig_val)).count("1")
        comp_str = _decode(int(sig_val))
        typer.echo(
            f"    {int(c):>7,d}  {n_members:>4d}  {float(m_r):>+8.3f}  {float(w_r):>5.1f}%  {float(p_m):>+8.3f}  {comp_str}"
        )

    if output_csv:
        from pathlib import Path as _P

        csv_path = _P(output_csv)
        with csv_path.open("w") as f:
            f.write("count,member_count,mean_R,win_pct,peak_R,composition\n")
            for sig_val, c, m_r, w_r, p_m in rows:
                n_members = bin(int(sig_val)).count("1")
                comp_str = _decode(int(sig_val))
                f.write(f"{int(c)},{n_members},{float(m_r):.4f},{float(w_r):.2f},{float(p_m):.4f},{comp_str}\n")
        typer.echo(f"\n  Wrote full table to {csv_path}")


# ---------------------------------------------------------------------------
# derive-hierarchy-weights — empirical per-level-type weights (I2 + I7)
# ---------------------------------------------------------------------------


@rl_app.command("derive-hierarchy-weights")
def derive_hierarchy_weights(
    min_samples: int = typer.Option(100, help="Min episodes a level must be present in to get a learned weight"),
    output_yaml: str = typer.Option(
        "/app/backend/src/rl/config/empirical_level_weights.yaml",
        help="YAML path to write (relative to container)",
    ),
    compare_hand_tuned: bool = typer.Option(True, help="Compare to hand-tuned _HIERARCHY_WEIGHTS"),
) -> None:
    """Derive empirical per-level-type hierarchy weights from realized R.

    Weight formula:  empirical_w_i = mean_R_when_level_i_present / global_mean_R

    This gives ratios centered on 1.0: strong levels >1, weak levels <1.
    The hand-tuned _HIERARCHY_WEIGHTS currently uses 0.3-1.0 caps; the
    empirical weights rank by actual realized outcomes.

    Low-sample levels (<min_samples present) fall back to weight=1.0 so they
    don't get penalized just for being rare.
    """
    from pathlib import Path as _P

    import numpy as np
    import yaml as _yaml

    from src.rl.config import LevelType
    from src.rl.features.feature_names import level_composition_names
    from src.rl.zone_builder import _DEFAULT_WEIGHT, _HIERARCHY_WEIGHTS

    episodes_dir = _EPISODES_DIR
    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy in {episodes_dir}. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    observations = np.load(obs_path, mmap_mode="r")
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")

    comp = (np.array(observations[:, 0:31]) > 0.5).astype(np.int8)
    realized_R = np.maximum(rewards_cont, rewards_rev).astype(np.float32)
    global_mean = float(realized_R.mean())
    typer.echo(f"Global mean realized R = {global_mean:+.4f} (n={len(realized_R):,})")

    names = level_composition_names()
    rows = []
    for i, name in enumerate(names):
        mask = comp[:, i] == 1
        count = int(mask.sum())
        if count < min_samples:
            emp_w = 1.0
            mean_r = float("nan")
            note = f"insufficient samples (n={count}), fallback=1.0"
        else:
            mean_r = float(realized_R[mask].mean())
            emp_w = mean_r / global_mean if global_mean > 0 else 1.0
            note = ""
        hand_w = _HIERARCHY_WEIGHTS.get(LevelType(name), _DEFAULT_WEIGHT)
        rows.append((name, count, mean_r, emp_w, hand_w, note))

    # Normalize empirical weights so their mean equals 1.0 (robust to baseline drift)
    valid = [r[3] for r in rows if not np.isnan(r[2])]
    if valid:
        scale = float(np.mean(valid))
        rows = [(n, c, m, w / scale, hw, note) for (n, c, m, w, hw, note) in rows]

    # ------------------------------------------------------------------
    # Print side-by-side comparison
    # ------------------------------------------------------------------
    typer.echo("\n  EMPIRICAL vs HAND-TUNED weights:")
    typer.echo(
        f"    {'level_type':<24s}  {'n':>7s}  {'mean_R':>8s}  {'emp_w':>6s}  {'hand_w':>6s}  {'delta':>6s}  note"
    )
    typer.echo(f"    {'─' * 24}  {'─' * 7}  {'─' * 8}  {'─' * 6}  {'─' * 6}  {'─' * 6}  ────")
    for name, count, mean_r, emp_w, hand_w, note in sorted(rows, key=lambda r: -r[3]):
        mean_r_s = f"{mean_r:+.3f}" if not np.isnan(mean_r) else "  n/a"
        delta = emp_w - hand_w
        typer.echo(
            f"    {name:<24s}  {count:>7,d}  {mean_r_s:>8s}  {emp_w:>6.3f}  {hand_w:>6.3f}  {delta:>+6.3f}  {note}"
        )

    # ------------------------------------------------------------------
    # Write YAML config
    # ------------------------------------------------------------------
    out_path = _P(output_yaml)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_from": "realized_R = max(rewards_cont, rewards_rev)",
        "n_episodes": int(len(realized_R)),
        "global_mean_R": round(global_mean, 4),
        "normalization": "emp_w = (mean_R_when_present / global_mean) / mean_of_valid",
        "min_samples_for_learned_weight": min_samples,
        "fallback_weight": 1.0,
        "weights": {name: round(emp_w, 4) for (name, _, _, emp_w, _, _) in rows},
    }
    out_path.write_text(_yaml.safe_dump(payload, sort_keys=False))
    typer.echo(f"\n  Wrote empirical weights to {out_path}")


# ---------------------------------------------------------------------------
# tune-early-exit-threshold — sweep EE threshold on net-R-saved (H5)
# ---------------------------------------------------------------------------


@rl_app.command("tune-early-exit-threshold")
def tune_early_exit_threshold(
    lock_r: float = typer.Option(0.5, help="Partial-profit locked if EE fires (+0.5R default)"),
    thresholds: str = typer.Option(
        "0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9",
        help="Comma-separated thresholds to sweep",
    ),
) -> None:
    """Sweep EarlyExit thresholds on net-R-saved across the OOS split.

    Counter-factual: at each threshold τ, for every episode:
      - If P(pump-and-retrace) >= τ AND peak_R >= lock_r: "early-exit fires"
        → realized_R → lock_r (we locked partial profit)
      - Otherwise: realized_R stays as-is

    Net-R-saved = sum((new_R - original_R) where EE fired). Positive means
    the EE rule saved R (we avoided larger retraces); negative means the
    rule cost R (we cut winners too early).
    """

    import numpy as np

    from src.rl.agent.early_exit_model import EarlyExitModel

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR

    ee_path = models_dir / "early_exit_model_latest.joblib"
    if not ee_path.exists():
        typer.echo(f"No {ee_path} — train one first.", err=True)
        raise typer.Exit(1)

    obs_path = episodes_dir / "observations.npy"
    peakc_path = episodes_dir / "peak_R_cont.npy"
    peakr_path = episodes_dir / "peak_R_rev.npy"
    if not all(p.exists() for p in [obs_path, peakc_path, peakr_path]):
        typer.echo("Need observations.npy + peak_R_cont.npy + peak_R_rev.npy", err=True)
        raise typer.Exit(1)

    typer.echo(f"Loading episodes + EE model ({ee_path.name})...")
    observations = np.load(obs_path)
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    peak_c = np.load(peakc_path)
    peak_r = np.load(peakr_path)

    X, trigger_obs = _build_augmented_obs(observations, episodes_dir)

    # Action-conditioned realized + peak (H4) — same convention the EE head trained on
    realized_R, label_source = _compute_action_conditioned_R(
        trigger_obs=trigger_obs,
        rewards_cont=rewards_cont,
        rewards_rev=rewards_rev,
        models_dir=models_dir,
    )
    if trigger_obs is not None:
        try:
            from src.rl.agent.trigger_gbt import TriggerGBT as _TGBT

            gbt = _TGBT.load(models_dir / "trigger_gbt_v5.joblib")
            actions, _c, _p = gbt.predict_direction_batch(trigger_obs.astype(np.float32))
            peak_R = np.where(actions == 0, peak_c, peak_r).astype(np.float32)
        except Exception:
            peak_R = np.maximum(peak_c, peak_r).astype(np.float32)
    else:
        peak_R = np.maximum(peak_c, peak_r).astype(np.float32)

    typer.echo(f"Label source: {label_source}")
    typer.echo(f"Loaded {len(X):,} episodes")

    # Use the last 20% as OOS (matches chronological split used everywhere else)
    val_split = int(len(X) * 0.80)
    Xv = X[val_split:]
    Rv = realized_R[val_split:]
    Pv = peak_R[val_split:]
    typer.echo(f"OOS split: {len(Xv):,} episodes")

    ee = EarlyExitModel.load(ee_path)
    probs = ee.predict_proba_batch(Xv)

    thresh_list = [float(t) for t in thresholds.split(",") if t.strip()]
    baseline_total = float(Rv.sum())
    typer.echo(f"\nBaseline total R (no EE): {baseline_total:+.1f}")
    typer.echo(f"Lock R on EE fire: +{lock_r}")
    typer.echo(
        f"\n  {'thresh':>7s}  {'flags':>7s}  {'flag%':>6s}  {'TP_flag':>8s}  {'FP_flag':>8s}  {'sum_new_R':>10s}  {'Δ_R':>9s}  note"
    )
    typer.echo(f"  {'─' * 7}  {'─' * 7}  {'─' * 6}  {'─' * 8}  {'─' * 8}  {'─' * 10}  {'─' * 9}  ────")

    for thr in thresh_list:
        fires = (probs >= thr) & (Pv >= lock_r)  # only counts if trade actually reached lock_r
        new_R = np.where(fires, lock_r, Rv)
        delta = float(new_R.sum() - baseline_total)
        n_fires = int(fires.sum())
        pct_fires = 100.0 * n_fires / max(len(Xv), 1)
        # TP_flag = EE fired AND realized_R < lock_r (retrace saved)
        tp_flag = int(((fires) & (Rv < lock_r)).sum())
        fp_flag = int(((fires) & (Rv >= lock_r)).sum())
        typer.echo(
            f"  {thr:>7.2f}  {n_fires:>7,d}  {pct_fires:>5.1f}%  {tp_flag:>8,d}  {fp_flag:>8,d}  "
            f"{float(new_R.sum()):>+10.1f}  {delta:>+9.1f}  {'(net saved R)' if delta > 0 else '(net cost R)'}"
        )


# ---------------------------------------------------------------------------
# per-day-report — daily R / trades / DD breakdown on the OOS split
# ---------------------------------------------------------------------------


@rl_app.command("per-day-report")
def per_day_report(
    apply_size_model: bool = typer.Option(True, help="Multiply each trade's R by SizeModel's predicted multiplier"),
    skip_threshold: float = typer.Option(0.15, help="Skip trade if TriggerGBT confidence below this"),
    summary_only: bool = typer.Option(False, help="Print summary stats only, no per-day rows"),
    head: int = typer.Option(40, help="Show first N + last N days when not summary-only"),
    output_csv: str | None = typer.Option(None, help="Optional CSV output path"),
) -> None:
    """Per-day breakdown of model performance on the OOS split.

    Loads the last 17% chronological slice (matches `rl eval`), runs the
    TriggerGBT to get per-episode actions + confidence, optionally applies
    SizeModel's size multiplier, then groups by trading day (US/Eastern
    cash session) and reports:

      day | trades | win% | day_R | running_R | day_max_R | day_DD

    Plus summary: total days, % positive, best day, worst day, max single-day
    DD, max running-equity DD across the OOS window.
    """
    from datetime import datetime, timezone

    import numpy as np

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR

    obs_path = episodes_dir / "observations.npy"
    te_path = episodes_dir / "touch_epochs.npy"
    if not (obs_path.exists() and te_path.exists()):
        typer.echo("Need observations.npy + touch_epochs.npy. Run replay first.", err=True)
        raise typer.Exit(1)

    typer.echo("Loading episodes...")
    observations = np.load(obs_path)
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    touch_epochs = np.load(te_path)

    X, trigger_obs = _build_augmented_obs(observations, episodes_dir)

    # Align all arrays to the shortest one — touch_epochs / trigger_observations
    # can lag observations by a tail (interrupted writes). Truncate to min len.
    n_align = min(
        len(X),
        len(rewards_cont),
        len(rewards_rev),
        len(touch_epochs),
        len(trigger_obs) if trigger_obs is not None else len(X),
    )
    if n_align != len(X):
        typer.echo(f"Aligned to min length: {n_align:,} (was {len(X):,})")
    X = X[:n_align]
    rewards_cont = rewards_cont[:n_align]
    rewards_rev = rewards_rev[:n_align]
    touch_epochs = touch_epochs[:n_align]
    if trigger_obs is not None:
        trigger_obs = trigger_obs[:n_align]

    # OOS split: last 17% chronologically (matches eval convention)
    val_split = int(n_align * 0.83)
    Xv = X[val_split:]
    rcv = rewards_cont[val_split:]
    rrv = rewards_rev[val_split:]
    tev = touch_epochs[val_split:]
    trigv = trigger_obs[val_split:] if trigger_obs is not None else None
    n_oos = len(Xv)
    typer.echo(f"OOS slice: {n_oos:,} episodes")

    # Run TriggerGBT for actions + confidence
    if trigv is None:
        typer.echo("Need trigger_observations.npy + trained TriggerGBT.", err=True)
        raise typer.Exit(1)

    from src.rl.agent.trigger_gbt import TriggerGBT

    tgbt_path = models_dir / "trigger_gbt_v5.joblib"
    if not tgbt_path.exists():
        typer.echo(f"No {tgbt_path}", err=True)
        raise typer.Exit(1)

    gbt = TriggerGBT.load(tgbt_path)
    actions, confs, _probs = gbt.predict_direction_batch(trigv.astype(np.float32))
    realized_R = np.where(actions == 0, rcv, rrv).astype(np.float32)

    # Skip threshold gate — confidence below threshold = no trade
    take = confs >= skip_threshold
    realized_R = np.where(take, realized_R, 0.0)

    # Optional: apply SizeModel multiplier
    size_mults = np.ones(n_oos, dtype=np.float32)
    if apply_size_model:
        sm_path = models_dir / "size_model_latest.joblib"
        if sm_path.exists():
            from src.rl.agent.size_model import SizeModel

            sm = SizeModel.load(sm_path)
            size_mults = sm.predict_size_batch(Xv).astype(np.float32)
            realized_R = realized_R * size_mults
            typer.echo(f"Applied SizeModel: mean mult = {size_mults.mean():.3f}")
        else:
            typer.echo("No size_model_latest.joblib; using uniform size 1.0")

    # Group by trading day (US/Eastern cash session — calendar day in ET)
    # touch_epochs are unix UTC. Subtract 5h to land in ET (rough, ignores DST).
    # For per-day grouping that's good enough; we're not measuring intraday.
    et_dates = []
    for ts in tev:
        if ts > 0:
            d = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            # Roughly bucket by ET day: subtract 5h (good enough, DST-agnostic)
            et_d = d.toordinal() if d.hour >= 5 else d.toordinal() - 1
            et_dates.append(et_d)
        else:
            et_dates.append(0)
    et_dates = np.array(et_dates, dtype=np.int64)

    unique_days = sorted(set(int(d) for d in et_dates if d > 0))
    typer.echo(f"OOS spans {len(unique_days)} trading days")

    rows = []
    cumulative = 0.0
    peak_cum = 0.0
    max_drawdown = 0.0
    for day_ord in unique_days:
        mask = et_dates == day_ord
        day_R = realized_R[mask]
        # Filter to actual trades (R != 0 means we took the trade)
        taken = day_R != 0
        n_trades = int(taken.sum())
        wins = int((day_R > 0).sum())
        sum_R = float(day_R.sum())
        cumulative += sum_R
        # Within-day intraday peak / trough for day_DD
        intraday = np.cumsum(day_R)
        intraday_peak = float(intraday.max()) if len(intraday) > 0 else 0.0
        intraday_trough = float(intraday.min()) if len(intraday) > 0 else 0.0
        day_DD = intraday_trough - intraday_peak  # negative if drawdown happened
        # Equity-curve DD across days
        peak_cum = max(peak_cum, cumulative)
        equity_dd = cumulative - peak_cum  # negative if below peak
        if equity_dd < max_drawdown:
            max_drawdown = equity_dd
        date_str = datetime.fromordinal(day_ord).strftime("%Y-%m-%d") if day_ord > 0 else "—"
        rows.append((date_str, n_trades, wins, sum_R, cumulative, intraday_peak, day_DD, equity_dd))

    # Summary
    day_R_arr = np.array([r[3] for r in rows], dtype=np.float64)
    n_pos = int((day_R_arr > 0).sum())
    n_neg = int((day_R_arr < 0).sum())
    n_flat = len(rows) - n_pos - n_neg
    best_day = max(rows, key=lambda r: r[3]) if rows else None
    worst_day = min(rows, key=lambda r: r[3]) if rows else None
    typer.echo(f"\nSUMMARY (OOS, threshold={skip_threshold}, size_model={apply_size_model})")
    typer.echo("-" * 78)
    typer.echo(f"  Trading days        : {len(rows)}")
    typer.echo(f"  Positive days       : {n_pos} ({100 * n_pos / max(len(rows), 1):.1f}%)")
    typer.echo(f"  Negative days       : {n_neg} ({100 * n_neg / max(len(rows), 1):.1f}%)")
    typer.echo(f"  Flat days           : {n_flat}")
    typer.echo(f"  Total R             : {cumulative:+.1f}")
    typer.echo(f"  Mean R / day        : {day_R_arr.mean():+.2f}")
    typer.echo(f"  Median R / day      : {float(np.median(day_R_arr)):+.2f}")
    typer.echo(f"  Std R / day         : {day_R_arr.std():.2f}")
    typer.echo(f"  Sharpe (R/day)      : {day_R_arr.mean() / max(day_R_arr.std(), 0.01):.2f}")
    typer.echo(f"  Max equity drawdown : {max_drawdown:+.2f} R (peak-to-trough)")
    if best_day:
        typer.echo(f"  Best day            : {best_day[0]}  {best_day[3]:+.1f} R  ({best_day[1]} trades)")
    if worst_day:
        typer.echo(f"  Worst day           : {worst_day[0]}  {worst_day[3]:+.1f} R  ({worst_day[1]} trades)")

    if not summary_only:
        typer.echo(
            f"\n{'date':<12s}  {'tr':>4s}  {'win':>4s}  {'win%':>5s}  {'day_R':>8s}  {'cum_R':>10s}  {'day_DD':>8s}  {'eq_DD':>8s}"
        )
        typer.echo("-" * 78)
        head_n = head
        if len(rows) <= 2 * head_n:
            display = rows
        else:
            display = rows[:head_n] + [("...", 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)] + rows[-head_n:]
        for r in display:
            date_str, n_tr, wins, sum_R, cum_R, intra_peak, day_DD, eq_DD = r
            if date_str == "...":
                typer.echo(f"{'...':<12s}  {'...':>4s}")
                continue
            win_pct = 100 * wins / max(n_tr, 1)
            typer.echo(
                f"{date_str:<12s}  {n_tr:>4d}  {wins:>4d}  {win_pct:>4.0f}%  "
                f"{sum_R:>+8.2f}  {cum_R:>+10.2f}  {day_DD:>+8.2f}  {eq_DD:>+8.2f}"
            )

    if output_csv:
        from pathlib import Path as _P

        csv_path = _P(output_csv)
        with csv_path.open("w") as f:
            f.write("date,trades,wins,day_R,cumulative_R,intraday_peak,day_DD,equity_DD\n")
            for r in rows:
                f.write(",".join(str(x) for x in r) + "\n")
        typer.echo(f"\nWrote per-day table to {csv_path}")


# ---------------------------------------------------------------------------
# inspect-day — drill into a specific date (regime event diagnosis)
# ---------------------------------------------------------------------------


@rl_app.command("inspect-day")
def inspect_day(
    date: str = typer.Argument(..., help="ET trading day to inspect, format YYYY-MM-DD"),
    compare_with: str | None = typer.Option(None, help="Optional second date for side-by-side comparison"),
    skip_threshold: float = typer.Option(0.15, help="TriggerGBT confidence cut for taking a trade"),
) -> None:
    """Drill into one trading day to diagnose regime events / bad days.

    For each requested date:
      - action distribution (CONT vs REV vs SKIP)
      - level-type composition (which zones the touches happened on)
      - confidence histogram
      - delta-alignment (was the OF veto triggering? was tape against trade dir?)
      - per-side win rate + R distribution

    Use to figure out *why* a day was abnormal — was the model picking
    the wrong side, was it sized too aggressively, were the level types
    different from training distribution, etc.
    """
    from datetime import datetime, timezone

    import numpy as np

    from src.rl.features.feature_names import level_composition_names

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR

    typer.echo("Loading episodes...")
    observations = np.load(episodes_dir / "observations.npy")
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    touch_epochs = np.load(episodes_dir / "touch_epochs.npy")

    X, trigger_obs = _build_augmented_obs(observations, episodes_dir)

    n_align = min(len(X), len(rewards_cont), len(touch_epochs), len(trigger_obs) if trigger_obs is not None else len(X))
    X = X[:n_align]
    rewards_cont = rewards_cont[:n_align]
    rewards_rev = rewards_rev[:n_align]
    touch_epochs = touch_epochs[:n_align]
    if trigger_obs is not None:
        trigger_obs = trigger_obs[:n_align]
    observations = observations[:n_align]

    from src.rl.agent.trigger_gbt import TriggerGBT

    gbt = TriggerGBT.load(models_dir / "trigger_gbt_v5.joblib")

    sm_path = models_dir / "size_model_latest.joblib"
    size_model = None
    if sm_path.exists():
        from src.rl.agent.size_model import SizeModel

        size_model = SizeModel.load(sm_path)

    def _day_mask(target_date: str) -> np.ndarray:
        d = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        # ET cash session: roughly 09:30-16:00 ET = 13:30-20:00 UTC (DST-agnostic).
        # Bucket by UTC ordinal-day-1-if-before-05:00 (matches per-day-report).
        target_ord = d.toordinal()
        mask = np.zeros(len(touch_epochs), dtype=bool)
        for i, ts in enumerate(touch_epochs):
            if ts <= 0:
                continue
            t = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            day_ord = t.toordinal() if t.hour >= 5 else t.toordinal() - 1
            if day_ord == target_ord:
                mask[i] = True
        return mask

    def _report(label: str, mask: np.ndarray) -> None:
        n = int(mask.sum())
        if n == 0:
            typer.echo(f"\n{label}: no episodes")
            return
        idx = np.where(mask)[0]
        Xs = X[idx]
        rcs = rewards_cont[idx]
        rrs = rewards_rev[idx]
        comp = (np.asarray(observations[idx, 0:31]) > 0.5).astype(np.int8)
        member_count = comp.sum(axis=1)
        trigs = trigger_obs[idx]
        actions, confs, _probs = gbt.predict_direction_batch(trigs.astype(np.float32))
        # Skip: confidence below threshold
        take_mask = confs >= skip_threshold
        # Action-conditioned R
        R = np.where(actions == 0, rcs, rrs).astype(np.float32)
        R_taken = R[take_mask]
        # Size mult
        if size_model is not None:
            sizes = size_model.predict_size_batch(Xs).astype(np.float32)
        else:
            sizes = np.ones(n, dtype=np.float32)
        R_sized = R * sizes
        R_sized_taken = R_sized[take_mask]

        # Delta alignment
        delta_signed = Xs[:, 31]  # orderflow[0]
        # Trade direction by approach + action
        approach_dir = Xs[:, 268]  # approach_dir[0] (signed in obs)
        # CONT in approach dir = same sign as approach_dir; REV = opposite
        # In our config: action 0=CONT, 1=REV; approach +1=up, -1=down
        # CONT+up=long, CONT+down=short, REV+up=short, REV+down=long
        td = np.where(actions == 0, approach_dir, -approach_dir)
        delta_aligned = (td * delta_signed) > 0
        delta_against = (td * delta_signed < 0) & (np.abs(delta_signed) > 0.3)

        # Level types most touched
        level_names = level_composition_names()
        level_counts = {level_names[i]: int(comp[:, i].sum()) for i in range(31) if comp[:, i].sum() > 0}
        top_levels = sorted(level_counts.items(), key=lambda x: -x[1])[:10]

        typer.echo(f"\n{'=' * 70}")
        typer.echo(f"{label}  ({n} episodes)")
        typer.echo(f"{'=' * 70}")
        typer.echo(
            f"  Actions       : CONT={int((actions == 0).sum())} ({100 * (actions == 0).mean():.1f}%) "
            f"REV={int((actions == 1).sum())} ({100 * (actions == 1).mean():.1f}%)"
        )
        typer.echo(
            f"  Confidence    : mean={confs.mean():.3f}  median={float(np.median(confs)):.3f}  >= {skip_threshold}: {int(take_mask.sum())} trades ({100 * take_mask.mean():.1f}%)"
        )
        if take_mask.sum() > 0:
            wins = int((R_taken > 0).sum())
            typer.echo(
                f"  Win rate      : {100 * wins / max(take_mask.sum(), 1):.1f}%  ({wins} wins / {int(take_mask.sum())} trades)"
            )
            typer.echo(f"  Mean R/trade  : action_conditioned={R_taken.mean():+.3f}  sized={R_sized_taken.mean():+.3f}")
            typer.echo(f"  Total R       : sized={R_sized_taken.sum():+.1f}  unsized={R_taken.sum():+.1f}")
            typer.echo(
                f"  R distribution: min={R_taken.min():+.2f} q25={float(np.quantile(R_taken, 0.25)):+.2f} median={float(np.median(R_taken)):+.2f} q75={float(np.quantile(R_taken, 0.75)):+.2f} max={R_taken.max():+.2f}"
            )
            typer.echo(
                f"  Size mult     : mean={sizes[take_mask].mean():.2f}  unique tiers: {sorted(set(round(float(s), 2) for s in sizes[take_mask]))}"
            )
        typer.echo(
            f"  Delta align   : aligned={int(delta_aligned.sum())} ({100 * delta_aligned.mean():.1f}%)  "
            f"strongly against={int(delta_against.sum())} ({100 * delta_against.mean():.1f}%)"
        )
        typer.echo(f"  Member counts : {dict(zip(*np.unique(member_count, return_counts=True), strict=False))}")
        typer.echo(f"  Top levels    : {top_levels}")

    _report(date, _day_mask(date))
    if compare_with:
        _report(compare_with, _day_mask(compare_with))


# ---------------------------------------------------------------------------
# simulate-phase2-gate — counter-factual replay of SessionState gate on OOS
# ---------------------------------------------------------------------------


@rl_app.command("simulate-phase2-gate")
def simulate_phase2_gate(
    cooldown_seconds: int = typer.Option(300, help="Per-zone cooldown (default 300s = 5 min)"),
    max_consec_losses: int = typer.Option(5, help="Trip circuit on N consecutive losses"),
    rolling_window: int = typer.Option(10, help="Rolling window size for win-rate gate"),
    min_rolling_win_rate: float = typer.Option(0.15, help="Trip if rolling win rate < this"),
    max_session_dd_r: float = typer.Option(200.0, help="Trip if session DD < -this"),
    wins_to_resume: int = typer.Option(2, help="Wins required to resume after circuit trip"),
    skip_threshold: float = typer.Option(0.15, help="TriggerGBT confidence cut"),
    apply_size_model: bool = typer.Option(True, help="Apply SizeModel multiplier"),
) -> None:
    """Counter-factual: replay SessionState gate on the OOS episodes.

    Walks the OOS slice in chronological order. For each episode:
      1. Compute model's action + R as `per-day-report` does
      2. Ask SessionState.should_skip — record decision + reason
      3. If allowed: count R; if blocked: count what we WOULD have made

    Output: aggregate Δ R from skips broken into "saved losses" and
    "missed winners", per-day breakdown, and tunable threshold view so
    we can see whether the defaults are too tight.
    """
    from datetime import datetime, timezone

    import numpy as np

    from src.rl.session_state import SessionState

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR

    obs_path = episodes_dir / "observations.npy"
    te_path = episodes_dir / "touch_epochs.npy"
    if not (obs_path.exists() and te_path.exists()):
        typer.echo("Need observations.npy + touch_epochs.npy.", err=True)
        raise typer.Exit(1)

    typer.echo("Loading OOS slice...")
    observations = np.load(obs_path)
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    touch_epochs = np.load(te_path)

    X, trigger_obs = _build_augmented_obs(observations, episodes_dir)

    n_align = min(
        len(X),
        len(rewards_cont),
        len(rewards_rev),
        len(touch_epochs),
        len(trigger_obs) if trigger_obs is not None else len(X),
    )
    X = X[:n_align]
    rewards_cont = rewards_cont[:n_align]
    rewards_rev = rewards_rev[:n_align]
    touch_epochs = touch_epochs[:n_align]
    if trigger_obs is not None:
        trigger_obs = trigger_obs[:n_align]
    observations = observations[:n_align]

    val_split = int(n_align * 0.83)
    Xv = X[val_split:]
    rcv = rewards_cont[val_split:]
    rrv = rewards_rev[val_split:]
    tev = touch_epochs[val_split:]
    trigv = trigger_obs[val_split:] if trigger_obs is not None else None
    obsv = observations[val_split:]
    n_oos = len(Xv)
    typer.echo(f"OOS slice: {n_oos:,} episodes")

    if trigv is None:
        typer.echo("Need trigger_observations.npy", err=True)
        raise typer.Exit(1)

    from src.rl.agent.trigger_gbt import TriggerGBT

    gbt = TriggerGBT.load(models_dir / "trigger_gbt_v5.joblib")
    actions, confs, _probs = gbt.predict_direction_batch(trigv.astype(np.float32))
    realized_R_unsized = np.where(actions == 0, rcv, rrv).astype(np.float32)

    take_mask = confs >= skip_threshold

    sizes = np.ones(n_oos, dtype=np.float32)
    if apply_size_model:
        sm_path = models_dir / "size_model_latest.joblib"
        if sm_path.exists():
            from src.rl.agent.size_model import SizeModel

            sm = SizeModel.load(sm_path)
            sizes = sm.predict_size_batch(Xv).astype(np.float32)
    realized_R = realized_R_unsized * sizes

    # Build SessionState with the requested thresholds
    session_state = SessionState(
        rolling_window=rolling_window,
        min_rolling_win_rate=min_rolling_win_rate,
        max_consecutive_losses=max_consec_losses,
        max_session_drawdown_r=max_session_dd_r,
        wins_to_resume=wins_to_resume,
        min_zone_cooldown_seconds=cooldown_seconds,
    )

    # Walk chronologically. Reset SessionState at session boundary (ET day change).
    last_day_ord = None
    n_taken = 0
    n_skip_threshold = 0
    n_skip_cooldown = 0
    n_skip_circuit = 0
    R_taken_baseline = 0.0  # what model would have made without gate
    R_taken_gated = 0.0  # what model makes with gate
    R_saved_from_losers = 0.0  # losses we avoided
    R_missed_from_winners = 0.0  # winners we cut
    skips_were_winners = 0
    skips_were_losers = 0

    # Per-day aggregation
    per_day = {}

    for i in range(n_oos):
        ts = float(tev[i])
        if ts <= 0:
            continue
        float(obsv[i, 0]) if False else 4500.0  # placeholder — see below
        # We don't have the zone price from obs (level_composition is multi-hot).
        # Use the touch_epoch-derived approximation: cluster trades by minute-bucket
        # within the same hour, since trades close in time at a "zone" tend to be
        # the same zone retested. Better proxy: use a small synthetic key derived
        # from the structure passthrough features (price relative to VAH/VAL).
        # For this counterfactual, use the zone_features hierarchy + member_count
        # as a stand-in (similar zones in the same minute will have similar values).
        # Simplest: use a hash of (minute_bucket, hierarchy_score, member_count).
        # Actual zone PRICE isn't preserved in the observation tensor.
        zone_feat = float(obsv[i, 169])  # zone_features.hierarchy_score
        member_norm = float(obsv[i, 170])  # zone_features.member_count_norm
        # Use the touch timestamp + zone features as a synthetic zone key.
        # 60-second window: same hierarchy+members within 60s → same "zone".
        minute_bucket = int(ts // 60)
        zone_key = float(hash((minute_bucket // 5, round(zone_feat, 2), round(member_norm, 2))) % 10000)

        # Reset SessionState at session boundary
        d = datetime.fromtimestamp(ts, tz=timezone.utc)
        day_ord = d.toordinal() if d.hour >= 5 else d.toordinal() - 1
        if last_day_ord is not None and day_ord != last_day_ord:
            session_state.reset_for_new_session()
        last_day_ord = day_ord

        if day_ord not in per_day:
            per_day[day_ord] = {
                "taken_baseline_R": 0.0,
                "taken_gated_R": 0.0,
                "skipped_R": 0.0,
                "n_taken_baseline": 0,
                "n_taken_gated": 0,
                "n_skip_cooldown": 0,
                "n_skip_circuit": 0,
            }
        d_stats = per_day[day_ord]

        # Skip-threshold filter (pre-gate)
        if not take_mask[i]:
            n_skip_threshold += 1
            continue

        R = float(realized_R[i])
        R_taken_baseline += R
        d_stats["taken_baseline_R"] += R
        d_stats["n_taken_baseline"] += 1

        sr = session_state.should_skip(zone_key=zone_key, now_ts=ts)
        if sr is None:
            R_taken_gated += R
            d_stats["taken_gated_R"] += R
            d_stats["n_taken_gated"] += 1
            n_taken += 1
            session_state.record_trade(zone_key=zone_key, now_ts=ts, realized_R=R)
        else:
            d_stats["skipped_R"] += R
            if sr.code == "cooldown":
                n_skip_cooldown += 1
                d_stats["n_skip_cooldown"] += 1
            else:
                n_skip_circuit += 1
                d_stats["n_skip_circuit"] += 1
            if R > 0:
                R_missed_from_winners += R
                skips_were_winners += 1
            else:
                R_saved_from_losers += -R  # turn negative R into positive "saved"
                skips_were_losers += 1

    delta_R = R_taken_gated - R_taken_baseline
    typer.echo(f"\n{'=' * 70}")
    typer.echo(f"Phase 2 gate counter-factual on {n_oos:,} OOS episodes")
    typer.echo(
        f"  cooldown={cooldown_seconds}s  consec={max_consec_losses}  rolling={rolling_window}@<{min_rolling_win_rate:.0%}  dd={max_session_dd_r:.0f}R"
    )
    typer.echo(f"{'=' * 70}")
    typer.echo(
        f"  Trades taken (baseline)    : {n_skip_threshold + R_taken_baseline / max(R_taken_baseline / 1, 1):.0f} above threshold"
    )
    n_above_threshold = int(take_mask.sum())
    typer.echo(f"  Trades above conf threshold: {n_above_threshold:,}")
    typer.echo(
        f"  Trades taken (gated)       : {n_taken:,}  ({100 * n_taken / max(n_above_threshold, 1):.1f}% of above-threshold)"
    )
    typer.echo(
        f"  Skipped by cooldown        : {n_skip_cooldown:,}  ({100 * n_skip_cooldown / max(n_above_threshold, 1):.1f}%)"
    )
    typer.echo(
        f"  Skipped by circuit         : {n_skip_circuit:,}  ({100 * n_skip_circuit / max(n_above_threshold, 1):.1f}%)"
    )
    typer.echo("")
    typer.echo(f"  Baseline total R           : {R_taken_baseline:+.1f}")
    typer.echo(f"  Gated total R              : {R_taken_gated:+.1f}")
    typer.echo(f"  Δ R (gated - baseline)     : {delta_R:+.1f}  ({'GATE HELPS' if delta_R > 0 else 'GATE HURTS'})")
    typer.echo("")
    typer.echo(f"  Skips that were losers     : {skips_were_losers:,}  → saved {R_saved_from_losers:+.1f} R")
    typer.echo(f"  Skips that were winners    : {skips_were_winners:,}  → missed {R_missed_from_winners:+.1f} R")
    typer.echo(
        f"  Skip mix win rate          : {100 * skips_were_winners / max(skips_were_losers + skips_were_winners, 1):.1f}%"
    )
    typer.echo("")

    # Best/worst day comparison
    sorted_days = sorted(per_day.keys())
    day_baselines = np.array([per_day[d]["taken_baseline_R"] for d in sorted_days])
    day_gateds = np.array([per_day[d]["taken_gated_R"] for d in sorted_days])
    typer.echo(
        f"  Worst baseline day         : {datetime.fromordinal(sorted_days[int(np.argmin(day_baselines))]).strftime('%Y-%m-%d')}  "
        f"baseline={day_baselines.min():+.1f}  gated={day_gateds[int(np.argmin(day_baselines))]:+.1f}"
    )
    typer.echo(
        f"  Best baseline day          : {datetime.fromordinal(sorted_days[int(np.argmax(day_baselines))]).strftime('%Y-%m-%d')}  "
        f"baseline={day_baselines.max():+.1f}  gated={day_gateds[int(np.argmax(day_baselines))]:+.1f}"
    )

    # Equity curve max DD comparison
    cum_baseline = np.cumsum(day_baselines)
    cum_gated = np.cumsum(day_gateds)
    peak_b = np.maximum.accumulate(cum_baseline)
    peak_g = np.maximum.accumulate(cum_gated)
    dd_b = (cum_baseline - peak_b).min()
    dd_g = (cum_gated - peak_g).min()
    typer.echo(f"  Max equity DD (baseline)   : {dd_b:+.1f} R")
    typer.echo(
        f"  Max equity DD (gated)      : {dd_g:+.1f} R  ({'BETTER' if dd_g > dd_b else 'WORSE'} by {dd_g - dd_b:+.1f} R)"
    )


# ---------------------------------------------------------------------------
# analyze-pivots — surface the dim values the model captured at major moves
# ---------------------------------------------------------------------------


@rl_app.command("analyze-pivots")
def analyze_pivots(
    hours: int = typer.Option(12, help="Lookback window in hours"),
    min_range_pts: float = typer.Option(15.0, help="Minimum 15-min range (points) to qualify as a pivot"),
    max_pivots: int = typer.Option(15, help="How many top pivots to analyze"),
    bucket_minutes: int = typer.Option(15, help="Pivot detection bucket size"),
) -> None:
    """Detect the biggest price-reversal windows over the last N hours and
    show what the model SAW at each pivot — orderflow, zone composition,
    macro, conviction, and the actual trade outcome (if any).

    Lets you cross-reference live chart pivots against the obs the model
    captured, so we can recognize the same pattern next time.

    Output per pivot:
      - Time window + price range
      - Each stock_signals row that fired in that window
      - Decoded reasoning JSONB (factors, OF, zone, conviction)
      - Linked broker_trade outcome (or "no trade" if gated out)
    """
    import base64
    import json
    import os
    from datetime import timedelta

    import numpy as np
    from sqlalchemy import create_engine, text

    pw = os.environ.get("DB_PASSWORD", "")
    market_url = f"postgresql://arnold:{pw}@postgres:5432/market"
    arnold_url = f"postgresql://arnold:{pw}@postgres:5432/arnold"

    market_engine = create_engine(market_url)
    arnold_engine = create_engine(arnold_url)

    # 1. Find the biggest N pivot windows in market_trades.
    pivot_sql = text(
        f"""
        SELECT
          date_trunc('hour', ts) + (FLOOR(EXTRACT(MINUTE FROM ts)/{bucket_minutes})::int * INTERVAL '{bucket_minutes} minutes') AS bucket,
          ROUND(MAX(price)::numeric, 2) AS hi,
          ROUND(MIN(price)::numeric, 2) AS lo,
          ROUND((MAX(price) - MIN(price))::numeric, 2) AS range_pt,
          COUNT(*) AS ticks
        FROM market_trades
        WHERE ts > NOW() - INTERVAL ':hours hours'
        GROUP BY 1
        HAVING MAX(price) - MIN(price) > :min_range
        ORDER BY range_pt DESC
        LIMIT :limit
        """.replace(":hours hours", f"{hours} hours")
    )
    with market_engine.connect() as conn:
        pivots = conn.execute(pivot_sql, {"min_range": min_range_pts, "limit": max_pivots}).fetchall()

    if not pivots:
        typer.echo(f"No pivots found > {min_range_pts}pt range in last {hours}h.")
        return

    typer.echo(f"\n=== TOP {len(pivots)} PIVOTS (last {hours}h, min range {min_range_pts}pt) ===\n")

    # 2. For each pivot, pull stock_signals + linked broker_trade
    sig_sql = text(
        """
        SELECT s.id AS sid, s.ts, s.action, s.price, s.confidence, s.cont_p, s.rev_p,
               s.observation_b64, s.observation_dim, s.zone_center, s.zone_members,
               s.reasoning, s.trade_id,
               t.id AS tid, t.entry_price, t.exit_price, t.was_stop, t.pnl_r, t.pnl_dollars,
               t.signal_trigger
        FROM stock_signals s
        LEFT JOIN broker_trades t ON s.trade_id = t.id
        WHERE s.ts BETWEEN :lo AND :hi
          AND s.observation_b64 IS NOT NULL
        ORDER BY s.ts
        """
    )

    for p in pivots:
        bucket: datetime = p.bucket
        bucket_end = bucket + timedelta(minutes=bucket_minutes)
        typer.echo(f"━━━ {bucket:%Y-%m-%d %H:%M} UTC: hi={p.hi} lo={p.lo} range={p.range_pt}pt ({p.ticks} ticks) ━━━")
        with arnold_engine.connect() as conn:
            sigs = conn.execute(sig_sql, {"lo": bucket, "hi": bucket_end}).fetchall()
        if not sigs:
            typer.echo("  (no stock_signals in this window)")
            continue
        for s in sigs:
            r = s.reasoning or {}
            if isinstance(r, str):
                try:
                    r = json.loads(r)
                except Exception:
                    r = {}
            zone = r.get("zone", {}) if isinstance(r, dict) else {}
            of = r.get("of", {}) if isinstance(r, dict) else {}
            factors = r.get("primary_factors", []) if isinstance(r, dict) else []
            macro = r.get("macro", {}) if isinstance(r, dict) else {}
            tline = (
                f"  {s.ts:%H:%M:%S} sid={s.sid} {s.action:<11} px={s.price:.2f}"
                f"  cp={(s.cont_p or 0):.2f}/rp={(s.rev_p or 0):.2f}  conf={(s.confidence or 0):.3f}"
                f"  OF={(of.get('score') or 0):.2f}  zone_str={(zone.get('strength') or 0):.2f}"
                f"  fams={zone.get('families', [])}"
            )
            typer.echo(tline)
            if factors:
                typer.echo(f"     factors: {factors}")
            if macro:
                typer.echo(
                    f"     macro: VIX={macro.get('vix')} regime={macro.get('regime')} score={macro.get('regime_score')}"
                )
            if s.tid is not None:
                outcome = "WIN" if (s.pnl_r or 0) > 0 else "LOSS" if (s.pnl_r or 0) < 0 else "BE"
                stop_kind = "STOPPED" if s.was_stop else "signal-exit"
                typer.echo(
                    f"     → traded id={s.tid} {outcome} pnl_r={(s.pnl_r or 0):+.2f}R ${(s.pnl_dollars or 0):+.0f} ({stop_kind})"
                )
            else:
                typer.echo("     → not traded (gated/cooldown/in-position)")

            # Decode key obs dims at this pivot moment
            try:
                obs = np.frombuffer(base64.b64decode(s.observation_b64), dtype=np.float32)
                # Orderflow segment lives at obs[21:42] per features/orderflow_features.py:
                #   [0] delta_pct, [1] delta_norm, [2] cvd, [3] cvd_trend,
                #   [4] vol_ratio, [9] stacked_imb_count, [10] stacked_dir,
                #   [13] vsa_absorption, [16] absorption_strength,
                #   [18] volume_climax, [19] delta_divergence, [20] flow_shift
                if obs.size >= 42:
                    of_seg = obs[21:42]
                    typer.echo(
                        f"     obs[OF]: delta_pct={of_seg[0]:+.2f} cvd_trend={of_seg[3]:+.0f}"
                        f" stacked={of_seg[9]:.2f}({of_seg[10]:+.0f})"
                        f" abs_str={of_seg[16]:.2f} vol_climax={of_seg[18]:.2f}"
                        f" delta_div={of_seg[19]:.0f} flow_shift={of_seg[20]:.0f}"
                    )
            except Exception:
                pass
        typer.echo("")
    typer.echo("=== END ===")


# ---------------------------------------------------------------------------
# label-zone-outcomes — retroactive REV/CONT/SKIP labels from market history
# ---------------------------------------------------------------------------


@rl_app.command("label-zone-outcomes")
def label_zone_outcomes(
    days: int = typer.Option(7, help="Lookback window for stock_signals"),
    horizon_min: int = typer.Option(30, help="Forward replay horizon (minutes)"),
    min_R_to_count: float = typer.Option(0.5, help="Min absolute R for a side to win"),
    default_stop_ticks: int = typer.Option(25, help="Stop ticks when signal didn't carry one"),
    write_chunk: bool = typer.Option(True, help="Write live_episodes chunk for trainer"),
) -> None:
    """For every zone touch in stock_signals, replay forward N minutes via
    market_trades and label the actual outcome (REV-win / CONT-win / SKIP).

    Generates retroactive (obs, action, reward) tuples — bypasses the
    live-execution gap. Every zone touch becomes a training label, not just
    the ones we actually traded.

    Output:
      live_episodes/obs_ZONE<ts>.npy  (and rc/rr/lt/st chunks)
      → next pipeline cycle's merge-live folds it into the training pool.

    Example::

        python -m src.app rl label-zone-outcomes --days 7 --horizon-min 30
    """
    import base64
    import os
    import time as _time
    from datetime import timedelta

    import numpy as np
    from sqlalchemy import create_engine, text

    from .data.zone_replay import simulate_forward

    pw = os.environ.get("DB_PASSWORD", "")
    arnold_engine = create_engine(f"postgresql://arnold:{pw}@postgres:5432/arnold")
    market_engine = create_engine(f"postgresql://arnold:{pw}@postgres:5432/market")

    sig_sql = text(
        """
        SELECT id, ts, action, price, zone_center, zone_members, stop_ticks,
               cont_p, rev_p, confidence, observation_b64, observation_dim
        FROM stock_signals
        WHERE ts > NOW() - INTERVAL ':days days'
          AND observation_b64 IS NOT NULL
          AND price IS NOT NULL AND zone_center IS NOT NULL
        ORDER BY ts
        """.replace(":days days", f"{days} days")
    )
    typer.echo(f"Loading stock_signals from last {days} days...")
    with arnold_engine.connect() as conn:
        sigs = conn.execute(sig_sql).fetchall()
    typer.echo(f"Found {len(sigs)} signals with observations.")

    if not sigs:
        return

    target_dim = _target_obs_dim()
    if target_dim is None:
        from collections import Counter

        dim_counts = Counter(int(s.observation_dim or 0) for s in sigs if s.observation_dim)
        if dim_counts:
            target_dim = max(dim_counts, key=lambda d: (dim_counts[d], d))
    if target_dim:
        typer.echo(f"Target obs dim: {target_dim} (rows at other dims will be skipped).")

    obs_list: list[np.ndarray] = []
    rc_list: list[float] = []
    rr_list: list[float] = []
    lt_list: list[int] = []
    st_list: list[float] = []

    rev_wins = cont_wins = skips = errors = skipped_dim = 0
    rev_R_sum = cont_R_sum = 0.0

    tick_sql = text(
        """
        SELECT EXTRACT(EPOCH FROM ts) AS ts_s, price
        FROM market_trades
        WHERE ts >= :lo AND ts <= :hi
        ORDER BY ts
        """
    )

    for i, s in enumerate(sigs):
        if i % 250 == 0 and i > 0:
            typer.echo(f"  processed {i}/{len(sigs)} ...")
        try:
            obs = np.frombuffer(base64.b64decode(s.observation_b64), dtype=np.float32)
            if s.observation_dim and obs.size != s.observation_dim:
                continue
            if target_dim and obs.size != target_dim:
                skipped_dim += 1
                continue

            entry = float(s.price)
            zone_c = float(s.zone_center)
            stop_ticks = int(s.stop_ticks or default_stop_ticks)
            stop_ticks = max(8, min(50, stop_ticks))

            # approach direction: which way price came into the zone
            approach_up = entry < zone_c
            rev_side = "short" if approach_up else "long"
            cont_side = "long" if approach_up else "short"

            # pull tick window
            t_end = s.ts + timedelta(minutes=horizon_min)
            with market_engine.connect() as mconn:
                rows = mconn.execute(tick_sql, {"lo": s.ts, "hi": t_end}).fetchall()
            ticks = [(r.ts_s, float(r.price)) for r in rows]
            if not ticks:
                continue

            rev_out = simulate_forward(ticks, entry, rev_side, stop_ticks)
            cont_out = simulate_forward(ticks, entry, cont_side, stop_ticks)

            # Decide label
            if rev_out.pnl_r >= min_R_to_count and rev_out.pnl_r > cont_out.pnl_r:
                action_label = 1  # REV
                reward = float(rev_out.pnl_r)
                rev_wins += 1
                rev_R_sum += reward
                rc = 0.0
                rr = reward
            elif cont_out.pnl_r >= min_R_to_count and cont_out.pnl_r > rev_out.pnl_r:
                action_label = 0  # CONT
                reward = float(cont_out.pnl_r)
                cont_wins += 1
                cont_R_sum += reward
                rc = reward
                rr = 0.0
            else:
                # chop / no clear winner — skip from training pool
                skips += 1
                continue

            obs_list.append(obs.astype(np.float32))
            rc_list.append(np.float32(rc))
            rr_list.append(np.float32(rr))
            lt_list.append(int(action_label))
            st_list.append(np.float32(stop_ticks))

        except Exception:
            errors += 1
            continue

    typer.echo("")
    typer.echo("=== Outcome breakdown ===")
    typer.echo(f"  REV wins:   {rev_wins}   avg R = {rev_R_sum / max(rev_wins, 1):+.3f}")
    typer.echo(f"  CONT wins:  {cont_wins}  avg R = {cont_R_sum / max(cont_wins, 1):+.3f}")
    typer.echo(f"  SKIP/chop:  {skips}")
    typer.echo(f"  errors:     {errors}")
    typer.echo(f"  dim-skipped: {skipped_dim}")
    typer.echo(f"  TOTAL labelled: {len(obs_list)}")

    if not write_chunk or not obs_list:
        typer.echo("Not writing chunk (write_chunk=False or no labels).")
        return

    chunk_id = f"ZONE{int(_time.time())}"
    live_dir = _DATA_DIR / "live_episodes"
    live_dir.mkdir(parents=True, exist_ok=True)
    np.save(live_dir / f"obs_{chunk_id}.npy", np.stack(obs_list))
    np.save(live_dir / f"rc_{chunk_id}.npy", np.array(rc_list, dtype=np.float32))
    np.save(live_dir / f"rr_{chunk_id}.npy", np.array(rr_list, dtype=np.float32))
    np.save(live_dir / f"lt_{chunk_id}.npy", np.array(lt_list, dtype=np.int32))
    np.save(live_dir / f"st_{chunk_id}.npy", np.array(st_list, dtype=np.float32))
    typer.echo(f"\nWrote chunk {chunk_id}: {len(obs_list)} labelled tuples → live_episodes/")
    typer.echo("Next pipeline cycle (merge-live) will fold these into the training pool.")
