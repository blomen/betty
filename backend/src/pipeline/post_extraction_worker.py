"""
Post-extraction worker.

Decouples the analyzer + ML side-effects from the per-provider extraction
hot path. Pipeline cycles enqueue a work item and return immediately;
a single async worker drains the queue serially, with a short debounce
window to coalesce bursts when multiple tiers complete back-to-back.

Why this exists:
- Before: every ProviderSchedule loop ran OpportunityAnalyzer + ML hooks
  inline under a process-global threading.Lock. With 6 api_soft providers
  on a 2-min cooldown, 6 concurrent loops queued for the same lock while
  also racing the cleanup loop on overlapping `odds`/`opportunities`
  rows. PG row-lock contention + DB pool saturation produced the
  api_soft tier freeze observed at 21:53 UTC on 2026-04-25.
- After: extraction loops do their own DB work (events + odds upsert,
  metrics, report) and hand off post-processing here. Analyzer runs once,
  serially, with no lock — debouncing absorbs concurrent tier completions
  into a single analyzer pass.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .metrics import PipelineMetrics

logger = logging.getLogger(__name__)

# Coalesce work items received within this window into a single analyzer run.
# Tiers (sharp, api_soft, browser_soft) frequently complete within a few
# seconds of each other; debouncing avoids running the analyzer 3x back-to-back
# over largely overlapping changed_event_ids sets.
_DEBOUNCE_SEC = 5.0

# Cap queue depth so backpressure surfaces as a log line instead of unbounded
# memory growth. Steady state is ~1 item per provider per cooldown (~30/min).
_MAX_QUEUE_DEPTH = 200

# Poll interval on the blocking get(); short enough that CancelledError
# during shutdown propagates within ~1 second.
_GET_POLL_SEC = 1.0


@dataclass
class PostExtractionWork:
    """One extraction cycle's worth of post-processing context."""

    run_id: str
    tier_name: str | None
    changed_event_ids: set[str] = field(default_factory=set)
    current_run: PipelineMetrics | None = None
    enqueued_at: float = field(default_factory=time.time)


# Thread-safe — pipeline cycles run inside per-provider threads spawned by the
# scheduler's asyncio.to_thread, while the worker runs in the main event loop.
_queue: queue.Queue[PostExtractionWork] = queue.Queue(maxsize=_MAX_QUEUE_DEPTH)


def enqueue(work: PostExtractionWork) -> None:
    """Hand a work item to the worker. Drops on overflow with a loud log."""
    try:
        _queue.put_nowait(work)
    except queue.Full:
        logger.error(
            "[PostExtractionWorker] queue full (%d items) — dropping run %s/%s",
            _MAX_QUEUE_DEPTH,
            work.tier_name,
            work.run_id,
        )


def queue_depth() -> int:
    return _queue.qsize()


async def _drain_pending(initial: PostExtractionWork) -> PostExtractionWork:
    """Coalesce items arriving within the debounce window into the initial one.

    Merges changed_event_ids; advances run_id/tier_name/current_run to the
    most recent item so downstream logging reflects the final tier seen.
    """
    deadline = time.time() + _DEBOUNCE_SEC
    merged = initial
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            nxt = await asyncio.to_thread(_queue.get, True, remaining)
        except queue.Empty:
            break
        merged.changed_event_ids |= nxt.changed_event_ids
        merged.run_id = nxt.run_id
        merged.tier_name = nxt.tier_name
        if nxt.current_run is not None:
            merged.current_run = nxt.current_run
    return merged


def _broadcast_deltas(analysis_results: dict, changed_count: int) -> None:
    from .broadcast import odds_broadcaster

    if odds_broadcaster.client_count <= 0:
        return

    for opp in analysis_results.get("added_opportunities", []):
        odds_broadcaster.publish(
            "opportunity_added",
            {
                "id": opp.id,
                "type": opp.type if hasattr(opp, "type") else "value",
                "edge_pct": getattr(opp, "edge_pct", None),
                "odds1": getattr(opp, "odds1", None),
                "fair_odds": getattr(opp, "fair_odds", None),
                "stake": getattr(opp, "stake", None),
                "event_id": getattr(opp, "event_id", None),
                "provider1": getattr(opp, "provider1_id", None),
                "outcome1": getattr(opp, "outcome1", None),
                "market": getattr(opp, "market", None),
            },
        )
    for opp in analysis_results.get("updated_opportunities", []):
        odds_broadcaster.publish(
            "opportunity_update",
            {
                "id": opp.id,
                "type": opp.type if hasattr(opp, "type") else "value",
                "edge_pct": getattr(opp, "edge_pct", None),
                "odds1": getattr(opp, "odds1", None),
                "fair_odds": getattr(opp, "fair_odds", None),
                "stake": getattr(opp, "stake", None),
            },
        )
    for item in analysis_results.get("removed_opportunities", []):
        if isinstance(item, tuple) and len(item) == 2:
            opp_id, opp_type = item
        else:
            opp_id, opp_type = item, "value"
        odds_broadcaster.publish(
            "opportunity_removed",
            {"id": opp_id, "type": opp_type, "reason": "edge_below_threshold"},
        )
    odds_broadcaster.publish("tier_complete", {"changed_events": changed_count})

    # Flush the API response cache so the next /opportunities request sees
    # the freshly written rows instead of a 60s-stale snapshot.
    try:
        from ..api.routes.opportunities import _opp_cache

        _opp_cache.clear()
    except Exception:
        pass


def _run_analyzer(session, work: PostExtractionWork) -> dict | None:
    from .analyzer import OpportunityAnalyzer

    analyzer = OpportunityAnalyzer(session)
    changed = work.changed_event_ids or None
    try:
        results = analyzer.run(changed_event_ids=changed)
        logger.info(
            "[PostExtractionWorker] analyzer ok: %d value, %d arb (changed=%d)",
            results.get("value", {}).get("found", 0),
            results.get("arb", {}).get("found", 0),
            len(work.changed_event_ids),
        )
        return results
    except Exception as e:
        if "deadlock" in str(e).lower():
            logger.warning("[PostExtractionWorker] analyzer deadlock, skipping: %s", e)
        else:
            logger.exception("[PostExtractionWorker] analyzer failed")
        try:
            session.rollback()
        except Exception:
            pass
        return None


def _log_ml_features(session, work: PostExtractionWork, analysis_results: dict | None) -> None:
    """Record extraction features + per-provider value attribution for ML training."""
    run_id = work.run_id
    tier_name = work.tier_name
    current_run = work.current_run

    try:
        from src.ml.features.extraction_features import (
            extract_extraction_features,
            log_extraction_run,
            update_extraction_outcomes,
        )

        avg_mr = 0.0
        if current_run and current_run.providers:
            rates = [p.match_rate for p in current_run.providers.values() if p.match_rate > 0]
            avg_mr = sum(rates) / len(rates) if rates else 0.0

        features = extract_extraction_features(
            run_id=run_id,
            trigger=tier_name or "manual",
            providers_attempted=current_run.providers_attempted if current_run else 0,
            providers_succeeded=current_run.providers_succeeded if current_run else 0,
            providers_failed=current_run.providers_failed if current_run else 0,
            total_events=current_run.total_events if current_run else 0,
            total_odds=current_run.total_odds if current_run else 0,
            avg_match_rate=avg_mr,
        )
        log_extraction_run(session, features)

        if analysis_results:
            value_found = analysis_results.get("value", {}).get("found", 0)
            arb_found = analysis_results.get("arb", {}).get("found", 0)
            reverse_found = analysis_results.get("reverse", {}).get("found", 0) + analysis_results.get(
                "reverse_value", {}
            ).get("found", 0)

            avg_edge = None
            try:
                from sqlalchemy import func

                from ..db.models import Opportunity

                row = session.query(func.avg(Opportunity.edge_pct)).filter(Opportunity.edge_pct > 0).scalar()
                avg_edge = float(row) if row else None
            except Exception:
                pass

            update_extraction_outcomes(
                session,
                run_id=run_id,
                value_bets_found=value_found,
                avg_edge_pct=avg_edge,
                arb_opportunities_found=arb_found,
                reverse_opportunities_found=reverse_found,
            )

        if current_run and current_run.providers:
            from sqlalchemy import func as sa_func

            from src.ml.features.extraction_features import extract_provider_value, log_provider_value

            from ..db.models import Opportunity

            for pid, pm in current_run.providers.items():
                matched = sum(1 for s in pm.sports.values() if s.events_processed > 0)
                total_sports = len(pm.sports)
                mr = matched / total_sports if total_sports > 0 else 0.0

                vb_count = 0
                vb_avg_edge = None
                try:
                    row = (
                        session.query(
                            sa_func.count(Opportunity.id),
                            sa_func.avg(Opportunity.edge_pct),
                        )
                        .filter(
                            Opportunity.provider1_id == pid,
                            Opportunity.edge_pct > 0,
                            Opportunity.type == "value",
                        )
                        .first()
                    )
                    if row:
                        vb_count = row[0] or 0
                        vb_avg_edge = float(row[1]) if row[1] else None
                except Exception:
                    pass

                pv_features = extract_provider_value(
                    run_id=run_id,
                    provider_id=pid,
                    events_extracted=pm.total_events,
                    odds_extracted=pm.total_odds,
                    duration_seconds=pm.duration_seconds,
                    match_rate=mr,
                    spread_count=sum(s.odds_processed for s in pm.sports.values()),
                    total_count=pm.total_odds,
                    value_bets_from_provider=vb_count,
                    avg_edge_from_provider=vb_avg_edge,
                )
                log_provider_value(session, pv_features)

        session.commit()
    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        logger.debug("ML extraction feature logging skipped: %s", e)


async def _run_side_effects(session, work: PostExtractionWork, analysis_results: dict | None) -> None:
    """Best-effort post-processing: ML features, analytics, CLV, macro, training.

    Each block is independently try/except'd — a failure in one (e.g. macro
    snapshot timeouts) does not block the others.
    """
    run_id = work.run_id
    tier_name = work.tier_name

    # Pinnacle coverage delta (skip on sharp tier — coverage is computed vs sharp)
    if tier_name != "sharp":
        try:
            from src.ml.features.pinnacle_coverage import log_coverage

            n = log_coverage(session, run_id)
            session.commit()
            logger.debug("[PostExtractionWorker] coverage rows logged: %d", n)
        except Exception as e:
            try:
                session.rollback()
            except Exception:
                pass
            logger.debug("Pinnacle coverage logging skipped: %s", e)

    _log_ml_features(session, work, analysis_results)

    # Analytics refresh
    try:
        from src.ml.analytics.engine import AnalyticsEngine

        AnalyticsEngine().refresh(session, run_id)
        session.commit()
    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        logger.debug("Extraction analytics skipped: %s", e)

    # Daily ML training (idempotent: runs once per UTC day)
    try:
        today = time.strftime("%Y-%m-%d")
        if getattr(_run_side_effects, "_last_train_day", None) != today:
            from src.ml.training.train_all import TrainingOrchestrator

            results = TrainingOrchestrator().train_all(session)
            _run_side_effects._last_train_day = today  # type: ignore[attr-defined]
            for model, status in (results or {}).items():
                if status == "trained":
                    logger.info("ML model trained: %s", model)
    except Exception as e:
        logger.debug("ML training check skipped: %s", e)

    # CLV resolution
    try:
        from src.ml.feature_store import resolve_clv_outcomes

        n = resolve_clv_outcomes(session)
        if n:
            logger.info("Resolved CLV for %d ML feature rows", n)
    except Exception:
        pass

    # Macro snapshot (async — uses external HTTP)
    try:
        from src.market_data.macro_provider import fetch_macro_snapshot
        from src.ml.models.macro_engine import store_daily_options_flow

        macro = await fetch_macro_snapshot()
        await store_daily_options_flow(session, macro)
    except Exception as e:
        logger.debug("Daily options_flow storage skipped: %s", e)

    # Trading outcome resolution
    try:
        from src.ml.feature_store import resolve_trading_outcomes

        n = resolve_trading_outcomes(session)
        if n:
            logger.info("Resolved %d trading signal outcomes", n)
    except Exception as e:
        logger.debug("Trading outcome resolution skipped: %s", e)


async def _process(work: PostExtractionWork) -> None:
    """Run analyzer + side-effects against a fresh DB session."""
    from ..db.models import get_session

    session = get_session()
    try:
        analysis_results = await asyncio.to_thread(_run_analyzer, session, work)
        if analysis_results is not None:
            _broadcast_deltas(analysis_results, len(work.changed_event_ids))
        await _run_side_effects(session, work, analysis_results)
    finally:
        try:
            session.close()
        except Exception:
            pass


async def run_worker() -> None:
    """Long-running task: drain the queue, debounce, run side-effects.

    Started as an asyncio task in the FastAPI lifespan. Cancellation is
    cooperative — we poll the blocking get() with a 1 s timeout so shutdown
    propagates within one tick.
    """
    logger.info(
        "[PostExtractionWorker] started (debounce=%.1fs, max_queue=%d)",
        _DEBOUNCE_SEC,
        _MAX_QUEUE_DEPTH,
    )
    while True:
        try:
            try:
                work = await asyncio.to_thread(_queue.get, True, _GET_POLL_SEC)
            except queue.Empty:
                continue

            work = await _drain_pending(work)

            t0 = time.time()
            logger.info(
                "[PostExtractionWorker] processing tier=%s run=%s changed=%d depth_after=%d",
                work.tier_name,
                (work.run_id or "?")[:8],
                len(work.changed_event_ids),
                _queue.qsize(),
            )
            await _process(work)
            logger.info("[PostExtractionWorker] done in %.1fs", time.time() - t0)
        except asyncio.CancelledError:
            logger.info("[PostExtractionWorker] cancelled, exiting")
            return
        except Exception:
            logger.exception("[PostExtractionWorker] fatal error in loop, continuing")
            await asyncio.sleep(1.0)
