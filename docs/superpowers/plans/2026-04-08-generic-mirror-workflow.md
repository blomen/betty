# Generic Mirror Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a data-driven GenericWorkflow that reads per-provider intel JSON files and executes the full mirror lifecycle (login, history sync, balance sync, navigation, bet placement) for any provider — replacing ManualWorkflow as the default fallback.

**Architecture:** Intel JSON files store discovered selectors/endpoints per provider. A discovery engine mines JSONL recordings + live DOM to populate intel. Optional Python strategy files override individual methods for edge cases. GenericWorkflow loads intel + strategy at init and dispatches each ABC method accordingly.

**Tech Stack:** Python 3.10+, Playwright async API, JSON files for intel storage, importlib for dynamic strategy loading.

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `backend/src/mirror/workflows/generic.py` | GenericWorkflow class — reads intel JSON, dispatches methods |
| Create | `backend/src/mirror/workflows/discovery.py` | Discovery engine — mines JSONL + DOM, writes intel JSON |
| Create | `backend/src/mirror/workflows/strategies/__init__.py` | Strategy dataclass + loader |
| Create | `backend/data/mirror_intel/.gitkeep` | Intel directory (JSON files gitignored except .gitkeep) |
| Modify | `backend/src/mirror/workflows/__init__.py:19-40` | Route unwired platforms to GenericWorkflow |
| Create | `tests/test_generic_workflow.py` | Tests for GenericWorkflow + discovery |

---

### Task 1: Intel schema + loader/saver

**Files:**
- Create: `backend/src/mirror/workflows/generic.py`
- Create: `backend/data/mirror_intel/.gitkeep`
- Test: `tests/test_generic_workflow.py`

- [ ] **Step 1: Write failing test for load_intel**

Create `tests/test_generic_workflow.py`:

```python
"""Tests for GenericWorkflow intel loading and method dispatch."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def intel_dir(tmp_path):
    d = tmp_path / "mirror_intel"
    d.mkdir()
    return d


@pytest.fixture
def sample_intel():
    return {
        "provider_id": "testprovider",
        "platform": "custom",
        "discovered_at": "2026-04-08T14:30:00Z",
        "updated_at": "2026-04-08T14:30:00Z",
        "capabilities": {
            "login": "discovered",
            "balance": "discovered",
            "history": "none",
            "placement": "none",
        },
        "login": {
            "method": "dom",
            "indicator": {"selector": ".user-balance", "regex": r"[\d.,]+"},
        },
        "balance": {
            "method": "api",
            "api": {"url": "/api/wallet/balance", "path": "data.balance", "currency": "SEK"},
            "dom": None,
        },
        "history": None,
        "betslip": None,
        "navigation": None,
        "api_endpoints": {},
        "notes": "",
    }


def test_load_intel_returns_dict(intel_dir, sample_intel):
    from backend.src.mirror.workflows.generic import load_intel
    # Write intel file
    (intel_dir / "testprovider.json").write_text(json.dumps(sample_intel))
    result = load_intel("testprovider", intel_dir)
    assert result["provider_id"] == "testprovider"
    assert result["capabilities"]["balance"] == "discovered"


def test_load_intel_missing_returns_none(intel_dir):
    from backend.src.mirror.workflows.generic import load_intel
    result = load_intel("nonexistent", intel_dir)
    assert result is None


def test_save_intel_roundtrip(intel_dir, sample_intel):
    from backend.src.mirror.workflows.generic import save_intel, load_intel
    save_intel("testprovider", sample_intel, intel_dir)
    result = load_intel("testprovider", intel_dir)
    assert result["provider_id"] == "testprovider"
    assert result["balance"]["api"]["path"] == "data.balance"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py -v -x`
Expected: FAIL — `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Implement load_intel and save_intel**

Create `backend/src/mirror/workflows/generic.py`:

```python
"""GenericWorkflow — data-driven workflow for any provider using intel JSON + strategy overrides."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


def _default_intel_dir() -> Path:
    from ...paths import get_data_dir
    d = get_data_dir() / "mirror_intel"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_intel(provider_id: str, intel_dir: Path | None = None) -> dict | None:
    """Load intel JSON for a provider. Returns None if not found."""
    d = intel_dir or _default_intel_dir()
    path = d / f"{provider_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[generic] Failed to load intel for {provider_id}: {e}")
        return None


def save_intel(provider_id: str, intel: dict, intel_dir: Path | None = None) -> Path:
    """Save intel JSON for a provider. Returns path written."""
    d = intel_dir or _default_intel_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{provider_id}.json"
    path.write_text(json.dumps(intel, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"[generic] Saved intel for {provider_id} → {path}")
    return path
```

- [ ] **Step 4: Create mirror_intel directory**

```bash
mkdir -p backend/data/mirror_intel
touch backend/data/mirror_intel/.gitkeep
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py -v -x`
Expected: 3 PASSED

- [ ] **Step 6: Commit**

```bash
git add backend/src/mirror/workflows/generic.py backend/data/mirror_intel/.gitkeep tests/test_generic_workflow.py
git commit -m "feat(mirror): add intel JSON loader/saver for generic workflow"
```

---

### Task 2: Strategy dataclass + dynamic loader

**Files:**
- Create: `backend/src/mirror/workflows/strategies/__init__.py`
- Test: `tests/test_generic_workflow.py` (append)

- [ ] **Step 1: Write failing test for load_strategy**

Append to `tests/test_generic_workflow.py`:

```python
def test_load_strategy_missing_returns_none():
    from backend.src.mirror.workflows.strategies import load_strategy
    result = load_strategy("nonexistent_provider_xyz")
    assert result is None


def test_load_strategy_loads_module(tmp_path):
    """Strategy module with a sync_balance override."""
    from backend.src.mirror.workflows.strategies import Strategy
    s = Strategy(sync_balance=lambda page, intel: 42.0)
    assert s.sync_balance is not None
    assert s.check_login is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py::test_load_strategy_missing_returns_none -v -x`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement Strategy dataclass and loader**

Create `backend/src/mirror/workflows/strategies/__init__.py`:

```python
"""Strategy overrides for GenericWorkflow.

Each provider can optionally have a strategies/{provider_id}.py file
that exports a `strategy` attribute of type Strategy. Only methods
that need custom logic should be set — the rest use intel JSON.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Callable, Any

logger = logging.getLogger(__name__)


@dataclass
class Strategy:
    """Optional per-provider method overrides.

    Each field is an async callable(page, intel) -> result, or None to use generic.
    """
    check_login: Callable | None = None
    sync_balance: Callable | None = None
    sync_history: Callable | None = None
    navigate_to_event: Callable | None = None
    place_bet: Callable | None = None
    check_live_price: Callable | None = None


def load_strategy(provider_id: str) -> Strategy | None:
    """Import strategies/{provider_id}.py if it exists, return .strategy attr."""
    try:
        mod = importlib.import_module(f"src.mirror.workflows.strategies.{provider_id}")
        return getattr(mod, "strategy", None)
    except ModuleNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"[generic] Failed to load strategy for {provider_id}: {e}")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py -v -x -k "strategy"`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/workflows/strategies/__init__.py tests/test_generic_workflow.py
git commit -m "feat(mirror): add Strategy dataclass and dynamic loader"
```

---

### Task 3: GenericWorkflow class — core methods

**Files:**
- Modify: `backend/src/mirror/workflows/generic.py`
- Test: `tests/test_generic_workflow.py` (append)

- [ ] **Step 1: Write failing test for GenericWorkflow.sync_balance (API path)**

Append to `tests/test_generic_workflow.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock


def test_generic_workflow_init_with_intel(intel_dir, sample_intel):
    from backend.src.mirror.workflows.generic import GenericWorkflow, save_intel
    save_intel("testprovider", sample_intel, intel_dir)
    wf = GenericWorkflow("testprovider", "test.com", intel_dir=intel_dir)
    assert wf.intel is not None
    assert wf.intel["provider_id"] == "testprovider"
    assert wf.mode.value == "guided"


def test_generic_workflow_init_no_intel(intel_dir):
    from backend.src.mirror.workflows.generic import GenericWorkflow
    wf = GenericWorkflow("unknown", "unknown.com", intel_dir=intel_dir)
    assert wf.intel is None


def test_sync_balance_api(intel_dir, sample_intel):
    from backend.src.mirror.workflows.generic import GenericWorkflow, save_intel
    save_intel("testprovider", sample_intel, intel_dir)
    wf = GenericWorkflow("testprovider", "test.com", intel_dir=intel_dir)

    page = AsyncMock()
    # _evaluate_api returns the API response
    wf._evaluate_api = AsyncMock(return_value={"data": {"balance": 1234.56}})

    result = asyncio.get_event_loop().run_until_complete(wf.sync_balance(page))
    assert result == 1234.56
    wf._evaluate_api.assert_called_once_with(page, "/api/wallet/balance")


def test_sync_balance_no_intel(intel_dir):
    from backend.src.mirror.workflows.generic import GenericWorkflow
    wf = GenericWorkflow("unknown", "unknown.com", intel_dir=intel_dir)
    page = AsyncMock()
    result = asyncio.get_event_loop().run_until_complete(wf.sync_balance(page))
    assert result == -1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py::test_sync_balance_api -v -x`
Expected: FAIL — `GenericWorkflow` not found or missing sync_balance

- [ ] **Step 3: Implement GenericWorkflow class**

Add to `backend/src/mirror/workflows/generic.py` (after load_intel/save_intel):

```python
def _extract_path(data: Any, path: str) -> Any:
    """Extract a value from nested dict using dot-separated path.

    Example: _extract_path({"data": {"balance": 123}}, "data.balance") → 123
    """
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


class GenericWorkflow(ProviderWorkflow):
    """Data-driven workflow that reads intel JSON + optional strategy overrides."""

    platform = "generic"

    def __init__(
        self,
        provider_id: str,
        domain: str,
        mode: WorkflowMode = WorkflowMode.GUIDED,
        intel_dir: Path | None = None,
    ):
        super().__init__(provider_id, domain, mode)
        self.intel = load_intel(provider_id, intel_dir)
        from .strategies import load_strategy
        self.strategy = load_strategy(provider_id)

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def check_login(self, page: "Page") -> bool:
        if self.strategy and self.strategy.check_login:
            return await self.strategy.check_login(page, self.intel)

        if not self.intel or not self.intel.get("login"):
            return True  # Assume logged in if no intel

        login = self.intel["login"]
        if login["method"] == "balance_api":
            # Try balance API — if it returns data, user is logged in
            bal = await self.sync_balance(page)
            return bal > 0

        if login["method"] == "dom":
            indicator = login.get("indicator", {})
            selector = indicator.get("selector")
            if selector:
                el = await page.query_selector(selector)
                return el is not None

        return True

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    async def sync_balance(self, page: "Page") -> float:
        if self.strategy and self.strategy.sync_balance:
            return await self.strategy.sync_balance(page, self.intel)

        if not self.intel or not self.intel.get("balance"):
            return -1.0

        bal = self.intel["balance"]

        # API method
        if bal["method"] == "api" and bal.get("api"):
            api = bal["api"]
            data = await self._evaluate_api(page, api["url"])
            if data is None or "__error" in (data or {}):
                return -1.0
            val = _extract_path(data, api["path"])
            try:
                return float(val) * api.get("multiplier", 1.0)
            except (TypeError, ValueError):
                logger.warning(f"[{self.provider_id}] Cannot parse balance: {val}")
                return -1.0

        # DOM method
        if bal["method"] == "dom" and bal.get("dom"):
            dom = bal["dom"]
            el = await page.query_selector(dom["selector"])
            if not el:
                return -1.0
            text = await el.text_content()
            if not text:
                return -1.0
            pattern = dom.get("regex", r"[\d.,]+")
            match = re.search(pattern, text)
            if not match:
                return -1.0
            try:
                cleaned = match.group().replace(",", "").replace(" ", "")
                return float(cleaned) * dom.get("multiplier", 1.0)
            except ValueError:
                return -1.0

        return -1.0

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        if self.strategy and self.strategy.sync_history:
            return await self.strategy.sync_history(page, self.intel)

        if not self.intel or not self.intel.get("history"):
            return []

        hist = self.intel["history"]

        # Navigate to history page if URL provided
        nav = self.intel.get("navigation", {})
        history_path = (nav or {}).get("history_path") or hist.get("url")
        if history_path:
            try:
                current = page.url or ""
                if history_path not in current:
                    full_url = history_path if history_path.startswith("http") else f"https://{self.domain}{history_path}"
                    await page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
                    import asyncio
                    await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"[{self.provider_id}] Failed to navigate to history: {e}")
                return []

        # API method
        if hist["method"] == "api" and hist.get("api"):
            return await self._sync_history_api(page, hist["api"])

        # DOM method
        if hist["method"] == "dom" and hist.get("dom"):
            return await self._sync_history_dom(page, hist["dom"])

        return []

    async def _sync_history_api(self, page: "Page", api_cfg: dict) -> list[HistoryEntry]:
        endpoint = api_cfg.get("endpoint", "")
        data = await self._evaluate_api(page, endpoint)
        if not data or "__error" in (data or {}):
            return []

        mapping = api_cfg.get("mapping", {})
        status_map = mapping.get("status_map", {})

        # Find the list of bets in the response
        bets_data = data
        if isinstance(data, dict):
            # Try common patterns: data.bets, data.items, data.results, or top-level list
            for key in ("bets", "items", "results", "data", "coupons"):
                if key in data:
                    bets_data = data[key]
                    break

        if not isinstance(bets_data, list):
            return []

        entries = []
        for bet in bets_data:
            try:
                raw_status = str(_extract_path(bet, mapping.get("status", "status")) or "")
                status = status_map.get(raw_status, raw_status)
                entries.append(HistoryEntry(
                    provider_bet_id=str(_extract_path(bet, mapping.get("bet_id", "id")) or ""),
                    event_name=str(_extract_path(bet, mapping.get("event_name", "event")) or ""),
                    market="",
                    outcome="",
                    odds=float(_extract_path(bet, mapping.get("odds", "odds")) or 0),
                    stake=float(_extract_path(bet, mapping.get("stake", "stake")) or 0),
                    status=status,
                    payout=float(_extract_path(bet, mapping.get("payout", "payout")) or 0) if _extract_path(bet, mapping.get("payout", "payout")) else None,
                ))
            except (TypeError, ValueError, KeyError) as e:
                logger.debug(f"[{self.provider_id}] Skip unparseable history entry: {e}")
        return entries

    async def _sync_history_dom(self, page: "Page", dom_cfg: dict) -> list[HistoryEntry]:
        container_sel = dom_cfg.get("container", "body")
        row_sel = dom_cfg.get("row_selector", "")
        fields = dom_cfg.get("fields", {})

        if not row_sel:
            return []

        rows = await page.query_selector_all(f"{container_sel} {row_sel}")
        entries = []
        for row in rows:
            try:
                entry = HistoryEntry(
                    provider_bet_id="",
                    event_name=await self._extract_dom_field(row, fields.get("event_name", {})),
                    market="",
                    outcome="",
                    odds=float(await self._extract_dom_field(row, fields.get("odds", {})) or 0),
                    stake=float(await self._extract_dom_field(row, fields.get("stake", {})) or 0),
                    status=await self._extract_dom_status(row, fields.get("status", {})),
                    payout=float(await self._extract_dom_field(row, fields.get("payout", {})) or 0) or None,
                )
                if entry.odds > 0 and entry.stake > 0:
                    entries.append(entry)
            except (TypeError, ValueError) as e:
                logger.debug(f"[{self.provider_id}] Skip unparseable DOM row: {e}")
        return entries

    async def _extract_dom_field(self, row, field_cfg: dict) -> str:
        """Extract a text field from a DOM row element."""
        if not field_cfg:
            return ""
        selector = field_cfg.get("selector", "")
        if not selector:
            return ""
        el = await row.query_selector(selector)
        if not el:
            return ""
        text = (await el.text_content() or "").strip()
        pattern = field_cfg.get("regex")
        if pattern:
            match = re.search(pattern, text)
            return match.group() if match else ""
        return text

    async def _extract_dom_status(self, row, field_cfg: dict) -> str:
        """Extract and map status from a DOM row element."""
        raw = await self._extract_dom_field(row, field_cfg)
        text_map = field_cfg.get("text_map", {})
        return text_map.get(raw, raw.lower())

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        if self.strategy and self.strategy.navigate_to_event:
            return await self.strategy.navigate_to_event(page, bet, self.intel)

        if not self.intel or not self.intel.get("navigation"):
            logger.info(f"[{self.provider_id}] No navigation intel — user navigates manually")
            return True

        nav = self.intel["navigation"]
        template = nav.get("event_url_template")
        if not template:
            return True

        # Try to fill template from bet attributes
        event_id = getattr(bet, "provider_event_id", "") or getattr(bet, "event_id", "")
        url = template.replace("{event_id}", str(event_id))
        if not url.startswith("http"):
            url = f"https://{self.domain}{url}"

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            logger.info(f"[{self.provider_id}] Navigated to {url}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Navigate failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Placement — always guided
    # ------------------------------------------------------------------

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        if self.strategy and self.strategy.place_bet:
            return await self.strategy.place_bet(page, bet, stake, self.intel)

        if not self.intel or not self.intel.get("betslip"):
            return PlacementResult(status="manual", bet_id=bet.bet_id, actual_stake=stake, reason="no_betslip_intel")

        bs = self.intel["betslip"]

        # Step 1: Try to fill stake input
        stake_sel = bs.get("stake_input", "")
        if stake_sel:
            try:
                input_el = await page.query_selector(stake_sel)
                if input_el:
                    await input_el.fill("")
                    await input_el.fill(f"{stake:.2f}")
                    logger.info(f"[{self.provider_id}] Stake filled: {stake:.2f}")
            except Exception as e:
                logger.warning(f"[{self.provider_id}] Cannot fill stake: {e}")

        # Step 2: Highlight confirm button (never click it)
        confirm_sel = bs.get("confirm_button", "")
        if confirm_sel:
            try:
                await page.evaluate(f"""
                    () => {{
                        const btn = document.querySelector('{confirm_sel}');
                        if (btn) {{
                            btn.style.outline = '3px solid #ff6600';
                            btn.style.outlineOffset = '2px';
                        }}
                    }}
                """)
            except Exception:
                pass

        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="generic_guided_user_confirms",
        )

    # ------------------------------------------------------------------
    # Live price (optional)
    # ------------------------------------------------------------------

    async def check_live_price(self, page: "Page", bet) -> float | None:
        if self.strategy and self.strategy.check_live_price:
            return await self.strategy.check_live_price(page, bet, self.intel)
        return None
```

- [ ] **Step 4: Run all tests**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py -v`
Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/workflows/generic.py tests/test_generic_workflow.py
git commit -m "feat(mirror): GenericWorkflow class with intel-driven dispatch"
```

---

### Task 4: Discovery engine — JSONL recording analysis

**Files:**
- Create: `backend/src/mirror/workflows/discovery.py`
- Test: `tests/test_generic_workflow.py` (append)

- [ ] **Step 1: Write failing test for analyze_recordings**

Append to `tests/test_generic_workflow.py`:

```python
def test_analyze_recordings_finds_api_endpoints(tmp_path):
    from backend.src.mirror.workflows.discovery import analyze_recordings

    # Create a fake JSONL recording
    rec_dir = tmp_path / "mirror_recordings" / "testprovider"
    rec_dir.mkdir(parents=True)
    entries = [
        {"ts": "2026-04-08T10:00:00Z", "method": "GET", "url": "https://test.com/api/wallet/balance", "status": 200, "response_body": '{"balance": 500}', "resource_type": "xhr"},
        {"ts": "2026-04-08T10:00:01Z", "method": "GET", "url": "https://test.com/api/bets/history", "status": 200, "response_body": '{"bets": []}', "resource_type": "xhr"},
        {"ts": "2026-04-08T10:00:02Z", "method": "POST", "url": "https://test.com/api/bets/place", "status": 200, "response_body": '{"id": 1}', "resource_type": "xhr", "request_body": '{"stake": 10}'},
        {"ts": "2026-04-08T10:00:03Z", "method": "GET", "url": "https://test.com/static/logo.png", "status": 200, "response_body": None, "resource_type": "image"},
        {"ts": "2026-04-08T10:00:04Z", "method": "GET", "url": "https://cdn.other.com/tracking", "status": 200, "response_body": None, "resource_type": "xhr"},
    ]
    with open(rec_dir / "2026-04-08_10-00-00.jsonl", "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")

    result = analyze_recordings("testprovider", tmp_path / "mirror_recordings")
    assert "balance" in result
    assert any("/wallet/balance" in ep for ep in result["balance"])
    assert "history" in result
    assert any("/bets/history" in ep for ep in result["history"])
    assert "placement" in result
    assert any("/bets/place" in ep for ep in result["placement"])


def test_analyze_recordings_no_recordings(tmp_path):
    from backend.src.mirror.workflows.discovery import analyze_recordings
    result = analyze_recordings("nonexistent", tmp_path / "mirror_recordings")
    assert result == {"balance": [], "history": [], "placement": [], "other_api": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py::test_analyze_recordings_finds_api_endpoints -v -x`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement discovery.py with analyze_recordings**

Create `backend/src/mirror/workflows/discovery.py`:

```python
"""Discovery engine — mines JSONL recordings + live DOM to populate intel JSON."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Keywords that identify API endpoint categories
_BALANCE_KEYWORDS = ("/wallet", "/balance", "/account/balance", "/wallets")
_HISTORY_KEYWORDS = ("/bets", "/history", "/coupons", "/bethistory", "/bet-history", "/mybets")
_PLACEMENT_KEYWORDS = ("/place", "/placewidget", "/placebet")

# Domains to ignore (CDNs, tracking, etc.)
_IGNORE_DOMAINS = (
    "google", "facebook", "hotjar", "clarity", "analytics",
    "doubleclick", "cloudflare", "fonts.googleapis",
)


def analyze_recordings(
    provider_id: str,
    recordings_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Mine JSONL recordings for API endpoint patterns.

    Returns: {
        "balance": ["GET /api/wallet/balance", ...],
        "history": ["GET /api/bets/history", ...],
        "placement": ["POST /api/bets/place", ...],
        "other_api": ["GET /api/settings", ...],
    }
    """
    if recordings_dir is None:
        from ...paths import get_data_dir
        recordings_dir = get_data_dir() / "mirror_recordings"

    provider_dir = recordings_dir / provider_id
    if not provider_dir.exists():
        return {"balance": [], "history": [], "placement": [], "other_api": []}

    seen: set[str] = set()
    categorized: dict[str, list[str]] = {"balance": [], "history": [], "placement": [], "other_api": []}

    for jsonl_file in sorted(provider_dir.glob("*.jsonl")):
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Skip DOM events
                    if entry.get("type") == "dom":
                        continue

                    url = entry.get("url", "")
                    method = entry.get("method", "GET")
                    resource_type = entry.get("resource_type", "")

                    # Only care about XHR/fetch
                    if resource_type not in ("xhr", "fetch", ""):
                        continue

                    # Skip ignored domains
                    url_lower = url.lower()
                    if any(d in url_lower for d in _IGNORE_DOMAINS):
                        continue

                    # Deduplicate by method + path (strip query params)
                    path = url.split("?")[0]
                    key = f"{method} {path}"
                    if key in seen:
                        continue
                    seen.add(key)

                    # Categorize
                    path_lower = path.lower()
                    entry_str = f"{method} {path}"

                    if any(kw in path_lower for kw in _BALANCE_KEYWORDS):
                        categorized["balance"].append(entry_str)
                    elif any(kw in path_lower for kw in _HISTORY_KEYWORDS):
                        categorized["history"].append(entry_str)
                    elif any(kw in path_lower for kw in _PLACEMENT_KEYWORDS):
                        categorized["placement"].append(entry_str)
                    elif resource_type in ("xhr", "fetch") and "/api/" in path_lower:
                        categorized["other_api"].append(entry_str)

        except OSError as e:
            logger.warning(f"[discovery] Error reading {jsonl_file}: {e}")

    return categorized


def _parse_response_body(entry: dict) -> dict | None:
    """Try to parse the response_body JSON from a JSONL entry."""
    body = entry.get("response_body")
    if not body or not isinstance(body, str):
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None


def _infer_balance_path(response: dict, depth: int = 0, prefix: str = "") -> str | None:
    """Walk a JSON response to find a field that looks like a balance value.

    Heuristic: numeric value under a key containing 'balance', 'amount', 'cash', 'total'.
    """
    if depth > 5:
        return None

    balance_keys = ("balance", "amount", "cash", "total", "real")

    if isinstance(response, dict):
        for key, val in response.items():
            current_path = f"{prefix}.{key}" if prefix else key
            key_lower = key.lower()

            if key_lower in balance_keys and isinstance(val, (int, float)):
                return current_path

            if isinstance(val, dict):
                result = _infer_balance_path(val, depth + 1, current_path)
                if result:
                    return result

    return None


def _infer_history_list_path(response: dict) -> tuple[str, list] | None:
    """Find the array of bet objects in a history API response."""
    bet_keys = ("bets", "items", "results", "coupons", "data", "history")

    if isinstance(response, list):
        return "", response

    if isinstance(response, dict):
        for key in bet_keys:
            if key in response and isinstance(response[key], list):
                return key, response[key]
        # One level deeper
        for key, val in response.items():
            if isinstance(val, dict):
                for bk in bet_keys:
                    if bk in val and isinstance(val[bk], list):
                        return f"{key}.{bk}", val[bk]
    return None


async def discover_balance_from_recordings(
    page: "Page",
    provider_id: str,
    recordings_dir: Path | None = None,
) -> dict | None:
    """Attempt to discover balance method from JSONL recordings.

    1. Find balance-like API endpoints
    2. Test them via page.evaluate(fetch)
    3. Infer the JSON path to the balance value
    """
    endpoints = analyze_recordings(provider_id, recordings_dir)
    balance_eps = endpoints.get("balance", [])

    for ep in balance_eps:
        parts = ep.split(" ", 1)
        method = parts[0] if len(parts) > 1 else "GET"
        url = parts[1] if len(parts) > 1 else parts[0]

        # Only test GET endpoints for balance
        if method != "GET":
            continue

        from .base import ProviderWorkflow
        data = await ProviderWorkflow._evaluate_api(
            ProviderWorkflow.__new__(ProviderWorkflow, provider_id=provider_id, domain="", mode=None),
            page, url
        )
        if data and "__error" not in (data or {}):
            # Try to infer the balance path
            path = _infer_balance_path(data)
            if path:
                return {
                    "method": "api",
                    "api": {"url": url, "path": path, "currency": "SEK"},
                    "dom": None,
                }

    return None


async def discover_balance_dom(page: "Page") -> dict | None:
    """Scan the current page DOM for balance-like elements in nav/header."""
    result = await page.evaluate("""
        () => {
            const moneyRegex = /\\d[\\d\\s,.]+/;
            const candidates = [];
            // Check nav, header, top bar areas
            const areas = document.querySelectorAll('nav, header, [class*="header"], [class*="nav"], [class*="balance"], [class*="wallet"], [class*="user"]');
            for (const area of areas) {
                const els = area.querySelectorAll('span, div, p, a');
                for (const el of els) {
                    const text = (el.textContent || '').trim();
                    if (moneyRegex.test(text) && text.length < 30) {
                        // Check if it looks like a balance (has digits, maybe currency symbol)
                        const hasNumber = /\\d/.test(text);
                        const hasCurrency = /[\\$€£kr\\sSEK]|\\bkr\\b/i.test(text);
                        if (hasNumber && (hasCurrency || text.match(/^[\\d\\s,.]+(\\s*kr)?$/))) {
                            // Build a CSS selector for this element
                            let selector = el.tagName.toLowerCase();
                            if (el.id) selector = '#' + el.id;
                            else if (el.className && typeof el.className === 'string') {
                                const cls = el.className.trim().split(/\\s+/)[0];
                                if (cls) selector = '.' + cls;
                            }
                            candidates.push({
                                text: text,
                                selector: selector,
                                tag: el.tagName.toLowerCase(),
                                className: (el.className || '').toString().substring(0, 100),
                            });
                        }
                    }
                }
            }
            return candidates.slice(0, 5);  // Top 5 candidates
        }
    """)

    if not result:
        return None

    # Pick the best candidate (shortest text that looks most like a pure number)
    best = None
    for candidate in result:
        if best is None or len(candidate["text"]) < len(best["text"]):
            best = candidate

    if best:
        return {
            "method": "dom",
            "api": None,
            "dom": {
                "selector": best["selector"],
                "regex": r"[\d.,]+",
                "multiplier": 1.0,
            },
        }
    return None


async def discover(
    page: "Page",
    provider_id: str,
    recordings_dir: Path | None = None,
    intel_dir: Path | None = None,
) -> dict:
    """Run full discovery for a provider. Returns intel dict, saves to JSON.

    Phases:
    1. Analyze JSONL recordings for API endpoints
    2. Discover balance (API first, DOM fallback)
    3. Discover history endpoints from recordings
    4. Save intel JSON
    """
    from .generic import save_intel

    logger.info(f"[discovery] Starting discovery for {provider_id}")

    # Phase 1: Analyze recordings
    endpoints = analyze_recordings(provider_id, recordings_dir)
    logger.info(f"[discovery] {provider_id} recordings: balance={len(endpoints['balance'])}, "
                f"history={len(endpoints['history'])}, placement={len(endpoints['placement'])}, "
                f"other={len(endpoints['other_api'])}")

    # Phase 2: Discover balance
    balance_intel = await discover_balance_from_recordings(page, provider_id, recordings_dir)
    if not balance_intel:
        balance_intel = await discover_balance_dom(page)
        if balance_intel:
            logger.info(f"[discovery] {provider_id} balance: DOM fallback → {balance_intel['dom']['selector']}")
    else:
        logger.info(f"[discovery] {provider_id} balance: API → {balance_intel['api']['url']}")

    # Phase 3: History — record what we found, actual testing needs manual verification
    history_intel = None
    if endpoints["history"]:
        # Take the first history endpoint, will need manual field mapping
        ep = endpoints["history"][0]
        parts = ep.split(" ", 1)
        method = parts[0]
        url = parts[1] if len(parts) > 1 else parts[0]
        history_intel = {
            "method": "api",
            "url": url,
            "api": {
                "endpoint": url,
                "settled_filter": {},
                "open_filter": {},
                "mapping": {
                    "bet_id": "id",
                    "odds": "odds",
                    "stake": "stake",
                    "status": "status",
                    "payout": "payout",
                    "event_name": "event",
                    "status_map": {},
                },
            },
            "dom": None,
        }
        logger.info(f"[discovery] {provider_id} history: API → {url} (mapping needs verification)")

    # Build intel
    now = datetime.now(timezone.utc).isoformat()
    intel = {
        "provider_id": provider_id,
        "platform": "unknown",
        "discovered_at": now,
        "updated_at": now,
        "capabilities": {
            "login": "discovered" if balance_intel else "none",
            "balance": "discovered" if balance_intel else "none",
            "history": "discovered" if history_intel else "none",
            "placement": "none",
        },
        "login": {
            "method": "balance_api" if (balance_intel and balance_intel.get("method") == "api") else "dom",
            "indicator": balance_intel.get("dom") if balance_intel else None,
        } if balance_intel else None,
        "balance": balance_intel,
        "history": history_intel,
        "betslip": None,
        "navigation": None,
        "api_endpoints": endpoints,
        "notes": f"Auto-discovered {now}. History field mapping needs manual verification.",
    }

    # Save
    save_intel(provider_id, intel, intel_dir)
    logger.info(f"[discovery] {provider_id} discovery complete: {intel['capabilities']}")
    return intel
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py -v -x -k "recordings"`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/workflows/discovery.py tests/test_generic_workflow.py
git commit -m "feat(mirror): discovery engine with JSONL recording analysis"
```

---

### Task 5: Wire GenericWorkflow into the factory

**Files:**
- Modify: `backend/src/mirror/workflows/__init__.py:19-40,60-96`

- [ ] **Step 1: Write failing test for factory routing**

Append to `tests/test_generic_workflow.py`:

```python
def test_get_workflow_returns_generic_for_unwired(monkeypatch):
    """Unwired platforms should get GenericWorkflow instead of ManualWorkflow."""
    from backend.src.mirror.workflows import get_workflow, _WORKFLOW_CACHE
    from backend.src.mirror.workflows.generic import GenericWorkflow

    # Clear cache
    _WORKFLOW_CACHE.clear()

    wf = get_workflow("coolbet")
    assert isinstance(wf, GenericWorkflow)
    assert wf.provider_id == "coolbet"

    # Clean up
    _WORKFLOW_CACHE.clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py::test_get_workflow_returns_generic_for_unwired -v -x`
Expected: FAIL — `coolbet` returns `ManualWorkflow`

- [ ] **Step 3: Update __init__.py to route unwired platforms to GenericWorkflow**

In `backend/src/mirror/workflows/__init__.py`, change the `_load_platform_map()` function and imports:

Replace the entire `_load_platform_map` function (lines 19-40):

```python
def _load_platform_map() -> dict[str, type[ProviderWorkflow]]:
    from .polymarket import PolymarketWorkflow
    from .pinnacle import PinnacleWorkflow
    from .altenar import AltenarWorkflow
    from .gecko import GeckoWorkflow
    from .kambi import KambiWorkflow
    from .generic import GenericWorkflow
    return {
        "polymarket": PolymarketWorkflow,
        "pinnacle": PinnacleWorkflow,
        "altenar": AltenarWorkflow,
        "gecko_v2": GeckoWorkflow,
        "kambi": KambiWorkflow,
        # All unwired platforms use GenericWorkflow (replaces ManualWorkflow)
        "spectate": GenericWorkflow,
        "tenbet": GenericWorkflow,
        "snabbare": GenericWorkflow,
        "custom": GenericWorkflow,
        "betconstruct": GenericWorkflow,
        "interwetten": GenericWorkflow,
        "coolbet": GenericWorkflow,
        "tipwin": GenericWorkflow,
    }
```

Also update the fallback in `get_workflow()` (around line 79-82). Replace:

```python
        from .manual import ManualWorkflow
        instance = ManualWorkflow(provider_id=provider_id, domain="")
```

With:

```python
        from .generic import GenericWorkflow
        instance = GenericWorkflow(provider_id=provider_id, domain="")
```

And the second fallback (around line 87-88). Replace:

```python
        from .manual import ManualWorkflow
        cls = ManualWorkflow
```

With:

```python
        from .generic import GenericWorkflow
        cls = GenericWorkflow
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py::test_get_workflow_returns_generic_for_unwired -v -x`
Expected: PASS

- [ ] **Step 5: Run all tests to ensure no regressions**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py -v`
Expected: All PASSED

- [ ] **Step 6: Commit**

```bash
git add backend/src/mirror/workflows/__init__.py tests/test_generic_workflow.py
git commit -m "feat(mirror): route unwired platforms to GenericWorkflow"
```

---

### Task 6: Discovery API endpoint

**Files:**
- Modify: `backend/src/app.py` (add `/api/mirror/discover/{provider_id}` route)

- [ ] **Step 1: Read current app.py mirror routes to find the right place to add**

Check `backend/src/app.py` for existing mirror route patterns (look for `@app` or router definitions that handle `/api/mirror/` paths).

- [ ] **Step 2: Add discovery endpoint**

Add to the mirror routes section in `backend/src/app.py`:

```python
@app.post("/api/mirror/discover/{provider_id}")
async def discover_provider(provider_id: str, force: bool = False):
    """Run DOM/API discovery for a provider and save intel JSON."""
    from .mirror.workflows.generic import load_intel
    from .mirror.workflows.discovery import discover

    # Check if intel already exists (skip unless force=True)
    existing = load_intel(provider_id)
    if existing and not force:
        return {"status": "exists", "provider_id": provider_id, "capabilities": existing["capabilities"]}

    # Need active mirror browser with provider tab open
    mirror = app.state.mirror_service
    if not mirror or not mirror.interceptor or mirror.interceptor.status != "listening":
        return {"status": "error", "message": "Mirror browser not running"}

    page = None
    context = mirror.interceptor.context
    if context:
        from .mirror.workflows import get_workflow
        wf = get_workflow(provider_id)
        page = await wf.find_tab(context)

    if not page:
        return {"status": "error", "message": f"No tab open for {provider_id}"}

    intel = await discover(page, provider_id)
    return {"status": "discovered", "provider_id": provider_id, "capabilities": intel["capabilities"]}
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/app.py
git commit -m "feat(mirror): add POST /api/mirror/discover/{provider_id} endpoint"
```

---

### Task 7: Auto-discovery trigger in GenericWorkflow

**Files:**
- Modify: `backend/src/mirror/workflows/generic.py`

- [ ] **Step 1: Add auto-discovery to GenericWorkflow init flow**

Add a method to `GenericWorkflow` that can be called when the provider is first detected and no intel exists:

```python
    async def auto_discover(self, page: "Page") -> bool:
        """Run discovery if no intel exists. Called on first provider detection."""
        if self.intel is not None:
            return True  # Already have intel

        from .discovery import discover
        try:
            self.intel = await discover(page, self.provider_id)
            logger.info(f"[{self.provider_id}] Auto-discovery complete: {self.intel.get('capabilities', {})}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Auto-discovery failed: {e}")
            return False
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/mirror/workflows/generic.py
git commit -m "feat(mirror): auto-discovery trigger in GenericWorkflow"
```

---

### Task 8: Integration — trigger discovery on provider detection

**Files:**
- Modify: `backend/src/mirror/service.py` (in `_handle_provider_detected`)

- [ ] **Step 1: Read current _handle_provider_detected**

Read `backend/src/mirror/service.py` around line 71 to understand the current detection flow.

- [ ] **Step 2: Add auto-discovery trigger**

In `_handle_provider_detected`, after the existing logic, add a check for GenericWorkflow auto-discovery:

```python
        # Auto-discover for generic (unwired) providers
        from .workflows import get_workflow
        from .workflows.generic import GenericWorkflow
        wf = get_workflow(provider_id)
        if isinstance(wf, GenericWorkflow) and wf.intel is None:
            context = self.interceptor.context
            if context:
                page = await wf.find_tab(context)
                if page:
                    import asyncio
                    asyncio.create_task(self._run_auto_discovery(wf, page, provider_id))
```

Add the helper method to MirrorService:

```python
    async def _run_auto_discovery(self, wf, page, provider_id: str):
        """Run auto-discovery in background, notify frontend when done."""
        import asyncio
        await asyncio.sleep(3)  # Wait for page to settle
        success = await wf.auto_discover(page)
        if success:
            self._notify("discovery_complete", {
                "provider": provider_id,
                "capabilities": wf.intel.get("capabilities", {}),
            })
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/mirror/service.py
git commit -m "feat(mirror): auto-trigger discovery for generic providers on detection"
```

---

### Task 9: Final integration test + cleanup

**Files:**
- Test: `tests/test_generic_workflow.py` (append)
- Delete: `backend/src/mirror/workflows/manual.py` (optional — keep for reference or delete)

- [ ] **Step 1: Write integration test for full workflow lifecycle**

Append to `tests/test_generic_workflow.py`:

```python
def test_full_workflow_lifecycle(intel_dir):
    """Test the complete lifecycle: load intel → check_login → sync_balance → place_bet."""
    from backend.src.mirror.workflows.generic import GenericWorkflow, save_intel

    intel = {
        "provider_id": "lifecycle_test",
        "platform": "custom",
        "discovered_at": "2026-04-08T14:30:00Z",
        "updated_at": "2026-04-08T14:30:00Z",
        "capabilities": {"login": "discovered", "balance": "discovered", "history": "none", "placement": "discovered"},
        "login": {"method": "balance_api", "indicator": None},
        "balance": {
            "method": "api",
            "api": {"url": "/api/balance", "path": "wallet.amount", "currency": "SEK"},
            "dom": None,
        },
        "history": None,
        "betslip": {
            "odds_buttons": ".odds-btn",
            "stake_input": "#stake",
            "confirm_button": ".confirm",
            "confirmation_selector": ".success",
        },
        "navigation": {"history_path": "/bets", "event_url_template": "/event/{event_id}"},
        "api_endpoints": {},
        "notes": "",
    }
    save_intel("lifecycle_test", intel, intel_dir)

    wf = GenericWorkflow("lifecycle_test", "test.com", intel_dir=intel_dir)

    page = AsyncMock()
    wf._evaluate_api = AsyncMock(return_value={"wallet": {"amount": 999.50}})

    # check_login (uses balance_api method → calls sync_balance internally)
    result = asyncio.get_event_loop().run_until_complete(wf.check_login(page))
    assert result is True

    # sync_balance
    balance = asyncio.get_event_loop().run_until_complete(wf.sync_balance(page))
    assert balance == 999.50

    # navigate_to_event
    page.goto = AsyncMock()
    page.url = "https://test.com/home"
    bet = MagicMock()
    bet.provider_event_id = "12345"
    nav_ok = asyncio.get_event_loop().run_until_complete(wf.navigate_to_event(page, bet))
    assert nav_ok is True
    page.goto.assert_called_once()
    assert "12345" in page.goto.call_args[0][0]

    # place_bet (guided — fills stake, returns manual)
    bet.bet_id = 1
    page.query_selector = AsyncMock(return_value=AsyncMock())
    page.evaluate = AsyncMock()
    result = asyncio.get_event_loop().run_until_complete(wf.place_bet(page, bet, 50.0))
    assert result.status == "manual"
    assert result.reason == "generic_guided_user_confirms"
```

- [ ] **Step 2: Run all tests**

Run: `cd backend && python -m pytest ../tests/test_generic_workflow.py -v`
Expected: All PASSED

- [ ] **Step 3: Commit**

```bash
git add tests/test_generic_workflow.py
git commit -m "test(mirror): integration test for GenericWorkflow lifecycle"
```

- [ ] **Step 4: Final commit with all files**

Verify everything is committed:

```bash
git status
git log --oneline -10
```

---

## Summary

| Task | What it builds | Files |
|------|---------------|-------|
| 1 | Intel JSON loader/saver | `generic.py`, `.gitkeep`, tests |
| 2 | Strategy dataclass + loader | `strategies/__init__.py`, tests |
| 3 | GenericWorkflow class (all ABC methods) | `generic.py`, tests |
| 4 | Discovery engine (JSONL mining) | `discovery.py`, tests |
| 5 | Factory wiring (replace ManualWorkflow) | `__init__.py`, tests |
| 6 | Discovery API endpoint | `app.py` |
| 7 | Auto-discovery trigger | `generic.py` |
| 8 | Service integration | `service.py` |
| 9 | Integration test + cleanup | tests |
