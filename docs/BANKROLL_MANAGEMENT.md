# Bankroll Management Guide

Complete guide to managing provider balances and tracking betting performance.

## Overview

The bankroll system tracks:
- **Provider balances** - Individual balance per bookmaker
- **Total bankroll** - Sum of all enabled providers
- **Bet history** - All placed bets with results
- **Statistics** - ROI, win rate, profit/loss

## Quick Start

### Initialize Default Balances

Sets $500 for common providers (unibet, leovegas, casumo, betsson, mrgreen, 888sport):

```bash
cd backend
python scripts/init_bankroll.py
```

Output:
```
Initializing default bankroll...
Setting $500.00 for 6 providers

  [OK] unibet          $0.00 -> $500.00
  [OK] leovegas        $0.00 -> $500.00
  [OK] casumo          $0.00 -> $500.00
  [OK] betsson         $0.00 -> $500.00
  [OK] mrgreen         $0.00 -> $500.00
  [OK] 888sport        $0.00 -> $500.00

Done!
Total bankroll: $3000.00
```

## CLI Management

### List All Balances

```bash
python scripts/manage_bankroll.py list
```

Shows:
- All providers with balances
- Enabled/disabled status
- Total bankroll (all + enabled only)

### Set Individual Balance

```bash
python scripts/manage_bankroll.py set unibet 1000
```

Sets unibet balance to $1000.

### Set All Balances

```bash
python scripts/manage_bankroll.py set-all 500
```

Sets all enabled providers to $500 each.

### Add/Subtract from Balance

```bash
# Add $100 to unibet
python scripts/manage_bankroll.py add unibet 100

# Subtract $50 from unibet
python scripts/manage_bankroll.py add unibet -50
```

### View Statistics

```bash
python scripts/manage_bankroll.py stats
```

Shows:
- Total bankroll
- Bet history (wins, losses, voids)
- ROI and win rate
- Per-provider breakdown

### Reset All Balances

```bash
python scripts/manage_bankroll.py reset-all
```

Sets all providers to $0.00 (requires confirmation).

## API Endpoints

### Get Bankroll

**GET** `/api/bankroll`

Returns total + per-provider balances.

```bash
curl http://localhost:8000/api/bankroll
```

Response:
```json
{
  "total": 3000.0,
  "providers": [
    {"id": "unibet", "name": "Unibet.Se", "balance": 500.0},
    {"id": "leovegas", "name": "Leovegas.Com", "balance": 500.0}
  ]
}
```

### Get Statistics

**GET** `/api/bankroll/stats`

Returns bet history statistics.

```bash
curl http://localhost:8000/api/bankroll/stats
```

Response:
```json
{
  "total_bets": 10,
  "wins": 6,
  "losses": 3,
  "voids": 1,
  "total_staked": 1000.0,
  "total_profit": 150.0,
  "roi_pct": 15.0,
  "win_rate": 60.0
}
```

### Set Individual Balance

**PUT** `/api/providers/{provider_id}`

Update provider balance.

```bash
curl -X PUT http://localhost:8000/api/providers/unibet \
  -H "Content-Type: application/json" \
  -d '{"balance": 1000.0}'
```

Response:
```json
{
  "success": true,
  "provider_id": "unibet",
  "old_balance": 500.0,
  "new_balance": 1000.0
}
```

### Set All Balances (Bulk)

**POST** `/api/bankroll/set-all`

Set balance for multiple providers at once.

```bash
# Set all enabled providers to $500
curl -X POST http://localhost:8000/api/bankroll/set-all \
  -H "Content-Type: application/json" \
  -d '{"balance": 500.0}'

# Set specific providers to $1000
curl -X POST http://localhost:8000/api/bankroll/set-all \
  -H "Content-Type: application/json" \
  -d '{"balance": 1000.0, "provider_ids": ["unibet", "leovegas"]}'
```

Response:
```json
{
  "success": true,
  "updated_count": 6,
  "balance_per_provider": 500.0,
  "total_balance": 3000.0
}
```

### Adjust Balance

**POST** `/api/bankroll/adjust/{provider_id}`

Add or subtract from balance.

```bash
# Add $100
curl -X POST http://localhost:8000/api/bankroll/adjust/unibet \
  -H "Content-Type: application/json" \
  -d '{"amount": 100.0}'

# Subtract $50
curl -X POST http://localhost:8000/api/bankroll/adjust/unibet \
  -H "Content-Type: application/json" \
  -d '{"amount": -50.0}'
```

Response:
```json
{
  "success": true,
  "provider_id": "unibet",
  "old_balance": 500.0,
  "adjustment": 100.0,
  "new_balance": 600.0
}
```

### Reset All Balances

**POST** `/api/bankroll/reset-all`

Reset all providers to $0.00.

```bash
curl -X POST http://localhost:8000/api/bankroll/reset-all
```

Response:
```json
{
  "success": true,
  "reset_count": 27,
  "message": "All balances reset to 0"
}
```

## Frontend Usage

### useBankroll Hook

```tsx
import { useBankroll } from '@/hooks';

function BankrollManager() {
  const {
    bankroll,
    stats,
    setAllBalances,
    adjustBalance,
    resetAllBalances,
  } = useBankroll();

  // Set all enabled providers to $500
  const handleSetAll = async () => {
    await setAllBalances(500);
  };

  // Add $100 to unibet
  const handleAdd = async () => {
    await adjustBalance('unibet', 100);
  };

  // Subtract $50 from unibet
  const handleSubtract = async () => {
    await adjustBalance('unibet', -50);
  };

  // Reset all balances
  const handleReset = async () => {
    if (confirm('Reset all balances?')) {
      await resetAllBalances();
    }
  };

  return (
    <div>
      <h2>Total Bankroll: ${bankroll.total.toFixed(2)}</h2>

      <div>
        <h3>Providers</h3>
        {bankroll.providers.map(p => (
          <div key={p.id}>
            {p.name}: ${p.balance.toFixed(2)}
          </div>
        ))}
      </div>

      <div>
        <h3>Statistics</h3>
        <p>Total Bets: {stats.total_bets}</p>
        <p>Win Rate: {stats.win_rate.toFixed(1)}%</p>
        <p>ROI: {stats.roi_pct.toFixed(2)}%</p>
        <p>Profit: ${stats.total_profit.toFixed(2)}</p>
      </div>
    </div>
  );
}
```

### Direct API Calls

```tsx
import { api } from '@/services/api';

// Set all balances
await api.setAllBalances(500);

// Set specific providers
await api.setAllBalances(1000, ['unibet', 'leovegas']);

// Adjust balance
await api.adjustBalance('unibet', 100);

// Reset all
await api.resetAllBalances();
```

## Bet Tracking

### Recording Bets

Bets are recorded when you place them via the API:

```bash
curl -X POST http://localhost:8000/api/bets \
  -H "Content-Type: application/json" \
  -d '{
    "provider_id": "unibet",
    "odds": 2.5,
    "stake": 100,
    "outcome": "Home Win",
    "event_id": "football:arsenal:chelsea:20260127"
  }'
```

This automatically deducts the stake from provider balance.

### Settling Bets

When a bet is settled:

```bash
curl -X PUT http://localhost:8000/api/bets/1 \
  -H "Content-Type: application/json" \
  -d '{"result": "won", "payout": 250}'
```

This adds the payout to provider balance.

### Bet Results

- **won** - Bet won, payout returned
- **lost** - Bet lost, stake lost (unless bonus bet)
- **void** - Bet voided, stake returned
- **pending** - Not yet settled

### Bonus Bets

For free/bonus bets, set `is_bonus: true`:

```json
{
  "provider_id": "unibet",
  "odds": 2.5,
  "stake": 100,
  "is_bonus": true,
  "bonus_type": "free_bet"
}
```

Free bets don't lose stake on loss (profit calculation differs).

## Kelly Stake Calculation

Calculate recommended stake based on edge:

```bash
curl -X POST http://localhost:8000/api/calculate/stake \
  -H "Content-Type: application/json" \
  -d '{"odds": 2.5, "fair_odds": 2.2}'
```

Response:
```json
{
  "recommended_stake": 45.0,
  "kelly_stake": 45.0,
  "max_stake": 150.0,
  "bankroll": 3000.0,
  "reason": "Kelly"
}
```

Factors:
- **Kelly fraction** - Default 0.25 (quarter Kelly)
- **Max stake %** - Default 5% of bankroll
- **Provider balance** - Limited by available funds

Configure in profile:

```bash
curl -X PUT http://localhost:8000/api/profile \
  -H "Content-Type: application/json" \
  -d '{
    "kelly_fraction": 0.25,
    "max_stake_pct": 5.0
  }'
```

## Database Schema

### Provider Table

```sql
CREATE TABLE providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT,
    is_enabled BOOLEAN DEFAULT TRUE,
    balance FLOAT DEFAULT 0.0,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

### Bet Table

```sql
CREATE TABLE bets (
    id INTEGER PRIMARY KEY,
    event_id TEXT,
    provider_id TEXT,
    market TEXT,
    outcome TEXT,
    odds FLOAT,
    stake FLOAT,
    is_bonus BOOLEAN DEFAULT FALSE,
    bonus_type TEXT,
    result TEXT DEFAULT 'pending',  -- won/lost/void/pending
    payout FLOAT DEFAULT 0.0,
    placed_at TIMESTAMP,
    settled_at TIMESTAMP
);
```

## Best Practices

### Initial Setup

1. Initialize default balances:
   ```bash
   python scripts/init_bankroll.py
   ```

2. Adjust individual providers as needed:
   ```bash
   python scripts/manage_bankroll.py set betsson 1000
   ```

3. Verify totals:
   ```bash
   python scripts/manage_bankroll.py list
   ```

### During Operation

1. **Track all bets** - Record every bet placed
2. **Settle promptly** - Update results when known
3. **Monitor stats** - Check ROI and win rate regularly
4. **Rebalance** - Redistribute funds between providers

### Bankroll Management

- **Keep 5-10% of total bankroll per provider** for diversification
- **Never exceed max stake %** (default 5% per bet)
- **Use quarter Kelly** (default 0.25) for conservative growth
- **Track by provider** to identify best/worst bookmakers

### Example Workflow

```bash
# 1. Initialize
python scripts/init_bankroll.py

# 2. View current state
python scripts/manage_bankroll.py stats

# 3. Place bet via frontend/API (automatically deducts stake)

# 4. Settle bet when result known (automatically adds payout)

# 5. Review statistics
python scripts/manage_bankroll.py stats

# 6. Rebalance if needed
python scripts/manage_bankroll.py set-all 500
```

## Troubleshooting

### Balances not updating

Check that:
- Database file exists: `backend/data/oddopp.db`
- API server is running on port 8000
- Provider exists in database

### Negative balances

This is allowed - represents debt/liability. To reset:
```bash
python scripts/manage_bankroll.py reset-all
```

### Stats not showing

Ensure bets are marked as settled (not pending):
```bash
# Check pending bets
curl http://localhost:8000/api/bets?status=pending
```

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `init_bankroll.py` | Initialize default $500 balances |
| `manage_bankroll.py list` | View all balances |
| `manage_bankroll.py set <id> <amt>` | Set individual balance |
| `manage_bankroll.py set-all <amt>` | Set all enabled balances |
| `manage_bankroll.py add <id> <amt>` | Adjust balance (+/-) |
| `manage_bankroll.py stats` | View statistics |
| `manage_bankroll.py reset-all` | Reset all to $0 |

## API Reference

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/bankroll` | GET | Get total + balances |
| `/api/bankroll/stats` | GET | Get bet statistics |
| `/api/bankroll/set-all` | POST | Bulk set balances |
| `/api/bankroll/adjust/{id}` | POST | Adjust balance +/- |
| `/api/bankroll/reset-all` | POST | Reset all to $0 |
| `/api/providers/{id}` | PUT | Update provider (including balance) |
| `/api/bets` | POST | Record bet (deducts stake) |
| `/api/bets/{id}` | PUT | Settle bet (adds payout) |
| `/api/calculate/stake` | POST | Calculate Kelly stake |

## Related Documentation

- `backend/src/bankroll/manager.py` - Kelly calculation
- `backend/src/db/models.py` - Database models
- `backend/src/api.py` - API endpoints
- `frontend/src/hooks/useBankroll.ts` - React hook
- `frontend/src/services/api.ts` - API client
