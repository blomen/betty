# Stats Page Refactor — Per-Profile Account Styles

**Date:** 2026-05-30
**Status:** Design — awaiting review
**Topic:** Replace the current monolithic, profile-blind Stats page with a profile-scoped dashboard whose layout adapts to each profile's *account style* (Personal vs Bonus Extraction).

---

## 1. Context & Problem

The Stats tab (`frontend/src/pages/StatsPage.tsx`, ~1,434 lines, exported as `BetsPage`/`StatsPage`) is the user's view of betting performance. Today it is "messy and wrong info everywhere." Concrete root causes found during exploration:

1. **No profile context.** Every backend endpoint it calls (`/bets`, `/bets/analytics`, `/bankroll`, `/bankroll` stats) already scopes to the **active profile** server-side, but the page never displays *which* profile it shows and has no selector. Numbers silently change when the active profile is switched elsewhere (e.g. on the Play/Bankroll tabs), with no indication on Stats.
2. **Headline vs chart mismatch.** The KPI summary uses a full server-side aggregate (`getBankrollStats`), but `BankrollChart` and `CLVChart` are built from a **500-bet client sample** (`api.getBets(undefined, 500)`). Different populations → the curve and the headline disagree once a profile exceeds 500 bets.
3. **Synthetic bankroll baseline.** `BankrollChart` back-solves the starting point as `currentBankroll − cumulativeProfit`, folding all deposits/withdrawals/bonus transfers into one invisible y-shift. The curve does not trace a real equity walk and the "%" is `profit/totalStaked` over a truncated sample.
4. **Widget zoo / mixed scopes.** Four overlapping CLV widgets (main `CLVChart`, reverse-value `CLVChart`, Shadow `MultiLineCLVChart`, analytics CLV columns), plus a **Shadow CLV sub-tab that is scanner-global, not profile-scoped** (`getOppSnapshotStats`), plus the `BonusArbTracker`. There is **no strategy-level P/L split** (arb vs value vs reverse — exactly what the user wants for their real account) and **no per-provider bonus-extraction panel** (what the user wants for fresh-soft profiles).

### The two account styles (user's examples)

- **Personal** ("my account" = my own sharp + soft): track arb performance, value-bet performance, overall betting performance, and bankroll growth.
- **Bonus Extraction** ("new profile" = my sharp accounts + fresh new soft accounts): track bonus-extraction performance; the sharp legs count toward this profile. The sharp accounts are the shared unlimited pool (pinnacle/cloudbet/kalshi/polymarket); a hedge leg is attributed to a profile via its `profile_id`.

---

## 2. Goals & Non-Goals

### Goals
- Make the Stats page **profile-scoped and self-evident** about which profile it shows.
- Let the user **view either profile's stats without disrupting the active Play profile**.
- **Adapt the layout to the profile's style**, with a shared top block + style-specific secondary panels.
- **Fix the correctness bugs** (1–3 above) so headline, curve, and breakdowns all agree and are currency-correct.
- **Decompose** the 1,434-line file into focused, independently-understandable components.
- **Relocate** the scanner-global Shadow CLV view out of the per-profile dashboard.

### Non-Goals
- No new bet-categorization logic — reuse existing `bet_type` / `is_bonus` / `arb_group_id`.
- No true dated cashflow ledger (deposits/withdrawals as a time series). The bankroll curve remains a **realized-P/L equity curve** anchored to current bankroll, but computed over the full bet set and labelled honestly.
- No change to how bets are placed, settled, or reconciled.
- No redesign of the Bankroll or Play tabs (only additive `profile_id` params, back-compatible).

---

## 3. Decisions (from brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| Q1 | How does the page treat the two styles? | **Style is a per-profile attribute.** Stats shows one profile at a time; the layout adapts to that profile's style. |
| Q2 | Bonus Extraction headline framing? | **Pure P/L — treat it like any account.** Net realized profit + bankroll growth across all legs (soft + sharp hedges + bonus credits). No extraction-efficiency funnel. |
| Q3 | What differentiates the two layouts? | **Shared top block + different secondary panels.** Personal → strategy split (arb/value/reverse). Bonus → per-provider bonus panel + sharp-side P/L. |
| Open | Profile picker scope? | **Stats-local `profile_id` param.** View any profile's stats without calling `/activate` (Play undisturbed). Param omitted ⇒ active profile (back-compat). |

---

## 4. Data Model Changes

### `Profile.style`
Add one column to the `Profile` model (`backend/src/db/models.py`):

```python
style = Column(String, nullable=False, default="personal")  # "personal" | "bonus_extraction"
```

**Migration:** follow the existing in-code idempotent pattern in `models.py` (the block of guarded `ALTER TABLE ... ADD COLUMN` statements run after `Base.metadata.create_all`, e.g. `ALTER TABLE profiles ADD COLUMN chrome_port INTEGER`):

```sql
ALTER TABLE profiles ADD COLUMN style TEXT NOT NULL DEFAULT 'personal';
```

Add a matching Alembic revision under `backend/alembic/versions/` to keep formal migrations in sync (the project has both mechanisms; the in-code block is the runtime guarantee).

Existing profiles default to `personal`. The user flips the relevant profile(s) to `bonus_extraction` via the UI (§6).

---

## 5. Backend Changes

All additive and back-compatible. A shared helper resolves the target profile:

```python
def _resolve_profile(profile_repo, profile_id: int | None) -> Profile:
    return profile_repo.get(profile_id) if profile_id else profile_repo.get_active()
```

### 5.1 `profile_id` query param (view-any-profile)
Add optional `profile_id: int | None = None` to:
- `GET /api/bets` (`list_bets`)
- `GET /api/bets/analytics` (`get_analytics`)
- `GET /api/bankroll` (`get_bankroll`) and the stats it feeds (`BankrollService.get_stats`)

When provided, scope to that profile; when omitted, use active profile. `ProfileRepo` already has per-`profile_id` methods; only the route resolution changes. Add a `get(profile_id)` to `ProfileRepo` if missing.

### 5.2 `by_strategy` in analytics (Personal strategy split)
Extend `get_analytics` to add a `by_strategy` grouping alongside `by_sport`/`by_edge_bucket`, reusing the existing `summarize()` (already currency-correct via per-provider `to_sek`). Map `bet_type` → lane:

| `bet_type` | lane |
|---|---|
| `value` | Value |
| `arb` | Arb |
| `reverse` | Reverse |
| `boost` | Boost |
| `polymarket`, `mirror`, null | Other |

Each lane returns the existing bucket shape (`n, won, lost, void, win_pct, staked, profit, roi_pct, avg_clv_pct`) plus `clv_positive_pct`.

### 5.3 `by_provider` in analytics (Bonus panel + sharp P/L)
Add a `by_provider` grouping to the same endpoint: per `provider_id`, the currency-correct `{n, staked, profit, roi_pct, avg_clv_pct}`. Used to compute:
- **Value captured** per provider in the bonus panel.
- **Sharp-side P/L** = Σ `by_provider[p].profit` for `p ∈ UNLIMITED_PROVIDERS` (`{pinnacle, cloudbet, kalshi, polymarket}`).

### 5.4 Equity-curve endpoint (correctness fix for the chart)
New lightweight `GET /api/bets/equity-curve?profile_id=&days=`:
- Query **all** settled bets for the profile in range, ordered by `placed_at`, selecting only `(placed_at, payout, stake, currency, provider_id, is_bonus)` — **no** per-bet odds/Pinnacle enrichment (keep it cheap; "make the PC the bottleneck").
- Server computes cumulative realized P/L in **SEK** (same `to_sek` convention as analytics).
- Return `{ points: [{t, cum_profit_sek}], total_profit_sek, total_staked_sek, current_bankroll_sek }`.
- Frontend renders the curve as `baseline + cum_profit`, where `baseline = current_bankroll_sek − total_profit_sek`. Same anchoring as today **but over the full population**, so the curve's endpoint and the KPI block agree. Chart label clarified to "Bankroll (realized P/L)".

This replaces the client-side reconstruction from the 500-bet sample. The KPI block continues to use `getBankrollStats` (full aggregate); the curve now uses the full aggregate too → they match.

### 5.5 Bonus panel data
Reuse existing per-provider bonus status: `ProfileRepo.get_bonus_statuses_batch(profile_id, provider_ids)` (already exposed via bankroll routes) for `{status, bonus_type, bonus_amount, wagering_requirement, wagered_amount, progress_pct, days_remaining}`. Combine with `by_provider.profit` for "value captured." No new bonus query needed.

---

## 6. Frontend Architecture

Decompose `StatsPage.tsx` into focused modules under `frontend/src/components/stats/` (charts already self-contained — just extract them):

```
pages/StatsPage.tsx              # Shell: sub-tab routing (Profile Stats | Shadow CLV),
                                 #        renders layout by selected profile's style
components/stats/
  StatsHeader.tsx                # Profile picker (stats-local) + style badge/toggle + range control
  KpiBlock.tsx                   # Shared 5-KPI row (Net Profit, ROI, Bets W/L/V, Avg CLV, Bankroll)
  charts.tsx                     # BankrollChart (equity-curve fed), CLVChart  (moved as-is, retargeted)
  StrategySplit.tsx              # Personal: Value/Arb/Reverse/Boost lane table
  EdgeAnalytics.tsx              # Personal: realized-vs-displayed edge (by sport / edge bucket), collapsible
  BonusPanel.tsx                 # Bonus: per-provider status + wagering% + value captured + sharp-side P/L + bonus-arb pairs
  BetHistory.tsx                 # Shared: history table + inline edit/cashout (moved as-is)
  ShadowCLV.tsx                  # Moved: scanner-global opp_snapshots view (profile-independent)
hooks/
  useStatsData.ts                # Orchestrates queries keyed on (profileId, range); single source of truth
```

### Profile picker & style control
- Reuse `useProfiles` for the profile list. The Stats header has its **own** selected-profile state (default = active profile id), passed as `profile_id` to all stats queries. Switching it does **not** call `/activate`.
- The style badge next to the profile name is a small control: clicking lets the user set that profile's `style` via `PUT /api/profiles/{id}` (`{style}`). Also add a Personal/Bonus toggle to the create form in `ProfileSelector.tsx`.
- Add `style` to the `Profile` / `ProfileCreate` / `ProfileUpdate` TS types and `ProfileCreate`/`ProfileUpdate` Pydantic schemas + `profile_to_dict`.

### Range control
Single `All | 90d | 30d | 7d` control in the header (default **90d**). It drives `days` for **strategy split, edge analytics, and bet history**. The **KPI block and bankroll curve are all-time** ("overall performance / bankroll growth" per the user's framing) — the range zooms the analytical breakdowns, not the headline. (Adjustable in review if the user wants range to govern everything.)

---

## 7. Layout Spec

### Shared (every profile)
1. **Header:** color dot + profile name + style badge/toggle + stats-local profile dropdown + range control.
2. **KPI block (all-time):** Net Profit (SEK) · ROI % · Bets (n + W/L/V) · Avg CLV (% + % beat close) · Bankroll (current SEK).
3. **Bankroll curve (all-time, realized P/L):** equity walk from the equity-curve endpoint; endpoint value == KPI bankroll.

### Personal — secondary
- **Strategy split** (`by_strategy`): rows Value / Arb / Reverse / Boost / Other → n · staked · profit · ROI · avg CLV · beat%.
- **CLV trend:** one consolidated `CLVChart` over placed non-bonus bets (drop the redundant reverse-value duplicate; keep reverse as a strategy-split row instead). Reverse-value CLV remains available via the strategy lane.
- **Edge analytics** (collapsible): realized-vs-displayed edge by sport / by edge bucket / sport×market Kelly-confidence (existing tables, profile-scoped, range-filtered).
- **Bet history** (collapsible): range-filtered, profile-scoped table with inline edit/cashout.

### Bonus Extraction — secondary
- **Per-provider bonus panel:** provider · bonus type · status (available/in-progress/trigger-needed/completed) · wagering progress % · days remaining · value captured (net SEK profit at that provider).
- **Sharp-side P/L callout:** net SEK P/L of `UNLIMITED_PROVIDERS` legs in this profile ("the sharp counts toward my profile").
- **Bonus-arb pairs:** existing `BonusArbTracker` folded into this layout.
- **Bet history** (collapsible): same shared component.

### Moved out
- **Shadow CLV** → its own top-level sub-tab on the Stats page (peer to "Profile Stats"), explicitly labelled scanner-quality / profile-independent. No longer interleaved with per-profile widgets.

---

## 8. Correctness Fixes — Mapping

| Problem (§1) | Fix |
|---|---|
| No profile context | Header shows profile + style; stats-local picker (§6). |
| Headline vs chart mismatch (500-bet sample) | Curve fed by full-population equity-curve endpoint (§5.4); KPIs already full aggregate. |
| Synthetic baseline over truncated sample | Same anchoring but over **all** settled bets, SEK-correct; chart relabelled "realized P/L." |
| Widget zoo / mixed scope | Consolidate to one CLV trend; reverse becomes a strategy row; Shadow CLV relocated; bonus widgets unified into `BonusPanel`. |
| Cross-currency sums | All new aggregates reuse analytics' per-provider `to_sek`; sharp P/L and value-captured summed in SEK only. |

---

## 9. Testing

- **Backend unit:** `by_strategy`/`by_provider` aggregation (currency mix: SEK + USDC provider → verify SEK sums); `profile_id` resolution (param vs active); equity-curve cumulative + baseline math; sharp-P/L = Σ unlimited providers.
- **Backend regression:** omitting `profile_id` returns identical results to pre-change (active profile) for `/bets`, `/analytics`, `/bankroll`.
- **Frontend:** KPI bankroll == curve endpoint value (no >500 drift); style toggle switches layout; range control updates breakdowns but not headline; bonus panel renders per-provider rows with correct status.
- **Manual:** verify against a Personal profile and a Bonus Extraction profile on the live data set; confirm switching the stats-local picker does not change the Play tab's active provider highlighting.

---

## 10. Migration / Deploy Notes

- Touches `backend/` ⇒ **backend rebuild** required (`server-deploy.sh rebuild backend`) after merge. Frontend ships via `betty.bat` (Vite).
- `Profile.style` column add is idempotent and safe on existing rows (defaults to `personal`).
- All API changes are additive; old frontend builds keep working (params optional).

---

## 11. Out of Scope / Future
- Cross-profile overview (option C from Q1) — deferred; per-profile is the agreed v1.
- True dated cashflow ledger for an exact bankroll (deposits/withdrawals over time).
- Extraction-efficiency funnel for bonus profiles (rejected in Q2; revisit if pure P/L proves insufficient).
