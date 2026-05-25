# Arnold Restructure — Betting-Only Clean Repo

**Date:** 2026-05-25
**Owner:** rasmus
**Status:** spec, awaiting plan

## Context

Arnold was a combined sports-betting + futures-trading platform. The trading subsystem was stripped between commits `7943d202` and `2d0a44d9` (2026-05-25), leaving arnold as betting-only. A parallel "fresh restart" repo (`blomen/betty`) was scaffolded on 2026-05-23 with the intended clean layout but never advanced past a backend skeleton. The user has decided to stay in `arnold` (live in prod on Hetzner) and reshape it toward betty's intended structure rather than migrate.

This spec defines the target layout, what gets deleted, and the PR sequence to get there without breaking prod.

## Goals

1. Three clean top-level layers: `frontend/`, `backend/`, `local/` (matching the structure intended for betty/quentin).
2. Remove all confirmed-dead code (trading leftovers, abandoned subdirs, root-level cruft).
3. Adopt betty's modern conventions (Python 3.12, `cli.py` entrypoint, commented pyproject) without disrupting prod.
4. Each PR deploys cleanly to the live Hetzner server — no extraction blackouts, no merge-pain branches.

## Non-Goals

- Rewriting the backend's `services/` + `repositories/` layered architecture. (Initially considered; sweep confirmed both layers are live and earning their keep. Betty's omission was scaffolding-stage, not a deliberate rejection.)
- Provider team-extraction deduplication (16 providers re-implementing parts of `matching/normalizer.py`). Tracked as a follow-up.
- Bankroll `edge_sampler.py` + `stake_calculator.py` consolidation. Tracked as a follow-up.
- Migrating to a new GitHub repo. History is institutional memory (MEMORY.md and CLAUDE.md reference commit hashes; extraction fixes have years of context in `git log`). Optional rename `arnold` → `arnold-betting` after PR 5, but the repo identity stays.
- Force-rewriting git history (`git filter-repo`) to scrub dead-code commits. Deferred unless explicitly wanted later.

## Target Layout

```
arnold/                          (repo root)
├── frontend/                    ← moved from arnold/frontend/
│   ├── src/  package.json  vite.config.ts  …
│
├── backend/                     ← server-side data engine, deployed to Hetzner
│   ├── alembic/  alembic.ini
│   ├── pyproject.toml  pyrightconfig.json
│   ├── src/
│   │   ├── analysis/  api/  bankroll/  config/  constants.py
│   │   ├── core/      db/   matching/  pipeline/  providers/
│   │   ├── services/  repositories/    ← kept (live, layered architecture)
│   │   ├── ml/  risk/  recorders/  jobs/   ← kept (all live)
│   │   └── cli.py     ← renamed from app.py
│   └── tests/
│
├── local/                       ← local client (renamed from arnold/)
│   ├── launch.py  server.py  proxy.py  http_client.py
│   ├── mirror/                  ← browser.py, play_loop.py, workflows/, …
│   ├── navigations/             ← moved from root navigations/
│   └── data/
│
├── money/                       ← shared package (backend + local import it)
├── docker/  docker-compose.yml  Dockerfile  nginx/
├── scripts/                     ← server-deploy.sh, watchdog, …
├── docs/
├── arnold.bat  arnold.ps1       ← launcher scripts at root (muscle memory)
└── README.md  CLAUDE.md  pyproject.toml (workspace-level only if needed)
```

### Why `money/` stays at repo root
Both `backend/src/` and `local/` import `money` (currency conversion at extraction time AND at bet-record time). Moving it under `backend/src/money/` would force the local client into ugly cross-boundary imports (`backend.src.money` or sys.path hacks). Keeping it at root makes the shared nature explicit.

### Why `arnold/` is renamed to `local/`
Matches the user's stated `frontend / backend / local(mirror)` model. The local-client is *the local layer* — its function, not a product name. `arnold.bat` and `arnold.ps1` stay at root because they're the user-facing launcher and changing them is muscle-memory cost for zero benefit.

## Kill List (Confirmed Dead)

Verified via grep + import-graph sweep:

| Path | Reason |
|------|--------|
| `backend/src/rl/` | Empty stubs (`data/`, `features/`, `labeling/` dirs only). Zero imports anywhere. |
| `arnold/tv_overlay/` | Only `__pycache__` remaining after April strip. |
| `arnold/tests/test_tv_overlay_router.py` | Imports `arnold.tv_overlay.router` which doesn't exist — currently failing. Tests `/stocks` route prefix (deleted). |
| `_raw_utf8.md` | 7.2 MB / 24,367-line trading research dump (order-flow tricks, volumetric analysis, YouTube links). Zero references in code. |
| `backend/nul` | Windows-redirection accident (`>` mistyped). |
| `arnold/debug_screenshot.png` | One-off debug artifact from 2026-05-10. |
| `package.json` + `node_modules/` at repo root | Leftover monorepo wrapper. Only dep is `playwright@^1.58.1` (dev). `arnold/frontend/` has its own. Verify no CI script references first. |
| `arnold/requirements.txt` | 92 bytes, redundant with `backend/pyproject.toml`. |
| `docs/tv-overlay-api-audit.md` | Audit doc for a deleted feature. |

Additional verification in PR 1: grep for `backend/src/factory.py` references — agent didn't explicitly confirm dead status.

`__pycache__/` dirs at repo root, `arnold/`, `money/`, `scripts/`, `tests/` should be gitignored if they're tracked (and removed from any current `git ls-files` output).

## Kept (Live, Not Deleted)

These were initially candidates for removal but the sweep confirmed they're load-bearing:

- `backend/src/services/` — 13 service modules imported by API routes
- `backend/src/repositories/` — 7 repo modules imported by services
- `backend/src/recorders/server_poller.py` + `polymarket_api.py` — runs 24/7 in backend container
- `backend/src/jobs/mirror_smoke.py` — daily cron via FastAPI lifespan
- `backend/src/risk/` — `/api/risk` route + `opportunity_service` (ProviderAllocator)
- `backend/src/ml/` — best-effort via try-imports for `/api/extraction` analytics. Stays.

## PR Sequence

Five PRs landing on `main`, each deployable to prod independently, each leaves the extraction loop green. Each respects the **5-minute deploy cooldown** (CLAUDE.md §"Multi-Agent Coordination") — no batched same-afternoon deploys.

### PR 1 — Confirmed-dead delete

**Scope:** every row in the Kill List above. Plus: gitignore any `__pycache__` not already covered.

**Verify before merge:**
- `grep -r "factory" backend/src/ arnold/ tests/` confirms `factory.py` is unreferenced (or, if referenced, exclude it from this PR)
- `grep -r "tv_overlay\|stocks" backend/src/ arnold/` finds no live references
- `cd backend && pytest tests/` green
- `cd arnold/frontend && npm run build` green
- `arnold.bat` boots locally without errors

**Risk:** near zero. Pure delete, no file moves.

### PR 2 — Move `arnold/frontend/` → `frontend/`

**Scope:**
- `git mv arnold/frontend frontend`
- Update `arnold/server.py`: change static-mount path from `arnold/frontend/dist` to `frontend/dist`
- Update `frontend/vite.config.ts`: base path / build output if anything is repo-relative
- Update `Dockerfile` Stage 1: change `COPY arnold/frontend ...` → `COPY frontend ...`
- Update `.dockerignore` if it references the old path
- Update `arnold.bat` if it `cd`'s into the frontend dir

**Verify before merge:**
- Standard: ruff, pytest, npm lint+build
- Local smoke: `arnold.bat` boots, browser opens, all three tabs (Sports/Bankroll/Stats) render
- Deploy verify: `curl https://148.251.40.251/` returns the SPA, `/health/ready` green

**Risk:** medium. Static-file path is a common source of "white screen of death" in prod. Test locally first.

### PR 3 — Rename `arnold/` → `local/` + fold `navigations/`

**Scope:**
- `git mv arnold local`
- `git mv navigations local/navigations`
- Update every `from arnold.mirror...` import → `from local.mirror...` (in `local/server.py`, `local/launch.py`, `local/proxy.py`, mirror workflows, tests)
- Update `arnold.bat` / `arnold.ps1` to invoke `local/launch.py`
- Update any path in `local/launch.py` that hard-codes `arnold/data/` → `local/data/`
- Update any test imports under `tests/` (root) or `local/tests/`
- Update CLAUDE.md references to `arnold/mirror/` etc.

**Verify before merge:**
- Standard: ruff, pytest, npm lint+build
- Local smoke: full mirror workflow — open a soft-book site, balance scrape works, history sync works, bet intercept works (use a 0.50 SEK test bet on any provider that allows it)
- Deploy verify: server-side unaffected (this is a local-only PR — no backend rebuild). Confirm with `git diff --name-only origin/main...HEAD | grep -v '^local/\|^arnold\.bat\|^arnold\.ps1\|^navigations/\|^CLAUDE\.md'` returns empty.

**Risk:** medium-high. Touches every mirror import. Easy to miss one and have a silent KeyError under a specific provider.

### PR 4 — Rename `app.py` → `cli.py` + adopt betty pyproject style

**Scope:**
- `git mv backend/src/app.py backend/src/cli.py`
- Update `Dockerfile` CMD/ENTRYPOINT to reference `cli` (likely `python -m src.cli ...` or similar)
- Update `backend/run_dev.py` if it imports `from src.app import ...`
- Update any `scripts/server-deploy.sh` reference (probably none)
- Port betty's heavily-commented pyproject.toml style to `backend/pyproject.toml`. The dep list is the same; only the comments + grouping change.
- Still on Python 3.10 — version bump is PR 5

**Verify before merge:**
- Standard checks
- `docker compose build backend` succeeds locally
- Deploy verify: `/health/ready` green; verify `boot_id` advances and container `CreatedAt` is post-deploy (CLAUDE.md §12 — guards against cached-layer drift)

**Risk:** low. Single rename + cosmetic pyproject change.

### PR 5 — Python 3.10 → 3.12 bump

**Scope:**
- `Dockerfile` Stage 2: `FROM python:3.12-slim` (or `3.12-bookworm-slim`)
- `backend/pyproject.toml`: `requires-python = ">=3.12"`
- `.github/workflows/*.yml`: `python-version: '3.12'`
- Any 3.10-specific syntax cleanup that ruff/pyright now flag
- Local `.venv` recreation instructions in README

**Verify before merge:**
- Standard checks
- Full backend test suite passes on 3.12 locally
- Deploy verify (extra): `/health/extraction` shows a successful full run for `pinnacle` AND at least one `browser_*` tier provider before declaring success. Match-rate must match the pre-deploy baseline ±2%.
- Watch extraction.log for 30 min post-deploy — any provider regression rolls back.

**Risk:** medium-high. Python version bumps regress in places nobody expects (asyncpg, patchright, playwright). This is last so any breakage isolates to "Python" not "Python AND moved files".

## Post-PR Cleanup

After PR 5 merges and is stable for ~48h:

- **Archive betty** on GitHub: Settings → "Archive this repository". README updated to point at `arnold`.
- **Optional GitHub rename:** `arnold` → `arnold-betting` for naming alignment with `quentin` (trading). GitHub auto-redirects old URLs; `/opt/arnold/.git/config` remote URL needs `git remote set-url origin` on the server (one-line change in `server-deploy.sh` or one manual SSH).

## Verification Checklist (Per PR)

Every PR must pass before merge:

1. `ruff check backend/ local/` clean
2. `ruff format --check backend/ local/` clean
3. `cd backend && pytest tests/` green
4. `cd frontend && npm run lint && npm run build` green (after PR 2)
5. `arnold.bat` boots locally, tunnel opens, all three tabs render
6. Post-deploy: `/health/ready` green, `/health/extraction` last-run success
7. Post-deploy: no error-rate spike in `extraction.log` for 30 min
8. Post-deploy verification per CLAUDE.md §12 (boot_id advances, container CreatedAt is post-deploy)

## Rollback

Every PR is a single commit on main and ships as one deploy. Rollback = `git revert` + `bash scripts/server-deploy.sh rebuild backend`. The deploy lock and 5-min cooldown still apply.

## Open Questions / Deferred

- **Provider team-extraction dedup** — 16 providers re-implement `_navigate_to_event` and `_extract_teams` logic that could centralize into `matching/normalizer.py`. Real refactor, separate spec.
- **Bankroll consolidation** — `edge_sampler.py` and `stake_calculator.py` overlap. Separate spec.
- **ML subsystem fate** — `backend/src/ml/` is best-effort (try-wrapped) but ~31 .py files. If unused for 60+ days post-restructure, candidate for deletion or extraction to a separate repo.

## Estimated Effort

- PR 1: 1 hour (mostly verification greps)
- PR 2: 2-3 hours (frontend path debugging)
- PR 3: 3-4 hours (import sweep across mirror)
- PR 4: 1 hour (rename + Dockerfile entrypoint)
- PR 5: 2-3 hours (Python bump + provider smoke test)

With the 5-min cooldown, realistic calendar time: **2-3 days** if shipped sequentially, more if other work interleaves.
