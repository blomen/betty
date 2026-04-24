"""Generate a structured trading-session review from broker_trades.

Usage (in container):
    python /app/backend/scripts/session_review.py                    # yesterday
    python /app/backend/scripts/session_review.py --date 2026-04-17  # specific day
    python /app/backend/scripts/session_review.py --date all         # all data

Writes JSON to /app/data/rl/sessions/<date>.json and prints a human summary
to stdout. Designed to be cron-friendly.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("session_review")

NQ_TICK = 0.25
CT = timezone(timedelta(hours=-5))  # CME trading time; matches what TopstepX dashboards show

# Duration buckets for reporting (seconds)
DURATION_BUCKETS = [
    (0, 30, "< 30s"),
    (30, 120, "30s-2m"),
    (120, 300, "2-5m"),
    (300, 900, "5-15m"),
    (900, 3600, "15-60m"),
    (3600, 86400, "1-24h"),
    (86400, 10**9, "> 1d"),
]

# Loss-magnitude buckets for stop-clustering detection (ticks)
LOSS_BUCKETS = [
    (0, 5), (5, 10), (10, 15), (15, 20), (20, 25),
    (25, 30), (30, 40), (40, 60), (60, 10**6),
]


def fetch_rows(cur, date_filter: str | None):
    if date_filter and date_filter != "all":
        cur.execute(
            "SELECT id, ts, closed_at, session_date, symbol, side, size, "
            "entry_price, exit_price, pnl_dollars, pnl_r, "
            "signal_action, signal_confidence, signal_zone "
            "FROM broker_trades WHERE session_date = %s ORDER BY closed_at ASC",
            (date_filter,),
        )
    else:
        cur.execute(
            "SELECT id, ts, closed_at, session_date, symbol, side, size, "
            "entry_price, exit_price, pnl_dollars, pnl_r, "
            "signal_action, signal_confidence, signal_zone "
            "FROM broker_trades ORDER BY closed_at ASC"
        )
    return cur.fetchall()


def enrich(rows: list[dict]) -> list[dict]:
    for r in rows:
        open_ts = r["ts"]
        close_ts = r["closed_at"]
        r["duration_sec"] = (
            (close_ts - open_ts).total_seconds() if close_ts and open_ts else 0
        )
        r["points"] = abs(r["exit_price"] - r["entry_price"]) if r["exit_price"] else 0
        r["pnl"] = float(r["pnl_dollars"] or 0)
        r["is_win"] = r["pnl"] > 0
        r["hour_ct"] = (
            close_ts.replace(tzinfo=timezone.utc).astimezone(CT).hour
            if close_ts
            else 0
        )
        r["ticks"] = r["points"] / NQ_TICK
    return rows


def overview(rows: list[dict]) -> dict:
    total = len(rows)
    wins = sum(1 for r in rows if r["is_win"])
    losses = total - wins
    net_pnl = sum(r["pnl"] for r in rows)
    gross_win = sum(r["pnl"] for r in rows if r["is_win"])
    gross_loss = -sum(r["pnl"] for r in rows if not r["is_win"])
    avg_win = gross_win / wins if wins else 0.0
    avg_loss = gross_loss / losses if losses else 0.0
    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / total) if total else 0.0,
        "net_pnl": round(net_pnl, 2),
        "gross_win": round(gross_win, 2),
        "gross_loss": round(-gross_loss, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(-avg_loss, 2),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "win_loss_ratio": round(avg_win / avg_loss, 3) if avg_loss else None,
        "expectancy": round(net_pnl / total, 2) if total else 0.0,
    }


def by_direction(rows: list[dict]) -> dict:
    out = {}
    for side in ("long", "short"):
        sub = [r for r in rows if r["side"] == side]
        if not sub:
            continue
        sw = sum(1 for r in sub if r["is_win"])
        spnl = sum(r["pnl"] for r in sub)
        out[side] = {
            "trades": len(sub),
            "wins": sw,
            "win_rate": sw / len(sub),
            "net": round(spnl, 2),
            "avg": round(spnl / len(sub), 2),
        }
    return out


def by_duration(rows: list[dict]) -> list[dict]:
    out = []
    for lo, hi, label in DURATION_BUCKETS:
        sub = [r for r in rows if lo <= r["duration_sec"] < hi]
        if not sub:
            continue
        sw = sum(1 for r in sub if r["is_win"])
        spnl = sum(r["pnl"] for r in sub)
        out.append(
            {
                "bucket": label,
                "trades": len(sub),
                "wins": sw,
                "win_rate": sw / len(sub),
                "avg_pnl": round(spnl / len(sub), 2),
                "total": round(spnl, 2),
            }
        )
    return out


def by_hour(rows: list[dict]) -> list[dict]:
    groups: dict[int, list] = defaultdict(list)
    for r in rows:
        groups[r["hour_ct"]].append(r)
    out = []
    for h in sorted(groups.keys()):
        sub = groups[h]
        sw = sum(1 for r in sub if r["is_win"])
        spnl = sum(r["pnl"] for r in sub)
        out.append(
            {
                "hour_ct": h,
                "trades": len(sub),
                "wins": sw,
                "win_rate": sw / len(sub),
                "avg_pnl": round(spnl / len(sub), 2),
                "total": round(spnl, 2),
            }
        )
    return out


def loss_histogram(rows: list[dict]) -> dict:
    loss_ticks = sorted(r["ticks"] for r in rows if not r["is_win"])
    win_ticks = sorted(r["ticks"] for r in rows if r["is_win"])
    hist = []
    for lo, hi in LOSS_BUCKETS:
        n = sum(1 for t in loss_ticks if lo <= t < hi)
        if n:
            hist.append({"range": f"{lo}-{hi}t", "count": n})
    return {
        "loss_ticks": {
            "count": len(loss_ticks),
            "min": round(loss_ticks[0], 1) if loss_ticks else None,
            "median": round(median(loss_ticks), 1) if loss_ticks else None,
            "max": round(loss_ticks[-1], 1) if loss_ticks else None,
            "mean": round(mean(loss_ticks), 1) if loss_ticks else None,
        },
        "win_ticks": {
            "count": len(win_ticks),
            "min": round(win_ticks[0], 1) if win_ticks else None,
            "median": round(median(win_ticks), 1) if win_ticks else None,
            "max": round(win_ticks[-1], 1) if win_ticks else None,
            "mean": round(mean(win_ticks), 1) if win_ticks else None,
        },
        "loss_histogram": hist,
    }


def consecutive_loss_tilt(rows: list[dict]) -> list[dict]:
    by_day: dict[str, list] = defaultdict(list)
    for r in rows:
        by_day[r["session_date"]].append(r)
    buckets: dict[int, list] = defaultdict(list)
    for day_rows in by_day.values():
        day_rows.sort(key=lambda x: x["closed_at"])
        streak = 0
        for r in day_rows:
            buckets[streak].append(r["pnl"])
            streak = streak + 1 if not r["is_win"] else 0
    out = []
    for s in sorted(buckets.keys())[:10]:
        pnls = buckets[s]
        if not pnls:
            continue
        sw = sum(1 for p in pnls if p > 0)
        out.append(
            {
                "after_consec_losses": s,
                "trades": len(pnls),
                "win_rate": sw / len(pnls),
                "avg_pnl": round(sum(pnls) / len(pnls), 2),
            }
        )
    return out


def flip_vs_same(rows: list[dict]) -> dict:
    by_day: dict[str, list] = defaultdict(list)
    for r in rows:
        by_day[r["session_date"]].append(r)
    flips = same = 0
    flip_pnl = same_pnl = 0.0
    for day_rows in by_day.values():
        day_rows.sort(key=lambda x: x["closed_at"])
        for prev, curr in zip(day_rows, day_rows[1:]):
            if prev["side"] != curr["side"]:
                flips += 1
                flip_pnl += curr["pnl"]
            else:
                same += 1
                same_pnl += curr["pnl"]
    return {
        "direction_flip": {
            "count": flips,
            "net": round(flip_pnl, 2),
            "avg": round(flip_pnl / flips, 2) if flips else 0.0,
        },
        "same_way_reentry": {
            "count": same,
            "net": round(same_pnl, 2),
            "avg": round(same_pnl / same, 2) if same else 0.0,
        },
    }


def top_outcomes(rows: list[dict], n: int = 5) -> dict:
    def _row(r):
        return {
            "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None,
            "side": r["side"],
            "ticks": round(r["ticks"], 1),
            "duration_sec": int(r["duration_sec"]),
            "pnl": r["pnl"],
        }

    return {
        "top_wins": [_row(r) for r in sorted(rows, key=lambda x: -x["pnl"])[:n]],
        "top_losses": [_row(r) for r in sorted(rows, key=lambda x: x["pnl"])[:n]],
    }


def auto_flags(summary: dict, rows: list[dict]) -> list[str]:
    flags = []
    total = summary["overview"]["trades"]
    if total == 0:
        return ["no trades"]

    quick_loss = [r for r in rows if not r["is_win"] and r["duration_sec"] < 60]
    if len(quick_loss) > total * 0.3:
        flags.append(
            f"{len(quick_loss)}/{total} trades stopped within 60s "
            "- entries getting picked off fast"
        )

    o = summary["overview"]
    if o.get("avg_win") and o.get("avg_loss"):
        ratio = o["avg_win"] / o["avg_loss"]
        wr = o["win_rate"]
        if wr < 0.3 and ratio < 1 / wr - 1 if wr else False:
            flags.append(
                f"WR {wr * 100:.1f}% requires avg_win/avg_loss >= "
                f"{1 / wr - 1:.1f}x; currently {ratio:.2f}x"
            )

    loss_hist = summary["loss_histogram"]["loss_histogram"]
    if loss_hist:
        biggest = max(loss_hist, key=lambda b: b["count"])
        total_losses = summary["overview"]["losses"]
        if total_losses and biggest["count"] > total_losses * 0.5:
            flags.append(
                f"{biggest['count']}/{total_losses} losses cluster in "
                f"{biggest['range']} - looks like a fixed stop"
            )

    by_dir = summary["by_direction"]
    if "short" in by_dir and by_dir["short"]["net"] < 0 and by_dir["short"]["trades"] >= 10:
        flags.append(
            f"short side net {by_dir['short']['net']:.0f} over "
            f"{by_dir['short']['trades']} trades "
            f"(WR {by_dir['short']['win_rate'] * 100:.1f}%)"
        )

    return flags


def build_summary(rows: list[dict]) -> dict:
    summary = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "trade_count": len(rows),
        "overview": overview(rows),
        "by_direction": by_direction(rows),
        "by_duration": by_duration(rows),
        "by_hour_ct": by_hour(rows),
        "loss_histogram": loss_histogram(rows),
        "consecutive_loss_tilt": consecutive_loss_tilt(rows),
        "flip_vs_same": flip_vs_same(rows),
        "outcomes": top_outcomes(rows),
    }
    summary["flags"] = auto_flags(summary, rows)
    return summary


# ------------------------------------------------------------------ rendering


def render_text(summary: dict, date_label: str) -> str:
    out = []
    p = out.append
    p(f"\n========== SESSION REVIEW — {date_label} "
      f"({summary['trade_count']} trades) ==========")

    o = summary["overview"]
    if not summary["trade_count"]:
        p("  (no trades)")
        return "\n".join(out)

    p(f"  WR {o['win_rate'] * 100:.1f}% | net ${o['net_pnl']:+,.2f} "
      f"| PF {o['profit_factor']} | expectancy ${o['expectancy']:+.2f}/trade")
    p(f"  avg win ${o['avg_win']:.2f} vs avg loss ${o['avg_loss']:.2f}"
      f" (ratio {o['win_loss_ratio']}x)")

    p("\n  by direction:")
    for side, d in summary["by_direction"].items():
        p(f"    {side:6s} {d['trades']:2d} trades  WR {d['win_rate'] * 100:5.1f}%  "
          f"net ${d['net']:+,.2f}  avg ${d['avg']:+,.2f}")

    p("\n  by duration:")
    for b in summary["by_duration"]:
        p(f"    {b['bucket']:>8s}  {b['trades']:2d}  WR {b['win_rate'] * 100:5.1f}%  "
          f"avg ${b['avg_pnl']:+,.2f}  total ${b['total']:+,.2f}")

    if summary["flags"]:
        p("\n  FLAGS:")
        for f in summary["flags"]:
            p(f"    ! {f}")

    return "\n".join(out)


# ------------------------------------------------------------------ main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD or 'all' (default: yesterday UTC)")
    ap.add_argument(
        "--out-dir",
        default="/app/data/rl/sessions",
        help="Where to write <date>.json archive (empty to skip)",
    )
    ap.add_argument("--json-only", action="store_true", help="Suppress text summary")
    args = ap.parse_args()

    if args.date is None:
        target = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        target = args.date

    dsn = {
        "host": os.environ.get("DB_HOST", "postgres"),
        "user": os.environ.get("DB_USER", "arnold"),
        "password": os.environ["DB_PASSWORD"],
        "dbname": os.environ.get("DB_NAME", "arnold"),
    }
    conn = psycopg2.connect(**dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        rows = fetch_rows(cur, target)
    finally:
        cur.close()
        conn.close()

    rows = enrich(rows)
    summary = build_summary(rows)
    summary["date"] = target

    label = "ALL TIME" if target == "all" else target
    if not args.json_only:
        print(render_text(summary, label))

    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{target}.json"
        with out_path.open("w") as f:
            json.dump(summary, f, indent=2, default=str)
        log.info("wrote %s", out_path)

    # Exit non-zero on empty day so cron email-on-failure shows "no trades"
    if summary["trade_count"] == 0 and target != "all":
        sys.exit(2)


if __name__ == "__main__":
    main()
