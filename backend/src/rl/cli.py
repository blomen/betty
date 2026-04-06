"""RL Trading Agent CLI — fetch, replay, train, eval."""

from __future__ import annotations

import datetime as _dt_mod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import torch
import typer

_ET = ZoneInfo("US/Eastern")

rl_app = typer.Typer(help="RL Trading Agent — fetch, replay, train, eval")


def _prepare_macro_data(macro_df, cot_df=None, stats_df=None) -> dict:
    """Convert raw macro parquet (VIX, DXY, US10Y, US2Y levels) into
    the dict format expected by extract_macro_features().

    Computes daily changes, yield curve spread, regime score,
    and merges weekly COT data (forward-filled to daily).
    """
    # Build COT lookup: forward-fill weekly COT to daily resolution
    cot_lookup: dict = {}
    if cot_df is not None and not cot_df.empty:
        # Reindex COT to daily frequency, forward-fill
        import pandas as pd
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


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

@rl_app.command()
def fetch(
    months: int = typer.Option(6, help="Number of months of history to fetch"),
    symbol: str = typer.Option("NQ", help="Symbol to fetch (default: NQ)"),
    only: Optional[str] = typer.Option(None, help="Comma-separated YYYY-MM months to fetch (overrides --months)"),
) -> None:
    """Fetch historical tick data and macro history from Databento / yfinance."""
    from src.rl.data.fetcher import fetch_ticks, fetch_macro_history

    if only:
        # Parse explicit month list and build date ranges
        from src.rl.data.fetcher import _to_utc, _month_ranges
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
        typer.echo(f"Run 'rl fetch' first to download historical data.", err=True)
        raise typer.Exit(1)

    df = pd.read_parquet(pfile)
    if "timestamp" not in df.columns:
        typer.echo(f"No 'timestamp' column in {pfile.name}", err=True)
        raise typer.Exit(1)

    df["_ts_et"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(_ET)
    df["_session_date"] = df["_ts_et"].apply(_assign_session_date)
    df = df.dropna(subset=["_session_date"])
    target_date = target.date()
    day_df = df[df["_session_date"] == target_date].drop(
        columns=["_session_date", "_ts_et"], errors="ignore"
    )

    if day_df.empty:
        typer.echo(f"No ticks found for {date} in {pfile.name}", err=True)
        raise typer.Exit(1)

    ticks = day_df.rename(columns={"timestamp": "ts"}).to_dict(orient="records")
    typer.echo(f"Replaying {len(ticks):,} ticks for {date} ...")

    session_dt = datetime(target_date.year, target_date.month, target_date.day, 12, 0, 0, tzinfo=_ET)

    engine = ReplayEngine()

    # Load precomputed levels if available
    from src.rl.data.session_store import load_summaries, compute_precomputed_levels

    summaries_path = _DATA_DIR / "session_summaries.json"
    summaries = load_summaries(summaries_path)
    precomputed = None
    if summaries:
        precomputed = compute_precomputed_levels(summaries, date)
        typer.echo(f"Loaded precomputed levels from {len(summaries)} sessions.")

    episodes = engine.replay_session(ticks, session_dt, precomputed_levels=precomputed)
    snapshot = engine.get_level_snapshot()

    typer.echo(f"\n{'='*60}")
    typer.echo(f"SESSION LEVELS — {date}")
    typer.echo(f"{'='*60}")

    sl = snapshot["session_levels"]
    for name, val in sl.items():
        if val is not None:
            typer.echo(f"  {name:20s}  {val:>12.2f}")

    typer.echo(f"\n{'─'*60}")
    typer.echo("VWAP BANDS")
    for name, val in snapshot["vwap"].items():
        if val is not None:
            typer.echo(f"  {name:20s}  {val:>12.2f}")

    typer.echo(f"\n{'─'*60}")
    typer.echo("VOLUME PROFILE")
    for name, val in snapshot["volume_profile"].items():
        if val is not None:
            typer.echo(f"  {name:20s}  {val:>12.2f}")

    typer.echo(f"\n{'─'*60}")
    typer.echo(f"ACTIVE LEVELS ({len(snapshot['active_levels'])} total)")
    # Sort by price for easy visual checking
    sorted_levels = sorted(snapshot["active_levels"], key=lambda x: x["price"], reverse=True)
    for lv in sorted_levels:
        typer.echo(f"  {lv['price']:>12.2f}  {lv['type']:20s}  {lv['name']}")

    typer.echo(f"\n{'─'*60}")
    typer.echo(f"FVGs: {len(snapshot['fvgs'])}  |  Order Blocks: {len(snapshot['order_blocks'])}")
    for fvg in snapshot["fvgs"][:5]:
        typer.echo(f"  FVG  {fvg['direction']:8s}  {fvg['low']:.2f} – {fvg['high']:.2f}")
    for ob in snapshot["order_blocks"][:5]:
        typer.echo(f"  OB   {ob['direction']:8s}  {ob['low']:.2f} – {ob['high']:.2f}")

    typer.echo(f"\n{'─'*60}")
    typer.echo(f"EPISODES: {len(episodes)} level touches detected")
    for i, ep in enumerate(episodes[:10]):
        typer.echo(f"  {i+1}. {ep.level_type:20s}  @ {ep.touch_ts}  best={ep.best_action}")

    # Also write JSON for frontend consumption
    out_path = _DATA_DIR / f"levels_{date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)
    typer.echo(f"\nJSON written to: {out_path}")


# ---------------------------------------------------------------------------
# precompute
# ---------------------------------------------------------------------------

@rl_app.command()
def precompute(
    all_months: bool = typer.Option(False, "--all", help="Process all Parquet files"),
    month: Optional[str] = typer.Option(None, help="Process a specific month YYYY-MM"),
) -> None:
    """Build session summaries from tick data for cross-session level computation."""
    import pandas as pd
    from src.rl.data.fetcher import TICKS_DIR
    from src.rl.data.session_store import build_session_summary, save_summaries, load_summaries

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

    Returns (n_episodes, n_sessions).
    """
    import numpy as np
    import pandas as pd
    from pathlib import Path
    from datetime import datetime

    from src.rl.data.replay_engine import ReplayEngine
    from src.rl.data.session_store import compute_precomputed_levels
    from src.rl.features.observation import augment_observation, build_position_state

    pfile = Path(pfile_path)
    out_dir = Path(chunk_dir)

    # Load GBT in this subprocess if needed
    gbt_model = None
    if gbt_path:
        from src.rl.agent.gbt_model import GBTModel
        gbt_model = GBTModel.load(Path(gbt_path))

    engine = ReplayEngine(macro_data=macro_data)

    df = pd.read_parquet(pfile)
    if "timestamp" not in df.columns:
        return 0, 0

    df["_ts_et"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(_ET)
    df["_session_date"] = df["_ts_et"].apply(_assign_session_date)
    df = df.dropna(subset=["_session_date"])
    df_renamed = df.rename(columns={"timestamp": "ts"})
    dates = sorted(df_renamed["_session_date"].unique())

    # For large files, group by session upfront and release the full DataFrame
    is_large = len(df_renamed) > 1_000_000
    session_groups = {}
    if is_large:
        for session_date in dates:
            session_groups[session_date] = df_renamed[df_renamed["_session_date"] == session_date].drop(
                columns=["_session_date", "_ts_et"], errors="ignore"
            )
        del df, df_renamed  # release ~600MB
        import gc; gc.collect()

    month_obs, month_rc, month_rr = [], [], []
    month_lt, month_st, month_be, month_lc = [], [], [], []

    session_count = 0
    prior_levels = None

    for session_date in dates:
        if is_large:
            day_df = session_groups.pop(session_date)  # pop to free memory after use
        else:
            day_df = df_renamed[df_renamed["_session_date"] == session_date].drop(
                columns=["_session_date", "_ts_et"], errors="ignore"
            )
        ticks = day_df.to_dict(orient="records")
        del day_df  # free session DataFrame immediately
        if not ticks:
            continue

        session_dt = datetime(
            session_date.year, session_date.month, session_date.day,
            12, 0, 0, tzinfo=_ET,
        )

        precomputed = None
        if summaries:
            precomputed = compute_precomputed_levels(summaries, str(session_date))

        try:
            episodes = engine.replay_session(
                ticks, session_dt,
                prior_session_levels=prior_levels,
                precomputed_levels=precomputed,
            )
        except Exception:
            continue

        prior_levels = engine.get_prior_session_for_chaining()

        # Reset weekly/monthly at boundaries
        idx = dates.tolist().index(session_date) + 1 if hasattr(dates, "tolist") else None
        if idx and idx < len(dates):
            nd = dates[idx]
            if hasattr(nd, "weekday") and nd.weekday() == 0:
                prior_levels["weekly_high"] = None
                prior_levels["weekly_low"] = None
            if hasattr(nd, "day") and nd.day == 1:
                prior_levels["monthly_high"] = None
                prior_levels["monthly_low"] = None

        for ep in episodes:
            obs = ep.observation
            if gbt_model is not None:
                gbt_forecast = gbt_model.predict_full(obs)
                pos_state = build_position_state()
                obs = augment_observation(obs, gbt_forecast, pos_state)
            month_obs.append(obs)
            month_rc.append(ep.reward_continuation)
            month_rr.append(ep.reward_reversal)
            month_lt.append(ep.level_type)
            month_st.append(ep.optimal_stop_ticks)
            month_be.append(float(ep.breakeven_reached))
            month_lc.append(float(ep.levels_captured_best))

        session_count += 1

    n_eps = len(month_obs)
    if n_eps > 0:
        np.save(out_dir / f"obs_{chunk_idx:04d}.npy", np.array(month_obs, dtype=np.float32))
        np.save(out_dir / f"rc_{chunk_idx:04d}.npy", np.array(month_rc, dtype=np.float32))
        np.save(out_dir / f"rr_{chunk_idx:04d}.npy", np.array(month_rr, dtype=np.float32))
        np.save(out_dir / f"lt_{chunk_idx:04d}.npy", np.array(month_lt))
        np.save(out_dir / f"st_{chunk_idx:04d}.npy", np.array(month_st, dtype=np.float32))
        np.save(out_dir / f"be_{chunk_idx:04d}.npy", np.array(month_be, dtype=np.float32))
        np.save(out_dir / f"lc_{chunk_idx:04d}.npy", np.array(month_lc, dtype=np.float32))

    return n_eps, len(dates)


@rl_app.command()
def replay(
    all_months: bool = typer.Option(False, "--all", help="Replay all Parquet files in TICKS_DIR"),
    month: Optional[str] = typer.Option(None, help="Replay a specific month YYYY-MM"),
    gbt: Optional[str] = typer.Option(None, help="GBT model for augmented observations (hybrid GBT+DQN)"),
    workers: int = typer.Option(0, help="Parallel workers (0 = auto, 1 = sequential)"),
    clean: bool = typer.Option(False, help="Delete existing chunks before replaying (fresh start)"),
) -> None:
    """Replay tick sessions through ReplayEngine and save episodes as .npy files.

    With --gbt: produces augmented episodes (base + 8 GBT forecast + 8 position state).
    Without --gbt: produces base episodes (market features only).
    Uses parallel workers for multi-core replay (default: auto = CPU count / 2).
    """
    import numpy as np
    import pandas as pd
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import multiprocessing

    from src.rl.data.fetcher import TICKS_DIR, MACRO_DIR
    from src.rl.data.normalization import RunningNormalizer
    from src.rl.features.observation import (
        OBSERVATION_DIM, AUGMENTED_OBSERVATION_DIM,
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
            typer.echo(f"Loaded macro data: {len(macro_data)} days" +
                       (f" (COT: {len(cot_df)} weeks)" if cot_df is not None else " (no COT)") + ".")
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
        from src.rl.agent.gbt_model import GBTModel
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
    existing_chunks = set(chunk_dir.glob("obs_*.npy"))

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
                            pfile_path=str(pfile), chunk_dir=str(chunk_dir),
                            chunk_idx=idx, macro_data=macro_data,
                            summaries=summaries, gbt_path=gbt_path_str,
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
                        pfile_path=str(pfile), chunk_dir=str(chunk_dir),
                        chunk_idx=idx, macro_data=macro_data,
                        summaries=summaries, gbt_path=gbt_path_str,
                    )
                    typer.echo(f"  {pfile.name}: {n_eps} episodes across {n_sessions} session(s)")
                except Exception as exc:
                    typer.echo(f"  {pfile.name}: FAILED — {exc}")

        # Large files: subprocess-isolated (OOM kills only the subprocess)
        if large_todo:
            typer.echo(f"\nSubprocess replay for {len(large_todo)} large file(s)...")
            for idx, pfile in large_todo:
                try:
                    with ProcessPoolExecutor(max_workers=1) as single:
                        future = single.submit(
                            _replay_single_file,
                            pfile_path=str(pfile), chunk_dir=str(chunk_dir),
                            chunk_idx=idx, macro_data=macro_data,
                            summaries=summaries, gbt_path=gbt_path_str,
                        )
                        n_eps, n_sessions = future.result(timeout=7200)
                    typer.echo(f"  {pfile.name}: {n_eps} episodes across {n_sessions} session(s)")
                except Exception as exc:
                    typer.echo(f"  {pfile.name}: FAILED — {exc}")

    # Concatenate all chunks from disk (including previously completed + new)
    chunk_indices = sorted(
        int(f.stem.split("_")[1]) for f in chunk_dir.glob("obs_*.npy")
    )
    if not chunk_indices:
        typer.echo("No episodes generated. Check tick data and replay engine.")
        raise typer.Exit(1)

    n_chunks = len(chunk_indices)
    total_episodes = sum(len(np.load(chunk_dir / f"obs_{i:04d}.npy")) for i in chunk_indices)
    typer.echo(f"\nConcatenating {n_chunks} chunks ({total_episodes} episodes)...")

    obs_array = np.concatenate([np.load(chunk_dir / f"obs_{i:04d}.npy") for i in chunk_indices])
    np.save(episodes_dir / "observations.npy", obs_array)

    np.save(episodes_dir / "rewards_cont.npy",
            np.concatenate([np.load(chunk_dir / f"rc_{i:04d}.npy") for i in chunk_indices]))
    np.save(episodes_dir / "rewards_rev.npy",
            np.concatenate([np.load(chunk_dir / f"rr_{i:04d}.npy") for i in chunk_indices]))
    np.save(episodes_dir / "level_types.npy",
            np.concatenate([np.load(chunk_dir / f"lt_{i:04d}.npy", allow_pickle=True) for i in chunk_indices]))
    np.save(episodes_dir / "stop_targets.npy",
            np.concatenate([np.load(chunk_dir / f"st_{i:04d}.npy") for i in chunk_indices]))
    np.save(episodes_dir / "breakeven_reached.npy",
            np.concatenate([np.load(chunk_dir / f"be_{i:04d}.npy") for i in chunk_indices]))
    np.save(episodes_dir / "levels_captured.npy",
            np.concatenate([np.load(chunk_dir / f"lc_{i:04d}.npy") for i in chunk_indices]))

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
    live_rc = np.concatenate([np.load(live_dir / f"rc_{f.stem.split('_')[1]}.npy") for f in live_chunks])
    live_rr = np.concatenate([np.load(live_dir / f"rr_{f.stem.split('_')[1]}.npy") for f in live_chunks])
    live_lt = np.concatenate([np.load(live_dir / f"lt_{f.stem.split('_')[1]}.npy", allow_pickle=True) for f in live_chunks])
    live_st = np.concatenate([np.load(live_dir / f"st_{f.stem.split('_')[1]}.npy") for f in live_chunks])

    typer.echo(f"Live episodes: {len(live_obs)} ({live_obs.shape[1]}-dim)")

    # Load existing main episodes
    main_obs_path = episodes_dir / "observations.npy"
    if main_obs_path.exists():
        main_obs = np.load(main_obs_path)
        main_rc = np.load(episodes_dir / "rewards_cont.npy")
        main_rr = np.load(episodes_dir / "rewards_rev.npy")
        main_lt = np.load(episodes_dir / "level_types.npy", allow_pickle=True)
        main_st = np.load(episodes_dir / "stop_targets.npy")
        typer.echo(f"Main episodes: {len(main_obs)} ({main_obs.shape[1]}-dim)")

        # Check dim compatibility
        if live_obs.shape[1] != main_obs.shape[1]:
            typer.echo(f"Dimension mismatch: live={live_obs.shape[1]} vs main={main_obs.shape[1]}. Cannot merge.", err=True)
            raise typer.Exit(1)

        # Concatenate
        merged_obs = np.concatenate([main_obs, live_obs])
        merged_rc = np.concatenate([main_rc, live_rc])
        merged_rr = np.concatenate([main_rr, live_rr])
        merged_lt = np.concatenate([main_lt, live_lt])
        merged_st = np.concatenate([main_st, live_st])
    else:
        merged_obs = live_obs
        merged_rc = live_rc
        merged_rr = live_rr
        merged_lt = live_lt
        merged_st = live_st

    # Save merged
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

    typer.echo(f"Merged: {len(merged_obs)} total episodes ({len(live_obs)} live + {len(merged_obs) - len(live_obs)} historical)")

    # Clean up live chunks (already merged)
    for f in live_dir.glob("*.npy"):
        f.unlink()
    typer.echo("Live episode chunks cleaned up.")


# ---------------------------------------------------------------------------
# train
# ---------------------------------------------------------------------------

@rl_app.command()
def train(
    epochs: int = typer.Option(100, help="Number of training epochs"),
    checkpoint: str = typer.Option("v1", help="Checkpoint name for saved model"),
    resume: bool = typer.Option(False, help="Resume from existing checkpoint"),
) -> None:
    """Train the DQN agent on replayed episodes."""
    import numpy as np

    from src.rl.agent.dqn import DQNAgent
    from src.rl.data.normalization import RunningNormalizer
    from src.rl.config import Action, BATCH_SIZE, REWARD_CLIP_MIN, REWARD_CLIP_MAX, REWARD_NORMALIZE
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
    level_types = np.load(episodes_dir / "level_types.npy", allow_pickle=True)
    stop_path = episodes_dir / "stop_targets.npy"
    stop_targets = np.load(stop_path) if stop_path.exists() else np.full(len(observations), 10.0, dtype=np.float32)

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
        normalizer.load(normalizer_path)
        typer.echo(f"Loaded normalizer (count={normalizer.count})")
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

    # Resume from checkpoint if requested
    start_epoch = 1
    model_path = models_dir / f"dqn_{checkpoint}.pt"
    if resume and model_path.exists():
        import torch as _torch
        ckpt = _torch.load(model_path, weights_only=False, map_location="cpu")
        agent.load(model_path)
        start_epoch = ckpt.get("epoch", 0) + 1
        typer.echo(f"Resumed from {model_path} (epoch {start_epoch - 1}, "
                    f"epsilon={agent.epsilon:.3f}, steps={agent.train_steps})")
    elif resume:
        typer.echo(f"No checkpoint found at {model_path} — starting fresh.")

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

    # Training loop
    remaining = epochs - start_epoch + 1
    typer.echo(f"\nTraining for {remaining} epochs ({start_epoch}-{epochs}) x {steps_per_epoch} steps/epoch ...")
    typer.echo(f"LR: {scheduler.get_last_lr()[0]:.2e} -> 1e-5 cosine | Epsilon: {agent.epsilon:.2f} -> 0.05")
    for epoch in range(start_epoch, epochs + 1):
        epoch_loss = 0.0
        for _step in range(steps_per_epoch):
            loss = agent.train_step()
            scheduler.step()
            epoch_loss += loss
        avg_loss = epoch_loss / steps_per_epoch
        if epoch % max(1, epochs // 20) == 0 or epoch == 1:
            lr = scheduler.get_last_lr()[0]
            typer.echo(f"  Epoch {epoch:>5}/{epochs}  loss={avg_loss:.4f}  epsilon={agent.epsilon:.3f}  lr={lr:.2e}")
        # Checkpoint every 5 epochs so progress is never lost
        if epoch % 5 == 0:
            ckpt_path = models_dir / f"dqn_{checkpoint}.pt"
            agent.save(ckpt_path, epoch=epoch)
            typer.echo(f"  [checkpoint saved: epoch {epoch}]")

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
    from src.rl.data.normalization import RunningNormalizer
    from src.rl.config import REWARD_CLIP_MIN, REWARD_CLIP_MAX
    from src.rl.features.observation import OBSERVATION_DIM

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
    level_types = np.load(episodes_dir / "level_types.npy", allow_pickle=True)
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
    typer.echo(f"\n  Baselines:")
    typer.echo(f"    always-REV:  avg_R={test_rr.mean():+.3f}")
    typer.echo(f"    always-CONT: avg_R={test_rc.mean():+.3f}")
    typer.echo(f"    oracle:      avg_R={np.maximum(test_rc, test_rr).mean():+.3f}")

    # Feature importance
    typer.echo(f"\n  Top 15 features:")
    segments = [
        (0, 31, "Zone composition"), (31, 52, "Orderflow"), (52, 116, "Dow/Session"),
        (116, 154, "TPO"), (154, 169, "Candle window"), (169, 173, "Zone features"),
        (173, 178, "Confluence"), (178, 189, "Macro"), (189, 194, "Exchange stats"),
        (194, 208, "Setup detection"), (208, 221, "AMT"), (221, 241, "Micro"),
        (241, 242, "Approach dir"), (242, 249, "Execution ctx"),
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
    skip_threshold: float = typer.Option(0.15, help="Min Q-spread to trade (below = SKIP, model uncertain)"),
) -> None:
    """Evaluate the trained DQN agent on the test split.

    The model predicts Q(CONT) and Q(REV). If |Q_cont - Q_rev| < skip_threshold,
    the model is uncertain about direction and the episode is SKIPped.
    """
    import numpy as np

    from src.rl.agent.dqn import DQNAgent
    from src.rl.agent.evaluate import compute_metrics, print_evaluation_report
    from src.rl.data.normalization import RunningNormalizer
    from src.rl.config import Action, EPSILON_END
    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR
    model_path = models_dir / f"dqn_{checkpoint}.pt"

    if not model_path.exists():
        typer.echo(f"Model not found: {model_path}. Run 'rl train' first.", err=True)
        raise typer.Exit(1)

    # Load episodes
    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy found. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    observations = np.load(episodes_dir / "observations.npy")
    rewards_cont = np.load(episodes_dir / "rewards_cont.npy")
    rewards_rev = np.load(episodes_dir / "rewards_rev.npy")
    level_types = np.load(episodes_dir / "level_types.npy", allow_pickle=True)

    n = len(observations)
    obs_dim = observations.shape[1]

    # Load normalizer with actual obs dim
    normalizer_path = episodes_dir / "normalizer.json"
    normalizer = RunningNormalizer(dim=obs_dim)
    if normalizer_path.exists():
        normalizer.load(normalizer_path)

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

        episode_dicts.append({
            "action": action,
            "reward": reward,
            "level_type": str(test_lt[i]),
        })

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
    from src.rl.data.normalization import RunningNormalizer
    from src.rl.data.replay_engine import ReplayEngine
    from src.rl.data.fetcher import TICKS_DIR, MACRO_DIR
    from src.rl.data.session_store import load_summaries, compute_precomputed_levels
    from src.rl.session_manager import SessionManager, PositionSide
    from src.rl.features.observation import OBSERVATION_DIM
    from src.rl.config import TICK_SIZE

    models_dir = _MODELS_DIR

    # Try GBT first, fall back to DQN
    gbt_path = models_dir / f"gbt_{checkpoint}.joblib"
    dqn_path = models_dir / f"dqn_{checkpoint}.pt"

    if gbt_path.exists():
        network = GBTModel.load(gbt_path)
        typer.echo(f"Loaded GBT model: {gbt_path}")
    elif dqn_path.exists():
        network = DQNetwork(input_dim=OBSERVATION_DIM)
        ckpt = torch.load(dqn_path, weights_only=False, map_location="cpu")
        network.load_state_dict(ckpt["q_network"])
        network.eval()
        typer.echo(f"Loaded DQN model: {dqn_path}")
    else:
        typer.echo(f"No model found: tried {gbt_path} and {dqn_path}", err=True)
        raise typer.Exit(1)

    # Load normalizer
    normalizer = RunningNormalizer(dim=OBSERVATION_DIM)
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
                session_date.year, session_date.month, session_date.day,
                12, 0, 0, tzinfo=_ET,
            )

            precomputed = None
            if summaries:
                precomputed = compute_precomputed_levels(summaries, str(session_date))

            try:
                episodes = engine.replay_session(
                    ticks, session_dt,
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

                signal = sm.on_level_touch(state, price)

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
    typer.echo(f"\n{'='*60}")
    typer.echo(f"  SESSION MANAGER BACKTEST REPORT")
    typer.echo(f"{'='*60}")

    total_trades = sum(s["trades"] for s in all_sessions)
    total_pnl = sum(s["total_pnl_r"] for s in all_sessions)
    total_winners = sum(s["winners"] for s in all_sessions)
    total_losers = sum(s["losers"] for s in all_sessions)
    total_flips = sum(s["flips"] for s in all_sessions)
    sessions_positive = sum(1 for s in all_sessions if s["total_pnl_r"] > 0)

    wr = total_winners / max(total_trades, 1) * 100
    avg_session_pnl = total_pnl / max(len(all_sessions), 1)

    typer.echo(f"  Sessions         : {len(all_sessions)}")
    typer.echo(f"  Sessions +       : {sessions_positive} ({sessions_positive/max(len(all_sessions),1)*100:.0f}%)")
    typer.echo(f"  Total trades     : {total_trades}")
    typer.echo(f"  Winners          : {total_winners}")
    typer.echo(f"  Losers           : {total_losers}")
    typer.echo(f"  Position flips   : {total_flips}")
    typer.echo(f"  Win rate         : {wr:.1f}%")
    typer.echo(f"  Total P&L        : {total_pnl:+.1f} R")
    typer.echo(f"  Avg session P&L  : {avg_session_pnl:+.2f} R")
    typer.echo(f"{'='*60}")

    # Top 10 best and worst sessions
    sorted_sessions = sorted(all_sessions, key=lambda s: s["total_pnl_r"], reverse=True)
    typer.echo(f"\n  BEST SESSIONS:")
    for s in sorted_sessions[:5]:
        typer.echo(f"    {s['date']}  {s['total_pnl_r']:+6.1f}R  trades={s['trades']}  flips={s['flips']}")
    typer.echo(f"\n  WORST SESSIONS:")
    for s in sorted_sessions[-5:]:
        typer.echo(f"    {s['date']}  {s['total_pnl_r']:+6.1f}R  trades={s['trades']}  flips={s['flips']}")


# ---------------------------------------------------------------------------
# label-setups
# ---------------------------------------------------------------------------

@rl_app.command("label-setups")
def label_setups() -> None:
    """Label all episodes with setup types (rule-based + clustering)."""
    import numpy as np
    from collections import Counter

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
    level_types = np.load(episodes_dir / "level_types.npy", allow_pickle=True)

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

        ep_dict = {
            "zone_types": zone_types,
            "approach_direction": approach_dir,
            "reward_cont": float(rewards_cont[i]),
            "reward_rev": float(rewards_rev[i]),
            "has_single_print": has_sp,
            "forward_reversal_speed": fwd_rev_speed,
            # Fields used by labeler but not always available from obs alone
            "price_vs_value": float(observations[i, 52]),  # struct_0: price_vs_vwap
            "has_gap": False,  # Cannot determine from observation vector
            "ib_closed": bool(observations[i, 57] > 0),  # struct_5: IB distance > 0
            "delta_ratio": float(observations[i, 31]),  # orderflow index 0
        }

        labels[i] = label_episode(ep_dict).value

    # Print distribution
    counts = Counter(labels)
    typer.echo(f"\n  Setup Label Distribution:")
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
            [all_level_types[j].value for j in range(31) if zone_comp[idx][j] > 0.5]
            for idx in unknown_idx
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
        typer.echo(f"\n  Updated Distribution (after clustering):")
        for setup_type in SetupType:
            c = counts.get(setup_type.value, 0)
            pct = c / n * 100 if n > 0 else 0
            typer.echo(f"    {setup_type.value:30s} {c:>7,}  ({pct:5.1f}%)")

    # Save
    out_path = episodes_dir / "setup_labels.npy"
    np.save(out_path, labels)
    typer.echo(f"\n  Saved setup labels to {out_path}")


# ---------------------------------------------------------------------------
# train-narrative-gbt
# ---------------------------------------------------------------------------

@rl_app.command("train-narrative-gbt")
def train_narrative_gbt(
    checkpoint: str = typer.Option("v5", help="Checkpoint name"),
    trees: int = typer.Option(500, help="Number of trees"),
    depth: int = typer.Option(5, help="Max depth"),
    lr: float = typer.Option(0.05, help="Learning rate"),
) -> None:
    """Train the Narrative GBT on slow features -> day type + setup probs."""
    import numpy as np

    from src.rl.agent.narrative_gbt import NarrativeGBT
    from src.rl.labeling.setup_types import NUM_SETUP_TYPES, SetupType

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy in {episodes_dir}. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    labels_path = episodes_dir / "setup_labels.npy"
    if not labels_path.exists():
        typer.echo(f"No setup_labels.npy. Run 'rl label-setups' first.", err=True)
        raise typer.Exit(1)

    observations = np.load(obs_path)
    setup_labels_raw = np.load(labels_path, allow_pickle=True)

    n = len(observations)
    typer.echo(f"Loaded {n:,} episodes ({observations.shape[1]}-dim)")

    # Subsample to fit in memory (LightGBM duplicates data per thread)
    MAX_GBT_SAMPLES = 250_000
    if n > MAX_GBT_SAMPLES:
        rng = np.random.RandomState(42)
        idx = rng.choice(n, MAX_GBT_SAMPLES, replace=False)
        idx.sort()
        observations = observations[idx]
        setup_labels_raw = setup_labels_raw[idx]
        n = MAX_GBT_SAMPLES
        typer.echo(f"Subsampled to {n:,} episodes for memory safety.")

    # Extract narrative-relevant features:
    #   structure  52:116  (64)
    #   TPO       116:154  (38)
    #   macro     178:189  (11)
    #   AMT       208:228  (20)
    #   AMT_dyn   228:248  (20)
    #   Total: 153 dims
    X = np.concatenate([
        observations[:, 52:116],    # structure (64)
        observations[:, 116:154],   # TPO (38)
        observations[:, 178:189],   # macro (11)
        observations[:, 208:228],   # AMT (20)
        observations[:, 228:248],   # AMT dynamics (20)
    ], axis=1)
    typer.echo(f"Narrative features: {X.shape[1]} dims")

    # Day type labels: AMT day type one-hot at AMT indices 0-5 → obs indices 208:214
    day_type_onehot = observations[:, 208:214]
    day_type_labels = np.argmax(day_type_onehot, axis=1).astype(np.int32)
    n_day_types = len(np.unique(day_type_labels))
    typer.echo(f"Day types: {n_day_types} classes")

    # Setup labels: convert string labels → binary matrix (N, NUM_SETUP_TYPES)
    setup_names = [s.value for s in SetupType if s != SetupType.UNKNOWN]
    setup_binary = np.zeros((n, NUM_SETUP_TYPES), dtype=np.int32)
    for i, lbl in enumerate(setup_labels_raw):
        for j, name in enumerate(setup_names):
            if lbl == name:
                setup_binary[i, j] = 1
                break

    pos_counts = setup_binary.sum(axis=0)
    for j, name in enumerate(setup_names):
        typer.echo(f"  Setup '{name}': {int(pos_counts[j]):,} positive samples")

    # Train
    model = NarrativeGBT()
    typer.echo(f"\nTraining NarrativeGBT (engine={model.engine}, trees={trees}, depth={depth}, lr={lr})...")
    metrics = model.train(
        X=X,
        day_type_labels=day_type_labels,
        setup_labels=setup_binary,
        n_estimators=trees,
        max_depth=depth,
        learning_rate=lr,
    )

    # Print metrics
    typer.echo(f"\n  Results:")
    typer.echo(f"    Engine           : {metrics['engine']}")
    typer.echo(f"    Alive features   : {metrics['alive_features']} / {metrics['total_features']}")
    typer.echo(f"    Day type acc     : {metrics['day_type_accuracy']}%")
    typer.echo(f"    Trained setups   : {metrics['trained_setups']}")
    typer.echo(f"    Skipped setups   : {metrics['skipped_setups']}")

    # Feature importance
    top_features = model.feature_importance(top_n=10)
    typer.echo(f"\n  Top 10 feature importances (day type head):")
    for idx, imp in top_features:
        typer.echo(f"    feature[{idx:3d}] = {imp:.4f}")

    # Save
    save_path = models_dir / f"narrative_gbt_{checkpoint}.joblib"
    model.save(save_path)
    typer.echo(f"\n  Saved to {save_path}")


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

    from src.rl.agent.narrative_gbt import NarrativeGBT
    from src.rl.agent.trigger_gbt import TriggerGBT
    from src.rl.features.passthrough_features import extract_passthrough

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
    typer.echo(f"Loaded {n:,} episodes ({observations.shape[1]}-dim)")

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
        if breakeven_reached is not None:
            breakeven_reached = breakeven_reached[idx]
        if levels_captured is not None:
            levels_captured = levels_captured[idx]
        n = MAX_GBT_SAMPLES
        typer.echo(f"Subsampled to {n:,} episodes for memory safety.")

    # --- Narrative augment: setup_probs from NarrativeGBT if available ---
    narrative_path = models_dir / f"narrative_gbt_{checkpoint}.joblib"
    setup_probs = None
    if narrative_path.exists():
        typer.echo(f"Loading NarrativeGBT from {narrative_path}...")
        narrative_model = NarrativeGBT.load(narrative_path)
        # Extract the same narrative features used during training
        narrative_feats = np.concatenate([
            observations[:, 52:116],    # structure (64)
            observations[:, 116:154],   # TPO (38)
            observations[:, 178:189],   # macro (11)
            observations[:, 208:228],   # AMT (20)
            observations[:, 228:248],   # AMT dynamics (20)
        ], axis=1)
        setup_probs = narrative_model.predict_setup_probs_batch(narrative_feats)  # (N, 8)
        typer.echo(f"  Setup probs shape: {setup_probs.shape}")
    else:
        typer.echo(f"No NarrativeGBT at {narrative_path} — training without narrative augment.")

    # --- Build trigger feature vector ---
    # Trigger-relevant raw features from observation:
    #   zone_comp     0:31   (31)
    #   orderflow    31:52   (21)
    #   candles     154:169  (15)
    #   zone_feat   169:173  (4)
    #   zone_conf   173:178  (5)
    #   micro       248:268  (20)
    #   approach    268:269  (1)
    # Total raw: 97 dims
    raw_trigger = np.concatenate([
        observations[:, 0:31],      # zone_comp (31)
        observations[:, 31:52],     # orderflow (21)
        observations[:, 154:169],   # candles (15)
        observations[:, 169:173],   # zone_feat (4)
        observations[:, 173:178],   # zone_conf (5)
        observations[:, 248:268],   # micro (20)
        observations[:, 268:269],   # approach (1)
    ], axis=1)

    # Passthrough features (10 high-importance raw features)
    passthrough = np.stack([extract_passthrough(obs) for obs in observations])

    # Assemble full trigger feature vector
    parts = [raw_trigger, passthrough]
    if setup_probs is not None:
        parts.append(setup_probs)
    X = np.concatenate(parts, axis=1)
    typer.echo(f"Trigger features: {X.shape[1]} dims (raw={raw_trigger.shape[1]} + passthrough={passthrough.shape[1]}"
               + (f" + narrative={setup_probs.shape[1]}" if setup_probs is not None else "") + ")")

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
    typer.echo(f"\n  Results:")
    typer.echo(f"    Engine           : {metrics['engine']}")
    typer.echo(f"    Alive features   : {metrics['alive_features']} / {metrics['total_features']}")
    typer.echo(f"    Direction acc    : {metrics['direction_accuracy']}%")
    if "breakeven_accuracy" in metrics:
        typer.echo(f"    Breakeven acc    : {metrics['breakeven_accuracy']}%")

    # Feature importance
    top_features = model.feature_importance(top_n=10)
    typer.echo(f"\n  Top 10 feature importances (direction head):")
    for idx, imp in top_features:
        typer.echo(f"    feature[{idx:3d}] = {imp:.4f}")

    # Save
    save_path = models_dir / f"trigger_gbt_{checkpoint}.joblib"
    model.save(save_path)
    typer.echo(f"\n  Saved to {save_path}")
