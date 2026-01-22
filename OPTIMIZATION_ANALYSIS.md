# OddOpp Code Optimization Analysis

## Executive Summary

Based on validation of all providers and code review, here are the key findings:

**Working Providers (11)**:
- Kambi-based (9): Unibet (117k odds), Expekt (111k), LeoVegas (99k), Casumo (30k), BetMGM (25k), ATG (24k), SpeedyBet (19k), X3000 (19k), PAF (19k)
- MrGreen: 186 odds (limited coverage)
- Polymarket: 14k odds (2,278 events)

**Non-Working Providers (2)**:
- 888sport: 0 odds (Spectate-based, needs investigation)
- Snabbare: 0 odds (DOM scraper, likely broken)

## Priority Optimization Opportunities

### 1. CRITICAL: Parallel Provider Extraction

**Current Issue**: `backend/src/pipeline/orchestrator.py:109-124`
```python
for provider_id in target_providers:
    log(f"Extracting from {provider_id}...")
    provider_results = await self._extract_provider(provider_id, ...)
```

Providers are extracted sequentially. With 11 providers, this takes 11x longer than necessary.

**Recommendation**: Extract providers in parallel
```python
async def _extract_all_providers(self, providers, sports, limit, log_fn):
    tasks = [
        self._extract_provider(pid, sports, limit)
        for pid in providers
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return dict(zip(providers, results))
```

**Impact**: Could reduce total extraction time by 5-10x

---

### 2. HIGH: Kambi Group Cache Sharing

**Current Issue**: `backend/src/providers/kambi.py:31`
```python
self._group_cache = {}  # Instance-level cache
```

Each Kambi provider fetches the same group tree independently. Unibet, Expekt, LeoVegas all hit the same Kambi API but don't share the group cache.

**Recommendation**: Class-level or factory-level cache
```python
class KambiRetriever(Retriever):
    _SHARED_GROUP_CACHE = {}  # Class-level cache

    async def extract(self, sport: str, limit: int = 50):
        cache_key = f"{self.brand}:{groups_url}"
        if cache_key in self._SHARED_GROUP_CACHE:
            group_data = self._SHARED_GROUP_CACHE[cache_key]
```

**Impact**: Reduces API calls by ~90% for Kambi providers

---

### 3. HIGH: Parallel Kambi Group Fetching

**Current Issue**: `backend/src/providers/kambi.py:69-72`
```python
for group in target_groups:
    events = await self._fetch_group_events(group)
    all_events.extend(events)
```

Groups are fetched sequentially. Football might have 50+ groups.

**Recommendation**: Fetch groups in parallel with semaphore
```python
sem = asyncio.Semaphore(5)  # Limit concurrency

async def fetch_with_limit(group):
    async with sem:
        return await self._fetch_group_events(group)

tasks = [fetch_with_limit(g) for g in target_groups[:limit]]
results = await asyncio.gather(*tasks)
```

**Impact**: 3-5x faster extraction per Kambi provider

---

### 4. MEDIUM: Centralized Market Normalization

**Current Issue**: Market name normalization is duplicated across providers:
- `backend/src/providers/spectate.py:247-272` - 30+ lines of market mappings
- Each provider has its own mapping logic
- Duplicated between providers

**Recommendation**: Create centralized normalizer
```python
# backend/src/matching/market_normalizer.py
MARKET_MAPPINGS = {
    "en": {
        "match winner": "moneyline",
        "match result": "moneyline",
        "1x2": "moneyline",
        "over/under": "over_under",
        "totals": "over_under",
        "spread": "spread",
        "handicap": "spread",
    },
    "sv": {
        "matchresultat": "moneyline",
        "vinnare": "moneyline",
        "över/under": "over_under",
        "handikapp": "spread",
    }
}

def normalize_market_name(raw_name: str, locale: str = "sv") -> str:
    """Centralized market name normalization"""
    raw = raw_name.lower().strip()

    # Try exact match
    if raw in MARKET_MAPPINGS.get(locale, {}):
        return MARKET_MAPPINGS[locale][raw]

    # Try fallback to English
    if raw in MARKET_MAPPINGS["en"]:
        return MARKET_MAPPINGS["en"][raw]

    # Fuzzy matching...
    return "unknown"
```

**Impact**: Easier maintenance, consistent normalization

---

### 5. MEDIUM: Database Batch Commits

**Current Issue**: `backend/src/pipeline/orchestrator.py:230`
```python
for sport in sports:
    # ... process events ...
    self.session.commit()  # Commit after each sport
```

Commits happen after every sport extraction. With 12 sports, that's 12 commits per provider.

**Recommendation**: Batch commits
```python
BATCH_SIZE = 100
event_count = 0

for sport in sports:
    for event in events:
        # ... store event ...
        event_count += 1
        if event_count % BATCH_SIZE == 0:
            self.session.commit()

self.session.commit()  # Final commit
```

**Impact**: 20-30% faster database writes

---

### 6. MEDIUM: Fix Snabbare (DOM Scraper)

**Current Issue**: Snabbare has 0 odds despite complex DOM scraping logic
- `backend/src/providers/snabbare.py:140-157` - Complex DOM selectors
- Likely broken due to site changes
- Very slow (Playwright overhead)

**Recommendation**: Either:
1. **Debug and fix** the selectors (check if site structure changed)
2. **Switch to API-based extraction** if Snabbare has an API
3. **Deprecate** if not worth maintaining

**Investigation needed**: Run Snabbare manually to see error messages

---

### 7. LOW: Sport Matching Centralization

**Current Issue**: `backend/src/providers/kambi.py:185-195`
```python
def _match_sport(self, group_sport: str, target_sport: str) -> bool:
    # Sport aliasing logic duplicated per provider
```

**Recommendation**: Move to config or normalizer
```python
# backend/src/matching/normalizer.py
SPORT_ALIASES = {
    "football": ["soccer", "fotboll", "football"],
    "ice_hockey": ["ice_hockey", "ishockey", "hockey"],
    "mma": ["martial_arts", "ufc/mma", "mma", "ufc"],
    "rugby": ["rugby_union", "rugby_league", "rugby"],
}
```

**Impact**: Minor, but improves maintainability

---

### 8. LOW: API Response Caching

**Current Issue**: No caching of API responses
- Polymarket is stable - events don't change every minute
- Could cache for 5-10 minutes

**Recommendation**: Simple time-based cache
```python
from datetime import datetime, timedelta

class CachedTransport:
    def __init__(self, ttl_seconds=300):
        self.cache = {}
        self.ttl = timedelta(seconds=ttl_seconds)

    async def get(self, url, params=None):
        key = (url, str(params))
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < self.ttl:
                return data

        data = await self._real_get(url, params)
        self.cache[key] = (data, datetime.now())
        return data
```

**Impact**: Reduces API calls during testing/debugging

---

### 9. CRITICAL: Fix 888sport (Spectate Provider)

**Current Issue**: 888sport has 0 odds despite having SpectateRetriever
- `backend/src/providers/spectate.py` - Full implementation exists
- Likely auth/protection issue or misconfiguration

**Investigation Needed**:
1. Check provider config in `backend/src/config/providers.yaml`
2. Test SpectateRetriever manually
3. Check if domain/API endpoints are correct

---

## Performance Metrics (Current State)

Based on database validation:

| Provider | Odds | Events | Sports | Status |
|----------|------|--------|--------|--------|
| Unibet | 117,417 | 1,626 | 11 | ✓ Working |
| Expekt | 111,451 | 1,403 | 11 | ✓ Working |
| LeoVegas | 99,133 | 1,259 | 11 | ✓ Working |
| Casumo | 30,543 | 412 | 9 | ✓ Working |
| BetMGM | 24,988 | 473 | 11 | ✓ Working |
| ATG | 23,758 | 412 | 11 | ✓ Working |
| SpeedyBet | 19,313 | 397 | 11 | ✓ Working |
| X3000 | 19,290 | 397 | 11 | ✓ Working |
| PAF | 19,042 | 406 | 11 | ✓ Working |
| Polymarket | 14,481 | 2,278 | 9 | ✓ Working |
| MrGreen | 186 | 62 | 1 | ⚠ Limited |
| 888sport | 0 | 0 | 0 | ✗ Broken |
| Snabbare | 0 | 0 | 0 | ✗ Broken |

**Total**: 479,602 odds across 4,103 events

**Matching Rate**: 8-13% of bookmaker events match Polymarket
- This is expected - prediction markets cover different events than bookmakers

---

## Implementation Priority

### Phase 1 (High Impact, Low Effort)
1. ✓ Validate pipeline (DONE)
2. Parallel provider extraction (orchestrator.py)
3. Kambi group cache sharing (kambi.py)

### Phase 2 (High Impact, Medium Effort)
4. Parallel Kambi group fetching (kambi.py)
5. Database batch commits (orchestrator.py)
6. Fix 888sport (investigate config)

### Phase 3 (Low Priority)
7. Centralized market normalization
8. Sport matching centralization
9. API response caching
10. Fix or deprecate Snabbare

---

## Code Quality Observations

**Strengths**:
- Clean separation of concerns (Retriever, Transport, Storage)
- Good use of StandardEvent for normalization
- Comprehensive config-driven architecture
- Proper async/await throughout

**Areas for Improvement**:
- Error handling could be more specific (catch specific exceptions)
- More type hints would help
- Some duplicated logic across providers
- No rate limiting (could hit API limits)
- No retry logic for transient failures

---

## Estimated Performance Gains

With all Phase 1 & 2 optimizations:

| Metric | Current | Optimized | Improvement |
|--------|---------|-----------|-------------|
| Total extraction time | ~10-15 min | ~2-3 min | 5x faster |
| Kambi provider time | ~60-90s each | ~15-20s each | 4x faster |
| API calls (Kambi) | ~450 calls | ~50 calls | 9x reduction |
| Database commits | ~120 commits | ~12 commits | 10x reduction |

**Note**: These are estimates. Actual gains depend on network latency, API rate limits, and data volume.
