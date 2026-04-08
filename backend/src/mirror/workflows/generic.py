"""GenericWorkflow — data-driven workflow for any provider using intel JSON + strategy overrides."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

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
