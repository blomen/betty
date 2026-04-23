"""Notification mute recipes — capture + replay templates for disabling notifications."""

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from ..paths import get_data_dir

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
    base = base_dir or get_data_dir()
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
