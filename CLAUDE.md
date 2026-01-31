# OddOpp - Betting Analytics Platform

## WHAT This Project Is

OddOpp compares odds across 40+ sportsbooks against sharp sources (Pinnacle, Polymarket) to find value bets and arbitrage opportunities.

**Architecture:**
```
backend/src/
├── providers/     # Bookmaker extractors (Kambi, Gecko, Spectate, Pinnacle, etc.)
│   └── mixins/    # Shared functionality (RSocket decoding)
├── pipeline/      # Orchestrator + storage
├── analysis/      # Value detection, arbitrage, devigging
├── matching/      # Event normalization + fuzzy matching
├── bankroll/      # Kelly criterion + stake sizing
├── db/            # SQLAlchemy models (Event, Odds, Bet, Provider)
├── constants.py   # Shared constants (ALLOWED_MARKETS, SHARP_PROVIDERS)
├── api.py         # FastAPI endpoints
└── app.py         # Typer CLI

frontend/src/
├── components/    # Terminal-style React UI
├── hooks/         # Data fetching + WebSocket
└── services/      # API client + Claude chat
```

**Tech stack:** Python 3.10+ / FastAPI / SQLite / Playwright | React 19 / TypeScript / Vite / Tailwind

## WHY It's Structured This Way

- **Provider extractors are isolated** - Each bookmaker has unique API/DOM structure
- **Sharp sources separate** - Pinnacle + Polymarket provide "fair odds" baseline
- **Matching layer abstracts providers** - Fuzzy matching normalizes "Real Madrid CF" → canonical event
- **Analysis is provider-agnostic** - Works on normalized events/odds

## HOW To Work In This Codebase

### Commands
```bash
# Extract odds
python -m src.extract --sources    # Sharp sources only (Pinnacle + Polymarket)
python -m src.extract --all        # All providers

# Find opportunities
python -m src.detect               # Value + arbitrage detection

# Run services
uvicorn src.api:app --reload       # API on :8000
cd frontend && npm run dev         # UI on :5173

# Tests
pytest tests/                      # Run test suite
```

### Adding a New Provider
1. Check provider type in `config/providers.yaml` (Kambi, Gecko V2, Spectate, SBTech, Altenar, etc.)
2. If existing type: add config entry only
3. If new type: create extractor in `providers/`, register in `factory.py`
4. For WebSocket/RSocket providers: use `RSocketMixin` from `providers/mixins/`
5. Test with `python -m src.extract --provider <name>`

### Key Domain Concepts
- **Fair odds**: True probability from Pinnacle (after devigging)
- **Edge %**: `(provider_odds / fair_odds - 1) × 100`
- **Value bet**: Single outcome with positive edge
- **Arbitrage**: Guaranteed profit across multiple providers
- **Sharp source**: Pinnacle ONLY (Polymarket is NOT used as sharp)

### Extraction Scope (IMPORTANT)
**We ONLY extract 1x2/moneyline markets. All other markets are skipped.**

- **Markets extracted**: `1x2`, `moneyline` (match winner bets only)
- **Markets skipped**: over/under, spreads, props, player markets, corners, cards, etc.
- **Live events**: Skipped entirely - only pre-match odds
- **Whitelist enforced in**: `constants.py` via `ALLOWED_MARKETS` (imported by `pipeline/storage.py`)

This keeps the system focused on the highest-value, most comparable market type across all providers.

## Configuration

- `config/providers.yaml` - Provider endpoints, types, concurrency limits
- `config/sports.json` - Sport/league mappings with provider-specific IDs
- `backend/data/oddopp.db` - SQLite database

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

### Code Cleanup Rule (IMPORTANT)
**If you find any redundant code handling markets other than 1x2/moneyline, remove it immediately.**

We only support 1x2/moneyline markets. Any code for over/under, spreads, props, player markets, corners, cards, etc. is dead code and should be deleted. This includes:
- Normalization logic for non-1x2 market types
- Storage logic for non-1x2 markets
- Analysis logic for non-1x2 markets
- UI components for non-1x2 markets

Keep the codebase lean - delete, don't comment out.

## Provider Pipeline Workflow

### Pipeline Data Flow
```
Provider API → StandardEvent
    ↓
normalize_team_name() + normalize_market()
    ↓
generate_canonical_id() → Event (deduplicated)
    ↓
Fuzzy match against Polymarket cache
    ↓
store_odds() → Odds table
    ↓
OpportunityScanner.scan_value() / scan_arbitrage()
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
   python -m src.app arbs    # Show arbitrage
   ```

### Benchmarking Metrics

Track these per provider during extraction:
- **Extraction time** (seconds) - logged at `[provider] sport: N events in X.Xs`
- **Events extracted** (count per sport)
- **API errors** (rate limits, timeouts)
- **Cross-provider matches** (events matched with Pinnacle)

### Known Data Quality Issues

1. **Kambi correct score outcomes** - Some Kambi providers return correct score outcomes (0-1, 1-2, etc.) labeled as '1x2' market. These inflate arbitrage counts. Fix: filter by `betOfferType.id` in Kambi extractor (ID 2 = Match Winner).

2. **Polymarket player name outcomes** - Tennis/esports outcomes stored as player names instead of normalized 'home'/'away'. Fix: enhance outcome normalization for Polymarket.

3. **High edge/profit warnings** - Scanner logs "Suspicious arb" for opportunities with >10% profit, indicating data issues (mismatched events or incorrect markets).

### Data Quality Validation (REQUIRED)

**After any provider changes, run this validation to ensure data quality:**

```bash
cd backend
rm -f data/oddopp.db  # Clear database
python -m src.app extract polymarket pinnacle <provider>
```

**Then run this SQL validation script:**
```python
import sqlite3
conn = sqlite3.connect('data/oddopp.db')
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
| Market types | 1x2 only | 1x2 only | 1x2 only |

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
