"""Periodic auto-poller for API-based recorders.

Calls the local /mirror/sync-positions/{provider_id} every POLL_INTERVAL_SEC
for each provider in PROVIDERS. Each tick does the full insert + settle pass.

This replaces the need for the user to manually click "Sync" — pending bets
and settlements stay current without user action, and without a Playwright
browser tab being open. All transports are pure HTTP (Kalshi authenticated
REST; Pinnacle/Cloudbet via page.evaluate(fetch)). Polymarket recording moved
server-side — see backend/src/recorders/server_poller.py.

Started from arnold/server.py:startup as an asyncio task. Crashes are
logged but the loop keeps going.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 300.0  # 5 min — fast enough that settled bets clear within ~10 min of market resolution
# Kalshi: pure HTTP, works whether or not the browser is running.
# Pinnacle + Cloudbet: also HTTP (page.evaluate(fetch)) but cookies live in
# the Playwright context — they're no-ops when no provider tab is open.
# Polymarket is NOT here — it moved to a server-side 24/7 recorder
# (backend/src/recorders/server_poller.py) so it records whether or not this
# local client is running.
PROVIDERS = ("kalshi", "pinnacle", "cloudbet")


async def _tick_one(client: httpx.AsyncClient, local_url: str, provider_id: str) -> None:
    """Call /mirror/sync-positions/{provider} once. Logs the summary."""
    try:
        r = await client.post(f"{local_url}/mirror/sync-positions/{provider_id}", timeout=60.0)
        if r.status_code != 200:
            logger.warning(f"[auto_poller] {provider_id} → {r.status_code}: {(r.text or '')[:200]}")
            return
        body = r.json() or {}
    except Exception as e:
        logger.warning(f"[auto_poller] {provider_id} raised: {type(e).__name__}: {e}")
        return

    inserted = body.get("inserted", 0)
    fetched = body.get("fetched", 0)
    skipped_dup = body.get("skipped_dup", 0)
    skipped_unmatched = body.get("skipped_unmatched", 0)
    settle = body.get("settle") or {}
    won = settle.get("won", 0)
    lost = settle.get("lost", 0)
    # Only log when something interesting happened — most ticks are no-ops
    if inserted or won or lost or skipped_unmatched:
        logger.info(
            f"[auto_poller] {provider_id}: fetched={fetched} inserted={inserted} "
            f"won={won} lost={lost} skipped_dup={skipped_dup} skipped_unmatched={skipped_unmatched}"
        )


async def _correlate_arbs(client: httpx.AsyncClient, local_url: str) -> None:
    """Link any newly recorded arb legs. Forwarded to the server by the
    local proxy's /api/* route."""
    try:
        r = await client.post(f"{local_url}/api/bets/correlate-arbs", timeout=60.0)
        if r.status_code != 200:
            logger.warning(f"[auto_poller] correlate-arbs → {r.status_code}: {(r.text or '')[:200]}")
            return
        body = r.json() or {}
        if body.get("linked"):
            logger.info(f"[auto_poller] arb correlation: linked={body['linked']} groups={body['groups']}")
    except Exception as e:
        logger.warning(f"[auto_poller] correlate-arbs raised: {type(e).__name__}: {e}")


async def run_auto_poller(local_url: str = "http://127.0.0.1:8000") -> None:
    """Forever loop — calls /mirror/sync-positions for each provider every
    POLL_INTERVAL_SEC. Per-provider failures are isolated."""
    logger.info(f"[auto_poller] started (interval={POLL_INTERVAL_SEC}s, providers={PROVIDERS})")
    async with httpx.AsyncClient() as client:
        while True:
            try:
                for pid in PROVIDERS:
                    await _tick_one(client, local_url, pid)
                await _correlate_arbs(client, local_url)
            except asyncio.CancelledError:
                logger.info("[auto_poller] cancelled — shutting down")
                raise
            except Exception as e:
                logger.warning(f"[auto_poller] tick crashed: {type(e).__name__}: {e}")
            await asyncio.sleep(POLL_INTERVAL_SEC)
