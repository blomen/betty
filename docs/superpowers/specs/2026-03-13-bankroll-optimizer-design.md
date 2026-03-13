# Bankroll Optimizer — Design Spec

**Date:** 2026-03-13
**Status:** Draft

## Context

BankrollBBQ compares odds across 40+ sportsbooks to find value bets. Each provider offers deposit bonuses (matched deposits, freebets) with wagering requirements. The current system tracks bonus progress but doesn't optimize the strategic layer: which providers to deposit at first, how to sequence bonus clearing, where to route bets, and when to redeploy capital.

The user's strategy is pure +EV — never sacrifice edge for wagering progress. But when multiple providers offer similar edges, the system should intelligently route to the provider that benefits most from the turnover. Beyond tiebreaking, the system should plan the entire bonus lifecycle: initial capital allocation, bonus sequencing, fund redeployment, and continuous re-optimization as the situation evolves.

**Goal:** Maximize long-term bankroll growth by optimally deploying capital across providers, accounting for bonus value, wagering requirements, limitation risk, and edge availability — all while never compromising on +EV.

## Design Decisions (from brainstorming)

- **Always max EV** — never lower min_edge for wagering. Pure +EV is non-negotiable.
- **Smart tiebreaker** — same edge ±0.5% → prefer provider needing wagering.
- **Freebets on highest edge** — no hedging/dutching. Freebet EV = (odds-1) × fair_prob.
- **Full auto bonus activation** — deposit triggers auto-setup from providers.yaml.
- **Rolling horizon planning** — no fixed episode endpoint. Agent continuously re-plans from current state.
- **Monte Carlo planning** (not pre-trained RL) — simulate forward from actual state, pick best action.
- **DB seeds simulator** — historical odds/edges/bets provide realistic distributions.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     CURRENT STATE (Real Data)                │
│  Provider balances, wagering progress, M2 limitation risk,   │
│  available edges (latest scan), bonus configs, bet history   │
└──────────────────────────┬──────────────────────────────────┘
                           │ snapshot
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   SIMULATOR (Forward Engine)                  │
│  Simulates N trajectories forward from current state         │
│  Models: edge arrivals, wagering, limitations, fund flows    │
│  Uses: historical distributions, M2 risk curves, bonus cfg   │
└──────────────────────────┬──────────────────────────────────┘
                           │ trajectories
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              MONTE CARLO PLANNER (Decision Engine)           │
│  Evaluates candidate actions via simulated trajectories      │
│  Picks action with highest expected terminal bankroll        │
│  Outputs: routing priority, deposit plan, transfer plan      │
└──────────────────────────┬──────────────────────────────────┘
                           │ recommendations
                           ▼
┌─────────────────────────────────────────────────────────────┐
│               INTEGRATION (Existing Pipeline)                │
│  Scanner re-ranks opportunities by routing priority          │
│  StakeCalculator unchanged (M8 Adaptive Kelly)               │
│  BankrollService auto-activates bonuses on deposit           │
│  New endpoint: GET /api/bankroll/plan                         │
└─────────────────────────────────────────────────────────────┘
```

## Component 1: Simulator

**File:** `backend/src/bankroll/simulator.py`

### State Representation

```python
@dataclass
class ProviderSimState:
    provider_id: str
    balance: float
    deposited: float
    bonus_status: str        # not_started | trigger | wagering | cleared | limited | expired
    bonus_type: str          # freebet | bonusdeposit
    bonus_amount: float
    wagered_amount: float
    wagering_requirement: float
    min_odds: float
    limitation_risk: float   # 0-1, from M2
    bets_placed: int
    days_active: int
    days_until_expiry: int   # Bonus deadline countdown
    freebet_available: bool
    trigger_settled: bool    # For two-phase: has trigger bet settled?

@dataclass
class SimState:
    day: int
    total_wealth: float         # All money including withdrawn (terminal metric)
    deployable_capital: float   # Cash available for Kelly sizing (excludes withdrawn)
    undeployed_capital: float   # Cash not yet deposited anywhere
    providers: dict[str, ProviderSimState]
    withdrawn: float            # Cumulative withdrawals (locked in, not at risk)
```

### Status Mapping (DB → Simulator)

The DB uses different status names than the simulator. Translation on snapshot:

| DB Status (`ProfileProviderBonus`) | Sim Status | Notes |
|---|---|---|
| `available` | `not_started` | Bonus exists but not activated |
| `trigger_needed` | `trigger` | Deposit made, trigger bet needed |
| `freebet_available` | `wagering` with `freebet_available=True` | Trigger settled, freebet ready |
| `in_progress` | `wagering` | Main wagering phase |
| `completed` | `cleared` | Bonus fully cleared |
| `claimed` | `cleared` | Already used, skip |
| (no DB equivalent) | `limited` | Inferred from M2 risk > threshold OR `ProviderRiskProfile.is_on_cooldown` |
| (no DB equivalent) | `expired` | `days_until_expiry <= 0` |

### Limitation Risk Model (Simplified for Simulation)

Instead of calling the full M2 model (which requires features not available in simulation), the simulator uses a simplified logistic limitation model calibrated from M2 outputs:

```python
def sim_limitation_prob(bets_placed: int, days_active: int, provider_type: str) -> float:
    """Simplified daily limitation probability for simulation.

    Calibrated from M2 predictions on historical data:
    - Fit logistic curve to M2 outputs vs (bets_placed, days_active)
    - Provider types: 'aggressive' (limits at ~30 bets), 'moderate' (~100), 'lenient' (~300)
    """
    # Provider-type-specific logistic curves (pre-fitted from M2 historical outputs)
    params = LIMITATION_CURVES[provider_type]  # {midpoint, steepness}
    cumulative_risk = 1 / (1 + exp(-params.steepness * (bets_placed - params.midpoint)))
    # Convert cumulative risk to daily probability
    # P(limited on day d) = P(limited by day d) - P(limited by day d-1)
    return max(0, cumulative_risk - previous_cumulative_risk)
```

Calibration runs once at startup: query historical M2 predictions from `ml_features` table, fit logistic curves per provider group.

### Simulation Loop (per tick = 1 day)

```python
def simulate_day(state: SimState, action: Action, edge_sampler) -> SimState:
    # 1. Execute action (deposit, withdraw)
    apply_action(state, action)

    # 2. For each active provider:
    for p in state.providers.values():
        if p.bonus_status in ("limited", "not_started", "expired"):
            continue

        # Handle freebet trigger phase
        if p.bonus_status == "trigger" and not p.trigger_settled:
            # Simulate one trigger bet per day until settled
            # Trigger bets are qualifying bets (odds >= min_odds, stake >= bonus_amount)
            p.trigger_settled = True  # Simplified: settles in 1 day
            if p.bonus_type == "freebet":
                p.freebet_available = True
            p.bonus_status = "wagering"
            continue

        # Sample today's edge opportunities from historical distribution
        n_per_day = edge_sampler.get_daily_volume(p.provider_id)
        opportunities = edge_sampler.sample(p.provider_id, n_per_day)

        for opp in opportunities:
            if opp.edge < MIN_EDGE_PCT:
                continue
            if p.bonus_status == "wagering" and opp.odds < p.min_odds:
                continue  # Doesn't count toward wagering

            # Stochastic bet resolution (realistic variance for Monte Carlo)
            stake = kelly_stake(opp.edge, opp.odds, state.deployable_capital)
            stake = min(stake, p.balance)  # Can't bet more than provider balance
            if stake <= 0:
                continue

            fair_prob = 1 / opp.fair_odds
            if random() < fair_prob:
                # Win
                p.balance += stake * (opp.odds - 1)
            else:
                # Lose
                p.balance -= stake

            # Update wagering (counts regardless of win/loss)
            if p.bonus_status == "wagering" and opp.odds >= p.min_odds:
                p.wagered_amount += stake

            p.bets_placed += 1

        # 3. Roll limitation event
        lim_prob = sim_limitation_prob(p.bets_placed, p.days_active, p.provider_type)
        if random() < lim_prob:
            p.bonus_status = "limited"

        # 4. Check bonus completion
        if p.bonus_status == "wagering" and p.wagered_amount >= p.wagering_requirement:
            if p.bonus_type == "bonusdeposit":
                p.balance += p.bonus_amount  # Bonus payout on clearing
            p.bonus_status = "cleared"

        # 5. Check bonus expiration
        p.days_until_expiry -= 1
        if p.days_until_expiry <= 0 and p.bonus_status == "wagering":
            p.bonus_status = "expired"
            # Bonus forfeited, but balance (real money) remains

        p.days_active += 1

    # 6. Update totals
    provider_balances = sum(p.balance for p in state.providers.values())
    state.deployable_capital = state.undeployed_capital + provider_balances
    state.total_wealth = state.deployable_capital + state.withdrawn
    state.day += 1
    return state
```

### Edge Sampler

**File:** `backend/src/bankroll/edge_sampler.py`

Builds per-provider edge distributions from historical data:

```python
class EdgeSampler:
    def __init__(self, db_session):
        # Query historical opportunities grouped by provider
        # Build empirical distributions: edges_per_day, edge_magnitude, odds, fair_odds
        self.distributions = self._build_from_history(db_session)

    def get_daily_volume(self, provider_id: str) -> int:
        """Return average number of +EV opportunities per day for this provider."""
        dist = self.distributions.get(provider_id)
        if not dist:
            return 0
        # Sample from Poisson distribution centered on historical mean
        return np.random.poisson(dist.avg_opportunities_per_day)

    def sample(self, provider_id: str, n: int) -> list[SimOpportunity]:
        dist = self.distributions.get(provider_id)
        if not dist or n <= 0:
            return []
        # Sample n opportunities from empirical joint distribution (edge, odds, fair_odds)
        indices = np.random.randint(0, len(dist.historical_data), size=n)
        return [SimOpportunity(
            edge=dist.historical_data[i].edge,
            odds=dist.historical_data[i].odds,
            fair_odds=dist.historical_data[i].fair_odds,
        ) for i in indices]

    def _build_from_history(self, db_session) -> dict:
        """Query opportunities table joined with Pinnacle fair odds.

        Also queries bets table for actual throughput per provider per day.
        Falls back to MIN_FALLBACK_VOLUME if provider has no history.
        """
        ...
```

**Cold start fallback:** If a provider has no historical data, use the average distribution from providers of the same type (Kambi, Altenar, etc.) as a proxy. If no type match, use global average. Log a warning — simulation quality is degraded.

### Snapshot from Real State

```python
def snapshot_current_state(db_session, profile_id) -> SimState:
    """Build SimState from actual DB state."""
    profile = db_session.query(Profile).get(profile_id)
    balances = db_session.query(ProfileProviderBalance).filter_by(profile_id=profile_id).all()
    bonuses = db_session.query(ProfileProviderBonus).filter_by(profile_id=profile_id).all()
    risk_profiles = db_session.query(ProviderRiskProfile).filter_by(profile_id=profile_id).all()

    providers = {}
    for bal in balances:
        bonus = next((b for b in bonuses if b.provider_id == bal.provider_id), None)
        risk = next((r for r in risk_profiles if r.provider_id == bal.provider_id), None)

        providers[bal.provider_id] = ProviderSimState(
            provider_id=bal.provider_id,
            balance=bal.balance,
            deposited=bal.deposited or 0,
            bonus_status=_map_db_status(bonus),  # Uses mapping table above
            bonus_type=bonus.bonus_type if bonus else "bonusdeposit",
            bonus_amount=bonus.bonus_amount if bonus else 0,
            wagered_amount=bonus.wagered_amount if bonus else 0,
            wagering_requirement=bonus.wagering_requirement if bonus else 0,
            min_odds=bonus.min_odds if bonus else 1.80,
            limitation_risk=risk.risk_score if risk else 0,
            bets_placed=_count_bets(db_session, profile_id, bal.provider_id),
            days_active=_days_since_opened(bal),
            days_until_expiry=_days_remaining(bonus),
            freebet_available=(bonus.bonus_status == "freebet_available") if bonus else False,
            trigger_settled=(bonus.bonus_status != "trigger_needed") if bonus else True,
        )

    provider_balances = sum(p.balance for p in providers.values())
    undeployed = profile.bankroll - sum(p.deposited for p in providers.values())

    return SimState(
        day=0,
        total_wealth=provider_balances + max(0, undeployed),
        deployable_capital=provider_balances + max(0, undeployed),
        undeployed_capital=max(0, undeployed),
        providers=providers,
        withdrawn=profile.total_withdrawn or 0,
    )
```

## Component 2: Monte Carlo Planner

**File:** `backend/src/bankroll/planner.py`

### Action Space

```python
@dataclass
class Action:
    type: str               # DEPOSIT | ROUTE | WITHDRAW | USE_FREEBET | WAIT
    provider_id: str | None
    amount: float | None
    bet_id: str | None      # For USE_FREEBET
```

**Note:** TRANSFER removed from action space. In reality, transferring between providers requires withdraw (1-5 business days) + deposit. The planner models this as two separate actions: WITHDRAW on day N, DEPOSIT on day N+3 (average withdrawal processing time). The greedy policy handles this automatically.

### Action Candidates (Pruned)

```python
def generate_candidates(state: SimState, bonus_configs: dict) -> list[Action]:
    candidates = [Action(type="WAIT")]

    # DEPOSIT: only for not_started providers with undeployed capital
    for p in state.providers.values():
        if p.bonus_status == "not_started" and state.undeployed_capital > 0:
            cfg = bonus_configs.get(p.provider_id)
            if not cfg:
                continue
            deposit_amount = min(cfg.amount, state.undeployed_capital)
            # Respect provider minimum deposit
            if deposit_amount < cfg.get("min_deposit", 100):
                continue
            candidates.append(Action(type="DEPOSIT", provider_id=p.provider_id, amount=deposit_amount))

    # WITHDRAW: only for cleared/limited providers with balance > 0
    for p in state.providers.values():
        if p.bonus_status in ("cleared", "limited") and p.balance > 0:
            candidates.append(Action(type="WITHDRAW", provider_id=p.provider_id, amount=p.balance))

    return candidates
```

### Greedy Policy (Fully Specified)

```python
def _greedy_policy(self, state: SimState, bonus_configs: dict) -> Action:
    """Default policy for days 1..N in simulation.

    Simple rule-based policy that the planner uses for future days:
    1. Auto-withdraw from cleared/limited providers
    2. Auto-deposit at not_started providers (ordered by bonus_value / wagering_requirement ratio)
    3. WAIT otherwise (betting happens implicitly in simulate_day)
    """
    # Priority 1: Withdraw cleared funds (redeployable capital)
    for p in state.providers.values():
        if p.bonus_status in ("cleared", "limited") and p.balance > 0:
            return Action(type="WITHDRAW", provider_id=p.provider_id, amount=p.balance)

    # Priority 2: Deposit at next best provider
    if state.undeployed_capital > 0:
        not_started = [p for p in state.providers.values() if p.bonus_status == "not_started"]
        if not_started:
            # Rank by bonus value density: bonus_amount / wagering_requirement
            # Higher density = bonus clears faster relative to value received
            not_started.sort(
                key=lambda p: bonus_configs[p.provider_id].amount / max(1, bonus_configs[p.provider_id].wagering_requirement),
                reverse=True
            )
            best = not_started[0]
            cfg = bonus_configs[best.provider_id]
            amount = min(cfg.amount, state.undeployed_capital)
            return Action(type="DEPOSIT", provider_id=best.provider_id, amount=amount)

    # Priority 3: Nothing to do
    return Action(type="WAIT")
```

### Planning Loop

```python
class MonteCarloPlanner:
    def __init__(self, simulator, n_trajectories=1000, horizon_days=30):
        self.simulator = simulator
        self.n_trajectories = n_trajectories
        self.horizon_days = horizon_days
        self._plan_lock = asyncio.Lock()  # Prevent concurrent re-plans

    async def plan(self, current_state: SimState, bonus_configs: dict) -> PlannerRecommendation:
        async with self._plan_lock:  # Debounce concurrent triggers
            return await asyncio.to_thread(self._plan_sync, current_state, bonus_configs)

    def _plan_sync(self, current_state: SimState, bonus_configs: dict) -> PlannerRecommendation:
        candidates = generate_candidates(current_state, bonus_configs)
        results = {}

        for action in candidates:
            terminal_bankrolls = []
            for _ in range(self.n_trajectories):
                state = deepcopy(current_state)
                # Apply candidate action on day 0
                apply_action(state, action)
                # Simulate forward with greedy policy
                for day in range(self.horizon_days):
                    greedy_action = self._greedy_policy(state, bonus_configs)
                    self.simulator.simulate_day(state, greedy_action)
                terminal_bankrolls.append(state.total_wealth)

            results[action] = {
                "mean": np.mean(terminal_bankrolls),
                "std": np.std(terminal_bankrolls),
                "p10": np.percentile(terminal_bankrolls, 10),
            }

        # Pick action with highest mean terminal wealth
        best_action = max(results, key=lambda a: results[a]["mean"])
        best_stats = results[best_action]

        # Compute routing priority from urgency scoring
        routing_priority = self._compute_routing_priority(current_state, bonus_configs)

        return PlannerRecommendation(
            primary_action=best_action,
            routing_priority=routing_priority,
            simulated_growth=(best_stats["mean"] / current_state.total_wealth - 1) * 100,
            confidence=1 / (1 + best_stats["std"] / max(1, best_stats["mean"])),  # Sigmoid-bounded [0,1]
            downside_p10=best_stats["p10"],
            all_results=results,
            generated_at=datetime.utcnow(),
        )

    def _compute_routing_priority(self, state, configs) -> list[str]:
        """Rank active providers for bet routing tiebreaker."""
        active = [p for p in state.providers.values() if p.bonus_status == "wagering"]

        def urgency_score(p):
            remaining_pct = 1 - (p.wagered_amount / max(1, p.wagering_requirement))
            deadline_factor = 1 / max(1, p.days_until_expiry)  # Higher when deadline close
            lim_factor = p.limitation_risk  # Higher when about to be limited
            return remaining_pct * deadline_factor + lim_factor

        active.sort(key=urgency_score, reverse=True)
        return [p.provider_id for p in active]
```

### Output

```python
@dataclass
class PlannerRecommendation:
    primary_action: Action
    routing_priority: list[str]       # Provider ranking for bet routing
    simulated_growth: float           # Expected bankroll growth % over horizon
    confidence: float                 # 0-1, sigmoid-bounded
    downside_p10: float               # 10th percentile terminal wealth (risk metric)
    all_results: dict                 # Full results for all candidate actions
    generated_at: datetime
```

### Re-planning Triggers

```python
REPLAN_TRIGGERS = [
    "extraction_complete",     # New scan finished
    "bonus_status_changed",    # Cleared, expired, triggered
    "provider_limited",        # Limitation detected
    "deposit_made",            # Manual deposit
    "withdrawal_made",         # Manual withdrawal
    "periodic_6h",             # Fallback every 6 hours
]
```

Debounced via `_plan_lock` — if a plan is running when a trigger fires, the trigger waits for the current plan to complete, then runs one re-plan (not N queued re-plans).

## Component 3: Integration

### Scanner Re-ranking

**File:** `backend/src/analysis/scanner.py` (modify existing)

After `scan_value()` computes all opportunities:

```python
def _apply_routing_priority(self, opportunities: list[dict], recommendation: PlannerRecommendation) -> list[dict]:
    """Re-rank opportunities: similar edges → prefer routing_priority provider."""
    if not recommendation:
        return opportunities  # Fallback: no re-ranking

    priority_map = {p: i for i, p in enumerate(recommendation.routing_priority)}

    def sort_key(opp):
        edge = opp["edge_pct"]
        provider_rank = priority_map.get(opp["provider_id"], 999)
        # Primary: edge (descending). Secondary: continuous provider priority penalty
        # Provider rank only matters as a tiny tiebreaker — never overrides meaningful edge difference
        return (-edge + provider_rank * 0.001)

    return sorted(opportunities, key=sort_key)
```

### Auto Bonus Activation

**File:** `backend/src/services/bankroll_service.py` (modify existing)

In `deposit_with_bonus()`: remove confirmation step, auto-read bonus config from `providers.yaml`, create `ProfileProviderBonus` record immediately on deposit.

### New API Endpoints

**File:** `backend/src/api/routes/bankroll.py` (modify existing)

```python
@router.get("/plan")
async def get_bankroll_plan(profile_id: int, db: Session):
    """Get current planner recommendation (returns cached if fresh)."""
    planner_service = BankrollPlannerService(db)
    recommendation = planner_service.get_latest_recommendation(profile_id)
    if not recommendation:
        return {"status": "no_plan", "message": "No plan available. Trigger /plan/replan."}
    return recommendation.to_dict()

@router.post("/plan/replan")
async def trigger_replan(profile_id: int, background_tasks: BackgroundTasks, db: Session):
    """Trigger re-planning in background. Returns immediately."""
    planner_service = BankrollPlannerService(db)
    background_tasks.add_task(planner_service.run_planner, profile_id)
    return {"status": "replanning", "message": "Re-plan triggered in background."}
```

### Planner Service

**File:** `backend/src/services/planner_service.py` (new)

```python
class BankrollPlannerService:
    _cache: dict[int, PlannerRecommendation] = {}  # profile_id → latest recommendation
    CACHE_TTL = timedelta(hours=6)

    def __init__(self, db_session):
        self.db = db_session
        self.edge_sampler = EdgeSampler(db_session)
        self.simulator = BonusSimulator(self.edge_sampler)
        self.planner = MonteCarloPlanner(self.simulator)

    async def run_planner(self, profile_id: int) -> PlannerRecommendation:
        current_state = snapshot_current_state(self.db, profile_id)
        bonus_configs = load_bonus_configs()  # From providers.yaml
        recommendation = await self.planner.plan(current_state, bonus_configs)
        self._cache[profile_id] = recommendation
        return recommendation

    def get_latest_recommendation(self, profile_id: int) -> PlannerRecommendation | None:
        cached = self._cache.get(profile_id)
        if cached and datetime.utcnow() - cached.generated_at < self.CACHE_TTL:
            return cached
        return None
```

### Fallback Heuristic

When planner hasn't run or confidence is low:

```python
def fallback_routing(opportunities, active_bonuses):
    """Simple heuristic: max EV, tiebreak on wagering urgency."""
    # 1. Sort by edge (descending)
    # 2. Within similar edges, prefer provider with:
    #    a. Active wagering AND closest deadline
    #    b. Active wagering AND highest limitation risk (use before losing it)
    #    c. No preference (original order)
    bonus_map = {b.provider_id: b for b in active_bonuses}

    def sort_key(opp):
        edge = opp["edge_pct"]
        bonus = bonus_map.get(opp["provider_id"])
        urgency = 0
        if bonus and bonus.bonus_status == "in_progress":
            remaining = bonus.wagering_requirement - bonus.wagered_amount
            days_left = max(1, (bonus.expires_at - datetime.utcnow()).days) if bonus.expires_at else 60
            urgency = remaining / days_left  # Higher = more urgent
        return (-edge + urgency * 0.0001)  # Tiny tiebreaker

    return sorted(opportunities, key=sort_key)
```

## Component 4: Freebet Optimization

Integrated into the planner — when a freebet is available:

```python
def recommend_freebet_usage(self, state: SimState, current_opportunities: list) -> Action | None:
    """Find highest-edge opportunity at the freebet provider."""
    for p in state.providers.values():
        if not p.freebet_available:
            continue
        # Filter opportunities at this provider
        provider_opps = [o for o in current_opportunities if o["provider_id"] == p.provider_id]
        if not provider_opps:
            continue  # No opportunities right now, check again next scan
        # Pick highest edge
        best = max(provider_opps, key=lambda o: o["edge_pct"])
        return Action(type="USE_FREEBET", provider_id=p.provider_id, bet_id=best["id"])
    return None
```

## Files to Create/Modify

### New Files
| File | Purpose |
|------|---------|
| `backend/src/bankroll/simulator.py` | Forward simulation engine + state snapshot |
| `backend/src/bankroll/edge_sampler.py` | Historical edge distribution sampler |
| `backend/src/bankroll/planner.py` | Monte Carlo planner + action space + greedy policy |
| `backend/src/services/planner_service.py` | Planner orchestration + caching |

### Modified Files
| File | Change |
|------|--------|
| `backend/src/analysis/scanner.py` | Add `_apply_routing_priority()` after `scan_value()` |
| `backend/src/services/bankroll_service.py` | Auto-activate bonus on deposit (remove confirmation) |
| `backend/src/api/routes/bankroll.py` | Add `GET /plan` and `POST /plan/replan` endpoints |
| `backend/src/services/bet_service.py` | Trigger re-plan on bet settlement when bonus status changes |

### Existing Code to Reuse
| Component | File | What |
|-----------|------|------|
| M2 Limit Predictor | `backend/src/ml/models/limit_predictor.py` | Historical predictions for calibrating sim limitation curves |
| M8 Adaptive Kelly | `backend/src/ml/models/adaptive_kelly.py` | Stake sizing (unchanged) |
| Predictor singleton | `backend/src/ml/serving/predictor.py` | Model serving |
| Bonus configs | `backend/src/config/providers.yaml` | Bonus parameters |
| ProfileProviderBonus | `backend/src/db/models.py` | Wagering state tracking |
| BankrollService | `backend/src/services/bankroll_service.py` | Balance/bonus operations |
| ProfileRepo | `backend/src/repositories/profile_repo.py` | Bonus state management |
| StakeCalculator | `backend/src/bankroll/stake_calculator.py` | Kelly sizing (reuse as-is) |
| ProviderRiskProfile | `backend/src/db/models.py` | Limitation risk data |

## Verification

### Unit Tests
- Simulator: given fixed state + deterministic RNG seed, verify correct wagering/limitation/clearing/expiration behavior
- Simulator: verify bonus payout on clearing (bonusdeposit adds amount to balance)
- Simulator: verify balance constraint (can't bet more than provider balance)
- Planner: given fixed simulator output, verify action ranking logic
- Edge sampler: verify distributions match historical data shape
- Edge sampler: verify cold start fallback (no history → use type average)
- Status mapping: verify all DB statuses translate correctly to sim statuses

### Integration Test
1. Seed DB with historical odds/bets data
2. Run `snapshot_current_state()` → verify SimState matches DB
3. Run planner → verify recommendation is valid (action exists, routing priority covers active providers)
4. Verify scanner re-ranking applies routing priority correctly

### End-to-End Validation
1. Start with known state: 50,000 SEK, 5 providers with known bonus configs
2. Run planner → get recommendation
3. Verify recommendation makes intuitive sense (e.g., deposit at highest-bonus-value provider first)
4. Run `GET /api/bankroll/plan` → verify API returns recommendation
5. Run extraction → verify opportunities are re-ranked by routing priority
6. Simulate a bonus clearing → verify re-plan triggers automatically
7. Verify bonus expiration: set a provider to 1 day remaining, run sim, verify it expires

### Performance
- 1000 trajectories × 30 days × 5 active providers should complete in <10s
- Planner runs in background thread (`asyncio.to_thread`) — doesn't block event loop
- Profile with `cProfile` if slow — main bottleneck will be `deepcopy` of state per trajectory
- Optimization: use numpy arrays instead of dataclasses for hot loop if needed
