# DegenTraderXD - Betting Analytics Platform

## WHAT This Project Is

DegenTraderXD compares odds across 40+ sportsbooks against sharp sources (Pinnacle) to find value bets.

**Architecture:**
```
backend/src/
├── providers/        # 11 extractors (Kambi, SBTech, Gecko V2, Spectate, Pinnacle, Polymarket)
│   └── mixins/       # RSocket decoding
├── pipeline/         # orchestrator, storage, pool_manager, circuit_breaker, cache, health, metrics
├── analysis/         # scanner, value, bonus, devig
├── matching/         # Event normalization + fuzzy matching
├── bankroll/         # Kelly criterion + stake sizing
├── repositories/     # Data access abstraction (ProfileRepo, EventRepo, OddsRepo, OpportunityRepo, BetRepo)
├── services/         # Business logic coordination (OpportunityService, BankrollService, BetService)
├── db/               # SQLAlchemy models (Event, Odds, Bet, Provider, Profile) — ORM only, no business logic
├── api/              # FastAPI application
│   └── routes/       # Thin HTTP handlers — delegate to services/repositories
├── core/             # Transport, exceptions
├── constants.py      # ALLOWED_MARKETS, SHARP_PROVIDERS
└── app.py            # Typer CLI

frontend/src/
├── components/
│   ├── Terminal/     # TerminalWindow, TerminalInput, ChatMessage, StreamingText, WelcomeMessage, ExtractionProgressMessage, CommandPanel, WorkflowPanel
│   └── ErrorBoundary.tsx
├── contexts/         # WorkflowContext
├── hooks/            # useBettingContext, useChat, useExtraction, useBankroll, useProfiles, useBonusWorkflow, useDropdownWorkflow, useBankrollWorkflow
└── services/         # api.ts
```

**Tech stack:** Python 3.10+ / FastAPI / SQLite / Playwright | React 19 / TypeScript / Vite / Tailwind

## WHY It's Structured This Way

- **Provider extractors are isolated** - Each bookmaker has unique API/DOM structure
- **Sharp sources separate** - Pinnacle provides "fair odds" baseline (Polymarket for event matching only)
- **Matching layer abstracts providers** - Fuzzy matching normalizes "Real Madrid CF" → canonical event
- **Analysis is provider-agnostic** - Works on normalized events/odds
- **Repositories abstract DB access** - All queries go through repo classes, not raw `session.query()` in routes/services
- **Services coordinate business logic** - Routes are thin HTTP handlers, services own the logic
- **`db/models.py` is ORM-only** - No helper functions, no business logic — just model definitions and DB init

## Skills Usage (IMPORTANT)

**Always use relevant skills (slash commands) for every task.** Skills provide specialized domain knowledge and enforce best practices.

**Before writing code, invoke the matching skill:**
- Refactoring / architecture → `architecture-patterns`
- Debugging / errors → `debugging-strategies` or `debugging-wizard`
- FastAPI routes / async → `fastapi-expert` or `fastapi-async-patterns`
- Python patterns / design → `python-design-patterns`, `python-anti-patterns`
- SQLAlchemy / DB → `sqlalchemy-orm`, `sql-optimization-patterns`
- Testing → `pytest`, `python-testing-patterns`, `e2e-testing-patterns`
- React / frontend → `react-dev`, `frontend-design`, `frontend-ui-dark-ts`
- Playwright / scraping → `playwright-expert`, `web-scraping`, `scrapy-web-scraping`
- Performance → `python-performance-optimization`
- Error handling → `python-error-handling`, `python-resilience`
- Logging → `logging-best-practices`, `python-observability`
- Type safety → `python-type-safety`, `pydantic`
- Code review → `typescript-react-reviewer`, `python-code-style`
- Git → `git-workflow`
- CI/CD → `github-actions-templates`

**If no existing skill fits, search for one:** use `find-skills` to discover installable skills, or search GitHub for open-source reference implementations to learn from.

**Multiple skills can be used per task** — e.g., `architecture-patterns` + `python-design-patterns` for a refactor, or `fastapi-expert` + `sql-optimization-patterns` for an API endpoint.

## HOW To Work In This Codebase

### Commands
```bash
# Extract odds
python -m src.extract --sources    # Sharp sources only (Pinnacle + Polymarket)
python -m src.extract --all        # All providers

# Find opportunities
python -m src.detect               # Value detection

# Run services (ALWAYS use these ports - terminals already running)
uvicorn src.api:app --reload       # API on :8000
cd frontend && npm run dev         # UI on :5173

# NOTE: Backend runs on port 8000, frontend on port 5173
# User has terminals already running these servers
# If you need to test, just refresh browser - don't start new servers
# If server crashed, kill process on port first then restart

# Tests
pytest tests/                      # Run test suite
```

### Adding a New Provider
1. Check provider type in `src/config/providers.yaml` (Kambi, Gecko V2, Spectate, SBTech, Altenar, etc.)
2. If existing type: add config entry only
3. If new type: create extractor in `providers/`, register in `factory.py`
4. For WebSocket/RSocket providers: use `RSocketMixin` from `providers/mixins/`
5. Test with `python -m src.extract --provider <name>`

### Key Domain Concepts
- **Fair odds**: True probability from Pinnacle (after devigging)
- **Edge %**: `(provider_odds / fair_odds - 1) × 100`
- **Value bet**: Single outcome with positive edge
- **Sharp source**: Pinnacle ONLY (Polymarket is NOT used as sharp)

### Extraction Scope (IMPORTANT)
**We extract 1x2/moneyline, spread, and total markets. All other markets are skipped.**

- **Markets extracted**: `1x2`, `moneyline` (match winner), `spread` (handicap), `total` (over/under)
- **Spread/total**: Main lines only (`isAlternate=false` for Pinnacle, betOfferType 6/7 for Kambi)
- **Markets skipped**: props, player markets, corners, cards, correct score, etc.
- **Live events**: Skipped entirely - only pre-match odds
- **Whitelist enforced in**: `constants.py` via `ALLOWED_MARKETS` (imported by `pipeline/storage.py`)

This keeps the system focused on the highest-value, most comparable market types across all providers.

## Configuration

- `src/config/providers.yaml` - **Single source of truth** for all provider config: endpoints, types, bonuses, active list, extraction tiers, orchestrator settings. Always read this file for current provider state — never hardcode provider lists elsewhere.
- `src/config/sports.yaml` - Sport/league mappings with provider-specific IDs
- `backend/data/degentraderxd.db` - SQLite database

### Extraction Volume Audit (IMPORTANT)

**When to audit:** After any provider changes, before marking PRODUCTION READY

**Common data loss causes:**
- Missing pagination (APIs often cap at 100-500 per request)
- Stale category/slug mappings (APIs change yearly identifiers like `nhl-2026`)
- Rate limiting silently dropping requests
- Filter parameters excluding valid data

**Audit workflow:**
1. **Visual baseline** - Browse provider site, count events manually for 1 sport
2. **Extract and compare** - Run extraction, compare counts
3. **If mismatch > 10%** - Investigate pagination, mappings, filters
4. **Log results** - Record expected vs actual in validation notes

**Pagination checklist:**
- [ ] Check API docs for limit/offset parameters
- [ ] Test with limit=1 to see if total_count returned
- [ ] Implement pagination loop if API caps results
- [ ] Log page count and total in extraction

**Slug/category mapping checklist:**
- [ ] API categories may change yearly (e.g., `nhl` → `nhl-2026`)
- [ ] Use flexible matching (strip year suffix as fallback)
- [ ] Log unmapped categories as warnings

**Audit script:** `python scripts/audit_extraction_volume.py <provider> --expected <count>`

## When Working Here

- Provider APIs return JSON - no HTML scraping needed for most
- Playwright only for DOM-based providers (Spectate, ComeOn, Hajper, FastBet)
- Rate limits enforced via circuit breaker in orchestrator
- Event matching uses `thefuzz` for team name normalization
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

We only support 1x2, moneyline, spread, and total markets. Any code for props, player markets, corners, cards, correct score, etc. is dead code and should be deleted. This includes:
- Normalization logic for unsupported market types
- Storage logic for unsupported markets
- Analysis logic for unsupported markets
- UI components for unsupported markets

Keep the codebase lean - delete, don't comment out.

## Provider Pipeline Workflow

### Pipeline Data Flow
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

### Running Extractions

**Sharp sources first (recommended workflow):**
```bash
# Via CLI
cd backend
python -m src.app extract polymarket
python -m src.app extract pinnacle

# Or via API
curl -X POST "http://localhost:8000/api/extraction/run?providers=pinnacle"
```

**Adding soft providers incrementally:**
```bash
python -m src.app extract leovegas   # Single provider
python -m src.app extract             # All enabled providers
```

### Validation Steps After Extraction

1. **Check event counts:**
   ```sql
   SELECT sport, COUNT(*) FROM events GROUP BY sport;
   ```

2. **Check cross-provider matches:**
   ```sql
   SELECT event_id, COUNT(DISTINCT provider_id) as providers
   FROM odds GROUP BY event_id HAVING providers > 1;
   ```

3. **Run opportunity detection:**
   ```bash
   python -m src.app value   # Show value bets
   ```

### Benchmarking Metrics

Track these per provider during extraction:
- **Extraction time** (seconds) - logged at `[provider] sport: N events in X.Xs`
- **Events extracted** (count per sport)
- **API errors** (rate limits, timeouts)
- **Cross-provider matches** (events matched with Pinnacle)

### Known Data Quality Issues

1. **Kambi correct score outcomes** - Some Kambi providers return correct score outcomes (0-1, 1-2, etc.) labeled as '1x2' market. These inflate counts. Fix: filter by `betOfferType.id` in Kambi extractor (ID 2 = Match Winner).

2. **Polymarket player name outcomes** - Tennis/esports outcomes stored as player names instead of normalized 'home'/'away'. Fix: enhance outcome normalization for Polymarket.

### Data Quality Validation (REQUIRED)

**After any provider changes, run this validation to ensure data quality:**

```bash
cd backend
rm -f data/degentraderxd.db  # Clear database
python -m src.app extract polymarket pinnacle <provider>
```

**Then run this SQL validation script:**
```python
import sqlite3
conn = sqlite3.connect('data/degentraderxd.db')
cursor = conn.cursor()

# 1. ODDS/EVENT RATIO (expected: 2.4-3.0 for 1x2 markets)
cursor.execute('''
    SELECT p.name, COUNT(o.id) as odds, COUNT(DISTINCT o.event_id) as events,
           ROUND(CAST(COUNT(o.id) AS FLOAT) / COUNT(DISTINCT o.event_id), 2) as ratio
    FROM odds o JOIN providers p ON o.provider_id = p.id
    GROUP BY p.name
''')
print("Provider         | Odds | Events | Ratio")
for row in cursor.fetchall():
    print(f"{row[0]:16} | {row[1]:4} | {row[2]:6} | {row[3]}")

# 2. OUTCOME NORMALIZATION (expected: 100% for Kambi, >97% for Polymarket)
cursor.execute('''
    SELECT provider_id,
           ROUND(100.0 * SUM(CASE WHEN outcome IN ('home','away','draw') THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
    FROM odds GROUP BY provider_id
''')
print("\nOutcome normalization rate:")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]}%")

# 3. SCORE-LIKE OUTCOMES (expected: 0 for all providers)
cursor.execute("SELECT provider_id, COUNT(*) FROM odds WHERE outcome LIKE '%-%' GROUP BY provider_id")
print("\nScore-like outcomes (should be 0):")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]}")

# 4. CROSS-PROVIDER MATCHING (higher = better data quality)
cursor.execute('''
    SELECT COUNT(DISTINCT event_id) as total,
           SUM(CASE WHEN cnt > 1 THEN 1 ELSE 0 END) as matched
    FROM (SELECT event_id, COUNT(DISTINCT provider_id) as cnt FROM odds GROUP BY event_id)
''')
row = cursor.fetchone()
print(f"\nCross-provider: {row[1]}/{row[0]} events ({100*row[1]/row[0]:.1f}%)")
```

**Expected benchmarks:**

| Metric | Pinnacle | Polymarket | Kambi (LeoVegas, etc.) |
|--------|----------|------------|------------------------|
| Odds/event ratio | 2.5-2.7 | 2.3-2.5 | 2.9-3.1 |
| Outcome normalization | 100% | >97% | 100% |
| Score-like outcomes | 0 | 0 | 0 |
| Market types | 1x2/ml/spread/total | 1x2 only | 1x2/ml/spread/total |

**Red flags to investigate:**
- Ratio > 4.0: Non-1x2 markets leaking through (check `betOfferType.id` filter)
- Ratio < 2.0: Missing outcomes (check market parsing)
- Normalization < 95%: Team name matching failing (check `normalize_outcome()`)
- Score-like > 0: Correct score markets not filtered (check market type filter)

**Sample data spot-check:**
```sql
-- Verify matched events have correct odds structure
SELECT e.id, e.home_team, e.away_team, o.provider_id, o.outcome, o.odds
FROM events e
JOIN odds o ON e.id = o.event_id
WHERE e.id IN (
    SELECT event_id FROM odds GROUP BY event_id HAVING COUNT(DISTINCT provider_id) > 1
)
ORDER BY e.id, o.provider_id, o.outcome
LIMIT 30;
```

### Extraction Review & Optimization (STANDARD PROCEDURE)

**When to run:** After any pipeline changes, provider additions, or performance issues.

**Standard workflow:**

1. **Clear database and run extraction:**
   ```bash
   cd backend
   rm -f data/degentraderxd.db
   python -m src.app extract
   ```

2. **Review extraction results:**
   - Check total time (target: <300s for 9 providers)
   - Verify all providers extracted (check event counts)
   - Note any rate limit errors (429s) or timeouts

3. **Run data quality validation:**
   ```bash
   python -c "
   import sqlite3
   conn = sqlite3.connect('data/degentraderxd.db')
   c = conn.cursor()

   print('=== Provider Odds Count ===')
   c.execute('SELECT provider_id, COUNT(*) FROM odds GROUP BY provider_id ORDER BY COUNT(*) DESC')
   for row in c.fetchall(): print(f'{row[0]:15}: {row[1]} odds')

   print('\n=== Odds/Event Ratio ===')
   c.execute('''
       SELECT provider_id, COUNT(id) as odds, COUNT(DISTINCT event_id) as events,
              ROUND(CAST(COUNT(id) AS FLOAT) / COUNT(DISTINCT event_id), 2) as ratio
       FROM odds GROUP BY provider_id
   ''')
   for row in c.fetchall(): print(f'{row[0]:16} | {row[1]:5} | {row[2]:6} | {row[3]}')

   print('\n=== Outcome Normalization ===')
   c.execute('''
       SELECT provider_id,
              ROUND(100.0 * SUM(CASE WHEN outcome IN (\"home\",\"away\",\"draw\") THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
       FROM odds GROUP BY provider_id
   ''')
   for row in c.fetchall(): print(f'  {row[0]:15}: {row[1]}%')
   "
   ```

4. **Performance tuning parameters** (in `src/config/providers.yaml`):
   ```yaml
   kambi_api:
     health_check_delay_ms: 1000    # Delay between health checks
     post_extraction_delay_ms: 15000 # Delay between Kambi providers
   ```

   And in `pipeline/orchestrator.py`:
   ```python
   sport_delay = 0.5 if is_kambi else 0.0  # Delay between sports
   ```

**Expected benchmarks (Kambi-only validation):**

| Metric | Target | Red Flag |
|--------|--------|----------|
| Total extraction time | <300s | >500s |
| Kambi odds/event ratio | 2.7-2.8 | <2.5 or >3.0 |
| Outcome normalization | 100% | <99% |
| Cross-provider matches | >50% | <30% |

**If extraction fails or data quality degrades:**
1. Check circuit breaker status (rate limits)
2. Review provider API responses for schema changes
3. Verify `betOfferType.id` filter for Kambi (ID 2 = Match Winner)
4. Check fuzzy match threshold (default: 90, min individual: 80)

### Scanner Validation (STANDARD PROCEDURE)

**After extraction, validate scanner results:**

```bash
cd backend && python -c "
from src.db.models import get_session
from src.analysis.scanner import OpportunityScanner

db = get_session()
scanner = OpportunityScanner(db)

# Value bets
vb = scanner.scan_value(min_edge_pct=5.0)
suspicious = [v for v in vb if v.edge_pct > 25]
print(f'VALUE BETS: {len(vb)} total, {len(suspicious)} suspicious (>25%)')

db.close()
"
```

**Expected benchmarks:**

| Metric | Target | Red Flag |
|--------|--------|----------|
| Value bets (>5% edge) | 300-500 | <100 or >1000 |
| Suspicious (>25% edge) | <10 | >50 |

**Data quality filters in `scanner.py`:**
- `MIN_VALID_PROB_SUM = 0.90` - Filter incomplete markets
- `MAX_ODDS_RATIO = 1.35` - Filter event mismatches (fuzzy matching false positives)

## Extraction Log Review (MANDATORY AFTER EVERY RUN)

**Purpose:** After each extraction, review logs to find regressions, coverage gaps, and optimization opportunities. The extraction report is generated automatically and stored in the DB. Always check it for issues.

**Database location:** `backend/data/degentraderxd.db`

### Step 1: Read the Extraction Report (always do this first)
```python
cd backend && python -c "
import sqlite3
conn = sqlite3.connect('data/degentraderxd.db')
# Check all 3 tiers: 'sharp', 'api_soft', 'browser_soft'
for tier in ('sharp', 'api_soft', 'browser_soft'):
    r = conn.execute('SELECT report FROM extraction_runs WHERE trigger = ? ORDER BY start_time DESC LIMIT 1', (tier,)).fetchone()
    if r: print(f'=== {tier.upper()} ==='); print(r[0])
conn.close()
"
```

The report shows: provider table (events, odds, 1x2/spread/total breakdown, match rate, speed), Pinnacle coverage delta, and ISSUES section. **Focus on the ISSUES section** — it flags:
- `!` = Critical: failed providers, 0 events, low match rate
- `~` = Warning: missing markets, slow extraction, rate limits, sport errors

### Step 2: Diagnose Specific Issues

**Provider with low match rate (< 50%):**
```sql
-- Check which sports are failing to match
SELECT sport, events_extracted, events_matched, events_unmatched,
       ml_count, spread_count, total_count
FROM sport_run_metrics WHERE provider_id = '<pid>'
ORDER BY events_unmatched DESC;
```
Root causes: sport name mismatch (check `sports.yaml` aliases), team name normalization gap, timezone date offset

**Provider with missing spread/total:**
```sql
-- Check market breakdown per sport
SELECT sport, ml_count, spread_count, total_count, odds_extracted
FROM sport_run_metrics WHERE provider_id = '<pid>' AND odds_extracted > 0
ORDER BY ml_count DESC;
```
Root causes: extractor not parsing spread/total DOM elements, dedup blocking multiple lines, point value not on outcomes

**Provider with 0 events:**
Check logs for DNS errors (patchright add_init_script), Cloudflare/Imperva blocks, site redesign, rate limiting. For browser providers: check if WS connection still works, DOM selectors still valid.

**Slow provider (> 300s):**
```sql
-- Check per-sport timing to find the bottleneck
SELECT sport, duration_seconds, events_extracted, odds_extracted
FROM sport_run_metrics WHERE provider_id = '<pid>'
ORDER BY duration_seconds DESC;
```
Root causes: too many leagues/pages to navigate, unnecessary waits, sequential instead of parallel, WS data not arriving

### Step 3: Full Provider Metrics (with new fields)
```python
cd backend && python -c "
import sqlite3
conn = sqlite3.connect('data/degentraderxd.db')
conn.row_factory = sqlite3.Row
# Change trigger: 'sharp', 'api_soft', 'browser_soft'
for r in conn.execute('''
    SELECT prm.provider_id, prm.status, prm.duration_seconds,
           prm.events_processed, prm.events_matched, prm.events_unmatched,
           prm.odds_processed, prm.ml_count, prm.spread_count, prm.total_count,
           prm.error_message
    FROM provider_run_metrics prm JOIN extraction_runs er ON prm.run_id = er.id
    WHERE er.trigger = 'api_soft' ORDER BY prm.odds_processed DESC
''').fetchall():
    d = dict(r)
    matched = d['events_matched'] or 0
    unmatched = d['events_unmatched'] or 0
    total = matched + unmatched
    rate = f'{100*matched/total:.0f}%' if total > 0 else '-'
    err = f' ERR: {d[\"error_message\"][:50]}' if d['error_message'] else ''
    print(f'{d[\"provider_id\"]:16s} | {d[\"duration_seconds\"]:5.0f}s | {d[\"events_processed\"]:4d} ev ({rate:>4} match) | {d[\"odds_processed\"]:5d} odds (ml={d[\"ml_count\"] or 0} spr={d[\"spread_count\"] or 0} tot={d[\"total_count\"] or 0}){err}')
conn.close()
"
```

### Step 4: Per-Sport Breakdown (with matching + markets)
```python
cd backend && python -c "
import sqlite3
from collections import defaultdict
conn = sqlite3.connect('data/degentraderxd.db')
conn.row_factory = sqlite3.Row
# Change trigger: 'sharp', 'api_soft', 'browser_soft'
rows = conn.execute('''
    SELECT srm.provider_id, srm.sport, srm.events_extracted, srm.odds_extracted,
           srm.events_matched, srm.events_unmatched, srm.ml_count, srm.spread_count,
           srm.total_count, srm.duration_seconds, srm.error_message
    FROM sport_run_metrics srm JOIN extraction_runs er ON srm.run_id = er.id
    WHERE er.trigger = 'browser_soft' ORDER BY srm.provider_id, srm.odds_extracted DESC
''').fetchall()
by_p = defaultdict(list)
for r in rows: by_p[r['provider_id']].append(dict(r))
for p, sports in sorted(by_p.items()):
    tot = sum(s['odds_extracted'] for s in sports)
    print(f'\n{p} ({tot} odds):')
    for s in sports:
        if s['odds_extracted'] > 0 or s['events_extracted'] > 0:
            m = s['events_matched'] or 0; u = s['events_unmatched'] or 0
            rate = f'{100*m/(m+u):.0f}%' if (m+u) > 0 else '-'
            print(f'  {s[\"sport\"]:15s}: {s[\"events_extracted\"]:3d} ev ({rate:>4} match), {s[\"odds_extracted\"]:4d} odds (ml={s[\"ml_count\"] or 0} spr={s[\"spread_count\"] or 0} tot={s[\"total_count\"] or 0}) {s[\"duration_seconds\"]:5.1f}s')
conn.close()
"
```

### What to Look For (Optimization Checklist)

| Signal | What it means | Action |
|--------|---------------|--------|
| Match rate < 70% | Fuzzy matching failing | Check `sports.yaml` aliases, team name normalization |
| 0 spread OR 0 total | Extractor not parsing market type | Check extractor parser, run with debug logging |
| ratio < 2.5 | Few odds per event | Missing market types or dedup too aggressive |
| ratio > 5.0 | Good multi-market coverage | Reference provider for others |
| events < 50 | Possibly broken or Pinnacle coverage gap | Check extractor logs, try manual extraction |
| duration > 300s | Slow extraction | Profile per-sport timing, reduce waits |
| sport errors | Individual sport failures | Usually timeouts — increase sport_timeout or reduce scope |

### Extraction Tables Schema

| Table | Key Fields |
|-------|-----------|
| `extraction_runs` | `id`, `start_time`, `duration_seconds`, `total_events`, `total_odds`, `trigger`, `report` |
| `provider_run_metrics` | `provider_id`, `events_processed`, `events_matched`, `events_unmatched`, `odds_processed`, `ml_count`, `spread_count`, `total_count`, `duration_seconds`, `status`, `error_message` |
| `sport_run_metrics` | `provider_id`, `sport`, `events_extracted`, `events_matched`, `events_unmatched`, `odds_extracted`, `ml_count`, `spread_count`, `total_count`, `duration_seconds`, `error_message` |
| `boost_extraction_logs` | `provider_id`, `scraper_type`, `status`, `boosts_found`, `error_message` |
| `specials` | `provider`, `title`, `boosted_odds`, `original_odds`, `boost_pct`, `sport`, `event`, `edge_pct`, `fair_odds`, `is_positive_ev`, `matched_outcome`, `scraped_at` |

### Trigger Types

| Trigger | Providers | Typical Duration |
|---------|-----------|-----------------|
| `sharp` | Pinnacle + Polymarket | ~15s |
| `api_soft` | 19 API providers (Kambi, Altenar, Gecko, Spectate, VBet) | ~150s |
| `browser_soft` | 10 browser providers (Tipwin, Spectate, ComeOn, etc.) | ~480s |
| `manual` | User-specified providers | Varies |

## Specials / Odds Boosts Pipeline

**Separate from regular extraction** — different data models, schedules, no shared lock. Boosts run on their own 120-minute scheduler tier.

### Architecture
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

**Key: EV enrichment runs at scrape time, not at query time.** The GET /api/specials endpoint reads pre-computed data from DB.

### EV Enrichment Logic (`src/analysis/ev_enrichment.py`)
- Only 1x2/moneyline boosts can be EV-analyzed (combos/props filtered via PROP_KEYWORDS)
- Matches boost event name against Pinnacle events using normalized team names
- De-vigs Pinnacle odds (multiplicative method) to get fair odds
- `edge_pct = (boosted_odds / fair_odds - 1) * 100`
- Sanity check: edge > 100% = wrong match, skip

### Boost Health in Extraction Report
The extraction report includes a "BOOST SCRAPER HEALTH" section showing:
- Per-provider table: scraper_type, status, boosts_found, duration, errors
- Flags: failed scrapers, 0-boost providers, slow scrapers (>60s)
- DB specials count and +EV count

### Boost Extraction Review
```python
cd backend && python -c "
import sqlite3
conn = sqlite3.connect('data/degentraderxd.db')

# Check boost scrape health
print('=== BOOST SCRAPE LOG ===')
for r in conn.execute('SELECT provider_id, scraper_type, status, boosts_found, duration_seconds, error_message FROM boost_extraction_logs ORDER BY boosts_found DESC'):
    err = f' ERR: {r[5][:40]}' if r[5] else ''
    print(f'  {r[0]:20s} | {r[1]:10s} | {r[2]:7s} | {r[3]:3d} boosts | {r[4]:5.0f}s{err}')

# Check stored specials with EV
print('\n=== SPECIALS IN DB ===')
total = conn.execute('SELECT COUNT(*) FROM specials').fetchone()[0]
ev_pos = conn.execute('SELECT COUNT(*) FROM specials WHERE is_positive_ev = 1').fetchone()[0]
matched = conn.execute('SELECT COUNT(*) FROM specials WHERE edge_pct IS NOT NULL').fetchone()[0]
print(f'  Total: {total} | Matched to Pinnacle: {matched} | +EV: {ev_pos}')

# Top +EV boosts
print('\n=== TOP +EV BOOSTS ===')
for r in conn.execute('SELECT provider, event, boosted_odds, fair_odds, edge_pct FROM specials WHERE is_positive_ev = 1 ORDER BY edge_pct DESC LIMIT 10'):
    print(f'  {r[0]:15s} | {r[1][:35]:35s} | boosted={r[2]:.2f} fair={r[3]:.2f} edge={r[4]:+.1f}%')
conn.close()
"
```

## Performance Architecture

### Key Optimizations Applied

**Data Integrity:**
- `OddsBatchProcessor.__exit__` always flushes (even on exception) to prevent data loss
- `HttpTransport.post()` has 429 retry with exponential backoff (matching GET behavior)
- All bare `except:` replaced with specific exception types (`ValueError`, `TypeError`, `Exception`)

**Query Performance:**
- DB indexes on Odds: `(provider_id, market)`, `(updated_at)`, `(event_id, market, outcome)`
- N+1 queries eliminated in `scan_value_with_stakes()` and opportunities route (pre-fetch events)
- `_get_fair_odds()` accepts pre-computed `pinnacle_market` dict (built once per market, not per outcome)
- `soft_prob_sums` pre-computed per provider per market (avoids O(outcomes * providers) recomputation)

**Resource Management:**
- Altenar uses `self.transport` (shared `HttpTransport`) instead of creating new `aiohttp.ClientSession` per call
- YAML config loaded once via `@lru_cache` in route handlers (bankroll.py imports from providers.py)

**Algorithm Optimization:**
- `normalize_outcome()` fast path: keyword checks ("1", "x", "2", "home", "away") before any fuzzy matching
- Fuzzy matching reduced from 6 calls to 2 per outcome (single `token_set_ratio` per team)

**Architecture:**
- `_resolve_event_id()` extracted from `store_provider_event()` (~180 lines of matching logic separated)
- Event resolution: exact → fuzzy → swapped-team → default (clear fallback chain)
