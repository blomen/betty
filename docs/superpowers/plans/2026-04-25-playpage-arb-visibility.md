# PlayPage Arb Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide empty cluster headers in the PlayPage Arbitrage sub-tab; surface drained clusters only when a "massive arb" (≥ 2.5%) makes a deposit worthwhile; remove now-unused bonus tracking from the page.

**Architecture:** Pure frontend change in a single React component. The cluster list is currently seeded from canonical sibling lists regardless of signals; we replace that with a signal-driven filter and add a "deposit-hint" rendering branch. Bonus state removal is a parallel cleanup since the new strategy ("arb-and-bleed-down") makes bonus tracking irrelevant in this UI.

**Tech Stack:** React 19, TypeScript 5.6, Vite 6, Tailwind 3.4. No test framework configured for the frontend; verification is `npx tsc --noEmit` (the existing CI gate at `.github/workflows/ci.yml`) plus visual check via `npm run dev` and the running Arnold launcher.

**Spec:** [docs/superpowers/specs/2026-04-25-playpage-arb-visibility-design.md](../specs/2026-04-25-playpage-arb-visibility-design.md)

---

## Pre-flight verification

Before starting, confirm the working tree is clean and TypeScript currently compiles. The plan must not bury a pre-existing compile error under its own changes.

- [ ] **Step P1: Confirm working tree clean (or only contains the two staged hunks the user mentioned)**

Run: `git status`
Expected: only `arnold/frontend/src/pages/PlayPage.tsx` and `backend/src/services/market_service.py` modified (these are pre-existing per the session start state). No other unexpected modifications.

If anything else is dirty, stop and check with the user before continuing.

- [ ] **Step P2: Confirm baseline TypeScript compile passes**

Run: `cd arnold/frontend && npx tsc --noEmit`
Expected: exit code 0, no errors. (Pre-existing edit on `PlayPage.tsx` should already type-check.)

If errors appear, fix or revert the pre-existing change before starting Task 1.

---

## Task 1: Raise drain threshold and add deposit-hint constant

**Files:**
- Modify: `arnold/frontend/src/pages/PlayPage.tsx:8-11` (constants block near top of file)

The two constants drive everything downstream. We change `DRAIN_THRESHOLD_SEK` from 1 to 20 (only count balances that can actually place a bet) and add `DEPOSIT_HINT_MIN_PROFIT_PCT = 2.5` (minimum guaranteed profit % for a drained cluster to surface as a deposit hint).

- [ ] **Step 1.1: Replace the threshold constant block**

Find this exact block at the top of `PlayPage.tsx`:

```ts
// Provider is "drained" when balance falls below this threshold (SEK).
// Keep small — we always play the remaining balance down, threshold just avoids
// residual-micro-balance bugs (1-2 SEK stuck from rounded stakes, refunds).
const DRAIN_THRESHOLD_SEK = 1
```

Replace with:

```ts
// Provider is "drained" when balance falls below this threshold (SEK).
// Below this, no meaningful bet can be placed after odds rounding and
// provider-side minimum stakes (typically 5-10 kr), so the residue is
// not actionable.
const DRAIN_THRESHOLD_SEK = 20

// Minimum guaranteed_profit_pct an arb must show for a fully-drained
// cluster (no funded members) to be surfaced as a deposit hint. Tuned
// to clear realistic execution costs: ~0.5-1.5% on Pinnacle-hedged
// arbs, ~1.5-4% on Kalshi/Polymarket-hedged arbs (slippage + spread +
// per-contract fees).
const DEPOSIT_HINT_MIN_PROFIT_PCT = 2.5
```

- [ ] **Step 1.2: Type-check**

Run: `cd arnold/frontend && npx tsc --noEmit`
Expected: exit code 0, no errors.

If errors mention `DEPOSIT_HINT_MIN_PROFIT_PCT` being unused, ignore for now — it's consumed in Task 4. TypeScript does not warn on unused top-level `const` by default; if a linter does, suppress until Task 4 lands.

- [ ] **Step 1.3: Commit**

```bash
git add arnold/frontend/src/pages/PlayPage.tsx
git commit -m "feat(playpage): raise drain threshold to 20 kr, add deposit-hint constant

Below 20 kr the residual is not actionable after odds rounding and
provider-side minimum stakes. New DEPOSIT_HINT_MIN_PROFIT_PCT (2.5%)
will be used by the cluster filter in a follow-up commit."
```

---

## Task 2: Strip bonus state and bonus-pill rendering

**Files:**
- Modify: `arnold/frontend/src/pages/PlayPage.tsx` — multiple regions (state declaration, load() extraction loop, allKnownPids set, isDone, bonus consumer in funded card, the `nonFunded.map` pill row).

After this task: the PlayPage no longer reads or displays bonus state from the backend. Cluster visibility is **not yet** changed — empty cluster headers still appear, just without the amber bonus pills.

- [ ] **Step 2.1: Remove the `providerBonuses` state declaration**

Find:

```ts
  const [providerBalances, setProviderBalances] = useState<Record<string, number>>({})
  // Per-provider bonus_amount (sourced from balance_status). Used alongside balance
  // to detect when a provider is fully "done" (no cash AND no bonus left).
  const [providerBonuses, setProviderBonuses] = useState<Record<string, number>>({})
  const [pendingByProvider, setPendingByProvider] = useState<Record<string, any[]>>({})
```

Replace with:

```ts
  const [providerBalances, setProviderBalances] = useState<Record<string, number>>({})
  const [pendingByProvider, setPendingByProvider] = useState<Record<string, any[]>>({})
```

- [ ] **Step 2.2: Remove the bonus-extraction loop in `load()`**

Find this block (currently around line 157-164):

```ts
      setProviderBalances(result.provider_balances ?? {})
      setPlacedToday(result.placed_today ?? {})
      // balance_status carries per-provider bonus_amount; extract for "done" detection
      const bonuses: Record<string, number> = {}
      for (const entry of result.balance_status ?? []) {
        if (entry?.provider_id && typeof entry.bonus_amount === 'number') {
          bonuses[entry.provider_id] = entry.bonus_amount
        }
      }
      setProviderBonuses(bonuses)
      const grouped: Record<string, any[]> = {}
```

Replace with:

```ts
      setProviderBalances(result.provider_balances ?? {})
      setPlacedToday(result.placed_today ?? {})
      const grouped: Record<string, any[]> = {}
```

- [ ] **Step 2.3: Remove `providerBonuses` from `allKnownPids`**

Find (currently around line 791-795):

```ts
          const allKnownPids = new Set([
            ...Object.keys(providerBalances),
            ...Object.keys(providerBonuses),
            ...Object.keys(pendingByProvider),
          ])
```

Replace with:

```ts
          const allKnownPids = new Set([
            ...Object.keys(providerBalances),
            ...Object.keys(pendingByProvider),
          ])
```

- [ ] **Step 2.4: Replace the per-provider classifier block (drop `isDone` and the bonus check from the legacy version)**

Find (currently around line 813-823):

```ts
          // Classify a provider:
          //   funded — has cash balance OR pending bets (worth a full card)
          //   done   — no balance, no bonus, no pending (fully depleted — red ✕)
          //   drained-but-live — in between (bonus-only): amber italic pill
          const isFunded = (pid: string) =>
            (providerBalances[pid] ?? 0) >= DRAIN_THRESHOLD_SEK ||
            (pendingByProvider[pid]?.length ?? 0) > 0
          const isDone = (pid: string) =>
            (providerBalances[pid] ?? 0) < DRAIN_THRESHOLD_SEK &&
            (providerBonuses[pid] ?? 0) < DRAIN_THRESHOLD_SEK &&
            (pendingByProvider[pid]?.length ?? 0) === 0
```

Replace with:

```ts
          // Classify a provider:
          //   funded   — has cash balance >= DRAIN_THRESHOLD_SEK OR pending bets
          //   unfunded — anything else; cluster shows only if a qualifying arb
          //              exists (deposit-hint mode) — see Task 3.
          const isFunded = (pid: string) =>
            (providerBalances[pid] ?? 0) >= DRAIN_THRESHOLD_SEK ||
            (pendingByProvider[pid]?.length ?? 0) > 0
```

- [ ] **Step 2.5: Replace the `nonFunded`/`funded` partition (drop `nonFunded`, since pills are gone)**

Find (currently around line 845-848):

```ts
                  {clusterOrder.map(cluster => {
                    const members = softByCluster[cluster]
                    const funded = members.filter(isFunded)
                    const nonFunded = members.filter(pid => !isFunded(pid) && !isDone(pid))
                    const opps = oppsByCluster[cluster] ?? []
                    const clusterMemberSet = new Set(members)
```

Replace with:

```ts
                  {clusterOrder.map(cluster => {
                    const members = softByCluster[cluster]
                    const funded = members.filter(isFunded)
                    const opps = oppsByCluster[cluster] ?? []
                    const clusterMemberSet = new Set(members)
```

- [ ] **Step 2.6: Remove the amber bonus-pill rendering inside the cluster header**

Find (currently around line 858-870):

```tsx
                          <span className="text-[10px] font-bold text-purple-300 uppercase tracking-wider">
                            {cluster}
                          </span>
                          {nonFunded.map(pid => (
                            <span
                              key={pid}
                              className="px-1.5 py-0.5 text-[10px] rounded border inline-flex items-center gap-1 text-amber-500/70 bg-zinc-900/50 border-zinc-800 italic"
                              title={`${pid} has bonus remaining`}
                            >
                              <span className="uppercase">{pid}</span>
                            </span>
                          ))}
                          <span className="text-[10px] text-zinc-600 ml-auto">
                            {funded.length > 0 ? `${opps.length} arb${opps.length === 1 ? '' : 's'} · siblings share odds` : 'no funded siblings'}
                          </span>
```

Replace with:

```tsx
                          <span className="text-[10px] font-bold text-purple-300 uppercase tracking-wider">
                            {cluster}
                          </span>
                          <span className="text-[10px] text-zinc-600 ml-auto">
                            {funded.length > 0 ? `${opps.length} arb${opps.length === 1 ? '' : 's'} · siblings share odds` : 'no funded siblings'}
                          </span>
```

(The `'no funded siblings'` text will be replaced in Task 4 with the deposit-hint variant. Leaving the today-text in place keeps Task 2 a clean removal.)

- [ ] **Step 2.7: Remove the `bonus` consumer line in the funded provider card**

Find (currently around line 873-877):

```tsx
                        {funded.map(pid => {
                          const bal = providerBalances[pid] ?? 0
                          const bonus = providerBonuses[pid] ?? 0
                          const pending = pendingByProvider[pid]?.length ?? 0
                          const isSkinActive = activeProviders.has(pid)
                          const isLoggedIn = loggedInProviders.has(pid)
```

Replace with:

```tsx
                        {funded.map(pid => {
                          const bal = providerBalances[pid] ?? 0
                          const pending = pendingByProvider[pid]?.length ?? 0
                          const isSkinActive = activeProviders.has(pid)
                          const isLoggedIn = loggedInProviders.has(pid)
```

- [ ] **Step 2.8: Verify no remaining references**

Run: `grep -n "providerBonuses\|setProviderBonuses\|nonFunded\|isDone\|bonus_amount" arnold/frontend/src/pages/PlayPage.tsx`
Expected: no matches.

If any remain, find and remove them. (None should remain in PlayPage.tsx; the backend's `bonus_amount` field on `balance_status` is left untouched because BankrollPage and capital plan still consume it elsewhere — verify with `grep -n "bonus_amount" arnold/frontend/src/` and confirm references outside PlayPage are intact.)

- [ ] **Step 2.9: Type-check**

Run: `cd arnold/frontend && npx tsc --noEmit`
Expected: exit code 0, no errors.

- [ ] **Step 2.10: Commit**

```bash
git add arnold/frontend/src/pages/PlayPage.tsx
git commit -m "refactor(playpage): drop bonus state from arb section

Strategy is arb-and-bleed-down — bonus money is bet down identically
to cash, so tracking bonus separately adds noise. Removes
providerBonuses state, the balance_status extraction loop, the
isDone classifier, and the amber bonus pills from cluster headers.

Bonus state on the backend (balance_status[i].bonus_amount) is left
in place because BankrollPage and capital plan still consume it."
```

---

## Task 3: Hide clusters with no funded members and no qualifying arb

**Files:**
- Modify: `arnold/frontend/src/pages/PlayPage.tsx` — the `clusterOrder` computation inside the `subTab === 'arb'` block.

After this task: empty cluster headers no longer render. Drained clusters with no qualifying arbs disappear; drained clusters that *do* have a ≥ 2.5% arb still render today's "no funded siblings" header (the deposit-hint copy and arb-row filter come in Task 4).

- [ ] **Step 3.1: Read the current cluster-seeding region to anchor the edit**

Run: `Read arnold/frontend/src/pages/PlayPage.tsx offset=781 limit=35`
Confirm the region shown matches what Step 3.2 expects to replace. Line numbers will have shifted slightly after Task 2 — adjust the `offset` if needed; what matters is the textual match below.

- [ ] **Step 3.2: Add the visibility filter to `clusterOrder`**

Find this block (the cluster-seeding + sort, currently spanning roughly lines 780-811 — look for the comment beginning `// Group ALL known soft providers`):

```ts
          // Group ALL known soft providers by cluster — every sibling from
          // SOFT_CLUSTER_MEMBERS, every standalone from SOFT_STANDALONES, and any
          // additional provider we have balance / bonus / pending data for.
          // Even untouched providers stay visible (rendered as ✕ done) so the
          // user can see the full universe at a glance.
          const softByCluster: Record<string, string[]> = {}
          // Seed with all canonical siblings
          for (const [cluster, members] of Object.entries(SOFT_CLUSTER_MEMBERS)) {
            softByCluster[cluster] = [...members]
          }
          // Seed standalones — each is its own one-provider "cluster"
          for (const pid of SOFT_STANDALONES) {
            if (!softByCluster[pid]) softByCluster[pid] = [pid]
          }
          // Add any extra provider we have data for that wasn't in the canonical lists
          const allKnownPids = new Set([
            ...Object.keys(providerBalances),
            ...Object.keys(pendingByProvider),
          ])
          for (const pid of allKnownPids) {
            if (UNLIMITED_PROVIDERS.has(pid)) continue
            const cluster = resolveSoftCluster(pid)
            if (!softByCluster[cluster]) softByCluster[cluster] = []
            if (!softByCluster[cluster].includes(pid)) softByCluster[cluster].push(pid)
          }
          // Stable sort order — named clusters first, then standalones alphabetically
          const namedClusters = Object.keys(SOFT_CLUSTER_MEMBERS)
          const clusterOrder = Object.keys(softByCluster).sort((a, b) => {
            const ai = namedClusters.indexOf(a)
            const bi = namedClusters.indexOf(b)
            if (ai >= 0 && bi >= 0) return ai - bi
            if (ai >= 0) return -1
            if (bi >= 0) return 1
            return a.localeCompare(b)
          })
```

Replace with (note: the `isFunded` definition is moved up so the filter can use it; this leaves the existing later `const isFunded = ...` block to be removed in Step 3.3):

```ts
          // Build the soft-cluster universe: canonical siblings + standalones +
          // any provider we have current balance/pending data for. Then filter
          // to only clusters with at least one funded member, OR (drained but)
          // at least one arb opp clearing DEPOSIT_HINT_MIN_PROFIT_PCT.
          const softByCluster: Record<string, string[]> = {}
          for (const [cluster, members] of Object.entries(SOFT_CLUSTER_MEMBERS)) {
            softByCluster[cluster] = [...members]
          }
          for (const pid of SOFT_STANDALONES) {
            if (!softByCluster[pid]) softByCluster[pid] = [pid]
          }
          const allKnownPids = new Set([
            ...Object.keys(providerBalances),
            ...Object.keys(pendingByProvider),
          ])
          for (const pid of allKnownPids) {
            if (UNLIMITED_PROVIDERS.has(pid)) continue
            const cluster = resolveSoftCluster(pid)
            if (!softByCluster[cluster]) softByCluster[cluster] = []
            if (!softByCluster[cluster].includes(pid)) softByCluster[cluster].push(pid)
          }

          // Funded check (used by both visibility filter and per-cluster render)
          const isFunded = (pid: string) =>
            (providerBalances[pid] ?? 0) >= DRAIN_THRESHOLD_SEK ||
            (pendingByProvider[pid]?.length ?? 0) > 0

          // Visibility: cluster shows if any member is funded, OR if the cluster
          // has a qualifying arb opp (>= DEPOSIT_HINT_MIN_PROFIT_PCT). Drained
          // clusters with no qualifying arb are hidden entirely.
          const clusterHasFunded = (cluster: string) =>
            (softByCluster[cluster] ?? []).some(isFunded)
          const clusterHasQualifyingArb = (cluster: string) =>
            (oppsByCluster[cluster] ?? []).some(
              (o: any) => (o.guaranteed_profit_pct ?? 0) >= DEPOSIT_HINT_MIN_PROFIT_PCT,
            )
          const visibleClusters = Object.keys(softByCluster).filter(
            c => clusterHasFunded(c) || clusterHasQualifyingArb(c),
          )

          // Stable sort: named clusters first, then standalones alphabetically
          const namedClusters = Object.keys(SOFT_CLUSTER_MEMBERS)
          const clusterOrder = visibleClusters.sort((a, b) => {
            const ai = namedClusters.indexOf(a)
            const bi = namedClusters.indexOf(b)
            if (ai >= 0 && bi >= 0) return ai - bi
            if (ai >= 0) return -1
            if (bi >= 0) return 1
            return a.localeCompare(b)
          })
```

- [ ] **Step 3.3: Remove the now-duplicate `isFunded` definition further down**

After Task 2, the page has a single `isFunded` block (the legacy `isDone` was removed). Step 3.2 hoisted a fresh `isFunded` upward. Find the original definition (still in place from Task 2's Step 2.4):

```ts
          // Classify a provider:
          //   funded   — has cash balance >= DRAIN_THRESHOLD_SEK OR pending bets
          //   unfunded — anything else; cluster shows only if a qualifying arb
          //              exists (deposit-hint mode) — see Task 3.
          const isFunded = (pid: string) =>
            (providerBalances[pid] ?? 0) >= DRAIN_THRESHOLD_SEK ||
            (pendingByProvider[pid]?.length ?? 0) > 0
```

Delete this block entirely (the hoisted definition from Step 3.2 is now the only one).

- [ ] **Step 3.4: Type-check**

Run: `cd arnold/frontend && npx tsc --noEmit`
Expected: exit code 0, no errors. If TypeScript complains about a duplicate `isFunded` declaration, Step 3.3 wasn't fully applied — re-check.

- [ ] **Step 3.5: Commit**

```bash
git add arnold/frontend/src/pages/PlayPage.tsx
git commit -m "feat(playpage): hide soft clusters with no funded members and no big arb

Cluster shows only if at least one sibling has balance >=20 kr or pending
bets, OR an arb in that cluster clears DEPOSIT_HINT_MIN_PROFIT_PCT (2.5%).
Eliminates the bare SPECTATE/COMEON_GROUP/10BET/BETHARD/COOLBET headers
that appeared when fully drained."
```

---

## Task 4: Add deposit-hint render mode for drained-but-arb clusters

**Files:**
- Modify: `arnold/frontend/src/pages/PlayPage.tsx` — inside the per-cluster `.map(cluster => ...)` block, branch on whether any member is funded.

After this task: a drained cluster that survived the filter (because of a ≥ 2.5% arb) renders as a header + a filtered arb table, with no provider cards and a clear "deposit to play" tail message. Funded clusters render unchanged from today.

- [ ] **Step 4.1: Read the current per-cluster render block**

Run: `Read arnold/frontend/src/pages/PlayPage.tsx offset=843 limit=80`
Adjust offset if line numbers shifted. Confirm you can see the block starting `{clusterOrder.map(cluster => {` and the header block followed by `{funded.map(pid => { ...` (the funded provider cards loop).

- [ ] **Step 4.2: Replace the cluster body to branch on funded/deposit-hint**

Find the current per-cluster body. After Tasks 2 and 3, it looks roughly like:

```tsx
                  {clusterOrder.map(cluster => {
                    const members = softByCluster[cluster]
                    const funded = members.filter(isFunded)
                    const opps = oppsByCluster[cluster] ?? []
                    const clusterMemberSet = new Set(members)

                    return (
                      <div key={cluster} className="border-b border-zinc-800/50 last:border-b-0">
                        {/* Cluster header — label + non-funded sibling pills (bonus remaining) + opp count */}
                        <div className="flex items-center gap-2 px-3 py-1 bg-zinc-900/40 border-b border-zinc-800/50 flex-wrap">
                          <span className="text-[10px] font-bold text-purple-300 uppercase tracking-wider">
                            {cluster}
                          </span>
                          <span className="text-[10px] text-zinc-600 ml-auto">
                            {funded.length > 0 ? `${opps.length} arb${opps.length === 1 ? '' : 's'} · siblings share odds` : 'no funded siblings'}
                          </span>
                        </div>

                        {/* One card per funded sibling — same opps, different balance/active context */}
                        {funded.map(pid => {
```

Replace just the header block + the opening of `{funded.map(...)}` so the cluster body branches on `funded.length`. The new structure: when funded.length > 0, render today's `funded.map` cards; when zero funded but the cluster survived the filter, render the deposit-hint card with the filtered arb table inline.

Find:

```tsx
                    return (
                      <div key={cluster} className="border-b border-zinc-800/50 last:border-b-0">
                        {/* Cluster header — label + non-funded sibling pills (bonus remaining) + opp count */}
                        <div className="flex items-center gap-2 px-3 py-1 bg-zinc-900/40 border-b border-zinc-800/50 flex-wrap">
                          <span className="text-[10px] font-bold text-purple-300 uppercase tracking-wider">
                            {cluster}
                          </span>
                          <span className="text-[10px] text-zinc-600 ml-auto">
                            {funded.length > 0 ? `${opps.length} arb${opps.length === 1 ? '' : 's'} · siblings share odds` : 'no funded siblings'}
                          </span>
                        </div>

                        {/* One card per funded sibling — same opps, different balance/active context */}
                        {funded.map(pid => {
```

Replace with:

```tsx
                    // Deposit-hint mode: cluster has zero funded members but a
                    // qualifying arb survived the visibility filter. Render the
                    // cluster header + the qualifying arb rows only (no provider
                    // cards, no Place/Skip — user must deposit first).
                    if (funded.length === 0) {
                      const qualifyingOpps = opps.filter(
                        (o: any) => (o.guaranteed_profit_pct ?? 0) >= DEPOSIT_HINT_MIN_PROFIT_PCT,
                      ).slice(0, 10)
                      return (
                        <div key={cluster} className="border-b border-zinc-800/50 last:border-b-0">
                          <div className="flex items-center gap-2 px-3 py-1 bg-zinc-900/40 border-b border-zinc-800/50 flex-wrap">
                            <span className="text-[10px] font-bold text-purple-300 uppercase tracking-wider">
                              {cluster}
                            </span>
                            <span className="px-1.5 py-0.5 text-[10px] rounded bg-amber-900/30 text-amber-400 border border-amber-700/40 uppercase tracking-wider">
                              deposit to play
                            </span>
                            <span className="text-[10px] text-zinc-600 ml-auto">
                              {qualifyingOpps.length} qualifying arb{qualifyingOpps.length === 1 ? '' : 's'} ≥ {DEPOSIT_HINT_MIN_PROFIT_PCT}%
                            </span>
                          </div>
                          <table className="w-full text-xs">
                            <tbody>
                              {qualifyingOpps.map((opp: any, i: number) => {
                                const counterLegs = opp.counter_plan ?? opp.counter_legs ?? opp.legs ?? []
                                const profitPct = opp.guaranteed_profit_pct ?? 0
                                const eventLabel = opp.display_home && opp.display_away
                                  ? `${opp.display_home} v ${opp.display_away}`
                                  : opp.event_id
                                const resolveLegOutcome = (leg: any): string => {
                                  const o = leg?.outcome
                                  if (!o) return '—'
                                  if (o === 'home') return opp.display_home || 'Home'
                                  if (o === 'away') return opp.display_away || 'Away'
                                  if (o === 'draw') return 'Draw'
                                  if (o === 'over' && leg.point != null) return `Over ${leg.point}`
                                  if (o === 'under' && leg.point != null) return `Under ${leg.point}`
                                  if (leg.point != null) return `${o} ${leg.point}`
                                  return o
                                }
                                const anchorLeg =
                                  (opp.legs ?? []).find((l: any) =>
                                    clusterMemberSet.has(l.provider ?? l.provider_id ?? ''),
                                  ) ?? {}
                                const anchorPid = anchorLeg.provider ?? anchorLeg.provider_id ?? cluster
                                const anchorOutcome = resolveLegOutcome(anchorLeg)
                                const counters = (counterLegs as any[]).filter((l: any) => {
                                  const lp = l.provider ?? l.provider_id ?? ''
                                  return !clusterMemberSet.has(lp)
                                })
                                return (
                                  <tr key={`hint-${cluster}-${i}`} className="border-b border-zinc-800/20 hover:bg-zinc-800/40">
                                    <td className="pl-9 pr-2 py-1 font-mono font-semibold text-right w-[60px] text-green-400">
                                      +{profitPct.toFixed(2)}%
                                    </td>
                                    <td className="px-2 py-1 text-zinc-200 max-w-[220px] truncate text-[11px]">{eventLabel}</td>
                                    <td className="px-2 py-1 text-zinc-500 text-[10px] uppercase">{opp.market ?? ''}</td>
                                    <td className="px-2 py-1 text-[11px]">
                                      <span className="text-[9px] text-zinc-500 uppercase tracking-wider mr-1">bet</span>
                                      <span className="text-green-400 font-semibold">{anchorOutcome}</span>
                                      <span className="text-zinc-600 mx-1">on</span>
                                      <span className="text-zinc-400 uppercase text-[10px]">{anchorPid}</span>
                                      <span className="font-mono text-zinc-200 ml-2">@ {Number(anchorLeg.odds ?? 0).toFixed(2)}</span>
                                    </td>
                                    <td className="px-2 py-1 text-[11px]">
                                      <div className="flex flex-col gap-0.5">
                                        {counters.map((leg: any, li: number) => (
                                          <div key={li} className="flex items-center gap-1">
                                            <span className="text-[9px] text-zinc-500 uppercase tracking-wider mr-1">hedge</span>
                                            <span className="text-pink-400 font-semibold">{resolveLegOutcome(leg)}</span>
                                            <span className="text-zinc-600">on</span>
                                            <span className="text-zinc-400 uppercase text-[10px]">{leg.provider ?? leg.provider_id}</span>
                                            <span className="font-mono text-zinc-300 ml-2">@ {Number(leg.odds ?? 0).toFixed(2)}</span>
                                          </div>
                                        ))}
                                      </div>
                                    </td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                        </div>
                      )
                    }

                    return (
                      <div key={cluster} className="border-b border-zinc-800/50 last:border-b-0">
                        {/* Cluster header — funded mode */}
                        <div className="flex items-center gap-2 px-3 py-1 bg-zinc-900/40 border-b border-zinc-800/50 flex-wrap">
                          <span className="text-[10px] font-bold text-purple-300 uppercase tracking-wider">
                            {cluster}
                          </span>
                          <span className="text-[10px] text-zinc-600 ml-auto">
                            {opps.length} arb{opps.length === 1 ? '' : 's'} · siblings share odds
                          </span>
                        </div>

                        {/* One card per funded sibling — same opps, different balance/active context */}
                        {funded.map(pid => {
```

The closing of `{funded.map(...)}` and the surrounding `</div>` are unchanged below; do not edit them.

- [ ] **Step 4.3: Type-check**

Run: `cd arnold/frontend && npx tsc --noEmit`
Expected: exit code 0, no errors.

If you get "Cannot find name 'resolveLegOutcome'" or similar, ensure Step 4.2 inserted the local `resolveLegOutcome` helper inside the deposit-hint branch (it's intentionally scoped local to avoid colliding with the existing `resolveOutcome` further down the file).

- [ ] **Step 4.4: Production build (catches issues `tsc --noEmit` may miss)**

Run: `cd arnold/frontend && npm run build`
Expected: build succeeds, `dist/` is regenerated. No TS errors, no Vite warnings about missing exports.

- [ ] **Step 4.5: Commit**

```bash
git add arnold/frontend/src/pages/PlayPage.tsx
git commit -m "feat(playpage): add deposit-hint mode for drained clusters with massive arbs

When a cluster has zero funded members but at least one arb opp clearing
DEPOSIT_HINT_MIN_PROFIT_PCT (2.5%), render the cluster header + filtered
arb rows without provider cards. Tail message reads 'deposit to play ·
{n} qualifying arbs >= 2.5%' to make the call to action explicit."
```

---

## Task 5: Visual verification

**Files:** none modified.

The plan made no test changes because the frontend has no test runner configured. Verification has to be visual.

- [ ] **Step 5.1: Boot the local Arnold client**

Run from the repo root: `arnold.bat`
Expected: SSH tunnel opens, local FastAPI starts on port 8000, browser opens to the Sports tab.

- [ ] **Step 5.2: Verify normal-mode rendering**

In the browser, click into the **Arbitrage** sub-tab. Confirm:
- Funded clusters (e.g. `ALTENAR_MAIN` with `BETINIA`/`QUICKCASINO`, `GECKO_BETSSON` with `SPELKLUBBEN`) still render with their balance buttons, pending counts, and arb tables.
- Cluster header tail reads `{n} arbs · siblings share odds` (the no-amber-pill version).
- No amber italic provider pills appear in any cluster header.

- [ ] **Step 5.3: Verify hidden-cluster behavior**

Confirm: the previously-empty headers — `SPECTATE`, `COMEON_GROUP`, `10BET`, `BETHARD`, `COOLBET` — are now absent from the page, **unless** one of them happens to have a ≥ 2.5% arb at the moment, in which case it renders in deposit-hint mode (Step 5.4).

- [ ] **Step 5.4: Verify deposit-hint mode (if any cluster qualifies)**

If any drained cluster shows up:
- Header reads `{CLUSTER} · deposit to play · {n} qualifying arbs ≥ 2.5%`.
- No provider cards (no balance buttons, no pending list, no Place/Skip).
- Up to 10 arb rows beneath, all with `guaranteed_profit_pct ≥ 2.5%`.
- Each row shows the anchor leg's provider name (the soft-book sibling that needs the deposit) and the hedge legs.

If no cluster qualifies right now, this case can't be verified live. Note in the PR description that deposit-hint mode is exercised only when the scanner produces a ≥ 2.5% arb on a drained cluster.

- [ ] **Step 5.5: Confirm no regressions in adjacent UI**

- Click into **Value Bets** sub-tab — providers still group correctly, no console errors.
- Click **Bankroll** main tab — balance/bonus rendering still works (the page uses `balance_status[i].bonus_amount`, which is unchanged on the backend).
- Open browser DevTools console — no React warnings or runtime errors related to the changes.

- [ ] **Step 5.6: Final commit (only if any whitespace / lint touch-ups were needed)**

If Steps 5.1–5.5 surfaced no issues, no further commit is needed. If a minor follow-up fix was needed, commit it with `fix(playpage): ...` and continue.

---

## Done criteria

All five tasks committed, the branch type-checks (`npx tsc --noEmit`), the production build succeeds (`npm run build`), and the visual checks in Task 5 pass. Deploy via `scripts/server-deploy.sh rebuild backend` is **not required** — this is a frontend-only change served by the local Arnold client. (Backend `dist/` is rebuilt during the Docker image stage 1, so a server rebuild only matters once this lands and a deploy is otherwise warranted.)
