# Frontend Integration Summary

This document summarizes the complete wiring of the OddOpp backend API to the React frontend.

## What Was Done

### 1. Type Definitions (`frontend/src/types/index.ts`)

Created comprehensive TypeScript types matching all backend models:

**Core Types:**
- `Opportunity` - Unified type for arbitrage/value/bonus opportunities
- `EventSummary` & `EventDetail` - Event data with odds
- `Provider` - Bookmaker configuration and balance
- `BankrollInfo` & `BankrollStats` - Bankroll management
- `Bet` - Individual bet tracking
- `Profile` - User settings (Kelly, thresholds)
- `StakeCalculation` - Kelly criterion results

**System Types:**
- `MetricsRun` & `ProviderMetrics` - Pipeline performance data
- `CircuitBreakerStatus` - Circuit breaker state
- `CacheStats` - Cache hit/miss statistics
- `HealthCheckStatus` - Provider health checks
- `ProviderHealth` - Comprehensive health assessments
- `ExtractionStatus` - Pipeline status

### 2. API Client (`frontend/src/services/api.ts`)

Complete REST API client with all 40+ backend endpoints:

#### Providers
```ts
api.getProviders()
api.createProvider(data)
api.updateProvider(id, data)
```

#### Bankroll
```ts
api.getBankroll()
api.getBankrollStats()
```

#### Events
```ts
api.getEvents(sport?, limit?)
api.getEvent(eventId)
```

#### Opportunities
```ts
api.getOpportunities(type?, activeOnly?)
```

#### Bets
```ts
api.getBets(status?, limit?)
api.createBet(data)
api.settleBet(betId, result)
```

#### Profile
```ts
api.getProfile()
api.updateProfile(data)
```

#### Stake Calculator
```ts
api.calculateStake(odds, fairOdds)
```

#### Extraction
```ts
api.getExtractionStatus()
api.runExtraction(providers?, sport?, maxGroups?)
```

#### Metrics
```ts
api.getMetricsHistory(limit?)
api.getProviderMetrics(providerId, limit?)
api.getCurrentMetrics()
```

#### Circuit Breaker
```ts
api.getCircuitBreakerStatus()
api.getProviderCircuitBreaker(providerId)
api.resetCircuitBreaker(providerId)
```

#### Cache
```ts
api.getCacheStats()
api.getProviderCacheStats(providerId)
api.clearCache(providerId?)
api.evictExpiredCache()
```

#### Health Checks
```ts
api.getHealthCheckStatus()
api.runHealthCheck(providerId, force?)
api.clearHealthCheckCache(providerId?)
```

#### Provider Monitoring
```ts
api.monitorAllProviders(limit?)
api.monitorProvider(providerId, limit?)
api.getUnhealthyProviders(limit?)
api.getCriticalProviders(limit?)
```

### 3. React Hooks (`frontend/src/hooks/`)

Created specialized hooks for each feature area:

**useBettingContext** - Main data context
```ts
const { context, isLoading, error, refresh } = useBettingContext();
// context: { opportunities, events, providers, bankroll }
```

**useExtraction** - Pipeline control
```ts
const { status, runExtraction, refresh } = useExtraction();
await runExtraction('unibet,leovegas', 'football', 5);
```

**useProviderMonitor** - Health monitoring
```ts
const { providers, summary, refresh } = useProviderMonitor();
// providers: Record<string, ProviderHealth>
```

**useBankroll** - Bankroll tracking
```ts
const { bankroll, stats, refresh } = useBankroll();
// stats: { total_bets, wins, losses, roi_pct, ... }
```

**useBets** - Bet management
```ts
const { bets, createBet, settleBet } = useBets('pending');
```

**useProfile** - User settings
```ts
const { profile, updateProfile } = useProfile();
```

**useWebSocket** - Real-time updates
```ts
const { isConnected, lastMessage } = useWebSocket('ws://...');
```

**useChat** - Claude integration (existing)

### 4. Formatters (`frontend/src/utils/formatters.ts`)

Utility functions for consistent display formatting:

**Numeric:**
- `formatCurrency(value)` - "$100.50"
- `formatPercentage(value)` - "5.23%"
- `formatOdds(odds)` - "2.50"
- `formatNumber(value, decimals)` - "1234"

**Date/Time:**
- `formatDateTime(iso)` - "Jan 27, 02:30 PM"
- `formatDate(iso)` - "Jan 27, 2026"
- `formatTime(iso)` - "02:30 PM"
- `formatDuration(ms)` - "5.4s"

**Status:**
- `formatHealthScore(score)` - Returns `{ text, color }`
- `formatCircuitState(state)` - Returns `{ text, color }`
- `formatSeverity(severity)` - Returns `{ symbol, color }`
- `formatBetResult(result)` - Returns `{ text, color, symbol }`
- `formatOpportunityType(type)` - Returns `{ text, color, symbol }`
- `formatTrend(direction)` - Returns `{ symbol, color }`

**Events:**
- `formatEventName(home, away)` - "Arsenal vs Chelsea"

### 5. Updated Existing Components

**WelcomeMessage.tsx**
- Updated to use new `context.opportunities` structure
- Added bankroll display (4th stat card)
- Filter opportunities by type

**TerminalHeader.tsx**
- Updated to filter opportunities by type
- Added bankroll indicator
- Fixed data presence checks

**useBettingContext.ts**
- Fetch from correct endpoints with unwrapping
- Return new structure: `{ opportunities, events, providers, bankroll }`

**claude.ts (service)**
- Updated system prompt with new context structure
- Filter opportunities by type in buildSystemPrompt
- Updated simulation responses

### 6. Configuration

**vite.config.ts**
- Fixed API proxy: port 8080 → 8000
- Added WebSocket proxy for `/ws`

```ts
proxy: {
  '/api': {
    target: 'http://localhost:8000',
    changeOrigin: true,
  },
  '/ws': {
    target: 'ws://localhost:8000',
    ws: true,
    changeOrigin: true,
  },
}
```

### 7. Documentation

**frontend/README.md**
- Complete architecture overview
- API integration reference
- Hook usage examples
- Formatter documentation
- Development guide
- Next steps suggestions

## File Changes Summary

### Created Files (10)
```
frontend/src/hooks/useBankroll.ts
frontend/src/hooks/useBets.ts
frontend/src/hooks/useExtraction.ts
frontend/src/hooks/useProfile.ts
frontend/src/hooks/useProviderMonitor.ts
frontend/src/hooks/useWebSocket.ts
frontend/src/hooks/index.ts
frontend/src/utils/formatters.ts
frontend/src/utils/index.ts
frontend/README.md
```

### Modified Files (8)
```
frontend/src/types/index.ts              [MAJOR] - All types updated
frontend/src/services/api.ts              [MAJOR] - 40+ endpoints added
frontend/src/hooks/useBettingContext.ts   [MINOR] - Updated API calls
frontend/src/services/claude.ts           [MINOR] - Updated context usage
frontend/src/components/Terminal/WelcomeMessage.tsx  [MINOR] - Updated stats
frontend/src/components/Terminal/TerminalHeader.tsx  [MINOR] - Updated indicators
frontend/vite.config.ts                   [MINOR] - Fixed proxy config
```

## Testing

Build verification:
```bash
cd frontend
npm install
npm run build  # SUCCESS - 373.28 kB bundle
```

## Usage Examples

### Fetch Opportunities
```tsx
import { useBettingContext } from '@/hooks';

function OpportunitiesView() {
  const { context } = useBettingContext();

  const arbs = context.opportunities.filter(o => o.type === 'arbitrage');
  const values = context.opportunities.filter(o => o.type === 'value');

  return (
    <div>
      <h2>Arbitrage: {arbs.length}</h2>
      <h2>Value: {values.length}</h2>
    </div>
  );
}
```

### Run Extraction
```tsx
import { useExtraction } from '@/hooks';

function ExtractionControl() {
  const { status, runExtraction } = useExtraction();

  const handleRun = async () => {
    await runExtraction('unibet,leovegas', 'football', 5);
  };

  return (
    <button onClick={handleRun} disabled={status.running}>
      {status.running ? 'Running...' : 'Run Extraction'}
    </button>
  );
}
```

### Monitor Providers
```tsx
import { useProviderMonitor } from '@/hooks';
import { formatHealthScore } from '@/utils/formatters';

function ProviderHealth() {
  const { providers, summary } = useProviderMonitor();

  return (
    <div>
      <p>Healthy: {summary.healthy} / {summary.total_providers}</p>
      {Object.entries(providers).map(([id, health]) => {
        const { text, color } = formatHealthScore(health.health_score);
        return (
          <div key={id}>
            <span>{id}</span>
            <span className={color}>{text}</span>
          </div>
        );
      })}
    </div>
  );
}
```

### Track Bets
```tsx
import { useBets } from '@/hooks';

function BetTracker() {
  const { bets, createBet, settleBet } = useBets();

  const placeBet = async () => {
    await createBet({
      provider_id: 'unibet',
      odds: 2.5,
      stake: 100,
      outcome: 'Home Win',
    });
  };

  const settle = async (betId: number) => {
    await settleBet(betId, 'won', 250);
  };

  return (
    <div>
      {bets.map(bet => (
        <div key={bet.id}>
          <span>{bet.outcome} @ {bet.odds}</span>
          <button onClick={() => settle(bet.id)}>Settle</button>
        </div>
      ))}
    </div>
  );
}
```

## Next Steps

### Immediate
1. Test all API endpoints with backend running
2. Verify WebSocket connection for extraction progress
3. Test Claude chat with real opportunities data

### Short-term
1. Create dedicated dashboard views
2. Add opportunity alerts
3. Implement bet entry forms
4. Add provider management UI

### Long-term
1. Real-time odds updates via WebSocket
2. Charts for bankroll/ROI trends
3. Advanced filtering/sorting
4. Mobile-responsive layout

## Dependencies

All frontend dependencies are properly installed:
- React 19.0.0
- TypeScript 5.6.2
- Vite 6.0.5
- Tailwind CSS 3.4.17
- react-markdown 9.0.1
- remark-gfm 4.0.0
- lucide-react 0.468.0

## Backend Requirements

Frontend expects backend running on:
- **API:** http://localhost:8000
- **WebSocket:** ws://localhost:8000

Start backend:
```bash
cd backend
python -m uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
```

## Status

[x] All backend API endpoints wired to frontend
[x] TypeScript types match backend models
[x] React hooks for all features
[x] Formatters for consistent display
[x] WebSocket support
[x] Vite configuration updated
[x] Build verification passed
[x] Documentation complete

**Status: COMPLETE** - All backend APIs fully integrated into frontend.
