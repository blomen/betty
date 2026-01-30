# OddOpp - Betting Analytics Platform

Sports betting analytics platform for finding value bets by comparing odds across providers against Polymarket (truth source).

## Tech Stack

**Backend:** Python 3.10+, FastAPI, SQLAlchemy/SQLite, aiohttp, Pydantic, thefuzz, Playwright (optional)

**Frontend:** React 19, Vite 7.2, Tailwind CSS 4.1, React Router 7

## Project Structure

```
backend/
├── src/
│   ├── core/           # Retriever, Transport, BrowserRetriever base classes
│   ├── providers/      # Provider extractors (kambi.py, pinnacle.py, etc.)
│   ├── analysis/       # value.py, arbitrage.py
│   ├── db/models.py    # SQLAlchemy models
│   ├── matching/       # normalizer.py, matcher.py, aliases.yaml
│   ├── config/         # providers.yaml, sports.json
│   ├── pipeline/       # orchestrator.py, storage.py, utils.py
│   └── factory.py      # ExtractorFactory singleton
├── data/               # SQLite database
└── scripts/            # Utility scripts

frontend/src/           # React app (pages/, components/, utils/)
tests/                  # Integration tests
```

## Commands

```bash
# Backend
pip install -e .                    # Install
pip install -e ".[dev]"             # With dev deps
python main.py                      # Run extraction
python main.py --providers unibet   # Specific provider
pytest tests/ -v                    # Run tests
uvicorn backend.src.api:app --port 8000  # Start API

# Frontend
cd frontend && npm install && npm run dev  # Dev server (port 3000)
```

## Core Concepts

### Canonical Event ID
Format: `{sport}:{home_normalized}:{away_normalized}:{YYYYMMDD}`
Example: `football:arsenal:chelsea:20260122`
Source: `backend/src/pipeline/utils.py:12`

### StandardEvent
All providers normalize to this dataclass: `backend/src/core/retriever.py`
```python
@dataclass
class StandardEvent:
    id, name, sport, markets, provider, url
    start_time, home_team, away_team, league
```

### Market Filtering (1x2 Only)
Only `1x2` and `moneyline` markets are stored. All other markets (spreads, totals) are filtered out.
Source: `backend/src/pipeline/storage.py:23`
```python
ALLOWED_MARKETS = {'1x2', 'moneyline'}
```

### Outcomes
Standardized to: `home`, `away`, `draw`

### Polymarket as Truth Source
Polymarket odds = fair probability (no margin). Value exists when provider odds exceed fair odds.
Edge% = (provider_odds / fair_odds - 1) * 100

## Data Flow

```
Provider API -> Extract -> Normalize teams/outcomes -> Filter (1x2 only) -> Store in DB
```

## Normalization Rules

### Team Names
`backend/src/matching/normalizer.py:79`
1. Lowercase, remove accents (e.g., u -> u)
2. Remove suffixes: FC, SC, IF, BK, SK, CF, AC, etc.
3. Remove prefixes: Real, Sporting, Club, FC, etc.
4. Apply aliases from `aliases.yaml`

Example: "Real Madrid CF" -> "madrid"

### Outcomes
`backend/src/matching/normalizer.py:395`
| Raw | Normalized |
|-----|------------|
| 1, yes, ja, hemma | home |
| X, oavgjort | draw |
| 2, no, nej, borta | away |
| over, over | over |
| under | under |

### Markets
`backend/src/matching/normalizer.py:235`
| Raw | Normalized |
|-----|------------|
| 1x2, full time, moneyline, vinnare | 1x2 |
| over/under, totala mal | over_under |
| spread, handicap, handikapp | spread |

## Key Files

| Purpose | File |
|---------|------|
| Provider base classes | `backend/src/core/retriever.py` |
| Team normalization | `backend/src/matching/normalizer.py` |
| Team aliases | `backend/src/matching/aliases.yaml` |
| Canonical ID generation | `backend/src/pipeline/utils.py` |
| Market filtering | `backend/src/pipeline/storage.py` |
| Provider configs | `backend/src/config/providers.yaml` |
| Sports configs | `backend/src/config/sports.json` |
| Database models | `backend/src/db/models.py` |
| Factory singleton | `backend/src/factory.py` |

## Database Models

`backend/src/db/models.py`
- **Event**: id (canonical), sport, home_team, away_team, league, start_time
- **Odds**: event_id, provider_id, market, outcome, odds, point (unique constraint on all 5)
- **Provider**: id, name, balance, active

## Workflow Rules

- No emojis - use ASCII style
- Temp files in `/scrap` folder, delete before commit
- Documentation files in `/docs`
