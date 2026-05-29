# Sharp Odds On-Click Refresh

**Status:** Design approved 2026-05-29
**Owner:** rasmus

## Problem

The Betty arb/value table can show stale sharp-baseline odds. Concrete example: WTA French Open R3, Magda Linette ML. Pinnacle's site at the time of inspection showed 14.51; the Betty UI showed raw 16.35 / devigged 13.75 with +9.2% edge. The drift is large enough that a click-through to place would carry a different real edge than the UI advertises.

Root cause is the snapshot lifecycle:

- `opportunities.odds2` (the devigged sharp fair odds) is written at scan time, keyed off the soft side's upsert ([`opportunity_repo.py:109`](../../../backend/src/repositories/opportunity_repo.py#L109)). When the soft side has not been re-scanned since the last Pinnacle move, the row keeps the old baseline.
- Pinnacle extracts on a ~3 min cycle (60s cooldown + ~130s run).
- The frontend polls `/api/opportunities/play/batch` every 10s ([`PlayPage.tsx:988`](../../../frontend/src/pages/PlayPage.tsx#L988)) and caches the previous batch in `localStorage` for 6h ([`PlayPage.tsx:816-839`](../../../frontend/src/pages/PlayPage.tsx#L816-L839)).

The arb tab already mitigates this on click: `onRowClick` calls `refreshPinnacleMatchup()` ([`PlayPage.tsx:4215`](../../../frontend/src/pages/PlayPage.tsx#L4215)), hitting `/mirror/pinnacle/refresh-matchup/{matchupId}` and persisting via `/api/odds/live-update`. The **value-bet** tab does not — `handleValueBetClick` ([`PlayPage.tsx:4736`](../../../frontend/src/pages/PlayPage.tsx#L4736)) only opens the event in Playwright.

## Goal

When the user clicks any opportunity row (value or arb), the row's sharp baseline gets re-fetched live, the row's devigged fair odds and edge% update in place within ~1–2s, and the row auto-skips if the refreshed edge falls below the value threshold. The refresh targets only the baseline actually used to compute that row's edge (`opportunities.provider2_id`), so Pinnacle-anchored rows hit Pinnacle, prediction-market-anchored rows are handled separately.

## Non-goals

- Adding per-event refresh paths to Polymarket or Kalshi. Polymarket's public API does not expose single-event endpoints today; Kalshi's per-event capability is untested. Rows with those baselines will surface a "no live refresh" affordance and fall back to cached odds. This is a follow-up.
- Refreshing the soft side (`provider1_id`). The Playwright DOM interception that fires on `navigate_to_event` already handles that; the soft odds the user sees on the betslip are live.
- A passive "go stale → re-refresh" loop. Refresh is triggered only by explicit row click. Background freshness stays bounded by the existing 10s poll + 3 min extraction cycle.
- Refactoring the existing arb path beyond consolidating its call into the shared hook.

## Architecture

One backend endpoint, one frontend hook, two consumers.

```
PlayPage row click
  ├─→ existing Playwright navigate (fire-and-forget, unchanged)
  └─→ useSharpRefresh.refresh()
        │
        ├─ POST /mirror/sharp/refresh-event
        │     { provider_id, matchup_id }
        │
        ├─ pinnacle  → mirror/pinnacle live per-matchup fetch
        │              → backend devigs → persists raw to odds table via /api/odds/live-update
        │              → returns { raw, fair, fetched_at }
        │
        └─ polymarket | kalshi  → 501 (deferred)
```

### Backend

**New endpoint:** `POST /mirror/sharp/refresh-event` in [`local/mirror/router.py`](../../../local/mirror/router.py).

Request:
```json
{ "provider_id": "pinnacle", "matchup_id": "1234567" }
```

Response (200):
```json
{
  "raw":  { "home": 1.95, "away": 1.92 },
  "fair": { "home": 2.01, "away": 1.98 },
  "fetched_at": "2026-05-29T01:46:12Z"
}
```

The handler dispatches on `provider_id`:

- `pinnacle` → existing mirror-side Pinnacle fetcher (the one currently behind `/mirror/pinnacle/refresh-matchup/{matchupId}`). The mirror layer is the right home because Pinnacle is fetched via the user's residential proxy through the SSH tunnel — same path the existing refresh-matchup endpoint uses. Devig the raw prices with the multiplicative method the scanner uses (`backend/src/analysis/devig.py`), import that function in mirror code rather than re-implement.
- `polymarket`, `kalshi` → return HTTP 501 with `{"detail": "no per-event refresh for $provider"}`.
- Anything else → 400.

The handler also calls `/api/odds/live-update` with the raw prices, preserving the existing arb path's persistence so the next scanner cycle picks up fresh baselines. This call is fire-and-forget — the response does not wait on it.

**Deprecation:** `/mirror/pinnacle/refresh-matchup/{matchupId}` stays in place to keep arb working during rollout, but `useSharpRefresh` calls only the new endpoint. The old endpoint can be removed once both consumers cut over (see Migration).

### Frontend

**New hook:** `useSharpRefresh` in [`frontend/src/hooks/useSharpRefresh.ts`](../../../frontend/src/hooks/useSharpRefresh.ts) (new file).

Signature:
```ts
type RefreshState = 'idle' | 'refreshing' | 'fresh' | 'stale' | 'unsupported'

interface UseSharpRefreshResult {
  state: RefreshState
  freshFair: Record<string, number> | null       // outcome → fair odds
  freshRaw: Record<string, number> | null
  freshAt: number | null                          // ms epoch
  refresh: () => Promise<void>
}

function useSharpRefresh(
  eventKey: string,                               // dedupe key, typically event_id + market
  baselineProviderId: string | null,
  matchupId: string | null,
): UseSharpRefreshResult
```

Internally:
- An in-flight dedupe map keyed by `eventKey` ensures sibling rows in the same cluster share one request (matches the cluster-dedup invariant in CLAUDE.md).
- `refresh()` returns the existing in-flight promise if one is pending for that key.
- If `baselineProviderId` is `null`, `matchupId` is `null`, or the provider returns 501, the hook lands in `state = 'unsupported'` and `refresh()` resolves immediately without a network call.
- A `staleAt` watchdog (60s) flips `state` from `'fresh'` back to `'stale'` so the UI can re-prompt the user if they linger on the page.

**Edge recompute:** done in the row render. The row already knows `odds1` (soft odds, what the user is taking). Recomputed edge:

```ts
const fair = freshFair?.[outcome] ?? row.fair_odds
const edgePct = (row.odds1 / fair - 1) * 100
```

This matches the scanner's value formula. Recompute happens in `useMemo` keyed on `(freshAt, row.odds1, fair)`.

**Cluster overlay:** when the hook resolves, all sibling rows in the cluster (`_CLUSTER_MEMBERS` on the row data, mirroring [`play_loop.py`](../../../local/mirror/play_loop.py)) read the same `freshFair` and recompute their edges. No per-row refresh storms.

**Auto-skip:** if the recomputed edge ≤ 0% (the value-bet positivity threshold), the row calls the existing `skipRow()` flow and a toast announces the delta: `"edge gone: 9.2% → -1.1%"`. The Playwright navigate has already fired — it lands on a stale-but-open page; the user sees the auto-skip toast and can close.

**Consumers:**

1. `handleValueBetClick` ([`PlayPage.tsx:4736`](../../../frontend/src/pages/PlayPage.tsx#L4736)): wrap with hook, call `refresh()` alongside the existing navigate.
2. Arb `onRowClick` ([`PlayPage.tsx:4276`](../../../frontend/src/pages/PlayPage.tsx#L4276)): replace the inline `refreshPinnacleMatchup` call with `useSharpRefresh.refresh()`. Same persistence, but now goes through the unified endpoint.

### Data flow

```
T+0    user clicks row → handler fires
T+0    Playwright navigate → mirror opens provider page (existing flow)
T+0    refresh() → state='refreshing', row pill replaces edge%
T+1.5  /mirror/sharp/refresh-event returns
T+1.5  backend kicks off fire-and-forget /api/odds/live-update
T+1.5  hook sets freshFair, freshAt, state='fresh'
T+1.5  row recomputes edge inline
T+1.5  if edge ≤ threshold → skipRow() + toast
T+10s  next /api/opportunities/play/batch poll lands
       → if poll's detected_at > freshAt, freshFair clears, state='stale'
T+60s  if still 'fresh', watchdog flips to 'stale'
```

### Stale-vs-poll race

The hook tracks `freshAt`. Each poll batch carries a `detected_at` per row. The row render picks `freshFair` if `freshAt > detected_at`, else the polled value. This means a successful refresh always wins until the scanner produces a newer row.

### Concurrent clicks on cluster siblings

The cluster mechanism in `play_loop.py:_CLUSTER_MEMBERS` already collapses sibling rows for placement. The refresh hook reuses the same event+market key for its dedupe map, so two sibling clicks share one network request. First click triggers; second click's `refresh()` resolves on the same promise. The `freshFair` value is then read by every sibling row's render so they all recompute edge from the same source.

## Error handling

| Failure | Behavior |
|---|---|
| Endpoint 501 (Polymarket/Kalshi) | `state='unsupported'`, no spinner, no toast on first click; row keeps cached fair odds. A small "no live refresh" pill replaces the edge% only after click. |
| Network error / timeout (10s) | `state='stale'`, toast `"refresh failed — using cached odds"`. Existing cached values stay rendered. |
| Pinnacle returns no markets for that matchup | Same as network error. |
| Devig fails (negative-vig market, missing outcomes) | Backend returns 200 with `raw` populated and `fair: null`. Frontend stays in `'stale'`, toast `"could not devig — using cached"`. |
| Backend `/api/odds/live-update` fire-and-forget fails | Logged server-side; user-facing flow unaffected (the response already used the fresh values). |

## Testing

- **Backend unit:** new endpoint dispatches correctly per `provider_id`; pinnacle path calls existing fetcher and devigs; polymarket/kalshi return 501; bad input returns 400.
- **Backend integration:** end-to-end against a recorded Pinnacle matchup fixture — confirm raw + fair + fetched_at populated and `/api/odds/live-update` invoked exactly once.
- **Frontend unit (Vitest):** `useSharpRefresh` dedupes in-flight requests by `eventKey`; transitions through states correctly; 60s watchdog flips to stale.
- **Frontend integration (Playwright if test harness covers PlayPage, else manual):**
  - Click value-bet row → refreshing pill shows → fresh odds + new edge render within 2s.
  - Click row whose post-refresh edge goes negative → auto-skip toast fires; row leaves the table.
  - Click Polymarket-baselined row → unsupported pill, no error.
  - Two cluster siblings clicked in rapid succession → one network request.
  - Poll lands during refresh → fresh values persist until poll has newer `detected_at`.

## Migration

1. Ship the new endpoint + hook + value-bet consumer behind no flag (frontend only).
2. Verify value-bet refresh works against production for one extraction cycle.
3. Cut the arb consumer over from inline `refreshPinnacleMatchup` to the hook.
4. Delete the old `/mirror/pinnacle/refresh-matchup/{matchupId}` route after a week of dual-path confirmation.

No database migration. No backend deploy needed if the endpoint lands in `local/mirror/` — that's local-client code and ships with `betty.bat`. (Per CLAUDE.md rule 13: `local/` + `frontend/` changes do not trigger a backend rebuild.) The `/api/odds/live-update` call already exists.

## Open questions

None. Design decisions captured:

- Sharp set = `provider2_id` per row, not a static list.
- UX: refreshing pill + queued navigate + auto-skip on negative edge.
- Edge recompute on frontend, using the backend-returned `fair` map (so scanner math stays the source of truth, frontend just substitutes).
- Polymarket/Kalshi: explicit 501 + UI affordance, follow-up to add per-event paths.
