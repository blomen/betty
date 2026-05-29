"""Opp-snapshots stats API — surfaces CLV data for the Stats > Shadow CLV sub-tab."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ...db.models import Event, OppSnapshot
from ..deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/opp-snapshots", tags=["opp_snapshots"])


@router.get("/stats")
def get_stats(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict:
    """Aggregated CLV stats for the Stats > Shadow CLV sub-tab.

    Returns three sections:
    - summary: scalar KPIs (total, distinct_events, mean_pinnacle_clv_pct, beat_close_pct)
    - history: time-series rows for the cumulative-CLV chart
    - breakdown: per (provider, type, market) means + sample size (n >= 3)

    All slices filtered to clv_computed_at IS NOT NULL AND first_detected_at > now - days.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)

    base = db.query(OppSnapshot).filter(
        OppSnapshot.clv_computed_at.isnot(None),
        OppSnapshot.first_detected_at > cutoff,
        OppSnapshot.pinnacle_clv_pct.isnot(None),
    )

    # ---- Summary ----
    total = base.count()
    distinct_events = base.with_entities(func.count(func.distinct(OppSnapshot.event_id))).scalar() or 0
    mean_pin = base.with_entities(func.avg(OppSnapshot.pinnacle_clv_pct)).scalar()
    beat = base.with_entities(func.sum(case((OppSnapshot.pinnacle_clv_pct >= 0, 1), else_=0))).scalar() or 0
    beat_pct = (beat / total * 100.0) if total else None

    summary = {
        "total": int(total),
        "distinct_events": int(distinct_events),
        "mean_pinnacle_clv_pct": float(mean_pin) if mean_pin is not None else None,
        "beat_close_pct": float(beat_pct) if beat_pct is not None else None,
    }

    # ---- History (time-series for chart) ----
    history_rows = (
        base.with_entities(
            OppSnapshot.first_detected_at,
            OppSnapshot.type,
            OppSnapshot.pinnacle_clv_pct,
        )
        .order_by(OppSnapshot.first_detected_at.asc())
        .all()
    )
    history = [
        {
            "detected_at": row.first_detected_at.isoformat() if row.first_detected_at else None,
            "type": row.type,
            "pinnacle_clv_pct": float(row.pinnacle_clv_pct),
        }
        for row in history_rows
    ]

    # ---- Breakdown table (n >= 3) ----
    breakdown_rows = (
        base.with_entities(
            OppSnapshot.provider1_id,
            OppSnapshot.type,
            OppSnapshot.market,
            func.count().label("n"),
            func.avg(OppSnapshot.pinnacle_clv_pct).label("mean_pin"),
            func.avg(OppSnapshot.provider_clv_pct).label("mean_prov"),
            func.avg(OppSnapshot.edge_pct_at_detection).label("mean_edge"),
        )
        .group_by(OppSnapshot.provider1_id, OppSnapshot.type, OppSnapshot.market)
        .having(func.count() >= 3)
        .order_by(func.count().desc())
        .all()
    )
    breakdown = [
        {
            "provider_id": row.provider1_id,
            "type": row.type,
            "market": row.market,
            "n": int(row.n),
            "mean_pinnacle_clv_pct": float(row.mean_pin) if row.mean_pin is not None else None,
            "mean_provider_clv_pct": float(row.mean_prov) if row.mean_prov is not None else None,
            "mean_edge_at_detection": float(row.mean_edge) if row.mean_edge is not None else None,
        }
        for row in breakdown_rows
    ]

    # ---- Per-sport blended-vs-Pinnacle comparison (drives flip decisions) ----
    # Only rows where BOTH CLV values exist, so the delta is apples-to-apples.
    blend_base = (
        db.query(
            Event.sport.label("sport"),
            func.count().label("n"),
            func.avg(OppSnapshot.pinnacle_clv_pct).label("mean_pin"),
            func.avg(OppSnapshot.blended_clv_pct).label("mean_blend"),
        )
        .join(Event, Event.id == OppSnapshot.event_id)
        .filter(
            OppSnapshot.clv_computed_at.isnot(None),
            OppSnapshot.first_detected_at > cutoff,
            OppSnapshot.pinnacle_clv_pct.isnot(None),
            OppSnapshot.blended_clv_pct.isnot(None),
        )
        .group_by(Event.sport)
        .having(func.count() >= 3)
        .order_by(func.count().desc())
        .all()
    )
    sport_blend_comparison = [
        {
            "sport": row.sport,
            "n": int(row.n),
            "mean_pinnacle_clv_pct": float(row.mean_pin) if row.mean_pin is not None else None,
            "mean_blended_clv_pct": float(row.mean_blend) if row.mean_blend is not None else None,
            "delta": (
                float(row.mean_blend) - float(row.mean_pin)
                if row.mean_blend is not None and row.mean_pin is not None
                else None
            ),
        }
        for row in blend_base
    ]

    return {
        "summary": summary,
        "history": history,
        "breakdown": breakdown,
        "sport_blend_comparison": sport_blend_comparison,
    }
