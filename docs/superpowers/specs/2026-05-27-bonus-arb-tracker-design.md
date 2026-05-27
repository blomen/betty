# Bonus-Arb Tracker — Stats Page Section

**Status:** approved 2026-05-27
**Owner:** rasmus
**Scope:** stats / observability only

## Why

User is about to run a multi-week experiment placing arbs on **lodur, betinia, swiper** (three Altenar sister skins, identical odds, three separate deposit accounts) hedged against the unlimited pool (Pinnacle / Cloudbet / Kalshi / Polymarket). Goal: extract bonus value, transfer it to sharp, and **validate that the displayed arb yield converges to realized profit** at this sample size.

Today's Stats page shows aggregate bankroll, CLV trend, and a flat bet history table. It cannot answer the question this experiment exists to answer: *for the arbs I placed today (and this week), did the displayed yield actually materialize after both legs settled?*

That requires a paired-leg view (anchor + counter), day/week aggregation in the user's local calendar, and currency-normalized P&L.

## What we're building

One new read-only API endpoint and one new section on the Stats page.

- **Endpoint:** `GET /api/bets/bonus-arbs?window={today|week|30d}`
- **Section:** `<BonusArbTracker />` on `StatsPage.tsx`, between the Charts row and the Realized-ROI Analytics accordion.

No DB migration. No change to placement, arb-correlation, or any extraction code. Existing columns (`arb_group_id`, `bet_type`, `clv_pct`, `fair_odds_at_placement`, `provider_clv_pct`, `is_bonus`, `currency`) already carry everything required.

One incidental change: `arb_group_id` is added to the `/api/bets` response payload (currently in DB, not serialized) and to the frontend `Bet` type. This is a one-line backend change and a one-line frontend type extension.

## Constants

```python
SOFT_PROVIDERS = {"lodur", "betinia", "swiper"}
# Hard-coded. Widening to other softs is a one-line edit when needed.

SEK_PER = {"USD": 10.50, "USDC": 10.50, "SEK": 1.0}
# Matches RATE_TO_SEK already in frontend/src/pages/StatsPage.tsx.

TZ = ZoneInfo("Europe/Stockholm")
# Day/week boundaries computed in this tz on bet.placed_at.

DAILY_HISTORY_DAYS = 30
```

## Data model — endpoint response

```jsonc
{
  "window": "week",
  "since": "2026-05-25T22:00:00Z",   // Monday 00:00 Stockholm -> UTC
  "until": "2026-05-27T23:59:59Z",
  "summary": {
    "today":   { "arbs": 8, "settled": 6, "stake_sek": 2350.0, "pnl_sek": 84.2,
                 "avg_displayed_pct": 3.8, "avg_realized_pct": 3.6,
                 "anchor_clv_avg": 1.2, "counter_clv_avg": -0.3,
                 "counter_provider_clv_avg": -0.1 },
    "week":    { ...same shape... },
    "thirty":  { ...same shape... }
  },
  "daily": [
    { "date": "2026-04-28", "arbs": 3, "settled": 3, "stake_sek": 1000.0,
      "pnl_sek": 36.5, "avg_displayed_pct": 3.7, "avg_realized_pct": 3.65 },
    ...30 entries, oldest first, gaps filled with zeros...
  ],
  "groups": [
    {
      "arb_group_id": "ab12cd34" | null,
      "status": "settled" | "pending" | "partial",
      "placed_at": "2026-05-27T13:42:00+02:00",
      "event": {
        "id": "soccer:laliga:real-madrid-vs-barcelona:2026-05-27",
        "home_team": "Real Madrid",
        "away_team": "Barcelona",
        "display_home": "Real Madrid",
        "display_away": "Barcelona",
        "sport": "soccer",
        "league": "laliga",
        "start_time": "2026-05-27T19:00:00Z"
      } | null,        // null if anchor.event_id is null (free-text boost)
      "boost_event": "Real Madrid vs Barcelona" | null,  // fallback name
      "anchor": {
        "id": 4711,
        "provider_id": "betinia",
        "market": "1x2",
        "outcome": "home",
        "point": null,
        "odds": 2.10,
        "stake_sek": 500.0,
        "stake_native": 500.0,
        "currency": "SEK",
        "payout_sek": 1050.0 | null,
        "profit_sek": 550.0 | null,
        "result": "won" | "lost" | "void" | "pending",
        "is_bonus": false,
        "fair_odds_at_placement": 2.05,
        "clv_pct": 1.4 | null,
        "provider_clv_pct": null     // typically null for soft anchors (no same-market sharp feed)
      },
      "counter": { ...same shape; provider_clv_pct populated for polymarket/kalshi, null for pinnacle/cloudbet... } | null,
      "total_stake_sek": 998.0,     // anchor.stake_sek + counter.stake_sek, or anchor.stake_sek alone when counter is null
      "displayed_yield_pct": 3.7 | null,
      "realized_yield_pct": 3.6 | null,
      "pnl_sek": 36.0 | null
    },
    ...newest first...
  ]
}
```

## Calculations

**Displayed yield** (theoretical edge at placement):
```
displayed_yield_pct = (1 / (1/anchor.odds + 1/counter.odds) - 1) * 100
```
Returns `null` when:
- counter is missing (unpaired anchor), or
- `anchor.is_bonus = True` (free stake, "yield" is undefined).

**Realized yield** (actual outcome):
```
realized_yield_pct = ((anchor.profit_sek + counter.profit_sek)
                      / (anchor.stake_sek + counter.stake_sek)) * 100
```
Returns `null` until `status == "settled"`. `profit_sek` uses `Bet.profit` (already accounts for `is_bonus`).

**Status:**
- `"settled"` — both legs have `result ∈ {won, lost, void}`.
- `"pending"` — both legs have `result == "pending"`.
- `"partial"` — exactly one leg settled, or counter missing entirely.

**Summary aggregates** (per window):
- `arbs` — total group count in window.
- `settled` — count where `status == "settled"`.
- `stake_sek` — sum of `total_stake_sek` across all groups.
- `pnl_sek` — sum of `pnl_sek` across settled groups only.
- `avg_displayed_pct` — mean over groups where `displayed_yield_pct is not None`.
- `avg_realized_pct` — mean over groups where `realized_yield_pct is not None`.
- `anchor_clv_avg`, `counter_clv_avg` — mean leg-level `clv_pct` where present.
- `counter_provider_clv_avg` — mean `provider_clv_pct` (same-market CLV, e.g. Polymarket-close-vs-entry) where present.

**Day/week bucketing:**
- "Today" = `placed_at` of the anchor leg falls in `[Stockholm midnight, now]`.
- "Week" = `placed_at` falls in `[Monday 00:00 Stockholm, now]`.
- "30d" = last 30 calendar days inclusive of today.
- Daily buckets in the `daily[]` array are keyed by Stockholm calendar date of the anchor's `placed_at`.

## Window selection rule

The `summary` block **always** contains all three windows (`today`, `week`, `thirty`) regardless of the `?window=` param — the Today and Week tiles need to stay populated no matter which window the user toggles for the table.

The `?window=` param controls **only the `groups[]` list** — i.e. which arbs render in the table.

The `daily[]` array always covers the last 30 calendar days regardless of window, so the bar chart's shape is stable as the user toggles windows.

## Group-selection query (SQL pseudocode)

```sql
-- Find anchor candidates first, then LEFT JOIN counters by arb_group_id.
WITH anchors AS (
  SELECT id, arb_group_id, provider_id, event_id, market, outcome, point, odds,
         stake, currency, payout, result, is_bonus, fair_odds_at_placement,
         clv_pct, placed_at, settled_at, boost_event
  FROM bets
  WHERE provider_id IN ('lodur','betinia','swiper')
    AND placed_at >= :since_utc
    AND placed_at <  :until_utc
    AND profile_id = :active_profile_id
)
SELECT a.*, c.*
FROM anchors a
LEFT JOIN bets c
  ON c.arb_group_id = a.arb_group_id
  AND c.id != a.id
  AND a.arb_group_id IS NOT NULL
ORDER BY a.placed_at DESC;
```

When `a.arb_group_id IS NULL`, the LEFT JOIN yields no counter — group renders with `counter=null, status='partial', displayed_yield_pct=null`.

When a single `arb_group_id` has more than one anchor or more than one counter (rare; happens on Altenar sister skins playing the same event independently), each anchor renders as its **own group**, paired with the same counter — the counter's stake/profit is divided evenly across anchors so the aggregate P&L isn't double-counted. This is the simplest correct behavior; if it produces nonsense rows in practice, we adjust later.

## File-level plan

### Backend

**New file** `backend/src/api/routes/bonus_arbs.py`
- `router = APIRouter(prefix="/api/bets/bonus-arbs", tags=["bets"])`
- `_window_bounds(window: str, now: datetime) -> tuple[datetime, datetime]` — returns UTC bounds for the requested window using `ZoneInfo("Europe/Stockholm")`.
- `_to_sek(amount: float | None, currency: str) -> float | None` — applies `SEK_PER`.
- `_build_group(anchor: Bet, counter: Bet | None, event: Event | None) -> dict` — per-group dict per the schema above.
- `_summarize(groups: list[dict]) -> dict` — per-window summary block.
- `_daily_buckets(all_30d_groups: list[dict]) -> list[dict]` — fills 30 dates oldest-first, zero-filled gaps.
- `get_bonus_arbs(window: Literal["today","week","30d"] = "week", db: Session = ...)` — main handler. Fetches anchors + counters + events in three queries (anchors filtered by provider+window, counters by `arb_group_id IN (...)`, events by `id IN (...)`).

**Edited file** `backend/src/api/routes/bets.py`
- Add `"arb_group_id": b.arb_group_id` to the bet dict at ~line 315.

**Edited file** `backend/src/api/app.py` (or wherever routers are mounted)
- `app.include_router(bonus_arbs.router)`.

**Edited file** `backend/src/api/schemas.py` — no change required (response is a plain dict, not a Pydantic model — same pattern as the `analytics` endpoint).

### Frontend

**Edited file** `frontend/src/types/index.ts`
- Add `arb_group_id?: string | null` to `Bet`.
- Add new types:
  ```ts
  export interface BonusArbLeg { id: number; provider_id: string; market: string; outcome: string;
    point: number | null; odds: number; stake_sek: number; stake_native: number; currency: string;
    payout_sek: number | null; profit_sek: number | null; result: 'won'|'lost'|'void'|'pending';
    is_bonus: boolean; fair_odds_at_placement: number | null; clv_pct: number | null;
    provider_clv_pct?: number | null }
  export interface BonusArbGroupEvent { id: string; home_team: string; away_team: string;
    display_home: string | null; display_away: string | null; sport: string | null;
    league: string | null; start_time: string | null }
  export interface BonusArbGroup { arb_group_id: string | null; status: 'settled'|'pending'|'partial';
    placed_at: string; event: BonusArbGroupEvent | null; boost_event: string | null;
    anchor: BonusArbLeg; counter: BonusArbLeg | null; total_stake_sek: number;
    displayed_yield_pct: number | null; realized_yield_pct: number | null; pnl_sek: number | null }
  export interface BonusArbSummary { arbs: number; settled: number; stake_sek: number;
    pnl_sek: number; avg_displayed_pct: number | null; avg_realized_pct: number | null;
    anchor_clv_avg: number | null; counter_clv_avg: number | null;
    counter_provider_clv_avg: number | null }
  export interface BonusArbDaily { date: string; arbs: number; settled: number;
    stake_sek: number; pnl_sek: number; avg_displayed_pct: number | null;
    avg_realized_pct: number | null }
  export interface BonusArbResponse { window: 'today'|'week'|'30d'; since: string; until: string;
    summary: { today: BonusArbSummary; week: BonusArbSummary; thirty: BonusArbSummary };
    daily: BonusArbDaily[]; groups: BonusArbGroup[] }
  ```

**Edited file** `frontend/src/services/api/bets.ts`
- `getBonusArbs(window: 'today'|'week'|'30d' = 'week'): Promise<BonusArbResponse>`.

**New file** `frontend/src/pages/components/BonusArbTracker.tsx`
- Self-contained component. Single `useQuery(['bonus-arbs', window], () => api.getBonusArbs(window), { staleTime: 30_000 })`.
- Three UI blocks:
  - Window chip row + provider chip (`lodur · betinia · swiper`).
  - Summary tile pair (Today / Week) — 4 tiles each: Arbs, Stake, P&L, ROI; sub-line shows displayed/realized/CLV.
  - 30-day bar chart of `daily[].pnl_sek` — bars green if `pnl_sek >= 0` else red; hover shows date + arbs + stake + pnl.
  - Group table — one row per `groups[]` entry; expand for leg detail. Columns: time · event · anchor (provider+odds+result+profit) · counter (provider+odds+result+profit) · stake (SEK) · displayed% · realized% · anchor ΔCLV · counter ΔCLV.
- Reuses `ProviderName`, `fmtAmount`, `fmtProfit`, `CLV_BADGE`, the `polyChart` helper (or a smaller bar-chart inline since polyChart is line-only).

**Edited file** `frontend/src/pages/StatsPage.tsx`
- Import `BonusArbTracker`.
- Mount between the Charts row (line ~679) and the Realized-ROI Analytics accordion (line ~681).

## Edge cases & explicit decisions

| Case | Behavior |
|---|---|
| Anchor with no counter (unpaired) | Render as `status="partial"`, `counter=null`. `displayed_yield_pct=null`, `realized_yield_pct=null` even if anchor is settled. Surfaces "pairing failed" to the user. |
| Multiple anchors share one counter (sister-skin replay) | Each anchor renders as its own group; counter stake/profit divided evenly across anchors. Aggregate P&L stays correct. |
| Bonus anchor (`is_bonus=True`) | Display `BONUS` chip on anchor cell. `displayed_yield_pct=null` (free stake — yield meaningless). `realized_yield_pct` still computed if both legs settled. |
| Voided leg | Treated as settled with profit = 0 (stake returned by `Bet.profit`). Counts in `settled` total. |
| Mixed currency legs | Both converted to SEK at `SEK_PER` rate before any sum/avg. |
| Empty window | Returns `summary` blocks with all zero/null values, `groups: []`, and 30 zero-filled `daily[]` entries. Component renders a "no arbs yet" message in the table area, charts/tiles render normally with zeros. |
| `placed_at` exactly at Stockholm midnight | Anchor at 00:00:00 Stockholm belongs to the new day (right-open intervals: `[day_start, day_end)`). |
| DST transition | `ZoneInfo` handles it. Day on 2026-03-29 is 23h long, 2026-10-25 is 25h — bucket boundary calculations remain correct. |

## Testing

**Backend** (`backend/tests/api/test_bonus_arbs.py`):
1. Empty DB → endpoint returns zeroed summary + empty groups + 30 zero-filled daily entries.
2. One settled arb (betinia win + pinnacle loss, SEK + SEK) → realized_yield matches hand-calc, pnl_sek matches.
3. One settled arb with mixed currency (lodur SEK win + polymarket USDC loss) → SEK conversion correct in stake, payout, profit, and aggregate.
4. Unpaired anchor at swiper → `counter=null, status="partial"`, group still appears in `groups[]`, `displayed_yield_pct=null`.
5. Bonus anchor → `displayed_yield_pct=null` but realized still computed.
6. Anchor at non-soft provider (e.g. spelklubben) → excluded from `groups[]`.
7. Anchor placed at 23:59 Stockholm on 2026-05-26 vs 00:01 on 2026-05-27 → fall into separate `daily[]` buckets.
8. `window=today` returns groups only from today's Stockholm calendar day; `window=week` includes everything since Monday 00:00; `window=30d` includes last 30 days.

**Frontend:** verify the component renders correctly via `betty.bat` (local launcher) once the endpoint is deployed. No new component tests required — matches the test-light pattern already in `frontend/src/pages/`.

## Out of scope

- No DB migration / schema change.
- No change to `arb_correlation`, placement path, or extraction.
- No PlayPage edits.
- No "edit / delete / reclassify" actions in this section — read-only.
- Not generalized beyond `{lodur, betinia, swiper}` — widening is a one-line edit when needed.
- No Pydantic response model — matches existing `analytics` endpoint convention.

## Risks & open questions

- **Sample size.** At ~50 arbs/week, week-level realized yield has high variance; the experiment value is in seeing that variance, not in any single week's number being "right". Documented in the spec; UI doesn't need to do anything special.
- **Counter CLV provenance.** `clv_pct` uses Pinnacle as the sharp baseline. For Polymarket / Kalshi counters, `provider_clv_pct` (same-market close) is the more honest CLV signal — we surface both in the summary.
- **Sister-skin coincidence.** If user plays the same event at lodur AND betinia AND swiper near-simultaneously with one Pinnacle counter, the counter-stake-divided-by-anchors heuristic produces three "1/3-counter" rows. Aggregate P&L stays correct but each individual row's `realized_yield_pct` looks anemic. Acceptable for v1; revisit if it actually happens often.
