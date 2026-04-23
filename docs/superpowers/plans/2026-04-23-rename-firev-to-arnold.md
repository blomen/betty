# Rename firev → arnold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the project from "firev" to "arnold" across the entire codebase, Docker stack, production server, PostgreSQL databases, GitHub repo, and local working directory.

**Architecture:** Phased execution ordered to minimize broken states. Local code changes land first (fully reversible via git), then Docker volumes + DBs are migrated on the production server during a brief maintenance window, then GitHub/local-dir renames happen last (externally visible, harder to revert). Each phase has a verification checkpoint before proceeding.

**Tech Stack:** bash, ssh, docker compose, PostgreSQL, git, find/sed-based mass replace.

---

## Scope Summary

- **Local codebase:** ~892 occurrences across 129 files (case-sensitive: `firev`, `Firev`, `FIREV`)
- **Directories to rename:** `firevsports/` → `arnoldsports/`, `firevstocks/` → `arnoldstocks/`
- **Batch launchers:** `firevsports.bat`, `firevstocks.bat`
- **Production server:** `/opt/firev` → `/opt/arnold`, containers `firev-*` → `arnold-*`, 6 Docker volumes (prefixed `firev_`), 2 DBs (`firev` + `market`), cron entry, `FIREV_API_KEY` env var
- **GitHub repo:** `blomen/Firev` → `blomen/Arnold` (manual via GitHub UI)
- **Memory files:** 22 files in `C:\Users\rasmu\.claude\projects\c--Users-rasmu-firev\memory\` (content + 2 filenames)
- **Local working dir:** `c:\Users\rasmu\firev` — renamed at the very end by the user outside this session

## Case Variants Policy

Preserve case when substituting:
- `firev` → `arnold` (lowercase: most code, paths, DB names, Docker)
- `Firev` → `Arnold` (PascalCase: class names, display strings, README title)
- `FIREV` → `ARNOLD` (uppercase: env vars like `FIREV_API_KEY`)

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Production extraction downtime during DB rename | Execute during low-activity window; pg_dump backup first; rollback plan documented in Task 10 |
| Docker volumes can't be renamed — only recreated | Back up `pg_data` via `pg_dump`, recreate volumes under new names, restore |
| Working dir rename breaks Claude session | Done last, by user, outside Claude Code |
| GitHub rename breaks clones that reference old URL | GitHub auto-redirects; update `origin` on local + server clones |
| Memory files referenced by absolute path `c--Users-rasmu-firev` | Memory folder path is tied to CWD — will migrate naturally when local dir renames; copy contents to new path |
| Hidden occurrences in binary files / node_modules | Exclude `node_modules/`, `.venv/`, `.git/`, `dist/`, `build/`, image/binary extensions |

---

## Phase 0: Pre-flight

### Task 0: Verify assumptions and snapshot current state

**Files:** None (recon only)

- [ ] **Step 1: Confirm no uncommitted work conflicts**

```bash
cd c:/Users/rasmu/firev
git status --short
```

Expected: Existing modified files are known (many `M` entries from active RL/bankroll work). Record which files are modified — we'll stash or commit them before the rename to keep the rename diff clean.

- [ ] **Step 2: Decide: stash or commit existing work**

If the existing M files are WIP and unrelated to the rename, either:
- Commit them first on the current branch (preferred if they're stable), OR
- `git stash push -u -m "pre-rename-stash"` and restore after rename

Record the decision in the commit message for Task 9.

- [ ] **Step 3: Verify server is healthy before touching it**

```bash
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh status"
```

Expected: All 3 containers `Up`, no deploy lock held, recent extraction log activity.

- [ ] **Step 4: Snapshot current server state for rollback**

```bash
ssh root@148.251.40.251 "docker ps --format '{{.Names}}' > /root/pre-rename-containers.txt && docker volume ls --format '{{.Name}}' > /root/pre-rename-volumes.txt && cat /root/pre-rename-containers.txt /root/pre-rename-volumes.txt"
```

Expected output captured as a reference for rollback.

- [ ] **Step 5: Announce checkpoint**

Tell user: "Pre-flight complete. Ready to start local rename (Phase 1). This phase is fully reversible via git. Proceed?"

---

## Phase 1: Local codebase rename (reversible via git)

### Task 1: Create rename branch

**Files:** None (git only)

- [ ] **Step 1: Branch from current state**

```bash
cd c:/Users/rasmu/firev
git checkout -b rename/firev-to-arnold
```

Expected: `Switched to a new branch 'rename/firev-to-arnold'`.

- [ ] **Step 2: Verify branch**

```bash
git branch --show-current
```

Expected: `rename/firev-to-arnold`.

### Task 2: Global case-sensitive find/replace in tracked files

**Files:** All git-tracked files containing `firev`/`Firev`/`FIREV` except plan/spec markdown in `docs/superpowers/` (historical record — leave intact).

- [ ] **Step 1: Build exclusion list**

Excluded from rename:
- `docs/superpowers/plans/*.md` — historical plans; reference prior state
- `docs/superpowers/specs/*.md` — historical specs
- This plan file itself (`2026-04-23-rename-firev-to-arnold.md`) — it IS the rename plan
- `.git/`, `node_modules/`, `.venv/`, `dist/`, `build/`, `.pytest_cache/`, `.ruff_cache/`

- [ ] **Step 2: List candidate files**

```bash
cd c:/Users/rasmu/firev
git ls-files | grep -v -E '^(docs/superpowers/(plans|specs)/|node_modules/|\.venv/)' > /tmp/rename_candidates.txt
wc -l /tmp/rename_candidates.txt
```

Expected: A few hundred files — these are the tracked files subject to replacement.

- [ ] **Step 3: Filter to files actually containing the word**

```bash
cd c:/Users/rasmu/firev
grep -l -I -E 'firev|Firev|FIREV' $(cat /tmp/rename_candidates.txt) > /tmp/rename_targets.txt 2>/dev/null
wc -l /tmp/rename_targets.txt
```

Expected: ~120 files.

- [ ] **Step 4: Dry-run sed on one file to validate behavior**

Pick `CLAUDE.md` as a test subject:

```bash
cd c:/Users/rasmu/firev
sed -E 's/firev/arnold/g; s/Firev/Arnold/g; s/FIREV/ARNOLD/g' CLAUDE.md | diff CLAUDE.md - | head -40
```

Expected: Diff shows lowercase `firev` → `arnold`, `Firev` → `Arnold`, `FIREV` → `ARNOLD`, no unexpected substrings broken (e.g., no longer word containing "firev" as a substring — there shouldn't be any).

- [ ] **Step 5: Execute in-place replace on all target files**

```bash
cd c:/Users/rasmu/firev
while IFS= read -r f; do
  sed -i -E 's/firev/arnold/g; s/Firev/Arnold/g; s/FIREV/ARNOLD/g' "$f"
done < /tmp/rename_targets.txt
```

Expected: No errors. `git diff --stat` shows changes across ~120 files.

- [ ] **Step 6: Sanity-check diff**

```bash
cd c:/Users/rasmu/firev
git diff --stat | tail -5
git diff CLAUDE.md | head -30
```

Expected: Diff shows only `firev`→`arnold` substitutions; no unintended side effects.

- [ ] **Step 7: Re-check for any missed occurrences**

```bash
cd c:/Users/rasmu/firev
grep -r -l -I -E 'firev|Firev|FIREV' --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git --exclude-dir=.pytest_cache --exclude-dir=.ruff_cache . | grep -v -E '^(\./docs/superpowers/(plans|specs)/)'
```

Expected: Only the plan file itself (`2026-04-23-rename-firev-to-arnold.md`) and possibly untracked files. If anything else remains, investigate and re-run sed on those files.

- [ ] **Step 8: Commit the content replace (rename commit 1/3)**

```bash
cd c:/Users/rasmu/firev
git add -A
git commit -m "rename: firev -> arnold (content only, directories still old names)"
```

### Task 3: Rename top-level directories

**Files:** `firevsports/` → `arnoldsports/`, `firevstocks/` → `arnoldstocks/`

- [ ] **Step 1: Rename firevsports**

```bash
cd c:/Users/rasmu/firev
git mv firevsports arnoldsports
```

Expected: `git status` shows renames.

- [ ] **Step 2: Rename firevstocks**

```bash
cd c:/Users/rasmu/firev
git mv firevstocks arnoldstocks
```

- [ ] **Step 3: Rename .bat launchers**

```bash
cd c:/Users/rasmu/firev
git mv firevsports.bat arnoldsports.bat
git mv firevstocks.bat arnoldstocks.bat
```

- [ ] **Step 4: Re-verify .bat internal references**

```bash
cat arnoldsports.bat arnoldstocks.bat
```

Expected: Launcher scripts reference `arnoldsports/` and `arnoldstocks/` dirs (was updated by Task 2 sed).

- [ ] **Step 5: Commit directory rename**

```bash
cd c:/Users/rasmu/firev
git commit -m "rename: firev -> arnold (directory renames)"
```

### Task 4: Update Python package metadata

**Files:** `pyproject.toml`, `backend/pyproject.toml` (if separate), any `setup.py`, any `__init__.py` with `__name__` or package constants.

- [ ] **Step 1: Check pyproject.toml for package name**

```bash
cd c:/Users/rasmu/firev
grep -E '^name\s*=' pyproject.toml
```

Expected: `name = "arnold"` (already replaced by Task 2). If it still says `firev`, fix it.

- [ ] **Step 2: Check frontend package.jsons**

```bash
cd c:/Users/rasmu/firev
grep -A1 '"name"' arnoldsports/frontend/package.json arnoldstocks/frontend/package.json
```

Expected: Both show `"name": "arnold..."`. If not, fix inline.

- [ ] **Step 3: Commit any stragglers**

```bash
cd c:/Users/rasmu/firev
git status
```

If any files are modified, commit: `git commit -am "rename: firev -> arnold (package metadata fixes)"`. Else skip.

### Task 5: Verify Python backend still imports and type-checks

**Files:** None (verification only)

- [ ] **Step 1: Syntax check via ruff**

```bash
cd c:/Users/rasmu/firev
ruff check backend/src/ arnoldsports/ 2>&1 | tail -20
```

Expected: No "undefined name" or "import error" introduced by the rename. Pre-existing ruff warnings are fine; watch for new `F821` (undefined name) or `E999` (syntax error).

- [ ] **Step 2: Run pytest collection (no execution)**

```bash
cd c:/Users/rasmu/firev/backend
python -m pytest --collect-only -q 2>&1 | tail -10
```

Expected: All tests collected without import errors.

- [ ] **Step 3: If errors, fix and recommit**

Any import errors indicate a missed reference (e.g., `from firevsports...` not caught by sed because of a case mismatch). Fix inline and commit: `git commit -am "rename: fix missed references"`.

### Task 6: Verify frontends build

**Files:** None (verification only)

- [ ] **Step 1: Arnoldsports frontend**

```bash
cd c:/Users/rasmu/firev/arnoldsports/frontend
npm install 2>&1 | tail -5
npm run build 2>&1 | tail -10
```

Expected: Build succeeds. If import paths reference `firev...`, the sed pass should have caught them — but double-check errors.

- [ ] **Step 2: Arnoldstocks frontend**

```bash
cd c:/Users/rasmu/firev/arnoldstocks/frontend
npm install 2>&1 | tail -5
npm run build 2>&1 | tail -10
```

Expected: Build succeeds.

- [ ] **Step 3: If errors, fix and commit**

Fix missed references inline; commit: `git commit -am "rename: fix frontend build"`.

### Task 7: Pin Docker compose project name to `arnold`

**Files:** `docker-compose.yml`

**Context:** Docker compose derives the project name from the parent directory by default. Pinning it explicitly prevents breakage when the server dir is still `/opt/firev` but we want the new project/volume prefix. Also future-proofs against directory renames.

- [ ] **Step 1: Read current compose file**

```bash
cd c:/Users/rasmu/firev
head -10 docker-compose.yml
```

- [ ] **Step 2: Add `name: arnold` as top-level key**

Add to the top of `docker-compose.yml` (below version if present, else at top):

```yaml
name: arnold
```

This is already in the file if Task 2's sed substituted `firev` in a `name:` field — verify it exists. If not, add it.

- [ ] **Step 3: Verify compose parses**

```bash
cd c:/Users/rasmu/firev
docker compose config --quiet
```

Expected: No output (success). If parse error, fix the YAML.

- [ ] **Step 4: Commit if changed**

```bash
cd c:/Users/rasmu/firev
git diff --stat docker-compose.yml
```

If modified: `git commit -am "rename: pin docker compose project name to arnold"`. Else skip.

### Task 8: Local Docker smoke test (optional — skip if no local Docker setup)

**Files:** None (verification only)

- [ ] **Step 1: Check if local Docker is expected to work**

Per CLAUDE.md, "Do NOT try to run the backend locally — it's deployed." Skip this task unless user explicitly wants a local smoke test.

- [ ] **Step 2: If skipping, document and proceed**

Commit message or PR note: "Local Docker smoke test skipped — server is source of truth per CLAUDE.md."

### Task 9: Push rename branch and open PR

**Files:** None (git remote)

- [ ] **Step 1: Push branch**

```bash
cd c:/Users/rasmu/firev
git push -u origin rename/firev-to-arnold
```

Expected: Branch created on origin.

- [ ] **Step 2: Open PR against main**

```bash
gh pr create --title "Rename firev -> arnold" --body "$(cat <<'EOF'
## Summary
- Content replace across ~120 files: firev/Firev/FIREV → arnold/Arnold/ARNOLD
- Directory renames: firevsports/ → arnoldsports/, firevstocks/ → arnoldstocks/
- Batch launchers renamed
- Docker compose project name pinned to `arnold`
- Historical plans/specs in docs/superpowers/{plans,specs}/ intentionally left unchanged

## What this PR does NOT do
- Server rename (/opt/firev → /opt/arnold) — executed post-merge as a separate deploy phase
- PostgreSQL DB rename — post-merge
- GitHub repo rename — manual, user will do via GitHub UI
- Local working-dir rename (c:\Users\rasmu\firev) — user does manually last

## Test plan
- [ ] pytest --collect-only passes
- [ ] ruff check passes (no new errors)
- [ ] arnoldsports/frontend npm run build succeeds
- [ ] arnoldstocks/frontend npm run build succeeds
- [ ] No remaining firev/Firev/FIREV occurrences outside docs/superpowers/{plans,specs}/

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL returned.

- [ ] **Step 3: Checkpoint — request user review**

Tell user: "Phase 1 complete. PR #N open. Review + merge to main when ready. Next phase = production server rename (destructive, requires brief downtime). Confirm before proceeding."

---

## Phase 2: Production server rename (destructive, requires maintenance window)

### Task 10: Backup databases before anything

**Files:** None (server-side only)

- [ ] **Step 1: Acquire deploy lock**

```bash
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh status"
```

Expected: No deploy in progress. If one is running, wait.

- [ ] **Step 2: Dump both databases**

```bash
ssh root@148.251.40.251 "cd /opt/firev && mkdir -p /root/pre-rename-backup && docker compose exec -T postgres pg_dump -U firev -Fc firev > /root/pre-rename-backup/firev.dump && docker compose exec -T postgres pg_dump -U firev -Fc market > /root/pre-rename-backup/market.dump && ls -lh /root/pre-rename-backup/"
```

Expected: Two `.dump` files, non-zero size. Record sizes for later verification.

- [ ] **Step 3: Verify dumps are restorable (metadata check)**

```bash
ssh root@148.251.40.251 "pg_restore -l /root/pre-rename-backup/firev.dump | head -5 && pg_restore -l /root/pre-rename-backup/market.dump | head -5"
```

Expected: TOC entries listed — dumps are intact.

### Task 11: Merge rename PR and pull on server

**Files:** None (git)

- [ ] **Step 1: Merge the PR (locally or via gh)**

```bash
gh pr merge <PR-number> --squash
```

- [ ] **Step 2: Stop containers before pulling (so renamed paths don't confuse a running stack)**

```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose down"
```

Expected: All containers stopped.

- [ ] **Step 3: Rename the server directory**

```bash
ssh root@148.251.40.251 "mv /opt/firev /opt/arnold && ls -la /opt/arnold | head -5"
```

Expected: `/opt/arnold` exists, old `/opt/firev` gone.

- [ ] **Step 4: Pull latest from renamed remote path**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && git pull origin main"
```

Expected: Fast-forward pull succeeds, working tree now contains `arnoldsports/`, `arnoldstocks/`, and all renamed content.

### Task 12: Migrate PostgreSQL databases (firev → arnold)

**Files:** None (DB only)

**Context:** We'll rename the main DB `firev` → `arnold`. The `market` DB keeps its name (it's a domain name, not a project name — confirm with user if unsure). If user wants `market` unchanged, skip that portion.

- [ ] **Step 1: Start only postgres to do the rename**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose up -d postgres && sleep 5"
```

Expected: Postgres container running.

- [ ] **Step 2: Rename DB user `firev` → `arnold`**

```bash
ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml exec -T postgres psql -U firev -d postgres -c 'ALTER USER firev RENAME TO arnold;'"
```

Expected: `ALTER ROLE`. Note: Password does NOT change on rename in PG ≥ 10, but per PG docs, if `md5` was used for the password hash, it MUST be reset. Test in step 4 — if auth fails, reset password.

- [ ] **Step 3: Rename database `firev` → `arnold`**

```bash
ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml exec -T postgres psql -U arnold -d postgres -c 'ALTER DATABASE firev RENAME TO arnold;'"
```

Expected: `ALTER DATABASE`. If it errors with "database is being accessed by other users", run `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='firev';` first.

- [ ] **Step 4: Verify new connection string works**

```bash
ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml exec -T postgres psql -U arnold -d arnold -c '\dt' | head -10"
```

Expected: Tables listed. If auth fails: `ALTER USER arnold WITH PASSWORD '<same-password-from-env>';`.

- [ ] **Step 5: If password reset was needed, verify DB_PASSWORD still in .env**

```bash
ssh root@148.251.40.251 "grep DB_PASSWORD /opt/arnold/.env"
```

Expected: Password unchanged — it's the same secret, just bound to renamed user.

### Task 13: Rename Docker volumes (via prune + restore, since Docker doesn't support rename)

**Files:** None

**Context:** Old volumes are named `firev_*`. The new compose project name is `arnold`, which will auto-create `arnold_*` volumes on next `up`. We need to either:
- **Option A (preferred):** Keep data in old volumes by mounting them explicitly in compose under the new names (external volume refs)
- **Option B:** Let new volumes be created; restore pg_data from the pg_dump backup; lose ephemeral caches (chrome_profile, certs)

Recommend **Option B** since it's cleaner and the pg_dump restore is straightforward. Chrome profile and certs regenerate automatically.

- [ ] **Step 1: Stop postgres**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose down"
```

- [ ] **Step 2: Bring the full stack up with new volume names**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose up -d postgres"
```

Expected: New `arnold_pg_data` volume created. Postgres starts with an empty data dir.

- [ ] **Step 3: Restore arnold DB (renamed from firev) from backup**

```bash
ssh root@148.251.40.251 "sleep 10 && docker compose -f /opt/arnold/docker-compose.yml exec -T postgres psql -U arnold -d postgres -c 'CREATE DATABASE arnold OWNER arnold;' && docker compose -f /opt/arnold/docker-compose.yml exec -T postgres pg_restore -U arnold -d arnold --clean --if-exists < /root/pre-rename-backup/firev.dump"
```

Expected: Restore completes. Some WARNINGS are OK (e.g., "relation does not exist" for --clean on fresh DB).

- [ ] **Step 4: Restore market DB**

```bash
ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml exec -T postgres psql -U arnold -d postgres -c 'CREATE DATABASE market OWNER arnold;' && docker compose -f /opt/arnold/docker-compose.yml exec -T postgres pg_restore -U arnold -d market --clean --if-exists < /root/pre-rename-backup/market.dump"
```

- [ ] **Step 5: Verify row counts match pre-rename state**

```bash
ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml exec -T postgres psql -U arnold -d arnold -c 'SELECT count(*) FROM events;' && docker compose -f /opt/arnold/docker-compose.yml exec -T postgres psql -U arnold -d market -c 'SELECT count(*) FROM candles_1m;'"
```

Expected: Non-zero row counts. Record numbers — compare to pre-rename baseline if available.

- [ ] **Step 6: Destroy old firev_* volumes (only after full verification)**

```bash
ssh root@148.251.40.251 "docker volume ls --format '{{.Name}}' | grep '^firev_' | xargs -r docker volume rm"
```

Expected: Old volumes removed. If any are still attached (unlikely since compose is on new stack now), investigate.

### Task 14: Update server cron and watchdog

**Files:** `/etc/cron.d/*` on server

- [ ] **Step 1: Find cron entries referencing /opt/firev**

```bash
ssh root@148.251.40.251 "grep -r firev /etc/cron.d/ /etc/crontab 2>/dev/null"
```

Expected: At least one entry: `*/5 * * * * root bash /opt/firev/scripts/container-watchdog.sh >> /var/log/firev-watchdog.log 2>&1`.

- [ ] **Step 2: Update each cron file**

```bash
ssh root@148.251.40.251 "sed -i -E 's|/opt/firev|/opt/arnold|g; s|firev-watchdog|arnold-watchdog|g' /etc/cron.d/firev-watchdog"
```

Adjust path to match the actual filename found in step 1. If file is at a different path, edit accordingly.

- [ ] **Step 3: Rename the cron file itself if it has `firev` in the name**

```bash
ssh root@148.251.40.251 "mv /etc/cron.d/firev-watchdog /etc/cron.d/arnold-watchdog 2>/dev/null; ls /etc/cron.d/"
```

- [ ] **Step 4: Rename log file**

```bash
ssh root@148.251.40.251 "mv /var/log/firev-watchdog.log /var/log/arnold-watchdog.log 2>/dev/null; touch /var/log/arnold-watchdog.log"
```

- [ ] **Step 5: Reload cron**

```bash
ssh root@148.251.40.251 "systemctl reload cron"
```

Expected: No output (success).

### Task 15: Update server .env files

**Files:** `/opt/arnold/.env`, `/opt/arnold/.env.docker`

- [ ] **Step 1: Inspect current env vars**

```bash
ssh root@148.251.40.251 "cat /opt/arnold/.env /opt/arnold/.env.docker 2>/dev/null | grep -iE 'firev|database_url'"
```

Expected output shows `FIREV_API_KEY=...` and likely `DATABASE_URL=postgresql://firev:...@postgres:5432/firev` (or similar).

- [ ] **Step 2: Rewrite env files**

```bash
ssh root@148.251.40.251 "sed -i -E 's/firev/arnold/g; s/FIREV/ARNOLD/g' /opt/arnold/.env /opt/arnold/.env.docker"
```

- [ ] **Step 3: Verify**

```bash
ssh root@148.251.40.251 "grep -E 'API_KEY|DATABASE_URL|DB_' /opt/arnold/.env /opt/arnold/.env.docker"
```

Expected: `ARNOLD_API_KEY=...`, `DATABASE_URL=postgresql://arnold:...@postgres:5432/arnold`.

### Task 16: Rebuild and bring the stack up under new name

**Files:** None

- [ ] **Step 1: Build + start full stack**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```

Expected: Build succeeds, containers start as `arnold-backend-1`, `arnold-nginx-1`, `arnold-postgres-1`. Health check passes within 2 min.

- [ ] **Step 2: Verify container names**

```bash
ssh root@148.251.40.251 "docker ps --format '{{.Names}}\t{{.Status}}'"
```

Expected: Three `arnold-*` containers, all `Up`.

- [ ] **Step 3: Hit health endpoint**

```bash
ssh root@148.251.40.251 "curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/health"
```

Expected: `200`.

- [ ] **Step 4: Hit extraction health endpoint**

```bash
ssh root@148.251.40.251 "curl -s http://localhost:8000/health/extraction | head -c 500"
```

Expected: JSON response with extraction status.

- [ ] **Step 5: Verify HTTPS/basic-auth from outside**

```bash
curl -sk -u firev:<password> https://148.251.40.251/health | head -c 200
```

Note: basic-auth username is still `firev` in `.htpasswd` — this was NOT changed by Task 2 sed (binary-ish file). If user wants basic-auth username changed too:

```bash
ssh root@148.251.40.251 "openssl passwd -apr1 <PASSWORD> | xargs -I{} echo 'arnold:{}' > /opt/arnold/nginx/.htpasswd && docker compose -f /opt/arnold/docker-compose.yml restart nginx"
```

- [ ] **Step 6: Watch logs for 2 minutes to confirm extraction runs**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh logs backend 50"
```

Expected: Normal extraction activity, no crash loops, no DB connection errors.

### Task 17: Checkpoint — production verified

- [ ] **Step 1: Report to user**

"Phase 2 complete. Server now runs as `arnold`. Main DB = `arnold`, containers = `arnold-*`. Old volumes deleted. Extraction running. Next phase = GitHub repo rename (requires user action) + local working-dir rename (requires user action outside Claude session). Proceed?"

---

## Phase 3: External renames (user actions)

### Task 18: Rename GitHub repo (user action)

**Files:** None — GitHub web UI

- [ ] **Step 1: User action: Rename repo**

User goes to https://github.com/blomen/Firev/settings → "Repository name" → change to `Arnold` → Rename.

GitHub keeps redirects automatically, so old clone URLs still work.

- [ ] **Step 2: Update origin on local clone**

```bash
cd c:/Users/rasmu/firev
git remote set-url origin https://github.com/blomen/Arnold.git
git remote -v
```

Expected: `origin  https://github.com/blomen/Arnold.git (fetch/push)`.

- [ ] **Step 3: Update origin on server clone**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && git remote set-url origin https://github.com/blomen/Arnold.git && git remote -v"
```

- [ ] **Step 4: Test fetch**

```bash
cd c:/Users/rasmu/firev
git fetch origin
```

Expected: Fetch succeeds.

### Task 19: Migrate Claude Code memory files

**Files:** `C:\Users\rasmu\.claude\projects\c--Users-rasmu-firev\memory\*`

**Context:** Memory folder path is derived from CWD. When the local directory is renamed (Task 20), Claude Code will look for memory at `c--Users-rasmu-arnold`. We'll prep that folder with updated content now.

- [ ] **Step 1: Copy memory folder to new location**

```bash
cp -r "C:/Users/rasmu/.claude/projects/c--Users-rasmu-firev" "C:/Users/rasmu/.claude/projects/c--Users-rasmu-arnold"
```

Note: The new folder won't be used until after Task 20. Both exist temporarily.

- [ ] **Step 2: Update content of memory files (firev → arnold)**

```bash
cd "C:/Users/rasmu/.claude/projects/c--Users-rasmu-arnold/memory"
for f in *.md; do
  sed -i -E 's/firev/arnold/g; s/Firev/Arnold/g; s/FIREV/ARNOLD/g' "$f"
done
```

- [ ] **Step 3: Rename memory files with `firev` in filename**

```bash
cd "C:/Users/rasmu/.claude/projects/c--Users-rasmu-arnold/memory"
mv project_firevstocks_status.md project_arnoldstocks_status.md
mv project_firevstocks_ui.md project_arnoldstocks_ui.md
```

- [ ] **Step 4: Update MEMORY.md index pointers**

Inspect `MEMORY.md` — any links like `[firevstocks Status](project_firevstocks_status.md)` should now point to the renamed file. Sed pass in step 2 already updated the link text; verify the filename link is also updated (it is, since sed matched `firevstocks` → `arnoldstocks` in both).

```bash
grep -E 'firev' "C:/Users/rasmu/.claude/projects/c--Users-rasmu-arnold/memory/MEMORY.md"
```

Expected: No remaining `firev` — all replaced.

### Task 20: Rename local working directory (USER action, breaks Claude session)

**Files:** `c:\Users\rasmu\firev` → `c:\Users\rasmu\arnold`

- [ ] **Step 1: Exit Claude Code session**

User must close this Claude Code window — renaming the CWD while it's open will fail or corrupt state.

- [ ] **Step 2: Rename directory (Windows)**

In a new terminal:

```powershell
Move-Item -Path "c:\Users\rasmu\firev" -Destination "c:\Users\rasmu\arnold"
```

Or via File Explorer right-click → Rename.

- [ ] **Step 3: Reopen Claude Code in new directory**

User opens `c:\Users\rasmu\arnold` in Claude Code. Memory folder auto-resolves to `c--Users-rasmu-arnold` (prepared in Task 19).

- [ ] **Step 4: Delete old memory folder**

After verifying the new session loads memory correctly:

```bash
rm -rf "C:/Users/rasmu/.claude/projects/c--Users-rasmu-firev"
```

- [ ] **Step 5: Verify launchers still work**

```bash
cd c:/Users/rasmu/arnold
./arnoldsports.bat
```

Expected: Arnoldsports app starts normally (SSH tunnel → server, local FastAPI, browser).

---

## Phase 4: Cleanup

### Task 21: Delete rollback backups (after 7-day safety window)

**Files:** `/root/pre-rename-backup/` on server

- [ ] **Step 1: Wait 7 days after Phase 2 completion**

Keep `/root/pre-rename-backup/firev.dump` + `market.dump` for a week. If production has had no surprises, delete.

- [ ] **Step 2: Remove backups**

```bash
ssh root@148.251.40.251 "rm -rf /root/pre-rename-backup/"
```

### Task 22: Update any remaining external integrations

**Files:** Various external systems

Unsure whether any of these exist — check with user:
- Hetzner Robot server name/label
- Any monitoring dashboards (Grafana, etc.) with hardcoded "firev" labels
- Backup cron destinations (if backups ship to S3/B2 with "firev" in path)
- Discord/Slack integration names
- DNS records if the app has a custom domain

Each handled manually — no automation.

---

## Rollback Plan

If anything goes wrong in Phase 2 (server) and the stack won't come up as `arnold`:

1. **Stop the broken stack:** `ssh root@148.251.40.251 "cd /opt/arnold && docker compose down"`
2. **Rename dir back:** `ssh root@148.251.40.251 "mv /opt/arnold /opt/firev"`
3. **Revert git:** `ssh root@148.251.40.251 "cd /opt/firev && git reset --hard <pre-rename-commit>"`
4. **Restore DB if volumes were destroyed:**
   ```bash
   ssh root@148.251.40.251 "cd /opt/firev && docker compose up -d postgres && sleep 10 && docker compose exec -T postgres pg_restore -U firev -d firev --clean --if-exists < /root/pre-rename-backup/firev.dump && docker compose exec -T postgres pg_restore -U firev -d market --clean --if-exists < /root/pre-rename-backup/market.dump"
   ```
5. **Rebuild:** `ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh rebuild backend"`

Phase 1 (local codebase) is rolled back via `git reset` or `git revert` on the rename branch — no PR merge required if issues surfaced before merge.
