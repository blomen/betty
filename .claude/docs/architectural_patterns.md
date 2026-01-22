# Architectural Patterns

Documented patterns observed across multiple files in the codebase.

## 1. Factory Pattern with Singleton

**Location**: `backend/src/factory.py:24-36`

The `ExtractorFactory` provides a single access point for all provider extractors:

```python
factory = ExtractorFactory.get_instance()
extractor = factory.get_extractor("unibet")
```

- Loads configs from JSON on initialization
- Caches extractor instances in `_extractor_cache`
- Routes to correct Retriever type based on `retriever_type` config

## 2. Abstract Base Class with Template Method

**Location**: `backend/src/core/retriever.py:22-69`

`Retriever` is the abstract base for all provider extractors:

```python
class Retriever(ABC):
    @abstractmethod
    def _get_sport_url(self, sport: str) -> str
    @abstractmethod
    def parse(self, data, sport) -> List[StandardEvent]

    async def extract(self, sport, limit) -> List[StandardEvent]:  # Template method
```

Implementations:
- `KambiRetriever` (`backend/src/providers/kambi.py`) - 2-step fetch
- `PolymarketRetriever` (`backend/src/providers/polymarket.py`) - Direct API
- `SpectateRetriever` (`backend/src/providers/spectate.py`) - DOM scraping
- `SnabbareRetriever` (`backend/src/providers/snabbare.py`) - Custom API

## 3. Unified Event Model (StandardEvent)

**Location**: `backend/src/core/retriever.py:10-20`

All providers normalize to `StandardEvent`:

```python
@dataclass
class StandardEvent:
    id, name, sport, markets, provider, url
    start_time, home_team, away_team, league
```

Markets use normalized structure:
```python
markets = [{"type": "1x2", "outcomes": [{"name": "home", "odds": 2.10}]}]
```

## 4. Configuration-Driven Architecture

**Locations**:
- `backend/src/config/sports.json` - Sports/leagues config
- `backend/src/config/providers.json` - Active providers whitelist
- `backend/src/config/providers/*.json` - Individual provider configs

Provider behavior is driven by JSON config, not hardcoded:

```json
{
  "id": "unibet",
  "retriever_type": "kambi",
  "api_base": "https://eu1.offering-api.kambicdn.com/...",
  "brand": "ubse"
}
```

Loaded at runtime by `ExtractorFactory._load_configs()` (`backend/src/factory.py:38-105`).

## 5. Repository Pattern

**Location**: `backend/src/db/models.py`

SQLAlchemy models with relationships for data access:

- `Event` (line 29) - Canonical events
- `Odds` (line 78) - Many-to-one with Event and Provider
- `Provider` (line 55) - Bookmaker state
- `Bet` (line 142) - Bet tracking with computed `profit` property

Unique constraints prevent duplicate odds: `backend/src/db/models.py:99-101`

## 6. Upsert Pattern for Deduplication

**Location**: `backend/src/pipeline.py:403-426`

Odds are upserted to avoid duplicates:

```python
def _upsert_odds(self, event_id, provider, market, outcome, odds):
    existing = self.session.query(Odds).filter(...).first()
    if existing:
        existing.odds = odds  # Update
        return 0
    else:
        self.session.add(Odds(...))  # Insert
        return 1
```

## 7. Fuzzy Matching Strategy

**Location**: `backend/src/utils/matching.py`

Multi-stage matching for cross-provider event alignment:

1. **Normalize** team names (line 249): Remove suffixes/prefixes, Unicode normalization
2. **Alias lookup** (line 52-246): 200+ team aliases (Bayern = Bayern Munich = FC Bayern)
3. **Fuzzy match** (line 331): `thefuzz` library with 85% threshold
4. **Token sort** (line 356): Handle word order differences

Match function: `backend/src/utils/matching.py:496-579`

## 8. Strategy Pattern for Analysis

**Locations**:
- `backend/src/analysis/value.py:45` - `find_value()`
- `backend/src/analysis/arbitrage.py:38` - `find_arbitrage()`

Discrete functions with clear inputs/outputs:

```python
def find_value(event_id, market, outcome, provider, provider_odds, fair_odds, min_edge_pct) -> ValueBet | None
def find_arbitrage(event_id, market, odds_by_outcome, min_profit_pct) -> ArbitrageOpportunity | None
```

## 9. Async Context Managers

**Locations**:
- `backend/src/core/retriever.py:71-78`
- `backend/src/pipeline.py:158`

Retrievers implement async context managers for resource cleanup:

```python
async with extractor as source:
    events = await source.extract(sport)
```

Ensures transport/browser cleanup via `__aenter__`/`__aexit__`.

## 10. Error Resilience in Pipeline

**Location**: `backend/src/pipeline.py:127-135`

Per-provider try-catch ensures one failure doesn't stop extraction:

```python
for provider_id in target_providers:
    try:
        provider_results = await self._extract_provider(...)
    except Exception as e:
        logger.error(f"Failed to extract from {provider_id}: {e}")
        results["providers"][provider_id] = {"error": str(e)}
```

## 11. Dataclass DTOs

**Locations**:
- `backend/src/analysis/value.py:21` - `ValueBet`
- `backend/src/analysis/arbitrage.py:20` - `ArbitrageOpportunity`
- `backend/src/bankroll/manager.py:17` - `StakeRecommendation`
- `backend/src/utils/matching.py:486` - `MatchResult`

Dataclasses used for type-safe data transfer with computed properties:

```python
@dataclass
class ValueBet:
    provider_odds: float
    fair_odds: float
    edge_pct: float

    @property
    def expected_value(self) -> float:
        return (self.provider_odds * self.fair_probability) - 1
```

## 12. Canonical ID for Cross-Provider Matching

**Location**: `backend/src/pipeline.py:33-50`

Events from different providers map to one canonical ID:

```python
canonical_id = f"{sport}:{home_norm}:{away_norm}:{date}"
# e.g., "football:manchester_united:liverpool:20250122"
```

Pipeline caches Polymarket events then fuzzy-matches provider events against them (`backend/src/pipeline.py:319-351`).

