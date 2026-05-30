# RL v6 — L2 Order-Book Features Research Plan

**Date:** 2026-04-29
**Status:** Saved for later — not started
**Trigger to revisit:** when v5 hybrid plateaus, OR on first measurable performance regression, OR when free engineering bandwidth opens up

## Context

On 2026-04-29 the user canceled their TopstepX Trading Combine Level 2 Market Data subscription. We confirmed:

1. The current v5 hybrid model (56.8% win / 2.24 PF on 89k OOS) trains on **tick-only features** — `orderflow_score`, VWAP, candle features, zone strength. None of the DQN observation dimensions read from `_state["depth"]`.
2. The L2 wiring that existed (`record_depth` in [`backend/src/stocks/dashboard.py:619`](../../../backend/src/stocks/dashboard.py#L619), `L2Ladder.tsx`, TV overlay autonomous depth display) was either unused by the model or already deleted from the user's WIP.
3. Cancelation has **zero immediate impact** on PnL or model quality.

But: this discussion concluded that L2 *would* meaningfully help a v6 model **specifically because** the architecture is built around zone touches. Tick orderflow is a backward-looking momentum proxy; L2 book is a forward-looking liquidity proxy — different signal modalities, not redundant. The current model predicts zone-defense quality from a backward-looking proxy of a forward-looking phenomenon.

## Realistic upside estimate

Microstructure literature on retail-grade book features typically shows 3-8% directional prediction lift for short-horizon models with raw exchange data + co-located execution. TopstepX's aggregated retail feed with 50-200ms latency probably captures 30-60% of that lift.

| Metric | Current (tick-only) | With L2 (realistic) | With L2 (optimistic) |
|---|---|---|---|
| Win rate | 56.8% | 58-60% | 60-62% |
| Profit factor | 2.24 | 2.4-2.6 | 2.6-2.9 |
| Annualized return | baseline | +10-20% | +25-40% |

A 2-3 percentage-point win-rate improvement is substantial — likely the difference between "comfortably profitable" and "obviously profitable" at NQ tick PnL.

## Cost stack (real)

| Cost | Magnitude |
|---|---|
| Monthly TopstepX L2 fee | low/mid double digits USD |
| Engineering: design `bid_at_zone`, `ask_at_zone`, `imbalance_5tick`, `imbalance_total`, `wall_strength`, `pre_touch_absorption_ratio` | 1-2 weeks |
| Data collection window before training pool is meaningful | 2-4 months at ~20-30 setups/day → ~3000 L2-tagged setups |
| Retraining + A/B validation against v5 | 1 week |
| Risk during transition (running v6 with thin live history) | uncertain |

**Note:** historical Databento NQ tick parquets are gone (lost during the firev → arnold rename, see CLAUDE.md). L2 cannot be backfilled onto old episodes; the training pool starts from L2-resubscribe-day-1.

## Phased plan

### Phase 1 — Resubscribe + instrument (week 1)
- [ ] Re-subscribe to TopstepX Level 2 Market Data via the dashboard
- [ ] Verify `GatewayDepth` events arrive: log payload count per minute in [`topstepx_stream.py`](../../../backend/src/stocks/topstepx_stream.py)
- [ ] Confirm [`record_depth`](../../../backend/src/stocks/dashboard.py#L619) still wires correctly; restore `L2Ladder.tsx` if useful for human verification

### Phase 2 — Persist book context (week 2)
- [ ] Extend `live_collector.on_zone_touch` to capture a snapshot of top-20 bids/asks at touch time
- [ ] Save as a separate `obs_book_LT*.npy` parallel to the existing observation file (don't bloat the main obs vector yet)
- [ ] Add a database column or JSON sidecar on `stock_signals` recording the book snapshot for later forensic analysis

### Phase 3 — Data collection (months 2-3)
- [ ] Run v5 trading as normal in production
- [ ] Accumulate L2-tagged episodes in `data/rl/live_episodes/`
- [ ] Daily backup is already in place (`/root/rl-backup.sh`)
- [ ] Target: ~3000 L2-tagged setups before training begins
- [ ] Stop early if TopstepX feed quality is visibly bad (gaps, aggregated artifacts, missing levels) — cancel and revert

### Phase 4 — Feature engineering + training (month 3-4)
Candidate features (start here, prune by importance):
- `bid_at_zone` — size resting at touched zone ± 1 tick
- `ask_at_zone` — mirror
- `imbalance_5tick` — Σ bid - Σ ask in nearest 5 ticks, normalized by total
- `imbalance_total` — same across top 20 levels
- `wall_strength` — log-scale max single-level size on defending side within 5 ticks
- `pre_touch_absorption_ratio` — change in defending-side size over the 30s pre-touch window
- `book_velocity` — rate of size change (book churn) in the 10s pre-touch window

Training:
- [ ] Build feature extractor reading `obs_book_LT*.npy`
- [ ] Train v6 with augmented obs vector (~285-290 dims, +6-11 from current 279)
- [ ] Use the same train/val/test splits as v5 for comparability
- [ ] Compare feature importance via SHAP or DQN attribution

### Phase 5 — A/B validation + decision (month 4)
- [ ] OOS evaluation on held-out 4-week window
- [ ] Decision criteria:
  - **Lift ≥ 1 pp win rate OR ≥ 0.15 PF** → switch live trading to v6
  - **Lift < 1 pp AND < 0.15 PF** → cancel L2 subscription, keep v5, document negative result
- [ ] If passing: write deployment checklist, archive v5 model, ship v6
- [ ] If failing: write postmortem with feature importance + suggestion (was it the data, the features, or the architecture?)

## Pitfalls to watch for

1. **Curse of dimensionality** — adding 6-11 features to a 279-dim vector dilutes the training pool. Mitigate by keeping the L2 feature count minimal until importance is verified.
2. **TopstepX feed quality** — retail-routed L2 may be aggregated/filtered vs raw CME. Validate by spot-checking against published volume bars or against another data source if accessible.
3. **Latency artifacts** — book at 50-200ms latency may already be stale by the time the DQN observes it. Snapshot timing should be the *touch* event, not "now"; record the latency on every snapshot for later analysis.
4. **Survivorship bias in collection** — only zones that get touched generate snapshots. Consider also snapshotting the book at non-touch decision points so the model sees the full distribution of book states.
5. **Don't conflate v6 with the "no-historical-tick-parquets" data gap** — even if v6 fails, the wider data-collection effort is independent and worth doing.

## Triggers to revisit

- v5 hybrid live performance starts trending toward 50% win rate or PF < 1.5 over a 4-week window → resubscribe immediately and start phase 1
- Free engineering bandwidth opens up after another major feature ships (e.g., Polymarket workflow stabilizes) → research-mode start
- TopstepX adds a "trial" or "pause" option for L2 → cheap to start collecting data without commitment

## Decision log

- **2026-04-29:** subscription canceled to save the monthly fee while v5 is still in early production. This plan is the path back if/when v5 plateaus or extra capacity opens up. **Default action: do nothing; revisit on the triggers above.**
