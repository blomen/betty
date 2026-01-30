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
- **Fair odds**: True probability from sharp sources (after devigging)
- **Edge %**: `(provider_odds / fair_odds - 1) × 100`
- **Value bet**: Single outcome with positive edge
- **Arbitrage**: Guaranteed profit across multiple providers

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
