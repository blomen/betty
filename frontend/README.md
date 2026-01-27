# OddOpp Frontend

Terminal-style React frontend for the OddOpp betting analytics platform.

## Architecture

### Tech Stack
- React 19
- TypeScript
- Vite 6.0.5
- Tailwind CSS 3.4.17
- Lucide React (icons)

### Key Features
- Terminal-style UI with ASCII aesthetics
- Real-time chat with Claude AI
- Live betting data updates
- WebSocket support for extraction progress
- Comprehensive API integration

## Project Structure

```
src/
├── components/
│   └── Terminal/          # Terminal UI components
│       ├── ChatMessage.tsx
│       ├── StreamingText.tsx
│       ├── TerminalHeader.tsx
│       ├── TerminalInput.tsx
│       ├── TerminalWindow.tsx
│       └── WelcomeMessage.tsx
├── hooks/                 # React hooks for data fetching
│   ├── useBankroll.ts    # Bankroll + stats
│   ├── useBets.ts        # Bet history + CRUD
│   ├── useBettingContext.ts  # Main context (opps, events, providers)
│   ├── useChat.ts        # Claude chat integration
│   ├── useExtraction.ts  # Pipeline control
│   ├── useProfile.ts     # User settings
│   ├── useProviderMonitor.ts  # Provider health monitoring
│   ├── useWebSocket.ts   # WebSocket connection
│   └── index.ts          # Barrel exports
├── services/
│   ├── api.ts            # Backend API client (all endpoints)
│   └── claude.ts         # Claude streaming chat
├── types/
│   └── index.ts          # TypeScript definitions
├── utils/
│   ├── formatters.ts     # Display formatters (currency, odds, etc.)
│   └── index.ts          # Barrel exports
├── App.tsx               # Root component
└── main.tsx              # Entry point
```

## API Integration

### Complete Backend Coverage

All backend API endpoints are wired up in `src/services/api.ts`:

#### Providers
- `GET /api/providers` - List all providers with balances
- `POST /api/providers` - Create new provider
- `PUT /api/providers/:id` - Update provider (enable/disable, balance)

#### Bankroll
- `GET /api/bankroll` - Get total bankroll + provider balances
- `GET /api/bankroll/stats` - Get bet history stats (ROI, win rate)

#### Events
- `GET /api/events` - List events (with filters)
- `GET /api/events/:id` - Get event details with all odds

#### Opportunities
- `GET /api/opportunities` - List arbitrage/value/bonus opportunities

#### Bets
- `GET /api/bets` - Get bet history
- `POST /api/bets` - Record new bet
- `PUT /api/bets/:id` - Settle bet with result

#### Profile
- `GET /api/profile` - Get user settings
- `PUT /api/profile` - Update Kelly fraction, thresholds

#### Stake Calculator
- `POST /api/calculate/stake` - Calculate Kelly stake for given odds

#### Extraction
- `GET /api/extraction/status` - Pipeline status
- `POST /api/extraction/run` - Trigger extraction

#### Metrics
- `GET /api/metrics/history` - Historical pipeline runs
- `GET /api/metrics/provider/:id` - Provider-specific metrics
- `GET /api/metrics/current` - Current run metrics

#### Circuit Breaker
- `GET /api/circuit-breaker/status` - All providers
- `GET /api/circuit-breaker/status/:id` - Specific provider
- `POST /api/circuit-breaker/reset/:id` - Reset circuit breaker

#### Cache
- `GET /api/cache/stats` - Overall cache statistics
- `GET /api/cache/stats/:id` - Provider cache stats
- `POST /api/cache/clear` - Clear cache (all or specific provider)
- `POST /api/cache/evict-expired` - Evict expired entries

#### Health Checks
- `GET /api/health-check/status` - All providers
- `POST /api/health-check/run/:id` - Run check for provider
- `POST /api/health-check/clear-cache` - Clear health check cache

#### Provider Monitoring
- `GET /api/monitor/providers` - Health assessment for all
- `GET /api/monitor/providers/:id` - Detailed health for one
- `GET /api/monitor/unhealthy` - List unhealthy providers
- `GET /api/monitor/critical` - List critical providers

#### WebSocket
- `WS /ws/extraction` - Real-time extraction progress

## Hooks

### useBettingContext
Main context hook that fetches:
- Opportunities (arbitrage/value/bonus)
- Events (with odds counts)
- Providers (with balances)
- Bankroll info

Auto-refreshes every 30 seconds (configurable).

```tsx
const { context, isLoading, error, refresh } = useBettingContext();
```

### useExtraction
Control and monitor extraction pipeline:
```tsx
const { status, runExtraction, refresh } = useExtraction();

// Run extraction
await runExtraction('unibet,leovegas', 'football', 5);
```

### useProviderMonitor
Monitor provider health across metrics:
```tsx
const { providers, summary, refresh } = useProviderMonitor();

// providers: Record<string, ProviderHealth>
// summary: { total_providers, healthy, unhealthy, critical }
```

### useBankroll
Bankroll and betting statistics:
```tsx
const { bankroll, stats, refresh } = useBankroll();

// bankroll: { total, providers: [...] }
// stats: { total_bets, wins, losses, roi_pct, ... }
```

### useBets
Bet history and CRUD:
```tsx
const { bets, createBet, settleBet } = useBets('pending');

// Create bet
await createBet({
  provider_id: 'unibet',
  odds: 2.5,
  stake: 100,
  outcome: 'Home Win'
});

// Settle bet
await settleBet(betId, 'won', 250);
```

### useProfile
User profile settings:
```tsx
const { profile, updateProfile } = useProfile();

// Update Kelly fraction
await updateProfile({ kelly_fraction: 0.25 });
```

### useWebSocket
Real-time updates via WebSocket:
```tsx
const { isConnected, lastMessage } = useWebSocket('ws://localhost:5173/ws/extraction');
```

## Formatters

Utility functions for consistent display formatting in `src/utils/formatters.ts`:

### Currency & Numbers
- `formatCurrency(100.5)` → "$100.50"
- `formatPercentage(5.23)` → "5.23%"
- `formatOdds(2.5)` → "2.50"
- `formatNumber(1234, 0)` → "1234"

### Date & Time
- `formatDateTime(isoString)` → "Jan 27, 02:30 PM"
- `formatDate(isoString)` → "Jan 27, 2026"
- `formatTime(isoString)` → "02:30 PM"
- `formatDuration(5400)` → "5.4s"

### Status & Health
- `formatHealthScore('EXCELLENT')` → `{ text: 'EXCELLENT', color: 'text-green-400' }`
- `formatCircuitState('CLOSED')` → `{ text: 'CLOSED', color: 'text-green-400' }`
- `formatSeverity('critical')` → `{ symbol: 'X', color: 'text-red-400' }`
- `formatBetResult('won')` → `{ text: 'WON', color: 'text-green-400', symbol: '+' }`

### Opportunities
- `formatOpportunityType('arbitrage')` → `{ text: 'ARB', color: 'text-cyan-400', symbol: '<<>>' }`
- `formatTrend('IMPROVING')` → `{ symbol: '^', color: 'text-green-400' }`

### Events
- `formatEventName('Arsenal', 'Chelsea')` → "Arsenal vs Chelsea"

## Development

### Setup
```bash
npm install
```

### Run Dev Server
```bash
npm run dev  # Runs on http://localhost:5173
```

Requires backend running on port 8000 for API proxy.

### Build
```bash
npm run build
```

### Preview Production Build
```bash
npm run preview
```

### Lint
```bash
npm run lint
```

## Configuration

### Vite Config (`vite.config.ts`)

```ts
server: {
  port: 5173,
  proxy: {
    '/api': {
      target: 'http://localhost:8000',  // Backend API
      changeOrigin: true,
    },
    '/ws': {
      target: 'ws://localhost:8000',    // WebSocket
      ws: true,
      changeOrigin: true,
    },
  },
}
```

### Path Aliases
- `@/*` → `src/*`

## TypeScript Types

All types are defined in `src/types/index.ts`:

### Core Models
- `Opportunity` - Arbitrage/value/bonus opportunity
- `EventSummary` - Event with odds count
- `EventDetail` - Event with full odds breakdown
- `Provider` - Bookmaker with balance
- `BankrollInfo` - Total + per-provider balances
- `BankrollStats` - Bet history statistics
- `Bet` - Individual bet record
- `Profile` - User settings (Kelly, thresholds)

### API Responses
- `ProvidersResponse` - `{ providers: [], total_balance }`
- `StakeCalculation` - Kelly stake recommendation
- `ExtractionStatus` - Pipeline status
- `MetricsRun` - Pipeline metrics for a run
- `CircuitBreakerStatus` - Circuit breaker state
- `CacheStats` - Cache hit/miss statistics
- `HealthCheckStatus` - Provider health check
- `ProviderHealth` - Comprehensive health assessment

### Chat
- `Message` - Chat message (user/assistant)
- `ChatState` - Chat UI state
- `BettingContext` - Main data context passed to Claude

## Chat Integration

The terminal includes a Claude AI assistant powered by the Anthropic API.

### System Prompt
Automatically includes current betting data:
- Arbitrage count + top opportunities
- Value bet count + top opportunities
- Event count
- Provider count
- Total bankroll

### Features
- Streaming responses
- Markdown rendering (react-markdown + remark-gfm)
- Automatic context refresh
- Fallback simulation mode when API unavailable

### Usage
Type questions in the terminal input:
- "Show me arbitrage opportunities"
- "What value bets do you see?"
- "Calculate stake for odds 2.5"
- "Compare providers"

## Styling

Terminal aesthetic using Tailwind CSS with custom colors defined in `tailwind.config.js`.

### Terminal Colors
- `terminal-bg` - Background
- `terminal-surface` - Card/surface
- `terminal-border` - Borders
- `terminal-text` - Primary text
- `terminal-muted` - Secondary text
- `terminal-accent` - Brand accent
- `terminal-cyan` - Arbitrage
- `terminal-green` - Value/success
- `terminal-purple` - Events
- `terminal-yellow` - Warning/bonus
- `terminal-red` - Error/loss

## Next Steps

### Potential Enhancements
1. **Dashboard Views** - Dedicated pages for:
   - Arbitrage opportunities table
   - Value bets table
   - Provider monitoring dashboard
   - Bet history with charts

2. **Real-time Updates** - Use WebSocket for:
   - Live odds updates
   - Opportunity alerts
   - Pipeline progress

3. **Charts & Analytics** - Visualizations for:
   - Bankroll growth over time
   - ROI by provider
   - Win rate trends
   - Provider performance

4. **Bet Tracking** - Enhanced features:
   - Quick bet entry form
   - Auto-calculate stakes
   - Track pending bets
   - P/L summaries

5. **Settings Panel** - UI for:
   - Profile configuration
   - Provider enable/disable
   - Notification preferences
   - Display preferences

## Contributing

See main project CLAUDE.md for development guidelines.

Key rules:
- No emojis (use ASCII symbols)
- Terminal-style aesthetics
- Type-safe API calls
- Comprehensive error handling
