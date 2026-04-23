"""Broker configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class BrokerConfig:
    """All broker settings. Same rules for demo and live."""

    enabled: bool = False
    env: str = "demo"

    # Tradovate credentials
    username: str = ""
    password: str = ""
    app_id: str = ""
    cid: str = ""
    device_id: str = "firev-agent"

    # Trading
    symbol: str = "NQM5"
    max_position: int = 2
    max_daily_loss: float = 1000.0
    max_trailing_dd: float = 2000.0
    flatten_et: str = "15:55"
    min_trade_interval_s: float = 30.0

    @property
    def base_url(self) -> str:
        if self.env == "live":
            return "https://live.tradovateapi.com/v1"
        return "https://demo.tradovateapi.com/v1"

    @classmethod
    def from_env(cls) -> BrokerConfig:
        return cls(
            enabled=os.environ.get("BROKER_ENABLED", "false").lower() == "true",
            env=os.environ.get("TRADOVATE_ENV", "demo"),
            username=os.environ.get("TRADOVATE_USERNAME", ""),
            password=os.environ.get("TRADOVATE_PASSWORD", ""),
            app_id=os.environ.get("TRADOVATE_APP_ID", ""),
            cid=os.environ.get("TRADOVATE_CID", ""),
            device_id=os.environ.get("TRADOVATE_DEVICE_ID", "firev-agent"),
            symbol=os.environ.get("BROKER_SYMBOL", "NQM5"),
            max_position=int(os.environ.get("BROKER_MAX_POSITION", "2")),
            max_daily_loss=float(os.environ.get("BROKER_MAX_DAILY_LOSS", "1000")),
            max_trailing_dd=float(os.environ.get("BROKER_MAX_TRAILING_DD", "2000")),
            flatten_et=os.environ.get("BROKER_FLATTEN_ET", "15:55"),
            min_trade_interval_s=float(os.environ.get("BROKER_MIN_INTERVAL", "30")),
        )
