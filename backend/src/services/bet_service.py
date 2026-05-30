"""Bet service - bet recording and settlement with risk management."""

import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..analysis.devig import compute_consensus_fair_odds, get_fair_odds_for_outcome
from ..config import get_provider_currency
from ..constants import PLATFORM_MAP, SHARP_PROVIDERS
from ..db.models import Bet, Event, Odds, ProfileProviderBonus, Provider, ProviderRiskProfile, SpecialOdds
from ..repositories import BetRepo, ProfileRepo

logger = logging.getLogger(__name__)


class BetService:
    """Business logic for bet recording, settlement, and balance adjustments."""

    def __init__(self, db: Session):
        self.db = db
        self.profile_repo = ProfileRepo(db)
        self.bet_repo = BetRepo(db)

    def _check_cooldown(self, provider_id: str) -> str | None:
        """Check if provider is on cooldown. Returns reason string or None."""
        risk_profile = self.db.query(ProviderRiskProfile).filter(ProviderRiskProfile.provider_id == provider_id).first()
        if not risk_profile or not risk_profile.is_on_cooldown:
            return None
        if risk_profile.cooldown_until and risk_profile.cooldown_until < datetime.now(UTC):
            # Cooldown expired — clear it
            risk_profile.is_on_cooldown = False
            risk_profile.cooldown_until = None
            risk_profile.cooldown_reason = None
            return None
        reason = risk_profile.cooldown_reason or "Provider is on cooldown"
        until = risk_profile.cooldown_until.isoformat() if risk_profile.cooldown_until else "unknown"
        return f"{reason} (until {until})"

    def _get_risk_score(self, provider_id: str) -> float:
        """Get current risk score for provider, or 0.0 if none."""
        risk_profile = self.db.query(ProviderRiskProfile).filter(ProviderRiskProfile.provider_id == provider_id).first()
        return risk_profile.risk_score if risk_profile else 0.0

    def _infer_bet_type_from_opportunity(
        self,
        provider_id: str,
        event_id: str,
        market: str,
        outcome: str,
        point: float | None,
    ) -> str | None:
        """Return the type of an active opportunity matching this placement.

        Used by create_bet to refine the generic 'value' label that mirror
        callers hardcode. The match is on (provider_id, event, market,
        outcome) — point is checked when present so a spread/total bet picks
        the right line. Returns None when no matching opp is found (caller
        keeps its passed bet_type).
        """
        from ..db.models import Opportunity

        q = self.db.query(Opportunity.type).filter(
            Opportunity.event_id == event_id,
            Opportunity.market == market,
            Opportunity.is_active.is_(True),
            Opportunity.outcome1 == outcome,
            (Opportunity.provider1_id == provider_id) | (Opportunity.provider2_id == provider_id),
        )
        if point is not None:
            q = q.filter(Opportunity.point == point)
        row = q.first()
        return row[0] if row else None

    def create_bet(
        self,
        event_id: str | None,
        provider_id: str,
        market: str | None,
        outcome: str | None,
        odds: float,
        stake: float,
        point: float | None = None,
        is_bonus: bool = False,
        bonus_type: str | None = None,
        utility_score: float | None = None,
        selection_probability: float | None = None,
        fair_odds_at_placement: float | None = None,
        boost_event: str | None = None,
        boost_title: str | None = None,
        bet_type: str | None = None,
        start_time_str: str | None = None,
        provider_bet_id: str | None = None,
        external_placement: bool = False,
        arb_group_id: str | None = None,
    ) -> dict:
        """Record a placed bet for active profile with risk tracking."""
        profile = self.profile_repo.get_active()

        # Verify provider exists
        provider = self.db.query(Provider).filter(Provider.id == provider_id).first()
        if not provider:
            return {"error": f"Provider {provider_id} not found"}

        # Block bets on banned providers
        from ..repositories.limit_repo import LimitRepo

        banned = LimitRepo(self.db).get_banned_providers(profile.id)
        if provider_id in banned:
            return {"error": f"Provider {provider_id} is banned — account closed"}

        # Block duplicate: same event + market + outcome + point already has a pending bet (any provider)
        if event_id and market and outcome:
            dup_query = self.db.query(Bet).filter(
                Bet.profile_id == profile.id,
                Bet.event_id == event_id,
                Bet.market == market,
                Bet.outcome == outcome,
                Bet.result == "pending",
            )
            if point is not None:
                dup_query = dup_query.filter(Bet.point == point)
            else:
                dup_query = dup_query.filter(Bet.point.is_(None))
            existing = dup_query.first()
            if existing:
                point_str = f" {point}" if point is not None else ""
                return {
                    "error": f"Already have a pending bet on this market ({market} {outcome}{point_str}) at {existing.provider_id}"
                }

        # Secondary near-dup guard: same provider + same odds + same stake placed
        # in the last 60s. Catches reactive-sync races where event_id / outcome
        # are missing (polymarket has no provider_bet_id and the picked-opp
        # state can be empty when sync fires independently of a click) — two
        # concurrent syncs both insert what looks like a fresh bet because the
        # event_id-keyed check above can't compare.
        from datetime import timedelta as _td

        near_dup_cutoff = datetime.now(UTC) - _td(seconds=60)
        near = (
            self.db.query(Bet)
            .filter(
                Bet.profile_id == profile.id,
                Bet.provider_id == provider_id,
                Bet.odds == odds,
                Bet.stake == stake,
                Bet.result == "pending",
                Bet.placed_at >= near_dup_cutoff,
            )
            .first()
        )
        if near:
            return {
                "error": (
                    f"Near-duplicate bet rejected: {provider_id} {odds}@{stake} already recorded "
                    f"as bet #{near.id} within the last 60s"
                )
            }

        # Check cooldown
        cooldown_reason = self._check_cooldown(provider_id)
        if cooldown_reason:
            return {"error": f"Bet blocked: {cooldown_reason}"}

        # Validate sufficient balance (unless free bet or external placement).
        # external_placement=True is used by the mirror's reactive sync when
        # recording bets the user already placed manually on the bookmaker's
        # site — the stake is already gone from the bookmaker balance, and
        # the local cached balance may be stale or zero. Rejecting on
        # insufficient balance would silently drop the record.
        # Stake is in native currency (USD for Polymarket, SEK for others).
        currency = get_provider_currency(provider_id)
        current_balance = self.profile_repo.get_balance(profile.id, provider_id)
        if not is_bonus and not external_placement and current_balance < stake:
            fmt = f"${current_balance:.2f}" if currency != "SEK" else f"{current_balance:.0f} kr"
            fmt_req = f"${stake:.2f}" if currency != "SEK" else f"{stake:.0f} kr"
            return {"error": f"Insufficient balance: {fmt} available, {fmt_req} required"}

        now = datetime.now(UTC)
        risk_score = self._get_risk_score(provider_id)

        # Compute fair odds at placement from current Pinnacle odds (or use passed value for boosts).
        # For total/spread markets, Pinnacle stores multiple lines (over 8.5, 9.5, 10.5, ...).
        # Without filtering by `point` the de-vig pulls a random line's odds into pin_market and
        # produces fantasy edge numbers (e.g. bet 613/614 Twins/Astros over showed +341% / +34.84%
        # because our over-X.5 was compared against the over-8.5 fair price). Filter by point so
        # the comparison is against the actual line we bet.
        if fair_odds_at_placement is None and event_id and market and outcome:
            pin_query = self.db.query(Odds).filter(
                Odds.event_id == event_id,
                Odds.provider_id == "pinnacle",
                Odds.market == market,
            )
            if market in ("total", "spread"):
                if point is not None:
                    pin_query = pin_query.filter(Odds.point == point)
                else:
                    pin_query = pin_query.filter(Odds.point.is_(None))
            pin_rows = pin_query.all()
            pin_market = {row.outcome: row.odds for row in pin_rows}
            if len(pin_market) >= 2 and outcome in pin_market:
                fair = get_fair_odds_for_outcome(outcome, pin_market, method="multiplicative")
                if fair and fair > 1.0:
                    fair_odds_at_placement = round(fair, 4)

        # Refine bet_type from the matching active opportunity. Mirror callers
        # hardcode bet_type="value" for every recorded placement, which hides
        # reverse_value bets at Pinnacle in the bets table — making CLV trend
        # analysis impossible for the strategy. Look up an active opp keyed on
        # (provider, event, market, outcome) and inherit its type when the
        # caller didn't pass something more specific (arb_anchor, boost, etc.).
        if bet_type in (None, "value") and event_id and market and outcome:
            inferred = self._infer_bet_type_from_opportunity(
                provider_id=provider_id,
                event_id=event_id,
                market=market,
                outcome=outcome,
                point=point,
            )
            if inferred:
                bet_type = inferred

        # Edge gate: reject bets with edge < MIN_EDGE_PCT unless they're part of an arb
        # pair (arb legs can individually be -EV as long as the pair locks profit),
        # already placed externally (mirror sync recording manual placements), or recorded
        # retroactively from provider history (mirror reactive sync catches up on bets the
        # user already placed — rejecting them would silently lose records).
        # Catches the Rockies/Rangers -5% bet that slipped through play_loop's local gate.
        MIN_EDGE_PCT = 0.5
        # bet_types that bypass the edge gate. Keep in sync with the recorders:
        #   - arb_anchor / arb_counter / arb : arb_runner.py + play_loop.py arb flow
        #   - mirror                          : provider history reactive sync (pinnacle, cloudbet)
        #   - boost                           : odds boost bets (LLM fair odds, not Pinnacle)
        ARB_BET_TYPES = ("arb_anchor", "arb_counter", "arb", "mirror", "boost")
        if (
            fair_odds_at_placement
            and fair_odds_at_placement > 1.0
            and odds > 0
            and not external_placement
            and bet_type not in ARB_BET_TYPES
            and not is_bonus
        ):
            edge_pct = (odds / fair_odds_at_placement - 1) * 100
            if edge_pct < MIN_EDGE_PCT:
                return {
                    "error": (
                        f"Bet rejected: edge {edge_pct:.2f}% < {MIN_EDGE_PCT}% floor "
                        f"(odds {odds:.4f} vs fair {fair_odds_at_placement:.4f}). "
                        f"Set bet_type='arb_anchor'/'arb_counter' or external_placement=True to override."
                    )
                }

        # For Polymarket bets, save the event_slug from odds.provider_meta
        # so we can look up the Gamma event for settlement even after odds are cleaned up
        confirmation_id = None
        if provider_id == "polymarket" and event_id:
            odds_row = self.db.query(Odds).filter(Odds.event_id == event_id, Odds.provider_id == "polymarket").first()
            if odds_row and odds_row.provider_meta:
                import json as _json

                try:
                    meta = _json.loads(odds_row.provider_meta)
                    confirmation_id = meta.get("event_slug")
                except (ValueError, TypeError):
                    pass

        # Resolve start_time: Event table > frontend-provided > specials fallback
        start_time = None
        if event_id:
            ev = self.db.query(Event).filter(Event.id == event_id).first()
            if ev and ev.start_time:
                start_time = ev.start_time
        if start_time is None and start_time_str:
            with contextlib.suppress(ValueError, TypeError):
                start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        if start_time is None and bet_type == "boost" and outcome:
            sp = self.db.query(SpecialOdds).filter(SpecialOdds.title == outcome).first()
            if sp and sp.event_time:
                with contextlib.suppress(ValueError, TypeError):
                    start_time = datetime.fromisoformat(sp.event_time.replace("Z", "+00:00"))

        bet = self.bet_repo.create(
            profile_id=profile.id,
            event_id=event_id,
            provider_id=provider_id,
            market=market,
            outcome=outcome,
            odds=odds,
            point=point,
            stake=stake,
            currency=currency,
            is_bonus=is_bonus,
            bonus_type=bonus_type,
            start_time=start_time,
            hour_of_day=now.hour,
            day_of_week=now.weekday(),
            risk_score_at_bet=risk_score,
            utility_score=utility_score,
            selection_probability=selection_probability,
            fair_odds_at_placement=fair_odds_at_placement,
            boost_event=boost_event,
            boost_title=boost_title,
            confirmation_id=confirmation_id,
            bet_type=bet_type,
            provider_bet_id=provider_bet_id,
            arb_group_id=arb_group_id,
        )

        # Balance is managed manually via Adjust — no auto-deduct on placement

        # Auto-advance freebet: mark as completed when freebet is used
        if is_bonus:
            bonus = (
                self.db.query(ProfileProviderBonus)
                .filter(
                    ProfileProviderBonus.profile_id == profile.id,
                    ProfileProviderBonus.provider_id == provider_id,
                    ProfileProviderBonus.bonus_status == "freebet_available",
                )
                .first()
            )
            if bonus:
                bonus.bonus_status = "completed"
                bonus.updated_at = datetime.now(UTC)
                logger.info(f"[BetService] Auto-completed freebet for {provider_id}")

        # Check current wagering status (but don't record — wagering counts on settlement)
        wagering_status = self.profile_repo.get_bonus_status(profile.id, provider_id)

        result_dict = {
            "success": True,
            "bet_id": bet.id,
            "profile_id": profile.id,
            "risk_score": risk_score,
            "bonus_wagering": wagering_status
            if wagering_status.get("status") in ("in_progress", "trigger_needed")
            else None,
        }

        # Advisory: warn if daily cap exceeded for this platform group
        try:
            from ..risk.allocator import ProviderAllocator

            allocator = ProviderAllocator(self.db, profile.id)
            allocator.preload_daily_bets()
            group_bets = allocator._count_group_bets(provider_id)
            cap = allocator._daily_cap
            if group_bets >= cap:
                result_dict["daily_cap_warning"] = (
                    f"Daily cap reached ({group_bets}/{cap} bets today in this platform group)"
                )
        except Exception:
            pass

        return result_dict

    def settle_bet(self, bet_id: int, result: str, payout: float) -> dict:
        """Settle a bet with result and CLV tracking."""
        bet = self.bet_repo.get_by_id(bet_id)
        if not bet:
            return {"error": f"Bet {bet_id} not found"}

        bet.result = result
        bet.payout = payout
        bet.settled_at = datetime.now(UTC)

        # Calculate CLV (Closing Line Value)
        clv_pct = self._calculate_clv(bet)
        if clv_pct is not None:
            bet.clv_pct = clv_pct

        # Balance is managed manually via Adjust — no auto-credit on settlement

        # Record wagering progress on settlement (not placement)
        wagering_status = None
        if bet.profile_id and result in ("won", "lost", "void"):
            wagering_status = self.profile_repo.record_wagering(bet.profile_id, bet.provider_id, bet.stake, bet.odds)

        # Auto-advance freebet: if trigger bet settled, unlock the freebet
        if bet.profile_id:
            bonus = (
                self.db.query(ProfileProviderBonus)
                .filter(
                    ProfileProviderBonus.profile_id == bet.profile_id,
                    ProfileProviderBonus.provider_id == bet.provider_id,
                    ProfileProviderBonus.bonus_status == "trigger_needed",
                )
                .first()
            )
            if bonus:
                # Refresh so wagered_amount reflects what record_wagering() just wrote
                self.db.refresh(bonus)
                trigger_mode = getattr(bonus, "trigger_mode", None) or "cumulative"
                if trigger_mode == "single":
                    # Single-shot: one bet that meets stake + odds requirements
                    if bet.odds >= (bonus.min_odds or 1.80) and bet.stake >= (bonus.bonus_amount or 0):
                        bonus.bonus_status = "freebet_available"
                        bonus.wagered_amount = bet.stake
                        bonus.updated_at = datetime.now(UTC)
                else:
                    # Cumulative: total wagered across bets meets the requirement
                    if (bonus.wagering_requirement or 0) > 0 and (
                        bonus.wagered_amount or 0
                    ) >= bonus.wagering_requirement:
                        bonus.bonus_status = "freebet_available"
                        bonus.updated_at = datetime.now(UTC)

        # Invalidate planner cache if bonus status changed (triggers re-plan on next request)
        if bet.profile_id and wagering_status:
            try:
                from .planner_service import BankrollPlannerService

                BankrollPlannerService.invalidate_cache(bet.profile_id)
            except Exception:
                pass  # Non-critical — planner cache will expire naturally

        # Schedule postmortem + ML resolution in background (non-blocking)
        self._schedule_post_settlement(bet_id, bet.bet_type, bet.outcome)

        return {
            "success": True,
            "profit": bet.profit,
            "profile_id": bet.profile_id,
            "clv_pct": clv_pct,
            "bonus_wagering": wagering_status
            if wagering_status and wagering_status.get("status") in ("in_progress", "trigger_needed")
            else None,
        }

    @staticmethod
    def _schedule_post_settlement(bet_id: int, bet_type: str | None, outcome: str | None):
        """Run postmortem + ML resolution in a background thread with a fresh DB session."""
        import threading

        def _run():
            from ..db.models import get_session

            db = get_session()
            try:
                from .postmortem_service import PostmortemService

                bet = db.query(Bet).get(bet_id)
                if bet:
                    PostmortemService(db).compute_bet(bet)

                if bet_type == "boost" and outcome:
                    from src.ml.feature_store import resolve_boost_outcomes

                    resolve_boost_outcomes(db, outcome)

                db.commit()
            except Exception as e:
                logger.warning(f"Post-settlement background task failed for bet {bet_id}: {e}")
                db.rollback()
            finally:
                db.close()

        threading.Thread(target=_run, daemon=True).start()

    def _build_odds_by_outcome(self, event_id: str, market: str, point: float | None = None) -> dict[str, list[dict]]:
        """Build {outcome: [{provider, odds}, ...]} from current DB odds for an event/market."""
        query = self.db.query(Odds).filter(
            Odds.event_id == event_id,
            Odds.market == market,
        )
        if market in ("spread", "total") and point is not None:
            query = query.filter(Odds.point == point)

        result: dict[str, list[dict]] = {}
        for row in query.all():
            if row.odds <= 1.0:
                continue
            result.setdefault(row.outcome, []).append(
                {
                    "provider": row.provider_id,
                    "odds": row.odds,
                }
            )
        return result

    def _consensus_closing_odds(self, bet: Bet) -> float | None:
        """Compute consensus soft book fair odds for a Pinnacle bet's CLV."""
        odds_by_outcome = self._build_odds_by_outcome(bet.event_id, bet.market, bet.point)
        if not odds_by_outcome:
            return None

        result = compute_consensus_fair_odds(
            outcome=bet.outcome,
            odds_by_outcome=odds_by_outcome,
            platform_map=PLATFORM_MAP,
            sharp_providers=SHARP_PROVIDERS,
            min_platforms=2,
        )
        if result is None:
            return None
        consensus_fair, _ = result
        return consensus_fair if consensus_fair > 1.0 else None

    def _own_closing_odds(self, bet: Bet) -> float | None:
        """Sharp book's own current line for a bet — fallback CLV benchmark
        when no soft-book consensus exists (Pinnacle-only events that soft
        books don't quote). Same-book CLV: did the line move in our favour."""
        query = self.db.query(Odds).filter(
            Odds.event_id == bet.event_id,
            Odds.provider_id == bet.provider_id,
            Odds.market == bet.market,
            Odds.outcome == bet.outcome,
        )
        if bet.market in ("spread", "total") and bet.point is not None:
            query = query.filter(Odds.point == bet.point)
        row = query.first()
        return row.odds if row and row.odds > 1.0 else None

    def _calculate_clv(self, bet: Bet) -> float | None:
        """
        Calculate Closing Line Value for a settled bet.

        Soft book bets: CLV = (bet_odds / pinnacle_closing_odds - 1) * 100
        Pinnacle bets:  CLV = (bet_odds / consensus_soft_closing - 1) * 100
        Provider CLV = (bet_odds / provider_closing_odds - 1) * 100  (Polymarket only)

        Positive CLV means the bet was placed at better odds than the
        closing line — the #1 indicator of sharp betting skill.
        """
        if not bet.event_id or not bet.outcome or not bet.market:
            return None

        # --- CLV for Pinnacle bets: compare against soft consensus closing ---
        is_sharp_bet = bet.provider_id in SHARP_PROVIDERS

        # --- Main CLV ---
        pinnacle_clv = None
        if bet.closing_odds is not None:
            # snapshot_closing_odds already captured it
            pinnacle_clv = round((bet.odds / bet.closing_odds - 1) * 100, 2)
        elif is_sharp_bet:
            # Pinnacle bet: use consensus of soft books as the closing benchmark
            consensus = self._consensus_closing_odds(bet)
            if consensus:
                bet.closing_odds = consensus
                pinnacle_clv = round((bet.odds / consensus - 1) * 100, 2)
            else:
                # No soft consensus — fall back to Pinnacle's own line.
                own = self._own_closing_odds(bet)
                if own:
                    bet.closing_odds = own
                    pinnacle_clv = round((bet.odds / own - 1) * 100, 2)
        else:
            # Soft book bet: use Pinnacle closing odds as benchmark
            query = self.db.query(Odds).filter(
                Odds.event_id == bet.event_id,
                Odds.provider_id.in_(SHARP_PROVIDERS),
                Odds.market == bet.market,
                Odds.outcome == bet.outcome,
            )
            if bet.market in ("spread", "total") and bet.point is not None:
                query = query.filter(Odds.point == bet.point)

            pinnacle_odds = query.first()

            if pinnacle_odds and pinnacle_odds.odds > 1.0:
                bet.closing_odds = pinnacle_odds.odds
                pinnacle_clv = round((bet.odds / pinnacle_odds.odds - 1) * 100, 2)

        # --- Provider CLV (same-market, Polymarket only) ---
        if bet.provider_id == "polymarket" and bet.provider_closing_odds is None:
            provider_query = self.db.query(Odds).filter(
                Odds.event_id == bet.event_id,
                Odds.provider_id == "polymarket",
                Odds.market == bet.market,
                Odds.outcome == bet.outcome,
            )
            if bet.market in ("spread", "total") and bet.point is not None:
                provider_query = provider_query.filter(Odds.point == bet.point)

            poly_odds = provider_query.first()

            if poly_odds and poly_odds.odds > 1.0:
                bet.provider_closing_odds = poly_odds.odds
                bet.provider_clv_pct = round((bet.odds / poly_odds.odds - 1) * 100, 2)
        elif bet.provider_closing_odds is not None and bet.provider_clv_pct is None:
            # Snapshot captured odds but not CLV — compute now
            bet.provider_clv_pct = round((bet.odds / bet.provider_closing_odds - 1) * 100, 2)

        return pinnacle_clv

    def snapshot_closing_odds(self) -> dict:
        """
        For all pending bets on events that have already started (start_time <= now),
        snapshot the current Pinnacle odds as closing_odds and compute CLV.
        For Polymarket bets, also snapshot the Polymarket closing price as
        provider_closing_odds for true same-market CLV.

        This should be called periodically (e.g., during extraction cleanup) to
        capture CLV before the odds/events are cleaned up from the database.

        Returns: {"processed": int, "updated": int, "provider_clv_updated": int}
        """
        now = datetime.now(UTC)

        # Find pending bets on started events — need either Pinnacle or provider CLV
        pending_bets = (
            self.db.query(Bet)
            .join(Event, Event.id == Bet.event_id)
            .filter(
                Bet.result == "pending",
                Bet.event_id.isnot(None),
                Event.start_time.isnot(None),
                Event.start_time <= now,
            )
            .filter(
                # Need Pinnacle CLV, or provider CLV for Polymarket bets
                (Bet.closing_odds.is_(None))
                | ((Bet.provider_id == "polymarket") & (Bet.provider_closing_odds.is_(None)))
            )
            .all()
        )

        processed = 0
        updated = 0
        provider_clv_updated = 0

        for bet in pending_bets:
            processed += 1
            if not bet.outcome or not bet.market:
                continue

            # --- CLV snapshot ---
            if bet.closing_odds is None:
                is_sharp_bet = bet.provider_id in SHARP_PROVIDERS

                if is_sharp_bet:
                    # Pinnacle bet: use consensus soft book closing as benchmark
                    consensus = self._consensus_closing_odds(bet)
                    if consensus:
                        bet.closing_odds = consensus
                        bet.clv_pct = round((bet.odds / consensus - 1) * 100, 2)
                        updated += 1
                    else:
                        # No soft consensus (Pinnacle-only event — soft books
                        # don't quote it). Fall back to Pinnacle's own current
                        # line as the closing benchmark so CLOSE/CLV still fill.
                        own = self._own_closing_odds(bet)
                        if own:
                            bet.closing_odds = own
                            bet.clv_pct = round((bet.odds / own - 1) * 100, 2)
                            updated += 1
                else:
                    # Soft book bet: use Pinnacle closing odds as benchmark
                    query = self.db.query(Odds).filter(
                        Odds.event_id == bet.event_id,
                        Odds.provider_id.in_(SHARP_PROVIDERS),
                        Odds.market == bet.market,
                        Odds.outcome == bet.outcome,
                    )
                    if bet.market in ("spread", "total") and bet.point is not None:
                        query = query.filter(Odds.point == bet.point)

                    pinnacle_odds = query.first()

                    if pinnacle_odds and pinnacle_odds.odds > 1.0:
                        bet.closing_odds = pinnacle_odds.odds
                        bet.clv_pct = round((bet.odds / pinnacle_odds.odds - 1) * 100, 2)
                        updated += 1

            # --- Provider CLV (same-market, Polymarket only) ---
            if bet.provider_id == "polymarket" and bet.provider_closing_odds is None:
                provider_query = self.db.query(Odds).filter(
                    Odds.event_id == bet.event_id,
                    Odds.provider_id == "polymarket",
                    Odds.market == bet.market,
                    Odds.outcome == bet.outcome,
                )
                if bet.market in ("spread", "total") and bet.point is not None:
                    provider_query = provider_query.filter(Odds.point == bet.point)

                poly_odds = provider_query.first()

                if poly_odds and poly_odds.odds > 1.0:
                    bet.provider_closing_odds = poly_odds.odds
                    bet.provider_clv_pct = round((bet.odds / poly_odds.odds - 1) * 100, 2)
                    provider_clv_updated += 1

        if updated > 0 or provider_clv_updated > 0:
            logger.info(
                f"[BetService] Snapshot closing odds: {updated}/{processed} Pinnacle, "
                f"{provider_clv_updated} provider CLV updated"
            )

        return {"processed": processed, "updated": updated, "provider_clv_updated": provider_clv_updated}

    def backfill_fair_odds(self) -> dict:
        """Backfill fair_odds_at_placement for pending PRE-KICKOFF bets that
        were recorded without it.

        Mirror history sync records bets the user placed directly on the book
        after the fact — when Pinnacle odds for the event often aren't in the
        DB yet, so create_bet can't compute the fair price. By the next
        Pinnacle extraction (~2 min) the odds exist; this fills the gap so the
        EST EDGE and PROB columns populate.

        Restricted to events that haven't started so the Pinnacle line used is
        still a pre-match fair approximation of the placement-time edge. Once
        an event starts, snapshot_closing_odds owns closing-line capture.

        Returns: {"processed": int, "updated": int}
        """
        now = datetime.now(UTC)

        candidates = (
            self.db.query(Bet)
            .join(Event, Event.id == Bet.event_id)
            .filter(
                Bet.result == "pending",
                Bet.event_id.isnot(None),
                Bet.market.isnot(None),
                Bet.outcome.isnot(None),
                Bet.fair_odds_at_placement.is_(None),
                Event.start_time.isnot(None),
                Event.start_time > now,
            )
            .all()
        )

        updated = 0
        for bet in candidates:
            # Mirror the de-vig from create_bet: filter by point for
            # total/spread so we compare against the actual line we bet.
            pin_query = self.db.query(Odds).filter(
                Odds.event_id == bet.event_id,
                Odds.provider_id == "pinnacle",
                Odds.market == bet.market,
            )
            if bet.market in ("total", "spread"):
                if bet.point is not None:
                    pin_query = pin_query.filter(Odds.point == bet.point)
                else:
                    pin_query = pin_query.filter(Odds.point.is_(None))
            pin_market = {row.outcome: row.odds for row in pin_query.all()}
            if len(pin_market) >= 2 and bet.outcome in pin_market:
                fair = get_fair_odds_for_outcome(bet.outcome, pin_market, method="multiplicative")
                if fair and fair > 1.0:
                    bet.fair_odds_at_placement = round(fair, 4)
                    updated += 1

        if updated:
            logger.info(f"[BetService] Backfilled fair_odds for {updated}/{len(candidates)} pending bets")

        return {"processed": len(candidates), "updated": updated}

    def edit_bet(
        self,
        bet_id: int,
        stake: float | None = None,
        odds: float | None = None,
        result: str | None = None,
        payout: float | None = None,
        provider_bet_id: str | None = None,
    ) -> dict:
        """Edit a settled bet to correct stake/odds/result.

        Recalculates payout and adjusts provider balance accordingly.
        Used when auto-stake was wrong and user needs to correct it post-settlement.
        """
        bet = self.bet_repo.get_by_id(bet_id)
        if not bet:
            return {"error": f"Bet {bet_id} not found"}

        old_stake = bet.stake
        old_payout = bet.payout
        old_result = bet.result

        # Apply changes
        if stake is not None:
            bet.stake = stake
        if odds is not None:
            bet.odds = odds
        if result is not None:
            bet.result = result
            # Set settled_at when transitioning from pending to a final result
            if old_result == "pending" and result in ("won", "lost", "void"):
                bet.settled_at = datetime.now(UTC)
            # Clear settled_at when reverting back to pending
            elif result == "pending":
                bet.settled_at = None

        # Recalculate payout based on (possibly new) result and stake/odds
        if bet.result == "won":
            bet.payout = bet.stake * bet.odds
        elif bet.result == "void":
            bet.payout = bet.stake
        elif bet.result == "lost":
            bet.payout = 0.0

        # Override payout if explicitly provided (e.g. cashout)
        if payout is not None:
            bet.payout = payout

        # Adjust balance: reverse old payout+stake, apply new payout+stake
        # Balance is managed manually via Adjust — no auto-correction on edit

        # Recalculate CLV if closing odds exist
        if bet.closing_odds and bet.closing_odds > 1.0:
            bet.clv_pct = round((bet.odds / bet.closing_odds - 1) * 100, 2)

        # Backfill provider_bet_id from history reconciliation
        if provider_bet_id is not None and bet.provider_bet_id != provider_bet_id:
            bet.provider_bet_id = provider_bet_id

        # Record wagering progress when transitioning to a settled result
        wagering_status = None
        if bet.profile_id and bet.result in ("won", "lost", "void"):
            if old_result == "pending":
                # New settlement — record full stake
                wagering_status = self.profile_repo.record_wagering(
                    bet.profile_id, bet.provider_id, bet.stake, bet.odds
                )
            elif old_result in ("won", "lost", "void") and stake is not None and stake != old_stake:
                # Stake correction on already-settled bet — record the delta
                delta = bet.stake - old_stake
                if delta > 0:
                    wagering_status = self.profile_repo.record_wagering(
                        bet.profile_id, bet.provider_id, delta, bet.odds
                    )

        self.db.commit()

        logger.info(
            f"[BetService] Edited bet #{bet_id}: "
            f"stake {old_stake}->{bet.stake}, result {old_result}->{bet.result}, "
            f"payout {old_payout}->{bet.payout}"
        )

        return {
            "success": True,
            "bet_id": bet_id,
            "stake": bet.stake,
            "odds": bet.odds,
            "result": bet.result,
            "payout": bet.payout,
            "profit": bet.profit,
            "balance_adjustment": (bet.payout - old_payout) - (bet.stake - old_stake),
            "bonus_wagering": wagering_status
            if wagering_status and wagering_status.get("status") in ("in_progress", "trigger_needed")
            else None,
        }

    def delete_bet(self, bet_id: int) -> dict:
        """Delete a pending bet that was incorrectly recorded.

        Only allows deletion of pending bets — settled bets cannot be deleted
        (use edit_bet to void them instead).
        """
        bet = self.bet_repo.get_by_id(bet_id)
        if not bet:
            return {"error": f"Bet {bet_id} not found"}
        if bet.result != "pending":
            return {"error": f"Cannot delete settled bet (result={bet.result}). Use edit to void instead."}

        provider_id = bet.provider_id
        stake = bet.stake
        self.db.delete(bet)
        self.db.commit()

        logger.info(f"[BetService] Deleted pending bet #{bet_id} ({provider_id}, stake={stake})")
        return {"success": True, "bet_id": bet_id, "deleted": True}
