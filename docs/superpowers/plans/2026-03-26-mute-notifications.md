# Mute Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically mute sportsbook email/SMS/push notifications by capturing the provider's "disable notifications" API call once, then replaying it on every future visit.

**Architecture:** Capture notification settings API calls via URL keyword matching in the interceptor, store as JSON recipes keyed by provider, auto-replay using the browser context when provider navigation is detected.

**Tech Stack:** Python, FastAPI, Playwright browser context API, JSON file storage

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/src/mirror/recipes.py` | Recipe dataclass, load/save JSON, recipe file path resolution |
| `backend/src/mirror/interceptor.py` | Add notification keyword detection in `_on_response`, new callback |
| `backend/src/mirror/service.py` | Handle captured recipes, replay on provider detection, muted set |
| `backend/src/api/routes/mirror.py` | GET/DELETE endpoints for recipe management |
| `backend/tests/test_mute_notifications.py` | All tests for capture, storage, replay logic |

---

### Task 1: Recipe Storage Module

**Files:**
- Create: `backend/src/mirror/recipes.py`
- Test: `backend/tests/test_mute_notifications.py`

- [ ] **Step 1: Write failing tests for recipe storage**

```python
# backend/tests/test_mute_notifications.py
import json
import pytest
from pathlib import Path
from src.mirror.recipes import NotificationRecipe, load_recipes, save_recipes, RECIPES_FILENAME


@pytest.fixture
def recipes_dir(tmp_path):
    return tmp_path


def _make_recipe(provider_id="campobet", method="PUT", url="https://campobet.se/api/v1/account/preferences"):
    return NotificationRecipe(
        provider_id=provider_id,
        captured_at="2026-03-26T14:30:00Z",
        method=method,
        url=url,
        content_type="application/json",
        body='{"email": false, "sms": false, "push": false}',
        status="active",
    )


def test_save_and_load_recipes(recipes_dir):
    recipes = [_make_recipe("campobet"), _make_recipe("betinia")]
    save_recipes(recipes, recipes_dir)

    loaded = load_recipes(recipes_dir)
    assert len(loaded) == 2
    assert loaded[0].provider_id == "campobet"
    assert loaded[1].provider_id == "betinia"


def test_load_recipes_missing_file(recipes_dir):
    loaded = load_recipes(recipes_dir)
    assert loaded == []


def test_save_overwrites_existing(recipes_dir):
    save_recipes([_make_recipe("campobet")], recipes_dir)
    save_recipes([_make_recipe("betinia")], recipes_dir)
    loaded = load_recipes(recipes_dir)
    assert len(loaded) == 1
    assert loaded[0].provider_id == "betinia"


def test_recipe_to_dict_roundtrip():
    recipe = _make_recipe()
    d = recipe.to_dict()
    restored = NotificationRecipe.from_dict(d)
    assert restored.provider_id == recipe.provider_id
    assert restored.url == recipe.url
    assert restored.body == recipe.body
    assert restored.status == recipe.status
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_mute_notifications.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.mirror.recipes'`

- [ ] **Step 3: Implement recipes module**

```python
# backend/src/mirror/recipes.py
"""Notification mute recipes — capture + replay templates for disabling notifications."""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

from ..paths import get_app_data_dir

logger = logging.getLogger(__name__)

RECIPES_FILENAME = "notification_recipes.json"


@dataclass
class NotificationRecipe:
    provider_id: str
    captured_at: str  # ISO 8601
    method: str  # PUT, POST, PATCH
    url: str  # Absolute URL
    content_type: str  # e.g. "application/json"
    body: str  # Request body as string
    status: str = "active"  # "active" or "stale"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NotificationRecipe":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _recipes_path(base_dir: Path | None = None) -> Path:
    """Resolve path to recipes JSON file."""
    base = base_dir or (get_app_data_dir() / "data")
    base.mkdir(parents=True, exist_ok=True)
    return base / RECIPES_FILENAME


def load_recipes(base_dir: Path | None = None) -> list[NotificationRecipe]:
    """Load all recipes from disk. Returns empty list if file missing."""
    path = _recipes_path(base_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [NotificationRecipe.from_dict(d) for d in data]
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[recipes] Failed to load {path}: {e}")
        return []


def save_recipes(recipes: list[NotificationRecipe], base_dir: Path | None = None) -> None:
    """Save all recipes to disk (full overwrite)."""
    path = _recipes_path(base_dir)
    path.write_text(
        json.dumps([r.to_dict() for r in recipes], indent=2),
        encoding="utf-8",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_mute_notifications.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/recipes.py backend/tests/test_mute_notifications.py
git commit -m "feat(mirror): add notification recipe storage module"
```

---

### Task 2: Interceptor — Detect Notification Settings Calls

**Files:**
- Modify: `backend/src/mirror/interceptor.py`
- Test: `backend/tests/test_mute_notifications.py`

- [ ] **Step 1: Write failing test for notification detection**

Append to `backend/tests/test_mute_notifications.py`:

```python
from src.mirror.interceptor import BetInterceptor


def test_notification_url_detection():
    interceptor = BetInterceptor()
    # Should match
    assert interceptor._is_notification_settings("https://campobet.se/api/v1/preferences", "PUT")
    assert interceptor._is_notification_settings("https://site.com/notifications/update", "POST")
    assert interceptor._is_notification_settings("https://site.com/consent/marketing", "PATCH")
    assert interceptor._is_notification_settings("https://site.com/settings/communication", "PUT")
    assert interceptor._is_notification_settings("https://site.com/subscriptions/email", "POST")
    # Should NOT match
    assert not interceptor._is_notification_settings("https://site.com/api/bets", "POST")
    assert not interceptor._is_notification_settings("https://site.com/preferences", "GET")  # GET = reading, not writing
    assert not interceptor._is_notification_settings("https://site.com/notifications", "GET")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_mute_notifications.py::test_notification_url_detection -v`
Expected: FAIL — `AttributeError: 'BetInterceptor' object has no attribute '_is_notification_settings'`

- [ ] **Step 3: Add notification detection to interceptor**

In `backend/src/mirror/interceptor.py`, add the keyword tuple and callback after the existing `_FINANCIAL_KEYWORDS`:

```python
# After _FINANCIAL_KEYWORDS line (~line 48):
_NOTIFICATION_KEYWORDS = (
    "preferences", "notifications", "communication", "consent",
    "marketing", "subscriptions", "gdpr", "contact-settings",
)
_NOTIFICATION_METHODS = {"PUT", "POST", "PATCH"}
```

Add to `__init__` — new callback parameter after `on_provider_detected`:

```python
def __init__(
    self,
    on_bet_response: Callable[..., Awaitable[None]] | None = None,
    on_event_data: Callable[[str, str], Awaitable[None]] | None = None,
    on_bet_history: Callable[[str, str], Awaitable[None]] | None = None,
    on_financial_data: Callable[[str, str], Awaitable[None]] | None = None,
    on_provider_detected: Callable[[str], Awaitable[None]] | None = None,
    on_notification_settings: Callable[..., Awaitable[None]] | None = None,
):
    # ... existing assignments ...
    self.on_notification_settings = on_notification_settings
```

Add the detection method:

```python
def _is_notification_settings(self, url: str, method: str) -> bool:
    """Check if this request is a notification/preference settings update."""
    if method not in self._NOTIFICATION_METHODS:
        return False
    url_lower = url.lower()
    return any(kw in url_lower for kw in self._NOTIFICATION_KEYWORDS)
```

Add to `_on_response` — after the financial data block (~line 193), before the bet placement check:

```python
# Intercept notification settings updates
if self.on_notification_settings and self._is_notification_settings(url, method):
    if response.status < 400:
        try:
            body_text = await response.text()
            request_body = response.request.post_data
            content_type = response.request.headers.get("content-type", "")
            await self.on_notification_settings(url, method, request_body, body_text, content_type)
        except Exception as e:
            logger.debug(f"[mirror] Could not read notification settings response: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_mute_notifications.py::test_notification_url_detection -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/interceptor.py backend/tests/test_mute_notifications.py
git commit -m "feat(mirror): detect notification settings API calls in interceptor"
```

---

### Task 3: MirrorService — Capture and Replay Recipes

**Files:**
- Modify: `backend/src/mirror/service.py`
- Test: `backend/tests/test_mute_notifications.py`

- [ ] **Step 1: Write failing tests for capture and replay logic**

Append to `backend/tests/test_mute_notifications.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.mirror.service import MirrorService


@pytest.fixture
def mirror(tmp_path):
    """MirrorService with recipes dir pointed at tmp."""
    svc = MirrorService(broadcaster=None)
    svc._recipes_dir = tmp_path
    svc._recipes = []
    return svc


def test_capture_notification_recipe(mirror):
    """Capturing a notification settings call stores a recipe."""
    asyncio.get_event_loop().run_until_complete(
        mirror._handle_notification_settings(
            url="https://campobet.se/api/v1/preferences",
            method="PUT",
            request_body='{"email": false, "sms": false}',
            response_body='{"ok": true}',
            content_type="application/json",
        )
    )
    assert len(mirror._recipes) == 1
    assert mirror._recipes[0].provider_id == "campobet"
    assert mirror._recipes[0].status == "active"
    # Verify persisted to disk
    loaded = load_recipes(mirror._recipes_dir)
    assert len(loaded) == 1


def test_capture_replaces_existing_recipe(mirror):
    """A second capture for the same provider replaces the old recipe."""
    for url in [
        "https://campobet.se/api/v1/preferences",
        "https://campobet.se/api/v2/notifications",
    ]:
        asyncio.get_event_loop().run_until_complete(
            mirror._handle_notification_settings(
                url=url, method="PUT",
                request_body='{"email": false}', response_body='{"ok": true}',
                content_type="application/json",
            )
        )
    assert len(mirror._recipes) == 1
    assert "v2" in mirror._recipes[0].url


def test_replay_calls_context_request(mirror):
    """Replay uses context.request to fire the stored recipe."""
    from src.mirror.recipes import NotificationRecipe
    mirror._recipes = [NotificationRecipe(
        provider_id="campobet",
        captured_at="2026-03-26T14:30:00Z",
        method="PUT",
        url="https://campobet.se/api/v1/preferences",
        content_type="application/json",
        body='{"email": false}',
        status="active",
    )]
    # Mock the Playwright context.request
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_request = AsyncMock()
    mock_request.fetch = AsyncMock(return_value=mock_response)
    mirror.interceptor.context = MagicMock()
    mirror.interceptor.context.request = mock_request

    asyncio.get_event_loop().run_until_complete(
        mirror._replay_notification_mute("campobet")
    )
    mock_request.fetch.assert_called_once()
    assert "campobet" in mirror._muted_providers


def test_replay_skips_already_muted(mirror):
    """Don't replay if already muted this session."""
    from src.mirror.recipes import NotificationRecipe
    mirror._recipes = [NotificationRecipe(
        provider_id="campobet",
        captured_at="2026-03-26T14:30:00Z",
        method="PUT",
        url="https://campobet.se/api/v1/preferences",
        content_type="application/json",
        body='{"email": false}',
        status="active",
    )]
    mirror._muted_providers.add("campobet")
    mock_request = AsyncMock()
    mirror.interceptor.context = MagicMock()
    mirror.interceptor.context.request = mock_request

    asyncio.get_event_loop().run_until_complete(
        mirror._replay_notification_mute("campobet")
    )
    mock_request.fetch.assert_not_called()


def test_replay_marks_stale_on_failure(mirror):
    """A failed replay marks the recipe as stale."""
    from src.mirror.recipes import NotificationRecipe
    mirror._recipes = [NotificationRecipe(
        provider_id="campobet",
        captured_at="2026-03-26T14:30:00Z",
        method="PUT",
        url="https://campobet.se/api/v1/preferences",
        content_type="application/json",
        body='{"email": false}',
        status="active",
    )]
    mock_response = AsyncMock()
    mock_response.status = 401
    mock_request = AsyncMock()
    mock_request.fetch = AsyncMock(return_value=mock_response)
    mirror.interceptor.context = MagicMock()
    mirror.interceptor.context.request = mock_request

    asyncio.get_event_loop().run_until_complete(
        mirror._replay_notification_mute("campobet")
    )
    assert mirror._recipes[0].status == "stale"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_mute_notifications.py -v -k "capture or replay"`
Expected: FAIL — `AttributeError` on missing methods

- [ ] **Step 3: Implement capture + replay in MirrorService**

In `backend/src/mirror/service.py`, add imports at the top:

```python
from .recipes import NotificationRecipe, load_recipes, save_recipes
```

In `__init__`, add after `self._pending_settlements`:

```python
# Notification mute recipes
self._recipes_dir: Path | None = None  # Set in tests; defaults to data dir
self._recipes: list[NotificationRecipe] = []
self._muted_providers: set[str] = set()
self._load_notification_recipes()
```

Add the `from pathlib import Path` import at top if not present.

Wire the new callback in the `BetInterceptor` constructor call:

```python
self.interceptor = BetInterceptor(
    on_bet_response=self._handle_bet_response,
    on_event_data=self._handle_event_data,
    on_bet_history=self._handle_bet_history,
    on_financial_data=self._handle_financial_data,
    on_provider_detected=self._handle_provider_detected,
    on_notification_settings=self._handle_notification_settings,
)
```

Update `_handle_provider_detected` to trigger replay after the existing sync logic:

```python
async def _handle_provider_detected(self, provider_id: str):
    """Fires when user navigates to a known provider site."""
    info = await asyncio.to_thread(self._get_provider_sync_info, provider_id)
    logger.info(
        f"[mirror] Sync available for {provider_id}: "
        f"balance={info['balance']}, pending={info['pending_bets']}"
    )
    self._notify("sync_available", {
        "provider": provider_id,
        "balance": info["balance"],
        "pending_bets": info["pending_bets"],
        "pending_stake": info["pending_stake"],
    })
    # Auto-mute notifications if we have a recipe
    await self._replay_notification_mute(provider_id)
```

Add the new methods:

```python
def _load_notification_recipes(self):
    """Load recipes from disk on init."""
    self._recipes = load_recipes(self._recipes_dir)
    if self._recipes:
        active = [r for r in self._recipes if r.status == "active"]
        logger.info(f"[mirror] Loaded {len(active)} active notification recipes")

def _save_notification_recipes(self):
    """Persist recipes to disk."""
    save_recipes(self._recipes, self._recipes_dir)

async def _handle_notification_settings(
    self, url: str, method: str, request_body: str | None,
    response_body: str, content_type: str,
):
    """Capture a notification settings API call as a recipe."""
    provider_id = self._detect_provider(url)
    if provider_id == "unknown":
        logger.debug(f"[mirror] Notification settings call from unknown provider: {url}")
        return

    recipe = NotificationRecipe(
        provider_id=provider_id,
        captured_at=datetime.now(timezone.utc).isoformat(),
        method=method,
        url=url,
        content_type=content_type or "application/json",
        body=request_body or "",
        status="active",
    )

    # Replace existing recipe for this provider
    self._recipes = [r for r in self._recipes if r.provider_id != provider_id]
    self._recipes.append(recipe)
    self._save_notification_recipes()

    logger.info(f"[mirror] Captured notification mute recipe for {provider_id}: {method} {url}")
    self._notify("notification_recipe_captured", {
        "provider": provider_id,
        "method": method,
        "url": url,
    })

async def _replay_notification_mute(self, provider_id: str):
    """Replay a stored notification mute recipe for a provider."""
    if provider_id in self._muted_providers:
        return

    recipe = next((r for r in self._recipes if r.provider_id == provider_id and r.status == "active"), None)
    if not recipe:
        return

    context = self.interceptor.context
    if not context:
        return

    try:
        # Small delay for auth cookies to settle after navigation
        await asyncio.sleep(2)

        resp = await context.request.fetch(
            recipe.url,
            method=recipe.method,
            headers={"content-type": recipe.content_type},
            data=recipe.body if recipe.body else None,
            timeout=10000,
        )

        if resp.status < 400:
            self._muted_providers.add(provider_id)
            logger.info(f"[mirror] Notifications muted for {provider_id} (HTTP {resp.status})")
            self._notify("notifications_muted", {"provider": provider_id})
        else:
            recipe.status = "stale"
            self._save_notification_recipes()
            logger.warning(f"[mirror] Mute replay failed for {provider_id} (HTTP {resp.status}) — recipe marked stale")
            self._notify("notifications_mute_failed", {"provider": provider_id, "status": resp.status})

    except Exception as e:
        logger.error(f"[mirror] Mute replay error for {provider_id}: {e}")

def get_notification_recipes(self) -> list[dict]:
    """Return all recipes as dicts for API response."""
    return [r.to_dict() for r in self._recipes]

def delete_notification_recipe(self, provider_id: str) -> bool:
    """Delete a recipe by provider ID. Returns True if found and deleted."""
    before = len(self._recipes)
    self._recipes = [r for r in self._recipes if r.provider_id != provider_id]
    if len(self._recipes) < before:
        self._save_notification_recipes()
        self._muted_providers.discard(provider_id)
        return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_mute_notifications.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/service.py backend/tests/test_mute_notifications.py
git commit -m "feat(mirror): capture and auto-replay notification mute recipes"
```

---

### Task 4: API Endpoints for Recipe Management

**Files:**
- Modify: `backend/src/api/routes/mirror.py`
- Test: `backend/tests/test_mute_notifications.py`

- [ ] **Step 1: Write failing tests for API endpoints**

Append to `backend/tests/test_mute_notifications.py`:

```python
from fastapi.testclient import TestClient
from src.api.routes.mirror import router, _mirrors
from fastapi import FastAPI


@pytest.fixture
def api_client(mirror):
    """FastAPI test client with a running mirror."""
    app = FastAPI()
    app.include_router(router)
    # Inject our test mirror into the module state
    _mirrors.clear()
    _mirrors["test"] = mirror
    mirror.interceptor.status = "listening"
    return TestClient(app)


def test_get_recipes_empty(api_client):
    resp = api_client.get("/api/mirror/notification-recipes")
    assert resp.status_code == 200
    assert resp.json() == {"recipes": []}


def test_get_recipes_with_data(api_client, mirror):
    from src.mirror.recipes import NotificationRecipe
    mirror._recipes = [NotificationRecipe(
        provider_id="campobet",
        captured_at="2026-03-26T14:30:00Z",
        method="PUT",
        url="https://campobet.se/api/v1/preferences",
        content_type="application/json",
        body='{"email": false}',
        status="active",
    )]
    resp = api_client.get("/api/mirror/notification-recipes")
    assert resp.status_code == 200
    data = resp.json()["recipes"]
    assert len(data) == 1
    assert data[0]["provider_id"] == "campobet"


def test_delete_recipe(api_client, mirror):
    from src.mirror.recipes import NotificationRecipe
    mirror._recipes = [NotificationRecipe(
        provider_id="campobet",
        captured_at="2026-03-26T14:30:00Z",
        method="PUT",
        url="https://campobet.se/api/v1/preferences",
        content_type="application/json",
        body='{"email": false}',
        status="active",
    )]
    resp = api_client.delete("/api/mirror/notification-recipes/campobet")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert len(mirror._recipes) == 0


def test_delete_recipe_not_found(api_client):
    resp = api_client.delete("/api/mirror/notification-recipes/nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_mute_notifications.py -v -k "api or recipe"`
Expected: FAIL — 404 on the new routes

- [ ] **Step 3: Add API endpoints to mirror router**

In `backend/src/api/routes/mirror.py`, add after the `reject_settlements` endpoint:

```python
@router.get("/notification-recipes")
async def get_notification_recipes():
    """List all stored notification mute recipes."""
    mirror = _get_active_mirror()
    if not mirror:
        return {"recipes": []}
    return {"recipes": mirror.get_notification_recipes()}


@router.delete("/notification-recipes/{provider_id}")
async def delete_notification_recipe(provider_id: str):
    """Delete a notification mute recipe for a provider."""
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(404, "No mirror running")
    deleted = mirror.delete_notification_recipe(provider_id)
    if not deleted:
        raise HTTPException(404, f"No recipe found for {provider_id}")
    return {"deleted": True, "provider_id": provider_id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_mute_notifications.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/mirror.py backend/tests/test_mute_notifications.py
git commit -m "feat(mirror): add GET/DELETE endpoints for notification recipes"
```

---

### Task 5: Integration Test — Full Capture + Replay Flow

**Files:**
- Test: `backend/tests/test_mute_notifications.py`

- [ ] **Step 1: Write integration test**

Append to `backend/tests/test_mute_notifications.py`:

```python
def test_full_capture_then_replay_flow(mirror):
    """End-to-end: capture a recipe, then replay it on provider detection."""
    # Step 1: Simulate capturing a notification settings call
    asyncio.get_event_loop().run_until_complete(
        mirror._handle_notification_settings(
            url="https://campobet.se/api/v1/preferences",
            method="PUT",
            request_body='{"email": false, "sms": false, "push": false}',
            response_body='{"ok": true}',
            content_type="application/json",
        )
    )
    assert len(mirror._recipes) == 1

    # Step 2: Simulate provider detection triggering replay
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_request = AsyncMock()
    mock_request.fetch = AsyncMock(return_value=mock_response)
    mirror.interceptor.context = MagicMock()
    mirror.interceptor.context.request = mock_request

    asyncio.get_event_loop().run_until_complete(
        mirror._replay_notification_mute("campobet")
    )

    # Verify replay happened with correct params
    call_args = mock_request.fetch.call_args
    assert call_args[0][0] == "https://campobet.se/api/v1/preferences"
    assert call_args[1]["method"] == "PUT"
    assert "campobet" in mirror._muted_providers

    # Step 3: Second visit should not replay
    mock_request.fetch.reset_mock()
    asyncio.get_event_loop().run_until_complete(
        mirror._replay_notification_mute("campobet")
    )
    mock_request.fetch.assert_not_called()
```

- [ ] **Step 2: Run full test suite**

Run: `cd backend && python -m pytest tests/test_mute_notifications.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_mute_notifications.py
git commit -m "test(mirror): add integration test for notification mute flow"
```
