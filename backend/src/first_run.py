"""
First-run setup for OddOpp desktop app.

Creates AppData directory structure, copies default configs,
and checks Playwright browser installation.
"""

import logging
import shutil
from pathlib import Path

from .paths import (
    get_app_data_dir,
    get_bundle_dir,
    get_logs_dir,
    is_bundled,
)

logger = logging.getLogger(__name__)


def run_first_time_setup():
    """Run first-time setup tasks. Safe to call on every launch."""
    app_dir = get_app_data_dir()

    # 1. Create directory structure
    (app_dir / "data").mkdir(parents=True, exist_ok=True)
    (app_dir / "config").mkdir(parents=True, exist_ok=True)
    get_logs_dir()  # creates logs dir

    # 2. Copy default config files if not present in AppData
    _copy_default_configs(app_dir)

    # 3. Create .env template if not exists
    env_path = app_dir / ".env"
    if not env_path.exists():
        env_path.write_text(
            "# OddOpp Environment Configuration\n"
            "# Anthropic API Key for Claude chat integration (optional)\n"
            "ANTHROPIC_API_KEY=\n"
        )
        logger.info("Created .env template at %s", env_path)

    # 4. Initialize database
    from .db.models import init_db
    init_db()

    # 5. Skip Playwright check on startup — it launches a full Chromium browser
    # which takes 10-17s and isn't necessary for the app to run. Browser-based
    # providers will fail gracefully at extraction time if browsers aren't installed.

    logger.info("Setup complete. Data directory: %s", app_dir)


def _copy_default_configs(app_dir: Path):
    """Copy bundled config files to AppData if they don't exist yet."""
    config_files = ["providers.yaml", "sports.yaml"]

    for filename in config_files:
        dest = app_dir / "config" / filename
        if dest.exists():
            continue

        # Find the bundled source
        if is_bundled():
            src = get_bundle_dir() / "config" / filename
        else:
            src = Path(__file__).parent / "config" / filename

        if src.exists():
            shutil.copy2(src, dest)
            logger.info("Copied default config: %s", filename)


def _check_playwright():
    """Check if Playwright browsers are installed. Warn if not."""
    if is_bundled():
        # In bundled mode, Playwright driver binary isn't available inside the .exe.
        # Browser-based providers will use the system Playwright install at runtime.
        # Skip the check to avoid noisy errors on startup.
        logger.info("Playwright check skipped (bundled mode)")
        return

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
            logger.info("Playwright browsers: OK")
    except Exception as e:
        logger.warning(
            "Playwright browsers not found. Browser-based providers will fail. "
            "Install with: playwright install chromium  (%s)",
            e,
        )
