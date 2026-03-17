"""Extraction state management."""

# Extraction state (backward compat for manual trigger endpoints)
extraction_state = {
    "running": False,
    "last_run": None,
    "start_time": None,
    "total_events": 0,
    "total_odds": 0,
    "providers": {},
    "current_provider": None,
    "completed_providers": 0,
    "total_providers": 0,
    "elapsed_seconds": 0,
}


def update_extraction_state(**kwargs):
    """Update extraction state."""
    extraction_state.update(kwargs)


def get_extraction_state():
    """Read extraction state."""
    return extraction_state.copy()


# Per-provider extraction state (no lock needed — single-threaded asyncio)
provider_states: dict[str, dict] = {}


def update_provider_state(provider_id: str, updates: dict):
    """Update state for a single provider."""
    if provider_id not in provider_states:
        provider_states[provider_id] = {
            "running": False,
            "last_completed": None,
            "last_duration": None,
            "last_error": None,
            "category": None,
        }
    provider_states[provider_id].update(updates)


def get_provider_states() -> dict:
    """Return copy of all provider states."""
    return dict(provider_states)
