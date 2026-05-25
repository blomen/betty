# Strip Trading from Arnold — Design

**Date:** 2026-05-25
**Status:** Approved scope, pending implementation plan
**Author:** Rasmus + Claude

## Goal

Convert Arnold from a hybrid sports-betting + NQ-futures-trading platform into a
pure sports-betting platform. Hard cutover, no backwards compatibility. Trading
code, DB tables, frontend tabs, config, infra hooks, docs, and memory entries
all go.

## Non-goals

- Maintain compatibility with the trading subsystem we're removing
- Smooth migration path for re-enabling trading later — re-adoption would start
  from git history, not from preserved scaffolding
- Cleaning git history or `docs/superpowers/{specs,plans}/` archive (dated docs
  stay as historical record)

## Scope

### Backend code — delete entire directories

- `backend/src/stocks/` — TopstepX client, stream, signal relay, broker adapter,
  server bootstrap, dashboard, tracker reconciler
- `backend/src/broker/` — broker adapter, position tracker, flatten scheduler,
  Tradovate client
- `backend/src/rithmic/` — Rithmic broker client + stream
- `backend/src/market_data/` — level monitor, orderflow, structure, scanner,
  scheduler, scoring, setups, stream, TopstepX poller, TPO, zone trail, levels,
  L1 persistence + quote state, AMT, COT, history, databento provider,
  data continuity monitor, macro provider
- `backend/src/edge/` — new supervised ML rebuild (273-dim features, LightGBM
  models, validation harness)
- `backend/src/rl/` — entire RL stack: zone builder, confidence, live collector,
  live inference, session manager, narrative bias, exit signals, stop policy,
  signal log, add policy, features/, labeling/, config/, data/, agent/,
  signal/ (most of agent/ + signal/ already staged for delete)

### Backend code — delete targeted files

- `backend/src/services/trading_service.py`
- `backend/src/services/market_service.py`
- `backend/src/api/routes/market.py`
- Stocks/RL mounts in `backend/src/api/__init__.py` (lifespan task, `/stocks/*`
  router, `/ws/signals` and `/ws/dashboard` WebSockets)
- `backend/scripts/rl_train_*.sh`, `audit_*.py`, `train_ft_v1.py`, `run_sim.py`,
  `backtest_shadow_models.py`, `shadow_daily_report.py`,
  `audit_dead_dims_diagnosis.py` (most already staged)

### Backend code — trading slice of `ml/`

The `ml/` directory mixes sports + trading. Delete only the trading slice:

- `backend/src/ml/level_touch/` (entire — level touch features for zones)
- `backend/src/ml/macro/` (entire — economic calendar, options flow, news
  impact recorder, all NQ-trading specific)
- `backend/src/ml/features/candle_features.py`
- `backend/src/ml/features/level_touch_features.py`
- `backend/src/ml/features/trading_features.py`
- `backend/src/ml/models/level_classifier.py`
- `backend/src/ml/models/macro_engine.py`
- `backend/src/ml/feature_store.py` (uses `CandleSnapshot`)
- `backend/src/ml/migrations.py` (trading-only)
- `backend/src/ml/training/train_all.py` (imports broker/stocks/market_data)

Leave alone (sports-side): `ml/models/{adaptive_kelly,boost_calibrator,edge_quality,gate_classifier,limit_predictor,setup_scorer,temporal_pattern}.py`,
`ml/optimizer/`, `ml/analytics/`, `ml/serving/`. Verify with an import smoke
after each deletion phase that these don't pull deleted symbols.

### Database

In `backend/src/db/models.py:_run_pg_migrations`, append idempotent
`DROP TABLE IF EXISTS ... CASCADE` for each trading table:

- `broker_trades`
- `stock_signals`
- `trades` (old manual journal)
- `trade_events`
- `postmortems`
- `pending_episodes`
- `live_episodes`
- `candle_snapshots`
- `ml_features` — verify by `grep -r "MlFeature\|ml_features" backend/src/`
  whether the table serves any sports-side code. If yes, leave the table and
  only drop trading rows via `DELETE FROM ml_features WHERE domain IN (...)`.
  If no, drop the table entirely.
- Any other table surfaced by inspecting `db/models.py` and grepping for
  references to deleted symbols

Drop the corresponding SQLAlchemy `Base.metadata` model classes. Sports tables
(events, odds, bets, opportunities, profiles, bankroll, arb groups, etc.) stay
untouched.

### Arnold local client

- Delete `arnold/stocks_runtime.py`
- Delete `arnold/tv_overlay/` (entire — userscript + assets)
- Delete `arnold/frontend/src/pages/stocks/` (entire — SignalsPage, BankrollPage,
  StatsPage, dqnConfig)
- `arnold/server.py`: drop `/stocks/*` router mount, stocks dashboard HTML,
  `/stocks/ws/dashboard` WebSocket endpoint
- `arnold/launch.py`: drop TopstepX env validation + stocks startup branch
- `arnold/frontend/src/pages/BankrollPage.tsx`: drop Trading sub-tab, inline
  Sportbets as the only view
- `arnold/frontend/src/pages/StatsPage.tsx`: drop Trading sub-tab, inline
  Betting as the only view
- Top-level tab nav (App.tsx or equivalent): drop the Stocks tab + the
  ErrorBoundary that wraps the stocks side
- Drop `useDashboardWS` hook + its file
- Drop stocks-related WS message handlers / TypeScript types

### Config + infra

- `.env.docker` (server) and any local `.env` examples: remove
  `STOCKS_AUTONOMOUS`, `TOPSTEPX_*`, `RITHMIC_*`, `STOCKS_AUTH_STARTUP_DELAY_SEC`,
  `RECKLESS_LEARNING_MODE`, `ENABLE_PER_TICK_REVERSAL`,
  `ENABLE_EARLY_EXIT_LOCK`, `ALLOW_OPEN_POSITION_DEPLOY`, `ZONE_COOLDOWN_S`,
  `MIN_TRADE_INTERVAL_S`, any RL-specific tuning vars
- `backend/pyproject.toml`: drop `torch`, `lightgbm`, `joblib` if no remaining
  sports-side import uses them (verify via grep before removing)
- `Dockerfile`: no structural change required, but the frontend stage will
  naturally shed the stocks bundle once those files are gone
- `scripts/server-deploy.sh`: remove `wait_for_rl_training`, the
  `Position/searchOpen` open-position gate, RL daemon process checks, and the
  `taskset`/`nice` CPU isolation lines
- `backend/src/api/__init__.py`: remove the `stocks_bootstrap` lifespan task
- Server cron (on Hetzner box): remove the RL daemon entry and `rl-backup.sh`
  schedule. Leave the script on disk for now — just unscheduled.

### Data on server

**Leave `/app/data/rl/` on disk.** With code gone there are no new writes;
storage cost is negligible and recovery is impossible to redo after a wipe.
User can `rm -rf` later if disk pressure arises.

### Docs

- `CLAUDE.md`: delete sections
  - "Stocks autonomous trading"
  - "Stocks — Trade Lifecycle (Phase 1 / Phase 2 state machine)"
  - "Stocks — Chart & Model Conventions"
  - "Live trade → training feedback loop"
  - "RL CPU isolation"
  - "Stocks-aware rebuild rules"
  - "Stocks-hot window" guidance in deploy etiquette
  - Trim "Two Programs" table to sports-only
  - Update architecture diagram (drop stocks blocks)
  - Drop the entire "Stocks autonomous trading" header block
- `docs/superpowers/specs/` and `docs/superpowers/plans/`: leave dated trading
  docs as historical archive — no harm, and they're searchable record of why
  we built it the way we did

### Memory (`C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\`)

Delete these `.md` files and their lines from `MEMORY.md`:

- All `project_topstepx_*`
- All `project_trail_*` / `project_trade_124_*` / `project_back_derive_*`
- All `project_arnoldstocks_*`
- All `project_rl_*` (CPU isolation, turbo flag, v5 hybrid results, v6 L2)
- All `project_signal_*`, `project_signalrelay_*`
- `project_combinations_analysis.md`
- `project_candle_data_safety.md`
- `project_short_side_asymmetry_2026_05_08.md`
- `project_parallel_trade_systems_2026_05_08.md`
- `project_recovery_naked_position_2026_05_12.md`
- `project_obs_pipeline_reconstruction_2026_05_12.md`
- `project_methodology_grouped_architecture_2026_05_17.md`
- `project_plan1_l1_quotes_shipped_2026_05_17.md`
- `project_plan2_shadow_framework_shipped_2026_05_18.md`
- `project_gbt_of_audit_2026_05_18.md`
- `project_trigger_obs_tpo_gap_2026_05_18.md`
- `project_of_methodology_limits_2026_05_18.md`
- `project_dim_outcome_study.md`
- `project_obs_dim_health_2026_05_22.md`
- `project_of_dims_broken_audit_2026_05_21.md`
- `project_edge_rebuild.md`
- `project_execution_model_v1_status.md`
- `project_methodology_refinement_edge.md`
- `project_pooled_stake_sizing_2026_05_20.md` (trading-adjacent: it scopes
  unlimited-provider stake pooling for arbs/values, so re-read before
  deleting — likely KEEP, sports)
- `project_stop_hunt_audit_2026_05_15.md`
- `project_signal_quality_findings_2026_05_05.md`
- `project_signal_storm_root_cause.md`
- `feedback_no_hard_time_gates.md`
- `feedback_obs_schema_offsets.md`
- `feedback_paper_phase_velocity.md`
- `feedback_no_rebuild_during_session.md`
- `feedback_per_fix_backtest.md`
- `feedback_audit_correlation_vs_causation.md`
- `feedback_backtest_measurement_discipline.md`

**Keep** (sports / general / mirror / infra):
`feedback_never_mix_currencies`, `feedback_rebuild_frontend`,
`proxy_architecture`, `reference_pinnacle_proxy`, `user_rasmus`,
`project_pinnacle_wiring`, `extraction_audit_2026_04_07`,
`project_international_providers`, `project_generic_mirror_workflow`,
`feedback_provider_discovery_first`, `feedback_provider_onboarding`,
`feedback_altenar_wasm`, `project_leovegas_mirror`,
`feedback_no_paid_data_feeds`, `feedback_no_more_databento_backfill`,
`project_kalshi_smarkets_integration`, `project_pinnacle_cleanup_race`,
`project_bankroll_deploy_pending`, `project_deploy_queue_needed`,
`feedback_no_deploy_for_local_frontend`, `project_comeon_dom_fingerprint`,
`feedback_capability_matrix_lies`, `feedback_keep_all_data_sources`,
`feedback_polymarket_only_redeem_at_100`, `feedback_ruff_autofix_strips_imports`,
`project_dbet_ghost_tab_fix`, `feedback_asyncio_task_strong_ref`,
`project_topstepx_api_subscription` — trading, but flagged DELETE,
`feedback_orphan_sweep_check_tracker` — trading, DELETE,
`project_bracket_stop_orphan_pickup` — trading, DELETE,
`project_signal_exit_orphan_stop` — trading, DELETE,
`project_cash_open_slippage_stop` — trading, DELETE,
`project_altenar_status_parser_gotcha`, `project_manual_bet_recorder`,
`project_dom_scrape_team_name_match`, `project_pending_row_ui_contract`,
`reference_arnold_venv`, `project_reactive_sync_architecture`,
`project_kalshi_recorder_fixes_2026_05_23`,
`project_training_feedback_loop` — trading, DELETE,
`project_topstepx_trade_endpoints` — trading, DELETE,
`project_rl_v6_l2_research` — trading, DELETE,
`project_trading_issues` — trading, DELETE.

The plan step for memory cleanup MUST start with a fresh grep of MEMORY.md +
re-eyeball each file. The list above is a starting point, not authoritative —
memory has rotated since this spec was written.

## Order of operations

1. Delete backend trading code (whole directories first, then targeted files)
2. Drop SQLAlchemy classes + add DROP TABLE migrations in `db/models.py`
3. Trim API routes + lifespan in `backend/src/api/__init__.py`
4. Strip arnold local client (server.py, launch.py, frontend pages, hook)
5. Strip config + Dockerfile + deploy script + cron
6. Update CLAUDE.md + memory
7. Verify:
   - `python -c "from backend.src.app import app"` — imports resolve
   - `pytest backend/tests/` — sports tests pass
   - `cd arnold/frontend && npm run build` — frontend builds
   - `arnold.bat` launches without TopstepX env
   - Deploy to Hetzner (gated by user flattening + setting `canTrade=false` if
     any live position exists)

## Risks + mitigations

- **Hidden imports of deleted modules from sports-side code** —
  `api/__init__.py` already touches both worlds, and `ml/` has known mixing.
  **Mitigation:** after each deletion phase, run
  `python -c "import backend.src.app"` to surface ImportErrors immediately. Fix
  in place; don't batch.
- **Server tries to start trading on next deploy** — if the stocks bootstrap
  call stays in the lifespan while the module is gone, deploy crashes.
  **Mitigation:** ensure bootstrap removal is in the same commit as the module
  deletion, and verify locally before pushing.
- **`ml/feature_store.py` and friends mix sports + trading tables** — deleting
  too aggressively breaks sports-side ML. **Mitigation:** keep `ml/` surgery
  surgical — only delete files that import broker/stocks/market_data/rl. Leave
  `setup_scorer.py`, `gate_classifier.py`, `limit_predictor.py`, etc. alone
  unless they pull deleted code. Verify with grep before each delete.
- **Open TopstepX position at deploy time** — could realize loss when broker
  bootstrap disappears without graceful flatten. **Mitigation:** user manually
  flattens + sets `canTrade=false` on TopstepX dashboard before the final
  deploy. We can also leave the broker code running on the *currently deployed*
  container until the user confirms flat, then deploy the stripped build.
- **Memory delete list is wrong** — accidental deletion of a sports memory.
  **Mitigation:** build the exact list with `grep -L "stocks\|topstepx\|broker\|rl\|trade\|dqn\|gbt\|obs\|zone\|signal\|level\|orderflow\|trail\|episode\|candle\|nq\|edge"` against the memory dir before deleting. Re-eyeball before `rm`.

## Verification checklist (final)

- [ ] `python -c "from backend.src.app import app"` succeeds
- [ ] `pytest backend/tests/` — green on remaining tests
- [ ] `grep -r "from src.stocks\|from src.broker\|from src.market_data\|from src.rl\|from src.rithmic\|from src.edge" backend/src/` returns nothing
- [ ] `grep -r "TopstepX\|topstepx\|broker_adapter\|level_monitor" backend/src/` returns nothing
- [ ] `cd arnold/frontend && npm run build` succeeds
- [ ] `arnold.bat` launches and shows only Sports, Bankroll (no Trading sub-tab),
      Stats (no Trading sub-tab) tabs
- [ ] Server deploys cleanly (`scripts/server-deploy.sh rebuild backend`)
- [ ] `/health` reports green, extraction continues, no stocks-related errors
- [ ] DB inspection shows trading tables dropped, sports tables intact
- [ ] `CLAUDE.md` reads as a pure sports-betting doc, no stocks references
- [ ] `MEMORY.md` index has no trading entries; trading memory files deleted

## Decisions made during scoping

- **Strip aggressiveness:** Total nuke (chosen 2026-05-25). Backend code, DB
  tables, frontend, config, docs, memory all go.
- **Filesystem data:** Keep `/app/data/rl/` on disk (no writes, cheap insurance,
  user can wipe later).
- **Trading specs/plans in `docs/superpowers/`:** Keep as historical archive.
- **Sub-tab shells in frontend:** Inline the remaining single view, don't keep
  shells.
