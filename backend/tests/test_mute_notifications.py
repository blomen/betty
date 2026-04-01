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

    with patch("asyncio.sleep", new=AsyncMock()):
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

    with patch("asyncio.sleep", new=AsyncMock()):
        asyncio.get_event_loop().run_until_complete(
            mirror._replay_notification_mute("campobet")
        )
    assert mirror._recipes[0].status == "stale"


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

    with patch("asyncio.sleep", new=AsyncMock()):
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
