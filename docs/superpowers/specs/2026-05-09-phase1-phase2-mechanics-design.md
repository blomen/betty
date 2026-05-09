# Phase 1 / Phase 2 trade mechanics — design spec

**Date:** 2026-05-09
**Status:** approved (design phase)
**Context:** This week's trade audit (5/5 days red, -$20,700, 26.6% WR, 0/331 trails fired). Behavior change for paper-trading phase to generate clean labeled training data with maximum signal volume.

## Goal

Make every trade follow a deterministic two-phase state machine driven by zone-touch DQN re-evaluations:

- **Phase 1** — sacred bracket. From entry, the trade plays out untouched until either the dim-predicted stop or +1.5R is reached. No DQN re-evals, no orderflow exits, no per-tick interventions.
- **Phase 2** — zone-driven trail and flip. Entered when peak_R first reaches 1.5R. Stop is locked to a "barely profitable" level. The position rides from zone to zone; at each zone touch the DQN re-evaluates and either pyramids (CONT), waits (SKIP), or flips opposite (REV).

Combined with floor-zero entry gates, this generates a consistent stream of labeled (obs, action, realized_R) tuples for the training feedback loop while keeping the system mechanical and auditable.

## Current vs. target behavior

| Aspect | Current | Target |
|---|---|---|
| Phase 2 threshold | peak_R >= 2.0 ([level_monitor.py:1811](backend/src/market_data/level_monitor.py#L1811)) | peak_R >= 1.5 (matches BE-lock) |
| Locked-profit stop | entry ± 2 ticks at 1.5R BE-lock ([broker_adapter.py:370](backend/src/stocks/broker_adapter.py#L370)) | unchanged — 2 ticks already covers spread + commission |
| Phase 2 reversal exit | orderflow-driven (`reversal_signals.should_exit` at peak_R≥2.0) | DQN-driven only — fires only when DQN action=REV at a zone touch |
| Phase 2 cont-trail | exists, runs at peak_R >= 2.0 with `compute_zone_trail_target` | unchanged behavior, threshold lowered to 1.5R |
| Phase 2 pyramid | DQN `pyramid_decision.add_size` ([level_monitor.py:1864](backend/src/market_data/level_monitor.py#L1864)) | size = round(BASE × `size_multiplier(composite_confidence)`) — confidence-scaled, not from pyramid head |
| Phase 2 flip on REV | does not exist | flatten + fresh-open opposite direction at confidence-scaled size, new position re-enters Phase 1 |
| Per-tick `reversal_signals` exit | active in Phase 2 | disabled — only zone touches drive Phase 2 decisions |
| Per-tick `early_exit_lock` | active | disabled — same reason |
| Entry confidence floor | 0.05 (RECKLESS_LEARNING_MODE) | 0.0 |
| Entry orderflow floor | 0.30 in broker dispatch path ([level_monitor.py:1892](backend/src/market_data/level_monitor.py#L1892)) | 0.0 |
| Entry size | size_model output (drawdown-aware ML) | round(BASE × `size_multiplier(composite_confidence)`), BASE=1 |
| DQN SKIP | respected | unchanged — still respected |
| Stop-tick sanity bounds | 6 ≤ stop_ticks ≤ 40 (in SessionManager backtest only) | enforced in live path too — skip if dim_stop_ticks outside this range |
| FORCE_REV_ONLY | already disabled in live path 2026-04-28 | unchanged |

## State machine

```
        ┌─────────────────────────────┐
        │           FLAT              │
        └──────────────┬──────────────┘
                       │ zone touch + DQN(action ≠ SKIP)
                       │ + 6 ≤ stop_ticks ≤ 40
                       │ + composite confidence > 0
                       ▼
        ┌─────────────────────────────┐
        │   PHASE 1 (sacred bracket)  │
        │                             │
        │   stop = dim-predicted SL   │
        │   target = entry ± 1.5R     │
        │                             │
        │   No DQN re-eval.           │
        │   No orderflow exits.       │
        │   No per-tick lock/flip.    │
        └──┬───────────────────┬──────┘
           │ stop hit          │ peak_R >= 1.5
           ▼                   ▼
        FLAT          ┌────────────────────────────┐
                      │   PHASE 1 → PHASE 2        │
                      │  stop → entry ± 2 ticks    │
                      │  (BE-lock already exists)  │
                      └─────────────┬──────────────┘
                                    ▼
        ┌─────────────────────────────────────────┐
        │       PHASE 2 (zone-driven ride)        │
        │                                         │
        │  On zone touch in trade direction:      │
        │    DQN action = CONT →                  │
        │       pyramid (size = conf-scaled)      │
        │       trail stop behind touched zone    │
        │       target = next zone                │
        │       stay in Phase 2                   │
        │                                         │
        │    DQN action = REV →                   │
        │       flatten entire position           │
        │       fresh-open opposite direction at  │
        │       conf-scaled size                  │
        │       new position enters Phase 1       │
        │                                         │
        │    DQN action = SKIP →                  │
        │       hold; stop and target unchanged   │
        └────────────────┬────────────────────────┘
                         │ stop hit (now locked-profit) or EOD flatten
                         ▼
                       FLAT
```

## Phase 1 — sacred bracket

**Entry conditions** (zone touch with broker dispatch):

| Gate | Condition |
|---|---|
| DQN action | ≠ SKIP |
| Composite confidence | > 0.0 |
| Orderflow score | > 0.0 |
| Dim stop ticks | 6 ≤ stop_ticks ≤ 40 |
| Position state | flat |
| Halt flag | not set |

**Stop placement:** existing logic — zone boundary ± dim-predicted stop ticks. Stop sits on the broker as a working order.

**Target:** soft target at entry ± 1.5R. **No TP order placed on the broker.** The target is observational — when peak_R reaches 1.5, the BE-lock fires and Phase 2 begins. Price can continue past 1.5R; we want to ride that.

**Sizing:** `entry_size = max(1, round(BASE_SIZE × size_multiplier(composite_confidence)))` where `BASE_SIZE = 1`. The 1-contract floor prevents zero-size trades when confidence is very low (still want the data point).

**Sacred — no interventions:**
- The orderflow `reversal_signals.should_exit` path is gated on phase. It fires only in Phase 2.
- The `early_exit_lock` head is gated on phase. Disabled in Phase 1.
- Zone touches that occur during Phase 1 (e.g., price drifts back toward entry zone, or hits a different zone before reaching ±1.5R) do NOT trigger DQN re-eval. They are logged but ignored.

The only Phase 1 exits are: dim-predicted stop hit, EOD flatten, panic flatten via `/api/stocks/halt`.

## Phase 1 → Phase 2 transition

Triggered by `update_mark_and_check_be_lock` when `peak_R` first reaches 1.5. Existing implementation at [broker_adapter.py:362-419](backend/src/stocks/broker_adapter.py#L362-L419) is unchanged:

1. Set `tracker.locked_BE = True` (this flag now also acts as the phase=2 indicator).
2. Compute `target_stop = entry_price ± 2 ticks` (the "barely profitable" point — covers ~$10/contract = 1-tick spread + commission with buffer).
3. Modify the broker stop order to `target_stop`.
4. Update `_pending_trade["stop_price"]` synchronously so any race-window flatten records the correct locked-profit stop.

`tracker.locked_BE` is the canonical phase indicator. `locked_BE == False` → Phase 1; `locked_BE == True` → Phase 2.

## Phase 2 — zone-driven ride

Entered when `tracker.locked_BE` is set. The position stays open with a locked-profit stop until either a stop hit, an EOD flatten, or a Phase 2 decision flips/closes the position.

### Decision tree at each zone touch (Phase 2 only)

The handler runs in `_emit_zone_dqn_inference` after DQN inference, only when `not broker.tracker.is_flat AND tracker.locked_BE`.

```python
if dqn_action == "CONT" and dqn_direction == tracker.side:
    # 1. Pyramid add (confidence-scaled)
    add_size = max(1, round(BASE_SIZE * size_multiplier(composite_confidence)))
    broker.add_to_position(add_size, price)

    # 2. Trail stop behind the just-touched zone
    target_stop = compute_zone_trail_target(tracker, zone, all_zones, current_zone_R)
    if target_stop and not_relax(target_stop):
        broker.modify_stop(target_stop)

    # 3. Stay in Phase 2

elif dqn_action == "REV" and dqn_direction != tracker.side:
    # Flip — close all, fresh open opposite
    await broker.flatten("dqn_zone_reversal")
    await broker.on_signal({
        "action": "ENTER",
        "side": opposite(tracker.side_before_flatten),
        "confidence": composite_confidence,
        "stop_ticks": dim_predicted_stop_ticks,
        ...
    })
    # New position auto-enters Phase 1 (locked_BE=False on a fresh open)

elif dqn_action == "SKIP":
    # Hold — no adjustment to stop or position
    pass
```

### Pyramid sizing

Per user spec: confidence-scaled at every level. `size_multiplier(composite_confidence)` is computed at the zone touch using the current zone's features. Returns 0.3–1.5×.

```python
add_size = max(1, round(BASE_SIZE * size_multiplier(composite_confidence)))
```

The DQN `pyramid_decision` head's `add_size` output is **ignored** in favor of confidence-scaled sizing. Rationale: the user's directive is "rely on the dims" — composite_confidence already aggregates DQN q_spread + trigger GBT + zone quality + narrative + micro alignment. Adding another DQN head's sizing on top introduces noise the trainer can't disentangle.

The `pyramid_decision.should_add` boolean is also ignored — Phase 2 CONT signals from the action head are sufficient to trigger an add.

### Cont-trail (existing path)

`compute_zone_trail_target` already implements "trail stop to behind the touched zone's edge." Behavior unchanged. The orderflow-aware skip (`of < 0.3` → don't trail) is preserved — it's a useful conviction filter for the trail decision.

### Flip on REV

New behavior. Flow:

1. `await broker.flatten("dqn_zone_reversal")` — closes whole position at market.
2. Wait for flatten to confirm (broker_adapter publishes `tracker.is_flat = True`). This is async — the next zone-touch handler will see the flat tracker and proceed.
3. The zone-touch handler **does not** re-fire on the same tick. The natural next zone-touch event re-runs the FLAT entry path with the REV signal still hot. For atomic flip-and-reenter, we'd need to inline the entry — defer that to the implementation plan.

Risk: between flatten and next zone touch, price may move. The fresh entry will be at the new spot, with a fresh dim-predicted stop. Acceptable — the alternative (atomic flip with retained position size) was rejected by the user in favor of "close all, open fresh in opposite direction."

### Phase 2 disabled handlers

These handlers currently fire in Phase 2 and will be **gated off** under the new spec:

- `reversal_signals.should_exit` (orderflow-driven flatten) — replaced by DQN-driven REV at zones.
- `early_exit_lock` — not in the user's spec; chops winners.

These remain present in DQN output (no model change) but the live broker path ignores them. The training pool still labels them for future calibration work.

## Entry gates (floor-zero)

All numeric floors drop to 0:

| Gate | Old | New | File |
|---|---|---|---|
| `conf_floor` (reckless) | 0.05 | 0.0 | [level_monitor.py:1645](backend/src/market_data/level_monitor.py#L1645) |
| `of_floor` (reckless) | 0.0 (audit gate) but 0.30 in dispatch | 0.0 in both | [level_monitor.py:1649](backend/src/market_data/level_monitor.py#L1649), [level_monitor.py:1892-1903](backend/src/market_data/level_monitor.py#L1892-L1903) |
| `MIN_ENTRY_STOP_TICKS` | 6 (backtest only) | 6 (also in live) | new live check |
| `MAX_ENTRY_STOP_TICKS` | 40 (backtest only) | 40 (also in live) | new live check |
| DQN SKIP | respected | respected | unchanged |
| Halt flag | respected | respected | unchanged |
| `is_flat` for new entries | required | required (Phase 1 only — Phase 2 CONT is a pyramid add, not a new entry) | unchanged |

The two stop-tick bounds are new in the live path. Rationale per the user's recommendation discussion: a stop of <6 ticks (~3 NQ points) is below typical noise and produces a near-instant stop hit; >40 ticks is an unclear setup with a stop too wide to be informative. Both produce trades the model can't learn from. Hard skip outside this range — no fallback widening.

## Sizing changes

Replace the existing `size_model` ML output with a deterministic `size_multiplier(composite_confidence)` lookup:

| Confidence tier | Multiplier | Contracts (BASE=1) |
|---|---|---|
| ≥ 0.85 | 1.5 | 2 (rounded) |
| 0.70–0.85 | 1.0 | 1 |
| 0.50–0.70 | 0.6 | 1 (rounded up via floor) |
| 0.30–0.50 | 0.3 | 1 (rounded up via floor) |
| < 0.30 | 0.5 (reckless) | 1 (rounded up via floor) |

In practice: a composite ≥ 0.85 gets 2 contracts; everything else gets 1 contract. The floor of 1 contract ensures every approved entry produces a labeled training tuple. As BASE_SIZE scales up post-paper-phase, the multiplier produces meaningful differentiation (BASE=4 → tiers of 6/4/3/2/2).

The `size_model_v5.joblib` is **not deleted** — it stays in the model pool for future use. We just stop calling it from the live entry path.

Same formula applies to:
- Phase 1 entry size
- Phase 2 pyramid add size (computed at the zone touch, not at entry)
- Phase 2 REV-flip fresh entry size (computed at the zone where flip fires)

## Prerequisite: trail bug fix

**This spec assumes the trail bug is fixed first.** From the [trail-broken memo](C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\project_trail_dropped_fill_bug_2026_05_08.md):

> on_signal pre-populated tracker AFTER stop placement; entry fills (<100ms) raced ahead and got dropped "while flat". peak_R stuck at 0, BE-lock + trail never fire.

Without this fix, `peak_R` never advances, BE-lock never fires, and Phase 2 is unreachable. **The Phase 1 / Phase 2 spec is moot until the tracker is reliably populated before the entry fill arrives.**

Implementation plan must include the tracker pre-populate fix as the first task. Verification: a synthetic test where `update_mark` is called with a price 1.5R away from a freshly-opened position must fire the BE-lock and set `tracker.locked_BE = True`.

## Acceptance criteria

After deployment, verify:

1. **Phase 1 sacred:** No `reversal_signals` or `early_exit_lock` flatten reasons appear in `broker_trades.exit_reason` while `locked_BE = False`. Query: `SELECT exit_reason, COUNT(*) FROM broker_trades WHERE ts >= deploy_ts AND closed_at IS NOT NULL GROUP BY exit_reason`. Expect: only `STOP`, `EOD`, `MANUAL_HALT`, `DQN_ZONE_REVERSAL`, and Phase 2 `STOP` (locked-profit hit).

2. **Trail fires:** `trail_count > 0` for at least 30% of Phase 2 trades. Query: `SELECT COUNT(*) FROM broker_trades WHERE ts >= deploy_ts AND trail_count > 0`. Compare to `WHERE peak_R >= 1.5`. Currently 0/331.

3. **Phase 2 transition rate matches BE-lock rate:** Query: `SELECT COUNT(*) FILTER (WHERE peak_R >= 1.5) AS reached_phase2, COUNT(*) AS total FROM broker_trades WHERE ts >= deploy_ts`. Expect: similar to historical 1.5R-rate (~4% under broken trail; should rise to 15-25% with working trail).

4. **DQN-driven flips appear:** `exit_reason = 'DQN_ZONE_REVERSAL'` is non-zero. Each REV-flip produces a trade pair (close + fresh open) — verify by `signal_id` linkage on the new fresh-open's signal row.

5. **Confidence-scaled sizing visible:** `SELECT signal_confidence, size, COUNT(*) FROM broker_trades WHERE ts >= deploy_ts GROUP BY 1, 2`. Expect size=2 only when signal_confidence ≥ 0.85, size=1 elsewhere.

6. **Stop-tick filter active:** No new trades with `stop_ticks < 6` or `stop_ticks > 40`. Query: `SELECT MIN(stop_ticks), MAX(stop_ticks) FROM broker_trades WHERE ts >= deploy_ts`.

## Risks / edge cases

1. **Price overshoots 1.5R before BE-lock fires.** `update_mark_and_check_be_lock` runs on every tick. If a single tick jumps past 1.5R, BE-lock fires on that tick and the stop is modified within ~50-100ms. There's a window where price could swing back through entry before the new stop is in place. Acceptable risk — seen in current code, no change.

2. **Zone touched simultaneously with stop hit.** Race condition: a Phase 2 winner that's running into a zone could trigger DQN-CONT (pyramid) at the same tick the price reverses through the locked-profit stop. The `flatten` callback path takes precedence (`tracker.is_flat` check at the top of `_emit_zone_dqn_inference` in-position handler). Acceptable.

3. **REV-flip with stale signal.** If the REV decision was based on a zone touched 100ms ago and price has moved, the fresh-open enters at the new price but with the old composite_confidence. Acceptable — confidence is a setup-strength score, not a price-specific value.

4. **Pyramid runaway in trending markets.** If the DQN keeps emitting CONT at every successive zone, the position grows linearly with each zone. With BASE=1 and confidence ≥ 0.85 occurring rarely, max realistic position size is 4-6 contracts in a strong trend. No hard cap proposed — paper-trading phase, want the data on whether pyramids run away.

5. **Confidence-scaled size = 1 floor everywhere.** Until BASE_SIZE scales above 1, the multiplier rarely produces sizing differentiation (only ≥ 0.85 confidence yields 2 contracts; everything else rounds to 1). Acceptable for paper phase — sizing-tier data accumulates without yet acting on it.

6. **stop_ticks bound rejection volume.** Some currently-fired signals will be rejected by the new 6-40 tick bound. Quick estimate from this week's data: small minority. Plan: log rejected signals so we can audit the bound's appropriateness after a week.

## Out of scope

- Trail bug fix mechanics (separate prerequisite implementation).
- Training pipeline changes for Phase 2 reward attribution (each pyramid leg / flip producing distinct training tuples). Decide once we have a week of post-fix data.
- Risk caps (PDLL, PDPT, max-trades-per-session). Per [paper-phase-velocity memo](C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\feedback_paper_phase_velocity.md): defer until real-money phase.
- News blackout, IB no-trade window. Per same memo.
- Removing `size_model_v5.joblib` from the model pool. Keep for future use.

## Open questions for review

1. Should Phase 2 add a hard-cap on cumulative pyramid size (e.g., ≤ 4× initial)? Defaulting to no cap above; user to confirm.
2. Should the REV-flip be atomic (single transaction: close + open with retained context) or two-step (flatten, wait for next zone-touch event)? Spec says two-step. Atomic is more correct but harder to wire. User to confirm acceptable.
3. Should `early_exit_lock` be deleted from the DQN output entirely, or just ignored by the live path? Spec says ignore. Removing the head is a model retrain — defer.
