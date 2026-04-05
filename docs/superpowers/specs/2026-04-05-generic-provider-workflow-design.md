# Generic Provider Workflow Design

**Date:** 2026-04-05
**Status:** Draft

## Context

The fire window currently has Polymarket-specific automation hardcoded (`if pid == "polymarket"` branches in `fire_window.py`, `_place_single_polymarket_bet()` in `mirror/service.py`). All other providers fall through to a "manual" path where the user places bets in the mirror browser and the interceptor catches the API call.

The goal: every provider follows the same workflow interface. Polymarket's existing automation becomes one implementation. Other platforms get wired incrementally — user clicks around in mirror to show the traffic patterns, we implement the workflow class from the intercepted data.

## Workflow Phases

Every provider goes through the same 6 phases during a fire window session:

```
1. check_login    — is the user authenticated on this provider's tab?
2. sync_history   — navigate to bet history, settle pending bets in DB
3. sync_balance   — read current balance, update ProfileRepo
4. navigate       — open the event page for the next bet
5. place_bet      — fill betslip: select outcome, enter stake, submit
6. confirm        — detect success/failure of the placement
```

Phases 4-6 repeat per bet. Phases 1-3 run once when a provider is activated in the fire window.

## Interface

```python
# backend/src/mirror/workflows/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from playwright.async_api import Page


class WorkflowMode(Enum):
    GUIDED = "guided"        # pause after each step, wait for user confirmation
    AUTONOMOUS = "autonomous" # run through without pausing


@dataclass
class PlacementResult:
    status: str           # "placed" | "failed" | "skipped" | "rejected"
    bet_id: int
    actual_odds: float | None = None    # odds at placement time (from DOM or API response)
    actual_stake: float | None = None
    reason: str | None = None           # failure/skip reason
    raw_response: dict | None = None    # provider API response if available


@dataclass
class HistoryEntry:
    provider_bet_id: str    # provider's own bet ID
    event_name: str
    market: str
    outcome: str
    odds: float
    stake: float
    status: str             # "won" | "lost" | "void" | "cashout" | "pending"
    payout: float | None = None


class ProviderWorkflow(ABC):
    """Base class for provider-specific fire workflow automation.

    Each platform (Altenar, Gecko, Kambi, Pinnacle, Polymarket) implements
    this interface. The fire window calls the same methods regardless of
    provider. Platform siblings (e.g., all Altenar sites) share one
    implementation with domain/config differences.
    """

    platform: str           # "altenar", "gecko", "kambi", "pinnacle", "polymarket"
    mode: WorkflowMode = WorkflowMode.GUIDED

    def __init__(self, provider_id: str, domain: str):
        self.provider_id = provider_id
        self.domain = domain

    @abstractmethod
    async def check_login(self, page: Page) -> bool:
        """Check if user is logged in on this provider's page.

        Implementation varies per platform:
        - API-based: hit a balance/session endpoint, check for 200 vs 401
        - DOM-based: look for a username/balance element

        Returns True if logged in, False otherwise.
        """

    @abstractmethod
    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Navigate to bet history and return settled bets.

        The fire window service handles DB reconciliation — this method
        just returns what the provider says.

        For API-based platforms: make the API call directly via page.evaluate
        or page.request. For DOM-based: navigate to history page, scrape.
        """

    @abstractmethod
    async def sync_balance(self, page: Page) -> float:
        """Read current balance from the provider.

        Returns balance in the provider's native currency (SEK for Swedish
        bookmakers, USDC for Polymarket, SEK for Pinnacle).
        """

    @abstractmethod
    async def navigate_to_event(self, page: Page, bet: "FireWindowBet") -> bool:
        """Navigate the page to the event for this bet.

        Uses whatever data is available on FireWindowBet:
        - event_id, display_home, display_away for team-name matching
        - market_slug for Polymarket
        - matchup_id for Pinnacle
        - sport for URL construction

        Returns True if successfully navigated to the event page.
        """

    @abstractmethod
    async def place_bet(self, page: Page, bet: "FireWindowBet", stake: float) -> PlacementResult:
        """Place a bet: select outcome, enter stake, submit.

        For autonomous mode: full DOM automation (click outcome, fill stake,
        click confirm). For guided mode: navigate to the right spot, then
        pause — user clicks confirm manually, interceptor catches the result.

        The method should verify the live odds before submitting and abort
        if slippage exceeds the platform's threshold.
        """

    async def await_confirmation(self, page: Page, timeout_s: float = 15.0) -> PlacementResult:
        """Wait for the provider to confirm or reject the bet.

        Default implementation: returns the PlacementResult from place_bet
        unchanged. This works for API-based platforms where the HTTP response
        IS the confirmation.

        Override for DOM-based platforms (Polymarket) where you need to watch
        for a success toast or transaction confirmation after clicking "Buy."
        """
        # Default: no-op — place_bet already has the result
        raise NotImplementedError("Subclass must pass result through or override")

    # -- Shared helpers (non-abstract) --

    async def find_tab(self, context) -> Page | None:
        """Find this provider's tab in the browser context."""
        for page in context.pages:
            if self.domain in (page.url or ""):
                return page
        return None
```

## Platform Implementations

### Altenar (campobet, quickcasino, betinia, swiper, lodur, dbet)

Already discovered from interceptor traffic:

| Phase | Method | Details |
|-------|--------|---------|
| check_login | `GET {domain}/sv/api/v3/account/balance` | 200 = logged in, 401 = not |
| sync_history | `POST sb2bethistory-gateway-altenar2.biahosted.com/api/WidgetReports/widgetBetHistory` | status: 1=won, 2=lost, 3=void, 4=cashout |
| sync_balance | `GET {domain}/sv/api/v3/account/balance` | `result.cash.total` |
| navigate | Not yet discovered — wire via mirror session | |
| place_bet | `POST sb2betgateway-altenar2.biahosted.com/api/widget/placeWidget` | Request body format to be captured during wiring |
| confirm | Response to placeWidget POST | |

**Wiring status: login/balance/history already intercepted. navigate + place_bet DOM interaction needs discovery.**

### Gecko V2 (spelklubben, betsson, betsafe, nordicbet, bethard)

| Phase | Method | Details |
|-------|--------|---------|
| check_login | `GET cloud-api.{domain}/wallets` | 200 = logged in |
| sync_history | TBD — no history endpoint discovered yet | |
| sync_balance | `GET cloud-api.{domain}/wallets` | `Balances.SEK.Real.Balance` |
| navigate | TBD | |
| place_bet | `POST {domain}/api/sb/v2/coupons` | couponId in response |
| confirm | Response to coupons POST | |

**Wiring status: login/balance/placement intercepted. history + navigate needs discovery.**

### Kambi (unibet, leovegas, expekt, 888sport, speedybet, x3000, goldenbull, 1x2, betmgm)

| Phase | Method | Details |
|-------|--------|---------|
| check_login | `GET {domain}/wallitt/mainbalance` (Unibet pattern) | `balance.cash` |
| sync_history | WS frames — needs investigation | |
| sync_balance | `GET {domain}/wallitt/mainbalance` | |
| navigate | `#/event/{kambiEventId}` deeplink (if available) | |
| place_bet | WS frame on `push.aws.kambicdn.com` | Contains couponId, odds, stake |
| confirm | WS response frame | |

**Wiring status: balance intercepted. Everything else via WebSocket — needs structured discovery session.**

### Pinnacle

| Phase | Method | Details |
|-------|--------|---------|
| check_login | `GET api.arcadia.pinnacle.se/0.1/wallet/balance` | 200 = logged in |
| sync_history | `GET arcadia.pinnacle.se/0.1/bets` | Bet history API |
| sync_balance | `GET api.arcadia.pinnacle.se/0.1/wallet/balance` | `{"amount": X, "currency": "SEK"}` |
| navigate | `pinnacle.se/en/sport/{matchupId}` | matchup_id already on FireWindowBet |
| place_bet | `POST api.arcadia.pinnacle.se/0.1/bets/straight` | Already intercepted |
| confirm | Response to bets/straight POST | |

**Wiring status: most phases have known endpoints. DOM interaction for betslip fill needs discovery.**

### Polymarket

Already fully automated. Extract existing code from `mirror/service.py`:

| Phase | Method | Details |
|-------|--------|---------|
| check_login | DOM scrape for "Cash$" element | |
| sync_history | Gamma API `fetch_resolved()` | |
| sync_balance | `GET data-api.polymarket.com/value?user={proxy_wallet}` or DOM | |
| navigate | `polymarket.com/event/{market_slug}` | |
| place_bet | DOM: click outcome btn → fill amount → click "Buy" | |
| confirm | DOM: watch for success state / Fun.xyz tx confirmation | |

**Wiring status: fully wired. Code exists in `_place_single_polymarket_bet()` — needs extraction into workflow class.**

## Workflow Registry

```python
# backend/src/mirror/workflows/__init__.py

from .altenar import AltenarWorkflow
from .gecko import GeckoWorkflow
from .kambi import KambiWorkflow
from .pinnacle import PinnacleWorkflow
from .polymarket import PolymarketWorkflow

_PLATFORM_MAP = {
    "altenar": AltenarWorkflow,
    "gecko": GeckoWorkflow,
    "kambi": KambiWorkflow,
    "pinnacle": PinnacleWorkflow,
    "polymarket": PolymarketWorkflow,
}

# provider_id → platform (loaded from providers.yaml)
def get_workflow(provider_id: str) -> ProviderWorkflow:
    from ...config.loader import load_config
    cfg = load_config()
    provider = cfg.get_provider(provider_id)
    platform = provider.platform  # "altenar", "gecko", "kambi", etc.
    domain = provider.domain
    cls = _PLATFORM_MAP.get(platform)
    if cls is None:
        raise ValueError(f"No workflow for platform '{platform}'")
    return cls(provider_id=provider_id, domain=domain)
```

## Fire Window Integration

`fire_window.py` changes:

**Before (current):**
```python
if pid == "polymarket" and mirror_service is not None:
    placement_result = await mirror_service._place_single_polymarket_bet(...)
else:
    placement_result = {"status": "manual", ...}
```

**After:**
```python
from ..mirror.workflows import get_workflow

workflow = get_workflow(pid)
page = await workflow.find_tab(mirror_service.interceptor.context)
if page is None:
    return {"status": "skipped", "reason": "no_tab"}

if not await workflow.check_login(page):
    return {"status": "skipped", "reason": "not_logged_in"}

# Sync once per provider activation (not per bet)
await workflow.sync_history(page)
balance = await workflow.sync_balance(page)

# Per bet:
if not await workflow.navigate_to_event(page, bet):
    return {"status": "failed", "reason": "navigation_failed"}

result = await workflow.place_bet(page, bet, actual_stake)
if result.status == "placed":
    # For API-based platforms, place_bet already has the confirmation.
    # For DOM-based (Polymarket), await_confirmation watches for success toast.
    confirmation = await workflow.await_confirmation(page)
    _record_bet(bet, pid, confirmation.raw_response or {}, actual_stake)
    _sync_balance_after_bet(bet, pid)
    return {"status": "placed", **confirmation.__dict__}

return result.__dict__
```

All `if pid == "polymarket"` branches in `fire_window.py`, `open_provider_tabs()`, `check_bet()`, `close_fire_window()` get replaced with workflow method calls.

## Guided vs Autonomous Mode

Configured per platform in `providers.yaml`:

```yaml
platforms:
  altenar:
    workflow_mode: guided    # pause after each step
  polymarket:
    workflow_mode: autonomous # full auto
```

In guided mode, each workflow method that would take an action (navigate, place, confirm) instead returns a "pending" state. The frontend shows what's about to happen and the user clicks "proceed." This is implemented in the base class:

```python
async def _guided_pause(self, action: str, details: dict) -> bool:
    """In guided mode, emit SSE event and wait for user confirmation.
    In autonomous mode, return True immediately.

    Uses the existing MirrorService broadcaster (SSE) — same mechanism
    that already pushes provider_opened and balance_synced events to
    the frontend. Frontend shows the pending action + "Proceed" button.
    POST /api/fire-window/confirm-step sets an asyncio.Event that
    unblocks this coroutine.
    """
    if self.mode == WorkflowMode.AUTONOMOUS:
        return True
    self._broadcast("workflow_pause", {"action": action, **details})
    return await self._wait_for_confirmation(timeout_s=120)
```

## Wiring New Platforms

The process for wiring a new platform:

1. User opens mirror, navigates to the provider site
2. User tells Claude: "wiring betsson, doing bet_history now"
3. User clicks through the bet history page
4. Claude sees intercepted traffic (already captured by `NetworkRecorder` + interceptor callbacks)
5. Claude implements `GeckoWorkflow.sync_history()` based on the observed API patterns
6. Repeat for each phase
7. Update `docs/mirror-wiring.md` status from `-` to `Y`

Platform siblings get the implementation for free — `AltenarWorkflow` works for all 6 Altenar providers, just different `domain` values.

## Files to Create

| File | Purpose |
|------|---------|
| `backend/src/mirror/workflows/__init__.py` | Registry + `get_workflow()` |
| `backend/src/mirror/workflows/base.py` | `ProviderWorkflow` ABC, `PlacementResult`, `HistoryEntry`, `WorkflowMode` |
| `backend/src/mirror/workflows/polymarket.py` | Extract from `mirror/service.py` |
| `backend/src/mirror/workflows/pinnacle.py` | From existing interceptor patterns |
| `backend/src/mirror/workflows/altenar.py` | From existing interceptor patterns |
| `backend/src/mirror/workflows/gecko.py` | From existing interceptor patterns |
| `backend/src/mirror/workflows/kambi.py` | Stub — WS-based, needs discovery |

## Files to Modify

| File | Change |
|------|--------|
| `backend/src/services/fire_window.py` | Replace all `if pid == "polymarket"` with workflow calls |
| `backend/src/api/routes/fire_window.py` | Remove Polymarket-specific tab logic, add `/confirm-step` endpoint |
| `backend/src/mirror/service.py` | Remove `_place_single_polymarket_bet`, `_scrape_polymarket_balance`, `_poly_tabs` — moved to `PolymarketWorkflow` |
| `backend/src/config/providers.yaml` | Add `platform` and `workflow_mode` fields per provider |
| `docs/mirror-wiring.md` | Update as platforms get wired |

## Verification

1. **Polymarket (regression):** Fire window activates Polymarket → workflow runs check_login → sync_balance → navigate → place_bet → confirm. Same behavior as today, just routed through `PolymarketWorkflow`.
2. **Pinnacle (guided):** Fire window activates Pinnacle → check_login via wallet API → sync_balance → navigate to matchup URL → pause (guided mode) → user places manually → interceptor catches → confirm.
3. **Altenar (guided):** Same flow — login check via balance API, navigate to event, pause for manual placement.
4. **No regressions:** Interceptor still catches all bet placements. `mirror-wiring.md` capabilities unchanged.
