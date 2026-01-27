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
│   ├── providers/      # Extractors: kambi.py, polymarket.py, spectate.py, snabbare.py
│   ├── analysis/       # value.py, arbitrage.py, bonus.py
│   ├── db/models.py    # SQLAlchemy models (Event, Odds, Provider, Bet, Profile, Opportunity)
│   ├── matching/       # normalizer.py (team names, markets, outcomes), matcher.py (fuzzy matching)
│   ├── bankroll/       # manager.py (Kelly criterion stake calculations)
│   ├── config/         # loader.py (centralized config), sports.json, providers.yaml
│   ├── pipeline/       # orchestrator.py (main pipeline), storage.py, utils.py
│   ├── factory.py      # ExtractorFactory singleton
│   ├── app.py          # Typer CLI application
│   └── api.py          # FastAPI routes
├── data/               # SQLite database files
└── scripts/            # Debug/utility scripts

frontend/
├── src/
│   ├── pages/          # Route pages (Home, Arbitrage, ValueBets, etc.)
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
npm run dev      # Dev server
npm run build    # Production build
npm run lint     # ESLint
```

### Workflow

- **No emojis allowed, use ascii style for symbols.
- **Temp files like test files or debug files created in /scrap folder, deleted before commit.

## Key Concepts

### Canonical Event IDs
Events are matched across providers using canonical IDs: `backend/src/pipeline/utils.py:15`
```
{sport}:{home_normalized}:{away_normalized}:{YYYYMMDD}
```

### Polymarket as Truth Source
Polymarket odds = fair probability (no bookmaker margin). Value exists when provider odds exceed fair odds.

### Analysis Functions
- **Value detection**: `backend/src/analysis/value.py:45` - Edge% = (provider_odds / fair_odds - 1) * 100
- **Arbitrage detection**: `backend/src/analysis/arbitrage.py:38` - Sum implied probs < 1 = guaranteed profit
- **Kelly stakes**: `backend/src/bankroll/manager.py:27` - f* = (bp - q) / b

### Database Models
- `Event` - Canonical events (provider-agnostic): `backend/src/db/models.py:29`
- `Odds` - Multi-provider odds per event: `backend/src/db/models.py:78`
- `Provider` - Bookmaker metadata/balances: `backend/src/db/models.py:55`
- `Bet` - Manual bet tracking: `backend/src/db/models.py:142`
- `Profile` - User settings (Kelly fraction, thresholds): `backend/src/db/models.py:167`
- `Opportunity` - Detected arbitrage/value/bonus opportunities: `backend/src/db/models.py:188`

### Provider Configuration
Providers configured via YAML/JSON with Pydantic validation:
- `backend/src/config/sports.json` - Sports/leagues with provider-specific IDs
- `backend/src/config/providers.yaml` - Active providers with configurations
- `backend/src/config/loader.py` - Centralized ConfigLoader singleton with validation

## Testing

Tests are primarily integration tests running against real APIs:
```bash
pytest tests/test_pipeline.py       # Pipeline tests
pytest tests/test_matching.py       # Fuzzy matching
```

Test config in `pyproject.toml:44` sets `asyncio_mode = "auto"`.

## Additional Documentation

When working on specific areas, check these files:

| Topic | File |
|-------|------|
| **Provider Implementation** | `backend/docs/PROVIDER_IMPLEMENTATION_GUIDE.md` |
| **Provider Validation** | `backend/docs/validated.md` |
| Architectural patterns | `.claude/docs/architectural_patterns.md` |
| Provider optimization (DOM + API) | `.claude/docs/provider_optimizations.md` |
| Team name normalization | `backend/src/matching/normalizer.py` |
| Provider configs | `backend/src/config/providers.yaml` |
| Config validation | `backend/src/config/loader.py` |

### Provider Development Workflow

**Adding a new provider? Follow this workflow:**

1. **Research** → `backend/docs/PROVIDER_IMPLEMENTATION_GUIDE.md` (Phase 1)
   - Analyze betting site API/structure
   - Choose implementation strategy
   - Document findings

2. **Implement** → `backend/docs/PROVIDER_IMPLEMENTATION_GUIDE.md` (Phase 2-3)
   - Create provider class in `backend/src/providers/{id}.py`
   - Configure in `backend/src/config/providers.yaml`
   - Register in `backend/src/factory.py`

3. **Test** → `backend/docs/PROVIDER_IMPLEMENTATION_GUIDE.md` (Phase 4)
   - Unit tests for normalization
   - Integration tests for extraction
   - Manual verification

4. **Validate** → `backend/docs/validated.md` (7 criteria)
   - Run validation script: `python scripts/validate_provider.py {provider}`
   - Check sports coverage, markets, normalization, performance
   - Document results

5. **Deploy** → `backend/docs/PROVIDER_IMPLEMENTATION_GUIDE.md` (Phase 6)
   - Enable in active providers list
   - Test pipeline integration
   - Monitor performance
   - Commit with validation results
