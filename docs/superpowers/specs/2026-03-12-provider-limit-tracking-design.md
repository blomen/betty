# Provider Limit Tracking — Design Spec

## Problem

When bookmakers limit a betting account (reduced stakes, market restrictions, account closure), there's no way to record this in the system or correlate it with the betting history that likely triggered it.

## Solution

A new `profile_provider_limits` table that records limits per profile+provider, with an auto-snapshotted summary of betting stats at the moment the limit is detected. Manual entry via the Stats page UI.

## Data Model

### `ProfileProviderLimit` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer PK | Auto-increment |
| `profile_id` | FK → profiles.id | Which betting profile |
| `provider_id` | FK → providers.id | Which bookmaker |
| `limit_type` | String | `stake_limited`, `market_restricted`, `odds_restricted`, `fully_banned` |
| `limit_level` | Integer (1-5) | 1=minor, 2=moderate, 3=severe, 4=gutted, 5=closed |
| `detected_at` | DateTime | When the limit was noticed |
| `notes` | Text, nullable | Free-form details (e.g., "max stake reduced to 50kr on football") |
| `betting_snapshot` | JSON | Auto-captured stats at recording time (see below) |
| `created_at` | DateTime | Row creation timestamp |

**Unique constraint:** `(profile_id, provider_id, limit_type)` — allows multiple limit types per provider (e.g., stake limited + market restricted simultaneously).

### Betting Snapshot JSON Schema

Auto-captured from the `bets` table for the given profile+provider at recording time:

```json
{
  "total_bets": 47,
  "total_stake": 12500.0,
  "total_profit": 1830.0,
  "win_rate": 0.53,
  "roi_pct": 14.6,
  "avg_clv_pct": 3.2,
  "avg_odds": 2.15,
  "account_age_days": 34,
  "sport_breakdown": {"football": 28, "ice_hockey": 12, "basketball": 7},
  "bet_type_breakdown": {"value": 40, "boost": 5, "reverse": 2},
  "market_breakdown": {"1x2": 30, "spread": 10, "total": 7},
  "bonus_bets": 3,
  "last_bet_date": "2026-03-10T14:30:00Z"
}
```

## API

All endpoints scoped to the active profile.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/limits` | List all limits (filter by `profile_id`, `provider_id`) |
| `POST` | `/api/limits` | Record a new limit — auto-snapshots betting stats |
| `PUT` | `/api/limits/{id}` | Update limit (change level, type, notes) |
| `DELETE` | `/api/limits/{id}` | Remove a limit record |

### POST /api/limits request body

```json
{
  "provider_id": "unibet",
  "limit_type": "stake_limited",
  "limit_level": 3,
  "detected_at": "2026-03-12T10:00:00Z",
  "notes": "Max stake reduced to 50kr on football 1x2"
}
```

`betting_snapshot` is NOT in the request — the service layer generates it automatically from the bets table.

## Service Layer

### `LimitService`

- `record_limit(profile_id, provider_id, limit_type, limit_level, notes, detected_at)` — queries bets for that profile+provider, builds snapshot JSON, creates the record
- `list_limits(profile_id?, provider_id?)` — query with optional filters
- `update_limit(id, ...)` — update mutable fields (level, type, notes)
- `delete_limit(id)` — remove record

### `LimitRepo`

Standard CRUD repository in `repositories/limit_repo.py`.

## Frontend — Stats Page

### New Provider Stats Section

A new table section on the Stats page showing per-provider betting stats for the active profile, with limit actions:

| Provider | Bets | Stake | Profit | ROI% | Avg CLV | Status |
|----------|------|-------|--------|------|---------|--------|
| unibet   | 47   | 12.5k | +1.8k | 14.6% | 3.2% | [Mark Limited] |
| betsson  | 23   | 8.2k  | +420  | 5.1% | 1.8% | Limited (3/5) |

- Styled with `tabStats` (cyan) accent, `sq` compact table class
- "Mark Limited" button opens inline form: limit type dropdown, severity 1-5, optional notes text field
- Already-limited providers show level badge, clickable to edit or remove
- Data sourced from a new `/api/bankroll/provider-stats` endpoint (or extend existing stats endpoint)

## File Changes

### New files
- `backend/src/repositories/limit_repo.py` — LimitRepo CRUD
- `backend/src/services/limit_service.py` — LimitService with snapshot generation
- `backend/src/api/routes/limits.py` — Thin route handlers

### Modified files
- `backend/src/db/models.py` — Add `ProfileProviderLimit` model + migration
- `backend/src/api/routes/__init__.py` — Register limits router
- `frontend/src/components/Terminal/pages/StatsPage.tsx` — Add provider stats section with limit actions
- `frontend/src/services/api.ts` — Add limit API methods + provider stats fetch
