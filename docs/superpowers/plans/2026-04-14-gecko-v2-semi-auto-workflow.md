# Gecko V2 Semi-Auto Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-navigate to events on all 5 Gecko V2 providers (betsson, betsafe, nordicbet, spelklubben, bethard) so the user doesn't have to find events manually.

**Architecture:** Store gecko event/selection IDs as `provider_meta` during extraction. At play time, build a URL from the event ID and navigate the browser. User still places bets manually; interceptor records them.

**Tech Stack:** Python / Playwright / FastAPI

---

### Task 1: Store `provider_meta` in Gecko V2 extraction

**Files:**
- Modify: `backend/src/providers/gecko_v2.py:607-696` (`_parse_markets` method)
- Modify: `backend/src/providers/gecko_v2.py:584-586` (`_parse_event` call site)

- [ ] **Step 1: Add `event_id` parameter to `_parse_markets()`**

In `backend/src/providers/gecko_v2.py`, change the method signature and the call site.

Call site at line 586 — add `event_id`:
```python
        markets = self._parse_markets(event_markets, selections_by_market, sport, event_id)
```

Method signature at line 607 — add `event_id` parameter:
```python
    def _parse_markets(
        self,
        markets_raw: list[dict],
        selections_by_market: dict[str, list[dict]],
        sport: str = "",
        event_id: str = "",
    ) -> list[dict]:
```

- [ ] **Step 2: Add `provider_meta` to outcome dicts**

In the `for sel in selections:` loop (around line 679), add `provider_meta` to each outcome dict. Change:
```python
                outcome_dict: dict[str, Any] = {
                    "name": outcome_name,
                    "odds": round(float(odds), 3),
                }
                if point is not None:
                    outcome_dict["point"] = point
                outcomes.append(outcome_dict)
```

To:
```python
                outcome_dict: dict[str, Any] = {
                    "name": outcome_name,
                    "odds": round(float(odds), 3),
                    "provider_meta": {
                        "selection_id": str(sel.get("id", "")),
                    },
                }
                if point is not None:
                    outcome_dict["point"] = point
                outcomes.append(outcome_dict)
```

- [ ] **Step 3: Add `provider_meta` to market dicts**

In the market append block (around line 688), change:
```python
            if outcomes:
                markets.append({"type": market_type, "outcomes": outcomes})
                seen_types.add(market_type)
```

To:
```python
            if outcomes:
                markets.append({
                    "type": market_type,
                    "outcomes": outcomes,
                    "provider_meta": {
                        "event_id": event_id,
                        "market_template": template_id,
                    },
                })
                seen_types.add(market_type)
```

- [ ] **Step 4: Verify extraction still works**

Run the extraction test suite:
```bash
cd backend && python -m pytest tests/ -k "gecko" -v --no-header 2>&1 | head -40
```

If no gecko-specific tests exist, do a quick sanity check that the module imports cleanly:
```bash
cd backend && python -c "from src.providers.gecko_v2 import GeckoV2Retriever; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/gecko_v2.py
git commit -m "feat(extraction): store provider_meta in Gecko V2 markets/outcomes

Stores event_id + market_template at market level, selection_id at outcome
level. Storage pipeline merges these into odds.provider_meta automatically."
```

---

### Task 2: Add `gecko_event_id` to play loop `_bet_ns()`

**Files:**
- Modify: `firevsports/mirror/play_loop.py:26-43` (`_bet_ns` function)

- [ ] **Step 1: Add gecko_event_id field**

In `firevsports/mirror/play_loop.py`, in the `_bet_ns()` function, add the Gecko field right after the Kambi fields (after line 42):

Change:
```python
    # Explicit Kambi fields — avoid collision with top-level event_id (canonical UUID)
    ns.kambi_event_id = meta.get("event_id", "")
    ns.kambi_outcome_id = meta.get("outcome_id", "")
    return ns
```

To:
```python
    # Explicit Kambi fields — avoid collision with top-level event_id (canonical UUID)
    ns.kambi_event_id = meta.get("event_id", "")
    ns.kambi_outcome_id = meta.get("outcome_id", "")
    # Gecko V2 fields — same event_id key in provider_meta, different prefix
    ns.gecko_event_id = meta.get("event_id", "")
    return ns
```

Note: Both Kambi and Gecko V2 use `event_id` as the key in `provider_meta`. This is fine — a given bet is always from one platform, never both. The play loop just flattens all known prefixes.

- [ ] **Step 2: Verify import works**

```bash
cd firevsports && python -c "from mirror.play_loop import _bet_ns; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add firevsports/mirror/play_loop.py
git commit -m "feat(play): add gecko_event_id to bet namespace

Flattens provider_meta.event_id to ns.gecko_event_id for Gecko V2
workflow navigation, same pattern as kambi_event_id."
```

---

### Task 3: Implement `navigate_to_event()` in frontend Gecko workflow

**Files:**
- Modify: `firevsports/mirror/workflows/gecko.py:1-74`

- [ ] **Step 1: Add init path map and asyncio import**

At the top of `firevsports/mirror/workflows/gecko.py`, add `asyncio` import and the init path map. Change:

```python
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)
```

To:

```python
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Betting page path per provider (default: /sv/odds for betsson/betsafe/nordicbet)
_INIT_PATHS: dict[str, str] = {
    "spelklubben": "/sv/betting",
    "bethard": "/sv/sports",
}
```

- [ ] **Step 2: Replace `navigate_to_event()` method**

Replace the no-op `navigate_to_event` method:

```python
    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """User navigates manually."""
        return True
```

With:

```python
    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Navigate to Gecko V2 event page using gecko_event_id from provider_meta.

        URL pattern: {site_url}{init_path}?eventId=f-{gecko_event_id}
        Verified: the main site passes eventId to the sportsbook iframe automatically.
        """
        gecko_eid = getattr(bet, "gecko_event_id", "")
        if not gecko_eid:
            return True  # No ID — user navigates manually

        if f"eventId=f-{gecko_eid}" in (page.url or ""):
            return True  # Already on this event

        init_path = _INIT_PATHS.get(self.provider_id, "/sv/odds")
        url = f"https://www.{self.domain}{init_path}?eventId=f-{gecko_eid}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1)
            logger.info(f"[{self.provider_id}] Navigated to event {gecko_eid}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate_to_event failed: {e}")
            return False
```

- [ ] **Step 3: Verify import**

```bash
cd firevsports && python -c "from mirror.workflows.gecko import GeckoWorkflow; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add firevsports/mirror/workflows/gecko.py
git commit -m "feat(mirror): auto-navigate to events on Gecko V2 providers

Navigates to {site_url}{init_path}?eventId=f-{id} which loads the
event page in the sportsbook iframe. Covers all 5 Gecko V2 providers."
```

---

### Task 4: Implement `navigate_to_event()` in backend Gecko workflow

**Files:**
- Modify: `backend/src/mirror/workflows/gecko.py:1-70`

- [ ] **Step 1: Add init path map and asyncio import**

Same change as Task 3 but in `backend/src/mirror/workflows/gecko.py`. Change:

```python
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)
```

To:

```python
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Betting page path per provider (default: /sv/odds for betsson/betsafe/nordicbet)
_INIT_PATHS: dict[str, str] = {
    "spelklubben": "/sv/betting",
    "bethard": "/sv/sports",
}
```

- [ ] **Step 2: Replace `navigate_to_event()` method**

Replace the no-op method:

```python
    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """User navigates manually."""
        return True
```

With:

```python
    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Navigate to Gecko V2 event page using gecko_event_id from provider_meta.

        URL pattern: {site_url}{init_path}?eventId=f-{gecko_event_id}
        Verified: the main site passes eventId to the sportsbook iframe automatically.
        """
        gecko_eid = getattr(bet, "gecko_event_id", "")
        if not gecko_eid:
            return True  # No ID — user navigates manually

        if f"eventId=f-{gecko_eid}" in (page.url or ""):
            return True  # Already on this event

        init_path = _INIT_PATHS.get(self.provider_id, "/sv/odds")
        url = f"https://www.{self.domain}{init_path}?eventId=f-{gecko_eid}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1)
            logger.info(f"[{self.provider_id}] Navigated to event {gecko_eid}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate_to_event failed: {e}")
            return False
```

- [ ] **Step 3: Verify import**

```bash
cd backend && python -c "from src.mirror.workflows.gecko import GeckoWorkflow; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/mirror/workflows/gecko.py
git commit -m "feat(mirror): backend gecko workflow navigate_to_event

Mirrors frontend gecko.py navigate — same URL pattern, same init_path map."
```
