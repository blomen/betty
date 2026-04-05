"""ProviderWorkflow — base class for provider-specific fire workflow automation."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

logger = logging.getLogger(__name__)


class WorkflowMode(Enum):
    GUIDED = "guided"
    AUTONOMOUS = "autonomous"


@dataclass
class PlacementResult:
    status: str  # "placed" | "failed" | "skipped" | "manual"
    bet_id: int
    actual_odds: float | None = None
    actual_stake: float | None = None
    reason: str | None = None
    raw_response: dict | None = None


@dataclass
class HistoryEntry:
    provider_bet_id: str
    event_name: str
    market: str
    outcome: str
    odds: float
    stake: float
    status: str  # "won" | "lost" | "void" | "cashout" | "pending"
    payout: float | None = None


class ProviderWorkflow(ABC):
    """Base class for provider-specific fire workflow automation.

    Each platform implements this interface. The fire window calls the same
    methods regardless of provider. Platform siblings share one implementation.
    """

    platform: str  # "altenar", "gecko", "kambi", "pinnacle", "polymarket"

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        self.provider_id = provider_id
        self.domain = domain
        self.mode = mode

    async def find_tab(self, context: "BrowserContext") -> "Page | None":
        """Find this provider's tab in the browser context."""
        for page in context.pages:
            url = page.url or ""
            if self.domain and self.domain in url:
                return page
        return None

    @abstractmethod
    async def check_login(self, page: "Page") -> bool:
        """Check if user is logged in. Returns True if authenticated."""

    @abstractmethod
    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """Read bet history and return settled bets for DB reconciliation."""

    @abstractmethod
    async def sync_balance(self, page: "Page") -> float:
        """Read current balance. Returns amount in provider's native currency."""

    @abstractmethod
    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Navigate the page to the event for this bet. Returns True on success."""

    @abstractmethod
    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Place a bet: select outcome, enter stake, submit."""

    async def check_live_price(self, page: "Page", bet) -> float | None:
        """Read live odds and return edge %. Override for providers with DOM/API price reads.
        Returns None if not supported or price unavailable."""
        return None

    async def await_confirmation(self, page: "Page", timeout_s: float = 15.0) -> PlacementResult | None:
        """Wait for placement confirmation. Default: no-op (API response IS confirmation).
        Override for DOM-based platforms where confirmation is async (e.g., Polymarket)."""
        return None

    async def cleanup(self, page: "Page") -> None:
        """Called after all bets for this provider are done. Override to close extra tabs etc."""
        pass

    async def _evaluate_api(self, page: "Page", url: str, method: str = "GET", body: dict | None = None) -> dict | None:
        """Make an API call from the page's session (inherits cookies/auth)."""
        try:
            if body:
                body_json = json.dumps(body)
                js = f"""
                    async () => {{
                        const resp = await fetch("{url}", {{
                            method: "{method}",
                            credentials: "include",
                            headers: {{"Content-Type": "application/json"}},
                            body: JSON.stringify({body_json})
                        }});
                        if (!resp.ok) return {{ __error: resp.status }};
                        return await resp.json();
                    }}
                """
            else:
                js = f"""
                    async () => {{
                        const resp = await fetch("{url}", {{
                            method: "{method}",
                            credentials: "include"
                        }});
                        if (!resp.ok) return {{ __error: resp.status }};
                        return await resp.json();
                    }}
                """
            return await page.evaluate(js)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] API call failed: {url} — {e}")
            return None
