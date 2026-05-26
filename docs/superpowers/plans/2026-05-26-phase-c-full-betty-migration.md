# Phase C — Full Infrastructure Rename arnold → betty

> **For agentic workers:** This plan involves DESTRUCTIVE postgres operations. Read the rollback section before executing any step. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Complete the arnold → betty rename across infrastructure: server path `/opt/arnold` → `/opt/betty`, postgres DB+user `arnold` → `betty`, Docker containers `arnold-*` → `betty-*`, Docker volume name, container OS user, `ARNOLD_*` env vars, `ArnoldError` class, localStorage keys. The label "arnold" survives nowhere except git history.

**User decision (2026-05-26):** pg_dump + restore approach. 5–10 min extraction blackout acceptable.

**Risk profile:** HIGH. Postgres data migration. Server-path mv. Coordinated container rebuild. Single deploy.

---

## End State

```
/opt/betty/                    (was /opt/arnold)
├── backend/  frontend/  local/  docs/
├── .env (DB_PASSWORD)
├── .env.docker (with BETTY_* vars + DATABASE_URL using betty user)
└── .git
```

Containers:
- `betty-backend-1`, `betty-postgres-1`, `betty-nginx-1`

Postgres:
- DB `betty`, user `betty` (was `arnold`/`arnold`)
- Volume `betty_postgres_data` (data restored from pg_dump)

Code/env:
- `ArnoldError` → `BettyError`
- `ARNOLD_DATA_DIR`/`ARNOLD_LOGS_DIR`/`ARNOLD_CONFIG_DIR`/`ARNOLD_TUNNEL_URL`/`ARNOLD_API_KEY`/`ARNOLD_MIRROR_ONLY` → `BETTY_*`
- localStorage keys `arnold:*` → `betty:*` (with one-time migration shim)
- Container OS user `arnold` (uid 1000) → `betty` (uid 1000)

---

## Split into 2 PRs

### PR C1 — Code-Side Rename (low risk, no infrastructure)

All pure code/config changes. Deploys to existing infrastructure cleanly. Backend keeps reading `ARNOLD_*` env vars (still set on server). After PR C1, the code is *ready* for betty infra but still wired to arnold infra.

**Files:**
- `backend/src/`: `ArnoldError` → `BettyError` (rename class + every import)
- `backend/src/` + `local/`: every `os.environ.get("ARNOLD_*")` → `os.environ.get("BETTY_*", os.environ.get("ARNOLD_*"))` (backward-compat reads — accept either name)
- `frontend/src/`: localStorage keys with migration shim — on read, check `betty:KEY` first, fall back to `arnold:KEY` + migrate (write to new key, delete old)
- `local/betty.bat` / `local/betty.ps1`: any `ARNOLD_TUNNEL_URL` env var setting also set `BETTY_TUNNEL_URL` with same value

Deploy: standard backend rebuild. Verify all 39 providers still healthy, mirror still works.

### PR C2 — Infrastructure Flip (HIGH RISK, coordinated single deploy)

Single coordinated server operation. Sequence MUST be exact.

**Server operations (NOT git commit-driven — these are SSH commands):**
1. Stop backend container (postgres + nginx stay up)
2. `pg_dump -U arnold -d arnold -F c -f /opt/arnold/db-backup-pre-c2.dump`
3. Update local code (git pull) — has new Dockerfile/compose/env names
4. `mv /opt/arnold /opt/betty`
5. Update `.env.docker` inside /opt/betty: rename ARNOLD_* → BETTY_*; update DATABASE_URL connection string (`postgresql+asyncpg://betty:${DB_PASSWORD}@postgres:5432/betty`)
6. Update crontab: `/opt/arnold/...` → `/opt/betty/...`
7. Update `/etc/cron.d/arnold-watchdog` → `/etc/cron.d/betty-watchdog`
8. `docker compose -p arnold down` (stops all containers; preserves arnold_postgres_data volume for now)
9. `docker compose --project-name betty -f /opt/betty/backend/docker-compose.yml --env-file ../.env up -d postgres` (creates betty-postgres-1 + betty_postgres_data volume; new empty DB)
10. `psql -U arnold -d postgres -c "CREATE USER betty WITH PASSWORD ...; CREATE DATABASE betty OWNER betty;"` (inside the new postgres container; `arnold` user was the bootstrap superuser)
11. `pg_restore -U betty -d betty -F c /opt/betty/db-backup-pre-c2.dump`
12. Bring up backend + nginx: `docker compose --project-name betty -f /opt/betty/backend/docker-compose.yml --env-file ../.env up -d`
13. Verify /health/ready, extraction, match-rates

**Code changes in PR C2:**
- `backend/Dockerfile`: `useradd -m -u 1000 arnold` → `useradd -m -u 1000 betty`; all `arnold` references in volume mounts/ownership → `betty`
- `backend/docker-compose.yml` + `.dev.yml`: any `arnold-*` service/container name → `betty-*`; explicit `name: betty` at top level; volume names if any
- `backend/scripts/server-deploy.sh`: every `/opt/arnold` → `/opt/betty`, lock file path `/opt/arnold/.deploy.lock` → `/opt/betty/.deploy.lock`, status file path
- `backend/scripts/container-watchdog.sh`: same
- `backend/.env.docker.example`: `arnold` → `betty` in DATABASE_URL example, POSTGRES_USER, POSTGRES_DB; `ARNOLD_*` → `BETTY_*`
- `local/betty.bat` / `.ps1`: any `ARNOLD_*` env exports → `BETTY_*` (drop backward-compat from PR C1)
- `backend/src/` + `local/`: drop the backward-compat fallback (PR C1's `os.environ.get("BETTY_*", os.environ.get("ARNOLD_*"))` → just `os.environ.get("BETTY_*")`)
- `CLAUDE.md`: every `/opt/arnold` → `/opt/betty`, `arnold-backend-1`/etc. → `betty-*`, `POSTGRES_USER=arnold` → `POSTGRES_USER=betty`
- `README.md`: update if it references old infrastructure

**Rollback for PR C2 (critical to define BEFORE starting):**
1. `git revert HEAD; git push`
2. On server: `mv /opt/betty /opt/arnold`; restore crontab paths; `docker compose -p betty down`; `docker compose -p arnold up -d`; verify backend connects to original arnold DB on original arnold_postgres_data volume
3. The original arnold DB+user+volume are LEFT INTACT during the migration — they exist alongside the new betty ones until cleanup. Rollback restores the original wiring.
4. If betty data was already restored but rollback needed: arnold DB volume preserved untouched; reverting just re-points containers at it.

**Cleanup (after 48h stable):**
- `docker volume rm arnold_postgres_data`
- `rm /opt/betty/db-backup-pre-c2.dump`
- Drop the backward-compat env var fallbacks from code (already done in PR C2 above)

---

## Execution Order

This session: PR C1 only. PR C2 deferred to a separate session because:
- Server downtime + postgres migration is a "do once, do right" operation
- Should be done when the user is actively watching, not during multi-agent activity
- The data backup before the operation is non-trivial to verify
- Wants explicit user go-ahead at execute-time

After PR C1 lands and stabilises, the user can decide when to schedule PR C2.
