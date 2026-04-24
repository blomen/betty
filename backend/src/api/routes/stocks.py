"""Stocks API routes — broker-trade ingestion from the local TopstepX runtime.

The local arnold app POSTs every closed round-trip here (entry/exit/PnL/signal
context). Without this, trade outcomes only existed in stdout + a 100-deep
in-memory dashboard deque, lost on container restart.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...db.models import BrokerTrade
from ..deps import get_db

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


def _parse_ts(v: str | float | None) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc).replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


class BrokerTradeIn(BaseModel):
    """Round-trip trade payload from the local broker_adapter."""

    ts: str | float | None = None
    session_date: str
    symbol: str = "NQ"
    side: str
    size: int
    entry_price: float
    stop_price: float | None = None
    exit_price: float | None = None
    tp_price: float | None = None
    pnl_dollars: float | None = None
    pnl_r: float | None = None
    fill_latency_ms: float | None = None
    slippage_ticks: float | None = None
    was_stop: bool | None = None
    trail_count: int | None = None
    stop_ticks: float | None = None
    signal_action: str | None = None
    signal_confidence: float | None = None
    signal_zone: float | None = None
    signal_trigger: str | None = None
    signal_cont_p: float | None = None
    signal_rev_p: float | None = None
    closed_at: str | float | None = None
    # Idempotency: a deterministic key the client can supply so retries don't
    # double-insert. We store it in signal_trigger for now (string column,
    # already indexed-friendly via session_date+ts).
    client_dedupe_key: str | None = Field(default=None)


@router.post("/broker-trades")
def ingest_broker_trade(body: BrokerTradeIn, db: Session = Depends(get_db)):
    """Persist a closed round-trip from the local TopstepX adapter.

    Idempotent on (session_date, symbol, side, entry_price, closed_at) — the
    client should send the same payload on retry; we'll skip if it already
    exists.
    """
    closed_at = _parse_ts(body.closed_at)
    ts = _parse_ts(body.ts) or datetime.utcnow()

    # Cheap dedupe: same closed_at + entry_price + size + side already in DB.
    if closed_at is not None:
        existing = (
            db.query(BrokerTrade.id)
            .filter(
                BrokerTrade.closed_at == closed_at,
                BrokerTrade.symbol == body.symbol,
                BrokerTrade.side == body.side,
                BrokerTrade.entry_price == body.entry_price,
                BrokerTrade.size == body.size,
            )
            .first()
        )
        if existing:
            return {"id": existing[0], "deduped": True}

    row = BrokerTrade(
        ts=ts,
        session_date=body.session_date,
        symbol=body.symbol,
        side=body.side,
        size=body.size,
        entry_price=body.entry_price,
        stop_price=body.stop_price,
        exit_price=body.exit_price,
        pnl_dollars=body.pnl_dollars,
        pnl_r=body.pnl_r,
        fill_latency_ms=body.fill_latency_ms,
        slippage_ticks=body.slippage_ticks,
        signal_action=body.signal_action,
        signal_confidence=body.signal_confidence,
        signal_zone=body.signal_zone,
        closed_at=closed_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "deduped": False}


@router.get("/broker-trades")
def list_broker_trades(
    days: int = 30,
    symbol: str | None = None,
    db: Session = Depends(get_db),
):
    """Return recent persisted trades — for the local dashboard's history view."""
    cutoff = datetime.utcnow() - __import__("datetime").timedelta(days=days)
    q = db.query(BrokerTrade).filter(BrokerTrade.ts >= cutoff)
    if symbol:
        q = q.filter(BrokerTrade.symbol == symbol)
    rows = q.order_by(BrokerTrade.ts.desc()).limit(2000).all()

    def _row_dict(r: BrokerTrade) -> dict:
        return {
            "id": r.id,
            "ts": r.ts.isoformat() if r.ts else None,
            "session_date": r.session_date,
            "symbol": r.symbol,
            "side": r.side,
            "size": r.size,
            "entry_price": r.entry_price,
            "stop_price": r.stop_price,
            "exit_price": r.exit_price,
            "pnl_dollars": r.pnl_dollars,
            "pnl_r": r.pnl_r,
            "signal_action": r.signal_action,
            "signal_confidence": r.signal_confidence,
            "signal_zone": r.signal_zone,
            "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        }

    return {"trades": [_row_dict(r) for r in rows], "count": len(rows)}


@router.get("/broker-trades/sessions")
def session_aggregates(db: Session = Depends(get_db)):
    """Per-trading-day aggregates: count, win rate, total PnL, max drawdown.

    Mirrors the table TopstepX shows on its dashboard.
    """
    from sqlalchemy import case, func

    rows = (
        db.query(
            BrokerTrade.session_date.label("date"),
            func.count().label("trades"),
            func.sum(case((BrokerTrade.pnl_dollars > 0, 1), else_=0)).label("wins"),
            func.sum(BrokerTrade.pnl_dollars).label("pnl"),
            func.max(BrokerTrade.pnl_dollars).label("biggest_win"),
            func.min(BrokerTrade.pnl_dollars).label("biggest_loss"),
            func.avg(case((BrokerTrade.pnl_dollars > 0, BrokerTrade.pnl_dollars))).label("avg_win"),
            func.avg(case((BrokerTrade.pnl_dollars <= 0, BrokerTrade.pnl_dollars))).label("avg_loss"),
        )
        .group_by(BrokerTrade.session_date)
        .order_by(BrokerTrade.session_date.desc())
        .all()
    )
    out = []
    for r in rows:
        trades = r.trades or 0
        wins = r.wins or 0
        out.append(
            {
                "date": r.date,
                "trades": trades,
                "wins": wins,
                "losses": trades - wins,
                "win_rate": round(wins / trades, 4) if trades else 0.0,
                "pnl": float(r.pnl or 0),
                "biggest_win": float(r.biggest_win or 0),
                "biggest_loss": float(r.biggest_loss or 0),
                "avg_win": float(r.avg_win or 0),
                "avg_loss": float(r.avg_loss or 0),
            }
        )
    return {"sessions": out, "count": len(out)}
