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
