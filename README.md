# OddOpp

**Odds Opportunities** - Compare betting odds against sharp bookmakers to find value.

## Quick Start

```bash
# Install
cd backend && pip install -e .

# Extract from truth sources (Pinnacle + Polymarket)
python -m src.extract --sources

# Extract from all providers
python -m src.extract --all

# Find opportunities
python -m src.detect

# Run API
uvicorn src.api:app --reload
```

## Documentation

See [CLAUDE.md](CLAUDE.md) for detailed architecture, adding providers, and development workflow.

## Architecture

```
backend/src/
├── providers/     # Bookmaker extractors (Kambi, Gecko, Spectate, etc.)
├── pipeline/      # Orchestrator + storage
├── analysis/      # Value detection, arbitrage, devigging
├── matching/      # Event normalization + fuzzy matching
├── db/            # SQLite models
├── api.py         # FastAPI endpoints
└── config/        # Provider configs (YAML)

frontend/src/
├── components/    # React UI
└── hooks/         # Data fetching
```

## Truth Sources

| Source | Method | Status |
|--------|--------|--------|
| Pinnacle | Guest API | Sharp lines (devigged) |
| Polymarket | Public API | Prediction market |
