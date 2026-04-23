"""M10d: Coverage Optimizer — identifies and prioritizes Pinnacle coverage gaps.

Activates at 20+ pinnacle_coverage_log rows per provider.
Analyzes coverage gaps by sport/market type, ranks by impact, and surfaces
unmatched Pinnacle events with diagnostic info for debugging.
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

MARKET_WEIGHTS = {"spread": 1.5, "total": 1.3, "event": 1.0}


class CoverageOptimizer:
    activation_threshold = 20

    def check_and_train(self, session) -> dict | None:
        from sqlalchemy import text

        rows = session.execute(
            text("SELECT provider_id, COUNT(*) as cnt FROM pinnacle_coverage_log GROUP BY provider_id")
        ).fetchall()
        ready = [pid for pid, cnt in rows if cnt >= self.activation_threshold]
        if not ready:
            return None

        last_run = session.execute(
            text("SELECT run_id FROM pinnacle_coverage_log ORDER BY created_at DESC LIMIT 1")
        ).scalar()
        if not last_run:
            return None

        cov_rows = session.execute(
            text(
                "SELECT provider_id, sport, event_coverage_pct, spread_coverage_pct, "
                "total_coverage_pct, missing_events, missing_spread, missing_total "
                "FROM pinnacle_coverage_log WHERE run_id = :rid"
            ),
            {"rid": last_run},
        ).fetchall()

        coverage_dicts = [
            {
                "provider_id": r[0],
                "sport": r[1],
                "event_coverage_pct": r[2] or 0,
                "spread_coverage_pct": r[3] or 0,
                "total_coverage_pct": r[4] or 0,
                "missing_events": r[5] or 0,
                "missing_spread": r[6] or 0,
                "missing_total": r[7] or 0,
            }
            for r in cov_rows
        ]

        gaps = self.identify_gaps(coverage_dicts)
        summary = self.provider_coverage_summary(coverage_dicts)
        agg = self.aggregate_coverage(coverage_dicts)
        unmatched = self.find_unmatched_events(session)

        return {
            "status": "computed",
            "gaps": gaps[:20],
            "provider_summary": summary,
            "sport_aggregate": agg,
            "unmatched_events": unmatched,
        }

    def identify_gaps(self, coverage_rows: list[dict]) -> list[dict]:
        gaps = []
        sport_gaps = defaultdict(lambda: {"spread": 0, "total": 0, "event": 0})

        for row in coverage_rows:
            sport = row["sport"]
            sport_gaps[sport]["spread"] += row.get("missing_spread", 0)
            sport_gaps[sport]["total"] += row.get("missing_total", 0)
            sport_gaps[sport]["event"] += row.get("missing_events", 0)

        for sport, markets in sport_gaps.items():
            for market, missing in markets.items():
                if missing <= 0:
                    continue
                weight = MARKET_WEIGHTS.get(market, 1.0)
                gaps.append(
                    {
                        "sport": sport,
                        "market": market,
                        "missing_count": missing,
                        "impact_score": round(missing * weight, 1),
                    }
                )

        gaps.sort(key=lambda x: x["impact_score"], reverse=True)
        return gaps

    def provider_coverage_summary(self, coverage_rows: list[dict]) -> dict:
        provider_data = defaultdict(list)
        for row in coverage_rows:
            provider_data[row["provider_id"]].append(row)

        summary = {}
        for pid, rows in provider_data.items():
            summary[pid] = {
                "avg_event_coverage": round(sum(r["event_coverage_pct"] for r in rows) / len(rows), 1),
                "avg_spread_coverage": round(sum(r["spread_coverage_pct"] for r in rows) / len(rows), 1),
                "avg_total_coverage": round(sum(r["total_coverage_pct"] for r in rows) / len(rows), 1),
                "sports_covered": len(rows),
                "total_missing_events": sum(r["missing_events"] for r in rows),
            }
        return summary

    def aggregate_coverage(self, coverage_rows: list[dict]) -> dict:
        sport_data = defaultdict(list)
        for row in coverage_rows:
            sport_data[row["sport"]].append(row)

        agg = {}
        for sport, rows in sport_data.items():
            agg[sport] = {
                "best_event_coverage": max(r["event_coverage_pct"] for r in rows),
                "best_spread_coverage": max(r["spread_coverage_pct"] for r in rows),
                "best_total_coverage": max(r["total_coverage_pct"] for r in rows),
                "providers_count": len(set(r["provider_id"] for r in rows)),
                "total_missing_events": min(r["missing_events"] for r in rows),
            }
        return agg

    def find_unmatched_events(self, session, limit: int = 10) -> list[dict]:
        from sqlalchemy import text

        rows = session.execute(
            text(
                "SELECT e.id, e.sport, e.league, e.home_team, e.away_team, e.start_time "
                "FROM events e "
                "JOIN odds o ON o.event_id = e.id AND o.provider_id = 'pinnacle' "
                "WHERE e.id NOT IN ("
                "  SELECT DISTINCT event_id FROM odds "
                "  WHERE provider_id NOT IN ('pinnacle', 'polymarket')"
                ") "
                "ORDER BY e.start_time ASC LIMIT :lim"
            ),
            {"lim": limit},
        ).fetchall()

        unmatched = []
        for r in rows:
            home = r[3] or ""
            away = r[4] or ""
            unmatched.append(
                {
                    "event_id": r[0],
                    "sport": r[1],
                    "league": r[2],
                    "home_team": home,
                    "away_team": away,
                    "start_time": str(r[5]),
                    "team_name_length": len(home) + len(away),
                    "has_special_chars": any(ord(c) > 127 for c in home + away),
                }
            )
        return unmatched
