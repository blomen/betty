# Profile Selector Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the profile selector dropdown in the `firevsports` header, backed by React Query, so users can view / switch / create / delete profiles from anywhere in the app.

**Architecture:** A `useProfiles` hook wraps the existing `profilesApi` via `@tanstack/react-query`. A `ProfileSelector` component renders a color-dotted trigger + dropdown panel with profile list + inline create form. Mounted into `App.tsx`'s header next to the tab list. On successful activate, the hook invalidates all profile-scoped React Query keys so the rest of the UI refreshes to the new profile's data.

**Tech Stack:** React 19, TypeScript, Vite, @tanstack/react-query v5, Tailwind CSS.

**Spec:** `docs/superpowers/specs/2026-04-19-profile-selector-restoration-design.md`

---

## File Structure

**Create:**
- `firevsports/frontend/src/hooks/useProfiles.ts` — React Query hook exposing `{ profiles, activeProfile, isLoading, activate, create, delete }`
- `firevsports/frontend/src/components/ProfileSelector.tsx` — dropdown UI component, self-contained, consumes `useProfiles`

**Modify:**
- `firevsports/frontend/src/App.tsx` — add `<ProfileSelector />` right-aligned in the header

No backend changes. No type changes — `Profile`, `ProfileCreate`, `ProfileUpdate` already exist in `firevsports/frontend/src/types/index.ts:221-245`.

---

## Task 1: `useProfiles` hook

**Files:**
- Create: `firevsports/frontend/src/hooks/useProfiles.ts`

- [ ] **Step 1: Create the hook**

```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { Profile, ProfileCreate } from '@/types';

// Query keys that must refresh when the active profile changes.
// React Query partial-match catches nested keys like ['bankroll', 'allocate', null].
const PROFILE_SCOPED_KEYS = [
  ['profiles'],
  ['bankroll'],
  ['bets'],
  ['opportunities'],
  ['providers'],
] as const;

export function useProfiles() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['profiles'],
    queryFn: () => api.getProfiles(),
    staleTime: 60_000,
  });

  const invalidateProfileScoped = () => {
    for (const key of PROFILE_SCOPED_KEYS) {
      queryClient.invalidateQueries({ queryKey: key });
    }
  };

  const activate = useMutation({
    mutationFn: (id: number) => api.activateProfile(id),
    onSuccess: invalidateProfileScoped,
  });

  const create = useMutation({
    mutationFn: (data: ProfileCreate) => api.createProfile(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['profiles'] }),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteProfile(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['profiles'] }),
  });

  return {
    profiles: (query.data?.profiles ?? []) as Profile[],
    activeProfile: (query.data?.active ?? null) as Profile | null,
    isLoading: query.isLoading,
    error: query.error,
    activate,
    create,
    remove,
  };
}
```

- [ ] **Step 2: Typecheck**

Run: `cd firevsports/frontend && npx tsc -b --noEmit`
Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add firevsports/frontend/src/hooks/useProfiles.ts
git commit -m "feat(hooks): useProfiles with React Query + cross-key invalidation"
```

---

## Task 2: `ProfileSelector` component

**Files:**
- Create: `firevsports/frontend/src/components/ProfileSelector.tsx`

- [ ] **Step 1: Create the component**

```typescript
import { useEffect, useRef, useState } from 'react';
import { useProfiles } from '@/hooks/useProfiles';

export function ProfileSelector() {
  const { profiles, activeProfile, isLoading, activate, create, remove } = useProfiles();
  const [open, setOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [createError, setCreateError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const handleActivate = async (id: number) => {
    if (id === activeProfile?.id) {
      setOpen(false);
      return;
    }
    try {
      await activate.mutateAsync(id);
      setOpen(false);
    } catch {
      // error surfaced via activate.error; keep dropdown open
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name) return;
    setCreateError(null);
    try {
      await create.mutateAsync({ name });
      setNewName('');
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await remove.mutateAsync(id);
    } catch {
      // surfaced via remove.error
    }
  };

  if (isLoading) {
    return <div className="text-muted text-xs px-2">Loading…</div>;
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 px-2 py-1 text-xs border border-border bg-panel hover:border-tabBankroll transition-colors"
      >
        <span
          className="inline-block w-2 h-2 rounded-full"
          style={{ backgroundColor: activeProfile?.color ?? '#666' }}
        />
        <span className="text-text">{activeProfile?.name ?? 'No profile'}</span>
        <span className="text-muted2">{open ? '▴' : '▾'}</span>
      </button>

      {open && (
        <div className="absolute top-full right-0 mt-1 w-72 z-50 bg-panel border border-border shadow-lg">
          <div className="max-h-64 overflow-y-auto">
            {profiles.map((profile) => {
              const isActive = profile.id === activeProfile?.id;
              return (
                <div
                  key={profile.id}
                  className={`flex items-center justify-between px-2 py-1.5 border-b border-border/50 hover:bg-panel2 ${
                    isActive ? 'bg-panel2' : ''
                  }`}
                >
                  <button
                    onClick={() => handleActivate(profile.id)}
                    className="flex-1 text-left flex items-center gap-2"
                  >
                    <span
                      className="inline-block w-2 h-2 rounded-full"
                      style={{ backgroundColor: profile.color }}
                    />
                    <span className={isActive ? 'text-tabBankroll' : 'text-text'}>
                      {profile.name}
                    </span>
                    {isActive && <span className="text-muted2 text-[10px]">active</span>}
                  </button>
                  {!isActive && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(profile.id);
                      }}
                      title="Delete profile"
                      className="px-1.5 py-0.5 text-muted2 hover:text-danger text-xs"
                    >
                      ×
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          <form
            onSubmit={handleCreate}
            className="flex items-center gap-1 p-2 border-t border-border"
          >
            <input
              type="text"
              value={newName}
              onChange={(e) => {
                setNewName(e.target.value);
                setCreateError(null);
              }}
              placeholder="New profile name"
              className="flex-1 px-2 py-1 bg-panel2 border border-border text-text text-xs"
            />
            <button
              type="submit"
              disabled={!newName.trim() || create.isPending}
              className="px-2 py-1 text-xs bg-tabBankroll/20 text-tabBankroll hover:bg-tabBankroll/30 disabled:opacity-50"
            >
              {create.isPending ? '…' : 'Create'}
            </button>
          </form>
          {createError && (
            <div className="px-2 pb-2 text-[10px] text-danger">{createError}</div>
          )}
          {activate.error && (
            <div className="px-2 pb-2 text-[10px] text-danger">
              {activate.error instanceof Error ? activate.error.message : 'Activation failed'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify Tailwind color names exist in the project**

Run: `grep -n "tabBankroll\|danger\|muted2\|panel2" firevsports/frontend/tailwind.config.js` (or `tailwind.config.ts` — the config file in the project).

Expected: all four color references exist. Specifically:
- `tabBankroll` — should be defined (used elsewhere in `BankrollPage.tsx`)
- `panel2` — should be defined (used elsewhere in `BankrollPage.tsx`)
- `muted2` — should be defined
- `danger` — if NOT defined, fall back to a color that IS defined (e.g. `red-400`, `warning`, or whatever the project uses for destructive actions — scan other components). Also check for `text-red-500` usage as an alternate convention.

If `danger` is not defined, edit the component to use the project's existing destructive color. Record the substitution.

- [ ] **Step 3: Typecheck**

Run: `cd firevsports/frontend && npx tsc -b --noEmit`
Expected: zero errors.

- [ ] **Step 4: Commit**

```bash
git add firevsports/frontend/src/components/ProfileSelector.tsx
git commit -m "feat(ui): ProfileSelector dropdown component"
```

---

## Task 3: Wire selector into header

**Files:**
- Modify: `firevsports/frontend/src/App.tsx`

- [ ] **Step 1: Add import + mount the selector**

Current `App.tsx` header (lines 17-34):

```tsx
    <div className="flex flex-col h-screen bg-zinc-950">
      <div className="flex items-center gap-1 px-3 py-1 border-b border-zinc-800 bg-zinc-900">
        <span className="text-sm font-bold text-orange-500 mr-4">FirevSports</span>
        {TABS.map(tab => (
          <button ... >
            ...
          </button>
        ))}
      </div>
```

Change to:

```tsx
    <div className="flex flex-col h-screen bg-zinc-950">
      <div className="flex items-center gap-1 px-3 py-1 border-b border-zinc-800 bg-zinc-900">
        <span className="text-sm font-bold text-orange-500 mr-4">FirevSports</span>
        {TABS.map(tab => (
          <button ... >
            ...
          </button>
        ))}
        <div className="ml-auto">
          <ProfileSelector />
        </div>
      </div>
```

And add the import near the top:

```tsx
import { ProfileSelector } from './components/ProfileSelector';
```

- [ ] **Step 2: Typecheck and build**

Run: `cd firevsports/frontend && npx tsc -b --noEmit && npm run build`
Expected: both clean (typecheck zero errors, build succeeds).

- [ ] **Step 3: Commit**

```bash
git add firevsports/frontend/src/App.tsx
git commit -m "feat(app): mount ProfileSelector in header"
```

---

## Task 4: Manual verification

The user runs this. Document the expected behavior so the controller can coordinate.

- [ ] **Step 1: Start FirevSports**

Double-click `firevsports/firevsports.bat` or run from a shell.

- [ ] **Step 2: Visual smoke**

Expect:
- Header shows selector top-right with a color dot + current profile name (default: "default").
- Clicking opens a dropdown with all profiles.
- Typing a name + clicking "Create" adds a new profile (not auto-activated).
- Clicking a non-active profile row activates it. All tabs (Play, Bankroll, Stats) refresh to show the new profile's data (Bankroll shows empty balances; Stats shows no bets; etc.).
- The `×` delete button next to non-active profiles removes them from the list.
- The `×` button is hidden on the active profile.

- [ ] **Step 3: Validate query invalidation**

Steps:
1. On "default" profile, note the Bankroll page shows its deposited balance + allocator recommendations.
2. Create a new profile "TestA" and switch to it.
3. Bankroll page should now show 0 kr for all providers and the allocator should recompute for an empty profile.
4. Switch back to "default". Bankroll returns to the original values.

- [ ] **Step 4: Validate errors**

Try to create a profile with the same name as an existing one. Expect an inline red error message at the bottom of the dropdown (backend returns 409 / 400 from unique constraint).

---

## Self-Review Checklist

- [ ] **Spec coverage:**
  - Hook with React Query + invalidation → Task 1 ✓
  - Component with trigger + dropdown + create + delete + active indicator → Task 2 ✓
  - Right-aligned selector in App.tsx header → Task 3 ✓
  - Manual verification covering the happy path + error → Task 4 ✓
  - Edge cases from spec (no profiles — backend-seeded; delete-active blocked — UI hides `×`; duplicate name — error surfaced) → covered across Tasks 2, 4
- [ ] **Placeholder scan:** No TBDs, no "similar to", every code block is complete.
- [ ] **Type consistency:** `useProfiles` returns `{profiles, activeProfile, isLoading, error, activate, create, remove}`. Consumer in `ProfileSelector` uses `remove` (not `delete`, which is a JS keyword). `activate`, `create`, `remove` are all mutation objects exposing `.mutateAsync()`, `.isPending`, `.error`. Consistent.
- [ ] **API alignment:** `api.getProfiles`, `api.createProfile`, `api.activateProfile`, `api.deleteProfile` are all defined in `firevsports/frontend/src/services/api/profiles.ts:4-36`. Verified.
