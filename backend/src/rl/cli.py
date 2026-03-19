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
                episodes = engine.replay_session(ticks, session_dt)
            except Exception as exc:
                typer.echo(f"    Warning: replay failed for {session_date}: {exc}")
                continue

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
