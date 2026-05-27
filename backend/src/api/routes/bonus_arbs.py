"""Bonus-Arb Tracker endpoint.

Read-only audit view for the user's bonus-extraction experiment across
{lodur, betinia, swiper}. Groups anchor legs at those providers with their
matched sharp counter (via bets.arb_group_id) and returns realized-vs-displayed
yield per arb, summary aggregates for today / this week / last 30 days, and
30 calendar days of P&L for a bar chart.

Day/week boundaries computed in Europe/Stockholm to match how the user reads
"my Tuesday arbs". All monetary values returned in SEK (USD/USDC * 10.50).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...db.models import Bet, Event
from ...repositories import ProfileRepo
from ..deps import get_db

router = APIRouter(prefix="/api/bets/bonus-arbs", tags=["bets"])

SOFT_PROVIDERS = {"lodur", "betinia", "swiper"}
SEK_PER = {"USD": 10.50, "USDC": 10.50, "SEK": 1.0}
STK = ZoneInfo("Europe/Stockholm")
DAILY_HISTORY_DAYS = 30


def _now_utc() -> datetime:
    """Indirection so tests can freeze 'now'."""
    return datetime.now(UTC)


def _to_sek(amount: float | None, currency: str) -> float | None:
    if amount is None:
        return None
    return round(amount * SEK_PER.get(currency or "SEK", 1.0), 2)


def _window_bounds(window: str, now_utc: datetime) -> tuple[datetime, datetime]:
    """Return (since_utc, until_utc) bounds for the requested window.

    'today' = since 00:00 Stockholm today
    'week'  = since Monday 00:00 Stockholm of this week
    '30d'   = last 30 calendar days (since 00:00 Stockholm 29 days ago)
    """
    now_stk = now_utc.astimezone(STK)
    if window == "today":
        start_stk = now_stk.replace(hour=0, minute=0, second=0, microsecond=0)
    elif window == "week":
        start_stk = now_stk.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now_stk.weekday())
    else:  # "30d"
        start_stk = now_stk.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=DAILY_HISTORY_DAYS - 1)
    return start_stk.astimezone(UTC), now_utc


def _placed_at_stk_date(b: Bet) -> date:
    """Calendar date in Europe/Stockholm of a bet's placed_at.

    Bet.placed_at is stored as naive UTC (TIMESTAMP WITHOUT TIME ZONE),
    so we re-attach UTC before converting.
    """
    return b.placed_at.replace(tzinfo=UTC).astimezone(STK).date()


def _leg_dict(b: Bet) -> dict:
    return {
        "id": b.id,
        "provider_id": b.provider_id,
        "market": b.market,
        "outcome": b.outcome,
        "point": b.point,
        "odds": b.odds,
        "stake_sek": _to_sek(b.stake, b.currency or "SEK"),
        "stake_native": b.stake,
        "currency": b.currency or "SEK",
        "payout_sek": _to_sek(b.payout, b.currency or "SEK"),
        "profit_sek": _to_sek(b.profit, b.currency or "SEK"),
        "result": b.result,
        "is_bonus": bool(b.is_bonus),
        "fair_odds_at_placement": b.fair_odds_at_placement,
        "clv_pct": b.clv_pct,
        "provider_clv_pct": b.provider_clv_pct,
    }


def _event_dict(ev: Event | None) -> dict | None:
    if ev is None:
        return None
    return {
        "id": ev.id,
        "home_team": ev.home_team,
        "away_team": ev.away_team,
        "display_home": ev.display_home,
        "display_away": ev.display_away,
        "sport": ev.sport,
        "league": ev.league,
        "start_time": ev.start_time.isoformat() + "Z" if ev.start_time else None,
    }


def _arb_status(anchor: Bet, counter: Bet | None) -> str:
    if counter is None:
        return "partial"
    settled_states = {"won", "lost", "void"}
    a_settled = anchor.result in settled_states
    c_settled = counter.result in settled_states
    if a_settled and c_settled:
        return "settled"
    if not a_settled and not c_settled:
        return "pending"
    return "partial"


def _displayed_yield_pct(anchor: Bet, counter: Bet | None) -> float | None:
    """Theoretical arb yield at placement. None for bonus or unpaired anchor."""
    if counter is None or anchor.is_bonus:
        return None
    if anchor.odds <= 1.0 or counter.odds <= 1.0:
        return None
    inv_sum = (1.0 / anchor.odds) + (1.0 / counter.odds)
    if inv_sum <= 0:
        return None
    return round((1.0 / inv_sum - 1.0) * 100, 3)


def _build_group(anchor: Bet, counter: Bet | None, counter_share: float, events: dict[str, Event]) -> dict:
    """Build one group dict. counter_share is 1.0 unless multiple anchors
    share the same counter (sister-skin replay), in which case the counter's
    stake/payout/profit are divided across anchors so aggregate totals match.
    """
    anchor_leg = _leg_dict(anchor)
    counter_leg = _leg_dict(counter) if counter is not None else None

    if counter_leg is not None and counter_share != 1.0:
        for k in ("stake_sek", "stake_native", "payout_sek", "profit_sek"):
            if counter_leg[k] is not None:
                counter_leg[k] = round(counter_leg[k] * counter_share, 2)

    status = _arb_status(anchor, counter)
    total_stake_sek = anchor_leg["stake_sek"] or 0.0
    if counter_leg is not None:
        total_stake_sek += counter_leg["stake_sek"] or 0.0
    total_stake_sek = round(total_stake_sek, 2)

    realized_yield_pct: float | None = None
    pnl_sek: float | None = None
    if status == "settled":
        pnl_sek = round((anchor_leg["profit_sek"] or 0.0) + (counter_leg["profit_sek"] if counter_leg else 0.0), 2)
        if total_stake_sek > 0:
            realized_yield_pct = round(pnl_sek / total_stake_sek * 100, 3)

    ev = events.get(anchor.event_id) if anchor.event_id else None
    return {
        "arb_group_id": anchor.arb_group_id,
        "status": status,
        "placed_at": anchor.placed_at.replace(tzinfo=UTC).astimezone(STK).isoformat(),
        "event": _event_dict(ev),
        "boost_event": anchor.boost_event,
        "anchor": anchor_leg,
        "counter": counter_leg,
        "total_stake_sek": total_stake_sek,
        "displayed_yield_pct": _displayed_yield_pct(anchor, counter),
        "realized_yield_pct": realized_yield_pct,
        "pnl_sek": pnl_sek,
    }


def _summarize(groups: list[dict]) -> dict:
    n = len(groups)
    settled = [g for g in groups if g["status"] == "settled"]
    disp = [g["displayed_yield_pct"] for g in groups if g["displayed_yield_pct"] is not None]
    real = [g["realized_yield_pct"] for g in settled if g["realized_yield_pct"] is not None]
    anchor_clv = [g["anchor"]["clv_pct"] for g in groups if g["anchor"]["clv_pct"] is not None]
    counter_clv = [
        g["counter"]["clv_pct"] for g in groups if g["counter"] is not None and g["counter"]["clv_pct"] is not None
    ]
    counter_prov_clv = [
        g["counter"]["provider_clv_pct"]
        for g in groups
        if g["counter"] is not None and g["counter"]["provider_clv_pct"] is not None
    ]
    return {
        "arbs": n,
        "settled": len(settled),
        "stake_sek": round(sum(g["total_stake_sek"] for g in groups), 2),
        "pnl_sek": round(sum(g["pnl_sek"] or 0.0 for g in settled), 2),
        "avg_displayed_pct": round(sum(disp) / len(disp), 3) if disp else None,
        "avg_realized_pct": round(sum(real) / len(real), 3) if real else None,
        "anchor_clv_avg": round(sum(anchor_clv) / len(anchor_clv), 2) if anchor_clv else None,
        "counter_clv_avg": round(sum(counter_clv) / len(counter_clv), 2) if counter_clv else None,
        "counter_provider_clv_avg": (
            round(sum(counter_prov_clv) / len(counter_prov_clv), 2) if counter_prov_clv else None
        ),
    }


def _daily_buckets(groups_30d: list[dict], now_utc: datetime) -> list[dict]:
    """Bucket 30d of groups by Stockholm calendar date, oldest first.

    Zero-fills missing days so the bar chart has stable width.
    """
    today_stk = now_utc.astimezone(STK).date()
    dates = [today_stk - timedelta(days=i) for i in range(DAILY_HISTORY_DAYS - 1, -1, -1)]
    by_day: dict[date, list[dict]] = defaultdict(list)
    for g in groups_30d:
        # placed_at in groups is the Stockholm-tz isoformat string; parse back.
        d = datetime.fromisoformat(g["placed_at"]).date()
        by_day[d].append(g)
    out = []
    for d in dates:
        items = by_day.get(d, [])
        settled = [g for g in items if g["status"] == "settled"]
        disp = [g["displayed_yield_pct"] for g in items if g["displayed_yield_pct"] is not None]
        real = [g["realized_yield_pct"] for g in settled if g["realized_yield_pct"] is not None]
        out.append(
            {
                "date": d.isoformat(),
                "arbs": len(items),
                "settled": len(settled),
                "stake_sek": round(sum(g["total_stake_sek"] for g in items), 2),
                "pnl_sek": round(sum(g["pnl_sek"] or 0.0 for g in settled), 2),
                "avg_displayed_pct": round(sum(disp) / len(disp), 3) if disp else None,
                "avg_realized_pct": round(sum(real) / len(real), 3) if real else None,
            }
        )
    return out


@router.get("")
def get_bonus_arbs(
    window: Literal["today", "week", "30d"] = "week",
    db: Session = Depends(get_db),
):
    """Paired anchor+counter view for arbs placed at lodur/betinia/swiper."""
    profile = ProfileRepo(db).get_active()
    if profile is None:
        return {
            "window": window,
            "since": None,
            "until": None,
            "summary": {"today": _summarize([]), "week": _summarize([]), "thirty": _summarize([])},
            "daily": _daily_buckets([], _now_utc()),
            "groups": [],
        }

    now = _now_utc()
    # Always fetch 30 days for daily buckets + the "thirty" summary; filter
    # for groups[] / window-summary in-memory afterwards.
    since_30d, until = _window_bounds("30d", now)
    since_30d_naive = since_30d.replace(tzinfo=None)
    until_naive = until.replace(tzinfo=None)

    anchors: list[Bet] = (
        db.query(Bet)
        .filter(
            Bet.profile_id == profile.id,
            Bet.provider_id.in_(SOFT_PROVIDERS),
            Bet.placed_at >= since_30d_naive,
            Bet.placed_at < until_naive,
        )
        .order_by(Bet.placed_at.desc())
        .all()
    )

    # Counter legs by arb_group_id. Exclude anchors themselves.
    arb_gids = {a.arb_group_id for a in anchors if a.arb_group_id}
    counters_by_gid: dict[str, list[Bet]] = defaultdict(list)
    anchors_by_gid: dict[str, list[Bet]] = defaultdict(list)
    for a in anchors:
        if a.arb_group_id:
            anchors_by_gid[a.arb_group_id].append(a)
    if arb_gids:
        rows = (
            db.query(Bet)
            .filter(
                Bet.profile_id == profile.id,
                Bet.arb_group_id.in_(arb_gids),
                Bet.provider_id.notin_(SOFT_PROVIDERS),
            )
            .all()
        )
        for c in rows:
            counters_by_gid[c.arb_group_id].append(c)

    # Hydrate events.
    event_ids = {a.event_id for a in anchors if a.event_id}
    events: dict[str, Event] = {}
    if event_ids:
        for ev in db.query(Event).filter(Event.id.in_(event_ids)).all():
            events[ev.id] = ev

    # Build groups. Sister-skin replay: if N anchors share a gid with 1 counter,
    # each anchor renders as its own group with counter stake/profit/payout
    # divided by N so aggregate totals match.
    groups_30d: list[dict] = []
    for a in anchors:
        counter = None
        share = 1.0
        if a.arb_group_id:
            cands = counters_by_gid.get(a.arb_group_id, [])
            if cands:
                counter = cands[0]  # one counter per arb_group_id is the norm
                sibling_anchors = anchors_by_gid.get(a.arb_group_id, [])
                if len(sibling_anchors) > 1:
                    share = 1.0 / len(sibling_anchors)
        groups_30d.append(_build_group(a, counter, share, events))

    # Window-filter for groups[].
    since_window, _ = _window_bounds(window, now)

    def in_window(g: dict, window_name: str) -> bool:
        ws_utc, _ = _window_bounds(window_name, now)
        return datetime.fromisoformat(g["placed_at"]) >= ws_utc.astimezone(STK)

    groups_in_window = [g for g in groups_30d if in_window(g, window)]

    return {
        "window": window,
        "since": since_window.isoformat(),
        "until": until.isoformat(),
        "summary": {
            "today": _summarize([g for g in groups_30d if in_window(g, "today")]),
            "week": _summarize([g for g in groups_30d if in_window(g, "week")]),
            "thirty": _summarize(groups_30d),
        },
        "daily": _daily_buckets(groups_30d, now),
        "groups": groups_in_window,
    }
