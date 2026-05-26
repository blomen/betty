"""Server-side 24/7 Polymarket position recorder.

Runs the Polymarket wallet-API recorder on the Hetzner backend so positions
are recorded whether or not the local ``betty.bat`` client is open. The
recorder is pure HTTP (public ``data-api.polymarket.com``, wallet-keyed — no
auth, no browser, no Playwright), so it runs naturally as a backend asyncio
task instead of inside the local client's auto-poller.

Wiring mirrors ``arnold/mirror/router.py:sync_positions`` (the polymarket
branch) but calls the local API over loopback HTTP instead of through the
SSH tunnel — every insert / settle still goes through ``/api/bets`` so the
exact same validation + dedup applies.

Started from ``api/__init__.py`` lifespan (server only). Replaces the
polymarket leg of the local client's ``auto_poller`` — Kalshi/Pinnacle/
Cloudbet stay local because they need browser cookies or differently-named
credentials.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from . import polymarket_api

logger = logging.getLogger(__name__)

# The public data-api is free + cheap — poll often enough that a freshly
# placed Polymarket bet shows in Betty within ~1.5 min (vs the old 5-min
# local poller, which also only ran while betty.bat was open).
POLL_INTERVAL_SEC = float(os.environ.get("POSITION_RECORDER_INTERVAL_SEC", "90"))
# Let uvicorn finish binding + the app warm up before the first loopback call.
STARTUP_DELAY_SEC = 30.0
# Polymarket proxy-wallet address — keys data-api /positions + /trades.
# Override via POLYMARKET_WALLET env; fallback is the default account.
DEFAULT_WALLET = "0x71fca29E6B31a93d262D2972C9b361Af371D426d"
INTERNAL_API_BASE = os.environ.get("INTERNAL_API_BASE", "http://127.0.0.1:8000")


async def _run_polymarket_tick(client: httpx.AsyncClient) -> None:
    """One full insert + settle pass for Polymarket."""
    wallet = os.environ.get("POLYMARKET_WALLET", "").strip() or DEFAULT_WALLET

    async def api_post(payload: dict):
        return await client.post("/api/bets", json=payload, timeout=15.0)

    async def api_patch(bet_id: int, payload: dict):
        return await client.patch(f"/api/bets/{bet_id}", json=payload, timeout=15.0)

    async def api_settle(bet_id: int, res: str, payout: float):
        return await client.put(f"/api/bets/{bet_id}", json={"result": res, "payout": payout}, timeout=15.0)

    async def fetch_events() -> list[dict]:
        # upcoming_only=true — without it the endpoint returns oldest events
        # first and limit=2000 still cuts today's matches.
        try:
            r = await client.get("/api/events?limit=2000&upcoming_only=true", timeout=15.0)
            if r.status_code == 200:
                return r.json().get("events", []) or []
        except Exception as exc:
            logger.warning(f"[server_poller] fetch_events failed: {exc!r}")
        return []

    async def fetch_db_pending() -> list[dict]:
        """Polymarket pending bets — read from the grouped pending-bets endpoint."""
        try:
            r = await client.get("/api/opportunities/play/pending-bets", timeout=15.0)
            if r.status_code == 200:
                for prov in r.json().get("providers", []) or []:
                    if prov.get("provider_id") == "polymarket":
                        return prov.get("bets", []) or []
        except Exception as exc:
            logger.warning(f"[server_poller] fetch_db_pending failed: {exc!r}")
        return []

    async def fetch_known_ids() -> list[str] | None:
        """All recorded polymarket conditionIds (any result) — the dedup source.

        Returns None on failure so the recorder fails closed instead of
        re-inserting every open position against an unknown dedup state.
        """
        try:
            r = await client.get("/api/bets/recorded-ids", params={"provider_id": "polymarket"}, timeout=30.0)
            r.raise_for_status()
            return r.json().get("provider_bet_ids", []) or []
        except Exception as exc:
            logger.warning(f"[server_poller] fetch_known_ids failed: {exc!r}")
            return None

    result = await polymarket_api.sync(
        wallet,
        api_post,
        fetch_events,
        fetch_db_pending,
        api_patch=api_patch,
        fetch_known_ids=fetch_known_ids,
    )

    settle_summary: dict = {}
    try:
        settle_summary = await polymarket_api.settle(wallet, api_settle, fetch_db_pending)
    except Exception as exc:
        logger.warning(f"[server_poller] polymarket settle raised: {exc!r}")

    won = settle_summary.get("won", 0)
    lost = settle_summary.get("lost", 0)
    # Only log when something interesting happened — most ticks are no-ops.
    if result.inserted or won or lost or result.skipped_unmatched or result.errors:
        logger.info(
            f"[server_poller] polymarket: fetched={result.fetched} "
            f"inserted={result.inserted} skipped_dup={result.skipped_dup} "
            f"skipped_unmatched={result.skipped_unmatched} won={won} lost={lost} "
            f"errors={len(result.errors)}"
        )


async def run_position_recorder() -> None:
    """Forever loop — records Polymarket positions every POLL_INTERVAL_SEC.

    Per-tick failures are isolated and logged; the loop keeps going.
    """
    if os.environ.get("POSITION_RECORDER_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        logger.info("[server_poller] disabled via POSITION_RECORDER_DISABLED — not starting")
        return

    api_key = os.environ.get("ARNOLD_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "[server_poller] ARNOLD_API_KEY not set — internal /api calls will be "
            "rejected; recorder will no-op until it is configured"
        )
    headers = {"X-API-Key": api_key} if api_key else {}

    logger.info(f"[server_poller] started (interval={POLL_INTERVAL_SEC}s, base={INTERNAL_API_BASE})")
    await asyncio.sleep(STARTUP_DELAY_SEC)
    async with httpx.AsyncClient(base_url=INTERNAL_API_BASE, headers=headers) as client:
        while True:
            try:
                await _run_polymarket_tick(client)
            except asyncio.CancelledError:
                logger.info("[server_poller] cancelled — shutting down")
                raise
            except Exception as exc:
                logger.warning(f"[server_poller] tick crashed: {type(exc).__name__}: {exc}")
            await asyncio.sleep(POLL_INTERVAL_SEC)
