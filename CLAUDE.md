# OddOpp - Betting Analytics Platform

Sports betting analytics platform for finding value bets and arbitrage opportunities by comparing odds across providers against Polymarket (truth source).

## Tech Stack

**Backend (Python 3.10+)**
- FastAPI (REST API)
- SQLAlchemy 2.0+ / SQLite (database)
- aiohttp (async HTTP)
- Pydantic 2.5+ (validation)
- thefuzz (fuzzy matching)
- Playwright (optional DOM scraping)

**Frontend (React 19)**
- Vite 7.2 (build)
- React Router 7
- Tailwind CSS 4.1
- Lucide React (icons)

## Project Structure

```
backend/
├── src/
│   ├── core/           # Base classes: Retriever, Transport, BrowserRetriever
│   ├── providers/      # Extractors: kambi.py, polymarket.py, spectate.py, etc.
│   ├── analysis/       # value.py, arbitrage.py, bonus.py
│   ├── db/models.py    # SQLAlchemy models
│   ├── matching/       # normalizer.py, matcher.py, aliases.yaml
│   ├── bankroll/       # manager.py (Kelly criterion)
│   ├── config/         # loader.py, sports.json, providers.yaml
│   ├── pipeline/       # orchestrator.py, storage.py, utils.py
│   ├── factory.py      # ExtractorFactory singleton
│   └── api.py          # FastAPI routes
├── data/               # SQLite database files
└── scripts/            # Utility scripts

frontend/
├── src/
│   ├── pages/          # Route pages
│   ├── components/     # UI components
│   └── utils/          # API client, formatters
└── package.json

tests/                  # Integration tests
```

## Build & Run Commands

### Backend
```bash
pip install -e .                    # Install package
pip install -e ".[dev]"             # With dev dependencies
pip install -e ".[scrape]"          # With Playwright

python main.py                      # Run extraction pipeline
python main.py --providers unibet   # Specific providers
python main.py --no-poly            # Skip Polymarket

pytest tests/                       # Run tests
pytest tests/ -v                    # Verbose
```

### Frontend
```bash
cd frontend
npm install
npm run dev      # Dev server (http://localhost:3000)
npm run build    # Production build
```

**Note:** Frontend requires backend running on port 8000 for API proxy.

### Workflow Rules
- No emojis - use ASCII style for symbols
- Temp files created in `/scrap` folder, deleted before commit
- Documentation files created in `/docs`

---

## Core Concepts

### Canonical Event IDs
Events matched across providers using: `backend/src/pipeline/utils.py:15`
```
{sport}:{home_normalized}:{away_normalized}:{YYYYMMDD}
```
Example: `football:arsenal:chelsea:20260122`

### StandardEvent Dataclass
All providers normalize to `StandardEvent`: `backend/src/core/retriever.py:10-20`
```python
@dataclass
class StandardEvent:
    id, name, sport, markets, provider, url
    start_time, home_team, away_team, league
```

### Polymarket as Truth Source
Polymarket odds = fair probability (no bookmaker margin). Value exists when provider odds exceed fair odds.

### Market Type Standardization
| Market Type | Outcomes | Point Required | Description |
|-------------|----------|----------------|-------------|
| `1x2` | `home`, `draw`, `away` | No | Three-way (football, hockey) |
| `moneyline` | `home`, `away` | No | Two-way (basketball, tennis) |
| `over_under` | `over`, `under` | Yes | Total goals/points |
| `spread` | `home`, `away` | Yes | Handicap betting |

### Outcome Normalization Rules
- Always lowercase: `home`, `away`, `draw`, `over`, `under`
- Map provider variants: `1` -> `home`, `X` -> `draw`, `2` -> `away`
- Swedish: `Över` -> `over`, `Under` -> `under`

### Analysis Functions
- **Value detection**: `backend/src/analysis/value.py:45` - Edge% = (provider_odds / fair_odds - 1) * 100
- **Arbitrage detection**: `backend/src/analysis/arbitrage.py:38` - Sum implied probs < 1 = guaranteed profit
- **Kelly stakes**: `backend/src/bankroll/manager.py:27` - f* = (bp - q) / b

---

## Architecture Patterns

### Factory Singleton
`backend/src/factory.py:24-36` - Single access point for all providers:
```python
factory = ExtractorFactory.get_instance()
extractor = factory.get_extractor("unibet")
```
- Loads configs from YAML on initialization
- Caches extractor instances in `_extractor_cache`
- Routes to Retriever type based on `retriever_type` config

### Retriever Hierarchy
`backend/src/core/retriever.py:22-69`
```
Retriever (ABC)
├── KambiRetriever      # REST API (13 providers)
├── AltenarRetriever    # REST API
├── PinnacleRetriever   # REST API
└── BrowserRetriever (ABC)
    ├── SpectateRetriever   # Browser + API interception
    ├── GeckoRetriever      # Browser + API interception
    ├── SnabbareRetriever   # DOM scraping
    └── HajperRetriever     # WebSocket interception
```

### Configuration-Driven Architecture
Provider behavior driven by YAML/JSON config:
- `backend/src/config/providers.yaml` - Provider configs
- `backend/src/config/sports.json` - Sports/leagues with provider-specific IDs
- `backend/src/config/loader.py` - ConfigLoader singleton with validation

### Repository/Upsert Pattern
`backend/src/pipeline/storage.py` - Deduplication via upsert:
```python
existing = session.query(Odds).filter(...).first()
if existing:
    existing.odds = odds  # Update
else:
    session.add(Odds(...))  # Insert
```

### Error Resilience
Per-provider try-catch ensures one failure doesn't stop extraction:
```python
for provider_id in target_providers:
    try:
        await self._extract_provider(...)
    except Exception as e:
        logger.error(f"Failed: {provider_id}: {e}")
        continue  # Continue with other providers
```

---

## Database Models

`backend/src/db/models.py`

| Model | Line | Key Fields |
|-------|------|------------|
| `Event` | 29 | id (canonical), sport, home_team, away_team, league, start_time |
| `Provider` | 55 | id, name, balance, active |
| `Odds` | 78 | event_id, provider_id, market, outcome, odds, point |
| `Bet` | 142 | event_id, provider_id, stake, odds, result |
| `Profile` | 167 | kelly_fraction, min_edge, min_profit |
| `Opportunity` | 188 | type, edge_pct, profit_pct |

**Odds Unique Constraint:**
```python
UniqueConstraint('event_id', 'provider_id', 'market', 'outcome', 'point')
```

---

## Provider Development

### Implementation Workflow
```
Research -> Implement -> Configure -> Test -> Validate -> Deploy
```

### Research Phase
1. Open DevTools Network tab, filter by Fetch/XHR or WS
2. Navigate through site, observe API calls
3. Determine data source type:
   - REST API -> `Retriever` base class
   - Browser + API -> `BrowserRetriever` base class
   - WebSocket -> `BrowserRetriever` with interception
   - DOM scraping -> `BrowserRetriever` (last resort)

### Implementation Templates

**REST API Provider:**
```python
class MyProviderRetriever(Retriever):
    SPORT_MAPPING = {1: 'football', 2: 'basketball'}
    MARKET_TYPE_MAP = {1: '1x2', 2: 'over_under'}

    async def extract(self, sport: str, limit: int = 100) -> List[StandardEvent]:
        url = f"{self.api_base}/events?sportId={self._get_sport_id(sport)}"
        data = await self.transport.get(url)
        return self.parse(data, sport)

    def parse(self, data: dict, sport: str) -> List[StandardEvent]:
        events = []
        for raw in data.get('events', []):
            home = normalize_team_name(raw['home'])
            away = normalize_team_name(raw['away'])
            markets = self._parse_markets(raw.get('markets', []))
            events.append(StandardEvent(...))
        return events
```

**Browser-Based Provider:**
```python
class MyProviderRetriever(BrowserRetriever):
    async def extract(self, sport: str, limit: int = 100) -> List[StandardEvent]:
        await self._ensure_init(url=f"{self.site_url}/{sport}")
        # Intercept API response or scrape DOM
        data = await self._fetch_api(f"/api/events/{sport}")
        return self.parse(data, sport)
```

### Configuration
Add to `backend/src/config/providers.yaml`:
```yaml
providers:
  myprovider:
    id: myprovider
    name: "My Provider"
    retriever_type: custom  # or kambi, altenar, etc.
    api_base: https://api.provider.com
    active: false  # Set true after validation
```

Register in `backend/src/factory.py:get_extractor()` if new retriever type.

### Team Name Normalization
Always use `normalize_team_name()`: `backend/src/matching/normalizer.py:25`
```python
from backend.src.matching.normalizer import normalize_team_name

home = normalize_team_name("Real Madrid CF")  # -> "madrid"
away = normalize_team_name("FC Barcelona")    # -> "barcelona"
```

Rules:
1. Lowercase all characters
2. Remove accents/diacritics (e -> e, u -> u)
3. Remove suffixes: FC, SC, IF, BK, SK, CF, AC
4. Remove prefixes: Real, Sporting, Club
5. Remove age indicators: U21, U19, B team

### Common Pitfalls
- **Missing normalization**: Always call `normalize_team_name()` for both teams
- **Missing outcome standardization**: Map all outcomes to `home/away/draw/over/under`
- **Missing point values**: Spreads and totals MUST have `point` field
- **Returning exceptions**: Return `[]` on error, never raise
- **Not deduplicating**: Use `seen_ids = set()` to prevent duplicates

---

## Provider Validation

### 7 Validation Criteria

| # | Criterion | Pass Condition |
|---|-----------|----------------|
| 1 | Sports Coverage | Returns events for >= 1 sport |
| 2 | Event Discovery | All events have home_team, away_team, sport |
| 3 | Market Coverage | Has 1x2/moneyline + over_under + spread |
| 4 | Normalization | Team names lowercase, no suffixes |
| 5 | Database Compliance | All odds > 1.0, point values present |
| 6 | Performance | < 30s per sport extraction |
| 7 | Error Handling | No crashes, graceful degradation |

### Run Validation
```bash
python scripts/validate_provider.py {provider_name}
python scripts/validate_provider.py kambi --sport basketball
```

### Provider Status Matrix

**Tier 1 - Production Ready (7/7):** Kambi (13 providers), Pinnacle, Betinia
**Tier 2 - Production (6/7):** Gecko V2 (betsson, betsafe, nordicbet), Hajper
**Tier 3 - Staging (5/7):** 888sport, mrgreen, ComeOn, Snabbare
**Tier 4 - Disabled:** fastbet (wrong platform), coolbet (blocked), polymarket (architecture)

Full status details: `backend/docs/validated.md`

---

## Optimization Patterns

### DOM Scraper Optimizations

**Pattern 1: Early Empty Detection** - Check for "No matches" before waiting
```python
empty = await page.query_selector_all('text=/No matches|Inga matcher/i')
if empty: return []
```

**Pattern 2: Increase Concurrency** - Use `Semaphore(10)` instead of 5
```python
sem = asyncio.Semaphore(10)
async with sem:
    return await self._process_league(league, sport)
```

**Pattern 3: Aggressive Timeouts** - Reduce wait times
```python
await page.goto(url, timeout=30000)      # 60s -> 30s
await page.wait_for_selector(sel, timeout=5000)  # 15s -> 5s
```

### API Optimizations

**Pattern 1: Response Caching** - Cache digest with 5-min TTL
```python
if sport in self._digest_cache:
    if (now - cache_time).seconds < 300:
        return self._digest_cache[sport]
```

**Pattern 2: Parallel Requests** - Use `asyncio.gather()`
```python
tasks = [fetch_bucket(b) for b in buckets]
results = await asyncio.gather(*tasks)
```

**Pattern 3: Filter Empty Buckets** - Only fetch buckets with count > 0
```python
if digest.get(key, 0) > 0:
    buckets_to_fetch.append(key)
```

### Performance Targets
| Metric | Target | Maximum |
|--------|--------|---------|
| Single sport | < 10s | < 30s |
| Full extraction | < 60s | < 120s |
| API timeout | 10s | 30s |

---

## Quick Reference

### Common Commands
```bash
# Run pipeline
python main.py
python main.py --providers unibet,betsson --sports football

# Validate provider
python scripts/validate_provider.py kambi

# Test extraction
python -c "
import asyncio
from src.factory import ExtractorFactory
async def test():
    p = ExtractorFactory.get_provider('kambi')
    events = await p.extract('football')
    print(f'{len(events)} events')
asyncio.run(test())
"

# Start API server
uvicorn backend.src.api:app --reload --port 8000
```

### Key File Locations
| Purpose | File |
|---------|------|
| Provider base classes | `backend/src/core/retriever.py` |
| Team normalization | `backend/src/matching/normalizer.py` |
| Team aliases | `backend/src/matching/aliases.yaml` |
| Canonical ID generation | `backend/src/pipeline/utils.py` |
| Provider configs | `backend/src/config/providers.yaml` |
| Sports configs | `backend/src/config/sports.json` |
| Database models | `backend/src/db/models.py` |
| Factory singleton | `backend/src/factory.py` |

### Debugging Checklist
- **No events returned**: Check API URL, sport ID mapping, response format
- **Team names not matching**: Verify `normalize_team_name()` called
- **Markets missing**: Check MARKET_TYPE_MAP, log skipped markets
- **Duplicate odds**: Verify canonical ID generation, check deduplication
- **Slow extraction**: Profile per-league times, check empty detection

### Testing
```bash
pytest tests/test_pipeline.py       # Pipeline tests
pytest tests/test_matching.py       # Fuzzy matching
pytest tests/ -v                    # All tests verbose
```

Test config in `pyproject.toml:44` sets `asyncio_mode = "auto"`.

---

## Reference Documentation

| Topic | Location |
|-------|----------|
| Provider validation status | `backend/docs/validated.md` |
| Provider implementation guide | (merged into this doc) |
| Optimization patterns | (merged into this doc) |
| Architecture patterns | (merged into this doc) |
