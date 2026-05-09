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


@dataclass
class PositionEntry:
    """An open/pending bet from the provider's perspective."""

    provider_bet_id: str
    event_name: str
    market: str
    outcome: str
    odds: float
    stake: float
    placed_at: str | None = None
    potential_payout: float | None = None


class ProviderWorkflow(ABC):
    """Base class for provider-specific fire workflow automation.

    Each platform implements this interface. The fire window calls the same
    methods regardless of provider. Platform siblings share one implementation.
    """

    platform: str  # "altenar", "gecko", "kambi", "pinnacle", "polymarket"
    autonomous_placement: bool = False  # True for API-based providers (Pinnacle) — place_bet() called on user confirm

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        self.provider_id = provider_id
        self.domain = domain
        self.mode = mode

    @property
    def home_url(self) -> str:
        """URL to open when launching this provider's tab. Override for /en/ etc."""
        return f"https://{self.domain}"

    async def find_tab(self, context: BrowserContext) -> Page | None:
        """Find this provider's tab in the browser context.

        Prefers the page with the longest URL (most likely logged in / deepest page).
        """
        from .._urls import hostname_matches

        best = None
        best_len = 0
        for page in context.pages:
            url = page.url or ""
            if self.domain and hostname_matches(self.domain, url):
                if len(url) > best_len:
                    best = page
                    best_len = len(url)
        return best

    @abstractmethod
    async def check_login(self, page: Page) -> bool:
        """Check if user is logged in. Returns True if authenticated."""

    @abstractmethod
    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Read bet history and return settled bets for DB reconciliation."""

    @abstractmethod
    async def sync_balance(self, page: Page) -> float:
        """Read current balance. Returns amount in provider's native currency."""

    @abstractmethod
    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate the page to the event for this bet. Returns True on success."""

    @abstractmethod
    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Place a bet: select outcome, enter stake, submit."""

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        """Phase 1: auto-select outcome + fill stake. Called before bet_ready.

        Returns PlacementResult with status="prepped" on success.
        Default: falls back to place_bet (no two-phase support).
        """
        return PlacementResult(status="no_prep", bet_id=0, reason="not_implemented")

    async def confirm_bet(self, page: Page) -> PlacementResult:
        """Phase 2: click submit button after user confirms. Called on Place.

        Default: no-op (for workflows where prep_betslip does everything).
        """
        return PlacementResult(status="manual", bet_id=0, reason="user_confirms_on_site")

    async def read_slip_odds(self, page: Page) -> float | None:
        """Read the odds the loaded slip widget currently displays.

        Idempotent, fast — called ~1Hz by SlipOddsStream while a slip is loaded.
        Returns None if slip is empty, errored, or workflow doesn't support scrape.
        Override per workflow.
        """
        return None

    async def update_slip_stake(self, page: Page, stake: float) -> bool:
        """Re-write the stake field on a loaded slip without re-navigating.

        Returns True on success. Used by ArbRunner to keep counter slips in sync
        with the actual placed anchor stake. Override per workflow.
        """
        return False

    async def fetch_history_for_bet(self, page: Page, bet: dict) -> list[HistoryEntry] | None:
        """Targeted history lookup for a specific bet that wasn't found in the
        paginated sync_history window.

        Returns a small list of history entries scoped to the bet's event
        window (typically start_time ± a few days), or None if this workflow
        doesn't support targeted lookup.

        Override per workflow. Default returns None (caller falls back to no-op).
        """
        return None

    async def check_live_price(self, page: Page, bet) -> tuple[float | None, float | None]:
        """Read live odds and return (live_odds, live_edge) or (None, None).

        Override for providers with DOM/API price reads.
        """
        return None, None

    async def await_confirmation(self, page: Page, timeout_s: float = 15.0) -> PlacementResult | None:
        """Wait for placement confirmation. Default: no-op (API response IS confirmation).
        Override for DOM-based platforms where confirmation is async (e.g., Polymarket)."""
        return None

    async def fetch_positions(self, page: Page) -> list[PositionEntry]:
        """Return open/pending bets from provider. Default: empty list."""
        return []

    @staticmethod
    def parse_placement_response(body: dict) -> str | None:
        """Extract provider_bet_id from placement confirmation. Override per platform."""
        return None

    @staticmethod
    def parse_placement_status(body: dict) -> dict:
        """Check if placement response indicates success, error, or stake limit.

        Returns dict with:
          - success: bool
          - error: str | None
          - max_stake: float | None
        Override per platform for provider-specific error detection.
        """
        return {"success": True, "error": None, "max_stake": None}

    async def cleanup(self, page: Page) -> None:
        """Called after all bets for this provider are done. Override to close extra tabs etc."""
        pass

    async def _evaluate_api(self, page: Page, url: str, method: str = "GET", body: dict | None = None) -> dict | None:
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
