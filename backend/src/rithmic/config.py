"""Rithmic connection configuration from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class RithmicConfig:
    """Rithmic connection settings for Apex/prop firm accounts."""
    user: str = ""
    password: str = ""
    system_name: str = "Rithmic Paper Trading"
    app_name: str = "firev"
    app_version: str = "1.0"
    url: str = "rituz00100.rithmic.com:443"  # paper trading gateway
    symbol: str = "NQM5"
    exchange: str = "CME"

    @classmethod
    def from_env(cls) -> RithmicConfig:
        return cls(
            user=os.environ.get("RITHMIC_USER", ""),
            password=os.environ.get("RITHMIC_PASSWORD", ""),
            system_name=os.environ.get("RITHMIC_SYSTEM_NAME", "Rithmic Paper Trading"),
            app_name=os.environ.get("RITHMIC_APP_NAME", "firev"),
            app_version=os.environ.get("RITHMIC_APP_VERSION", "1.0"),
            url=os.environ.get("RITHMIC_URL", "rituz00100.rithmic.com:443"),
            symbol=os.environ.get("RITHMIC_SYMBOL", "NQM5"),
            exchange=os.environ.get("RITHMIC_EXCHANGE", "CME"),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.user and self.password)
