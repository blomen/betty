"""Trading configuration loader. Caches on first load."""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_trading_config: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _trading_config
    if _trading_config is not None:
        return _trading_config

    from ..paths import get_config_path
    path = get_config_path("trading.yaml")

    if not path.exists():
        logger.warning("Trading config not found at %s — using empty defaults", path)
        _trading_config = {"instruments": {}, "setups": {}, "daily_routine": {}}
        return _trading_config

    with open(path, "r", encoding="utf-8") as f:
        _trading_config = yaml.safe_load(f) or {}

    logger.info(
        "Loaded trading config: %d instruments, %d setups",
        len(_trading_config.get("instruments", {})),
        len(_trading_config.get("setups", {})),
    )
    return _trading_config


def get_trading_config() -> dict[str, Any]:
    """Full trading config dict."""
    return _load()


def get_instruments() -> dict[str, dict]:
    """Instrument definitions keyed by symbol."""
    return _load().get("instruments", {})


def get_setups() -> dict[str, dict]:
    """Setup definitions keyed by setup_type."""
    return _load().get("setups", {})


def get_routine_config() -> dict[str, Any]:
    """Daily routine checklist config."""
    return _load().get("daily_routine", {})


def get_market_data_config() -> dict[str, Any]:
    """Market data provider config."""
    return _load().get("market_data", {})


def get_scanner_config() -> dict[str, Any]:
    """Scanner/scoring config."""
    return _load().get("scanner", {})
