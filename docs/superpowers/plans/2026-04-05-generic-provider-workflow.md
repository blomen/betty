# Generic Provider Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all provider-specific branches in fire_window.py with a generic ProviderWorkflow interface, extracting Polymarket automation into its own workflow class and creating stubs for all other platforms.

**Architecture:** ABC `ProviderWorkflow` with 6 standard phases (check_login, sync_history, sync_balance, navigate_to_event, place_bet, await_confirmation). Platform implementations keyed by `retriever_type` from providers.yaml. Fire window calls workflow methods instead of hardcoded `if pid == "polymarket"` branches.

**Tech Stack:** Python 3.10+, Playwright async API, existing MirrorService/BetInterceptor infrastructure.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/src/mirror/workflows/__init__.py` | Registry: `get_workflow(provider_id) -> ProviderWorkflow` |
| `backend/src/mirror/workflows/base.py` | ABC + dataclasses (PlacementResult, HistoryEntry, WorkflowMode) |
| `backend/src/mirror/workflows/polymarket.py` | Full automation extracted from mirror/service.py |
| `backend/src/mirror/workflows/pinnacle.py` | API-based: login/balance via REST, navigate via matchup_id URL |
| `backend/src/mirror/workflows/altenar.py` | API-based: balance/history via known endpoints |
| `backend/src/mirror/workflows/gecko.py` | API-based: balance via wallets endpoint, placement via coupons |
| `backend/src/mirror/workflows/kambi.py` | Stub: WS-based, guided mode only |
| `backend/src/mirror/workflows/manual.py` | Fallback for unwired platforms: all phases return "manual" |
| `backend/src/services/fire_window.py` | Modified: replace all `if pid ==` branches with workflow calls |
| `backend/src/api/routes/fire_window.py` | Modified: remove Polymarket-specific tab/scrape logic |
| `backend/src/mirror/service.py` | NOT modified in this plan — PolymarketWorkflow delegates to existing methods. Future task: move implementation into workflow, remove from service. |

---

### Task 1: Create the base workflow interface

**Files:**
- Create: `backend/src/mirror/workflows/__init__.py`
- Create: `backend/src/mirror/workflows/base.py`

- [ ] **Step 1: Create the workflows directory**

```bash
mkdir -p backend/src/mirror/workflows
```

- [ ] **Step 2: Write base.py with the ABC and dataclasses**

Create `backend/src/mirror/workflows/base.py`:

```python
"""ProviderWorkflow — base class for provider-specific fire workflow automation."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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

    async def await_confirmation(self, page: "Page", timeout_s: float = 15.0) -> PlacementResult | None:
        """Wait for placement confirmation. Default: no-op (API response IS confirmation).

        Override for DOM-based platforms where confirmation is async (e.g., Polymarket).
        """
        return None

    async def check_live_price(self, page: "Page", bet) -> float | None:
        """Read live odds and return edge %. Override for providers with DOM/API price reads.
        Returns None if not supported or price unavailable."""
        return None

    async def cleanup(self, page: "Page") -> None:
        """Called after all bets for this provider are done. Override to close extra tabs etc."""
        pass

    # -- Helpers for subclasses --

    async def _evaluate_api(self, page: "Page", url: str, method: str = "GET", body: dict | None = None) -> dict | None:
        """Make an API call from the page's session (inherits cookies/auth)."""
        try:
            js = f"""
                async () => {{
                    const resp = await fetch("{url}", {{
                        method: "{method}",
                        headers: {{"Content-Type": "application/json"}},
                        {"body: JSON.stringify(" + repr(body) + ")," if body else ""}
                    }});
                    if (!resp.ok) return {{ __error: resp.status }};
                    return await resp.json();
                }}
            """
            return await page.evaluate(js)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] API call failed: {url} — {e}")
            return None
```

- [ ] **Step 3: Write the registry `__init__.py`**

Create `backend/src/mirror/workflows/__init__.py`:

```python
"""Workflow registry — maps provider_id to platform workflow class."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular deps — populated on first call
_PLATFORM_MAP: dict[str, type[ProviderWorkflow]] | None = None


def _load_platform_map() -> dict[str, type[ProviderWorkflow]]:
    from .polymarket import PolymarketWorkflow
    from .pinnacle import PinnacleWorkflow
    from .altenar import AltenarWorkflow
    from .gecko import GeckoWorkflow
    from .kambi import KambiWorkflow
    from .manual import ManualWorkflow
    return {
        "polymarket": PolymarketWorkflow,
        "pinnacle": PinnacleWorkflow,
        "altenar": AltenarWorkflow,
        "gecko_v2": GeckoWorkflow,
        "kambi": KambiWorkflow,
        # Fallback for unwired platforms
        "spectate": ManualWorkflow,
        "tenbet": ManualWorkflow,
        "snabbare": ManualWorkflow,
        "custom": ManualWorkflow,
        "betconstruct": ManualWorkflow,
        "interwetten": ManualWorkflow,
        "coolbet": ManualWorkflow,
        "tipwin": ManualWorkflow,
    }


# retriever_type already serves as platform identifier in providers.yaml
_RETRIEVER_TO_PLATFORM = {
    "polymarket": "polymarket",
    "pinnacle": "pinnacle",
    "altenar": "altenar",
    "gecko_v2": "gecko_v2",
    "kambi": "kambi",
    "spectate": "spectate",
    "tenbet": "tenbet",
    "snabbare": "snabbare",
    "custom": "custom",
    "betconstruct": "betconstruct",
    "interwetten": "interwetten",
    "coolbet": "coolbet",
    "tipwin": "tipwin",
}


def get_workflow(provider_id: str) -> ProviderWorkflow:
    """Get the workflow instance for a provider."""
    global _PLATFORM_MAP
    if _PLATFORM_MAP is None:
        _PLATFORM_MAP = _load_platform_map()

    from ...config.loader import load_config
    cfg = load_config()
    provider = cfg.get_provider(provider_id)

    if provider is None:
        # Providers not in config (e.g., polymarket has no domain)
        # Try direct match
        if provider_id in _PLATFORM_MAP:
            domain = {"polymarket": "polymarket.com", "pinnacle": "pinnacle.com"}.get(provider_id, "")
            return _PLATFORM_MAP[provider_id](provider_id=provider_id, domain=domain)
        from .manual import ManualWorkflow
        return ManualWorkflow(provider_id=provider_id, domain="")

    platform = _RETRIEVER_TO_PLATFORM.get(provider.retriever_type, provider.retriever_type)
    cls = _PLATFORM_MAP.get(platform)
    if cls is None:
        from .manual import ManualWorkflow
        cls = ManualWorkflow

    domain = provider.domain or ""
    return cls(provider_id=provider_id, domain=domain)


__all__ = [
    "ProviderWorkflow", "WorkflowMode", "PlacementResult", "HistoryEntry",
    "get_workflow",
]
```

- [ ] **Step 4: Verify imports work**

Run: `cd backend && python -c "from src.mirror.workflows.base import ProviderWorkflow, PlacementResult; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/workflows/__init__.py backend/src/mirror/workflows/base.py
git commit -m "feat(workflows): add ProviderWorkflow ABC and registry"
```

---

### Task 2: Create ManualWorkflow fallback

**Files:**
- Create: `backend/src/mirror/workflows/manual.py`

This is the fallback for any provider without a wired workflow. All phases return manual/noop results. The user places bets in mirror, interceptor catches them.

- [ ] **Step 1: Write manual.py**

Create `backend/src/mirror/workflows/manual.py`:

```python
"""ManualWorkflow — fallback for unwired providers.

All phases are no-ops or return manual status. The user places bets
in the mirror browser and the interceptor catches the API calls.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class ManualWorkflow(ProviderWorkflow):
    platform = "manual"

    async def check_login(self, page: "Page") -> bool:
        """Assume logged in if page is open (can't verify without wiring)."""
        return True

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """No-op — interceptor handles history when user browses to it."""
        return []

    async def sync_balance(self, page: "Page") -> float:
        """Return -1 to signal unknown — fire window uses DB balance instead."""
        return -1

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """No-op — user navigates manually."""
        logger.info(f"[{self.provider_id}] Manual: navigate to {bet.display_home} vs {bet.display_away}")
        return True

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Return manual status — user places in mirror, interceptor records."""
        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="manual_placement",
        )
```

- [ ] **Step 2: Verify import**

Run: `cd backend && python -c "from src.mirror.workflows.manual import ManualWorkflow; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/mirror/workflows/manual.py
git commit -m "feat(workflows): add ManualWorkflow fallback for unwired providers"
```

---

### Task 3: Extract PolymarketWorkflow from mirror/service.py

**Files:**
- Create: `backend/src/mirror/workflows/polymarket.py`
- Modify: `backend/src/mirror/service.py` (remove extracted methods later in Task 7)

This extracts the Polymarket-specific automation from MirrorService into a workflow class. For now, we create the new class that delegates to the existing MirrorService methods — we'll remove the old methods in Task 7 after fire_window.py is updated.

- [ ] **Step 1: Write polymarket.py**

Create `backend/src/mirror/workflows/polymarket.py`:

```python
"""PolymarketWorkflow — full DOM automation for Polymarket betting."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, PlacementResult, HistoryEntry, WorkflowMode
from ...analysis.value import compute_edge

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class PolymarketWorkflow(ProviderWorkflow):
    platform = "polymarket"

    def __init__(self, provider_id: str = "polymarket", domain: str = "polymarket.com",
                 mode: WorkflowMode = WorkflowMode.AUTONOMOUS):
        super().__init__(provider_id, domain, mode)
        self._tabs: dict[str, "Page"] = {}  # slug -> page

    async def check_login(self, page: "Page") -> bool:
        """Check login by looking for Cash balance in DOM."""
        try:
            await asyncio.sleep(2)  # Wait for client-side render
            balance_text = await page.evaluate("""
                () => {
                    const els = document.querySelectorAll('*');
                    for (const el of els) {
                        const text = el.textContent || '';
                        if (/Cash\\s*\\$[\\d,.]+/.test(text) && el.children.length === 0) {
                            return text;
                        }
                    }
                    return null;
                }
            """)
            if balance_text:
                logger.info(f"[polymarket] Logged in: {balance_text}")
                return True
            return False
        except Exception:
            return False

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """No-op — Polymarket settlement uses Gamma API, not browser scraping."""
        return []

    async def sync_balance(self, page: "Page") -> float:
        """Scrape USDC cash balance from DOM."""
        try:
            await asyncio.sleep(1)
            balance = await page.evaluate("""
                () => {
                    const els = document.querySelectorAll('*');
                    for (const el of els) {
                        const text = el.textContent || '';
                        const match = text.match(/Cash\\s*\\$([\\d,.]+)/);
                        if (match && el.children.length === 0) {
                            return parseFloat(match[1].replace(',', ''));
                        }
                    }
                    return null;
                }
            """)
            if balance is not None:
                logger.info(f"[polymarket] Balance: ${balance:.2f}")
                return balance
            return -1
        except Exception:
            return -1

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Open the market page for this bet."""
        slug = getattr(bet, "market_slug", None)
        if not slug:
            logger.warning(f"[polymarket] No market_slug for bet {bet.bet_id}")
            return False

        url = f"https://polymarket.com/event/{slug}"
        try:
            # Check if already on this page
            if slug in (page.url or ""):
                return True

            # Use persistent tab or navigate current page
            if slug in self._tabs:
                tab = self._tabs[slug]
                if not tab.is_closed():
                    return True

            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector("button.trading-button", timeout=10000)
            self._tabs[slug] = page
            return True
        except Exception as e:
            logger.warning(f"[polymarket] Navigation failed: {url} — {e}")
            return False

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Place bet via DOM automation: click outcome, fill amount, submit.

        Delegates to MirrorService._place_single_polymarket_bet for now.
        This keeps the existing battle-tested DOM automation code working
        while we refactor the routing layer.
        """
        # Import here to get the running mirror instance
        from ...api.routes.mirror import _get_active_mirror
        mirror = _get_active_mirror()
        if mirror is None:
            return PlacementResult(status="failed", bet_id=bet.bet_id, reason="no_mirror")

        slug = getattr(bet, "market_slug", None)
        poly_outcome = getattr(bet, "poly_outcome", None) or bet.outcome
        original_outcome = getattr(bet, "original_outcome", None) or ""

        # Get the right page (persistent tab or the navigated page)
        target_page = self._tabs.get(slug, page)

        try:
            expected_price = 1 / bet.odds if bet.odds > 0 else 0
            result = await mirror._place_single_polymarket_bet(
                page=target_page,
                bet_id=bet.bet_id,
                slug=slug or "",
                outcome=poly_outcome,
                amount=stake,
                expected_price=expected_price,
                max_slippage=3.0,
                original_outcome=original_outcome,
                market_type=bet.market,
            )
            status = result.get("status", "failed")
            return PlacementResult(
                status=status,
                bet_id=bet.bet_id,
                actual_odds=result.get("actual_odds"),
                actual_stake=stake,
                reason=result.get("reason"),
                raw_response=result,
            )
        except Exception as e:
            return PlacementResult(
                status="failed",
                bet_id=bet.bet_id,
                actual_stake=stake,
                reason=str(e),
            )

    async def check_live_price(self, page: "Page", bet) -> float | None:
        """Read live price from DOM trading buttons. Returns edge % or None."""
        from ...api.routes.mirror import _get_active_mirror
        mirror = _get_active_mirror()
        if mirror is None:
            return None

        slug = getattr(bet, "market_slug", None)
        target_page = self._tabs.get(slug, page)

        try:
            for attempt in range(3):
                buttons = await mirror._read_btn_prices(target_page)
                matched = mirror._find_btn_for_market(
                    buttons, bet.outcome, bet.market,
                    home_name=bet.display_home, away_name=bet.display_away,
                )
                if matched and matched.get("price"):
                    break
                await asyncio.sleep(1)

            if not matched:
                return None

            price = matched.get("price")
            if not price or price <= 0 or price >= 1:
                return None

            live_odds = round(1 / price, 4)
            edge = compute_edge("polymarket", live_odds, bet.fair_odds)
            return edge
        except Exception:
            return None

    async def cleanup(self, page: "Page") -> None:
        """Close all persistent Polymarket tabs."""
        for slug, tab in list(self._tabs.items()):
            try:
                if not tab.is_closed():
                    await tab.close()
            except Exception:
                pass
        self._tabs.clear()
```

- [ ] **Step 2: Verify import**

Run: `cd backend && python -c "from src.mirror.workflows.polymarket import PolymarketWorkflow; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/mirror/workflows/polymarket.py
git commit -m "feat(workflows): add PolymarketWorkflow — extract from mirror/service.py"
```

---

### Task 4: Create PinnacleWorkflow

**Files:**
- Create: `backend/src/mirror/workflows/pinnacle.py`

Pinnacle has REST API endpoints for balance and bet history, plus a matchup_id URL pattern for navigation. Bet placement is manual (interceptor catches `POST /0.1/bets/straight`).

- [ ] **Step 1: Write pinnacle.py**

Create `backend/src/mirror/workflows/pinnacle.py`:

```python
"""PinnacleWorkflow — REST API balance/history, manual bet placement."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, PlacementResult, HistoryEntry, WorkflowMode
from ...analysis.value import compute_edge

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class PinnacleWorkflow(ProviderWorkflow):
    platform = "pinnacle"

    def __init__(self, provider_id: str = "pinnacle", domain: str = "pinnacle.com",
                 mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    async def check_login(self, page: "Page") -> bool:
        """Hit wallet/balance API — 200 means logged in."""
        result = await self._evaluate_api(page, "https://api.arcadia.pinnacle.se/0.1/wallet/balance")
        if result and "__error" not in result:
            logger.info(f"[pinnacle] Logged in — balance: {result}")
            return True
        return False

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """Fetch settled bets from Pinnacle API.

        The interceptor already handles this when the user navigates to bet history.
        This method provides a programmatic way to trigger it.
        """
        result = await self._evaluate_api(page, "https://api.arcadia.pinnacle.se/0.1/bets")
        if not result or not isinstance(result, list):
            return []

        entries = []
        for b in result:
            settled_at = b.get("settledAt")
            if not settled_at:
                continue
            risk = float(b.get("riskAmount", 0))
            win = float(b.get("winAmount", 0))
            if win > 0:
                status = "won"
            elif risk > 0:
                status = "lost"
            else:
                status = "void"
            sels = b.get("selections", [])
            event_name = str(sels[0].get("matchup_id", "")) if sels else ""
            entries.append(HistoryEntry(
                provider_bet_id=str(b.get("id", "")),
                event_name=event_name,
                market=sels[0].get("marketType", "") if sels else "",
                outcome=sels[0].get("side", "") if sels else "",
                odds=float(b.get("price", 0)),
                stake=risk,
                status=status,
                payout=win if win > 0 else 0,
            ))
        return entries

    async def sync_balance(self, page: "Page") -> float:
        """Read balance from Pinnacle wallet API."""
        result = await self._evaluate_api(page, "https://api.arcadia.pinnacle.se/0.1/wallet/balance")
        if result and "amount" in result:
            balance = float(result["amount"])
            logger.info(f"[pinnacle] Balance: {balance} {result.get('currency', 'SEK')}")
            return balance
        return -1

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Navigate to event via Pinnacle search with team name."""
        matchup_id = getattr(bet, "matchup_id", None)
        home = getattr(bet, "display_home", "") or ""

        if matchup_id:
            # Direct matchup URL
            url = f"https://www.pinnacle.se/en/search/{home.replace(' ', '%20')}/"
        elif home:
            url = f"https://www.pinnacle.se/en/search/{home.replace(' ', '%20')}/"
        else:
            return False

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            import asyncio
            await asyncio.sleep(2)  # Wait for search results to render
            return True
        except Exception as e:
            logger.warning(f"[pinnacle] Navigation failed: {url} — {e}")
            return False

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Manual placement — user places in mirror, interceptor catches POST /0.1/bets/straight."""
        logger.info(
            f"[pinnacle] Manual: {bet.display_home} vs {bet.display_away} "
            f"{bet.market} {bet.outcome} @ {bet.odds} stake={stake}"
        )
        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="manual_placement",
        )

    async def check_live_price(self, page: "Page", bet) -> float | None:
        """Read live odds from Pinnacle search results DOM."""
        try:
            odds_data = await page.evaluate("""
                () => {
                    const results = [];
                    const rows = document.querySelectorAll('tr, [class*="row"], [class*="matchup"]');
                    for (const row of rows) {
                        const text = row.textContent || '';
                        const odds = [...text.matchAll(/(\\d{1,3}\\.\\d{2,3})/g)].map(m => parseFloat(m[1]));
                        if (odds.length >= 2) {
                            results.push({ text: text.slice(0, 200), odds });
                        }
                    }
                    return results;
                }
            """)

            if not odds_data:
                return None

            target_home = (bet.display_home or "").lower()[:4]
            target_away = (bet.display_away or "").lower()[:4]

            for row in odds_data:
                row_text = row["text"].lower()
                if target_home in row_text and target_away in row_text:
                    odds_list = row["odds"]
                    if bet.outcome == "home" and len(odds_list) >= 1:
                        live_odds = odds_list[0]
                    elif bet.outcome == "draw" and len(odds_list) >= 2:
                        live_odds = odds_list[1]
                    elif bet.outcome == "away":
                        if bet.market == "1x2" and len(odds_list) >= 3:
                            live_odds = odds_list[2]
                        elif len(odds_list) >= 2:
                            live_odds = odds_list[-1]
                        else:
                            continue
                    else:
                        continue

                    if live_odds > 1:
                        edge = compute_edge(bet.provider_id, live_odds, bet.fair_odds)
                        logger.info(
                            f"[pinnacle] Live: {bet.display_home} vs {bet.display_away} "
                            f"{bet.outcome} @ {live_odds} edge={edge:.1f}%"
                        )
                        return edge
            return None
        except Exception:
            logger.debug("Pinnacle price read failed", exc_info=True)
            return None
```

- [ ] **Step 2: Verify import**

Run: `cd backend && python -c "from src.mirror.workflows.pinnacle import PinnacleWorkflow; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/mirror/workflows/pinnacle.py
git commit -m "feat(workflows): add PinnacleWorkflow — API balance/history, manual placement"
```

---

### Task 5: Create AltenarWorkflow and GeckoWorkflow

**Files:**
- Create: `backend/src/mirror/workflows/altenar.py`
- Create: `backend/src/mirror/workflows/gecko.py`

Both have partially discovered API patterns. Login/balance are wired. History is wired for Altenar. Navigate and place_bet are manual (guided mode).

- [ ] **Step 1: Write altenar.py**

Create `backend/src/mirror/workflows/altenar.py`:

```python
"""AltenarWorkflow — API-based balance/history, guided bet placement.

Covers: campobet, quickcasino, betinia, swiper, lodur, dbet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, PlacementResult, HistoryEntry, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class AltenarWorkflow(ProviderWorkflow):
    platform = "altenar"

    def __init__(self, provider_id: str, domain: str,
                 mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    async def check_login(self, page: "Page") -> bool:
        """Hit balance API — 200 means logged in."""
        url = f"https://{self.domain}/sv/api/v3/account/balance"
        result = await self._evaluate_api(page, url)
        if result and "__error" not in result:
            logger.info(f"[{self.provider_id}] Logged in")
            return True
        return False

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """Fetch bet history from Altenar widgetBetHistory endpoint."""
        url = "https://sb2bethistory-gateway-altenar2.biahosted.com/api/WidgetReports/widgetBetHistory"
        # Need to discover the correct request body format per provider
        # For now return empty — interceptor handles history when user browses to it
        return []

    async def sync_balance(self, page: "Page") -> float:
        """Read balance from Altenar balance API."""
        url = f"https://{self.domain}/sv/api/v3/account/balance"
        result = await self._evaluate_api(page, url)
        if result and "result" in result:
            cash = result.get("result", {}).get("cash", {})
            balance = float(cash.get("total", 0))
            logger.info(f"[{self.provider_id}] Balance: {balance} SEK")
            return balance
        return -1

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Not yet wired — user navigates manually in guided mode."""
        logger.info(
            f"[{self.provider_id}] Navigate to: {bet.display_home} vs {bet.display_away}"
        )
        return True

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Manual — user places in mirror, interceptor catches POST placeWidget."""
        logger.info(
            f"[{self.provider_id}] Manual: {bet.display_home} vs {bet.display_away} "
            f"{bet.market} {bet.outcome} @ {bet.odds} stake={stake}"
        )
        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="manual_placement",
        )
```

- [ ] **Step 2: Write gecko.py**

Create `backend/src/mirror/workflows/gecko.py`:

```python
"""GeckoWorkflow — API-based balance, guided bet placement.

Covers: spelklubben, betsson, betsafe, nordicbet, bethard.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, PlacementResult, HistoryEntry, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class GeckoWorkflow(ProviderWorkflow):
    platform = "gecko_v2"

    def __init__(self, provider_id: str, domain: str,
                 mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    async def check_login(self, page: "Page") -> bool:
        """Hit wallets API — 200 means logged in."""
        url = f"https://cloud-api.{self.domain}/wallets"
        result = await self._evaluate_api(page, url)
        if result and "__error" not in result:
            logger.info(f"[{self.provider_id}] Logged in")
            return True
        return False

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """Not yet discovered — return empty."""
        return []

    async def sync_balance(self, page: "Page") -> float:
        """Read balance from Gecko wallets API."""
        url = f"https://cloud-api.{self.domain}/wallets"
        result = await self._evaluate_api(page, url)
        if result and "Balances" in result:
            sek = result.get("Balances", {}).get("SEK", {}).get("Real", {})
            balance = float(sek.get("Balance", 0))
            logger.info(f"[{self.provider_id}] Balance: {balance} SEK")
            return balance
        return -1

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Not yet wired — user navigates manually."""
        logger.info(
            f"[{self.provider_id}] Navigate to: {bet.display_home} vs {bet.display_away}"
        )
        return True

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Manual — user places in mirror, interceptor catches POST /api/sb/v2/coupons."""
        logger.info(
            f"[{self.provider_id}] Manual: {bet.display_home} vs {bet.display_away} "
            f"{bet.market} {bet.outcome} @ {bet.odds} stake={stake}"
        )
        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="manual_placement",
        )
```

- [ ] **Step 3: Verify imports**

Run: `cd backend && python -c "from src.mirror.workflows.altenar import AltenarWorkflow; from src.mirror.workflows.gecko import GeckoWorkflow; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/src/mirror/workflows/altenar.py backend/src/mirror/workflows/gecko.py
git commit -m "feat(workflows): add AltenarWorkflow and GeckoWorkflow — API balance, guided placement"
```

---

### Task 6: Create KambiWorkflow stub

**Files:**
- Create: `backend/src/mirror/workflows/kambi.py`

Kambi is WebSocket-based. Balance endpoint is known for some operators. Everything else needs discovery.

- [ ] **Step 1: Write kambi.py**

Create `backend/src/mirror/workflows/kambi.py`:

```python
"""KambiWorkflow — WS-based platform, guided mode only.

Covers: unibet, leovegas, expekt, 888sport, speedybet, x3000, goldenbull, 1x2, betmgm.
Balance endpoint is known for unibet (/wallitt/mainbalance).
Bet placement goes through WS on push.aws.kambicdn.com — interceptor catches it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, PlacementResult, HistoryEntry, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Known balance endpoints per Kambi operator
_BALANCE_ENDPOINTS = {
    "unibet": "/wallitt/mainbalance",
}


class KambiWorkflow(ProviderWorkflow):
    platform = "kambi"

    def __init__(self, provider_id: str, domain: str,
                 mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    async def check_login(self, page: "Page") -> bool:
        """Try known balance endpoint if available, otherwise assume logged in."""
        endpoint = _BALANCE_ENDPOINTS.get(self.provider_id)
        if endpoint:
            url = f"https://{self.domain}{endpoint}"
            result = await self._evaluate_api(page, url)
            if result and "__error" not in result:
                logger.info(f"[{self.provider_id}] Logged in")
                return True
            return False
        # No known endpoint — assume logged in if page is open
        return True

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """Not wired — Kambi history is in WS frames, needs investigation."""
        return []

    async def sync_balance(self, page: "Page") -> float:
        """Try known balance endpoint if available."""
        endpoint = _BALANCE_ENDPOINTS.get(self.provider_id)
        if endpoint:
            url = f"https://{self.domain}{endpoint}"
            result = await self._evaluate_api(page, url)
            if result and "balance" in result:
                cash = result.get("balance", {}).get("cash", 0)
                balance = float(cash)
                logger.info(f"[{self.provider_id}] Balance: {balance} SEK")
                return balance
        return -1

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Not yet wired — user navigates manually."""
        logger.info(
            f"[{self.provider_id}] Navigate to: {bet.display_home} vs {bet.display_away}"
        )
        return True

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Manual — user places in mirror, interceptor catches WS frames."""
        logger.info(
            f"[{self.provider_id}] Manual: {bet.display_home} vs {bet.display_away} "
            f"{bet.market} {bet.outcome} @ {bet.odds} stake={stake}"
        )
        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="manual_placement",
        )
```

- [ ] **Step 2: Verify full registry works**

Run: `cd backend && python -c "from src.mirror.workflows import get_workflow; w = get_workflow('polymarket'); print(type(w).__name__); w2 = get_workflow('betsson'); print(type(w2).__name__)"`
Expected:
```
PolymarketWorkflow
GeckoWorkflow
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/mirror/workflows/kambi.py
git commit -m "feat(workflows): add KambiWorkflow stub — guided mode, WS-based"
```

---

### Task 7: Refactor fire_window.py to use workflows

**Files:**
- Modify: `backend/src/services/fire_window.py`

This is the core task — replace all `if pid == "polymarket"` and `if pid == "pinnacle"` branches with workflow calls.

- [ ] **Step 1: Add workflow import and helper at top of fire_window.py**

After the existing imports (around line 14), add:

```python
from ..mirror.workflows import get_workflow
```

- [ ] **Step 2: Refactor `open_window()` — replace Polymarket/Pinnacle metadata resolution**

Replace the Polymarket metadata resolution block (lines 109-122) and the Pinnacle matchup resolution block (lines 124-147) with a generic metadata resolution approach.

The Polymarket metadata (market_slug, poly_outcome) and Pinnacle metadata (matchup_id) come from the Odds table's `provider_meta` field. Generalize to resolve provider_meta for ALL providers:

Replace lines 109-147 (the two provider-specific blocks) with:

```python
    # Resolve provider-specific metadata from DB (market_slug, matchup_id, etc.)
    _resolve_provider_meta(provider_bets)
```

Add a new function that combines the logic of `_resolve_polymarket_meta` and the Pinnacle matchup resolution:

```python
def _resolve_provider_meta(provider_bets: dict[str, list[FireWindowBet]]) -> None:
    """Resolve provider-specific metadata from Odds table for all providers.

    Polymarket: market_slug, poly_outcome, display names from provider_meta.
    Pinnacle: matchup_id from provider_meta.
    Others: no metadata needed yet.
    """
    # Polymarket metadata
    if "polymarket" in provider_bets:
        poly_meta = _resolve_polymarket_meta(provider_bets["polymarket"])
        for bet in provider_bets["polymarket"]:
            meta = poly_meta.get(bet.event_id)
            if meta:
                bet.market_slug = meta["market_slug"]
                outcome_map = meta.get("poly_outcome_map", {})
                bet.poly_outcome = outcome_map.get(bet.outcome)
                if meta.get("poly_home"):
                    bet.display_home = meta["poly_home"]
                if meta.get("poly_away"):
                    bet.display_away = meta["poly_away"]

    # Pinnacle metadata
    if "pinnacle" in provider_bets:
        pin_event_ids = list({b.event_id for b in provider_bets["pinnacle"]})
        if pin_event_ids:
            db = get_session()
            try:
                rows = (
                    db.query(Odds)
                    .filter(Odds.provider_id == "pinnacle", Odds.event_id.in_(pin_event_ids))
                    .all()
                )
                matchup_map: dict[str, str] = {}
                for row in rows:
                    meta = row.provider_meta or {}
                    mid = meta.get("matchup_id")
                    if mid and row.event_id not in matchup_map:
                        matchup_map[row.event_id] = str(mid)
                for bet in provider_bets["pinnacle"]:
                    bet.matchup_id = matchup_map.get(bet.event_id)
            finally:
                db.close()
```

This preserves exact existing behavior but groups it under one function.

- [ ] **Step 3: Refactor `check_bet()` — replace provider-specific price checking**

Replace the Polymarket and Pinnacle branches in `check_bet()` (lines 590-641) with:

```python
async def check_bet(bet_id: int, mirror_service) -> dict:
    """Check live price for a specific bet. Returns price comparison."""
    if _window is None:
        return {"error": "no fire window open"}

    pid = _window.current_provider
    bets = _window.provider_bets.get(pid, [])
    bet = next((b for b in bets if b.bet_id == bet_id), None)
    if bet is None:
        return {"error": f"bet {bet_id} not found"}

    live_edge = None
    live_cents = None

    if mirror_service is not None:
        workflow = get_workflow(pid)
        context = getattr(mirror_service, 'interceptor', None)
        context = getattr(context, 'context', None) if context else None
        if context:
            page = await workflow.find_tab(context)
            if page:
                # Navigate to event if needed
                await workflow.navigate_to_event(page, bet)
                # Check live price (returns None if not supported)
                live_edge = await workflow.check_live_price(page, bet)
                    live_cents = getattr(bet, '_live_cents', None)

    db_cents = round((1 / bet.odds) * 100) if bet.odds > 1 else 0
    fair_cents = round((1 / bet.fair_odds) * 100) if bet.fair_odds > 1 else 0

    return {
        "bet_id": bet_id,
        "db_cents": db_cents,
        "live_cents": live_cents,
        "fair_cents": fair_cents,
        "db_edge": bet.edge_pct,
        "live_edge": live_edge,
        "is_positive": (live_edge or bet.edge_pct) > 0,
    }
```

- [ ] **Step 4: Refactor `place_bet()` — replace the Polymarket branch with workflow**

Replace `place_bet()` (lines 657-741) with:

```python
async def place_bet(bet_id: int, mirror_service) -> dict:
    """Place a single confirmed bet, record to DB, sync balance."""
    if _window is None:
        return {"error": "no fire window open"}

    pid = _window.current_provider
    bets = _window.provider_bets.get(pid, [])
    bet = next((b for b in bets if b.bet_id == bet_id), None)
    if bet is None:
        return {"error": f"bet {bet_id} not found"}

    # Track fired bets
    fired_key = f"{pid}_bet_ids"
    if fired_key not in _window.fired_results:
        _window.fired_results[fired_key] = set()
    _window.fired_results[fired_key].add(bet_id)

    # Adjust stake to available balance
    balance = float('inf')
    try:
        from ..repositories.profile_repo import ProfileRepo
        _db = get_session()
        try:
            _repo = ProfileRepo(_db)
            _profile = _repo.get_active()
            if _profile:
                balance = _repo.get_balance(_profile.id, pid)
        finally:
            _db.close()
    except Exception:
        pass

    actual_stake = min(bet.stake, balance)
    # Polymarket rounds to $1, others to nearest 10 kr
    if pid == "polymarket":
        actual_stake = float(int(actual_stake))
    else:
        actual_stake = float(int(actual_stake / 10) * 10)
    min_bet = 1.0 if pid == "polymarket" else 10.0
    if actual_stake < min_bet:
        return {"status": "skipped", "bet_id": bet_id, "reason": "insufficient_balance"}

    label = f"*{bet.display_home} vs {bet.display_away}*{bet.market}*{bet.outcome}*"

    # Use workflow for placement
    workflow = get_workflow(pid)
    context = getattr(mirror_service, 'interceptor', None) if mirror_service else None
    context = getattr(context, 'context', None) if context else None

    if context:
        page = await workflow.find_tab(context)
    else:
        page = None

    if page is None and workflow.mode.value == "autonomous":
        print(f"  {label}FAILED no tab*")
        return {"status": "failed", "bet_id": bet_id, "reason": "no_tab"}

    if page:
        result = await workflow.place_bet(page, bet, actual_stake)
    else:
        from ..mirror.workflows.base import PlacementResult
        result = PlacementResult(status="manual", bet_id=bet_id, actual_stake=actual_stake)

    if result.status == "placed":
        _record_bet(bet, pid, result.raw_response or {}, actual_stake)
        _sync_balance_after_bet(bet, pid)
        print(f"  {label}PLACED*")
    elif result.status == "manual":
        print(f"  {label}MANUAL — place in mirror, interceptor records*")
    else:
        print(f"  {label}{result.status.upper()} {result.reason or ''}*")

    return {
        "status": result.status,
        "bet_id": bet_id,
        "provider_id": pid,
        "stake": actual_stake,
        "reason": result.reason,
        **({"actual_odds": result.actual_odds} if result.actual_odds else {}),
    }
```

- [ ] **Step 5: Refactor `fire_provider()` — replace batch fire Polymarket branch**

Replace `fire_provider()` (lines ~815-940) with workflow-based version:

```python
async def fire_provider(mirror_service) -> dict:
    """Fire all +EV bets for the current provider."""
    if _window is None:
        return {"error": "no fire window open"}

    pid = _window.current_provider
    if pid is None:
        return {"error": "no active provider"}

    _window.status = "firing"
    bets = _window.provider_bets.get(pid, [])
    sorted_bets = sorted(bets, key=lambda b: -b.edge_pct)

    from ..repositories.profile_repo import ProfileRepo
    db = get_session()
    try:
        profile_repo = ProfileRepo(db)
        profile = profile_repo.get_active()
        balance = profile_repo.get_balance(profile.id, pid) if profile else float("inf")
    finally:
        db.close()

    remaining = balance
    placed = []
    failed = []
    excluded = []

    workflow = get_workflow(pid)
    context = getattr(mirror_service, 'interceptor', None) if mirror_service else None
    context = getattr(context, 'context', None) if context else None
    page = await workflow.find_tab(context) if context else None

    for bet in sorted_bets:
        label = f"*{bet.display_home} vs {bet.display_away}*{bet.market}*{bet.outcome}*"

        # Check live edge if workflow supports it
        edge = bet.edge_pct
        if page:
            live_edge = await workflow.check_live_price(page, bet)
            if live_edge is not None:
                edge = live_edge

        if edge <= 0:
            print(f"  {label}SKIP edge={edge:.1f}%*")
            excluded.append({"bet_id": bet.bet_id, "reason": "negative_edge"})
            continue

        if remaining < bet.stake:
            print(f"  {label}SKIP balance*")
            excluded.append({"bet_id": bet.bet_id, "reason": "insufficient_balance"})
            continue

        print(f"  {label}FIRE edge={edge:.1f}%*")

        actual_stake = min(bet.stake, remaining)
        if pid == "polymarket":
            actual_stake = float(int(actual_stake))
        else:
            actual_stake = float(int(actual_stake / 10) * 10)

        if page:
            result = await workflow.place_bet(page, bet, actual_stake)
        else:
            from ..mirror.workflows.base import PlacementResult
            result = PlacementResult(status="manual", bet_id=bet.bet_id, actual_stake=actual_stake)

        if result.status == "placed":
            placed.append({"bet_id": bet.bet_id, "status": "placed", "stake": actual_stake})
            _record_bet(bet, pid, result.raw_response or {}, actual_stake)
            _sync_balance_after_bet(bet, pid)
            remaining -= actual_stake
        elif result.status == "manual":
            placed.append({"bet_id": bet.bet_id, "status": "manual", "provider_id": pid, "stake": actual_stake})
            remaining -= actual_stake
        else:
            failed.append({"bet_id": bet.bet_id, "reason": result.reason or result.status})

    # Cleanup (close extra tabs etc.)
    if page:
        await workflow.cleanup(page)

    fire_result = {
        "provider_id": pid,
        "placed": placed,
        "failed": failed,
        "excluded": excluded,
        "summary": {
            "total": len(bets),
            "fired": len(placed),
            "failed": len(failed),
            "excluded": len(excluded),
        },
    }

    _window.fired_results[pid] = fire_result
    _window.status = "active"
    return fire_result
```

- [ ] **Step 6: Remove the old `_check_live_price_poly` and `_check_live_price_pinnacle` functions**

Delete lines 352-461 (the `_check_live_price_poly` and `_check_live_price_pinnacle` functions). These are now in `PolymarketWorkflow.check_live_price` and `PinnacleWorkflow.check_live_price`.

- [ ] **Step 7: Refactor `get_next_bet()` stake rounding**

In `get_next_bet()` (line 529), replace the provider-specific rounding with a helper:

```python
def _round_stake(pid: str, stake: float) -> float:
    """Round stake down: $1 for Polymarket, 10 kr for others."""
    if pid == "polymarket":
        return float(int(stake))
    return float(int(stake / 10) * 10)

def _min_bet(pid: str) -> float:
    return 1.0 if pid == "polymarket" else 10.0
```

Then use `_round_stake(pid, actual_stake)` and `_min_bet(pid)` in `get_next_bet()` and `place_bet()`.

- [ ] **Step 8: Verify fire_window.py has no remaining `if pid == "polymarket"` branches**

Run: `cd backend && grep -n 'polymarket' src/services/fire_window.py`

Expected: Only `_resolve_polymarket_meta` function (which is called by the generic `_resolve_provider_meta`), `_round_stake` and `_min_bet` helpers, and the `poly_outcome`/`market_slug` fields on `FireWindowBet` dataclass.

- [ ] **Step 9: Commit**

```bash
git add backend/src/services/fire_window.py
git commit -m "refactor(fire_window): replace provider-specific branches with workflow calls"
```

---

### Task 8: Refactor fire_window route to remove Polymarket-specific code

**Files:**
- Modify: `backend/src/api/routes/fire_window.py`

- [ ] **Step 1: Replace `open_provider_tabs()` — remove Polymarket balance scrape and tab logic**

Replace lines 28-101 of `fire_window.py` route with:

```python
@router.post("/open-tabs")
async def open_provider_tabs():
    """Open mirror browser tabs for all providers in the fire window queue."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")

    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")

    context = getattr(mirror, 'interceptor', None)
    context = getattr(context, 'context', None) if context else None
    if not context:
        raise HTTPException(400, "No browser context")

    from ...config.loader import load_config
    from ...repositories.profile_repo import ProfileRepo
    from ...db.models import get_session
    from ...mirror.workflows import get_workflow

    cfg = load_config()
    db = get_session()
    try:
        repo = ProfileRepo(db)
        profile = repo.get_active()
        balances = repo.get_all_balances(profile.id) if profile else {}
    finally:
        db.close()

    opened = []
    for pid in window.provider_queue:
        if balances.get(pid, 0) < 10:
            continue

        workflow = get_workflow(pid)
        # Use workflow domain for URL, with fallbacks
        if workflow.domain:
            url = f"https://www.{workflow.domain}"
        else:
            pconfig = cfg.get_provider(pid)
            if pconfig:
                url = pconfig.site_url or (f"https://www.{pconfig.domain}" if pconfig.domain else None)
            else:
                url = None
        if not url:
            continue

        try:
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            opened.append(pid)
        except Exception as e:
            logger.warning(f"Failed to open tab for {pid}: {e}")

    return {"opened": opened, "count": len(opened)}
```

- [ ] **Step 2: Remove Polymarket-specific code from `check_bet()` route**

Replace lines 145-167 with:

```python
@router.post("/check-bet/{bet_id}")
async def check_bet(bet_id: int):
    """Check live price for a specific bet."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    mirror = _get_active_mirror()
    return await fw.check_bet(bet_id, mirror)
```

The per-bet tab opening is now handled inside `check_bet()` in `fire_window.py` via the workflow's `navigate_to_event()`.

- [ ] **Step 3: Simplify `close_fire_window()` route**

Replace lines 204-214 with:

```python
@router.post("/close")
async def close_fire_window():
    """Close fire window, cleanup."""
    fw.close_window()
    return {"status": "closed"}
```

Cleanup (closing Polymarket tabs) now happens via `workflow.cleanup()` inside `fire_provider()`.

- [ ] **Step 4: Verify route file has no Polymarket references**

Run: `cd backend && grep -n 'polymarket\|poly' src/api/routes/fire_window.py`
Expected: No matches.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/fire_window.py
git commit -m "refactor(routes): remove Polymarket-specific code from fire_window routes"
```

---

### Task 9: Verify end-to-end — registry resolves all providers

**Files:** None (verification only)

- [ ] **Step 1: Test that every provider in providers.yaml resolves to a workflow**

Run:
```bash
cd backend && python -c "
from src.mirror.workflows import get_workflow
test_providers = [
    'polymarket', 'pinnacle',
    'campobet', 'quickcasino', 'betinia', 'swiper', 'lodur', 'dbet',
    'spelklubben', 'betsson', 'betsafe', 'nordicbet', 'bethard',
    'unibet', 'leovegas', 'expekt', '888sport', 'speedybet',
    'comeon', 'hajper', 'tipwin', 'vbet',
]
for pid in test_providers:
    try:
        w = get_workflow(pid)
        print(f'{pid:20s} -> {type(w).__name__}')
    except Exception as e:
        print(f'{pid:20s} -> ERROR: {e}')
"
```

Expected output (approximate):
```
polymarket           -> PolymarketWorkflow
pinnacle             -> PinnacleWorkflow
campobet             -> AltenarWorkflow
quickcasino          -> AltenarWorkflow
betinia              -> AltenarWorkflow
swiper               -> AltenarWorkflow
lodur                -> AltenarWorkflow
dbet                 -> AltenarWorkflow
spelklubben          -> GeckoWorkflow
betsson              -> GeckoWorkflow
betsafe              -> GeckoWorkflow
nordicbet            -> GeckoWorkflow
bethard              -> GeckoWorkflow
unibet               -> KambiWorkflow
leovegas             -> KambiWorkflow
expekt               -> KambiWorkflow
888sport             -> KambiWorkflow
speedybet            -> KambiWorkflow
comeon               -> ManualWorkflow
hajper               -> ManualWorkflow
tipwin               -> ManualWorkflow
vbet                 -> ManualWorkflow
```

- [ ] **Step 2: Check for import errors**

Run: `cd backend && python -c "from src.services.fire_window import open_window, check_bet, place_bet, fire_provider; print('fire_window OK')"`
Expected: `fire_window OK`

Run: `cd backend && python -c "from src.api.routes.fire_window import router; print('routes OK')"`
Expected: `routes OK`

- [ ] **Step 3: Run existing tests**

Run: `cd backend && pytest tests/ -x -q --timeout=30 2>&1 | tail -20`
Expected: No new failures.

- [ ] **Step 4: Commit if any fixes were needed**

```bash
git add -A
git commit -m "fix(workflows): resolve import/integration issues from verification"
```

---

### Task 10: Update mirror-wiring.md with workflow status

**Files:**
- Modify: `docs/mirror-wiring.md`

- [ ] **Step 1: Add a "Workflow" column to the capabilities table**

Add a column tracking which providers have a workflow implementation:

| Symbol | Meaning |
|--------|---------|
| A | Autonomous workflow |
| G | Guided workflow (manual placement, interceptor records) |
| M | ManualWorkflow fallback |

Update each row based on the implementation:
- Polymarket: `A`
- Pinnacle: `G`
- All Altenar providers: `G`
- All Gecko V2 providers: `G`
- All Kambi providers: `G`
- Everything else: `M`

- [ ] **Step 2: Commit**

```bash
git add docs/mirror-wiring.md
git commit -m "docs: update mirror-wiring.md with workflow status column"
```
