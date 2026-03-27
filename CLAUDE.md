# Firev - Betting Analytics Platform

## WHAT This Project Is

Firev compares odds across 40+ sportsbooks against sharp sources (Pinnacle) to find value bets.

**Tech stack:** Python 3.10+ / FastAPI / SQLite / Playwright | React 19 / TypeScript / Vite / Tailwind

## Architecture

```
backend/src/
├── providers/        # 16 extractors (Kambi, Altenar, Gecko V2, Spectate, Pinnacle, Polymarket, etc.)
│   ├── mixins/       # RSocket decoding
│   └── shared/       # Shared provider utilities
├── pipeline/         # orchestrator, storage, scheduler, pool_manager, circuit_breaker, cache, health, metrics, extraction_report
├── analysis/         # scanner, value, bonus, devig, ev_enrichment
├── matching/         # Event normalization + fuzzy matching
├── bankroll/         # Kelly criterion + stake sizing
├── risk/             # Risk management
├── repositories/     # Data access abstraction (ProfileRepo, EventRepo, OddsRepo, OpportunityRepo, BetRepo)
├── services/         # Business logic coordination (OpportunityService, BankrollService, BetService)
├── db/               # SQLAlchemy models (Event, Odds, Bet, Provider, Profile) — ORM only, no business logic
├── api/              # FastAPI application
│   └── routes/       # Thin HTTP handlers — delegate to services/repositories
├── core/             # Transport, exceptions (FirevError hierarchy)
├── constants.py      # ALLOWED_MARKETS, SHARP_PROVIDERS
├── paths.py          # Centralized path resolution (dev vs bundled .exe)
└── app.py            # Typer CLI

frontend/src/
├── components/
│   ├── Terminal/     # TerminalWindow, Sidebar, TabBar, FilterBar, StreamingText, WorkflowPanel, ExtractionProgressBar
│   │   └── pages/   # ValuePage, SpecialsPage, DutchPage, ReversePage, BetsPage, BankrollPage, StatsPage, ProfilePage, PolymarketPage
│   └── ErrorBoundary.tsx
├── contexts/         # WorkflowContext
├── hooks/            # useBettingContext, useChat, useExtractionStatus, useBankroll, useProfiles, useRisk, useMultiSort, useTableSort
└── services/         # api.ts
```

## WHY It's Structured This Way

- **Provider extractors are isolated** - Each bookmaker has unique API/DOM structure
- **Sharp sources separate** - Pinnacle provides "fair odds" baseline (Polymarket for event matching only)
- **Matching layer abstracts providers** - Fuzzy matching normalizes "Real Madrid CF" → canonical event
- **Analysis is provider-agnostic** - Works on normalized events/odds
- **Repositories abstract DB access** - All queries go through repo classes, not raw `session.query()` in routes/services
- **Services coordinate business logic** - Routes are thin HTTP handlers, services own the logic
- **`db/models.py` is ORM-only** - No helper functions, no business logic — just model definitions and DB init

## HOW To Work In This Codebase

### Commands
```bash
# Extract odds (via CLI)
cd backend
python -m src.app extract polymarket pinnacle     # Sharp sources
python -m src.app extract                         # All enabled providers

# Or via API
curl -X POST "http://localhost:8000/api/extraction/run?providers=pinnacle"

# Tests
pytest tests/
```

**Dev servers** are configured in `.claude/launch.json` (backend :8000, frontend :5173). Use Claude Preview to start/verify.

### Key Domain Concepts
- **Fair odds**: True probability from Pinnacle (after devigging)
- **Edge %**: `(provider_odds / fair_odds - 1) × 100`
- **Value bet**: Single outcome with positive edge
- **Sharp source**: Pinnacle ONLY (Polymarket is NOT used as sharp)

### Extraction Scope
**We extract 1x2/moneyline, spread, and total markets. All other markets are skipped.**

- **Markets extracted**: `1x2`, `moneyline` (match winner), `spread` (handicap), `total` (over/under)
- **Spread/total**: Main lines only (`isAlternate=false` for Pinnacle, betOfferType 6/7 for Kambi)
- **Markets skipped**: props, player markets, corners, cards, correct score, etc.
- **Live events**: Skipped entirely - only pre-match odds
- **Whitelist enforced in**: `constants.py` via `ALLOWED_MARKETS` (imported by `pipeline/storage.py`)

## Configuration

- `src/config/providers.yaml` - **Single source of truth** for all provider config: endpoints, types, bonuses, active list, extraction tiers, orchestrator settings. Always read this file for current provider state — never hardcode provider lists elsewhere.
- `src/config/sports.yaml` - Sport/league mappings with provider-specific IDs
- `backend/data/firev.db` - SQLite database (queryable via sqlite MCP)

## When Working Here

- Provider APIs return JSON - no HTML scraping needed for most
- Playwright only for DOM-based providers (Spectate, ComeOn, Hajper)
- Rate limits enforced via circuit breaker in orchestrator
- Event matching uses `rapidfuzz` for team name normalization
- Shared constants in `constants.py` (ALLOWED_MARKETS, SHARP_PROVIDERS)

### UI Uniformity Rule (IMPORTANT)
**All tab pages must follow the same UI patterns. When adding a feature to one page, apply it to all similar pages.**

Standard patterns:
- **FilterBar** with `MultiSelectDropdown` for provider/sport filtering (shared component in `FilterBar.tsx`)
- **Expanded rows** use `<select>` dropdown to pick provider + single bet button (not multiple per-provider buttons)
- **Accent colors** per tab: `tabValue` (orange) for Soft, `tabBonus` (purple) for Specials, `success` (green) for Dutch
- **Table structure**: compact `sq` class, consistent column naming (Event/Boost, Providers, Odds, Edge, etc.)
- **EV data**: show `edge_pct` (vs Pinnacle fair odds) wherever available, not just `boost_pct` (vs original odds)

Shared filter components in `frontend/src/components/Terminal/FilterBar.tsx`:
- `MultiSelectDropdown` — compact popover with checkboxes + search (for >6 options)
- `SingleSelectPills` — inline pill buttons for single-select categories
- `MultiSelectPills` — inline pills for multi-select (available, not yet used)
- `RangeFilter` — min/max number inputs (available, not yet used)

### Code Cleanup Rule (IMPORTANT)
**If you find any redundant code handling markets other than 1x2/moneyline/spread/total, remove it immediately.**

We only support 1x2, moneyline, spread, and total markets. Any code for props, player markets, corners, cards, correct score, etc. is dead code and should be deleted. Keep the codebase lean - delete, don't comment out.

## Pipeline Data Flow

```
Provider API → StandardEvent
    ↓
normalize_team_name() + normalize_market()
    ↓
_resolve_event_id() → exact match / fuzzy match / swapped-team fallback
    ↓
store_provider_event() → Event + Odds (via OddsBatchProcessor)
    ↓
detect_and_fix_inversion() → swap if needed (cached sharp odds)
    ↓
OpportunityScanner.scan_value() → pre-computed Pinnacle dict + soft prob sums
```

### Extraction Tiers

| Trigger | Providers | Typical Duration |
|---------|-----------|-----------------|
| `sharp` | Pinnacle + Polymarket | ~15s |
| `api_soft` | API providers (Kambi, Altenar, Gecko, Spectate, VBet) | ~150s |
| `browser_soft` | Browser providers (Tipwin, Spectate, ComeOn, etc.) | ~480s |

### Pinnacle Match Rate (Key Health Metric)

The primary extraction quality metric is **how many soft provider events match against Pinnacle events**. The extraction report flags match rates automatically:
- `!` = Critical: failed providers, 0 events, match rate < 30%
- `~` = Warning: missing markets, slow extraction, rate limits

**Use the sqlite MCP to query `extraction_runs`, `provider_run_metrics`, and `sport_run_metrics` for extraction health.**

**If match rate drops:** check `sports.yaml` aliases, team name normalization, timezone date offset.

### Scanner Quality Filters
- `MIN_VALID_PROB_SUM = 0.90` - Filter incomplete markets
- `MAX_ODDS_RATIO = 1.35` - Filter event mismatches (fuzzy matching false positives)

## Extraction Health Checklist

**After extraction runs, query these via sqlite MCP (tables: `extraction_runs`, `provider_run_metrics`, `sport_run_metrics`).**

Check in order of severity:
- Failed providers (`status != 'success'`, error messages, 0 events)
- Match rate drops (`events_unmatched / events_processed` ratio increasing)
- Missing market types (`spread_count=0` or `total_count=0`)
- Timing regressions (duration significantly higher than baseline)
- Sport-level gaps (sports with 0 events or 0 matches)
- Opportunity yield (query `opportunities` table)

Record findings in `backend/docs/provider_performance.md`.

## Specials / Odds Boosts Pipeline

**Separate from regular extraction** — different data models, schedules, no shared lock. Boosts run on their own 120-minute scheduler tier.

```
scrape_specials.scrape_all()  →  Special dataclass list
    ↓
save_specials()               →  JSON backup (data/specials.json)
    ↓
filter_expired()              →  Remove started/expired events
    ↓
enrich_specials_with_ev()     →  Match vs Pinnacle fair odds → edge_pct, fair_odds, is_positive_ev
    ↓
store_specials_to_db()        →  Full replace into `specials` table (DELETE all + INSERT)
```

**EV enrichment runs at scrape time, not at query time.** The GET /api/specials endpoint reads pre-computed data from DB.

EV logic (`src/analysis/ev_enrichment.py`):
- Only 1x2/moneyline boosts can be EV-analyzed (combos/props filtered via PROP_KEYWORDS)
- Matches boost event name against Pinnacle events using normalized team names
- De-vigs Pinnacle odds (multiplicative method) to get fair odds
- `edge_pct = (boosted_odds / fair_odds - 1) * 100`
- Sanity check: edge > 100% = wrong match, skip

## Workflow Automation

**Use plugins/skills to automate the development loop:**

- **New features / providers**: `/brainstorm` → `/write-plan` → `/execute-plan` (superpowers)
- **Debugging extraction**: `systematic-debugging` triggers automatically for root cause investigation
- **Shipping**: `/commit-push-pr` (commit-commands) → `/code-review` (posts review comment on PR)
- **Code review** runs 5 parallel agents checking: CLAUDE.md compliance, bugs, git history, previous PRs, code comments. Only issues scoring 80+ confidence are posted.
- **Frontend changes**: Use Claude Preview (`preview_start`, `preview_screenshot`) to verify UI
- **DB queries**: Use sqlite MCP directly — no Python scripts needed
- **Multi-file sweeps**: `/ralph-loop` for repetitive changes across many files
- **Docs lookup**: context7 MCP for FastAPI, SQLAlchemy, Playwright, rapidfuzz docs
