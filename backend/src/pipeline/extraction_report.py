"""
Extraction Report Generator

Produces a structured, human-readable summary of extraction runs
for diagnosing provider performance and identifying optimization areas.

Includes Pinnacle delta analysis: event coverage, market coverage,
and per-sport gaps vs the sharp baseline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..constants import SHARP_PROVIDERS, PROVIDER_CANONICAL, PLATFORM_GROUPS

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
        value_data = analysis.get("value", {})
        dutch_data = analysis.get("dutch", {})
        value_bets = value_data.get("found", 0)
        dutch_bets = dutch_data.get("found", 0)
        reverse_bets = analysis.get("reverse", {}).get("found", 0)
        # "found" = unique canonical opportunities; "fanned" = extra alias copies
        value_total = value_bets + value_data.get("fanned", 0)
        dutch_total = dutch_bets + dutch_data.get("fanned", 0)

        providers_data = results.get("providers", {})
        succeeded = sum(1 for p in providers_data.values() if not p.get("error"))
        total_providers = len(providers_data)

        lines.append(f"Duration: {duration:.1f}s | Providers: {succeeded}/{total_providers} OK")
        lines.append(f"Events: {total_events:,} | Odds: {total_odds:,} | Matched: {matched_events:,} ({match_pct:.1f}%)")
        if value_bets > 0 or dutch_bets > 0 or reverse_bets > 0:
            parts = []
            if value_bets > 0:
                s = f"{value_bets} value"
                if value_total > value_bets:
                    s += f" ({value_total} incl. aliases)"
                parts.append(s)
            if dutch_bets > 0:
                s = f"{dutch_bets} dutch"
                if dutch_total > dutch_bets:
                    s += f" ({dutch_total} incl. aliases)"
                parts.append(s)
            if reverse_bets > 0:
                parts.append(f"{reverse_bets} reverse")
            lines.append(f"Opportunities: {', '.join(parts)}")
        lines.append("")

        # ── Run history trend ──────────────────────────────────────
        if db_session:
            trend_lines = self._build_run_trend(db_session, results)
            if trend_lines:
                lines.extend(trend_lines)

        # ── Provider performance table ──────────────────────────────
        provider_rows = self._build_provider_rows(results, metrics)
        provider_rows.sort(key=lambda r: (not r["is_sharp"], -r["events"]))

        # Get previous run's provider data for delta indicators
        prev_provider = self._get_previous_provider_data(db_session, results) if db_session else {}

        lines.append("PROVIDER PERFORMANCE")
        lines.append(thin_sep)
        lines.append(f"{'Provider':<16} {'Ev':>5} {'dEv':>4} {'Odds':>6} {'1x2':>5} {'Spr':>5} {'Tot':>5} {'Match':>6} {'Time':>6} {'dT':>4}  {'Status'}")
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

            # Match rate column
            if row["match_rate"] is not None:
                match_str = f'{row["match_rate"] * 100:>4.0f}%'
            else:
                match_str = '    -'

            time_str = f'{row["duration"]:.0f}s' if row["duration"] > 0 else "  -"

            # Delta indicators vs previous run
            pid = row["provider"]
            ev_delta_str = "   "
            time_delta_str = "   "
            if pid in prev_provider:
                prev = prev_provider[pid]
                prev_ev = prev.get("events", 0)
                prev_dur = prev.get("duration", 0)
                if prev_ev > 0 and row["events"] > 0:
                    ev_diff = row["events"] - prev_ev
                    if ev_diff > 0:
                        ev_delta_str = f"+{min(ev_diff, 999):>3}"
                    elif ev_diff < 0:
                        ev_delta_str = f"{max(ev_diff, -999):>4}"
                    else:
                        ev_delta_str = "  ="
                if prev_dur > 0 and row["duration"] > 0:
                    dur_diff = row["duration"] - prev_dur
                    if abs(dur_diff) < 5:
                        time_delta_str = "  ="
                    elif dur_diff > 0:
                        time_delta_str = f"+{min(int(dur_diff), 999):>3}"
                    else:
                        time_delta_str = f"{max(int(dur_diff), -999):>4}"

            status = row["status"]
            if row["is_sharp"]:
                status += " [SHARP]"

            line = f"{row['provider']:<16} {row['events']:>5,} {ev_delta_str} {row['odds']:>6,} {ml_str} {spr_str} {tot_str} {match_str:>6} {time_str:>6} {time_delta_str}  {status}"
            lines.append(line)

        lines.append(thin_sep)
        lines.append(f"{'Totals (' + str(succeeded) + '/' + str(total_providers) + ')':<16} {total_events_sum:>5,}      {total_odds_sum:>6,}")
        lines.append("")

        # ── Timing budget ─────────────────────────────────────────
        timed_rows = [r for r in provider_rows if r["duration"] > 0 and not r["status"].startswith("= ")]
        if timed_rows and duration > 0:
            timed_rows.sort(key=lambda r: -r["duration"])
            lines.append("TIMING BUDGET (wall-clock share of bottleneck)")
            lines.append(thin_sep)
            for row in timed_rows[:8]:
                pct = row["duration"] / duration * 100
                bar_len = int(pct / 2.5)  # max ~40 chars for 100%
                bar = "#" * bar_len
                ev_rate = row["events"] / row["duration"] if row["duration"] > 0 else 0
                lines.append(
                    f"  {row['provider']:<14} {row['duration']:>5.0f}s ({pct:>4.0f}%) "
                    f"{bar:<30} {ev_rate:>5.1f} ev/s"
                )
            lines.append("")

        # ── Pinnacle delta analysis ─────────────────────────────────
        if db_session:
            delta_lines = self._build_pinnacle_delta(db_session, provider_rows)
            if delta_lines:
                lines.extend(delta_lines)

        # ── Boost scraper health ──────────────────────────────────
        if db_session:
            boost_lines = self._build_boost_health(db_session)
            if boost_lines:
                lines.extend(boost_lines)

        # ── Issues ──────────────────────────────────────────────────
        issues = self._detect_issues(provider_rows, results, metrics, db_session)
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
        """Pick one representative provider per platform for the sport table.

        Uses PLATFORM_GROUPS canonicals first, then standalone providers.
        After consolidation, soft_providers only contains canonical providers.
        """
        # Build platform → canonical from PLATFORM_GROUPS
        platform_map = {}
        for group_name, group_data in PLATFORM_GROUPS.items():
            canonical = group_data["canonical"]
            # Use short group name for display
            label = group_name.split("_")[0]  # kambi, spectate, altenar, gecko
            if label not in platform_map:
                platform_map[label] = []
            platform_map[label].append(canonical)

        # Add standalone platforms (not in PLATFORM_GROUPS)
        standalone = {
            "vbet": ["vbet"],
            "tipwin": ["tipwin"],
            "iwetten": ["interwetten"],
            "10bet": ["10bet"],
            "coolbet": ["coolbet"],
            "snabbare": ["snabbare"],
        }
        platform_map.update(standalone)

        reps = []
        used = set()
        for label, candidates in platform_map.items():
            for c in candidates:
                if c in soft_providers and c not in used:
                    reps.append((label, c))
                    used.add(c)
                    break
        return reps

    # ── Boost scraper health ─────────────────────────────────────────

    def _build_boost_health(self, session) -> list[str]:
        """Build boost scraper health section from boost_extraction_logs."""
        try:
            from ..db.models import BoostExtractionLog, SpecialOdds
        except Exception:
            return []

        lines: list[str] = []
        thin_sep = "-" * 90

        # Get latest run's rows (all share same run_id)
        latest = (
            session.query(BoostExtractionLog)
            .order_by(BoostExtractionLog.scraped_at.desc())
            .first()
        )
        if not latest:
            return []

        run_id = latest.run_id
        rows = (
            session.query(BoostExtractionLog)
            .filter(BoostExtractionLog.run_id == run_id)
            .order_by(BoostExtractionLog.boosts_found.desc())
            .all()
        )
        if not rows:
            return []

        # Get current specials stats from DB
        specials_count = session.query(SpecialOdds).count()
        ev_count = session.query(SpecialOdds).filter(SpecialOdds.is_positive_ev == True).count()

        lines.append("BOOST SCRAPER HEALTH")
        lines.append(thin_sep)

        # Run-level summary
        run_total = latest.run_total_boosts or 0
        run_dur = latest.run_duration_seconds or 0
        scraped_at = latest.scraped_at.strftime("%Y-%m-%d %H:%M:%S") if latest.scraped_at else "?"
        lines.append(
            f"Last run: {scraped_at} | {run_total} boosts scraped in {run_dur:.0f}s "
            f"| DB: {specials_count} active ({ev_count} +EV)"
        )
        lines.append("")

        # Per-provider table
        lines.append(f"{'Provider':<20} {'Type':<10} {'Status':<8} {'Boosts':>6} {'Time':>6}  {'Error'}")
        lines.append(thin_sep)

        total_boosts = 0
        failed_providers = []
        zero_boost_providers = []

        for r in rows:
            pid = r.provider_id or "?"
            stype = r.scraper_type or "-"
            status = (r.status or "?").upper()
            boosts = r.boosts_found or 0
            dur = r.duration_seconds or 0
            err = ""
            if r.error_message:
                err = self._truncate(r.error_message, 40)

            total_boosts += boosts

            if status == "FAILED":
                status_str = "FAIL"
                failed_providers.append(pid)
            elif boosts == 0 and status != "SKIPPED":
                status_str = "0 !"
                zero_boost_providers.append(pid)
            else:
                status_str = "OK" if status == "SUCCESS" else status[:8]

            lines.append(
                f"{pid:<20} {stype:<10} {status_str:<8} {boosts:>6} {dur:>5.0f}s  {err}"
            )

        lines.append(thin_sep)
        lines.append(f"Total: {total_boosts} boosts from {len(rows)} providers")

        # Flag issues
        boost_issues = []
        if failed_providers:
            boost_issues.append(f"! Failed scrapers: {', '.join(failed_providers)}")
        if zero_boost_providers:
            boost_issues.append(f"~ 0 boosts from: {', '.join(zero_boost_providers)}")
        slow = [r for r in rows if (r.duration_seconds or 0) > 60]
        if slow:
            boost_issues.append(f"~ Slow scrapers (>60s): {', '.join(r.provider_id for r in slow)}")
        if specials_count == 0 and run_total > 0:
            boost_issues.append("! Boosts scraped but DB has 0 specials — store_specials_to_db() may have failed")

        if boost_issues:
            lines.append("")
            for issue in boost_issues:
                lines.append(issue)

        lines.append("")
        return lines

    # ── Provider rows ───────────────────────────────────────────────

    def _build_provider_rows(self, results: dict, metrics: PipelineMetrics | None) -> list[dict]:
        """Build provider row data from results and metrics."""
        rows = []
        providers_data = results.get("providers", {})

        # Track which providers were consolidated (skipped because canonical was extracted)
        consolidated_providers = results.get("consolidated_providers", {})

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

        # Add rows for consolidated (skipped) providers
        for pid, canonical in consolidated_providers.items():
            rows.append({
                "provider": pid,
                "is_sharp": False,
                "events": 0,
                "odds": 0,
                "ratio": 0,
                "match_rate": None,
                "duration": 0,
                "status": f"= {canonical}",
                "error": None,
                "sport_errors": [],
                "rate_limit_hits": 0,
                "retries": 0,
                "market_counts": {},
            })

        return rows

    # ── Issues ──────────────────────────────────────────────────────

    SLOW_PROVIDER_THRESHOLD = 300  # > 5 min is suspicious
    LOW_EVENT_THRESHOLD = 10       # < 10 events probably broken

    EVENT_DROP_THRESHOLD = 0.30  # Flag if events drop to <30% of recent average

    def _detect_issues(
        self,
        provider_rows: list[dict],
        results: dict,
        metrics: PipelineMetrics | None,
        db_session=None,
    ) -> list[str]:
        """Detect actionable issues from extraction results."""
        issues = []

        # Build historical baseline for event drop detection
        prev_avg = self._get_previous_event_averages(db_session, results) if db_session else {}

        for row in provider_rows:
            pid = row["provider"]

            # Skip consolidated alias members — they intentionally have 0 events
            if row["status"].startswith("= "):
                continue

            if row["error"]:
                issues.append(f"! {pid}: {row['status']}")
                continue

            # Zero events — provider probably broken
            if row["events"] == 0 and not row["is_sharp"]:
                issues.append(f"! {pid}: 0 events extracted (broken extractor or site change?)")
                continue

            # Event count drop vs recent average
            if pid in prev_avg and prev_avg[pid] > 0 and not row["is_sharp"]:
                ratio = row["events"] / prev_avg[pid]
                if ratio < self.EVENT_DROP_THRESHOLD:
                    issues.append(
                        f"! {pid}: {row['events']} events vs avg {prev_avg[pid]:.0f} "
                        f"({ratio:.0%} of normal — possible silent failure)"
                    )

            # Very few events
            if row["events"] < self.LOW_EVENT_THRESHOLD and not row["is_sharp"]:
                issues.append(f"! {pid}: only {row['events']} events (expected hundreds)")

            # Low Pinnacle match rate
            if not row["is_sharp"] and row["match_rate"] is not None:
                if row["match_rate"] < self.LOW_MATCH_RATE:
                    pct = row["match_rate"] * 100
                    issues.append(f"! {pid}: {pct:.1f}% match rate (fuzzy matching failing or sport name mismatch)")

            # Missing market types (spread/total gaps)
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
                    issues.append(f"~ {pid}: ratio {row['ratio']:.2f} — missing {', '.join(missing)} markets (1x2={ml}, spr={spr}, tot={tot})")
                else:
                    issues.append(f"~ {pid}: ratio {row['ratio']:.2f} — low odds count (1x2={ml}, spr={spr}, tot={tot})")

            # Slow extraction (> 5 min)
            if row["duration"] > self.SLOW_PROVIDER_THRESHOLD and not row["is_sharp"]:
                issues.append(f"~ {pid}: {row['duration']:.0f}s extraction (>{self.SLOW_PROVIDER_THRESHOLD}s threshold)")

            # Rate limit hits
            if row["rate_limit_hits"] > 0:
                issues.append(f"~ {pid}: {row['rate_limit_hits']} rate limit hits (429)")

            for se in row.get("sport_errors", []):
                sport = se.get("sport", "?")
                error_msg = se.get("error", se.get("error_type", "Error"))
                issues.append(f"~ {pid}/{sport}: {self._truncate(error_msg, 50)}")

        return issues

    def _get_previous_event_averages(self, session, results: dict) -> dict[str, float]:
        """Get average event counts per provider from recent runs of the same trigger."""
        try:
            from ..db.models import ExtractionRun, ProviderRunMetrics
            from sqlalchemy import func
        except Exception:
            return {}

        trigger = results.get("trigger")
        if not trigger:
            return {}

        # Get the 5 most recent completed runs of this trigger type (excluding current)
        recent_runs = (
            session.query(ExtractionRun.id)
            .filter(ExtractionRun.trigger == trigger, ExtractionRun.duration_seconds.isnot(None))
            .order_by(ExtractionRun.start_time.desc())
            .limit(6)
            .all()
        )
        # Skip the first one (current run) if it's already persisted
        run_ids = [r[0] for r in recent_runs]
        current_id = results.get("run_id")
        if current_id in run_ids:
            run_ids.remove(current_id)
        run_ids = run_ids[:5]

        if not run_ids:
            return {}

        rows = (
            session.query(
                ProviderRunMetrics.provider_id,
                func.avg(ProviderRunMetrics.events_processed),
            )
            .filter(
                ProviderRunMetrics.run_id.in_(run_ids),
                ProviderRunMetrics.events_processed > 0,
            )
            .group_by(ProviderRunMetrics.provider_id)
            .all()
        )
        return {pid: avg for pid, avg in rows}

    # ── Run trend ──────────────────────────────────────────────────

    def _build_run_trend(self, session, results: dict) -> list[str]:
        """Build a compact table showing the last 5 runs of this trigger type for trend analysis."""
        try:
            from ..db.models import ExtractionRun, ProviderRunMetrics
            from sqlalchemy import func
        except Exception:
            return []

        trigger = results.get("trigger")
        if not trigger:
            return []

        runs = (
            session.query(ExtractionRun)
            .filter(ExtractionRun.trigger == trigger, ExtractionRun.duration_seconds.isnot(None))
            .order_by(ExtractionRun.start_time.desc())
            .limit(6)
            .all()
        )

        # Filter out current run if present
        current_id = results.get("run_id")
        runs = [r for r in runs if r.id != current_id][:5]

        if len(runs) < 2:
            return []

        lines: list[str] = []
        thin_sep = "-" * 90

        lines.append("RUN HISTORY (last 5)")
        lines.append(thin_sep)
        lines.append(f"{'Time':>16} {'Dur':>6} {'Events':>7} {'Odds':>8} {'Match':>6} {'OK/F':>5} {'Value':>6} {'Dutch':>6}")
        lines.append(thin_sep)

        for r in runs:
            time_str = r.start_time.strftime("%m-%d %H:%M") if r.start_time else "?"
            dur = r.duration_seconds or 0
            evts = r.total_events or 0
            odds = r.total_odds or 0

            # Extract match info from report text (quick parse)
            match_str = "    -"
            opp_value = "     -"
            opp_dutch = "     -"
            if r.report:
                import re
                m = re.search(r'Matched:\s*[\d,]+\s*\((\d+\.\d+)%\)', r.report)
                if m:
                    match_str = f"{float(m.group(1)):>4.0f}%"
                m = re.search(r'(\d+)\s*value', r.report)
                if m:
                    opp_value = f"{int(m.group(1)):>6}"
                m = re.search(r'(\d+)\s*dutch', r.report)
                if m:
                    opp_dutch = f"{int(m.group(1)):>6}"

            ok = r.providers_succeeded or 0
            fail = r.providers_failed or 0

            lines.append(f"{time_str:>16} {dur:>5.0f}s {evts:>7,} {odds:>8,} {match_str} {ok:>2}/{fail:<2} {opp_value} {opp_dutch}")

        # Trend summary
        first, last = runs[-1], runs[0]
        ev_first = first.total_events or 0
        ev_last = last.total_events or 0
        dur_first = first.duration_seconds or 0
        dur_last = last.duration_seconds or 0

        if ev_first > 0 and ev_last > 0:
            ev_change = ((ev_last - ev_first) / ev_first) * 100
            dur_change = dur_last - dur_first
            ev_arrow = "+" if ev_change > 0 else ""
            dur_arrow = "+" if dur_change > 0 else ""
            lines.append(thin_sep)
            lines.append(
                f"Trend (oldest->newest): events {ev_arrow}{ev_change:.0f}%, "
                f"duration {dur_arrow}{dur_change:.0f}s"
            )

        lines.append("")
        return lines

    def _get_previous_provider_data(self, session, results: dict) -> dict[str, dict]:
        """Get provider event counts and durations from the previous run of the same trigger."""
        try:
            from ..db.models import ExtractionRun, ProviderRunMetrics
        except Exception:
            return {}

        trigger = results.get("trigger")
        if not trigger:
            return {}

        runs = (
            session.query(ExtractionRun.id)
            .filter(ExtractionRun.trigger == trigger, ExtractionRun.duration_seconds.isnot(None))
            .order_by(ExtractionRun.start_time.desc())
            .limit(3)
            .all()
        )

        current_id = results.get("run_id")
        run_ids = [r[0] for r in runs if r[0] != current_id]
        if not run_ids:
            return {}

        prev_run_id = run_ids[0]
        rows = (
            session.query(ProviderRunMetrics)
            .filter(ProviderRunMetrics.run_id == prev_run_id)
            .all()
        )
        return {
            r.provider_id: {
                "events": r.events_processed or 0,
                "odds": r.odds_processed or 0,
                "duration": r.duration_seconds or 0,
            }
            for r in rows
        }

    @staticmethod
    def _truncate(s: str, max_len: int) -> str:
        if len(s) <= max_len:
            return s
        return s[:max_len - 3] + "..."
