"""
Opportunity Analyzer

Integrates OpportunityScanner into the extraction pipeline.
Delegates value detection to scanner (which applies all quality gates),
then persists results to the Opportunity table.

Architecture:
    Extraction (orchestrator.py)
        |
        v
    [Database: Events + Odds]
        |
        +---> scanner.find_value_in_market() --> Value bets (de-vigged Pinnacle)
        |
        +---> scanner.scan_bonus() --> Bonus mode (anchor vs counterpart, any edge)
        |
        v
    [Opportunity table] --> UI
"""

import logging
import time

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..analysis.scanner import SET_SPREAD_SPORTS, OpportunityScanner
from ..constants import CANONICAL_MEMBERS, PROVIDER_CANONICAL
from ..db.models import Opportunity, Profile
from ..repositories import OpportunityRepo
from ..services.bet_service import BetService
from ..services.opportunity_service import cleanup_stale_opportunities

logger = logging.getLogger(__name__)


def _parse_market_point(market: str) -> tuple[str, float | None]:
    """Parse market key into (clean_market, point_value). E.g., 'spread_-1.5' -> ('spread', -1.5)."""
    if "_" in market and market.split("_")[-1].replace(".", "").replace("-", "").isdigit():
        parts = market.rsplit("_", 1)
        if len(parts) == 2:
            try:
                return parts[0], float(parts[1])
            except ValueError:
                pass
    return market, None


class OpportunityAnalyzer:
    """
    Analyzes stored odds to detect opportunities.

    Runs after extraction to find:
    - Value: Provider odds exceed fair odds from sharp sources

    Usage:
        analyzer = OpportunityAnalyzer(session)
        results = analyzer.run()
    """

    def __init__(self, session: Session, min_edge_pct: float = None):
        """
        Initialize analyzer.

        Args:
            session: SQLAlchemy session
            min_edge_pct: Minimum value edge % (default from profile or 5.0)
        """
        self.session = session
        self.scanner = OpportunityScanner(session)
        self.opp_repo = OpportunityRepo(session)

        # Get thresholds from active profile or use defaults
        profile = None
        try:
            profile = session.query(Profile).filter(Profile.is_active).first()
        except Exception as e:
            logger.debug(f"[Analyzer] Could not load profile: {e}")

        self.min_edge_pct = (
            min_edge_pct if min_edge_pct is not None else (getattr(profile, "min_edge_pct", 5.0) if profile else 5.0)
        )

    def run(self, changed_event_ids: set[str] | None = None) -> dict:
        """
        Run opportunity detection on all events with 2+ providers.

        Args:
            changed_event_ids: When provided, only rescan events with changed odds
                               (incremental mode). When None, rescan all events (full mode).

        Returns:
            Dictionary with analysis results:
            {
                "value": {"found": int, "new": int},
                "events_analyzed": int,
                "updated_opportunities": list,
                "added_opportunities": list,
                "removed_opportunities": list[tuple[int, str]],
            }
        """
        logger.info("[Analyzer] Starting opportunity detection...")

        # Snapshot closing odds for pending bets on started events
        # (must run BEFORE cleanup deletes past event odds)
        try:
            bet_service = BetService(self.session)
            clv_snapshot = bet_service.snapshot_closing_odds()
        except Exception as e:
            logger.warning(f"[Analyzer] CLV snapshot failed: {e}")
            clv_snapshot = {"processed": 0, "updated": 0}

        # Clean up stale opportunities before detection (incremental deactivation when provided)
        cleanup_stats = self.opp_repo.cleanup_stale(changed_event_ids=changed_event_ids)

        # Expire opportunities with stale odds or past start times
        try:
            stale_cleanup = cleanup_stale_opportunities(self.session)
            logger.info(
                "[opp_cleanup] expired %d post-start, %d stale-odds",
                stale_cleanup["expired_post_start"],
                stale_cleanup["expired_stale_odds"],
            )
        except Exception as e:
            logger.warning(f"[opp_cleanup] failed: {e}")

        # Purge old odds_movements rows so the table doesn't grow unbounded
        # once STEAM_DETECTOR_ENABLED is on. Steam detection only uses the
        # last 5 min — anything past 24h is dead weight. No-op when the
        # feature is off (table stays empty).
        try:
            from ..analysis.steam_detector import is_enabled as steam_enabled
            from ..analysis.steam_detector import purge_old_movements

            if steam_enabled():
                purged = purge_old_movements(self.session, retention_hours=24)
                if purged > 0:
                    logger.info("[steam_cleanup] purged %d old odds_movement rows", purged)
        except Exception as e:
            logger.warning(f"[steam_cleanup] failed: {e}")

        # Pre-load events once — shared across all scan types (value, arb, reverse)
        events = self.scanner.get_multi_provider_events(min_providers=2)

        # Incremental mode: restrict scan to changed events only
        if changed_event_ids is not None:
            events = [e for e in events if e.id in changed_event_ids]

        # Delta tracking: record which opportunity IDs existed before scanning
        # so we can distinguish added vs updated vs removed after scanning.
        pre_scan_ids: set[int] = set()
        if changed_event_ids is not None and events:
            pre_scan_ids = set(
                row[0]
                for row in self.session.query(Opportunity.id).filter(Opportunity.event_id.in_(changed_event_ids)).all()
            )

        results = {
            "value": {"found": 0, "new": 0, "fanned": 0},
            "arb": {"found": 0, "new": 0},
            "reverse": {"found": 0, "new": 0},
            "reverse_value": {"found": 0, "new": 0},
            "events_analyzed": len(events),
            "cleanup": cleanup_stats,
            "clv_snapshot": clv_snapshot,
            "updated_opportunities": [],
            "added_opportunities": [],
            "removed_opportunities": [],
        }

        upserted_opps: list = []  # all opportunity objects returned from upserts

        from ..constants import scannable_scopes_for

        for event in events:
            # Tennis (set-based sports): Pinnacle spread = set handicap,
            # soft providers spread = game handicap → phantom edges. Skip.
            skip_spreads = event.sport in SET_SPREAD_SPORTS

            # Scan each in-scope period independently. For most sports this
            # iterates once over {"ft"} — bit-for-bit identical to the prior
            # single-scope behaviour. Baseball iterates {"ft", "f5"} so MLB F5
            # markets produce their own opportunity rows alongside the full
            # game ones.
            for scope in scannable_scopes_for(event.sport):
                odds_grouped = self.scanner.group_odds(event, scope=scope)

                for market, odds_by_outcome in odds_grouped.items():
                    if skip_spreads and market.startswith("spread"):
                        continue

                    # Detect value via scanner, then persist best per outcome
                    value_count = self._detect_value(
                        event.id, market, odds_by_outcome, odds_grouped, sport=event.sport, scope=scope
                    )
                    results["value"]["found"] += value_count["found"]
                    results["value"]["new"] += value_count["new"]
                    results["value"]["fanned"] += value_count.get("fanned", 0)
                    upserted_opps.extend(value_count.get("opps", []))

                    # Detect arb/reverse (cross-book opportunities)
                    arb_count = self._detect_arb(event, market, odds_by_outcome, odds_grouped, scope=scope)
                    results["arb"]["found"] += arb_count["arb_found"]
                    results["arb"]["new"] += arb_count["arb_new"]
                    results["reverse"]["found"] += arb_count["reverse_found"]
                    results["reverse"]["new"] += arb_count["reverse_new"]
                    upserted_opps.extend(arb_count.get("opps", []))

                    # Detect reverse value (Pinnacle vs soft consensus)
                    rv_count = self._detect_reverse_value(event.id, market, odds_by_outcome, odds_grouped, scope=scope)
                    results["reverse_value"]["found"] += rv_count["found"]
                    results["reverse_value"]["new"] += rv_count["new"]
                    upserted_opps.extend(rv_count.get("opps", []))

        # Commit with deadlock retry — concurrent pipelines can deadlock on
        # opportunity rows when two analyzers update overlapping event sets.
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.session.commit()
                break
            except OperationalError as e:
                if "deadlock" in str(e).lower() and attempt < max_retries - 1:
                    wait = 0.1 * (2**attempt)  # 100ms, 200ms
                    logger.warning(
                        f"[Analyzer] Deadlock on commit (attempt {attempt + 1}), retrying in {wait:.0f}ms..."
                    )
                    self.session.rollback()
                    time.sleep(wait)
                else:
                    raise

        # Build delta lists after commit (IDs are now assigned for new objects)
        if changed_event_ids is not None:
            upserted_ids: set[int] = set()
            for opp in upserted_opps:
                opp_id = getattr(opp, "id", None)
                if opp_id is None:
                    continue
                upserted_ids.add(opp_id)
                if opp_id in pre_scan_ids:
                    results["updated_opportunities"].append(opp)
                else:
                    results["added_opportunities"].append(opp)

            # Opportunities that were deactivated but never re-created
            removed_ids = pre_scan_ids - upserted_ids
            if removed_ids:
                removed_rows = (
                    self.session.query(Opportunity.id, Opportunity.type).filter(Opportunity.id.in_(removed_ids)).all()
                )
                results["removed_opportunities"] = [(row[0], row[1]) for row in removed_rows]

        logger.info(
            f"[Analyzer] Complete: {results['events_analyzed']} events analyzed, "
            f"{results['value']['found']} value bets, "
            f"{results['arb']['found']} arb, "
            f"{results['reverse']['found']} reverse, "
            f"{results['reverse_value']['found']} reverse_value"
        )

        return results

    def run_bonus(
        self,
        anchor_provider: str,
        devig: bool = True,
    ) -> dict:
        """
        Run bonus-specific analysis.

        Finds ALL opportunities for clearing a bonus at the anchor provider,
        compared against Pinnacle as the sole sharp source. No edge threshold -
        returns all matches sorted by edge (best first, including negative).

        Args:
            anchor_provider: Provider where bonus bet must be placed (e.g., "unibet")
            devig: Whether to de-vig Pinnacle odds (default True)

        Returns:
            {
                "opportunities": list[BonusOpportunity],
                "count": int,
                "best_edge": float,
                "worst_edge": float,
                "positive_count": int,  # Number with edge > 0
                "anchor_provider": str,
            }

        Example:
            >>> analyzer = OpportunityAnalyzer(session)
            >>> result = analyzer.run_bonus("unibet")
            >>> for opp in result["opportunities"][:5]:
            ...     print(f"{opp.edge_pct:+.1f}% {opp.event_id} {opp.outcome}")
        """
        # Resolve to canonical for DB query (odds stored under canonical after consolidation)
        # e.g., run_bonus("expekt") → queries DB for "unibet" odds (same platform)
        query_provider = PROVIDER_CANONICAL.get(anchor_provider, anchor_provider)
        if query_provider != anchor_provider:
            logger.info(
                f"[Analyzer] Running bonus scan: anchor={anchor_provider} (querying as {query_provider}, same platform)"
            )
        else:
            logger.info(f"[Analyzer] Running bonus scan: anchor={anchor_provider}")

        opportunities = self.scanner.scan_bonus(
            anchor_provider=query_provider,
            devig=devig,
        )

        result = {
            "opportunities": opportunities,
            "count": len(opportunities),
            "best_edge": max((o.edge_pct for o in opportunities), default=0),
            "worst_edge": min((o.edge_pct for o in opportunities), default=0),
            "positive_count": sum(1 for o in opportunities if o.edge_pct > 0),
            "anchor_provider": anchor_provider,
        }

        logger.info(
            f"[Analyzer] Bonus scan complete: {result['count']} opportunities, "
            f"{result['positive_count']} positive edge, "
            f"best={result['best_edge']:+.1f}%, worst={result['worst_edge']:+.1f}%"
        )

        return result

    def _detect_value(
        self,
        event_id: str,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        all_markets: dict[str, dict[str, list[dict]]] = None,
        sport: str | None = None,
        scope: str = "ft",
    ) -> dict:
        """
        Detect value betting opportunities for a market.

        Delegates to scanner.find_value_in_market() which applies all quality gates:
        - MAX_ODDS_RATIO: rejects likely event mismatches
        - MIN_VALID_PROB_SUM: rejects incomplete soft provider markets
        - Market type mismatch: prevents 3-way vs 2-way comparison
        - Pinnacle market completeness: validates prob_sum before de-vigging

        Keeps only the best value bet per outcome (highest edge) and upserts
        to the Opportunity table.

        Returns:
            {"found": int, "new": int}
        """
        result = {"found": 0, "new": 0, "fanned": 0, "opps": []}

        # Delegate to scanner (all quality gates applied here)
        value_bets = self.scanner.find_value_in_market(
            event_id=event_id,
            market=market,
            odds_by_outcome=odds_by_outcome,
            min_edge_pct=self.min_edge_pct,
            all_markets=all_markets,
        )

        if not value_bets:
            return result

        # Keep best per (outcome, provider) — each provider gets its own opportunity
        best_by_outcome_provider: dict = {}
        for vb in value_bets:
            key = (vb.outcome, vb.provider)
            existing_best = best_by_outcome_provider.get(key)
            if existing_best is None or vb.edge_pct > existing_best.edge_pct:
                best_by_outcome_provider[key] = vb

        for (outcome, _provider), vb in best_by_outcome_provider.items():
            result["found"] += 1

            logger.debug(
                f"[Analyzer] Value found: {event_id} {market} {outcome} "
                f"@ {vb.provider} {vb.provider_odds} (+{vb.edge_pct}% vs pinnacle)"
            )

            # Extract point value from market key if present
            clean_market, point_value = _parse_market_point(market)

            # Diagnostic annotations populated once per value bet — read by
            # the frontend for indicator badges. All three return None when
            # the relevant feature flag is off or the data isn't applicable,
            # so this block is fail-safe.
            from ..analysis.consensus_lean import compute_consensus_lean
            from ..analysis.key_numbers import annotate as annotate_key_number
            from ..analysis.shading import compute_shading
            from ..analysis.steam_detector import lookup_signal_for_outcome

            key_info = annotate_key_number(sport=sport, market=clean_market, point=point_value)

            # Use the scope this value bet was detected at — steam signals are
            # scope-tagged in storage so an F5 value bet must look up F5 steam
            # signals, not ft ones.
            steam_sig = lookup_signal_for_outcome(
                self.session,
                event_id=event_id,
                market=clean_market,
                outcome=outcome,
                point=point_value,
                scope=scope,
            )
            lean_obj = compute_consensus_lean(
                odds_snapshot=vb.odds_snapshot,
                sharp_fair_probability=vb.fair_probability,
                bet_provider=vb.provider,
            )
            lean_dict = lean_obj.to_dict() if lean_obj else None
            shading_obj = compute_shading(
                fair_probability=vb.fair_probability,
                market=clean_market,
                consensus_lean=lean_dict,
            )
            annotations: dict | None = None
            if key_info or steam_sig or lean_obj or shading_obj:
                annotations = {
                    "key_number": key_info.to_dict() if key_info else None,
                    "steam_signal": steam_sig,
                    "consensus_lean": lean_dict,
                    "shading": shading_obj.to_dict() if shading_obj else None,
                }

            # Fan out to all platform members (e.g., unibet → all 8 Kambi brands)
            fan_providers = CANONICAL_MEMBERS.get(vb.provider, [vb.provider])
            result["fanned"] += len(fan_providers) - 1  # track fan-out inflation
            for fan_provider in fan_providers:
                # Build outcomes JSON per fan provider
                outcomes_json = [
                    {"provider": fan_provider, "outcome": outcome, "odds": vb.provider_odds, "edge_pct": vb.edge_pct},
                    {"provider": "pinnacle", "outcome": outcome, "odds": vb.fair_odds, "is_fair_odds": True},
                ]

                # Upsert to Opportunity table via repo
                is_new, opp = self.opp_repo.upsert_value(
                    event_id=event_id,
                    market=clean_market,
                    outcome=outcome,
                    provider_id=fan_provider,
                    provider_odds=vb.provider_odds,
                    fair_odds=vb.fair_odds,
                    edge_pct=vb.edge_pct,
                    outcomes_json=outcomes_json,
                    point=point_value,
                    annotations=annotations,
                    scope=scope,
                )
                result["opps"].append(opp)
                if is_new:
                    result["new"] += 1

        return result

    def _detect_reverse_value(
        self,
        event_id: str,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        all_markets: dict[str, dict[str, list[dict]]] = None,
        scope: str = "ft",
    ) -> dict:
        """
        Detect reverse value opportunities: Pinnacle raw odds vs soft consensus.

        Delegates to scanner.find_reverse_value_in_market() which applies:
        - MIN_REVERSE_ODDS / MAX_REVERSE_ODDS filters (longshots only)
        - MIN_CONSENSUS_PLATFORMS (5+ independent platforms)
        - MAX_ODDS_RATIO discrepancy check
        - MAX_EDGE_PCT cap

        Returns:
            {"found": int, "new": int}
        """
        result = {"found": 0, "new": 0, "opps": []}

        reverse_bets = self.scanner.find_reverse_value_in_market(
            event_id=event_id,
            market=market,
            odds_by_outcome=odds_by_outcome,
            min_edge_pct=self.min_edge_pct,
            all_markets=all_markets,
        )

        if not reverse_bets:
            return result

        for vb in reverse_bets:
            result["found"] += 1

            # Extract point value from market key if present
            clean_market, point_value = _parse_market_point(market)

            outcomes_json = [
                {
                    "provider": "pinnacle",
                    "outcome": vb.outcome,
                    "odds": vb.provider_odds,
                    "edge_pct": vb.edge_pct,
                },
                {
                    "provider": "consensus",
                    "outcome": vb.outcome,
                    "odds": vb.fair_odds,
                    "is_fair_odds": True,
                    "platforms": int(vb.prob_sum) if vb.prob_sum else 0,
                },
            ]

            is_new, opp = self.opp_repo.upsert_reverse_value(
                event_id=event_id,
                market=clean_market,
                outcome=vb.outcome,
                pinnacle_odds=vb.provider_odds,
                consensus_fair_odds=vb.fair_odds,
                edge_pct=vb.edge_pct,
                outcomes_json=outcomes_json,
                point=point_value,
                scope=scope,
            )
            result["opps"].append(opp)
            if is_new:
                result["new"] += 1

        return result

    def _detect_arb(
        self,
        event,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        all_markets: dict[str, dict[str, list[dict]]] = None,
        scope: str = "ft",
    ) -> dict:
        """
        Detect arb opportunities for a market.

        Soft book legs with +EV; Pinnacle legs at fair odds (0% edge) as coverage.
        Requires at least one soft +EV leg.

        Returns:
            {"arb_found": int, "arb_new": int, "reverse_found": int, "reverse_new": int}
        """
        result = {"arb_found": 0, "arb_new": 0, "reverse_found": 0, "reverse_new": 0, "opps": []}

        opp = self.scanner._find_arb_in_market(
            event=event,
            market=market,
            odds_by_outcome=odds_by_outcome,
            all_markets=all_markets,
        )

        if opp is None:
            return result

        # Require at least one +EV leg
        if not any(leg["edge_pct"] > 0 for leg in opp.legs):
            return result

        # Extract point from market key
        clean_market, point_value = _parse_market_point(market)

        providers_str = ", ".join(f"{leg['provider']}({leg['outcome']})" for leg in opp.legs)
        logger.debug(
            f"[Analyzer] Arb found: {event.id} {market} GP={opp.guaranteed_profit_pct:+.2f}% [{providers_str}]"
        )

        result["arb_found"] = 1

        # ── Post-validation: reject if platform dedup failed ──
        # Safety net: ensure no two soft legs share the same canonical platform.
        # The scanner's _resolve_platform_conflicts should prevent this, but
        # this guard catches any edge cases or regressions.
        soft_legs = [leg for leg in opp.legs if not leg.get("is_sharp", False)]
        seen_canonicals: set[str] = set()
        has_platform_violation = False
        for leg in soft_legs:
            canon = PROVIDER_CANONICAL.get(leg["provider"], leg["provider"])
            if canon in seen_canonicals:
                has_platform_violation = True
                logger.warning(
                    f"[Analyzer] Platform violation detected! {event.id} {market}: "
                    f"multiple soft legs on canonical '{canon}' — skipping arb"
                )
                break
            seen_canonicals.add(canon)

        if has_platform_violation:
            return result

        # Store once with canonical providers (no fan-out needed for arb —
        # there's only one row per event+market, so fan-out to platform members
        # would overwrite the same row N times with only the last write persisting).
        is_new, arb_opp = self.opp_repo.upsert_arb(
            event_id=event.id,
            market=clean_market,
            legs=opp.legs,
            combined_edge_pct=opp.combined_edge_pct,
            guaranteed_profit_pct=opp.guaranteed_profit_pct,
            point=point_value,
            arb_profit_pct=opp.arb_profit_pct,
            arb_legs=opp.arb_legs,
            scope=scope,
        )
        result["opps"].append(arb_opp)
        if is_new:
            result["arb_new"] = 1

        return result
