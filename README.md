# Oddopp

**Odds Opportunities** - Compare betting odds against sharp bookmakers to find value.

## Quick Start

```bash
# Install
pip install -e .

# Extract from truth sources (Polymarket + Pinnacle)
python -m src.extract --sources

# Extract from all providers
python -m src.extract --all

# Find opportunities
python -m src.detect
```

## Architecture

```
oddopp/
├── src/
│   ├── sources/          # Truth sources (Polymarket, Pinnacle)
│   ├── extractors/       # Provider extractors (Kambi, etc.)
│   ├── db/               # SQLite models & repository
│   └── utils/            # HTTP, normalization
├── config/               # Provider configs
├── data/                 # SQLite database
└── tests/
```

## Truth Sources

| Source | Method | Status |
|--------|--------|--------|
| Polymarket | Public API | ✅ Ready |

## Extraction Priority

1. **API** (found via Network tab) - fastest, most reliable
2. **DOM Scrape** (Playwright) - fallback when API protected
