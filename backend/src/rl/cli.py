"""RL Trading Agent CLI — fetch, replay, train, eval."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer

rl_app = typer.Typer(help="RL Trading Agent — fetch, replay, train, eval")

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
) -> None:
    """Fetch historical tick data and macro history from Databento / yfinance."""
    from src.rl.data.fetcher import fetch_ticks, fetch_macro_history

    end = datetime.now(tz=timezone.utc)
    # Extra month for bootstrap
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

    df["_date"] = pd.to_datetime(df["timestamp"]).dt.date
    target_date = target.date()
    day_df = df[df["_date"] == target_date].drop(columns=["_date"])

    if day_df.empty:
        typer.echo(f"No ticks found for {date} in {pfile.name}", err=True)
        raise typer.Exit(1)

    ticks = day_df.rename(columns={"timestamp": "ts"}).to_dict(orient="records")
    typer.echo(f"Replaying {len(ticks):,} ticks for {date} ...")

    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("US/Eastern")
    session_dt = datetime(target_date.year, target_date.month, target_date.day, 12, 0, 0, tzinfo=_ET)

    engine = ReplayEngine()
    episodes = engine.replay_session(ticks, session_dt)
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

    sp = snapshot["swing_points"]
    if sp:
        typer.echo(f"\n{'─'*60}")
        typer.echo(f"SWING STRUCTURE: {sp.get('structure', 'unknown')}")
        for k in ["swing_high", "swing_low", "last_hh", "last_hl", "last_lh", "last_ll"]:
            if sp.get(k) is not None:
                typer.echo(f"  {k:20s}  {sp[k]:>12.2f}")

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

    from zoneinfo import ZoneInfo
    import datetime as _dt_mod
    _ET = ZoneInfo("US/Eastern")

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

        def _assign_session_date(ts_et):
            t = ts_et.time()
            d = ts_et.date()
            if t.hour >= 18:
                d = d + _dt_mod.timedelta(days=1)
                while d.weekday() >= 5:
                    d = d + _dt_mod.timedelta(days=1)
            if d.weekday() >= 5:
                return None
            return d

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
# replay
# ---------------------------------------------------------------------------

@rl_app.command()
def replay(
    all_months: bool = typer.Option(False, "--all", help="Replay all Parquet files in TICKS_DIR"),
    month: Optional[str] = typer.Option(None, help="Replay a specific month YYYY-MM"),
) -> None:
    """Replay tick sessions through ReplayEngine and save episodes as .npy files."""
    import numpy as np
    import pandas as pd

    from src.rl.data.fetcher import TICKS_DIR, MACRO_DIR
    from src.rl.data.replay_engine import ReplayEngine
    from src.rl.data.normalization import RunningNormalizer
    from src.rl.features.observation import OBSERVATION_DIM

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

    typer.echo(f"Found {len(parquet_files)} tick file(s) to replay.")

    # Load macro data
    macro_path = MACRO_DIR / "macro_daily.parquet"
    macro_data: dict = {}
    if macro_path.exists():
        try:
            macro_df = pd.read_parquet(macro_path)
            for date_idx, row in macro_df.iterrows():
                date_str = str(date_idx)[:10]
                macro_data[date_str] = row.to_dict()
            typer.echo(f"Loaded macro data: {len(macro_data)} days.")
        except Exception as exc:
            typer.echo(f"Warning: could not load macro data: {exc}")
    else:
        typer.echo("No macro_daily.parquet found — macro features will be zeroed.")

    normalizer = RunningNormalizer(dim=OBSERVATION_DIM)
    engine = ReplayEngine(macro_data=macro_data)

    all_observations: list[np.ndarray] = []
    all_rewards_long: list[float] = []
    all_rewards_short: list[float] = []
    all_level_types: list[str] = []

    total_episodes = 0

    for pfile in parquet_files:
        try:
            df = pd.read_parquet(pfile)
        except Exception as exc:
            typer.echo(f"  Skipping {pfile.name}: {exc}")
            continue

        # Group ticks by date
        if "timestamp" in df.columns:
            df["_date"] = pd.to_datetime(df["timestamp"]).dt.date
        else:
            typer.echo(f"  Skipping {pfile.name}: no 'timestamp' column")
            continue

        # Convert to list of dicts with 'ts' key
        df_renamed = df.rename(columns={"timestamp": "ts"})
        dates = sorted(df_renamed["_date"].unique())

        session_episodes = 0
        prior_levels: dict | None = None  # Chain session levels across days

        for session_date in dates:
            day_df = df_renamed[df_renamed["_date"] == session_date].drop(columns=["_date"])
            ticks = day_df.to_dict(orient="records")

            if not ticks:
                continue

            # session_date as ET noon — ensures .astimezone(ET).date() gives the correct day
            # (UTC midnight would convert to previous day in ET due to UTC-4/5 offset)
            from zoneinfo import ZoneInfo
            _ET = ZoneInfo("US/Eastern")
            session_dt = datetime(session_date.year, session_date.month, session_date.day, 12, 0, 0, tzinfo=_ET)

            try:
                episodes = engine.replay_session(ticks, session_dt, prior_session_levels=prior_levels)
            except Exception as exc:
                typer.echo(f"    Warning: replay failed for {session_date}: {exc}")
                continue

            # Chain: this session's RTH range → next session's PDH/PDL
            prior_levels = engine.get_prior_session_for_chaining()

            # Reset weekly/monthly at boundaries
            next_idx = dates.tolist().index(session_date) + 1 if hasattr(dates, 'tolist') else None
            if next_idx and next_idx < len(dates):
                next_date = dates[next_idx]
                if hasattr(next_date, 'weekday') and next_date.weekday() == 0:  # Monday
                    prior_levels["weekly_high"] = None
                    prior_levels["weekly_low"] = None
                if hasattr(next_date, 'day') and next_date.day == 1:  # 1st of month
                    prior_levels["monthly_high"] = None
                    prior_levels["monthly_low"] = None

            for ep in episodes:
                normalizer.update(ep.observation)
                all_observations.append(ep.observation)
                all_rewards_long.append(ep.reward_long)
                all_rewards_short.append(ep.reward_short)
                all_level_types.append(ep.level_type)

            session_episodes += len(episodes)

        total_episodes += session_episodes
        typer.echo(f"  {pfile.name}: {session_episodes} episodes across {len(dates)} session(s)")

    if total_episodes == 0:
        typer.echo("No episodes generated. Check tick data and replay engine.")
        raise typer.Exit(1)

    # Save .npy files
    obs_array = np.stack(all_observations).astype(np.float32)
    np.save(episodes_dir / "observations.npy", obs_array)
    np.save(episodes_dir / "rewards_long.npy", np.array(all_rewards_long, dtype=np.float32))
    np.save(episodes_dir / "rewards_short.npy", np.array(all_rewards_short, dtype=np.float32))
    np.save(episodes_dir / "level_types.npy", np.array(all_level_types))

    # Save normalizer
    normalizer.save(episodes_dir / "normalizer.json")

    typer.echo(f"\nTotal episodes: {total_episodes}")
    typer.echo(f"Observation shape: {obs_array.shape}")
    typer.echo(f"Saved to: {episodes_dir}")
    typer.echo(f"  observations.npy, rewards_long.npy, rewards_short.npy, level_types.npy")
    typer.echo(f"  normalizer.json")


# ---------------------------------------------------------------------------
# train
# ---------------------------------------------------------------------------

@rl_app.command()
def train(
    epochs: int = typer.Option(100, help="Number of training epochs"),
    checkpoint: str = typer.Option("v1", help="Checkpoint name for saved model"),
) -> None:
    """Train the DQN agent on replayed episodes."""
    import numpy as np

    from src.rl.agent.dqn import DQNAgent
    from src.rl.data.normalization import RunningNormalizer
    from src.rl.config import Action, BATCH_SIZE
    from src.rl.features.observation import OBSERVATION_DIM

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    # Load episode arrays
    obs_path = episodes_dir / "observations.npy"
    if not obs_path.exists():
        typer.echo(f"No observations.npy found in {episodes_dir}. Run 'rl replay' first.", err=True)
        raise typer.Exit(1)

    observations = np.load(episodes_dir / "observations.npy")
    rewards_long = np.load(episodes_dir / "rewards_long.npy")
    rewards_short = np.load(episodes_dir / "rewards_short.npy")
    level_types = np.load(episodes_dir / "level_types.npy", allow_pickle=True)

    n = len(observations)
    typer.echo(f"Loaded {n} episodes from {episodes_dir}")

    # Load and apply normalizer
    normalizer_path = episodes_dir / "normalizer.json"
    normalizer = RunningNormalizer(dim=OBSERVATION_DIM)
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
    train_rl = rewards_long[:train_end]
    train_rs = rewards_short[:train_end]

    val_obs = normalized_obs[train_end:val_end]
    val_rl = rewards_long[train_end:val_end]
    val_rs = rewards_short[train_end:val_end]

    typer.echo(f"Split: train={len(train_obs)}, val={len(val_obs)}, test={n - val_end}")

    # Build best actions per training episode
    skip_reward = 0.0
    agent = DQNAgent(observation_dim=OBSERVATION_DIM)

    # Load all training episodes into the replay buffer
    for i in range(len(train_obs)):
        rl = float(train_rl[i])
        rs = float(train_rs[i])
        max_reward = max(rl, rs, skip_reward)
        if skip_reward == max_reward:
            best_action = Action.SKIP.value
        elif rs == max_reward:
            best_action = Action.SHORT.value
        else:
            best_action = Action.LONG.value

        reward = max_reward
        agent.store(train_obs[i], best_action, reward)

    typer.echo(f"Buffer loaded: {agent.buffer.size} transitions")

    if agent.buffer.size < BATCH_SIZE:
        typer.echo(f"Buffer too small ({agent.buffer.size} < {BATCH_SIZE}). Need more training data.", err=True)
        raise typer.Exit(1)

    # Training loop
    typer.echo(f"\nTraining for {epochs} epochs ...")
    for epoch in range(1, epochs + 1):
        loss = agent.train_step()
        if epoch % 10 == 0:
            typer.echo(f"  Epoch {epoch:>5}/{epochs}  loss={loss:.4f}  epsilon={agent.epsilon:.3f}")

    # Validation
    typer.echo("\nRunning validation ...")
    correct = 0
    for i in range(len(val_obs)):
        predicted_action = agent.select_action(val_obs[i])
        rl = float(val_rl[i])
        rs = float(val_rs[i])
        max_reward = max(rl, rs, skip_reward)
        if skip_reward == max_reward:
            best_action = Action.SKIP.value
        elif rs == max_reward:
            best_action = Action.SHORT.value
        else:
            best_action = Action.LONG.value
        if predicted_action == best_action:
            correct += 1

    val_accuracy = correct / max(len(val_obs), 1)
    typer.echo(f"  Validation accuracy: {val_accuracy:.1%} ({correct}/{len(val_obs)})")

    # Save model
    model_path = models_dir / f"dqn_{checkpoint}.pt"
    agent.save(model_path)
    typer.echo(f"\nModel saved to: {model_path}")


# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------

@rl_app.command()
def eval(
    checkpoint: str = typer.Option("v1", help="Checkpoint name to load"),
) -> None:
    """Evaluate the trained DQN agent on the test split."""
    import numpy as np

    from src.rl.agent.dqn import DQNAgent
    from src.rl.agent.evaluate import compute_metrics, print_evaluation_report
    from src.rl.data.normalization import RunningNormalizer
    from src.rl.config import Action, EPSILON_END
    from src.rl.features.observation import OBSERVATION_DIM

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
    rewards_long = np.load(episodes_dir / "rewards_long.npy")
    rewards_short = np.load(episodes_dir / "rewards_short.npy")
    level_types = np.load(episodes_dir / "level_types.npy", allow_pickle=True)

    n = len(observations)

    # Load normalizer
    normalizer_path = episodes_dir / "normalizer.json"
    normalizer = RunningNormalizer(dim=OBSERVATION_DIM)
    if normalizer_path.exists():
        normalizer.load(normalizer_path)

    normalized_obs = np.stack([normalizer.normalize(obs) for obs in observations])

    # Test split: last 17%
    val_end = int(n * 0.83)
    test_obs = normalized_obs[val_end:]
    test_rl = rewards_long[val_end:]
    test_rs = rewards_short[val_end:]
    test_lt = level_types[val_end:]

    typer.echo(f"Test split: {len(test_obs)} episodes (last 17% of {n})")

    # Load agent with greedy policy
    agent = DQNAgent(observation_dim=OBSERVATION_DIM, epsilon=0.0)
    agent.load(model_path)
    agent.epsilon = 0.0  # Greedy evaluation
    typer.echo(f"Loaded model: {model_path}")

    # Run greedy evaluation
    skip_reward = 0.0
    episode_dicts: list[dict] = []
    for i in range(len(test_obs)):
        action = agent.select_action(test_obs[i])
        rl = float(test_rl[i])
        rs = float(test_rs[i])

        if action == Action.LONG.value:
            reward = rl
        elif action == Action.SHORT.value:
            reward = rs
        else:
            reward = skip_reward

        episode_dicts.append({
            "action": action,
            "reward": reward,
            "level_type": str(test_lt[i]),
        })

    metrics = compute_metrics(episode_dicts)
    print_evaluation_report(metrics)
