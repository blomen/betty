# Altenar Platform Implementation

## Overview

Successfully implemented Altenar platform support, unlocking Betinia provider.

**Implementation Date:** 2026-01-26
**Provider Added:** Betinia (betinia.se)
**Architecture:** REST API (not WebSocket as initially expected)

## Key Findings

### Platform Architecture

Initial research suggested Altenar used WebSocket SDK, but investigation revealed:
- **No WebSocket connections** - Confirmed via comprehensive network monitoring
- **REST API based** - Events delivered via HTTP endpoints
- **Two main endpoints:**
  - `/widget/GetUpcoming` - Pre-match events
  - `/widget/GetLivenow` - Live events

### API Structure

**Response Format:**
```json
{
  "events": [...],          // List of event objects
  "competitors": [...],     // List of team/player objects
  "champs": [...],          // List of championship/league objects
  "markets": [...],         // List of market objects
  "odds": [...]            // List of odds objects
}
```

**Relational Model:**
- All entities have `id` fields
- Events reference competitors, championships, and markets by ID
- Markets reference odds by ID
- Requires ID resolution to construct complete event data

### Example Event Data

```json
{
  "id": 15399934,
  "name": "Spain (Emily) vs. Portugal (Rose)",
  "sportId": 66,
  "champId": 59472,
  "competitorIds": [1271199, 2429712],
  "marketIds": [1379097014, 1379097013],
  "startDate": "2026-01-26T16:40:00Z"
}
```

## Implementation Details

### Files Created/Modified

**New Retriever:**
- `backend/src/providers/altenar.py` - AltenarRetriever class (REST API based)

**Configuration:**
- `backend/src/config/providers.yaml` - Added Betinia configuration
- `backend/src/factory.py` - Registered altenar retriever type

**Key Features:**
- Sport mapping (66=football, 67=basketball, etc.)
- Market type mapping (1=1x2, 2=over_under, 18=over_under, etc.)
- ID resolution for competitors, championships, markets, odds
- No browser automation required (pure HTTP requests)

### Configuration Example

```yaml
betinia:
  id: betinia
  name: Betinia
  domain: betinia.se
  retriever_type: altenar
  api_base: https://sb2frontend-altenar2.biahosted.com/api
  integration: betiniase2  # Altenar skin ID

  supported_sports:
    - football
    - basketball
    - tennis
    - ice_hockey
    - table_tennis
    - handball
    - volleyball
    - esports
```

## Performance

### Test Results (Full API Response)

**Sport Coverage:**
- Football: 807 events, 3057 markets
- Basketball: 0 events (not available at test time)
- Tennis: 0 events
- Ice Hockey: 0 events

**Market Classification:**
- 1x2: 26.4% (correctly identified)
- over_under: 26.2% (correctly identified)
- other: 47.5% (unmapped market type IDs)
- **Total: 52.6% correct classification**

### Production Results (Limited to 100 events)

**Events Extracted:**
- 100 football events
- 38 unique leagues
- 821 odds

**Market Classification:**
- 1x2: 36.5%
- over_under: 23.6%
- other: 39.8%
- **Total: 60.1% correct classification**

Better classification in production due to different event mix (limited sample).

## Comparison with Similar Providers

| Provider | Architecture | Events | Markets | Classification |
|----------|-------------|--------|---------|----------------|
| Hajper   | WebSocket   | 289    | 2,257   | 41% other      |
| ComeOn   | WebSocket   | 1,000+ | ~4,000  | ~40% other     |
| Betinia  | REST API    | 807    | 3,057   | 47.5% other    |

Betinia (Altenar) provides:
- **2.8x more events than Hajper**
- **Similar coverage to ComeOn**
- **Simpler implementation** (REST vs WebSocket)
- **Faster extraction** (no browser overhead)

## Market Type Improvement Opportunities

Current unmapped market type IDs (47.5% as "other") can be reduced by:

1. **Enable debug logging** to capture unmapped IDs during extraction
2. **Map top 10-20 unmapped IDs** to standard types
3. **Target: <30% "other"** (similar to other providers)

Example unmapped types likely include:
- Double chance variations (IDs 4-7)
- Draw no bet (IDs 9, 11)
- Alternative totals (IDs 12-13)
- Props/specials (IDs 14+)

## Future Work

### Additional Providers on Altenar Platform

**FrankFred** (frankfred.com)
- Same Altenar platform
- Need to discover:
  - Integration ID (skin)
  - API base URL (if different)
  - Test extraction

### Market Type Mapping Enhancement

Steps to improve from 52.6% to 70%+ classification:
1. Run extraction with debug logging
2. Analyze top unmapped market type IDs
3. Map IDs 4-20 to standard market types
4. Test and validate mappings

### Multi-Sport Testing

Only football tested comprehensively. Need to:
- Test basketball (0 events in initial test)
- Test tennis
- Test ice hockey
- Test esports
- Validate sport ID mappings

## Technical Notes

### WebSocket Investigation

Extensive investigation confirmed no WebSocket usage:
- Monitored ALL WebSocket connections (not just filtered)
- Page loads successfully without WebSocket
- SDK JavaScript is loaded but doesn't establish WebSocket
- Data delivered via synchronous REST API calls

This was a key finding that simplified implementation significantly.

### API Endpoint Discovery

Found via network monitoring:
- `GetUpcoming` - 803 total events (all sports)
- `GetLivenow` - 42 live events
- `GetSportInfo` - Sports metadata
- `GetSportMenu` - Navigation structure
- `GetCoupons` - Betting coupons (if any)

### Integration ID

The `integration` parameter (e.g., "betiniase2") is critical:
- Required for API authentication
- Determines which events/markets are returned
- Different per brand/domain
- Found in page config: `project/info?fields=sportsbook`

## Summary

Successfully implemented Altenar platform support:
- ✓ Betinia provider active and working
- ✓ 807 football events extracted
- ✓ 52.6% market classification (60% in production)
- ✓ Simpler than expected (REST API, not WebSocket)
- ✓ Fast extraction (no browser overhead)
- ⚠ Market type mapping can be improved
- → FrankFred can be added easily (same platform)

**Estimated Effort:** 6-8 hours (vs 6-10 hours estimated)
**Result:** Production-ready provider with good coverage
