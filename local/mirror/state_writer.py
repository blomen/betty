"""Local-mirror → server-DB state writer (Phase 2 of platform rebuild, 2026-05-08).

Fire-and-forget POSTs to /api/mirror/* so the server's `mirror_provider_state`,
`mirror_runner_state`, and `mirror_event_log` tables stay in sync with what
the local mirror is actually doing. The frontend reads from those tables
instead of trying to reconstruct state from in-memory + ephemeral SSE +
React state.

Writes are non-blocking — they MUST NOT slow down or break the runner.
HTTP failures are swallowed (logged at debug). The server cron + auto-
reconnect logic in arnold/launch.py handles tunnel wedges; we just keep
trying.

Three entry points:
- write_provider_state(provider_id, **fields) — login/balance/tab/etc.
- write_runner_state(provider_id, **fields)   — runner state transitions
- write_event(event_type, data)                — every SSE event for replay

Each is called from the existing publish/state-set sites in browser.py,
arb_runner.py, provider_runner.py without changing their behavior.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _tunnel_client():
    """Lazy import to avoid pulling http_client at module load (test isolation)."""
    from local.http_client import tunnel_client

    return tunnel_client()


def _fire_post(path: str, payload: dict[str, Any]) -> None:
    """Best-effort POST to the local /api proxy. Swallows all errors."""

    async def _do():
        try:
            client = _tunnel_client()
            await client.post(path, json=payload, timeout=5.0)
        except Exception as e:
            # Debug-only — these fire on every state change and we don't want
            # to spam logs when the tunnel is wedged.
            logger.debug(f"[state_writer] POST {path} failed: {e!r}")

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do())
    except RuntimeError:
        # No running loop — caller is sync context. This shouldn't happen
        # inside the runner (always async), but the safety net is cheap.
        try:
            asyncio.run(_do())
        except Exception:
            pass


def write_provider_state(
    provider_id: str,
    *,
    logged_in: bool | None = None,
    balance: float | None = None,
    balance_currency: str | None = None,
    tab_url: str | None = None,
    tab_open: bool | None = None,
) -> None:
    """Upsert MirrorProviderState. Only fields you pass get updated."""
    payload: dict[str, Any] = {"provider_id": provider_id}
    if logged_in is not None:
        payload["logged_in"] = logged_in
    if balance is not None:
        payload["balance"] = balance
    if balance_currency is not None:
        payload["balance_currency"] = balance_currency
    if tab_url is not None:
        payload["tab_url"] = tab_url
    if tab_open is not None:
        payload["tab_open"] = tab_open
    _fire_post("/api/mirror/provider-state", payload)


def write_runner_state(
    provider_id: str,
    *,
    state: str | None = None,
    mode: str | None = None,
    current_arb_group_id: str | None = None,
    current_opp_id: int | None = None,
    last_idle_reason: str | None = None,
) -> None:
    """Upsert MirrorRunnerState. Only fields you pass get updated."""
    payload: dict[str, Any] = {"provider_id": provider_id}
    if state is not None:
        payload["state"] = state
    if mode is not None:
        payload["mode"] = mode
    if current_arb_group_id is not None:
        payload["current_arb_group_id"] = current_arb_group_id
    if current_opp_id is not None:
        payload["current_opp_id"] = current_opp_id
    if last_idle_reason is not None:
        payload["last_idle_reason"] = last_idle_reason
    _fire_post("/api/mirror/runner-state", payload)


def write_event(event_type: str, data: dict[str, Any] | None = None) -> None:
    """Append an event to the mirror_event_log table for replay + post-mortem."""
    payload: dict[str, Any] = {"event_type": event_type}
    if data:
        payload["data"] = data
        provider_id = data.get("provider_id")
        if provider_id:
            payload["provider_id"] = provider_id
    _fire_post("/api/mirror/event", payload)
