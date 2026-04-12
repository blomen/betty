"""TopstepX configuration dataclass."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class TopstepXConfig:
    username: str = ""
    api_key: str = ""
    contract_id: str = "CON.F.US.ENQ.M26"
    base_url: str = "https://api.topstepx.com"
    market_hub_url: str = "wss://rtc.topstepx.com/hubs/market"
    user_hub_url: str = "wss://rtc.topstepx.com/hubs/user"
    server_ws_url: str = "ws://127.0.0.1:18000/ws/signals"
    account_id: int = 0  # 0 = auto-select (prefers PRAC accounts)
    max_position: int = 2
    max_daily_loss: float = 1000.0
    max_trailing_dd: float = 2000.0
    flatten_et: str = "15:55"

    @property
    def is_configured(self) -> bool:
        return bool(self.username and self.api_key)

    @classmethod
    def from_env(cls) -> TopstepXConfig:
        """Load config from environment variables."""
        return cls(
            username=os.getenv("TOPSTEPX_USERNAME", ""),
            api_key=os.getenv("TOPSTEPX_API_KEY", ""),
            contract_id=os.getenv("TOPSTEPX_CONTRACT", "CON.F.US.ENQ.M26"),
            base_url=os.getenv("TOPSTEPX_BASE_URL", "https://api.topstepx.com"),
            market_hub_url=os.getenv("TOPSTEPX_MARKET_HUB_URL", "wss://rtc.topstepx.com/hubs/market"),
            user_hub_url=os.getenv("TOPSTEPX_USER_HUB_URL", "wss://rtc.topstepx.com/hubs/user"),
            server_ws_url=os.getenv("TOPSTEPX_SERVER_WS_URL", "ws://127.0.0.1:18000/ws/signals"),
            account_id=int(os.getenv("TOPSTEPX_ACCOUNT_ID", "0")),
            max_position=int(os.getenv("TOPSTEPX_MAX_POSITION", "2")),
            max_daily_loss=float(os.getenv("TOPSTEPX_MAX_DAILY_LOSS", "1000.0")),
            max_trailing_dd=float(os.getenv("TOPSTEPX_MAX_TRAILING_DD", "2000.0")),
            flatten_et=os.getenv("TOPSTEPX_FLATTEN_ET", "15:55"),
        )
