"""Daily mirror smoke-test cron — Phase 4 of platform rebuild (2026-05-08).

Wakes every `MIRROR_SMOKE_INTERVAL_S` (default 24h), runs a two-step health
update for every provider in `providers.yaml`:

    1. HTTP probe of `home_url` → writes `home_url_status` ('green'|'amber'|'red')
       + http_code into `mirror_provider_health`.
    2. Recompute event-derived fields (last_balance_intercept_at, etc.) from
       `mirror_event_log` aggregation.

The recompute step is idempotent and the same logic that
`POST /api/mirror/health/recompute` exposes for ad-hoc calls. The HTTP probe
adds the network-reachability dimension that the operator can't get from
the event log alone.

Output: each provider's row in `mirror_provider_health` with `overall`
rolled up to one badge that the frontend §9 matrix renders. Replaces the
static markdown matrix that "lied" — silently showed ✅ for capabilities
that had broken.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from sqlalchemy import func, select

from ..db.models import MirrorEventLog, MirrorProviderHealth

logger = logging.getLogger(__name__)


MIRROR_SMOKE_INTERVAL_S = int(os.environ.get("MIRROR_SMOKE_INTERVAL_S", "86400"))  # 24h
MIRROR_SMOKE_PROBE_TIMEOUT_S = float(os.environ.get("MIRROR_SMOKE_PROBE_TIMEOUT_S", "10"))


def _load_provider_home_urls() -> dict[str, str]:
    """Read providers.yaml + extract home_url per provider.

    The yaml has provider definitions like:
        providers:
          betinia:
            domain: betinia.se
            home_url: https://betinia.se/sv/sport
    Falls back to https://{domain}/ if home_url is absent.
    """
    # `backend/src/jobs/mirror_smoke.py` → `backend/src/config/providers.yaml`
    # is two parents up + /config/providers.yaml.
    cfg_path = Path(__file__).resolve().parents[1] / "config" / "providers.yaml"
    if not cfg_path.exists():
        logger.warning(f"[mirror_smoke] providers.yaml not found at {cfg_path}; skipping probes")
        return {}
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    out: dict[str, str] = {}
    for pid, pcfg in (cfg.get("providers") or {}).items():
        if not isinstance(pcfg, dict):
            continue
        url = pcfg.get("home_url")
        if not url:
            domain = pcfg.get("domain")
            if domain:
                url = f"https://{domain}/"
        if url:
            out[pid] = url
    return out


async def _probe_home_url(client: httpx.AsyncClient, url: str) -> tuple[str, int | None]:
    """GET `url` and return (status_label, http_code).

    status_label:
      'green' — 2xx
      'amber' — 3xx-4xx (reachable, but unexpected response — could be a maintenance page)
      'red'   — 5xx, network error, timeout, DNS fail
    """
    try:
        resp = await client.get(url, timeout=MIRROR_SMOKE_PROBE_TIMEOUT_S, follow_redirects=True)
        code = resp.status_code
        if 200 <= code < 300:
            return "green", code
        if 300 <= code < 500:
            return "amber", code
        return "red", code
    except Exception as e:
        logger.debug(f"[mirror_smoke] probe {url!r} failed: {e!r}")
        return "red", None


def _compute_overall(row: MirrorProviderHealth) -> str:
    """Mirrors `mirror_state.py:_compute_overall`. Defined here to avoid a
    circular import — server cron should not depend on the API route module.
    """
    if row.home_url_status == "red":
        return "red"
    if row.last_provider_skipped_at and (
        not row.last_balance_intercept_at or row.last_provider_skipped_at > row.last_balance_intercept_at
    ):
        return "amber"
    if row.last_balance_intercept_at:
        return "green"
    return "amber"


async def _run_one_pass() -> dict[str, str]:
    """Run probes + recompute for every configured provider. Returns
    {provider_id: overall_label} for the just-completed pass.
    """
    home_urls = _load_provider_home_urls()
    if not home_urls:
        return {}

    # 1. HTTP probes (parallel, single client for connection reuse)
    probe_results: dict[str, tuple[str, int | None]] = {}
    async with httpx.AsyncClient(http2=False, verify=True) as client:
        tasks = {pid: asyncio.create_task(_probe_home_url(client, url)) for pid, url in home_urls.items()}
        for pid, task in tasks.items():
            try:
                probe_results[pid] = await task
            except Exception as e:
                logger.warning(f"[mirror_smoke] probe task {pid} crashed: {e!r}")
                probe_results[pid] = ("red", None)

    # 2. Event-log aggregation + write rows.
    # `get_session_factory()` returns a sessionmaker; calling it returns a
    # Session (not a context manager — close manually in finally).
    summary: dict[str, str] = {}
    db = get_session_factory()()
    try:
        # Set of provider_ids we want to refresh: configured providers + any
        # provider with events in the log (covers manually-added test pids).
        pids_with_events = (
            db.execute(select(MirrorEventLog.provider_id).where(MirrorEventLog.provider_id.isnot(None)).distinct())
            .scalars()
            .all()
        )
        all_pids: set[str] = set(home_urls.keys()) | {p for p in pids_with_events if p}

        for pid in all_pids:

            def _last_ts(event_type: str, _pid=pid):
                return db.execute(
                    select(func.max(MirrorEventLog.ts)).where(
                        MirrorEventLog.provider_id == _pid, MirrorEventLog.event_type == event_type
                    )
                ).scalar()

            last_login = _last_ts("login_detected")
            last_balance = _last_ts("balance_intercepted")
            last_placement = _last_ts("bet_placed")
            last_settled = _last_ts("settlements_confirmed")
            last_skip_row = db.execute(
                select(MirrorEventLog)
                .where(MirrorEventLog.provider_id == pid, MirrorEventLog.event_type == "provider_skipped")
                .order_by(MirrorEventLog.ts.desc())
                .limit(1)
            ).scalar_one_or_none()

            row = db.get(MirrorProviderHealth, pid)
            if row is None:
                row = MirrorProviderHealth(provider_id=pid)
                db.add(row)

            probe = probe_results.get(pid)
            if probe is not None:
                row.home_url_status = probe[0]
                row.home_url_http_code = probe[1]

            row.last_login_detected_at = last_login
            row.last_balance_intercept_at = last_balance
            row.last_placement_at = last_placement
            row.last_settled_at = last_settled
            if last_skip_row:
                row.last_provider_skipped_at = last_skip_row.ts
                row.last_provider_skipped_reason = (last_skip_row.data or {}).get("reason")
            row.overall = _compute_overall(row)
            row.checked_at = datetime.now(timezone.utc)
            summary[pid] = row.overall or "?"

        db.commit()
    finally:
        db.close()
    return summary


async def smoke_loop() -> None:
    """Forever-loop entry point. Hook into FastAPI lifespan startup as a
    background task. Cancellation-safe.
    """
    logger.info(f"[mirror_smoke] starting; interval={MIRROR_SMOKE_INTERVAL_S}s")
    while True:
        try:
            summary = await _run_one_pass()
            if summary:
                ok = sum(1 for v in summary.values() if v == "green")
                logger.info(
                    f"[mirror_smoke] pass complete: {ok}/{len(summary)} green "
                    f"({', '.join(f'{k}={v}' for k, v in sorted(summary.items()))})"
                )
            else:
                logger.info("[mirror_smoke] pass complete: no providers configured")
        except asyncio.CancelledError:
            logger.info("[mirror_smoke] cancelled")
            break
        except Exception:
            logger.exception("[mirror_smoke] unhandled error in pass; will retry")

        try:
            await asyncio.sleep(MIRROR_SMOKE_INTERVAL_S)
        except asyncio.CancelledError:
            break
