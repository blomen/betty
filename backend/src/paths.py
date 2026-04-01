"""
Centralized path resolution for Firev.

Uses environment variables with defaults:
  FIREV_DATA_DIR  → /app/data   (Docker) or backend/data (dev)
  FIREV_LOGS_DIR  → /app/logs   (Docker) or backend/logs (dev)
  FIREV_CONFIG_DIR → src/config  (always relative to source)
"""

import os
from pathlib import Path

# Base: the backend/ directory (parent of src/)
_BACKEND_DIR = Path(__file__).parent.parent


def get_data_dir() -> Path:
    """Persistent data directory (DB files, exports)."""
    d = Path(os.environ.get("FIREV_DATA_DIR", str(_BACKEND_DIR / "data")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_db_path() -> Path:
    """SQLite database path (used until Postgres migration)."""
    return get_data_dir() / "firev.db"


def get_market_db_path() -> Path:
    """Separate SQLite database for market tick/candle data."""
    return get_data_dir() / "market.db"


def get_logs_dir() -> Path:
    """Logs directory."""
    d = Path(os.environ.get("FIREV_LOGS_DIR", str(_BACKEND_DIR / "logs")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_config_path(filename: str) -> Path:
    """Config file path (providers.yaml, sports.yaml)."""
    config_dir = Path(os.environ.get(
        "FIREV_CONFIG_DIR",
        str(Path(__file__).parent / "config"),
    ))
    return config_dir / filename


def get_config_dir() -> Path:
    """Config directory."""
    return Path(os.environ.get(
        "FIREV_CONFIG_DIR",
        str(Path(__file__).parent / "config"),
    ))


def get_aliases_path() -> Path:
    """Team name aliases YAML."""
    return Path(__file__).parent / "matching" / "aliases.yaml"


def get_frontend_dir() -> Path:
    """Frontend dist directory."""
    return Path(os.environ.get(
        "FIREV_FRONTEND_DIR",
        str(_BACKEND_DIR.parent / "frontend" / "dist"),
    ))


def get_env_path() -> Path:
    """.env file path."""
    return _BACKEND_DIR / ".env"
