# Profile Selector Restoration — Design

**Date:** 2026-04-19
**Status:** Approved

## Context

Firev's backend already has a complete `Profile` model (`backend/src/db/profiles.py`) and full CRUD API (`backend/src/api/routes/profiles.py`): list, get active, create, update, activate, delete, plus per-profile Kelly/edge/bonus settings. `ProfileRepo.get_active()` is the singleton entry point used throughout the backend.

The frontend once had a `ProfileSelector` dropdown + `useProfiles` hook + full `ProfilePage`. These were deleted in commit `b6c2d4ee` ("refactor: rename dutch → arb; drop server dashboard frontend") when the server dashboard was split off. The `firevsports/frontend` today has `Profile` types and `api/profiles.ts` wired but zero UI — no way to see, switch, or create profiles from the app.

Use case: multi-user (Rasmus + friends/family sharing the server) plus test profiles for trying new features without polluting real bankroll data. People need to switch profiles frequently from anywhere in the app.

## What it does

A dropdown in the header of `App.tsx`, right-aligned next to the Play/Bankroll/Stats tabs. Shows the active profile's name + color dot. Clicking opens a panel with:
- List of all profiles. Each row: color dot, name, `[*]` marker if active, delete `×` button (disabled for active).
- Click a profile row → activate it. Panel closes. All profile-scoped queries refresh.
- Inline "+ Create new" at the bottom: name input + button. Created profile is added to the list but not auto-activated.

Matches the existing firevsports styling (`bg-panel`, `border-border`, `text-muted`, `text-tabBankroll` hot-pink accent, small `text-xs` type).

## Out of scope

- Profile edit UI (Kelly, edge thresholds, bonus settings) — exists in backend, can be added later when needed.
- Per-profile chrome_port browser session isolation — separate concern for the mirror launcher.
- Dedicated ProfilePage tab.

## Architecture

```
App.tsx (header)
  └── <ProfileSelector />
        └── useProfiles()
              ├── useQuery(['profiles'], api.getProfiles)
              ├── useMutation(api.activateProfile)
              │     └── onSuccess → queryClient.invalidateQueries multiple keys
              ├── useMutation(api.createProfile)
              └── useMutation(api.deleteProfile)
```

All three mutations invalidate `['profiles']` at minimum. `activate` additionally invalidates the profile-scoped query keys so the whole UI refreshes to the new profile's data.

## Data contract

Already defined in `firevsports/frontend/src/types/index.ts`:

```typescript
interface Profile {
  id: number;
  name: string;
  bankroll: number;
  currency: string;
  liquid_balance: number;
  kelly_fraction: number;
  min_edge_pct: number;
  min_arb_pct: number;
  max_stake_pct: number;
  bonus_enabled: boolean;
  color: string;          // hex like "#22c55e"
  is_active: boolean;
  created_at: string;
  // ...
}

interface ProfileCreate { name: string; /* rest optional */ }
```

API responses (per `backend/src/api/routes/profiles.py`):
- `GET /api/profiles` → `{ profiles: Profile[], active: Profile | null }`
- `POST /api/profiles` → `{ profile: Profile }`
- `POST /api/profiles/{id}/activate` → `{ profile: Profile, previous: Profile | null }`
- `DELETE /api/profiles/{id}` → `{ success: boolean }` (rejects if active)

## Query invalidation on activate

When a user switches profiles, the frontend must refresh everything that's profile-scoped. The mutation's `onSuccess` calls `queryClient.invalidateQueries` for these root keys:
- `['profiles']` (selector itself)
- `['bankroll']` (exposure, liquid balance, allocate envelope)
- `['bets']` (bet history, pending, etc.)
- `['opportunities']` (play batch)
- `['providers']` (provider balances are profile-scoped via ProfileProviderBalance)

React Query partial-match invalidation catches nested keys like `['bankroll', 'allocate', null]` and `['opportunities', 'play', 'batch']` automatically.

## UI layout

```
┌─ HEADER ────────────────────────────────────────────────────────────────┐
│ FirevSports  ● Play  ● Bankroll  ● Stats        [● Rasmus ▾]            │
└─────────────────────────────────────────────────────────────────────────┘

Dropdown (when open, 280px wide, anchored top-right):
┌─────────────────────────────────────┐
│ ● Rasmus              [*]           │  ← active
│ ● Test profile              [×]     │
│ ● Guest                     [×]     │
├─────────────────────────────────────┤
│ + Create new                        │
│ [_name______________]  [Create]     │
└─────────────────────────────────────┘
```

## Edge cases

- **No profiles** (fresh DB): backend auto-seeds a "default" profile in `ProfileRepo.get_active()`, so list is never empty in practice. UI still guards with `if (!active) return <loading/>`.
- **Active profile delete attempt**: delete button disabled on the active row. If somehow triggered, backend returns 409 and the hook surfaces the error.
- **Create with duplicate name**: backend enforces unique constraint. Hook surfaces the error inline next to the input.
- **Create with empty name**: frontend validation refuses; button disabled until non-whitespace.
- **Race on switch**: optimistic update — mark the clicked profile active immediately; rollback on failure.

## Files to touch

**Create:**
- `firevsports/frontend/src/hooks/useProfiles.ts`
- `firevsports/frontend/src/components/ProfileSelector.tsx`

**Modify:**
- `firevsports/frontend/src/App.tsx` — add selector to the header

**No backend changes.** The existing API is already complete.

## Verification

- Manual: launch firevsports, header shows selector with "default" profile. Create a profile "Test". List updates. Click "Test" → all tabs refresh (provider balances empty for new profile). Switch back to "default" → data returns. Delete "Test" → row disappears. Delete attempt on active profile is blocked.
- Typecheck: `npx tsc -b --noEmit` passes.
- Build: `npm run build` clean.
