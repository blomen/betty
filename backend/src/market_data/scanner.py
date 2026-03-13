"""Market scanner — scores trading setups against current AMT conditions.

Maps each setup's confirmations to auto-scorable conditions from market data.
Produces signals with composite quality scores (0-100).
"""

import logging
from dataclasses import dataclass, field

from .amt import SessionAnalysis

logger = logging.getLogger(__name__)


@dataclass
class ScoredCondition:
    """A single scored condition within a setup."""
    name: str
    score: float  # 0.0 - 1.0
    weight: float  # relative importance
    is_auto: bool  # True = scored from data, False = user confirms
    detail: str = ""


@dataclass
class SetupSignal:
    """A scored setup signal."""
    setup_type: str
    setup_name: str
    category: str
    direction: str  # "long", "short"
    score: float  # 0-100 composite
    conditions: list[ScoredCondition] = field(default_factory=list)
    suggested_entry: float | None = None
    suggested_stop: float | None = None
    suggested_target: float | None = None


class MarketScanner:
    """Scores all configured setups against current session analysis."""

    def __init__(self, setups: dict, threshold: float = 70.0, db_session=None):
        self.setups = setups
        self.threshold = threshold
        self.db_session = db_session

    def scan(self, session: SessionAnalysis, candles: list | None = None) -> list[SetupSignal]:
        """Score all setups, return those meeting threshold.

        Macro regime acts as a multiplier on directional setups:
        - risk_on: boosts long scores, penalizes short scores
        - risk_off: boosts short scores, penalizes long scores
        - mixed/unknown: no adjustment
        """
        signals = []

        # Regime multiplier from macro data
        regime_score = 0.0
        if session.macro and session.macro.regime_score:
            regime_score = session.macro.regime_score

        for setup_type, setup_cfg in self.setups.items():
            # Try both directions for each setup
            for direction in ("long", "short"):
                conditions = self._score_setup(setup_type, setup_cfg, session, direction)
                if not conditions:
                    continue

                composite = self._composite_score(conditions)

                # Apply regime multiplier (±5% max adjustment)
                if regime_score != 0:
                    if direction == "long":
                        regime_adj = regime_score * 5  # +5 at max risk-on, -5 at max risk-off
                    else:
                        regime_adj = -regime_score * 5  # inverse for shorts
                    composite = max(0, min(100, composite + regime_adj))

                # Extract ML features before threshold check (needed for M5 prediction)
                ml_features = None
                try:
                    from src.ml.features.trading_features import extract_trading_features
                    orderflow = session.delta
                    ml_features = extract_trading_features(
                        setup_type=setup_type,
                        direction=direction,
                        base_score=int(round(composite)),
                        delta=getattr(orderflow, 'delta', None) if orderflow else None,
                        cvd=getattr(orderflow, 'cvd', None) if orderflow else None,
                        passive_active_ratio=getattr(orderflow, 'passive_active_ratio', None) if orderflow else None,
                        market_type=session.market_type,
                        poor_high=session.poor_high,
                        poor_low=session.poor_low,
                    )
                except Exception as e:
                    logger.debug(f"ML feature extraction skipped: {e}")

                # M5: ML-predicted score overrides composite (best-effort)
                try:
                    from src.ml.serving.predictor import get_predictor
                    predictor = get_predictor()
                    if predictor.is_loaded("setup_scorer") and ml_features:
                        ml_pred = predictor.predict("setup_scorer", ml_features)
                        if ml_pred is not None and isinstance(ml_pred, (int, float)):
                            # ML returns predicted R-multiple; convert to 0-100 score
                            # R > 1.0 -> 85+, R > 0.5 -> 70+, R < 0 -> below threshold
                            ml_score = min(100, max(0, 50 + ml_pred * 25))
                            composite = ml_score
                except Exception as e:
                    logger.debug(f"M5 prediction skipped: {e}")

                # M6: Temporal pattern overlay -- boosts/penalizes based on candle pattern
                try:
                    from src.ml.serving.predictor import get_predictor
                    predictor = get_predictor()
                    if predictor.is_loaded("temporal_pattern") and candles:
                        from src.ml.features.candle_features import snapshot_candles as _snap
                        candle_dicts = _snap(
                            candles,
                            vwap=session.vwap_bands.vwap if session.vwap_bands else None,
                            poc=session.volume_profile.poc if session.volume_profile else None,
                        )
                        if candle_dicts and len(candle_dicts) >= 20:
                            pattern_pred = predictor.predict("temporal_pattern", {"candle_sequence": candle_dicts})
                            if pattern_pred and isinstance(pattern_pred, dict):
                                # Classes: 0=rev_long, 1=rev_short, 2=cont_long, 3=cont_short, 4=chop
                                pattern_class = pattern_pred.get("class", 4)
                                probs = pattern_pred.get("probabilities", [])
                                if probs:
                                    if direction == "long" and pattern_class in (0, 2):
                                        composite = min(100, composite + max(probs) * 10)
                                    elif direction == "short" and pattern_class in (1, 3):
                                        composite = min(100, composite + max(probs) * 10)
                                    elif pattern_class == 4:
                                        composite = max(0, composite - 5)
                except Exception as e:
                    logger.debug(f"M6 prediction skipped: {e}")

                # M9: Add news event proximity to features
                try:
                    if self.db_session is not None and ml_features is not None:
                        from src.data.economic_calendar import get_upcoming_events, get_recent_events
                        from datetime import datetime, timezone
                        upcoming = get_upcoming_events(self.db_session, minutes_ahead=30)
                        if upcoming:
                            nearest = upcoming[0]
                            ml_features["news_event_minutes_away"] = (
                                datetime.fromisoformat(nearest.event_datetime) - datetime.now(timezone.utc)
                            ).total_seconds() / 60
                            ml_features["news_event_importance"] = nearest.importance
                        recent = get_recent_events(self.db_session, minutes_ago=60)
                        if recent:
                            latest = recent[0]
                            ml_features["post_news_minutes"] = (
                                datetime.now(timezone.utc) - datetime.fromisoformat(latest.event_datetime)
                            ).total_seconds() / 60
                            ml_features["news_surprise"] = latest.surprise
                except Exception as e:
                    logger.debug(f"M9 news context skipped: {e}")

                if composite >= self.threshold:
                    entry, stop, target = self._suggest_levels(
                        setup_type, session, direction
                    )
                    signal = SetupSignal(
                        setup_type=setup_type,
                        setup_name=setup_cfg.get("name", setup_type),
                        category=setup_cfg.get("category", "other"),
                        direction=direction,
                        score=round(composite, 1),
                        conditions=conditions,
                        suggested_entry=entry,
                        suggested_stop=stop,
                        suggested_target=target,
                    )
                    signals.append(signal)

                    # Log ML features (best-effort, never blocks scanning)
                    try:
                        if self.db_session is not None and ml_features is not None:
                            from src.ml.feature_store import log_features, log_candle_snapshot
                            source_id = f"{setup_type}_{direction}_{id(signal)}"
                            feat_row = log_features(
                                session=self.db_session,
                                domain="trading",
                                source_id=source_id,
                                source_type="trading_signal",
                                features=ml_features,
                            )
                            if candles and feat_row:
                                from src.ml.features.candle_features import snapshot_candles as _snap_log
                                candle_dicts = _snap_log(
                                    candles,
                                    vwap=session.vwap_bands.vwap if session.vwap_bands else None,
                                    poc=session.volume_profile.poc if session.volume_profile else None,
                                )
                                log_candle_snapshot(
                                    session=self.db_session,
                                    signal_id=feat_row.id,
                                    candles=candle_dicts,
                                )
                    except Exception as e:
                        logger.debug(f"ML feature logging skipped: {e}")

        signals.sort(key=lambda s: s.score, reverse=True)
        logger.info("Scan produced %d signals (threshold=%.0f)", len(signals), self.threshold)
        return signals

    def _composite_score(self, conditions: list[ScoredCondition]) -> float:
        """Weighted average score × 100."""
        total_weight = sum(c.weight for c in conditions)
        if total_weight == 0:
            return 0
        return sum(c.score * c.weight for c in conditions) / total_weight * 100

    def _score_setup(
        self,
        setup_type: str,
        setup_cfg: dict,
        session: SessionAnalysis,
        direction: str,
    ) -> list[ScoredCondition]:
        """Score a specific setup. Returns conditions list."""
        scorer = self._get_scorer(setup_type)
        if scorer:
            return scorer(session, direction)

        # Fallback: score generic confirmations at 0.5 (manual)
        return [
            ScoredCondition(name=c, score=0.5, weight=1.0, is_auto=False)
            for c in setup_cfg.get("confirmations", [])
        ]

    def _get_scorer(self, setup_type: str):
        """Get the specialized scoring function for a setup."""
        scorers = {
            "reversal_vwap_2sd": self._score_reversal_vwap_2sd,
            "reversal_vwap_3sd": self._score_reversal_vwap_3sd,
            "trend_continuation": self._score_trend_continuation,
            "trapped_traders": self._score_trapped_traders,
            "initial_balance_break": self._score_ib_break,
            "eighty_percent_rule": self._score_80pct_rule,
            "break_from_balance": self._score_break_from_balance,
            "swing_failure_trap": self._score_swing_failure,
            "delta_unwind": self._score_delta_unwind,
            "break_of_structure": self._score_break_of_structure,
            "double_distribution_reversal": self._score_double_dist,
            "ib_continuation": self._score_ib_continuation,
            "acceptance_failure": self._score_acceptance_failure,
        }
        return scorers.get(setup_type)

    # ============ Setup Scorers ============

    def _score_reversal_vwap_2sd(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: Price at VWAP 2SD, exhaustion, delta divergence."""
        if not s.vwap_bands or not s.last_price:
            return []

        conditions = []
        vwap = s.vwap_bands

        # Condition 1: Price proximity to 2SD band
        if direction == "long" and s.price_vs_vwap in ("below_2sd", "below_3sd"):
            dist = abs(s.last_price - vwap.lower_2sd)
            band_width = abs(vwap.upper_2sd - vwap.lower_2sd) or 1
            proximity = max(0, 1.0 - dist / (band_width * 0.1))
            conditions.append(ScoredCondition("Price at VWAP 2SD band", proximity, 2.0, True, f"Price {s.last_price:.2f} vs 2SD lower {vwap.lower_2sd:.2f}"))
        elif direction == "short" and s.price_vs_vwap in ("above_2sd", "above_3sd"):
            dist = abs(s.last_price - vwap.upper_2sd)
            band_width = abs(vwap.upper_2sd - vwap.lower_2sd) or 1
            proximity = max(0, 1.0 - dist / (band_width * 0.1))
            conditions.append(ScoredCondition("Price at VWAP 2SD band", proximity, 2.0, True, f"Price {s.last_price:.2f} vs 2SD upper {vwap.upper_2sd:.2f}"))
        else:
            return []  # Not applicable for this direction

        # Condition 2: Delta divergence
        div_score = 0.9 if s.delta and s.delta.delta_divergence else 0.3
        conditions.append(ScoredCondition("Divergence on cumulative delta", div_score, 1.5, True))

        # Condition 3: Absorption/exhaustion (manual — default 0.5)
        conditions.append(ScoredCondition("Absorption / exhaustion on delta", 0.5, 1.5, False))

        # Condition 4: No major news catalyst (manual)
        conditions.append(ScoredCondition("No major news catalyst", 0.5, 1.0, False))

        return conditions

    def _score_reversal_vwap_3sd(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: Price at VWAP 3SD, volume climax, exhaustion."""
        if not s.vwap_bands or not s.last_price:
            return []

        conditions = []
        vwap = s.vwap_bands

        # Must be at 3SD
        if direction == "long" and s.price_vs_vwap == "below_3sd":
            conditions.append(ScoredCondition("Price at VWAP 3SD band", 0.95, 2.5, True))
        elif direction == "short" and s.price_vs_vwap == "above_3sd":
            conditions.append(ScoredCondition("Price at VWAP 3SD band", 0.95, 2.5, True))
        else:
            return []

        # Delta divergence
        div_score = 0.9 if s.delta and s.delta.delta_divergence else 0.3
        conditions.append(ScoredCondition("Delta divergence confirmed", div_score, 2.0, True))

        # Manual conditions
        conditions.append(ScoredCondition("Clear exhaustion candle", 0.5, 1.5, False))
        conditions.append(ScoredCondition("Volume climax", 0.5, 1.5, False))

        return conditions

    def _score_trend_continuation(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: Higher TF trend, pullback to VA/VWAP, delta confirmation."""
        if not s.volume_profile or not s.vwap_bands:
            return []

        conditions = []

        # Market type alignment
        if direction == "long" and s.market_type == "trending_up":
            conditions.append(ScoredCondition("Clear trend direction on higher TF", 0.85, 2.0, True))
        elif direction == "short" and s.market_type == "trending_down":
            conditions.append(ScoredCondition("Clear trend direction on higher TF", 0.85, 2.0, True))
        else:
            conditions.append(ScoredCondition("Clear trend direction on higher TF", 0.2, 2.0, True))

        # Pullback to VWAP or value area
        if s.price_vs_vwap == "at_vwap" or s.price_vs_va == "within":
            conditions.append(ScoredCondition("Pullback to VWAP or value area", 0.8, 1.5, True))
        elif s.price_vs_vwap in ("above_1sd", "below_1sd"):
            conditions.append(ScoredCondition("Pullback to VWAP or value area", 0.5, 1.5, True))
        else:
            conditions.append(ScoredCondition("Pullback to VWAP or value area", 0.2, 1.5, True))

        # Delta confirmation (manual - need chart reading)
        conditions.append(ScoredCondition("Delta confirmation on entry TF", 0.5, 1.5, False))
        conditions.append(ScoredCondition("Volume increase on continuation", 0.5, 1.0, False))

        return conditions

    def _score_trapped_traders(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: Failed breakout, volume spike, reversal + delta shift."""
        conditions = []

        # Poor high/low indicates failed breakout
        if direction == "long" and s.poor_low:
            conditions.append(ScoredCondition("Clear breakout attempt that fails", 0.8, 2.0, True))
        elif direction == "short" and s.poor_high:
            conditions.append(ScoredCondition("Clear breakout attempt that fails", 0.8, 2.0, True))
        else:
            conditions.append(ScoredCondition("Clear breakout attempt that fails", 0.3, 2.0, True))

        # Manual conditions
        conditions.append(ScoredCondition("Volume spike on breakout (trapped participants)", 0.5, 1.5, False))
        conditions.append(ScoredCondition("Quick reversal back into range", 0.5, 1.5, False))
        conditions.append(ScoredCondition("Delta shift confirming trapped side", 0.5, 1.5, False))

        return conditions

    def _score_ib_break(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: IB established, price beyond IB, volume expansion."""
        if not s.initial_balance or s.initial_balance.ib_range == 0:
            return []

        conditions = []
        ib = s.initial_balance

        # IB range established
        conditions.append(ScoredCondition("IB range established (first 60 min)", 0.9, 1.0, True, f"IB: {ib.ib_low:.2f} - {ib.ib_high:.2f} (range: {ib.ib_range:.2f})"))

        # Price beyond IB
        if direction == "long" and s.price_vs_ib == "above":
            conditions.append(ScoredCondition("Clean break of IB high", 0.85, 2.0, True))
        elif direction == "short" and s.price_vs_ib == "below":
            conditions.append(ScoredCondition("Clean break of IB low", 0.85, 2.0, True))
        else:
            return []  # No IB break yet

        # Manual conditions
        conditions.append(ScoredCondition("Volume expansion on break", 0.5, 1.5, False))
        conditions.append(ScoredCondition("Bias aligns with break direction", 0.5, 1.0, False))

        return conditions

    def _score_80pct_rule(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: Open within prev VA, rotation to other side."""
        conditions = []

        # Open within previous value area
        if s.prev_vah and s.prev_val and s.last_price:
            in_prev_va = s.prev_val <= s.last_price <= s.prev_vah
            conditions.append(ScoredCondition("Open within previous day value area", 0.8 if in_prev_va else 0.2, 2.0, True))
        else:
            conditions.append(ScoredCondition("Open within previous day value area", 0.5, 2.0, False))

        # Coverage of VA (auto-check if price has traversed 80% of prev VA)
        if s.prev_vah and s.prev_val:
            va_range = s.prev_vah - s.prev_val
            if va_range > 0:
                if direction == "long" and s.last_price:
                    coverage = (s.last_price - s.prev_val) / va_range
                    conditions.append(ScoredCondition("80% of previous VA covered", min(coverage / 0.8, 1.0), 2.0, True))
                elif direction == "short" and s.last_price:
                    coverage = (s.prev_vah - s.last_price) / va_range
                    conditions.append(ScoredCondition("80% of previous VA covered", min(coverage / 0.8, 1.0), 2.0, True))

        # Manual
        conditions.append(ScoredCondition("Initial move toward one VA extreme", 0.5, 1.0, False))
        conditions.append(ScoredCondition("Delta confirms rotation direction", 0.5, 1.0, False))

        return conditions

    def _score_break_from_balance(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: Multi-day balance, clean break, single prints."""
        conditions = []

        # Balance area detection
        if s.market_type == "balanced":
            conditions.append(ScoredCondition("Multi-day balance area identified", 0.7, 2.0, True))
        else:
            conditions.append(ScoredCondition("Multi-day balance area identified", 0.3, 2.0, True))

        # Single prints as evidence of initiative activity
        sp_score = min(len(s.single_prints) / 3, 1.0) if s.single_prints else 0.2
        conditions.append(ScoredCondition("Market profile shows single prints", sp_score, 1.5, True))

        # Manual
        conditions.append(ScoredCondition("Clean break of balance boundary", 0.5, 2.0, False))
        conditions.append(ScoredCondition("Increased volume on break", 0.5, 1.0, False))

        return conditions

    def _score_swing_failure(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: Swing beyond level, poor high/low, counter-delta."""
        conditions = []

        # Poor high/low
        if direction == "long" and s.poor_low:
            conditions.append(ScoredCondition("Failure to hold (poor low)", 0.85, 2.0, True))
        elif direction == "short" and s.poor_high:
            conditions.append(ScoredCondition("Failure to hold (poor high)", 0.85, 2.0, True))
        else:
            conditions.append(ScoredCondition("Failure to hold (poor high/low)", 0.3, 2.0, True))

        # Manual
        conditions.append(ScoredCondition("Swing above/below key level", 0.5, 1.5, False))
        conditions.append(ScoredCondition("Quick reversal back through level", 0.5, 1.5, False))
        conditions.append(ScoredCondition("Aggressive counter-delta", 0.5, 1.5, False))

        return conditions

    def _score_delta_unwind(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: Aggressive delta buildup, price stall, divergence."""
        conditions = []

        # Delta divergence
        div_score = 0.9 if s.delta and s.delta.delta_divergence else 0.3
        conditions.append(ScoredCondition("Delta divergence forming", div_score, 2.0, True))

        # Manual conditions
        conditions.append(ScoredCondition("Aggressive delta buildup identified", 0.5, 2.0, False))
        conditions.append(ScoredCondition("Price stalls despite delta push", 0.5, 1.5, False))
        conditions.append(ScoredCondition("Unwind candle confirmation", 0.5, 1.0, False))

        return conditions

    def _score_break_of_structure(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: HH/HL or LH/LL break, body close, volume."""
        conditions = []

        # Trending market supports structural breaks
        if direction == "long" and s.market_type == "trending_up":
            conditions.append(ScoredCondition("Key level identified (HH/HL or LH/LL)", 0.7, 1.5, True))
        elif direction == "short" and s.market_type == "trending_down":
            conditions.append(ScoredCondition("Key level identified (HH/HL or LH/LL)", 0.7, 1.5, True))
        else:
            conditions.append(ScoredCondition("Key level identified (HH/HL or LH/LL)", 0.4, 1.5, True))

        # Manual
        conditions.append(ScoredCondition("Clean break with body close beyond level", 0.5, 2.0, False))
        conditions.append(ScoredCondition("Volume supports the break", 0.5, 1.5, False))
        conditions.append(ScoredCondition("No immediate resistance/support ahead", 0.5, 1.0, False))

        return conditions

    def _score_double_dist(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: Double distribution reversal at extreme."""
        conditions = []

        # TPO distribution type is the best indicator
        if s.tpo_profile and s.tpo_profile.distribution_type == "double":
            sp_score = 0.9
        elif s.single_prints:
            sp_score = min(len(s.single_prints) / 2, 1.0)
        else:
            sp_score = 0.2
        conditions.append(ScoredCondition("Double distribution profile forming", sp_score, 2.0, True))

        # Manual
        conditions.append(ScoredCondition("Price at extreme of second distribution", 0.5, 2.0, False))
        conditions.append(ScoredCondition("Exhaustion signals present", 0.5, 1.5, False))
        conditions.append(ScoredCondition("Volume declining at extreme", 0.5, 1.0, False))

        return conditions

    def _score_ib_continuation(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: IB break with conviction, acceptance."""
        if not s.initial_balance or s.initial_balance.ib_range == 0:
            return []

        conditions = []

        # IB break
        if direction == "long" and s.price_vs_ib == "above":
            conditions.append(ScoredCondition("IB break with strong conviction", 0.8, 2.0, True))
        elif direction == "short" and s.price_vs_ib == "below":
            conditions.append(ScoredCondition("IB break with strong conviction", 0.8, 2.0, True))
        else:
            return []

        # Manual
        conditions.append(ScoredCondition("Acceptance above/below IB (time + volume)", 0.5, 2.0, False))
        conditions.append(ScoredCondition("No excess / poor auction at IB edge", 0.5, 1.0, False))

        # TPO structure — auto-score if TPO data available
        if s.tpo_profile:
            # p_shape = bullish (fat top), b_shape = bearish (fat bottom)
            if (direction == "long" and s.tpo_profile.distribution_type == "p_shape") or \
               (direction == "short" and s.tpo_profile.distribution_type == "b_shape"):
                tpo_score = 0.85
            elif s.tpo_profile.distribution_type == "normal":
                tpo_score = 0.5
            else:
                tpo_score = 0.3
            conditions.append(ScoredCondition("TPO structure confirms direction", tpo_score, 1.0, True))
        else:
            conditions.append(ScoredCondition("TPO structure confirms direction", 0.5, 1.0, False))

        return conditions

    def _score_acceptance_failure(self, s: SessionAnalysis, direction: str) -> list[ScoredCondition]:
        """Score: Failed acceptance above/below key level."""
        conditions = []

        # Poor high/low = failure to accept
        if direction == "long" and s.poor_low:
            conditions.append(ScoredCondition("Failure to build value beyond level", 0.8, 2.0, True))
        elif direction == "short" and s.poor_high:
            conditions.append(ScoredCondition("Failure to build value beyond level", 0.8, 2.0, True))
        else:
            conditions.append(ScoredCondition("Failure to build value beyond level", 0.3, 2.0, True))

        # Manual
        conditions.append(ScoredCondition("Break of key level", 0.5, 1.5, False))
        conditions.append(ScoredCondition("Responsive activity back through level", 0.5, 1.5, False))
        conditions.append(ScoredCondition("POC migrating away from break", 0.5, 1.0, False))

        return conditions

    # ============ Level Suggestions ============

    def _suggest_levels(
        self, setup_type: str, s: SessionAnalysis, direction: str
    ) -> tuple[float | None, float | None, float | None]:
        """Suggest entry/stop/target for a setup based on current levels."""
        if not s.last_price:
            return None, None, None

        price = s.last_price
        entry = price

        # Default stop/target based on direction and key levels
        if direction == "long":
            # Stop below nearest support
            stops = [l for l in [
                s.volume_profile.val if s.volume_profile else None,
                s.vwap_bands.lower_1sd if s.vwap_bands else None,
                s.initial_balance.ib_low if s.initial_balance else None,
                s.overnight_low,
            ] if l is not None and l < price]
            stop = max(stops) if stops else price - 20  # 20 pt default stop

            # Target above nearest resistance
            targets = [l for l in [
                s.volume_profile.vah if s.volume_profile else None,
                s.vwap_bands.upper_1sd if s.vwap_bands else None,
                s.initial_balance.ib_high if s.initial_balance else None,
                s.overnight_high,
            ] if l is not None and l > price]
            target = min(targets) if targets else price + 40

        else:  # short
            stops = [l for l in [
                s.volume_profile.vah if s.volume_profile else None,
                s.vwap_bands.upper_1sd if s.vwap_bands else None,
                s.initial_balance.ib_high if s.initial_balance else None,
                s.overnight_high,
            ] if l is not None and l > price]
            stop = min(stops) if stops else price + 20

            targets = [l for l in [
                s.volume_profile.val if s.volume_profile else None,
                s.vwap_bands.lower_1sd if s.vwap_bands else None,
                s.initial_balance.ib_low if s.initial_balance else None,
                s.overnight_low,
            ] if l is not None and l < price]
            target = max(targets) if targets else price - 40

        return round(entry, 2), round(stop, 2), round(target, 2)
