"""M10b: Provider Priority Scorer — ranks providers by value per extraction second.

Activates at 100+ provider_value_log rows per provider.
Composite score: (value_bets × avg_clv) / duration, penalized by failure rate.
Includes provider health data from provider_run_metrics.
"""

import logging

logger = logging.getLogger(__name__)

DEACTIVATION_THRESHOLD_RUNS = 10


class ProviderPriorityScorer:
    activation_threshold = 100

    def check_and_train(self, session) -> dict | None:
        from sqlalchemy import text

        rows = session.execute(
            text("SELECT provider_id, COUNT(*) as cnt FROM provider_value_log GROUP BY provider_id")
        ).fetchall()
        ready = {pid: cnt for pid, cnt in rows if cnt >= self.activation_threshold}
        if not ready:
            return None

        provider_stats = {}
        for pid in ready:
            stats = session.execute(
                text(
                    "SELECT "
                    "  COALESCE(SUM(value_bets_from_provider), 0) as total_vb, "
                    "  COALESCE(AVG(NULLIF(avg_edge_from_provider, 0)), 0) as avg_edge, "
                    "  COALESCE(AVG(NULLIF(clv_avg_from_provider, 0)), 0) as avg_clv, "
                    "  COALESCE(AVG(duration_seconds), 120) as avg_dur "
                    "FROM provider_value_log WHERE provider_id = :pid"
                ),
                {"pid": pid},
            ).fetchone()

            health = session.execute(
                text(
                    "SELECT "
                    "  COALESCE(SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END), 0) as failures, "
                    "  COALESCE(SUM(CASE WHEN circuit_breaker_tripped = true THEN 1 ELSE 0 END), 0) as cb_trips, "
                    "  COUNT(*) as total, "
                    "  COALESCE(AVG(spread_count), 0) as avg_spread, "
                    "  COALESCE(AVG(total_count), 0) as avg_total "
                    "FROM (SELECT * FROM provider_run_metrics WHERE provider_id = :pid "
                    "ORDER BY start_time DESC LIMIT 10)"
                ),
                {"pid": pid},
            ).fetchone()

            total_runs = ready[pid]
            failure_rate = (health[0] / health[2]) if health[2] > 0 else 0
            is_browser = (
                session.execute(
                    text("SELECT COUNT(*) FROM provider_value_log WHERE provider_id = :pid AND duration_seconds > 100"),
                    {"pid": pid},
                ).scalar()
                or 0
            )

            provider_stats[pid] = {
                "value_bets": stats[0] / total_runs if total_runs else 0,
                "avg_edge": float(stats[1]),
                "avg_clv": float(stats[2]),
                "duration": float(stats[3]),
                "is_browser": is_browser > total_runs * 0.5,
                "failure_rate": round(failure_rate, 2),
                "cb_trips_last_10": int(health[1]),
                "avg_spread_count": float(health[3]),
                "avg_total_count": float(health[4]),
            }

        ranked = self.rank_providers(provider_stats)
        return {"status": "computed", "rankings": ranked}

    def _compute_value_score(
        self,
        value_bets: float,
        avg_edge: float,
        avg_clv: float,
        duration: float,
        failure_rate: float = 0.0,
    ) -> float:
        if duration <= 0:
            return 0.0
        clv_weight = max(avg_clv, 0.1)
        raw_score = value_bets * clv_weight / duration
        reliability = 1.0 - failure_rate
        return round(raw_score * reliability, 6)

    def rank_providers(self, provider_stats: dict) -> list[dict]:
        scored = []
        for pid, stats in provider_stats.items():
            score = self._compute_value_score(
                stats["value_bets"],
                stats["avg_edge"],
                stats["avg_clv"],
                stats["duration"],
                stats.get("failure_rate", 0),
            )
            scored.append(
                {
                    "provider_id": pid,
                    "value_score": score,
                    "value_bets_per_run": stats["value_bets"],
                    "avg_edge": stats["avg_edge"],
                    "avg_clv": stats["avg_clv"],
                    "avg_duration": stats["duration"],
                    "is_browser": stats["is_browser"],
                    "failure_rate": stats.get("failure_rate", 0),
                    "cb_trips_last_10": stats.get("cb_trips_last_10", 0),
                    "avg_spread_count": stats.get("avg_spread_count", 0),
                    "avg_total_count": stats.get("avg_total_count", 0),
                    "suggest_deactivate": stats["value_bets"] == 0 and stats["is_browser"],
                }
            )
        scored.sort(key=lambda x: x["value_score"], reverse=True)
        return scored
