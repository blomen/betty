# Bankroll Management - Fixes Summary

Complete overhaul of bankroll management system with CLI tools, API endpoints, and frontend integration.

## What Was Fixed

### Problem
- All provider balances were stuck at $0.00
- No easy way to initialize or manage balances
- Limited API endpoints for bulk operations
- No CLI tools for quick management
- Missing statistics (win rate, void count)

### Solution
Created comprehensive bankroll management system with:
1. **CLI scripts** for quick balance management
2. **New API endpoints** for bulk operations
3. **Enhanced frontend hooks** with all bankroll functions
4. **Improved statistics** including win rate and void bets

## New Features

### 1. CLI Scripts

#### `scripts/init_bankroll.py`
Initialize default balances for common providers.

```bash
python scripts/init_bankroll.py
```

Sets $500 for: unibet, leovegas, casumo, betsson, mrgreen, 888sport
Total bankroll: $3000

#### `scripts/manage_bankroll.py`
Complete CLI tool for bankroll management.

**Commands:**
```bash
# View all balances
python scripts/manage_bankroll.py list

# Set individual balance
python scripts/manage_bankroll.py set unibet 1000

# Set all enabled providers
python scripts/manage_bankroll.py set-all 500

# Add/subtract from balance
python scripts/manage_bankroll.py add unibet 100
python scripts/manage_bankroll.py add unibet -50

# View detailed statistics
python scripts/manage_bankroll.py stats

# Reset all balances to $0
python scripts/manage_bankroll.py reset-all
```

### 2. New API Endpoints

#### Bulk Balance Update
**POST** `/api/bankroll/set-all`

Set balance for all providers or specific ones:
```json
{
  "balance": 500.0,
  "provider_ids": ["unibet", "leovegas"]  // Optional
}
```

#### Balance Adjustment
**POST** `/api/bankroll/adjust/{provider_id}`

Add or subtract from balance:
```json
{
  "amount": 100.0  // Positive = add, negative = subtract
}
```

#### Reset All Balances
**POST** `/api/bankroll/reset-all`

Reset all providers to $0.00.

### 3. Enhanced Statistics

**GET** `/api/bankroll/stats` now includes:
```json
{
  "total_bets": 10,
  "wins": 6,
  "losses": 3,
  "voids": 1,           // NEW
  "total_staked": 1000.0,
  "total_profit": 150.0,
  "roi_pct": 15.0,
  "win_rate": 60.0      // NEW
}
```

### 4. Frontend Integration

#### Updated `useBankroll` Hook

```tsx
const {
  bankroll,
  stats,
  setAllBalances,      // NEW
  adjustBalance,       // NEW
  resetAllBalances,    // NEW
  refresh,
} = useBankroll();

// Set all providers to $500
await setAllBalances(500);

// Set specific providers to $1000
await setAllBalances(1000, ['unibet', 'leovegas']);

// Add $100 to unibet
await adjustBalance('unibet', 100);

// Subtract $50 from unibet
await adjustBalance('unibet', -50);

// Reset all balances
await resetAllBalances();
```

#### Updated API Client

All new endpoints added to `frontend/src/services/api.ts`:
- `api.setAllBalances(balance, providerIds?)`
- `api.adjustBalance(providerId, amount)`
- `api.resetAllBalances()`

#### Updated Types

`BankrollStats` type now includes:
- `voids: number`
- `win_rate: number`

### 5. Improved Provider Update Response

**PUT** `/api/providers/{provider_id}` now returns:
```json
{
  "success": true,
  "provider_id": "unibet",
  "old_balance": 500.0,    // NEW
  "new_balance": 1000.0    // NEW
}
```

## Files Created

```
backend/scripts/init_bankroll.py          # Initialize default balances
backend/scripts/manage_bankroll.py        # Complete CLI management
docs/BANKROLL_MANAGEMENT.md               # Full documentation
docs/BANKROLL_FIXES_SUMMARY.md            # This file
```

## Files Modified

```
backend/src/api.py                        # Added 3 new endpoints, updated responses
frontend/src/services/api.ts              # Added 3 new API calls
frontend/src/hooks/useBankroll.ts         # Added 3 new methods
frontend/src/types/index.ts               # Updated BankrollStats type
```

## Testing

### Verified CLI Tools

```bash
# Initialize default balances
$ python scripts/init_bankroll.py
Done! Total bankroll: $3000.00

# Set individual balance
$ python scripts/manage_bankroll.py set unibet 1000
Updated unibet balance: $500.00 -> $1000.00

# Add to balance
$ python scripts/manage_bankroll.py add unibet 100
Updated unibet balance: $1000.00 -> $1100.00 (+100.00)

# View stats
$ python scripts/manage_bankroll.py stats
Total balance: $3600.00
Active providers: 27
```

### Verified API Endpoints

All endpoints tested and working:
- ✓ GET /api/bankroll
- ✓ GET /api/bankroll/stats
- ✓ POST /api/bankroll/set-all
- ✓ POST /api/bankroll/adjust/{id}
- ✓ POST /api/bankroll/reset-all
- ✓ PUT /api/providers/{id}

## Usage Examples

### Quick Setup for Development

```bash
# 1. Initialize default balances
cd backend
python scripts/init_bankroll.py

# 2. Verify
python scripts/manage_bankroll.py list

# 3. Start API server
python -m uvicorn src.api:app --port 8000

# 4. Frontend will now show $3000 total bankroll
```

### Adjust Balances During Testing

```bash
# Set all to $1000
python scripts/manage_bankroll.py set-all 1000

# Or via API
curl -X POST http://localhost:8000/api/bankroll/set-all \
  -H "Content-Type: application/json" \
  -d '{"balance": 1000.0}'
```

### Track Bet Performance

```bash
# View stats after placing bets
python scripts/manage_bankroll.py stats

# Shows:
# - Total bets: 10
# - Wins: 6 (60.0% win rate)
# - ROI: 15.0%
# - Profit: $150.00
```

## Benefits

1. **Easy Setup** - One command to initialize balances
2. **Quick Management** - CLI tools for instant updates
3. **Bulk Operations** - Set multiple providers at once
4. **Better Tracking** - Win rate and void bets included
5. **Frontend Integration** - All functions available in React
6. **Complete Statistics** - Comprehensive performance metrics

## Documentation

Full documentation available in:
- `docs/BANKROLL_MANAGEMENT.md` - Complete guide
- `scripts/manage_bankroll.py --help` - CLI usage
- `scripts/init_bankroll.py` - Script comments

## Migration Notes

### Existing Installations

If you have existing provider data with $0 balances:

```bash
# Initialize common providers
python scripts/init_bankroll.py

# Or set all manually
python scripts/manage_bankroll.py set-all 500
```

### Database Changes

No schema changes required - uses existing `balance` column in `providers` table.

### API Changes

All new endpoints are additions - no breaking changes to existing endpoints.

### Frontend Changes

`useBankroll` hook has new methods but existing functionality unchanged - backward compatible.

## Next Steps

### Recommended Actions

1. **Initialize balances** for your providers:
   ```bash
   python scripts/init_bankroll.py
   ```

2. **Verify totals**:
   ```bash
   python scripts/manage_bankroll.py list
   ```

3. **Test in frontend**:
   - Start backend (port 8000)
   - Start frontend (port 5173)
   - Check welcome screen shows total bankroll

### Future Enhancements

Potential improvements:
- Bankroll history tracking
- Provider-specific Kelly fractions
- Auto-rebalancing
- Withdrawal/deposit tracking
- Charts in frontend

## Known Issues

### Deprecation Warning

Scripts show `datetime.utcnow()` deprecation warning. This is cosmetic and doesn't affect functionality. Will be fixed in future update.

### Frontend Build

No changes to frontend build - all updates are in hooks/API client (runtime only).

## Summary

Bankroll management is now fully functional with:
- ✓ Easy initialization ($500 default for 6 providers)
- ✓ CLI tools for all operations
- ✓ API endpoints for bulk updates
- ✓ Frontend integration with React hooks
- ✓ Enhanced statistics (win rate, voids)
- ✓ Complete documentation

**Status: COMPLETE** - Bankroll system fully operational and documented.
