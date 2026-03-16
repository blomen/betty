# Postmortem System Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated post-settlement classification and pattern detection for bets and trades, with a dashboard for periodic review.

**Architecture:** Two new DB tables (`BetPostmortem`, `TradePostmortem`) materialized by a `PostmortemClassifier` after settlement. A `PatternDetector` segments postmortem data and surfaces insights. New API routes serve the dashboard. Betting gets a new tab; trading gets a new section in TradingStatsPage.

**Tech Stack:** Python / SQLAlchemy / FastAPI / React / TypeScript / Tailwind

**Spec:** `docs/superpowers/specs/2026-03-16-postmortem-system-design.md`

---

## Chunk 1: Backend Data Model + Repository

### Task 1: Add BetPostmortem and TradePostmortem models

**Files:**
- Modify: `backend/src/db/models.py` (after line 268, after Bet model)

- [ ] **Step 1: Add BetPostmortem model after Bet class (line 268)**

```python
class BetPostmortem(Base):
    """Post-settlement classification for a bet. One row per settled bet."""
    __tablename__ = "bet_postmortems"

    bet_id = Column(Integer, ForeignKey("bets.id"), primary_key=True)
    classification = Column(String, nullable=False)  # expected_loss, edge_erosion, false_edge, sizing_error, expected_win, bonus_win
    edge_at_placement = Column(Float, nullable=True)  # Derived: (odds / fair_odds_at_placement - 1) * 100
    clv_pct = Column(Float, nullable=True)  # Copied from bet.clv_pct
    clv_confirmed = Column(Boolean, default=False)  # True if (start_time - placed_at) <= 12h
    expected_win_pct = Column(Float, nullable=True)  # 1 / fair_odds_at_placement
    kelly_fraction = Column(Float, nullable=True)  # actual_stake / kelly_optimal_stake
    is_oversized = Column(Boolean, default=False)  # kelly_fraction > 1.5
    is_undersized = Column(Boolean, default=False)  # kelly_fraction < 0.5
    variance_score = Column(Float, nullable=True)  # win: 1 - expected_win_pct, loss: expected_win_pct
    computed_at = Column(DateTime, default=_utcnow)
    version = Column(Integer, default=1)

    bet = relationship("Bet")

    __table_args__ = (
        Index("ix_bet_pm_classification_version", "classification", "version"),
    )
```

- [ ] **Step 2: Add TradePostmortem model after TradeReview class (line 995)**

```python
class TradePostmortem(Base):
    """Post-close classification for a trade. One row per closed trade."""
    __tablename__ = "trade_postmortems"

    trade_id = Column(Integer, ForeignKey("trades.id"), primary_key=True)
    classification = Column(String, nullable=False)  # expected_loss, stop_too_wide, thesis_invalid, expected_win, runner
    r_multiple = Column(Float, nullable=True)
    setup_avg_r = Column(Float, nullable=True)
    setup_win_rate = Column(Float, nullable=True)
    stop_quality = Column(String, nullable=True)  # optimal, too_wide
    target_quality = Column(String, nullable=True)  # hit_target, partial_exit_good, missed_runner, exited_early
    streak_position = Column(Integer, nullable=True)  # negative = losing streak
    routine_psych_avg = Column(Float, nullable=True)
    rules_followed = Column(Boolean, nullable=True)
    computed_at = Column(DateTime, default=_utcnow)
    version = Column(Integer, default=1)

    trade = relationship("Trade")

    __table_args__ = (
        Index("ix_trade_pm_classification_version", "classification", "version"),
    )
```

- [ ] **Step 3: Verify tables are created**

Run: `cd backend && python -c "from src.db.models import init_db; init_db()"`
Expected: No errors, `bet_postmortems` and `trade_postmortems` tables exist in SQLite.

- [ ] **Step 4: Commit**

```bash
git add backend/src/db/models.py
git commit -m "feat: add BetPostmortem and TradePostmortem models"
```

---

### Task 2: Add PostmortemRepo

**Files:**
- Create: `backend/src/repositories/postmortem_repo.py`

- [ ] **Step 1: Create PostmortemRepo with CRUD methods**

```python
"""Postmortem repository — data access for postmortem tables."""

from sqlalchemy.orm import Session, joinedload

from ..db.models import Bet, Trade, BetPostmortem, TradePostmortem, _utcnow


class PostmortemRepo:
    """Data access for bet and trade postmortems."""

    def __init__(self, db: Session):
        self.db = db

    # ── Bet Postmortems ──

    def get_bet_pm(self, bet_id: int) -> BetPostmortem | None:
        return self.db.query(BetPostmortem).filter(BetPostmortem.bet_id == bet_id).first()

    def upsert_bet_pm(self, bet_id: int, **kwargs) -> BetPostmortem:
        """Create or update a bet postmortem."""
        existing = self.get_bet_pm(bet_id)
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            existing.version += 1
            existing.computed_at = _utcnow()
            return existing
        pm = BetPostmortem(bet_id=bet_id, **kwargs)
        self.db.add(pm)
        return pm

    def get_bet_pms_for_profile(self, profile_id: int) -> list[tuple[Bet, BetPostmortem]]:
        """Get all postmortems for a profile (joined with Bet + Event to avoid N+1)."""
        return (
            self.db.query(Bet, BetPostmortem)
            .join(BetPostmortem, Bet.id == BetPostmortem.bet_id)
            .options(joinedload(Bet.event))
            .filter(Bet.profile_id == profile_id)
            .order_by(Bet.placed_at.desc())
            .all()
        )

    def get_uncomputed_bets(self, profile_id: int, algo_version: int) -> list[Bet]:
        """Get settled bets missing postmortem or with outdated version."""
        from sqlalchemy import or_
        computed_ids = (
            self.db.query(BetPostmortem.bet_id)
            .filter(BetPostmortem.version >= algo_version)
            .subquery()
        )
        return (
            self.db.query(Bet)
            .filter(
                Bet.profile_id == profile_id,
                Bet.result.in_(["won", "lost"]),
                ~Bet.id.in_(self.db.query(computed_ids))
            )
            .all()
        )

    # ── Trade Postmortems ──

    def get_trade_pm(self, trade_id: int) -> TradePostmortem | None:
        return self.db.query(TradePostmortem).filter(TradePostmortem.trade_id == trade_id).first()

    def upsert_trade_pm(self, trade_id: int, **kwargs) -> TradePostmortem:
        """Create or update a trade postmortem."""
        existing = self.get_trade_pm(trade_id)
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            existing.version += 1
            existing.computed_at = _utcnow()
            return existing
        pm = TradePostmortem(trade_id=trade_id, **kwargs)
        self.db.add(pm)
        return pm

    def get_trade_pms_for_account(self, account_id: int) -> list[tuple[Trade, TradePostmortem]]:
        """Get all postmortems for a trading account (joined with Trade)."""
        return (
            self.db.query(Trade, TradePostmortem)
            .join(TradePostmortem, Trade.id == TradePostmortem.trade_id)
            .filter(Trade.account_id == account_id)
            .order_by(Trade.closed_at.desc())
            .all()
        )

    def get_uncomputed_trades(self, account_id: int, algo_version: int) -> list[Trade]:
        """Get closed trades missing postmortem or with outdated version."""
        computed_ids = (
            self.db.query(TradePostmortem.trade_id)
            .filter(TradePostmortem.version >= algo_version)
            .subquery()
        )
        return (
            self.db.query(Trade)
            .filter(
                Trade.account_id == account_id,
                Trade.state.in_(["closed", "reviewed"]),
                ~Trade.id.in_(self.db.query(computed_ids))
            )
            .all()
        )
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/repositories/postmortem_repo.py
git commit -m "feat: add PostmortemRepo for bet/trade postmortem CRUD"
```

---

## Chunk 2: Classification Logic

### Task 3: Create PostmortemClassifier

**Files:**
- Create: `backend/src/analysis/postmortem.py`

- [ ] **Step 1: Create the classifier module**

```python
"""
Postmortem Classification — decision tree for bet/trade outcome analysis.

Bet classifications: expected_loss, edge_erosion, false_edge, sizing_error, expected_win, bonus_win
Trade classifications: expected_loss, stop_too_wide, thesis_invalid, expected_win, runner
"""

import logging
from datetime import timedelta

from ..db.models import Bet, Trade, TradeEvent, DailyRoutine
from ..bankroll.stake_calculator import calculate_stake

logger = logging.getLogger(__name__)

CURRENT_ALGO_VERSION = 1


def classify_bet(bet: Bet, profile_bankroll: float | None = None) -> dict:
    """
    Classify a settled bet. Returns dict of all postmortem fields.

    Only call on bets with result in ('won', 'lost'). Void bets are skipped.
    """
    result = {}

    # Derive edge at placement
    edge_at_placement = None
    if bet.fair_odds_at_placement and bet.fair_odds_at_placement > 0:
        edge_at_placement = (bet.odds / bet.fair_odds_at_placement - 1) * 100
    result["edge_at_placement"] = edge_at_placement

    # Copy CLV
    result["clv_pct"] = bet.clv_pct

    # CLV confirmed: closing odds reliable if bet placed within 12h of start
    clv_confirmed = False
    if bet.closing_odds is not None and bet.start_time and bet.placed_at:
        ttk = bet.start_time - bet.placed_at
        clv_confirmed = ttk <= timedelta(hours=12)
    result["clv_confirmed"] = clv_confirmed

    # Expected win probability
    expected_win_pct = None
    if bet.fair_odds_at_placement and bet.fair_odds_at_placement > 0:
        expected_win_pct = 1.0 / bet.fair_odds_at_placement
    result["expected_win_pct"] = expected_win_pct

    # Kelly fraction: actual_stake / kelly_optimal_stake
    kelly_fraction = None
    is_oversized = False
    is_undersized = False
    if edge_at_placement and edge_at_placement > 0 and profile_bankroll and profile_bankroll > 0:
        try:
            stake_result = calculate_stake(
                bankroll_total=profile_bankroll,
                edge_raw=edge_at_placement / 100,
                odds=bet.odds,
            )
            if stake_result.stake > 0:
                kelly_fraction = bet.stake / stake_result.stake
                is_oversized = kelly_fraction > 1.5
                is_undersized = kelly_fraction < 0.5
        except Exception:
            pass
    result["kelly_fraction"] = kelly_fraction
    result["is_oversized"] = is_oversized
    result["is_undersized"] = is_undersized

    # Variance score
    variance_score = None
    if expected_win_pct is not None:
        if bet.result == "won":
            variance_score = 1.0 - expected_win_pct
        elif bet.result == "lost":
            variance_score = expected_win_pct
    result["variance_score"] = variance_score

    # Classification
    if bet.result == "won":
        if bet.clv_pct is not None and bet.clv_pct > 0:
            result["classification"] = "expected_win"
        else:
            result["classification"] = "bonus_win"
    elif bet.result == "lost":
        # Priority: sizing_error > false_edge > edge_erosion > expected_loss
        if is_oversized:
            result["classification"] = "sizing_error"
        elif bet.clv_pct is not None and bet.clv_pct < 0 and (edge_at_placement is None or edge_at_placement < 1):
            result["classification"] = "false_edge"
        elif bet.clv_pct is not None and bet.clv_pct < 0 and edge_at_placement is not None and edge_at_placement >= 1:
            result["classification"] = "edge_erosion"
        elif edge_at_placement is not None and edge_at_placement < 1 and bet.clv_pct is None:
            result["classification"] = "false_edge"
        elif bet.clv_pct is not None and bet.clv_pct > 0:
            result["classification"] = "expected_loss"
        else:
            result["classification"] = "expected_loss"

    return result


def classify_trade(trade: Trade, all_trades_for_setup: list[Trade],
                   streak_position: int, routine: DailyRoutine | None = None,
                   trade_events: list[TradeEvent] | None = None) -> dict:
    """
    Classify a closed trade. Returns dict of all postmortem fields.

    Only call on trades with state in ('closed', 'reviewed').
    """
    result = {}
    r = trade.r_multiple or 0.0
    result["r_multiple"] = r

    # Setup benchmarks
    closed_setup_trades = [t for t in all_trades_for_setup
                           if t.state in ("closed", "reviewed") and t.r_multiple is not None and t.id != trade.id]
    if closed_setup_trades:
        wins = [t for t in closed_setup_trades if t.r_multiple > 0]
        result["setup_win_rate"] = len(wins) / len(closed_setup_trades)
        result["setup_avg_r"] = sum(t.r_multiple for t in closed_setup_trades) / len(closed_setup_trades)
    else:
        result["setup_win_rate"] = None
        result["setup_avg_r"] = None

    # Stop quality
    stop_widened = False
    if trade_events:
        for ev in trade_events:
            if ev.event_type == "trail_stop" and ev.details:
                old_stop = ev.details.get("old_stop")
                new_stop = ev.details.get("new_stop")
                if old_stop is not None and new_stop is not None and trade.entry_price is not None:
                    # Widened = new stop is farther from entry than old stop
                    old_dist = abs(trade.entry_price - old_stop)
                    new_dist = abs(trade.entry_price - new_stop)
                    if new_dist > old_dist:
                        stop_widened = True

    if r < 0 and r < -1.0 and stop_widened:
        result["stop_quality"] = "too_wide"
    else:
        result["stop_quality"] = "optimal"

    # Target quality
    if r > 0:
        if r >= 2.0:
            result["target_quality"] = "hit_target"
        else:
            result["target_quality"] = "partial_exit_good"
    else:
        result["target_quality"] = None

    # Context
    result["streak_position"] = streak_position
    result["routine_psych_avg"] = routine.psych_average if routine else None
    result["rules_followed"] = trade.review.followed_rules if trade.review else None

    # Classification
    if r > 0:
        result["classification"] = "runner" if r >= 2.0 else "expected_win"
    else:
        # Priority: stop_too_wide > thesis_invalid > expected_loss
        if r < -1.0 and stop_widened:
            result["classification"] = "stop_too_wide"
        elif result["setup_avg_r"] is not None and result["setup_avg_r"] < 0 and len(closed_setup_trades) >= 5:
            result["classification"] = "thesis_invalid"
        else:
            result["classification"] = "expected_loss"

    return result
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/analysis/postmortem.py
git commit -m "feat: add PostmortemClassifier with bet/trade decision trees"
```

---

## Chunk 3: Service Layer + Settlement Integration

### Task 4: Create PostmortemService

**Files:**
- Create: `backend/src/services/postmortem_service.py`

- [ ] **Step 1: Create the service**

```python
"""
Postmortem Service — orchestrates classification, stores results.

Triggered inline after settlement (synchronous, non-blocking on failure)
or via manual recompute endpoint.
"""

import logging
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..analysis.postmortem import classify_bet, classify_trade, CURRENT_ALGO_VERSION
from ..db.models import Bet, Trade, DailyRoutine, TradeEvent
from ..repositories.postmortem_repo import PostmortemRepo

logger = logging.getLogger(__name__)

_recompute_lock = threading.Lock()


class PostmortemService:
    """Orchestrates postmortem computation and storage."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = PostmortemRepo(db)

    def compute_bet(self, bet: Bet) -> dict | None:
        """Compute and store postmortem for a single settled bet."""
        if bet.result not in ("won", "lost"):
            return None

        try:
            bankroll = bet.profile.bankroll if bet.profile else None
            fields = classify_bet(bet, profile_bankroll=bankroll)
            self.repo.upsert_bet_pm(bet.id, **fields)
            return fields
        except Exception as e:
            logger.warning(f"Postmortem failed for bet {bet.id}: {e}")
            return None

    def compute_trade(self, trade: Trade) -> dict | None:
        """Compute and store postmortem for a single closed trade."""
        if trade.state not in ("closed", "reviewed"):
            return None

        try:
            # Gather context
            setup_trades = (
                self.db.query(Trade)
                .filter(Trade.setup_type == trade.setup_type, Trade.account_id == trade.account_id)
                .all()
            )

            # Streak position
            recent_trades = (
                self.db.query(Trade)
                .filter(
                    Trade.account_id == trade.account_id,
                    Trade.state.in_(["closed", "reviewed"]),
                    Trade.closed_at < trade.closed_at,
                )
                .order_by(Trade.closed_at.desc())
                .limit(20)
                .all()
            )
            streak = 0
            for t in recent_trades:
                if t.r_multiple is not None and t.r_multiple < 0:
                    streak -= 1
                else:
                    break

            # Routine
            routine = None
            if trade.daily_routine_id:
                routine = self.db.query(DailyRoutine).filter(DailyRoutine.id == trade.daily_routine_id).first()

            # Trade events
            events = self.db.query(TradeEvent).filter(TradeEvent.trade_id == trade.id).all()

            fields = classify_trade(trade, setup_trades, streak, routine, events)
            self.repo.upsert_trade_pm(trade.id, **fields)
            return fields
        except Exception as e:
            logger.warning(f"Postmortem failed for trade {trade.id}: {e}")
            return None

    def recompute_all_bets(self, profile_id: int) -> int:
        """Recompute all bet postmortems for a profile. Returns count."""
        bets = self.repo.get_uncomputed_bets(profile_id, CURRENT_ALGO_VERSION)
        count = 0
        for bet in bets:
            if self.compute_bet(bet):
                count += 1
        self.db.commit()
        return count

    def recompute_all_trades(self, account_id: int) -> int:
        """Recompute all trade postmortems for an account. Returns count."""
        trades = self.repo.get_uncomputed_trades(account_id, CURRENT_ALGO_VERSION)
        count = 0
        for trade in trades:
            if self.compute_trade(trade):
                count += 1
        self.db.commit()
        return count

    @staticmethod
    def try_acquire_recompute_lock() -> bool:
        """Try to acquire the recompute lock. Returns False if already running."""
        return _recompute_lock.acquire(blocking=False)

    @staticmethod
    def release_recompute_lock():
        """Release the recompute lock."""
        _recompute_lock.release()
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/services/postmortem_service.py
git commit -m "feat: add PostmortemService for classification orchestration"
```

---

### Task 5: Hook into settle_bet()

**Files:**
- Modify: `backend/src/services/bet_service.py` (after line 285, before the return statement at line 287)

- [ ] **Step 1: Add postmortem compute call after settlement logic**

Insert before `return {` at line 287:

```python
        # Compute postmortem (synchronous, non-critical)
        try:
            from .postmortem_service import PostmortemService
            PostmortemService(self.db).compute_bet(bet)
        except Exception as e:
            logger.warning(f"Postmortem compute failed for bet {bet_id}: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/services/bet_service.py
git commit -m "feat: hook postmortem computation into settle_bet()"
```

---

### Task 6: Hook into close_trade()

**Files:**
- Modify: `backend/src/services/trading_service.py` (before `self.db.commit()` at line 351)

- [ ] **Step 1: Add postmortem compute call BEFORE the commit in close_trade()**

Insert before `self.db.commit()` (line 351), so the postmortem is committed in the same transaction:

```python
        # Compute postmortem (synchronous, non-critical)
        try:
            from .postmortem_service import PostmortemService
            PostmortemService(self.db).compute_trade(trade)
        except Exception as e:
            logger.warning(f"Postmortem compute failed for trade {trade_id}: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/services/trading_service.py
git commit -m "feat: hook postmortem computation into close_trade()"
```

---

## Chunk 3: Pattern Detection Engine

### Task 7: Create PatternDetector

**Files:**
- Create: `backend/src/analysis/patterns.py`

- [ ] **Step 1: Create the pattern detection module**

```python
"""
Pattern Detection Engine — segments postmortem data and surfaces insights.

Operates on pre-computed postmortem rows joined with bets/trades.
Returns a list of pattern dicts, each with: rule, severity, message, segment, sample_size.
"""

import logging
from collections import defaultdict

from ..db.models import Bet, Trade, BetPostmortem, TradePostmortem

logger = logging.getLogger(__name__)

MIN_BET_SAMPLE = 10
MIN_TRADE_SAMPLE = 5


def detect_bet_patterns(rows: list[tuple[Bet, BetPostmortem]]) -> list[dict]:
    """
    Detect patterns from bet postmortem data.

    Args:
        rows: List of (Bet, BetPostmortem) tuples from PostmortemRepo.

    Returns:
        List of pattern insights, sorted by severity.
    """
    if len(rows) < MIN_BET_SAMPLE:
        return []

    patterns = []

    # Build segments
    segments = {
        "market": defaultdict(list),
        "provider": defaultdict(list),
        "sport": defaultdict(list),
        "edge_band": defaultdict(list),
        "odds_range": defaultdict(list),
        "ttk_band": defaultdict(list),
        "day_of_week": defaultdict(list),
        "classification": defaultdict(list),
    }

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for bet, pm in rows:
        segments["market"][bet.market].append((bet, pm))
        segments["provider"][bet.provider_id].append((bet, pm))

        # Edge band
        edge = pm.edge_at_placement
        if edge is not None:
            if edge < 2:
                segments["edge_band"]["<2%"].append((bet, pm))
            elif edge < 5:
                segments["edge_band"]["2-5%"].append((bet, pm))
            elif edge < 10:
                segments["edge_band"]["5-10%"].append((bet, pm))
            else:
                segments["edge_band"]["10%+"].append((bet, pm))

        # Odds range
        if bet.odds < 1.5:
            segments["odds_range"]["<1.5"].append((bet, pm))
        elif bet.odds < 2.5:
            segments["odds_range"]["1.5-2.5"].append((bet, pm))
        elif bet.odds < 4.0:
            segments["odds_range"]["2.5-4.0"].append((bet, pm))
        else:
            segments["odds_range"]["4.0+"].append((bet, pm))

        # TTK band (time to kickoff at placement)
        if bet.start_time and bet.placed_at:
            from datetime import timedelta
            ttk = bet.start_time - bet.placed_at
            if ttk <= timedelta(hours=6):
                segments["ttk_band"]["<6h"].append((bet, pm))
            elif ttk <= timedelta(hours=24):
                segments["ttk_band"]["6-24h"].append((bet, pm))
            elif ttk <= timedelta(hours=48):
                segments["ttk_band"]["24-48h"].append((bet, pm))
            else:
                segments["ttk_band"]["48h+"].append((bet, pm))

        # Day of week
        if bet.placed_at:
            dow = bet.placed_at.weekday()
            segments["day_of_week"][day_names[dow]].append((bet, pm))

        segments["classification"][pm.classification].append((bet, pm))

        # Sport (from event — ensure joinedload used in repo query)
        if bet.event and bet.event.sport:
            segments["sport"][bet.event.sport].append((bet, pm))

    # Check each segment for patterns
    for dim_name, dim_segments in segments.items():
        for seg_key, seg_rows in dim_segments.items():
            if len(seg_rows) < MIN_BET_SAMPLE:
                continue

            total_stake = sum(b.stake for b, _ in seg_rows)
            total_profit = sum(b.profit for b, _ in seg_rows)
            roi = (total_profit / total_stake * 100) if total_stake > 0 else 0
            wins = sum(1 for b, _ in seg_rows if b.result == "won")
            win_rate = wins / len(seg_rows) * 100

            # Losing segment
            if roi < -10:
                patterns.append({
                    "rule": "losing_segment",
                    "severity": "red",
                    "message": f"{dim_name}={seg_key} has {roi:+.1f}% ROI over {len(seg_rows)} bets (win rate {win_rate:.0f}%)",
                    "segment": f"{dim_name}:{seg_key}",
                    "sample_size": len(seg_rows),
                    "roi": roi,
                })

            # Winning segment
            if roi > 5:
                patterns.append({
                    "rule": "winning_segment",
                    "severity": "green",
                    "message": f"{dim_name}={seg_key} has {roi:+.1f}% ROI over {len(seg_rows)} bets (win rate {win_rate:.0f}%)",
                    "segment": f"{dim_name}:{seg_key}",
                    "sample_size": len(seg_rows),
                    "roi": roi,
                })

    # Edge erosion hotspot: >=40% of losses in a segment are edge_erosion
    for dim_name in ("provider", "market"):
        for seg_key, seg_rows in segments[dim_name].items():
            losses = [(b, pm) for b, pm in seg_rows if b.result == "lost"]
            if len(losses) < MIN_BET_SAMPLE:
                continue
            erosion_count = sum(1 for _, pm in losses if pm.classification == "edge_erosion")
            if erosion_count / len(losses) >= 0.4:
                patterns.append({
                    "rule": "edge_erosion_hotspot",
                    "severity": "amber",
                    "message": f"{dim_name}={seg_key}: {erosion_count}/{len(losses)} losses are edge erosion ({erosion_count/len(losses)*100:.0f}%)",
                    "segment": f"{dim_name}:{seg_key}",
                    "sample_size": len(losses),
                })

    # False edge concentration: >=30% of losses are false_edge
    for dim_name in ("provider", "market"):
        for seg_key, seg_rows in segments[dim_name].items():
            losses = [(b, pm) for b, pm in seg_rows if b.result == "lost"]
            if len(losses) < MIN_BET_SAMPLE:
                continue
            false_count = sum(1 for _, pm in losses if pm.classification == "false_edge")
            if false_count / len(losses) >= 0.3:
                patterns.append({
                    "rule": "false_edge_concentration",
                    "severity": "red",
                    "message": f"{dim_name}={seg_key}: {false_count}/{len(losses)} losses are false edge ({false_count/len(losses)*100:.0f}%)",
                    "segment": f"{dim_name}:{seg_key}",
                    "sample_size": len(losses),
                })

    # Sizing alert: >=3 sizing_error in trailing 30 days
    from datetime import datetime, timezone, timedelta
    cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)
    sizing_errors = [pm for b, pm in rows if pm.classification == "sizing_error" and b.placed_at and b.placed_at >= cutoff_30d]
    if len(sizing_errors) >= 3:
        patterns.append({
            "rule": "sizing_alert",
            "severity": "amber",
            "message": f"{len(sizing_errors)} bets flagged as oversized (Kelly ratio > 1.5×)",
            "segment": "all",
            "sample_size": len(sizing_errors),
        })

    # Sort: red first, then amber, then green
    severity_order = {"red": 0, "amber": 1, "green": 2}
    patterns.sort(key=lambda p: (severity_order.get(p["severity"], 9), -p["sample_size"]))

    return patterns


def detect_trade_patterns(rows: list[tuple[Trade, TradePostmortem]]) -> list[dict]:
    """
    Detect patterns from trade postmortem data.

    Args:
        rows: List of (Trade, TradePostmortem) tuples from PostmortemRepo.

    Returns:
        List of pattern insights.
    """
    if len(rows) < MIN_TRADE_SAMPLE:
        return []

    patterns = []

    # Build segments
    segments = {
        "setup_type": defaultdict(list),
        "instrument": defaultdict(list),
        "direction": defaultdict(list),
    }

    for trade, pm in rows:
        segments["setup_type"][trade.setup_type].append((trade, pm))
        segments["instrument"][trade.instrument].append((trade, pm))
        segments["direction"][trade.direction].append((trade, pm))

    # Setup underperformer
    for setup, seg_rows in segments["setup_type"].items():
        if len(seg_rows) < MIN_TRADE_SAMPLE:
            continue
        avg_r = sum(t.r_multiple or 0 for t, _ in seg_rows) / len(seg_rows)
        wins = sum(1 for t, _ in seg_rows if (t.r_multiple or 0) > 0)
        win_rate = wins / len(seg_rows) * 100
        if avg_r < 0:
            patterns.append({
                "rule": "setup_underperformer",
                "severity": "red",
                "message": f"Setup '{setup}' averages {avg_r:+.2f}R over {len(seg_rows)} trades (win rate {win_rate:.0f}%)",
                "segment": f"setup_type:{setup}",
                "sample_size": len(seg_rows),
            })
        elif avg_r > 0.5:
            patterns.append({
                "rule": "setup_performer",
                "severity": "green",
                "message": f"Setup '{setup}' averages {avg_r:+.2f}R over {len(seg_rows)} trades (win rate {win_rate:.0f}%)",
                "segment": f"setup_type:{setup}",
                "sample_size": len(seg_rows),
            })

    # Direction/instrument check
    for dim_name in ("instrument", "direction"):
        for seg_key, seg_rows in segments[dim_name].items():
            if len(seg_rows) < MIN_TRADE_SAMPLE:
                continue
            avg_r = sum(t.r_multiple or 0 for t, _ in seg_rows) / len(seg_rows)
            if avg_r < -0.3:
                patterns.append({
                    "rule": "losing_segment",
                    "severity": "red",
                    "message": f"{dim_name}={seg_key} averages {avg_r:+.2f}R over {len(seg_rows)} trades",
                    "segment": f"{dim_name}:{seg_key}",
                    "sample_size": len(seg_rows),
                })
            elif avg_r > 0.5:
                patterns.append({
                    "rule": "winning_segment",
                    "severity": "green",
                    "message": f"{dim_name}={seg_key} averages {avg_r:+.2f}R over {len(seg_rows)} trades",
                    "segment": f"{dim_name}:{seg_key}",
                    "sample_size": len(seg_rows),
                })

    # Streak impact: win rate after 2+ consecutive losses vs baseline
    all_wins = sum(1 for t, _ in rows if (t.r_multiple or 0) > 0)
    baseline_wr = all_wins / len(rows) * 100 if rows else 0

    after_streak = [(t, pm) for t, pm in rows if pm.streak_position is not None and pm.streak_position <= -2]
    if len(after_streak) >= MIN_TRADE_SAMPLE:
        streak_wins = sum(1 for t, _ in after_streak if (t.r_multiple or 0) > 0)
        streak_wr = streak_wins / len(after_streak) * 100
        if abs(streak_wr - baseline_wr) > 15:
            patterns.append({
                "rule": "streak_impact",
                "severity": "red",
                "message": f"After 2+ consecutive losses, win rate drops to {streak_wr:.0f}% (vs {baseline_wr:.0f}% baseline) over {len(after_streak)} trades",
                "segment": "streak:>=2_losses",
                "sample_size": len(after_streak),
            })

    # Psych correlation: avg R by psych score band
    psych_bands = {"low (<6)": [], "high (>=7)": []}
    for trade, pm in rows:
        if pm.routine_psych_avg is not None:
            if pm.routine_psych_avg < 6:
                psych_bands["low (<6)"].append((trade, pm))
            elif pm.routine_psych_avg >= 7:
                psych_bands["high (>=7)"].append((trade, pm))

    if len(psych_bands["low (<6)"]) >= MIN_TRADE_SAMPLE and len(psych_bands["high (>=7)"]) >= MIN_TRADE_SAMPLE:
        low_r = sum(t.r_multiple or 0 for t, _ in psych_bands["low (<6)"]) / len(psych_bands["low (<6)"])
        high_r = sum(t.r_multiple or 0 for t, _ in psych_bands["high (>=7)"]) / len(psych_bands["high (>=7)"])
        if abs(high_r - low_r) > 0.5:
            patterns.append({
                "rule": "psych_correlation",
                "severity": "purple",
                "message": f"Psych score >=7 averages {high_r:+.2f}R vs {low_r:+.2f}R when <6 — {abs(high_r-low_r):.1f}R difference",
                "segment": "psych_score",
                "sample_size": len(psych_bands["low (<6)"]) + len(psych_bands["high (>=7)"]),
            })

    severity_order = {"red": 0, "amber": 1, "purple": 2, "green": 3}
    patterns.sort(key=lambda p: (severity_order.get(p["severity"], 9), -p.get("sample_size", 0)))

    return patterns
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/analysis/patterns.py
git commit -m "feat: add PatternDetector with bet/trade segmentation rules"
```

---

## Chunk 4: API Routes

### Task 8: Create postmortem API routes

**Files:**
- Create: `backend/src/api/routes/postmortem.py`
- Modify: `backend/src/api/routes/__init__.py`
- Modify: `backend/src/api/__init__.py`

- [ ] **Step 1: Create route file**

```python
"""Postmortem API routes — thin handlers delegating to PostmortemService."""

import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ...repositories import ProfileRepo
from ...repositories.postmortem_repo import PostmortemRepo
from ...services.postmortem_service import PostmortemService
from ...analysis.patterns import detect_bet_patterns, detect_trade_patterns

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/postmortem", tags=["postmortem"])


def _active_profile_id(db: Session) -> int | None:
    """Get the active profile ID."""
    profile = ProfileRepo(db).get_active()
    return profile.id if profile else None


@router.get("/bets")
def get_bet_postmortems(
    classification: str | None = None,
    market: str | None = None,
    provider: str | None = None,
    sport: str | None = None,
    db: Session = Depends(get_db),
):
    """Classified bets with optional filters. Scoped to active profile."""
    profile_id = _active_profile_id(db)
    if not profile_id:
        return {"postmortems": [], "count": 0}

    repo = PostmortemRepo(db)
    rows = repo.get_bet_pms_for_profile(profile_id)

    # Apply filters
    if classification:
        rows = [(b, pm) for b, pm in rows if pm.classification == classification]
    if market:
        rows = [(b, pm) for b, pm in rows if b.market == market]
    if provider:
        rows = [(b, pm) for b, pm in rows if b.provider_id == provider]
    if sport:
        rows = [(b, pm) for b, pm in rows if b.event and b.event.sport == sport]

    return {
        "postmortems": [
            {
                "bet_id": b.id,
                "provider": b.provider_id,
                "market": b.market,
                "outcome": b.outcome,
                "odds": b.odds,
                "stake": b.stake,
                "result": b.result,
                "profit": b.profit,
                "classification": pm.classification,
                "edge_at_placement": pm.edge_at_placement,
                "clv_pct": pm.clv_pct,
                "clv_confirmed": pm.clv_confirmed,
                "expected_win_pct": pm.expected_win_pct,
                "kelly_fraction": pm.kelly_fraction,
                "is_oversized": pm.is_oversized,
                "variance_score": pm.variance_score,
                "placed_at": b.placed_at.isoformat() if b.placed_at else None,
            }
            for b, pm in rows
        ],
        "count": len(rows),
    }


@router.get("/bets/summary")
def get_bet_summary(db: Session = Depends(get_db)):
    """Aggregate stats by classification."""
    profile_id = _active_profile_id(db)
    if not profile_id:
        return {"summary": [], "total": 0}

    repo = PostmortemRepo(db)
    rows = repo.get_bet_pms_for_profile(profile_id)

    from collections import defaultdict
    buckets = defaultdict(lambda: {"count": 0, "total_stake": 0.0, "total_profit": 0.0, "edge_sum": 0.0, "clv_sum": 0.0, "edge_count": 0, "clv_count": 0})

    for bet, pm in rows:
        b = buckets[pm.classification]
        b["count"] += 1
        b["total_stake"] += bet.stake
        b["total_profit"] += bet.profit
        if pm.edge_at_placement is not None:
            b["edge_sum"] += pm.edge_at_placement
            b["edge_count"] += 1
        if pm.clv_pct is not None:
            b["clv_sum"] += pm.clv_pct
            b["clv_count"] += 1

    summary = []
    for cls, b in buckets.items():
        summary.append({
            "classification": cls,
            "count": b["count"],
            "avg_edge": round(b["edge_sum"] / b["edge_count"], 2) if b["edge_count"] else None,
            "avg_clv": round(b["clv_sum"] / b["clv_count"], 2) if b["clv_count"] else None,
            "total_profit": round(b["total_profit"], 2),
            "roi": round(b["total_profit"] / b["total_stake"] * 100, 2) if b["total_stake"] > 0 else 0,
        })

    summary.sort(key=lambda s: s["count"], reverse=True)
    return {"summary": summary, "total": len(rows)}


@router.get("/bets/patterns")
def get_bet_patterns(db: Session = Depends(get_db)):
    """Auto-detected pattern insights for bets."""
    profile_id = _active_profile_id(db)
    if not profile_id:
        return {"patterns": []}

    repo = PostmortemRepo(db)
    rows = repo.get_bet_pms_for_profile(profile_id)
    patterns = detect_bet_patterns(rows)
    return {"patterns": patterns}


@router.get("/trades")
def get_trade_postmortems(
    classification: str | None = None,
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Classified trades with optional filters. Scoped to active trading account."""
    if not account_id:
        return {"postmortems": [], "count": 0}

    repo = PostmortemRepo(db)
    rows = repo.get_trade_pms_for_account(account_id)

    if classification:
        rows = [(t, pm) for t, pm in rows if pm.classification == classification]

    return {
        "postmortems": [
            {
                "trade_id": t.id,
                "instrument": t.instrument,
                "direction": t.direction,
                "setup_type": t.setup_type,
                "r_multiple": pm.r_multiple,
                "classification": pm.classification,
                "setup_avg_r": pm.setup_avg_r,
                "setup_win_rate": pm.setup_win_rate,
                "stop_quality": pm.stop_quality,
                "target_quality": pm.target_quality,
                "streak_position": pm.streak_position,
                "routine_psych_avg": pm.routine_psych_avg,
                "rules_followed": pm.rules_followed,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            }
            for t, pm in rows
        ],
        "count": len(rows),
    }


@router.get("/trades/summary")
def get_trade_summary(account_id: int, db: Session = Depends(get_db)):
    """Aggregate stats by classification for trades."""
    repo = PostmortemRepo(db)
    rows = repo.get_trade_pms_for_account(account_id)

    from collections import defaultdict
    buckets = defaultdict(lambda: {"count": 0, "r_sum": 0.0, "pnl_sum": 0.0})

    for trade, pm in rows:
        b = buckets[pm.classification]
        b["count"] += 1
        b["r_sum"] += pm.r_multiple or 0
        b["pnl_sum"] += trade.realized_pnl or 0

    summary = []
    for cls, b in buckets.items():
        summary.append({
            "classification": cls,
            "count": b["count"],
            "avg_r": round(b["r_sum"] / b["count"], 2) if b["count"] else 0,
            "total_pnl": round(b["pnl_sum"], 2),
        })

    summary.sort(key=lambda s: s["count"], reverse=True)
    return {"summary": summary, "total": len(rows)}


@router.get("/trades/patterns")
def get_trade_patterns(account_id: int, db: Session = Depends(get_db)):
    """Auto-detected pattern insights for trades."""
    repo = PostmortemRepo(db)
    rows = repo.get_trade_pms_for_account(account_id)
    patterns = detect_trade_patterns(rows)
    return {"patterns": patterns}


@router.post("/recompute")
def recompute_postmortems(
    profile_id: int | None = None,
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Force recompute all postmortems. Returns 409 if already running."""
    if not PostmortemService.try_acquire_recompute_lock():
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="Recompute already in progress")

    try:
        svc = PostmortemService(db)
        bet_count = 0
        trade_count = 0

        if profile_id:
            bet_count = svc.recompute_all_bets(profile_id)
        elif not account_id:
            # Default: use active profile
            pid = _active_profile_id(db)
            if pid:
                bet_count = svc.recompute_all_bets(pid)

        if account_id:
            trade_count = svc.recompute_all_trades(account_id)

        return {"bets_recomputed": bet_count, "trades_recomputed": trade_count}
    finally:
        PostmortemService.release_recompute_lock()
```

- [ ] **Step 2: Register the router in routes `__init__.py`**

Add to `backend/src/api/routes/__init__.py`:

```python
from .postmortem import router as postmortem_router
```

And add `'postmortem_router'` to `__all__`.

- [ ] **Step 3: Register in api `__init__.py`**

Add to imports (line 28-46):
```python
postmortem_router,
```

Add to include_router block (after line 350):
```python
app.include_router(postmortem_router)
```

- [ ] **Step 4: Verify API starts**

Run: `cd backend && python -c "from src.api import app; print('OK')"`
Expected: `OK` with no import errors.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/postmortem.py backend/src/api/routes/__init__.py backend/src/api/__init__.py
git commit -m "feat: add postmortem API routes (bets + trades + patterns + recompute)"
```

---

## Chunk 5: Frontend — PostmortemPage Tab

### Task 9: Add PostmortemPage tab to frontend

**Files:**
- Create: `frontend/src/components/Terminal/pages/PostmortemPage.tsx`
- Modify: `frontend/src/components/Terminal/Sidebar.tsx` (add to TabName type)
- Modify: `frontend/src/components/Terminal/TabBar.tsx` (add tab to SPORTS_TABS)
- Modify: `frontend/src/components/Terminal/TerminalWindow.tsx` (add case + lazy import)
- Modify: `frontend/src/services/api.ts` (add API calls)

- [ ] **Step 1: Add API methods to `api.ts`**

Add after the trading section:

```typescript
  // ============ Postmortem ============

  async getPostmortemBets(filters?: { classification?: string; market?: string; provider?: string }): Promise<{ postmortems: any[]; count: number }> {
    const params = new URLSearchParams();
    if (filters?.classification) params.set('classification', filters.classification);
    if (filters?.market) params.set('market', filters.market);
    if (filters?.provider) params.set('provider', filters.provider);
    return fetchJson(`/postmortem/bets?${params}`);
  },

  async getPostmortemBetsSummary(): Promise<{ summary: any[]; total: number }> {
    return fetchJson('/postmortem/bets/summary');
  },

  async getPostmortemBetsPatterns(): Promise<{ patterns: any[] }> {
    return fetchJson('/postmortem/bets/patterns');
  },

  async getPostmortemTrades(accountId: number, classification?: string): Promise<{ postmortems: any[]; count: number }> {
    const params = new URLSearchParams();
    params.set('account_id', String(accountId));
    if (classification) params.set('classification', classification);
    return fetchJson(`/postmortem/trades?${params}`);
  },

  async getPostmortemTradesSummary(accountId: number): Promise<{ summary: any[]; total: number }> {
    return fetchJson(`/postmortem/trades/summary?account_id=${accountId}`);
  },

  async getPostmortemTradesPatterns(accountId: number): Promise<{ patterns: any[] }> {
    return fetchJson(`/postmortem/trades/patterns?account_id=${accountId}`);
  },

  async recomputePostmortems(profileId?: number, accountId?: number): Promise<{ bets_recomputed: number; trades_recomputed: number }> {
    const params = new URLSearchParams();
    if (profileId) params.set('profile_id', String(profileId));
    if (accountId) params.set('account_id', String(accountId));
    return fetchJson(`/postmortem/recompute?${params}`, { method: 'POST' });
  },
```

- [ ] **Step 2: Add `'postmortem'` to TabName in `Sidebar.tsx`**

Change line 3:
```typescript
export type TabName = 'value' | 'dutch' | 'reverse' | 'polymarket' | 'stats' | 'bankroll' | 'profiles' | 'settings' | 'tradingIntraday' | 'tradingBankroll' | 'tradingStats' | 'postmortem';
```

- [ ] **Step 3: Add tab to SPORTS_TABS in `TabBar.tsx`**

Add after the `stats` entry (line 16):
```typescript
  { name: 'postmortem', label: 'PM', color: '#14B8A6' },
```

- [ ] **Step 4: Create PostmortemPage component**

Create `frontend/src/components/Terminal/pages/PostmortemPage.tsx`. This is a large file — the implementer should follow the existing page patterns (BetsPage, TradingStatsPage) and include:

1. Summary cards row (total settled, % expected losses, % false edge, % sizing errors)
2. Classification breakdown table (classification, count, avg edge%, avg CLV%, total P/L, ROI)
3. Pattern insights list (color-coded by severity: red ▼, amber ●, green ▲)
4. FilterBar with MultiSelectDropdown for classification, market, provider

Use `api.getPostmortemBetsSummary()` for summary data, `api.getPostmortemBetsPatterns()` for insights, `api.getPostmortemBets()` for the detail table.

Follow the retro terminal style with `border-2 border-border`, `text-muted`, `font-mono`, compact `sq` class tables.

- [ ] **Step 5: Add lazy import + route in `TerminalWindow.tsx`**

Add lazy import (after line 19):
```typescript
const PostmortemPage = lazy(() => import('./pages/PostmortemPage').then(m => ({ default: m.PostmortemPage })));
```

Add case in `renderPage()` switch (after `case 'stats':` block):
```typescript
      case 'postmortem':
        return <PostmortemPage />;
```

- [ ] **Step 6: Verify the page renders**

Start dev servers and navigate to the PM tab. Verify it loads without errors (will show empty state if no settled bets with postmortems).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/services/api.ts frontend/src/components/Terminal/Sidebar.tsx frontend/src/components/Terminal/TabBar.tsx frontend/src/components/Terminal/TerminalWindow.tsx frontend/src/components/Terminal/pages/PostmortemPage.tsx
git commit -m "feat: add PostmortemPage tab with summary, breakdown, and patterns"
```

---

### Task 10: Add postmortem section to TradingStatsPage

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingStatsPage.tsx`

- [ ] **Step 1: Add postmortem section to TradingStatsPage**

Add a new section at the bottom of the page that shows:

1. Summary cards (closed trades, % expected losses, % stop issues, psych correlation %)
2. Pattern insights (streak impact, setup performance, psych correlation, direction bias)

Use `api.getPostmortemTradesSummary(accountId)` and `api.getPostmortemTradesPatterns(accountId)` to fetch data. The section should only render when data is available (graceful degradation if no postmortem rows exist).

Follow the same card/table patterns used in the existing stats section above it.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingStatsPage.tsx
git commit -m "feat: add postmortem section to TradingStatsPage"
```

---

## Chunk 6: End-to-End Verification

### Task 11: Verify full pipeline

- [ ] **Step 1: Seed test data by settling a bet via API**

Use an existing pending bet or create one, then settle it:
```bash
curl -X PUT "http://localhost:8000/api/bets/{bet_id}" -H "Content-Type: application/json" -d '{"result": "lost", "payout": 0}'
```

- [ ] **Step 2: Verify postmortem was created**

Query SQLite:
```sql
SELECT * FROM bet_postmortems ORDER BY computed_at DESC LIMIT 5;
```

- [ ] **Step 3: Verify API returns data**

```bash
curl "http://localhost:8000/api/postmortem/bets/summary"
curl "http://localhost:8000/api/postmortem/bets/patterns"
```

- [ ] **Step 4: Verify frontend displays data**

Open the PM tab and verify summary cards and pattern insights render.

- [ ] **Step 5: Test recompute endpoint**

```bash
curl -X POST "http://localhost:8000/api/postmortem/recompute"
```

Verify response shows recomputed count.

- [ ] **Step 6: Final commit if any fixes needed**

```bash
git add -A && git commit -m "fix: postmortem end-to-end verification fixes"
```
