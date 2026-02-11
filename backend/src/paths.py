"""
Centralized path resolution for OddOpp.

Handles both development mode (running from source) and bundled mode
(running from PyInstaller .exe). All path-dependent modules import from here.

In dev mode:
  - Bundled resources (config, frontend) → relative to source tree
  - User data (DB, logs) → backend/data/, backend/logs/

In bundled mode:
  - Bundled resources → sys._MEIPASS (PyInstaller temp dir)
  - User data → %LOCALAPPDATA%/OddOpp/
"""

import os
import sys
from pathlib import Path


def is_bundled() -> bool:
    """True when running from a PyInstaller .exe."""
    return getattr(sys, '_MEIPASS', None) is not None


def get_bundle_dir() -> Path:
    """Directory containing bundled resources (config, frontend, aliases)."""
    if is_bundled():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # Dev mode: backend/ directory
    return Path(__file__).parent.parent


def get_app_data_dir() -> Path:
    """
    Persistent user data directory.

    Bundled: %LOCALAPPDATA%/OddOpp/
    Dev:     backend/ (preserves current behavior)
    """
    if is_bundled():
        local = os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))
        app_dir = Path(local) / 'OddOpp'
        app_dir.mkdir(parents=True, exist_ok=True)
        return app_dir
    return Path(__file__).parent.parent


def get_db_path() -> Path:
    """SQLite database path. Always in user data directory."""
    db_dir = get_app_data_dir() / 'data'
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / 'oddopp.db'


def get_logs_dir() -> Path:
    """Logs directory. Always in user data directory."""
    logs_dir = get_app_data_dir() / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_config_path(filename: str) -> Path:
    """
    Config file path with user override support.

    Checks %LOCALAPPDATA%/OddOpp/config/ first (user customization),
    then falls back to bundled default.
    """
    if is_bundled():
        # Check AppData override first
        override = get_app_data_dir() / 'config' / filename
        if override.exists():
            return override
        # Fall back to bundled
        return get_bundle_dir() / 'config' / filename
    # Dev mode: src/config/
    return Path(__file__).parent / 'config' / filename


def get_config_dir() -> Path:
    """Config directory (for loader.py which needs the directory, not individual files)."""
    if is_bundled():
        override_dir = get_app_data_dir() / 'config'
        if override_dir.exists() and any(override_dir.iterdir()):
            return override_dir
        return get_bundle_dir() / 'config'
    return Path(__file__).parent / 'config'


def get_aliases_path() -> Path:
    """Team name aliases YAML (read-only, bundled resource)."""
    if is_bundled():
        return get_bundle_dir() / 'matching' / 'aliases.yaml'
    return Path(__file__).parent / 'matching' / 'aliases.yaml'


def get_frontend_dir() -> Path:
    """Frontend dist directory (bundled React build)."""
    if is_bundled():
        return get_bundle_dir() / 'frontend' / 'dist'
    return Path(__file__).parent.parent.parent / 'frontend' / 'dist'


def get_env_path() -> Path:
    """
    .env file path.

    Bundled: %LOCALAPPDATA%/OddOpp/.env
    Dev:     backend/.env
    """
    return get_app_data_dir() / '.env'
