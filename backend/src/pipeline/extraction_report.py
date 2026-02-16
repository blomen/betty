"""
Extraction Report Generator

Produces a structured, human-readable summary of extraction runs
for diagnosing provider performance and identifying optimization areas.

Includes Pinnacle delta analysis: event coverage, market coverage,
and per-sport gaps vs the sharp baseline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..constants import SHARP_PROVIDERS

if TYPE_CHECKING:
    from .metrics import PipelineMetrics


class ExtractionReport:
    """Generates structured extraction run summaries."""

    LOW_MATCH_RATE = 0.50
    LOW_ODDS_RATIO = 2.0

    def generate(
        self,
        results: dict,
        metrics: PipelineMetrics | None,
        duration: float,
        db_session=None,
    ) -> str:
        lines: list[str] = []
        sep = "=" * 90
        thin_sep = "-" * 90

        # ── Header ──────────────────────────────────────────────────
        lines.append("")
        lines.append(sep)
        lines.append("                           EXTRACTION REPORT")
        lines.append(sep)

        total_events = results.get("total_events", 0)
        total_odds = results.get("total_odds", 0)
        matched_events = results.get("matched_events", 0)
        match_pct = (matched_events / total_events * 100) if total_events > 0 else 0

        analysis = results.get("analysis", {})
        value_bets = analysis.get("value", {}).get("found", 0)
        dutch_bets = analysis.get("dutch", {}).get("found", 0)
        reverse_bets = analysis.get("reverse", {}).get("found", 0)

        providers_data = results.get("providers", {})
        succeeded = sum(1 for p in providers_data.values() if not p.get("error"))
        total_providers = len(providers_data)

        lines.append(f"Duration: {duration:.1f}s | Providers: {succeeded}/{total_providers} OK")
        lines.append(f"Events: {total_events:,} | Odds: {total_odds:,} | Matched: {matched_events:,} ({match_pct:.1f}%)")
        if value_bets > 0 or dutch_bets > 0 or reverse_bets > 0:
            parts = []
            if value_bets > 0:
                parts.append(f"{value_bets} value")
            if dutch_bets > 0:
                parts.append(f"{dutch_bets} dutch")
            if reverse_bets > 0:
                parts.append(f"{reverse_bets} reverse")
            lines.append(f"Opportunities: {', '.join(parts)}")
        lines.append("")

        # ── Provider performance table ──────────────────────────────
        provider_rows = self._build_provider_rows(results, metrics)
        provider_rows.sort(key=lambda r: (not r["is_sharp"], -r["events"]))

        lines.append("PROVIDER PERFORMANCE")
        lines.append(thin_sep)
        lines.append(f"{'Provider':<16} {'Ev':>5} {'Odds':>6} {'1x2':>5} {'Spr':>5} {'Tot':>5}  {'ev/s':>5} {'Time':>6}  {'Status'}")
        lines.append(thin_sep)

        total_events_sum = 0
        total_odds_sum = 0

        for row in provider_rows:
            total_events_sum += row["events"]
            total_odds_sum += row["odds"]

            mc = row.get("market_counts", {})
            ml_count = mc.get("1x2", 0) + mc.get("moneyline", 0)
            spr_count = mc.get("spread", 0)
            tot_count = mc.get("total", 0)
            ml_str = f'{ml_count:>5}' if ml_count > 0 else '    -'
            spr_str = f'{spr_count:>5}' if spr_count > 0 else '    -'
            tot_str = f'{tot_count:>5}' if tot_count > 0 else '    -'

            time_str = f'{row["duration"]:.0f}s' if row["duration"] > 0 else "  -"
            evps = row["events"] / row["duration"] if row["duration"] > 0 else 0
            evps_str = f'{evps:>5.1f}' if evps > 0 else '    -'

            status = row["status"]
            if row["is_sharp"]:
                status += " [SHARP]"

            line = f"{row['provider']:<16} {row['events']:>5,} {row['odds']:>6,} {ml_str} {spr_str} {tot_str}  {evps_str} {time_str:>6}  {status}"
            lines.append(line)

        lines.append(thin_sep)
        lines.append(f"{'Totals (' + str(succeeded) + '/' + str(total_providers) + ')':<16} {total_events_sum:>5,} {total_odds_sum:>6,}")
        lines.append("")

        # ── Pinnacle delta analysis ─────────────────────────────────
        if db_session:
            delta_lines = self._build_pinnacle_delta(db_session, provider_rows)
            if delta_lines:
                lines.extend(delta_lines)

        # ── Issues ──────────────────────────────────────────────────
        issues = self._detect_issues(provider_rows, results, metrics)
        if issues:
            lines.append("ISSUES")
            lines.append(thin_sep)
            for issue in issues:
                lines.append(issue)
            lines.append("")

        lines.append(sep)
        return "\n".join(lines)

    # ── Pinnacle delta ──────────────────────────────────────────────

    def _build_pinnacle_delta(self, session, provider_rows: list[dict]) -> list[str]:
        """Build Pinnacle coverage delta: event and market gaps per provider."""
        try:
            from sqlalchemy import func, distinct
            from src.db.models import Odds, Event
        except Exception:
            return []

        lines: list[str] = []
        thin_sep = "-" * 90

        # Get Pinnacle baseline per sport
        pin_sport_data = {}
        pin_rows = (
            session.query(
                Event.sport,
                func.count(distinct(Odds.event_id)),
            )
            .join(Event, Odds.event_id == Event.id)
            .filter(Odds.provider_id == "pinnacle")
            .group_by(Event.sport)
            .all()
        )
        for sport, cnt in pin_rows:
            pin_sport_data[sport] = {"events": cnt}

        # Get Pinnacle market counts per sport
        pin_market_rows = (
            session.query(
                Event.sport,
                Odds.market,
                func.count(distinct(Odds.event_id)),
            )
            .join(Event, Odds.event_id == Event.id)
            .filter(Odds.provider_id == "pinnacle")
            .group_by(Event.sport, Odds.market)
            .all()
        )
        for sport, market, cnt in pin_market_rows:
            if sport in pin_sport_data:
                mtype = "ml" if market in ("1x2", "moneyline") else market
                pin_sport_data[sport][mtype] = cnt

        pin_event_ids = set(
            r[0] for r in session.query(distinct(Odds.event_id))
            .filter(Odds.provider_id == "pinnacle").all()
        )
        pin_total = len(pin_event_ids)

        if pin_total == 0:
            return []

        # ── Event + Market coverage per provider ────────────────────
        soft_providers = [r["provider"] for r in provider_rows if not r["is_sharp"]]

        lines.append("PINNACLE COVERAGE DELTA")
        lines.append(thin_sep)
        lines.append(f"Pinnacle baseline: {pin_total} events")
        lines.append("")
        lines.append(f"{'Provider':<16} {'PinEv':>5} {'Cov%':>5} {'Miss':>5} | {'ML%':>5} {'Spr%':>5} {'Tot%':>5} | {'P_Spr':>5} {'S_Spr':>5} {'P_Tot':>5} {'S_Tot':>5}")
        lines.append(thin_sep)

        for pid in soft_providers:
            # Events this provider has that overlap with Pinnacle
            prov_pin_events = set(
                r[0] for r in session.query(distinct(Odds.event_id))
                .filter(Odds.provider_id == pid, Odds.event_id.in_(pin_event_ids))
                .all()
            )
            overlap = len(prov_pin_events)
            missing = pin_total - overlap
            cov_pct = 100 * overlap / pin_total

            if overlap == 0:
                lines.append(f"  {pid:<14} {overlap:>5} {cov_pct:>4.0f}% {missing:>5} |     -     -     - |     -     -     -     -")
                continue

            shared_ids = list(prov_pin_events)

            # Pinnacle market counts on shared events
            pin_ml = session.query(func.count(distinct(Odds.event_id))).filter(
                Odds.provider_id == "pinnacle", Odds.market.in_(["1x2", "moneyline"]),
                Odds.event_id.in_(shared_ids)
            ).scalar() or 0
            pin_spr = session.query(func.count(distinct(Odds.event_id))).filter(
                Odds.provider_id == "pinnacle", Odds.market == "spread",
                Odds.event_id.in_(shared_ids)
            ).scalar() or 0
            pin_tot = session.query(func.count(distinct(Odds.event_id))).filter(
                Odds.provider_id == "pinnacle", Odds.market == "total",
                Odds.event_id.in_(shared_ids)
            ).scalar() or 0

            # Provider market counts on shared events
            s_ml = session.query(func.count(distinct(Odds.event_id))).filter(
                Odds.provider_id == pid, Odds.market.in_(["1x2", "moneyline"]),
                Odds.event_id.in_(shared_ids)
            ).scalar() or 0
            s_spr = session.query(func.count(distinct(Odds.event_id))).filter(
                Odds.provider_id == pid, Odds.market == "spread",
                Odds.event_id.in_(shared_ids)
            ).scalar() or 0
            s_tot = session.query(func.count(distinct(Odds.event_id))).filter(
                Odds.provider_id == pid, Odds.market == "total",
                Odds.event_id.in_(shared_ids)
            ).scalar() or 0

            ml_pct = 100 * s_ml / pin_ml if pin_ml > 0 else 0
            spr_pct = 100 * s_spr / pin_spr if pin_spr > 0 else 0
            tot_pct = 100 * s_tot / pin_tot if pin_tot > 0 else 0

            lines.append(
                f"  {pid:<14} {overlap:>5} {cov_pct:>4.0f}% {missing:>5} "
                f"| {ml_pct:>4.0f}% {spr_pct:>4.0f}% {tot_pct:>4.0f}% "
                f"| {pin_spr:>5} {s_spr:>5} {pin_tot:>5} {s_tot:>5}"
            )

        lines.append("")

        # ── Per-sport coverage (top sports only) ────────────────────
        sorted_sports = sorted(pin_sport_data.items(), key=lambda x: -x[1]["events"])
        top_sports = [s for s, d in sorted_sports if d["events"] >= 4][:8]

        if top_sports:
            lines.append("PER-SPORT COVERAGE (events matched / pinnacle events)")
            lines.append(thin_sep)

            # Build header with provider names (abbreviated)
            # Pick representative providers per platform group
            platform_reps = self._get_platform_reps(soft_providers)

            header = f"{'Sport':<14} {'Pin':>4}"
            for label, _ in platform_reps:
                header += f" {label:>8}"
            lines.append(header)
            lines.append(thin_sep)

            for sport in top_sports:
                pin_cnt = pin_sport_data[sport]["events"]
                line = f"{sport:<14} {pin_cnt:>4}"

                for label, pid in platform_reps:
                    cnt = session.query(func.count(distinct(Odds.event_id))).filter(
                        Odds.provider_id == pid,
                        Odds.event_id.in_(
                            session.query(Odds.event_id).join(Event, Odds.event_id == Event.id)
                            .filter(Odds.provider_id == "pinnacle", Event.sport == sport)
                        )
                    ).scalar() or 0
                    pct = 100 * cnt / pin_cnt if pin_cnt > 0 else 0
                    if cnt == 0:
                        line += f" {'   -':>8}"
                    else:
                        line += f" {cnt:>3}({pct:>2.0f}%)"
                line += f"  spr={pin_sport_data[sport].get('spread', 0)} tot={pin_sport_data[sport].get('total', 0)}"
                lines.append(line)

            lines.append("")

        return lines

    def _get_platform_reps(self, soft_providers: list[str]) -> list[tuple[str, str]]:
        """Pick one representative provider per platform for the sport table."""
        # Map platform → preferred provider (pick highest-coverage one if present)
        platform_map = {
            "kambi": ["unibet", "1x2", "leovegas", "expekt", "betmgm", "speedybet", "x3000", "goldenbull"],
            "altenar": ["lodur", "betinia", "campobet", "swiper", "dbet", "quickcasino"],
            "gecko": ["betsson", "nordicbet", "bethard", "spelklubben"],
            "spectate": ["mrgreen", "888sport"],
            "comeon": ["comeon", "hajper", "lyllo"],
            "vbet": ["vbet"],
            "tipwin": ["tipwin"],
            "iwetten": ["interwetten"],
            "10bet": ["10bet"],
            "coolbet": ["coolbet"],
            "snabbare": ["snabbare"],
        }
        reps = []
        used = set()
        for label, candidates in platform_map.items():
            for c in candidates:
                if c in soft_providers and c not in used:
                    reps.append((label, c))
                    used.add(c)
                    break
        return reps

    # ── Provider rows ───────────────────────────────────────────────

    def _build_provider_rows(self, results: dict, metrics: PipelineMetrics | None) -> list[dict]:
        """Build provider row data from results and metrics."""
        rows = []
        providers_data = results.get("providers", {})

        for pid, pdata in providers_data.items():
            is_sharp = pid in SHARP_PROVIDERS
            events = pdata.get("events_processed", 0)
            odds = pdata.get("odds_processed", 0)
            ratio = odds / events if events > 0 else 0
            events_matched = pdata.get("events_matched", 0)
            events_unmatched = pdata.get("events_unmatched", 0)

            match_total = events_matched + events_unmatched
            match_rate = events_matched / match_total if match_total > 0 else None

            duration = 0.0
            rate_limit_hits = 0
            retries = 0
            if metrics and pid in metrics.providers:
                pm = metrics.providers[pid]
                duration = pm.duration_seconds
                rate_limit_hits = pm.rate_limit_hits
                retries = pm.retries

            market_counts = pdata.get("market_counts", {})

            error = pdata.get("error")
            sport_errors = pdata.get("sport_errors", [])
            if error:
                status = f"FAILED: {self._truncate(str(error), 30)}"
            elif sport_errors:
                status = f"OK ({len(sport_errors)} sport errors)"
            else:
                status = "OK"

            rows.append({
                "provider": pid,
                "is_sharp": is_sharp,
                "events": events,
                "odds": odds,
                "ratio": ratio,
                "match_rate": match_rate,
                "duration": duration,
                "status": status,
                "error": error,
                "sport_errors": sport_errors,
                "rate_limit_hits": rate_limit_hits,
                "retries": retries,
                "market_counts": market_counts,
            })

        return rows

    # ── Issues ──────────────────────────────────────────────────────

    def _detect_issues(
        self,
        provider_rows: list[dict],
        results: dict,
        metrics: PipelineMetrics | None,
    ) -> list[str]:
        """Detect actionable issues from extraction results."""
        issues = []

        for row in provider_rows:
            pid = row["provider"]

            if row["error"]:
                issues.append(f"! {pid}: {row['status']}")
                continue

            if not row["is_sharp"] and row["match_rate"] is not None:
                if row["match_rate"] < self.LOW_MATCH_RATE:
                    pct = row["match_rate"] * 100
                    issues.append(f"! {pid}: {pct:.1f}% match rate (coverage gap or name mismatch)")

            if row["events"] > 0 and row["ratio"] < self.LOW_ODDS_RATIO and not row["is_sharp"]:
                mc = row.get("market_counts", {})
                ml = mc.get("1x2", 0) + mc.get("moneyline", 0)
                spr = mc.get("spread", 0)
                tot = mc.get("total", 0)
                missing = []
                if spr == 0:
                    missing.append("spread")
                if tot == 0:
                    missing.append("total")
                if missing:
                    issues.append(f"~ {pid}: ratio {row['ratio']:.2f} — missing {', '.join(missing)} markets (1x2={ml}, spread={spr}, total={tot})")
                else:
                    issues.append(f"~ {pid}: ratio {row['ratio']:.2f} — low odds count (1x2={ml}, spread={spr}, total={tot})")

            if row["rate_limit_hits"] > 0:
                issues.append(f"~ {pid}: {row['rate_limit_hits']} rate limit hits (429)")

            for se in row.get("sport_errors", []):
                sport = se.get("sport", "?")
                error_msg = se.get("error", se.get("error_type", "Error"))
                issues.append(f"~ {pid}/{sport}: {self._truncate(error_msg, 50)}")

        return issues

    @staticmethod
    def _truncate(s: str, max_len: int) -> str:
        if len(s) <= max_len:
            return s
        return s[:max_len - 3] + "..."
